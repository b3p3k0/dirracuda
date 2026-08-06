"""Read-only Stage-F namespace and durable-owner census.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .c0b2_checkpoint import (
    INVOCATION_CAPS, Checkpoint, CheckpointError, ImmutableViolation,
    canonical_json, sha256_json,
)
from .c0b2_public_schema import PlanActivation, validate_artifact
from .c0b2_runtime_common import (
    _bound_control, _decision_digest, _rfc3339, load_runtime_control,
)

LATER_KEYS = ("F_SEED_17", "F_SEED_20260804")
_ACTIVE_STATES = {"RUNNING", "PAUSED_RESOURCE", "PAUSED_PREFLIGHT",
                  "PAUSED_SOFT_WALL", "CANCELLED_PENDING_RESUME"}


def finite_timestamp(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImmutableViolation(f"{label} is not a finite real timestamp")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ImmutableViolation(f"{label} is not a finite real timestamp")
    return parsed


def _public_budget_namespace(point: Checkpoint) -> None:
    """Bind Stage F to the frozen public-run allowances before local setup."""
    from .c0b2_runtime import PUBLIC_CUMULATIVE_CAP, PUBLIC_LIMITS

    header = point.header()
    if (header.get("run_type") != "public"
            or canonical_json(header.get("limits"))
            != canonical_json(PUBLIC_LIMITS)
            or type(header.get("cumulative_cap")) is not int
            or header["cumulative_cap"] != PUBLIC_CUMULATIVE_CAP
            or canonical_json(header.get("invocation_caps"))
            != canonical_json(INVOCATION_CAPS)):
        raise ImmutableViolation(
            "public budget ledger differs from the implementation contract")
    stages = sorted((stage, sum(classes.values()))
                    for stage, classes in PUBLIC_LIMITS.items())
    classes = sorted((stage, kind, allowance)
                     for stage, values in PUBLIC_LIMITS.items()
                     for kind, allowance in values.items())
    stored_stages = point.conn.execute(
        "SELECT stage,hard_cap FROM stage_limits ORDER BY 1").fetchall()
    stored_classes = point.conn.execute(
        "SELECT stage,call_class,allowance FROM class_limits ORDER BY 1,2"
    ).fetchall()
    if stored_stages != stages or stored_classes != classes:
        raise ImmutableViolation(
            "public budget tables differ from the implementation contract")


def _runtime_state_namespace(point: Checkpoint) -> None:
    if point.conn.execute(
            "SELECT count(*) FROM context_obligations WHERE stage='F'"
    ).fetchone()[0]:
        raise ImmutableViolation("legacy F context obligation is forbidden")
    known = set(point.header()["model_digests"])
    for model, failures, retry_at, updated in point.conn.execute(
            "SELECT model,failures,retry_not_before,updated FROM model_backoff"):
        retry = finite_timestamp(retry_at, "F model backoff retry")
        changed = finite_timestamp(updated, "F model backoff update")
        if (str(model) not in known or type(failures) is not int
                or failures < 1 or retry > changed + 300.0):
            raise ImmutableViolation("F model backoff namespace changed")


def _canonical(raw: str, digest: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"{label} is not valid JSON") from exc
    if (not isinstance(value, dict) or canonical_json(value) != raw
            or sha256_json(value) != digest):
        raise ImmutableViolation(f"{label} hash or canonical encoding changed")
    return value


def _activation_owner(point: Checkpoint, plan: Mapping[str, Any]
                      ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    key = str(plan["plan_key"])
    row = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations WHERE plan_key=?",
        (key,)).fetchone()
    if not row:
        raise ImmutableViolation(f"active F plan {key} lacks activation")
    try:
        activation = validate_artifact(
            PlanActivation, _canonical(str(row[1]), str(row[0]), f"{key} activation"))
    except (TypeError, ValueError) as exc:
        raise ImmutableViolation(f"active F plan {key} activation is malformed") from exc
    groups = list(activation["activated_group_ids"])
    ordered = [group["group_id"] for group in plan.get("groups", ())]
    if (activation["budget_stage"] != "F" or activation["plan_key"] != key
            or activation["plan_sha256"] != sha256_json(plan)
            or activation["run_id"] != point.header()["run_id"]
            or activation["state"] != "ACTIVATED"):
        raise ImmutableViolation(f"active F plan {key} activation owner changed")
    if key == "F_SEED_1" and (
            activation["parent_decision_sha256"]
            != _decision_digest(point, "stage-d-selection")
            or activation["evidence_sha256"] is not None):
        raise ImmutableViolation("active F seed-1 activation lineage changed")
    if key == "F_SEED_1" and groups != ordered:
        raise ImmutableViolation("active F seed-1 activation changed")
    if key in LATER_KEYS and (not groups or any(group not in ordered for group in groups)
            or len(groups) != len(set(groups))):
        raise ImmutableViolation(f"active F plan {key} groups changed")
    if key == "F_ACCEPTANCE" and groups:
        raise ImmutableViolation("active F acceptance cannot activate groups")
    work = list(plan["work"])
    if key in LATER_KEYS:
        work = [item for group in groups for item in work
                if item["activation_group_id"] == group]
    return activation, work


def _exact_work_namespace(
        point: Checkpoint, plans: Mapping[str, Mapping[str, Any]],
        activation_keys: tuple[str, ...],
        work_validator: Callable[..., list[dict[str, Any]]], *, terminal: bool,
) -> None:
    expected: list[tuple[Any, ...]] = []
    work_by_id: dict[str, Mapping[str, Any]] = {}
    for key in activation_keys:
        _activation, work = _activation_owner(point, plans[key])
        for item in work:
            work_id = str(item["work_id"])
            if work_id in work_by_id:
                raise ImmutableViolation("F work identity has multiple plan owners")
            work_by_id[work_id] = item
            expected.append((work_id, key, item["activation_group_id"], "F",
                             item["cell_id"], item["request_sha256"]))
    actual = tuple(point.conn.execute(
        "SELECT r.work_id,r.plan_key,r.activation_group_id,w.stage,w.cell_id,"
        "w.request_hash FROM phase_work_registry r JOIN work_items w "
        "ON w.work_id=r.work_id LEFT JOIN phase_plans p ON p.plan_key=r.plan_key "
        "WHERE r.plan_key LIKE 'F_%' OR p.budget_stage='F' OR w.stage='F' "
        "ORDER BY r.rowid"))
    if actual != tuple(expected):
        raise ImmutableViolation("F registry/work namespace changed")
    stage_work = tuple(row[0] for row in point.conn.execute(
        "SELECT work_id FROM work_items WHERE stage='F' ORDER BY rowid"))
    if stage_work != tuple(row[0] for row in expected):
        raise ImmutableViolation("F work-item namespace changed")
    attempts = point.conn.execute(
        "SELECT a.stage,a.work_id,a.control_id,w.stage,c.plan_key FROM attempts a "
        "LEFT JOIN work_items w ON w.work_id=a.work_id "
        "LEFT JOIN runtime_controls c ON c.control_id=a.control_id "
        "LEFT JOIN phase_plans p ON p.plan_key=c.plan_key "
        "WHERE a.stage='F' OR w.stage='F' OR c.plan_key LIKE 'F_%' "
        "OR p.budget_stage='F'").fetchall()
    if any((row[1] is None) == (row[2] is None) or row[0] != "F"
           or row[1] is not None
           and (row[1] not in work_by_id or row[3] != "F") for row in attempts):
        raise ImmutableViolation("F attempt has orphan or cross-stage ownership")
    for item in work_by_id.values():
        work_validator(point, item, terminal_required=terminal)


def _cancellation_barrier(point: Checkpoint, control: Mapping[str, Any],
                          record: Any) -> None:
    from .c0b2_runtime_common import _cancellation_observation

    evidence = _canonical(
        str(record.evidence_json), str(record.evidence_sha256),
        "pending F cancellation evidence")
    evidence = _cancellation_observation(evidence, str(control["control_id"]))
    attempt = point.conn.execute(
        "SELECT control_id,state,metadata_json,updated FROM attempts "
        "WHERE attempt_id=?", (evidence["cancel_attempt_id"],)).fetchone()
    metadata = {
        "cancel_elapsed_ms": evidence["cancel_elapsed_ms"],
        "cancel_first_byte_seen": evidence["cancel_first_byte_seen"],
        "owned_stream_cancelled": True,
    }
    delay = max(2000, int(control["health_not_before_ms"])) / 1000.0
    updated = (finite_timestamp(attempt[3], "F cancellation attempt update")
               if attempt else None)
    expected_not_before = (_rfc3339(updated + delay)
                           if attempt else None)
    if (not attempt or attempt[:2] != (control["control_id"],
                                       "CANCELLED_UNVERIFIED")
            or attempt[2] != canonical_json(metadata)
            or record.not_before_utc != expected_not_before):
        raise ImmutableViolation("pending F health barrier changed")
    try:
        observed = datetime.fromisoformat(
            str(record.not_before_utc).removesuffix("Z") + "+00:00")
        threshold = datetime.fromtimestamp(updated + delay, timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ImmutableViolation("pending F health barrier is invalid") from exc
    if observed != threshold:
        raise ImmutableViolation("pending F health barrier changed")


def _seed1_control_namespace(point: Checkpoint, seed1: Mapping[str, Any],
                             corpus: Any) -> None:
    expected = [control for group in seed1["groups"] for control in (
        group["context_control"], group["cancellation_control"],
        group["health_control"])]
    expected_ids = tuple(str(control["control_id"]) for control in expected)
    rows = tuple(point.conn.execute(
        "SELECT rc.control_id,rc.plan_key,rc.kind,rc.control_hash,rc.control_json "
        "FROM runtime_controls rc LEFT JOIN phase_plans p ON p.plan_key=rc.plan_key "
        "WHERE rc.plan_key LIKE 'F_%' OR p.budget_stage='F' OR rc.control_id IN ("
        + ",".join("?" for _ in expected_ids) + ") ORDER BY rc.rowid", expected_ids))
    if tuple(row[0] for row in rows) != expected_ids:
        raise ImmutableViolation("active F runtime-control namespace changed")
    allowed = {"context_probe": {"PENDING", "COMPLETE"},
               "cancellation_probe": {"PENDING", "CANCELLED_UNVERIFIED"},
               "cancellation_health": {"PENDING", "COMPLETE"}}
    states: dict[str, str] = {}
    for control, row in zip(expected, rows):
        kind, identity = str(control["kind"]), str(control["control_id"])
        normalized = _bound_control(point, "F_SEED_1", kind, control)
        record = load_runtime_control(point, identity)
        if (normalized != control or row != (
                identity, "F_SEED_1", kind, sha256_json(control),
                canonical_json(control)) or record.state not in allowed[kind]):
            raise ImmutableViolation("active F runtime-control owner changed")
        events = tuple(point.conn.execute(
            "SELECT seq,state,evidence_hash,evidence_json,not_before_utc "
            "FROM runtime_control_events WHERE control_id=? ORDER BY seq",
            (identity,)))
        expected_events = ((1, "PENDING", None, None, None),)
        if record.state != "PENDING":
            expected_events += ((2, record.state, record.evidence_sha256,
                                 record.evidence_json, record.not_before_utc),)
        if events != expected_events:
            raise ImmutableViolation("active F runtime-control event prefix changed")
        if record.state == "CANCELLED_UNVERIFIED":
            _cancellation_barrier(point, control, record)
        states[identity] = record.state
    _seed1_progress_prefix(point, seed1, states)
    for group in seed1["groups"]:
        _completed_control_owners(point, seed1, corpus, group, states)


def _completed_control_owners(
        point: Checkpoint, seed1: Mapping[str, Any], corpus: Any,
        group: Mapping[str, Any], states: Mapping[str, str],
) -> None:
    from . import chunker
    from . import c0b2_runtime_f_evidence as evidence

    context = group["context_control"]
    if states[str(context["control_id"])] == "COMPLETE":
        stored, _record = evidence._control_evidence(point, context, "COMPLETE")
        rows = evidence._control_attempt_rows(
            point, context["control_id"], context["payload_sha256"])
        accepted = [row for row in rows if row[3] == "ACCEPTED"]
        if len(accepted) != 1 or accepted[0][4] is None or accepted[0][5] is None:
            raise ImmutableViolation("completed F context lacks exact attempt evidence")
        rebuilt = evidence._context_evidence(
            point, context, seed1, str(accepted[0][4]),
            json.loads(str(accepted[0][5])))
        if canonical_json(stored) != canonical_json(rebuilt):
            raise ImmutableViolation("completed F context evidence changed")
    health = group["health_control"]
    if states[str(health["control_id"])] != "COMPLETE":
        return
    cancel = group["cancellation_control"]
    stored_cancel, cancel_record = evidence._control_evidence(
        point, cancel, "CANCELLED_UNVERIFIED")
    stored_health, _health_record = evidence._control_evidence(
        point, health, "COMPLETE")
    cancel_rows = evidence._control_attempt_rows(
        point, cancel["control_id"], cancel["request_sha256"])
    health_rows = evidence._control_attempt_rows(
        point, health["control_id"], health["request_sha256"])
    cancel_row = next((row for row in cancel_rows
                       if row[0] == stored_cancel["cancel_attempt_id"]), None)
    evidence._validate_health_not_before(
        point, cancel_record, cancel_row, str(health["control_id"]), health_rows)
    candidates = {row["candidate_id"]: row for row in seed1["candidates"]}
    candidate = candidates[group["candidate_id"]]
    document = corpus.by_id().get(health["source_doc_id"])
    if document is None:
        raise ImmutableViolation("completed F health source is absent")
    source, _view = document.source_for(candidate["chunk_chars"], derived=False)
    chunks = chunker.chunk(source, chunk_chars=candidate["chunk_chars"],
                           overlap_chars=256)
    rebuilt = evidence._health_evidence(
        point, health, cancel_record, source=chunks[health["chunk_index"]].text,
        worksheet=candidate["worksheet"], num_predict=candidate["num_predict"],
        num_ctx=candidate["num_ctx"])
    if canonical_json(stored_health) != canonical_json(rebuilt):
        raise ImmutableViolation("completed F health evidence changed")


def _seed1_progress_prefix(point: Checkpoint, seed1: Mapping[str, Any],
                           controls: Mapping[str, str]) -> None:
    seen_current = False
    for group in seed1["groups"]:
        work = [item for item in seed1["work"]
                if item["candidate_id"] == group["candidate_id"]]
        snapshots = []
        for item in work:
            owner = point.conn.execute(
                "SELECT state FROM work_items WHERE work_id=?",
                (item["work_id"],)).fetchone()
            counts = point.conn.execute(
                "SELECT count(*),sum(state IN ('ACCEPTED','SCHEMA_INVALID')) "
                "FROM attempts WHERE work_id=?", (item["work_id"],)).fetchone()
            snapshots.append((str(owner[0]) if owner else None, int(counts[0]),
                              int(counts[1] or 0)))
        context = controls[str(group["context_control"]["control_id"])]
        cancel = controls[str(group["cancellation_control"]["control_id"])]
        health = controls[str(group["health_control"]["control_id"])]
        untouched = (all(row == ("PENDING", 0, 0) for row in snapshots)
                     and (context, cancel, health) == ("PENDING",) * 3)
        complete = (all(row[0] in {"SUCCEEDED", "COMPLETED_INVALID"}
                        for row in snapshots)
                    and (context, cancel, health)
                    == ("COMPLETE", "CANCELLED_UNVERIFIED", "COMPLETE"))
        terminal = [row[0] in {"SUCCEEDED", "COMPLETED_INVALID"}
                    for row in snapshots]
        prefix = terminal == sorted(terminal, reverse=True)
        later_untouched = all(row[1] == 0 for index, row in enumerate(snapshots)
                              if not terminal[index]
                              and index > next((i for i, done in enumerate(terminal)
                                                if not done), len(terminal)))
        context_pending = (context == "PENDING" and cancel == health == "PENDING"
                           and all(row[1] == 0 for row in snapshots[1:]))
        cancel_order = ((cancel, health) == ("PENDING", "PENDING")
                        or all(terminal) and (cancel, health)
                        == ("CANCELLED_UNVERIFIED", "PENDING"))
        context_done = (context == "COMPLETE" and snapshots[0][2] > 0
                        and prefix and later_untouched
                        and cancel_order)
        progress = context_pending or context_done
        if complete:
            if seen_current:
                raise ImmutableViolation("completed F group follows current work")
        elif untouched:
            seen_current = True
        elif progress and not seen_current:
            seen_current = True
        else:
            raise ImmutableViolation("seed-1 work/control prefix is impossible")


def validate_active_f_namespace(
        point: Checkpoint, *, strict_controls: bool = False) -> None:
    from . import c0b2_runtime_f_evidence as evidence

    if point.state() not in _ACTIVE_STATES:
        raise CheckpointError("active F namespace requires a resumable checkpoint")
    master, master_hash, corpus, _key = evidence._frozen_f_inputs(point)
    _public_budget_namespace(point)
    _runtime_state_namespace(point)
    plans = evidence.seed_plans(master)
    cursor = point.conn.execute(
        "SELECT active_stage,active_plan_key FROM runtime_cursor WHERE id=1").fetchone()
    if not cursor or cursor[0] != "F" or cursor[1] not in {
            "F_SEED_1", *LATER_KEYS, "F_ACCEPTANCE"}:
        raise ImmutableViolation("active F cursor is invalid")
    key = str(cursor[1])
    active = {
        "F_SEED_1": (("F_SEED_1",), (), (), 0),
        "F_SEED_17": (("F_SEED_1", *LATER_KEYS), ("F_SEED_1",),
                      ("stage-f-seed-activation",), 0),
        "F_SEED_20260804": (("F_SEED_1", *LATER_KEYS), ("F_SEED_1",),
                            ("stage-f-seed-activation",), 1),
        "F_ACCEPTANCE": (("F_SEED_1", *LATER_KEYS, "F_ACCEPTANCE"),
                         ("F_SEED_1", "F_SEED_20260804"),
                         ("stage-f-seed-activation",
                          "stage-f-provisional-selection"), 1),
    }[key]
    plan_keys = ("F_SEED_1", *LATER_KEYS,
                 *(("F_ACCEPTANCE",) if key == "F_ACCEPTANCE" else ()))
    census = {
        "plans": tuple(point.conn.execute(
            "SELECT plan_key,budget_stage FROM phase_plans WHERE plan_key LIKE 'F_%' "
            "OR budget_stage='F' ORDER BY rowid")),
        "activations": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM plan_activations WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "aggregates": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM phase_aggregates WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "decisions": tuple(row[0] for row in point.conn.execute(
            "SELECT decision_id FROM decisions WHERE stage='F' OR decision_id LIKE "
            "'stage-f-%' OR decision_id='c0b2-completion' ORDER BY rowid")),
        "events": point.conn.execute(
            "SELECT count(*) FROM events WHERE kind='F_SEED_CURSOR_TRANSITION' OR "
            "detail_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
        "artifacts": point.conn.execute(
            "SELECT count(*) FROM public_artifacts WHERE artifact_id LIKE 'stage-f-%' "
            "OR artifact_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
    }
    expected = {"plans": tuple((row, "F") for row in plan_keys),
                "activations": active[0], "aggregates": active[1],
                "decisions": active[2], "events": active[3], "artifacts": 0}
    post_f202 = (key == "F_SEED_20260804" and census == {
        **expected, "aggregates": ("F_SEED_1", "F_SEED_20260804"),
        "decisions": (*active[2], "stage-f-provisional-selection")})
    if census != expected and not post_f202:
        raise ImmutableViolation("active F namespace is premature or poisoned")
    if post_f202:
        evidence.validate_final_f_owner(point, master, master_hash, corpus)
    for plan_key in ("F_SEED_1", *LATER_KEYS):
        row = point.conn.execute(
            "SELECT plan_hash,plan_json FROM phase_plans WHERE plan_key=?",
            (plan_key,)).fetchone()
        if row != (sha256_json(plans[plan_key]), canonical_json(plans[plan_key])):
            raise ImmutableViolation("active F seed plan changed")
    if key == "F_ACCEPTANCE":
        row = point.conn.execute(
            "SELECT plan_hash,plan_json FROM phase_plans WHERE plan_key=?", (key,)).fetchone()
        if not row:
            raise ImmutableViolation("active F acceptance plan is absent")
        plans[key] = _canonical(str(row[1]), str(row[0]), "active F acceptance plan")
    _exact_work_namespace(
        point, plans, active[0], evidence._work_attempt_evidence, terminal=False)
    _seed1_control_namespace(point, plans["F_SEED_1"], corpus)
    evidence.validate_b4_f_control_census(
        point, allow_dispatching=not strict_controls)
    if not strict_controls:
        return
    if key == "F_SEED_1":
        return
    backup_keys = ((key,) if key in LATER_KEYS
                   else ("F_SEED_20260804", "F_ACCEPTANCE"))
    for backup_key in backup_keys:
        row = point.conn.execute(
            "SELECT p.plan_json,a.activation_json FROM phase_plans p JOIN "
            "plan_activations a ON a.plan_key=p.plan_key WHERE p.plan_key=?",
            (backup_key,)).fetchone()
        if not row:
            raise ImmutableViolation("active F owner is absent")
        plan = json.loads(str(row[0]))
        activation = validate_artifact(PlanActivation, json.loads(str(row[1])))
        evidence.validate_b4_backup_activation(
            point.conn, point.header(), backup_key, plan, activation)


def assert_f_namespace_empty_before_master(point: Checkpoint) -> None:
    counts = {
        "plans": point.conn.execute(
            "SELECT count(*) FROM phase_plans WHERE budget_stage='F' OR plan_key LIKE 'F_%'"
        ).fetchone()[0],
        "invocations": point.conn.execute(
            "SELECT count(*) FROM invocations WHERE stage='F'").fetchone()[0],
        "attempts": point.conn.execute(
            "SELECT count(*) FROM attempts WHERE stage='F'").fetchone()[0],
        "aggregates": point.conn.execute(
            "SELECT count(*) FROM phase_aggregates WHERE plan_key LIKE 'F_%'"
        ).fetchone()[0],
        "decisions": point.conn.execute(
            "SELECT count(*) FROM decisions WHERE stage='F' OR decision_id IN "
            "('stage-f-seed-activation','stage-f-provisional-selection','c0b2-completion')"
        ).fetchone()[0],
        "events": point.conn.execute(
            "SELECT count(*) FROM events WHERE kind='F_SEED_CURSOR_TRANSITION' "
            "OR detail_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
        "artifacts": point.conn.execute(
            "SELECT count(*) FROM public_artifacts WHERE artifact_id LIKE 'stage-f-%' "
            "OR artifact_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
        "legacy_acceptance": point.conn.execute(
            "SELECT count(*) FROM acceptance_plan").fetchone()[0],
    }
    if any(counts.values()):
        raise ImmutableViolation(f"fresh F namespace is not empty: {counts}")


def assert_seed1_replay_namespace(point: Checkpoint) -> None:
    poison = {
        "aggregates": point.conn.execute(
            "SELECT count(*) FROM phase_aggregates WHERE plan_key LIKE 'F_%'"
        ).fetchone()[0],
        "decisions": point.conn.execute(
            "SELECT count(*) FROM decisions WHERE stage='F' OR decision_id IN "
            "('stage-f-seed-activation','stage-f-provisional-selection','c0b2-completion')"
        ).fetchone()[0],
        "events": point.conn.execute(
            "SELECT count(*) FROM events WHERE kind='F_SEED_CURSOR_TRANSITION' "
            "OR detail_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
        "artifacts": point.conn.execute(
            "SELECT count(*) FROM public_artifacts WHERE artifact_id LIKE 'stage-f-%' "
            "OR artifact_json LIKE '%\"stage\":\"F\"%'").fetchone()[0],
        "legacy_acceptance": point.conn.execute(
            "SELECT count(*) FROM acceptance_plan").fetchone()[0],
    }
    if any(poison.values()):
        raise ImmutableViolation(f"seed-1 activation replay has future rows: {poison}")


def later_namespace_census(point: Checkpoint) -> dict[str, tuple[Any, ...]]:
    return {
        "future_aggregates": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM phase_aggregates WHERE plan_key IN "
            "('F_SEED_17','F_SEED_20260804','F_ACCEPTANCE') ORDER BY rowid")),
        "transition_events": tuple(row[0] for row in point.conn.execute(
            "SELECT seq FROM events WHERE kind='F_SEED_CURSOR_TRANSITION' ORDER BY seq")),
        "legacy_acceptance": tuple(row[0] for row in point.conn.execute(
            "SELECT id FROM acceptance_plan ORDER BY id")),
    }


def assert_acceptance_namespace_clean(point: Checkpoint) -> None:
    poison = {
        "acceptance_aggregate": point.conn.execute(
            "SELECT count(*) FROM phase_aggregates WHERE plan_key='F_ACCEPTANCE'"
        ).fetchone()[0],
        "completion": point.conn.execute(
            "SELECT count(*) FROM decisions WHERE decision_id='c0b2-completion'"
        ).fetchone()[0],
        "result": point.conn.execute(
            "SELECT count(*) FROM public_artifacts WHERE artifact_id='stage-f-result'"
        ).fetchone()[0],
        "legacy_acceptance": point.conn.execute(
            "SELECT count(*) FROM acceptance_plan").fetchone()[0],
    }
    if any(poison.values()):
        raise ImmutableViolation(f"acceptance namespace contains premature rows: {poison}")


def assert_terminal_f_namespace(
        point: Checkpoint, plans: Mapping[str, Mapping[str, Any]],
        aggregate_keys: tuple[str, ...], active_keys: tuple[str, ...],
        cursor_key: str, state: str,
        work_validator: Callable[..., list[dict[str, Any]]],
) -> None:
    namespace = {
        "plans": tuple(point.conn.execute(
            "SELECT plan_key,budget_stage FROM phase_plans WHERE plan_key LIKE 'F_%' "
            "OR budget_stage='F' ORDER BY rowid")),
        "aggregates": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM phase_aggregates WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "activations": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM plan_activations WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "registries": tuple(row[0] for row in point.conn.execute(
            "SELECT DISTINCT plan_key FROM phase_work_registry "
            "WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "decisions": tuple(row[0] for row in point.conn.execute(
            "SELECT decision_id FROM decisions WHERE stage='F' OR decision_id LIKE "
            "'stage-f-%' OR decision_id='c0b2-completion' ORDER BY rowid")),
        "events": tuple(row[0] for row in point.conn.execute(
            "SELECT kind FROM events WHERE kind='F_SEED_CURSOR_TRANSITION' "
            "OR detail_json LIKE '%\"stage\":\"F\"%' ORDER BY seq")),
        "artifacts": tuple(row[0] for row in point.conn.execute(
            "SELECT artifact_id FROM public_artifacts WHERE artifact_id LIKE 'stage-f-%' "
            "OR artifact_json LIKE '%\"stage\":\"F\"%' ORDER BY rowid")),
        "legacy_acceptance": tuple(row[0] for row in point.conn.execute(
            "SELECT id FROM acceptance_plan ORDER BY id")),
    }
    expected = {
        "plans": tuple((row, "F") for row in ("F_SEED_1", *LATER_KEYS,
            *(("F_ACCEPTANCE",) if "F_ACCEPTANCE" in active_keys else ()))),
        "aggregates": aggregate_keys, "activations": active_keys,
        "registries": active_keys,
        "decisions": ("stage-f-seed-activation",
                      "stage-f-provisional-selection", "c0b2-completion"),
        "events": (() if active_keys == ("F_SEED_1",)
                   else ("F_SEED_CURSOR_TRANSITION",)),
        "artifacts": ("stage-f-result",), "legacy_acceptance": (),
    }
    cursor = point.conn.execute(
        "SELECT active_stage,active_plan_key FROM runtime_cursor WHERE id=1").fetchone()
    if namespace != expected or point.state() != state or cursor != ("F", cursor_key):
        raise ImmutableViolation("F terminal namespace is partial or conflicting")
    _public_budget_namespace(point)
    _runtime_state_namespace(point)
    _exact_work_namespace(
        point, plans, active_keys, work_validator, terminal=True)
