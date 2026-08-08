"""Read-only evidence and backup ownership for the public Stage-F runtime.

This module never activates work or advances a cursor.  It reconstructs bounded
evidence from checkpoint rows and fails closed at integration boundaries that belong
to the full runtime.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from . import chunker
from .c0b2_checkpoint import (
    Checkpoint, CheckpointError, ImmutableViolation, canonical_json, sha256_json,
)
from .c0b2_plan import attempt_id as stable_attempt_id
from .c0b2_public_schema import (
    AcceptancePlan, CandidateSelection, FMasterPlan, PlanActivation, Sha256, StrictModel,
    validate_artifact,
)
from .c0b2_runtime_common import (
    _bound_control, _cancellation_observation, _context_evidence, _health_evidence,
    _rfc3339, load_runtime_control,
)
from .c0b2_runtime_f_namespace import (
    assert_acceptance_namespace_clean, assert_f_namespace_empty_before_master,
    assert_seed1_replay_namespace, assert_terminal_f_namespace,
    finite_timestamp, later_namespace_census, validate_active_f_namespace,
)
from .c0b2_stage_d import AttemptEvidence
from .c0b2_stage_f import (
    Seed1Evidence, SeedActivationDecision, StageFError,
    build_acceptance_aggregate, build_c44_scored_aggregate, build_final_result,
    build_inconclusive_result, build_no_seed1_provisional_decision,
    build_provisional_decision, build_seed_activation_decision, validate_seed1_evidence,
    validate_stage_f_aggregate_from_attempts,
)
from .c0b2_stage_f_plan import PublicCorpus, build_acceptance_plan

LATER_KEYS = ("F_SEED_17", "F_SEED_20260804")
B4_ACTIVATION_KEYS = frozenset((*LATER_KEYS, "F_ACCEPTANCE"))


class ProvisionalDecision(StrictModel):
    version: Literal["stage-f-selection-v1"]
    stage: Literal["F"]
    plan_sha256: Sha256
    aggregate_sha256: Sha256
    outcome: Literal["PROVISIONAL_SELECTED", "INCONCLUSIVE"]
    reason: Literal[
        "single_qualifier", "pairwise_decisive", "no_seed1_qualifier",
        "no_all_seed_qualifier", "ranking_not_decisive"]
    selection: CandidateSelection | None

    @model_validator(mode="after")
    def selection_matches_outcome(self) -> "ProvisionalDecision":
        selected = self.outcome == "PROVISIONAL_SELECTED"
        if selected != (self.selection is not None):
            raise ValueError("provisional selection presence differs from outcome")
        if selected != (self.reason in {"single_qualifier", "pairwise_decisive"}):
            raise ValueError("provisional reason differs from outcome")
        return self


class F17TerminalWork(StrictModel):
    work_id: Sha256
    state: Literal["SUCCEEDED", "COMPLETED_INVALID"]
    accepted_attempt_id: Sha256 | None

    @model_validator(mode="after")
    def accepted_identity_matches_state(self) -> "F17TerminalWork":
        if (self.state == "SUCCEEDED") != (self.accepted_attempt_id is not None):
            raise ValueError("F17 terminal work acceptance identity contradicts state")
        return self


class FSeedCursorTransition(StrictModel):
    version: Literal["c0b2-f-seed-cursor-transition-v1"]
    run_id: str = Field(min_length=1)
    from_plan_key: Literal["F_SEED_17"]
    to_plan_key: Literal["F_SEED_20260804"]
    f_master_plan_sha256: Sha256
    seed_activation_decision_sha256: Sha256
    from_plan_sha256: Sha256
    from_activation_sha256: Sha256
    to_plan_sha256: Sha256
    to_activation_sha256: Sha256
    activated_from_group_ids: list[Sha256]
    activated_to_group_ids: list[Sha256]
    completed_from_work_ids: list[Sha256]
    completed_from_work_census_sha256: Sha256
    transitioned_at_utc: str = Field(min_length=1)
    transition_sha256: Sha256

    @model_validator(mode="after")
    def self_hash_matches(self) -> "FSeedCursorTransition":
        body = self.model_dump(mode="json", exclude={"transition_sha256"})
        if self.transition_sha256 != sha256_json(body):
            raise ValueError("F seed cursor transition self-hash changed")
        return self


def typed(value: Mapping[str, Any], model: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Stage-F artifact must be a mapping")
    return validate_artifact(model, value)


def canonical_row(raw: str, digest: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"{label} is not valid JSON") from exc
    if (not isinstance(value, dict) or canonical_json(value) != raw
            or sha256_json(value) != digest):
        raise ImmutableViolation(f"{label} hash or canonical encoding changed")
    return value


def _restore_category_order(value: Any) -> Any:
    from .c0b2_runtime_d import _restore_d_category_order
    return _restore_d_category_order(value)


def seed_plans(master: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["payload"]["plan_key"]): dict(row["payload"])
            for row in master["plans"]}


def validate_stored_d_owner(point: Checkpoint) -> tuple[str, dict[str, Any]]:
    from . import c0b2_runtime_d

    validator = getattr(c0b2_runtime_d, "validate_stored_final_d_owner", None)
    if validator is None:
        raise CheckpointError(
            "Stage F requires validate_stored_final_d_owner integration")
    return validator(point, c0b2_runtime_d.load_stage_d_inputs(point))


def _frozen_f_inputs(
        point: Checkpoint,
) -> tuple[dict[str, Any], str, PublicCorpus, bytes]:
    from .c0b2_stage_d_plan import verified_run_nonce_key
    from .c0b2_stage_f_plan import load_public_corpus, validate_f_master_plan

    try:
        master_manifest_hash, master_manifest_raw = point.load_manifest("master")
        f_hash, f_raw = point.load_manifest("f_master")
        _key_hash, key_raw = point.load_manifest("run_nonce_key")
        _c_parent, _c_hash, c_raw = point.load_plan("C")
        key = verified_run_nonce_key(key_raw, c_raw)
        corpus = load_public_corpus(
            json.loads(master_manifest_raw),
            master_manifest_sha256=master_manifest_hash)
        master = validate_f_master_plan(
            canonical_row(f_raw, f_hash, "F master"),
            corpus=corpus, run_nonce_key=key)
        d_digest, d_decision = validate_stored_d_owner(point)
    except Exception as exc:
        raise ImmutableViolation("F control census cannot rebuild its owners") from exc
    candidates = seed_plans(master)["F_SEED_1"]["candidates"]
    selections = [{field: row[field] for field in CandidateSelection.model_fields}
                  for row in candidates]
    if (master["parent_decision_sha256"] != d_digest
            or selections != [row["selection"] for row in d_decision["selections"]]):
        raise ImmutableViolation("F control census differs from stored D finalists")
    return master, f_hash, corpus, key


def _phase_windows(point: Checkpoint, plans: Mapping[str, Mapping[str, Any]]
                   ) -> list[tuple[float, float | None, str]]:
    activations = {str(row[0]): finite_timestamp(row[1], "F activation")
                   for row in point.conn.execute(
        "SELECT plan_key,created FROM plan_activations WHERE plan_key LIKE 'F_%'")}
    if "F_SEED_1" not in activations:
        raise ImmutableViolation("F control census lacks seed-1 activation")
    marker = point.conn.execute(
        "SELECT created FROM events WHERE kind='F_SEED_CURSOR_TRANSITION'"
    ).fetchall()
    acceptance = activations.get("F_ACCEPTANCE")
    f17_created = activations.get("F_SEED_17")
    f202_created = activations.get("F_SEED_20260804")
    if (f17_created is None) != (f202_created is None):
        raise ImmutableViolation("later F activations are not an exact pair")
    paired = (max(f17_created, f202_created)
              if f17_created is not None and f202_created is not None else None)
    windows = [(activations["F_SEED_1"], paired, "F_SEED_1")]
    if paired is not None:
        if len(marker) > 1:
            raise ImmutableViolation("F seed cursor marker is not unique")
        transition = finite_timestamp(marker[0][0], "F transition") if marker else None
        windows.append((paired, transition, "F_SEED_17"))
        if transition is not None:
            windows.append((transition, acceptance, "F_SEED_20260804"))
    if acceptance is not None:
        if not marker:
            raise ImmutableViolation("acceptance activation predates seed transition")
        windows.append((acceptance, None, "F_ACCEPTANCE"))
    if any(key not in plans and key != "F_ACCEPTANCE" for _start, _end, key in windows):
        raise ImmutableViolation("F control census phase is absent from master")
    return windows


def validate_b4_f_control_census(
        point: Checkpoint, *, allow_dispatching: bool = False) -> None:
    from .c0b2_executor import SERVER_CONTROL_MODEL, control_id, resource_probe_id
    from .c0b2_runtime_d import _preflight_specs, _resource_probe_spec
    from .c0b2_transport import request_spec_hash

    master, _master_hash, _corpus, _key = _frozen_f_inputs(point)
    plans = seed_plans(master)
    acceptance_row = point.conn.execute(
        "SELECT plan_json FROM phase_plans WHERE plan_key='F_ACCEPTANCE'").fetchone()
    if acceptance_row:
        plans["F_ACCEPTANCE"] = json.loads(str(acceptance_row[0]))
    windows = _phase_windows(point, plans)

    def phase_at(when: float) -> str:
        matches = [key for start, end, key in windows
                   if start <= when and (end is None or when < end)]
        if len(matches) != 1:
            raise ImmutableViolation("F control falls outside one active plan window")
        return matches[0]

    invocations = [(int(row[0]), finite_timestamp(row[1], "F invocation"))
                   for row in point.conn.execute(
        "SELECT ordinal,created FROM invocations WHERE stage='F' ORDER BY ordinal")]
    if ([row[0] for row in invocations] != list(range(1, len(invocations) + 1))
            or any(left[1] >= right[1]
                   for left, right in zip(invocations, invocations[1:]))):
        raise ImmutableViolation("F invocation chronology changed")
    invocation_windows = {
        ordinal: (created, invocations[index + 1][1]
                  if index + 1 < len(invocations) else None)
        for index, (ordinal, created) in enumerate(invocations)}
    header = point.header()
    generic_hashes = {
        model: request_spec_hash(_resource_probe_spec(point, model))
        for model in header["model_digests"]}

    def active_candidates(key: str) -> list[dict[str, Any]]:
        if key not in LATER_KEYS:
            return list(plans[key]["candidates"])
        row = point.conn.execute(
            "SELECT activation_json FROM plan_activations WHERE plan_key=?",
            (key,)).fetchone()
        activation = typed(json.loads(str(row[0])), PlanActivation) if row else None
        groups = {row["group_id"]: row["candidate_id"] for row in plans[key]["groups"]}
        candidates = {row["candidate_id"]: row for row in plans[key]["candidates"]}
        return [candidates[groups[group]] for group in (
            activation["activated_group_ids"] if activation else ())]

    def preflights(key: str) -> list[tuple[str, str, Any]]:
        candidates = active_candidates(key)
        ids = {row["candidate_id"] for row in candidates}
        plan = {**plans[key], "candidates": candidates,
                "work": [row for row in plans[key]["work"]
                         if row["candidate_id"] in ids]}
        return _preflight_specs(header, SimpleNamespace(plan=plan))

    planned_controls: dict[str, tuple[str, str, set[str], str, int | None]] = {}
    seed1 = plans["F_SEED_1"]
    for group in seed1["groups"]:
        planned_controls[group["context_control"]["control_id"]] = (
            group["context_control"]["payload_sha256"], "preflight_probe",
            {"ACCEPTED"}, "F_SEED_1", None)
        planned_controls[group["cancellation_control"]["control_id"]] = (
            group["cancellation_control"]["request_sha256"], "preflight_probe",
            {"CANCELLED_UNVERIFIED"}, "F_SEED_1", None)
        planned_controls[group["health_control"]["control_id"]] = (
            group["health_control"]["request_sha256"], "preflight_probe",
            {"ACCEPTED", "SCHEMA_INVALID"}, "F_SEED_1", None)

    attempts = list(point.conn.execute(
        "SELECT attempt_id,control_id,invocation_ordinal,call_class,attempt_no,"
        "request_hash,state,response,created,updated FROM attempts "
        "WHERE stage='F' AND control_id IS NOT NULL ORDER BY control_id,attempt_no"))
    grouped: dict[str, list[tuple[Any, ...]]] = {}
    for row in attempts:
        grouped.setdefault(str(row[1]), []).append(row)
    seen_preflight: dict[int, set[str]] = {ordinal: set() for ordinal, _ in invocations}
    classifications: dict[str, str] = {}
    for identity, rows in grouped.items():
        group_exact: tuple[str, str, set[str], str, int | None] | None = None
        expected_class: str | None = None
        for index, row in enumerate(rows, 1):
            _attempt, _control, ordinal, call_class, number, request_hash, state, \
                response, created, updated = row
            created = finite_timestamp(created, "F control attempt creation")
            updated = finite_timestamp(updated, "F control attempt update")
            if type(ordinal) is not int or ordinal not in invocation_windows:
                raise ImmutableViolation("F control lacks invocation ownership")
            invoked, next_invoked = invocation_windows[ordinal]
            if (created < invoked or next_invoked is not None and created >= next_invoked):
                raise ImmutableViolation("F control lies outside its invocation window")
            key = phase_at(created)
            if phase_at(invoked) != key:
                raise ImmutableViolation("F invocation crossed a plan boundary")
            exact = planned_controls.get(identity)
            if exact is None:
                for kind, model, spec in preflights(key):
                    if identity == control_id("F", ordinal, kind, model):
                        exact = (request_spec_hash(spec), "preflight_probe",
                                 {"ACCEPTED"}, key, ordinal)
                        seen_preflight[ordinal].add(identity)
                        classifications[identity] = "standard_preflight"
                        break
            if exact is None:
                models = {candidate["model"] for candidate in active_candidates(key)}
                probes = [(model, generic_hashes[model], "generic_recovery")
                          for model in models]
                if key == "F_SEED_1":
                    probes.extend((
                        candidate["model"], next(
                            row["request_sha256"] for row in seed1["work"]
                            if row["work_id"] == group["first_work_id"]),
                        f"trigger_recovery:{group['candidate_id']}")
                        for group in seed1["groups"]
                        for candidate in seed1["candidates"]
                        if candidate["candidate_id"] == group["candidate_id"])
                for model, digest, classification in probes:
                    if identity == resource_probe_id("F", ordinal, model, digest):
                        exact = (digest, "transport_orphan",
                                 {"ACCEPTED", "SCHEMA_INVALID"}, key, ordinal)
                        classifications[identity] = classification
                        break
            if exact is None or exact[3] != key:
                raise ImmutableViolation("F control charge has no exact plan owner")
            if group_exact is None:
                group_exact, expected_class = exact, exact[1]
                classifications.setdefault(identity, "planned_control")
            elif exact != group_exact:
                raise ImmutableViolation("F control changed ownership across attempts")
            allowed = exact[2] | {"RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN",
                                  "CANCELLED_UNVERIFIED"}
            allowed |= {"DISPATCHING"} if allow_dispatching else set()
            answered = state in {"ACCEPTED", "SCHEMA_INVALID"}
            if (_attempt != stable_attempt_id(f"control:{identity}", index)
                    or number != index or call_class != expected_class
                    or request_hash != exact[0] or state not in allowed
                    or answered != isinstance(response, str)
                    or (index > 1 and finite_timestamp(
                        rows[index - 2][9], "F prior control attempt") > created)):
                raise ImmutableViolation("F control attempt identity or sequence changed")
            expected_class = ({"RETRYABLE_TRANSPORT": "transport_orphan",
                               "ORPHANED_UNKNOWN": "transport_orphan",
                               "CANCELLED_UNVERIFIED": "transport_orphan",
                               "SCHEMA_INVALID": "schema_retry"}.get(state))
    for ordinal, invoked in invocations:
        key = phase_at(invoked)
        required_order = [control_id("F", ordinal, kind, model)
                          for kind, model, _spec in preflights(key)]
        non_preflight = point.conn.execute(
            "SELECT work_id,control_id FROM attempts WHERE stage='F' "
            "AND invocation_ordinal=?", (ordinal,)).fetchall()
        charged_order: list[str] = []
        for row in point.conn.execute(
                "SELECT control_id FROM attempts WHERE stage='F' "
                "AND invocation_ordinal=? ORDER BY created,rowid", (ordinal,)):
            identity = str(row[0])
            if (classifications.get(identity) == "standard_preflight"
                    and (not charged_order or identity != charged_order[-1])):
                charged_order.append(identity)
        complete_required = any(
            row[0] is not None
            or classifications.get(str(row[1])) != "standard_preflight"
            for row in non_preflight)
        accepted = lambda identity: point.conn.execute(
            "SELECT 1 FROM attempts WHERE control_id=? AND state='ACCEPTED'",
            (identity,)).fetchone()
        first_non_preflight = min((finite_timestamp(
            row[0], "F non-preflight attempt") for row in point.conn.execute(
            "SELECT created FROM attempts WHERE stage='F' AND invocation_ordinal=? "
            "AND (work_id IS NOT NULL OR control_id IS NULL OR control_id NOT IN ("
            + ",".join("?" for _ in required_order) + "))",
            (ordinal, *required_order))), default=None)
        last_preflight = max((finite_timestamp(
            row[0], "F preflight attempt") for row in point.conn.execute(
            "SELECT updated FROM attempts WHERE control_id IN ("
            + ",".join("?" for _ in required_order) + ")",
            tuple(required_order))), default=None)
        if (charged_order != required_order[:len(charged_order)]
                or seen_preflight[ordinal] != set(charged_order)
                or (first_non_preflight is not None and last_preflight is not None
                    and first_non_preflight < last_preflight)
                or complete_required and (
                    charged_order != required_order
                    or any(not accepted(identity) for identity in required_order))
                or not complete_required and any(
                    not accepted(identity) for identity in charged_order[:-1])):
            raise ImmutableViolation("F invocation preflight census is incomplete")
    complete_contexts = {str(row[0]) for row in point.conn.execute(
        "SELECT control_id FROM runtime_controls WHERE plan_key='F_SEED_1' "
        "AND kind='context_probe' AND state='COMPLETE'")}
    _validate_context_adjacency(point, seed1, classifications, complete_contexts)


def _validate_context_adjacency(
        point: Checkpoint, seed1: Mapping[str, Any],
        classifications: Mapping[str, str], complete_contexts: set[str]) -> None:
    """Prove each trigger/recovery/context sequence has no relevant interloper."""
    for group in seed1["groups"]:
        if str(group["context_control"]["control_id"]) not in complete_contexts:
            continue
        candidate_id = str(group["candidate_id"])
        work_ids = [row["work_id"] for row in seed1["work"]
                    if row["candidate_id"] == candidate_id]
        trigger = point.conn.execute(
            "SELECT updated FROM attempts WHERE work_id=? "
            "AND state IN ('ACCEPTED','SCHEMA_INVALID') "
            "ORDER BY attempt_no LIMIT 1", (group["first_work_id"],)).fetchone()
        context_id = str(group["context_control"]["control_id"])
        context = point.conn.execute(
            "SELECT created FROM attempts WHERE control_id=? AND state='ACCEPTED' "
            "ORDER BY attempt_no LIMIT 1", (context_id,)).fetchone()
        trigger_at = (finite_timestamp(trigger[0], "F context trigger")
                      if trigger else None)
        context_at = (finite_timestamp(context[0], "F context attempt")
                      if context else None)
        if trigger_at is None or context_at is None or trigger_at > context_at:
            raise ImmutableViolation("F context barrier timestamps changed")
        between = point.conn.execute(
            "SELECT rowid,work_id,control_id,state,updated FROM attempts "
            "WHERE stage='F' AND created>=? AND created<? ORDER BY created,rowid",
            (trigger_at, context_at)).fetchall()
        allowed_recovery = f"trigger_recovery:{candidate_id}"
        answered_recovery: tuple[int, float] | None = None
        for rowid, work_id, control_id_value, state, updated in between:
            classification = classifications.get(str(control_id_value))
            if work_id is not None:
                raise ImmutableViolation("scored work crossed a pending F context barrier")
            if classification == "standard_preflight":
                continue
            if classification != allowed_recovery:
                raise ImmutableViolation(
                    "unrelated recovery/control crossed the F context barrier")
            if state in {"ACCEPTED", "SCHEMA_INVALID"}:
                answered_recovery = (
                    int(rowid), finite_timestamp(updated, "F trigger recovery"))
        if answered_recovery is not None:
            relevant = point.conn.execute(
                "SELECT work_id,control_id,created FROM attempts WHERE stage='F' "
                "AND rowid>? ORDER BY rowid", (answered_recovery[0],)).fetchall()
            next_relevant = next((row for row in relevant
                                  if classifications.get(str(row[1]))
                                  != "standard_preflight"), None)
            if (next_relevant is None or next_relevant[:2] != (None, context_id)
                    or answered_recovery[1] > finite_timestamp(
                        next_relevant[2], "F post-recovery context")):
                raise ImmutableViolation(
                    "planned /api/ps is not immediate after trigger recovery")
        # The candidate owns no other scored work before the context completion.
        next_work = point.conn.execute(
            "SELECT min(created) FROM attempts WHERE work_id IN ("
            + ",".join("?" for _ in work_ids[1:]) + ")",
            tuple(work_ids[1:])).fetchone()[0]
        if next_work is not None and context_at > finite_timestamp(
                next_work, "F next candidate work"):
            raise ImmutableViolation("candidate work predates its /api/ps barrier")


def _plan_time_window(point: Checkpoint, plan_key: str
                      ) -> tuple[float, float | None] | None:
    activations = {str(row[0]): finite_timestamp(row[1], "F activation")
                   for row in point.conn.execute(
        "SELECT plan_key,created FROM plan_activations WHERE plan_key LIKE 'F_%'")}
    marker = point.conn.execute(
        "SELECT created FROM events WHERE kind='F_SEED_CURSOR_TRANSITION'"
    ).fetchall()
    paired_values = [activations[key] for key in LATER_KEYS if key in activations]
    paired = max(paired_values) if len(paired_values) == 2 else None
    transition = (finite_timestamp(marker[0][0], "F transition")
                  if len(marker) == 1 else None)
    if plan_key == "F_SEED_1" and "F_SEED_1" in activations:
        return activations["F_SEED_1"], paired
    if plan_key == "F_SEED_17" and paired is not None:
        return paired, transition
    if plan_key == "F_SEED_20260804" and transition is not None:
        return transition, activations.get("F_ACCEPTANCE")
    if plan_key == "F_ACCEPTANCE" and "F_ACCEPTANCE" in activations:
        return activations["F_ACCEPTANCE"], None
    return None


def attempt_in_invocation(
        point: Checkpoint, ordinal: int, created: float,
        plan_key: str = "F_SEED_1",
) -> bool:
    invocation = point.conn.execute(
        "SELECT created FROM invocations WHERE stage='F' AND ordinal=?",
        (ordinal,)).fetchone()
    next_invocation = point.conn.execute(
        "SELECT created FROM invocations WHERE stage='F' AND ordinal>? "
        "ORDER BY ordinal LIMIT 1", (ordinal,)).fetchone()
    plan_window = _plan_time_window(point, plan_key)
    created = finite_timestamp(created, "F attempt creation")
    invoked = (finite_timestamp(invocation[0], "F invocation")
               if invocation else None)
    next_invoked = (finite_timestamp(next_invocation[0], "next F invocation")
                    if next_invocation else None)
    return bool(invoked is not None and plan_window
                and invoked <= created
                and (next_invoked is None or created < next_invoked)
                and plan_window[0] <= invoked
                and (plan_window[1] is None
                     or invoked < plan_window[1])
                and plan_window[0] <= created
                and (plan_window[1] is None or created < plan_window[1]))


def _control_attempt_rows(point: Checkpoint, control_id: str,
                          request_hash: str) -> list[tuple[Any, ...]]:
    rows = point.conn.execute(
        "SELECT attempt_id,attempt_no,call_class,state,response,metadata_json,"
        "stage,invocation_ordinal,request_hash,created,updated FROM attempts "
        "WHERE control_id=? ORDER BY attempt_no", (control_id,)).fetchall()
    for index, row in enumerate(rows):
        (attempt, number, call_class, state, _response, metadata_raw,
         stage, ordinal, digest, created, _updated) = row
        previous = rows[index - 1][3] if index else None
        expected_class = ("preflight_probe" if index == 0 else
                          "schema_retry" if previous == "SCHEMA_INVALID" else
                          "transport_orphan" if previous in {
                              "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN",
                              "CANCELLED_UNVERIFIED"} else None)
        try:
            metadata = json.loads(metadata_raw) if metadata_raw is not None else {}
        except (TypeError, json.JSONDecodeError) as exc:
            raise ImmutableViolation("control attempt metadata is not valid JSON") from exc
        if (number != index + 1
                or attempt != stable_attempt_id(f"control:{control_id}", int(number))
                or call_class != expected_class or stage != "F" or digest != request_hash
                or type(ordinal) is not int
                or not attempt_in_invocation(
                    point, int(ordinal), finite_timestamp(
                        created, "F control attempt creation"))
                or (metadata_raw is not None and canonical_json(metadata) != metadata_raw)
                or (index and finite_timestamp(
                    rows[index - 1][10], "F prior control update")
                    > finite_timestamp(created, "F control attempt creation"))):
            raise ImmutableViolation("control attempt identity, time, or sequence changed")
    return rows


def _control_evidence(point: Checkpoint, control: Mapping[str, Any],
                      state: str) -> tuple[dict[str, Any], Any]:
    control_id, kind = str(control["control_id"]), str(control["kind"])
    normalized = _bound_control(point, "F_SEED_1", kind, control)
    record = load_runtime_control(point, control_id)
    control_raw, control_hash = canonical_json(normalized), sha256_json(normalized)
    if (record.plan_key != "F_SEED_1" or record.kind != kind
            or record.control_sha256 != control_hash or record.control_json != control_raw
            or record.state != state or record.evidence_json is None
            or record.evidence_sha256 is None):
        raise ImmutableViolation("seed-1 runtime control changed or is incomplete")
    evidence = canonical_row(
        record.evidence_json, record.evidence_sha256, f"{kind} evidence")
    events = point.conn.execute(
        "SELECT seq,state,evidence_hash,evidence_json,not_before_utc "
        "FROM runtime_control_events WHERE control_id=? ORDER BY seq",
        (control_id,)).fetchall()
    expected = [(1, "PENDING", None, None, None),
                (2, state, record.evidence_sha256, record.evidence_json,
                 record.not_before_utc)]
    if events != expected:
        raise ImmutableViolation("seed-1 runtime control event history changed")
    return evidence, record


def _work_attempt_evidence(
        point: Checkpoint, work: Mapping[str, Any], *, terminal_required: bool = True,
) -> list[dict[str, Any]]:
    work_id = str(work["work_id"])
    owner = point.conn.execute(
        "SELECT stage,cell_id,request_hash,state,accepted_attempt_id "
        "FROM work_items WHERE work_id=?", (work_id,)).fetchone()
    if (not owner or owner[:3] != ("F", work["cell_id"], work["request_sha256"])
            or owner[3] not in {"PENDING", "SUCCEEDED", "COMPLETED_INVALID"}
            or terminal_required and owner[3] == "PENDING"
            or owner[3] == "PENDING" and owner[4] is not None):
        raise ImmutableViolation("seed-1 work is absent, changed, or incomplete")
    rows = point.conn.execute(
        "SELECT attempt_id,attempt_no,call_class,state,response,metadata_json,"
        "request_hash,stage,invocation_ordinal,created,updated FROM attempts "
        "WHERE work_id=? ORDER BY attempt_no", (work_id,)).fetchall()
    evidence = []
    for index, row in enumerate(rows):
        (attempt, number, call_class, state, response, metadata_raw,
         request_hash, stage, ordinal, created, _updated) = row
        previous = rows[index - 1][3] if index else None
        expected_class = ("scored" if index == 0 else
                          "schema_retry" if previous == "SCHEMA_INVALID" else
                          "transport_orphan" if previous in {
                              "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN",
                              "CANCELLED_UNVERIFIED"} else None)
        try:
            metadata = json.loads(metadata_raw) if metadata_raw is not None else {}
        except (TypeError, json.JSONDecodeError) as exc:
            raise ImmutableViolation("seed-1 attempt metadata is not valid JSON") from exc
        answered = state in {"ACCEPTED", "SCHEMA_INVALID"}
        created = finite_timestamp(created, "F work attempt creation")
        updated = finite_timestamp(_updated, "F work attempt update")
        if (number != index + 1 or attempt != stable_attempt_id(work_id, int(number))
                or call_class != expected_class or request_hash != work["request_sha256"]
                or stage != "F" or type(ordinal) is not int
                or not attempt_in_invocation(
                    point, int(ordinal), created, str(work["plan_key"]))
                or (metadata_raw is not None and canonical_json(metadata) != metadata_raw)
                or (index and finite_timestamp(
                    rows[index - 1][10], "F prior work update") > created)):
            raise ImmutableViolation("seed-1 attempt identity, time, or sequence changed")
        if state == "DISPATCHING":
            if (terminal_required or owner[3] != "PENDING" or index != len(rows) - 1
                    or response is not None or metadata):
                raise ImmutableViolation("seed-1 dispatching attempt is not current")
            continue
        value = {
            "attempt_id": attempt, "work_id": work_id, "attempt_no": number,
            "call_class": call_class, "request_sha256": request_hash,
            "state": state, "response": response,
            "done_reason": metadata.get("done_reason") if answered else None,
            "prompt_eval_count": metadata.get("prompt_eval_count") if answered else None,
            "tools_empty": metadata.get("tools_empty") if answered else None,
            "images_empty": metadata.get("images_empty") if answered else None,
            "unknown_message_fields_empty": (
                metadata.get("unknown_message_fields_empty") if answered else None),
        }
        try:
            evidence.append(AttemptEvidence.model_validate(
                value, strict=True).model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ImmutableViolation("seed-1 attempt facts are malformed") from exc
    accepted = [row["attempt_id"] for row in evidence if row["state"] == "ACCEPTED"]
    invalid = sum(row["state"] == "SCHEMA_INVALID" for row in evidence)
    if owner[3] == "PENDING":
        if accepted or invalid > 1:
            raise ImmutableViolation("pending F work contradicts its attempt history")
        return evidence
    if ((owner[3] == "SUCCEEDED" and (accepted != [owner[4]] or invalid > 1))
            or (owner[3] == "COMPLETED_INVALID"
                and (owner[4] is not None or accepted or invalid != 2))
            or not evidence or evidence[-1]["state"] != (
                "ACCEPTED" if owner[3] == "SUCCEEDED" else "SCHEMA_INVALID")):
        raise ImmutableViolation("seed-1 work terminal state differs from attempts")
    return evidence


def _validate_health_not_before(
        point: Checkpoint, cancel_record: Any, cancel_row: tuple[Any, ...] | None,
        health_control_id: str, health_rows: list[tuple[Any, ...]],
) -> None:
    """Independently bind health dispatch and its invocation to durable delay state."""
    try:
        not_before = datetime.fromisoformat(
            str(cancel_record.not_before_utc).removesuffix("Z")
            + "+00:00").timestamp()
    except (TypeError, ValueError) as exc:
        raise ImmutableViolation(
            "cancellation health lacks a valid durable not-before") from exc
    if cancel_row is None or not health_rows:
        raise ImmutableViolation(
            "cancellation/health controls crossed candidate or invocation order")
    cancel_ordinal, health_ordinal = int(cancel_row[7]), int(health_rows[0][7])
    invocations = point.conn.execute(
        "SELECT ordinal,created FROM invocations WHERE stage='F' AND ordinal>? "
        "AND ordinal<=? ORDER BY ordinal", (cancel_ordinal, health_ordinal)).fetchall()
    non_preflight = tuple(row[0] for row in point.conn.execute(
        "SELECT DISTINCT a.invocation_ordinal FROM attempts a WHERE a.stage='F' "
        "AND a.invocation_ordinal>? AND a.invocation_ordinal<=? AND ("
        "a.work_id IS NOT NULL OR a.control_id IN (SELECT control_id FROM "
        "runtime_controls WHERE plan_key LIKE 'F_%') OR (a.control_id IS NOT NULL "
        "AND a.attempt_no=1 AND a.call_class!='preflight_probe')) "
        "ORDER BY a.invocation_ordinal", (cancel_ordinal, health_ordinal)))
    first_non_preflight = point.conn.execute(
        "SELECT a.control_id FROM attempts a WHERE a.stage='F' "
        "AND a.invocation_ordinal>? AND a.invocation_ordinal<=? AND ("
        "a.work_id IS NOT NULL OR a.control_id IN (SELECT control_id FROM "
        "runtime_controls WHERE plan_key LIKE 'F_%') OR (a.control_id IS NOT NULL "
        "AND a.attempt_no=1 AND a.call_class!='preflight_probe')) "
        "ORDER BY a.invocation_ordinal,a.created,a.rowid LIMIT 1",
        (cancel_ordinal, health_ordinal)).fetchone()
    expected_ordinals = list(range(cancel_ordinal + 1, health_ordinal + 1))
    early_attempt = point.conn.execute(
        "SELECT 1 FROM attempts WHERE stage='F' AND invocation_ordinal>? "
        "AND invocation_ordinal<=? AND created<? LIMIT 1",
        (cancel_ordinal, health_ordinal, not_before)).fetchone()
    if ([int(row[0]) for row in invocations] != expected_ordinals
            or any(finite_timestamp(row[1], "F health invocation") < not_before
                   for row in invocations)
            or non_preflight != (health_ordinal,) or early_attempt
            or first_non_preflight != (health_control_id,)
            or finite_timestamp(
                health_rows[0][9], "F health attempt") < not_before):
        raise ImmutableViolation(
            "cancellation/health controls crossed candidate or invocation order")


def seed1_inputs(
        point: Checkpoint, master: Mapping[str, Any], corpus: PublicCorpus,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]],
           dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rebuild every seed-1 scorer input from durable work/control rows."""
    if (not isinstance(corpus, PublicCorpus)
            or corpus.master_manifest_sha256 != point.header()["master_manifest_sha256"]):
        raise ImmutableViolation("public corpus differs from the frozen manifest")
    seed1 = seed_plans(master)["F_SEED_1"]
    attempts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    health: dict[str, dict[str, Any]] = {}
    candidates = {row["candidate_id"]: row for row in seed1["candidates"]}
    previous_candidate_end: float | None = None
    for group in seed1["groups"]:
        candidate_id = str(group["candidate_id"])
        candidate = candidates[candidate_id]
        work = [row for row in seed1["work"] if row["candidate_id"] == candidate_id]
        attempts[candidate_id] = {
            str(row["work_id"]): _work_attempt_evidence(point, row) for row in work}
        windows = [point.conn.execute(
            "SELECT min(created),max(updated) FROM attempts WHERE work_id=?",
            (row["work_id"],)).fetchone() for row in work]
        if any(row[0] is None or row[1] is None for row in windows):
            raise ImmutableViolation("seed-1 work crossed frozen serial order")
        windows = [(finite_timestamp(row[0], "F work window start"),
                    finite_timestamp(row[1], "F work window end")) for row in windows]
        if (any(windows[index][1] > windows[index + 1][0]
                for index in range(len(work) - 1))
                or (previous_candidate_end is not None
                    and previous_candidate_end > windows[0][0])):
            raise ImmutableViolation("seed-1 work crossed frozen serial order")
        first_answered = point.conn.execute(
            "SELECT work_id,updated FROM attempts WHERE work_id IN ("
            + ",".join("?" for _ in work)
            + ") AND state IN ('ACCEPTED','SCHEMA_INVALID') "
              "ORDER BY created,attempt_no LIMIT 1",
            tuple(row["work_id"] for row in work)).fetchone()
        if not first_answered or first_answered[0] != group["first_work_id"]:
            raise ImmutableViolation("context trigger is not first answered candidate work")
        context, _context_record = _control_evidence(
            point, group["context_control"], "COMPLETE")
        context_rows = _control_attempt_rows(
            point, group["context_control"]["control_id"],
            group["context_control"]["payload_sha256"])
        accepted_context = [row for row in context_rows if row[3] == "ACCEPTED"]
        if len(accepted_context) != 1 or accepted_context[0][4] is None \
                or accepted_context[0][5] is None:
            raise ImmutableViolation("context probe lacks one accepted attempt")
        context_created, context_updated = (
            finite_timestamp(accepted_context[0][9], "F context creation"),
            finite_timestamp(accepted_context[0][10], "F context update"))
        if (finite_timestamp(first_answered[1], "F first answer") > context_created
                or context_updated > windows[1][0]):
            raise ImmutableViolation("planned /api/ps is not the immediate work barrier")
        rebuilt_context = _context_evidence(
            point, group["context_control"], seed1,
            str(accepted_context[0][4]), json.loads(str(accepted_context[0][5])))
        if canonical_json(context) != canonical_json(rebuilt_context):
            raise ImmutableViolation("context probe differs from persisted attempts")
        cancel, cancel_record = _control_evidence(
            point, group["cancellation_control"], "CANCELLED_UNVERIFIED")
        cancel_rows = _control_attempt_rows(
            point, group["cancellation_control"]["control_id"],
            group["cancellation_control"]["request_sha256"])
        _cancellation_observation(cancel, group["cancellation_control"]["control_id"])
        stored_health, _health_record = _control_evidence(
            point, group["health_control"], "COMPLETE")
        health_rows = _control_attempt_rows(
            point, group["health_control"]["control_id"],
            group["health_control"]["request_sha256"])
        cancel_row = next((row for row in cancel_rows
                           if row[0] == cancel["cancel_attempt_id"]), None)
        _validate_health_not_before(
            point, cancel_record, cancel_row,
            str(group["health_control"]["control_id"]), health_rows)
        if (cancel_row is None or not health_rows or finite_timestamp(
                cancel_row[9], "F cancellation creation") < windows[-1][1]):
            raise ImmutableViolation(
                "cancellation/health controls crossed candidate or invocation order")
        document = corpus.by_id().get(group["health_control"]["source_doc_id"])
        if document is None:
            raise ImmutableViolation("health control public source is absent")
        source, _view = document.source_for(candidate["chunk_chars"], derived=False)
        chunks = chunker.chunk(
            source, chunk_chars=candidate["chunk_chars"], overlap_chars=256)
        rebuilt_health = _health_evidence(
            point, group["health_control"], cancel_record,
            source=chunks[group["health_control"]["chunk_index"]].text,
            worksheet=candidate["worksheet"], num_predict=candidate["num_predict"],
            num_ctx=candidate["num_ctx"])
        if canonical_json(stored_health) != canonical_json(rebuilt_health):
            raise ImmutableViolation("cancellation health differs from persisted attempts")
        previous_candidate_end = max(
            windows[-1][1],
            max(finite_timestamp(row[10], "F cancellation update")
                for row in cancel_rows),
            max(finite_timestamp(row[10], "F health update")
                for row in health_rows),
        )
        contexts[candidate_id], health[candidate_id] = context, stored_health
    return attempts, contexts, health


def all_seed_attempt_inputs(
        point: Checkpoint, master: Mapping[str, Any],
        seed1_evidence: Mapping[str, Any],
) -> dict[str, dict[int, dict[str, list[dict[str, Any]]]]]:
    """Extract exact terminal attempt maps for every activated candidate/seed."""
    parsed_seed1 = typed(seed1_evidence, Seed1Evidence)
    qualified = {row["candidate_id"] for row in parsed_seed1["candidates"]
                 if row["qualified"]}
    result: dict[str, dict[int, dict[str, list[dict[str, Any]]]]] = {
        candidate_id: {} for candidate_id in master["base_candidate_order"]}
    for envelope in master["plans"]:
        plan = envelope["payload"]
        validate_plan_work_serial_order(point, plan)
        seed = int(plan["work"][0]["seed"])
        for candidate_id in master["base_candidate_order"]:
            if seed != 1 and candidate_id not in qualified:
                continue
            work = [row for row in plan["work"]
                    if row["candidate_id"] == candidate_id]
            registered = tuple(row[0] for row in point.conn.execute(
                "SELECT work_id FROM phase_work_registry WHERE plan_key=? "
                "AND activation_group_id=? ORDER BY rowid",
                (plan["plan_key"], next(group["group_id"] for group in plan["groups"]
                                        if group["candidate_id"] == candidate_id))))
            if registered != tuple(row["work_id"] for row in work):
                raise ImmutableViolation("F aggregate attempts differ from registry")
            result[candidate_id][seed] = {
                row["work_id"]: _work_attempt_evidence(point, row) for row in work}
    return result


def validate_plan_work_serial_order(
        point: Checkpoint, plan: Mapping[str, Any],
) -> tuple[str, ...]:
    """Prove one activated F plan follows its registry order without work interleave."""
    key = str(plan["plan_key"])
    activation_row = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key=?", (key,),
    ).fetchone()
    if not activation_row:
        raise ImmutableViolation(f"{key} serial census lacks its activation")
    activation = typed(canonical_row(
        str(activation_row[1]), str(activation_row[0]), f"{key} activation"),
        PlanActivation)
    if activation["plan_sha256"] != sha256_json(plan):
        raise ImmutableViolation(f"{key} serial census differs from its plan")
    expected = ([str(item["work_id"]) for item in plan["work"]]
                if key == "F_ACCEPTANCE" else [
                    str(item["work_id"])
                    for group in activation["activated_group_ids"]
                    for item in plan["work"]
                    if item["activation_group_id"] == group])
    registry = [str(row[0]) for row in point.conn.execute(
        "SELECT work_id FROM phase_work_registry WHERE plan_key=? ORDER BY rowid",
        (key,))]
    if registry != expected:
        raise ImmutableViolation(f"{key} work registry differs from activation order")
    previous_end: float | None = None
    for work_id in registry:
        window = point.conn.execute(
            "SELECT min(created),max(updated) FROM attempts WHERE work_id=?",
            (work_id,)).fetchone()
        start = (finite_timestamp(window[0], f"{key} work start")
                 if window and window[0] is not None else None)
        end = (finite_timestamp(window[1], f"{key} work end")
               if window and window[1] is not None else None)
        if (start is None or end is None
                or previous_end is not None and previous_end > start):
            raise ImmutableViolation(f"{key} work crossed frozen serial order")
        previous_end = end
    return tuple(registry)


def acceptance_attempt_inputs(
        point: Checkpoint, acceptance_plan: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    plan = typed(acceptance_plan, AcceptancePlan)
    validate_plan_work_serial_order(point, plan)
    return {str(work["work_id"]): _work_attempt_evidence(point, work)
            for work in plan["work"]}


def connection_decision(
        conn: Any, decision_id: str, model: Any,
) -> tuple[dict[str, Any], str, tuple[Any, ...]]:
    row = conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if not row:
        raise ImmutableViolation(f"checkpoint lacks {decision_id}")
    value = typed(json.loads(str(row[4])), model)
    if canonical_json(value) != row[4]:
        raise ImmutableViolation(f"checkpoint {decision_id} is not canonical")
    return value, sha256_json((decision_id, *row)), row


def validate_seed_activation_owner(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        corpus: PublicCorpus,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Rebuild frozen seed-1 evidence and its exact decision-row digest."""
    plans = seed_plans(master)
    aggregate = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='F_SEED_1'").fetchone()
    if not aggregate or aggregate[0] != sha256_json(plans["F_SEED_1"]):
        raise ImmutableViolation("seed activation lacks its nested seed-1 plan owner")
    stored = typed(_restore_category_order(canonical_row(
        str(aggregate[2]), str(aggregate[1]), "stored seed-1 evidence")),
        Seed1Evidence)
    attempts, contexts, health = seed1_inputs(point, master, corpus)
    try:
        evidence = validate_seed1_evidence(
            stored, master, attempts, contexts, health, corpus=corpus)
        expected = build_seed_activation_decision(master, evidence)
    except StageFError as exc:
        raise ImmutableViolation("stored seed activation cannot be re-derived") from exc
    decision, digest, row = connection_decision(
        point.conn, "stage-f-seed-activation", SeedActivationDecision)
    activation = "ACTIVATED" if expected["activated_group_ids"] else "NOT_ACTIVATED"
    if (decision != expected or row[:4] != (
            "F", master_hash, aggregate[1], activation)):
        raise ImmutableViolation("stored seed activation decision changed")
    return evidence, decision, digest


def validate_final_aggregate_owner(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        corpus: PublicCorpus, expected_hash: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Rebuild the final all-seed aggregate from activated attempt evidence."""
    seed1, decision, decision_digest = validate_seed_activation_owner(
        point, master, master_hash, corpus)
    plans = seed_plans(master)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='F_SEED_20260804'").fetchone()
    if (not row or row[:2] != (
            sha256_json(plans["F_SEED_20260804"]), expected_hash)):
        raise ImmutableViolation("final F aggregate lacks its exact phase owner")
    stored = _restore_category_order(canonical_row(
        str(row[2]), str(row[1]), "final F aggregate"))
    try:
        aggregate = validate_stage_f_aggregate_from_attempts(
            stored, master, seed1, decision,
            all_seed_attempt_inputs(point, master, seed1),
            seed_activation_decision_sha256=decision_digest, corpus=corpus)
    except StageFError as exc:
        raise ImmutableViolation("final F aggregate cannot re-derive") from exc
    return aggregate, seed1, decision, decision_digest


def validate_final_f_owner(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        corpus: PublicCorpus,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """Rebuild the final F aggregate and exact provisional decision-row owner."""
    row = point.conn.execute(
        "SELECT aggregate_hash FROM phase_aggregates "
        "WHERE plan_key='F_SEED_20260804'",
    ).fetchone()
    if not row:
        raise ImmutableViolation("final F owner lacks its aggregate")
    aggregate, _seed1, _decision, _digest = validate_final_aggregate_owner(
        point, master, master_hash, corpus, str(row[0]))
    expected = build_provisional_decision(aggregate)
    provisional, digest, owner = connection_decision(
        point.conn, "stage-f-provisional-selection", ProvisionalDecision)
    activation = ("ACTIVATED" if expected["outcome"] == "PROVISIONAL_SELECTED"
                  else "NOT_ACTIVATED")
    if provisional != expected or owner[:4] != (
            "F", master_hash, row[0], activation):
        raise ImmutableViolation("provisional F decision changed from attempt owner")
    return aggregate, str(row[0]), provisional, digest


def f17_terminal_census(
        point: Checkpoint, plan: Mapping[str, Any], group_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    expected = [item for group in group_ids for item in plan["work"]
                if item["activation_group_id"] == group]
    registry = point.conn.execute(
        "SELECT r.work_id,w.state,w.accepted_attempt_id,w.stage,w.cell_id,w.request_hash "
        "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key='F_SEED_17' ORDER BY r.rowid").fetchall()
    identities = [(item["work_id"], item["stage"], item["cell_id"],
                   item["request_sha256"]) for item in expected]
    if [(row[0], row[3], row[4], row[5]) for row in registry] != identities:
        raise ImmutableViolation("F17 transition registry differs from activation order")
    for item in expected:
        _work_attempt_evidence(point, item)
    rows = []
    for work_id, state, accepted, _stage, _cell, _request in registry:
        try:
            rows.append(F17TerminalWork.model_validate({
                "work_id": work_id, "state": state,
                "accepted_attempt_id": accepted,
            }, strict=True).model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise CheckpointError("F17 cursor transition requires terminal work") from exc
    return rows, [str(row["work_id"]) for row in rows]


def transition_value(
        run_id: str, master_hash: str, decision_digest: str,
        from_plan_hash: str, from_activation_hash: str,
        to_plan_hash: str, to_activation_hash: str,
        from_groups: list[str], to_groups: list[str],
        census: list[dict[str, Any]], transitioned_at_utc: str,
) -> dict[str, Any]:
    census_hash = sha256_json({
        "domain": "c0b2-f17-terminal-work-census-v1", "rows": census})
    body = {
        "version": "c0b2-f-seed-cursor-transition-v1", "run_id": run_id,
        "from_plan_key": "F_SEED_17", "to_plan_key": "F_SEED_20260804",
        "f_master_plan_sha256": master_hash,
        "seed_activation_decision_sha256": decision_digest,
        "from_plan_sha256": from_plan_hash,
        "from_activation_sha256": from_activation_hash,
        "to_plan_sha256": to_plan_hash,
        "to_activation_sha256": to_activation_hash,
        "activated_from_group_ids": from_groups,
        "activated_to_group_ids": to_groups,
        "completed_from_work_ids": [row["work_id"] for row in census],
        "completed_from_work_census_sha256": census_hash,
        "transitioned_at_utc": transitioned_at_utc,
    }
    return typed({**body, "transition_sha256": sha256_json(body)},
                 FSeedCursorTransition)


class _ReadonlyPoint:
    """Minimal checkpoint facade for state-independent snapshot validation."""

    def __init__(self, conn: Any, header: Mapping[str, Any]):
        self.conn = conn
        self._header = dict(header)

    def header(self) -> dict[str, Any]:
        return dict(self._header)

    @property
    def path(self) -> Path:
        rows = self.conn.execute("PRAGMA database_list").fetchall()
        matches = [row for row in rows if row[1] == "main"]
        if len(matches) != 1 or not isinstance(matches[0][2], str) or not matches[0][2]:
            raise ImmutableViolation("read-only checkpoint lacks a main database path")
        path = Path(matches[0][2])
        if not path.is_absolute():
            raise ImmutableViolation("read-only checkpoint path is not absolute")
        return path

    def state(self) -> str:
        return str(self.conn.execute(
            "SELECT state FROM run_state WHERE id=1").fetchone()[0])

    def work(self, work_id: str) -> tuple[str, str | None]:
        row = self.conn.execute(
            "SELECT state,accepted_attempt_id FROM work_items WHERE work_id=?",
            (work_id,)).fetchone()
        if not row:
            raise CheckpointError(f"unknown work {work_id}")
        return str(row[0]), str(row[1]) if row[1] is not None else None

    def load_manifest(self, name: str) -> tuple[str, str]:
        row = self.conn.execute(
            "SELECT manifest_hash,manifest_json FROM manifests WHERE name=?",
            (name,)).fetchone()
        if not row:
            raise CheckpointError(f"backup lacks manifest {name}")
        canonical_row(str(row[1]), str(row[0]), f"backup manifest {name}")
        return str(row[0]), str(row[1])

    def load_plan(self, stage: str) -> tuple[str | None, str, str]:
        row = self.conn.execute(
            "SELECT parent_hash,plan_hash,plan_json FROM plans WHERE stage=?",
            (stage,)).fetchone()
        if not row:
            raise CheckpointError(f"backup lacks plan {stage}")
        canonical_row(str(row[2]), str(row[1]), f"backup plan {stage}")
        return (str(row[0]) if row[0] is not None else None,
                str(row[1]), str(row[2]))


def _backup_registry(conn: Any, key: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(conn.execute(
        "SELECT r.work_id,r.activation_group_id,w.stage,w.cell_id,w.request_hash "
        "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key=? ORDER BY r.rowid", (key,)).fetchall())


def _backup_plan_activation(
        conn: Any, header: Mapping[str, Any], key: str,
        plan: Mapping[str, Any], parent: str, evidence_hash: str,
        groups: list[str],
) -> tuple[str, dict[str, Any]]:
    expected = typed({
        "version": "c0b2-plan-activation-v1", "run_id": header["run_id"],
        "budget_stage": "F", "plan_key": key,
        "plan_sha256": sha256_json(plan), "parent_decision_sha256": parent,
        "state": "ACTIVATED", "activated_group_ids": groups,
        "evidence_sha256": evidence_hash,
    }, PlanActivation)
    row = conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations WHERE plan_key=?",
        (key,)).fetchone()
    if not row or row != (sha256_json(expected), canonical_json(expected)):
        raise ImmutableViolation(f"backup {key} activation changed")
    return str(row[0]), expected


def _validate_backup_transition(
        conn: Any, header: Mapping[str, Any], master_hash: str,
        decision_digest: str, plans: Mapping[str, Mapping[str, Any]],
        activation_hashes: Mapping[str, str],
        activation_groups: Mapping[str, list[str]]) -> None:
    cursor = conn.execute(
        "SELECT active_plan_key,updated FROM runtime_cursor WHERE id=1").fetchone()
    markers = conn.execute(
        "SELECT detail_json,created FROM events "
        "WHERE kind='F_SEED_CURSOR_TRANSITION' ORDER BY seq").fetchall()
    if not cursor:
        raise ImmutableViolation("backup lacks the F cursor")
    if cursor[0] == "F_SEED_17":
        f202_attempts = conn.execute(
            "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
            "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_20260804'"
        ).fetchone()[0]
        if markers or f202_attempts:
            raise ImmutableViolation("paired F17 cursor exception has future evidence")
        return
    if cursor[0] not in {"F_SEED_20260804", "F_ACCEPTANCE"} or len(markers) != 1:
        raise ImmutableViolation("backup lacks one exact seed cursor marker")
    raw = str(markers[0][0])
    created = finite_timestamp(markers[0][1], "F transition marker")
    cursor_updated = finite_timestamp(cursor[1], "F transition cursor")
    if cursor[0] == "F_SEED_20260804" and cursor_updated != created:
        raise ImmutableViolation("backup F202 cursor timestamp changed")
    from_plan = plans["F_SEED_17"]
    expected_items = [item for group in activation_groups["F_SEED_17"]
                      for item in from_plan["work"]
                      if item["activation_group_id"] == group]
    registry = conn.execute(
        "SELECT r.work_id,w.state,w.accepted_attempt_id FROM phase_work_registry r "
        "JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key='F_SEED_17' ORDER BY r.rowid").fetchall()
    if [row[0] for row in registry] != [row["work_id"] for row in expected_items]:
        raise ImmutableViolation("backup transition F17 registry changed")
    readonly = _ReadonlyPoint(conn, header)
    for item in expected_items:
        _work_attempt_evidence(readonly, item)
    census = []
    for work_id, state, accepted in registry:
        try:
            census.append(F17TerminalWork.model_validate({
                "work_id": work_id, "state": state,
                "accepted_attempt_id": accepted,
            }, strict=True).model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ImmutableViolation("backup transition census is not terminal") from exc
    expected = transition_value(
        str(header["run_id"]), master_hash, decision_digest,
        sha256_json(plans["F_SEED_17"]), activation_hashes["F_SEED_17"],
        sha256_json(plans["F_SEED_20260804"]),
        activation_hashes["F_SEED_20260804"],
        activation_groups["F_SEED_17"], activation_groups["F_SEED_20260804"],
        census, _rfc3339(created))
    if typed(json.loads(raw), FSeedCursorTransition) != expected \
            or canonical_json(expected) != raw:
        raise ImmutableViolation("backup seed transition marker changed")
    ids = [row["work_id"] for row in census]
    placeholders = ",".join("?" for _ in ids)
    if conn.execute(
            f"SELECT count(*) FROM attempts WHERE work_id IN ({placeholders}) "
            "AND updated>?", (*ids, created)).fetchone()[0]:
        raise ImmutableViolation("backup F17 attempt crossed transition")
    if conn.execute(
            "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
            "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_20260804' "
            "AND a.created<?", (created,)).fetchone()[0]:
        raise ImmutableViolation("backup F202 attempt predates transition")


def validate_b4_backup_activation(
        conn: Any, header: Mapping[str, Any], key: str,
        plan: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
    """Validate one restored B4 activation from state-independent owners."""
    if key not in B4_ACTIVATION_KEYS:
        raise ValueError("B4 backup hook received a non-B4 activation")
    point = _ReadonlyPoint(conn, header)
    master, master_hash, corpus, _key = _frozen_f_inputs(point)
    validate_b4_f_control_census(point)
    plans = seed_plans(master)
    seed1_row = conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='F_SEED_1'").fetchone()
    if (not seed1_row
            or seed1_row[0] != sha256_json(plans["F_SEED_1"])):
        raise ImmutableViolation("backup lacks exact seed-1 evidence")
    seed1, decision, decision_digest = validate_seed_activation_owner(
        point, master, master_hash, corpus)
    if key in LATER_KEYS:
        hashes: dict[str, str] = {}
        groups_by_key: dict[str, list[str]] = {}
        for later_key in LATER_KEYS:
            expected_plan = plans[later_key]
            stored = conn.execute(
                "SELECT plan_hash,plan_json FROM phase_plans WHERE plan_key=?",
                (later_key,)).fetchone()
            if stored != (sha256_json(expected_plan), canonical_json(expected_plan)):
                raise ImmutableViolation("backup later plan differs from F master")
            group_set = {row["group_id"] for row in expected_plan["groups"]}
            groups = [group for group in decision["activated_group_ids"]
                      if group in group_set]
            hashes[later_key], _activation = _backup_plan_activation(
                conn, header, later_key, expected_plan, decision_digest,
                str(seed1_row[1]), groups)
            groups_by_key[later_key] = groups
            work = [item for group in groups for item in expected_plan["work"]
                    if item["activation_group_id"] == group]
            expected_registry = tuple((
                item["work_id"], item["activation_group_id"], item["stage"],
                item["cell_id"], item["request_sha256"]) for item in work)
            if _backup_registry(conn, later_key) != expected_registry:
                raise ImmutableViolation("backup later registry changed")
        inactive = {item["work_id"] for later_key in LATER_KEYS
                    for item in plans[later_key]["work"]
                    if item["activation_group_id"] in decision["inactive_group_ids"]}
        if inactive & {str(row[0]) for row in conn.execute(
                "SELECT work_id FROM work_items WHERE stage='F'")}:
            raise ImmutableViolation("backup registered inactive later work")
        _validate_backup_transition(
            conn, header, master_hash, decision_digest, plans, hashes, groups_by_key)
        if (canonical_json(plan) != canonical_json(plans[key])
                or canonical_json(activation) != conn.execute(
                    "SELECT activation_json FROM plan_activations WHERE plan_key=?",
                    (key,)).fetchone()[0]):
            raise ImmutableViolation("backup hook input differs from later owner")
        return

    final_row = conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='F_SEED_20260804'").fetchone()
    if (not final_row
            or final_row[0] != sha256_json(plans["F_SEED_20260804"])):
        raise ImmutableViolation("backup acceptance lacks final F aggregate")
    stored_final = _restore_category_order(canonical_row(
        str(final_row[2]), str(final_row[1]), "backup final F aggregate"))
    try:
        final = validate_stage_f_aggregate_from_attempts(
            stored_final, master, seed1, decision,
            all_seed_attempt_inputs(point, master, seed1),
            seed_activation_decision_sha256=decision_digest, corpus=corpus)
    except StageFError as exc:
        raise ImmutableViolation("backup final F aggregate cannot re-derive") from exc
    provisional, provisional_digest, provisional_row = connection_decision(
        conn, "stage-f-provisional-selection", ProvisionalDecision)
    if provisional_row[:4] != ("F", master_hash, final_row[1], "ACTIVATED"):
        raise ImmutableViolation("backup provisional owner changed")
    selection = provisional["selection"]
    winner = next((candidate["candidate_id"]
                   for candidate in plans["F_SEED_1"]["candidates"]
                   if selection is not None and all(
                       candidate[field] == selection[field] for field in selection)), None)
    template = next((row for row in master["acceptance_templates"]
                     if row["candidate_id"] == winner), None)
    if (not template or final["ranking"]["winner_candidate_id"] != winner
            or plan["parent_decision_sha256"] != provisional_digest
            or plan["master_plan_sha256"] != master_hash
            or plan["template_sha256"] != template["template_sha256"]
            or plan["candidates"] != template["payload"]["candidates"]
            or plan["work"] != template["payload"]["work"]
            or activation["parent_decision_sha256"] != provisional_digest
            or activation["evidence_sha256"] != final_row[1]
            or activation["activated_group_ids"]):
        raise ImmutableViolation("backup acceptance differs from winner/template")
    expected_registry = tuple((
        item["work_id"], item["activation_group_id"], item["stage"],
        item["cell_id"], item["request_sha256"]) for item in plan["work"])
    if _backup_registry(conn, key) != expected_registry:
        raise ImmutableViolation("backup acceptance registry changed")


def _validate_no_seed1_terminal_rows(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        seed1_plan_hash: str, seed1: Mapping[str, Any], seed1_hash: str,
        decision: Mapping[str, Any],
) -> str:
    """Bind the early F terminal to its attempt-derived zero-qualifier owner."""
    if (decision["qualifier_candidate_ids"] or decision["activated_group_ids"]
            or not decision["inactive_group_ids"]):
        raise ImmutableViolation("no-seed1 terminal has a qualifying candidate")
    try:
        provisional = build_no_seed1_provisional_decision(master, seed1)
    except StageFError as exc:
        raise ImmutableViolation("no-seed1 provisional cannot re-derive") from exc
    provisional_row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id='stage-f-provisional-selection'",
    ).fetchone()
    if provisional_row != (
            "F", master_hash, seed1_hash, "NOT_ACTIVATED",
            canonical_json(provisional)):
        raise ImmutableViolation("no-seed1 provisional changed from its owner")

    artifact = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "F",
        "aggregate_sha256": seed1_hash, "reason": "no_seed1_qualifier",
    }
    artifact_hash = sha256_json(artifact)
    artifact_row = point.conn.execute(
        "SELECT terminal,artifact_hash,artifact_json FROM public_artifacts "
        "WHERE artifact_id='stage-f-result'",
    ).fetchone()
    if artifact_row != (
            "INCONCLUSIVE", artifact_hash, canonical_json(artifact)):
        raise ImmutableViolation("no-seed1 result changed from its owner")
    completion = {
        "outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
        "facts": {"deterministic_stop": True, "reason": "no_seed1_qualifier"},
    }
    completion_row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id='c0b2-completion'",
    ).fetchone()
    if completion_row != (
            "F", seed1_plan_hash, seed1_hash, "NOT_ACTIVATED",
            canonical_json(completion)):
        raise ImmutableViolation("no-seed1 completion changed from its public result")

    assert_terminal_f_namespace(
        point, seed_plans(master), ("F_SEED_1",), ("F_SEED_1",),
        "F_SEED_1", "INCONCLUSIVE", _work_attempt_evidence)
    return artifact_hash


def _result_completion_owner(
        point: Checkpoint, artifact: Mapping[str, Any], parent_hash: str,
        aggregate_hash: str,
) -> str:
    artifact_hash = sha256_json(artifact)
    terminal = str(artifact["terminal"])
    row = point.conn.execute(
        "SELECT terminal,artifact_hash,artifact_json FROM public_artifacts "
        "WHERE artifact_id='stage-f-result'").fetchone()
    if row != (terminal, artifact_hash, canonical_json(artifact)):
        raise ImmutableViolation("F result changed from its aggregate owner")
    if terminal == "SELECTED":
        gates = {name: True for name in (
            "strict_validity", "first_pass_invalid_bound", "raw_grounding",
            "retained_grounding", "category_recall", "false_positive_bound",
            "injection_robustness", "boundary_identifiers", "truncation_complete",
            "context_channel_cancellation_provenance_safety")}
        completion = {"outcome": "SELECTED", "artifact_sha256": artifact_hash,
                      "facts": {"accepted_document_count": 166, "gates": gates}}
        activation = "ACTIVATED"
    else:
        completion = {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
                      "facts": {"deterministic_stop": True,
                                "reason": artifact["reason"]}}
        activation = "NOT_ACTIVATED"
    owner = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions "
        "WHERE decision_id='c0b2-completion'").fetchone()
    if owner != ("F", parent_hash, aggregate_hash, activation,
                 canonical_json(completion)):
        raise ImmutableViolation("F completion changed from its public result")
    return artifact_hash


def _acceptance_plan_owner(
        point: Checkpoint, header: Mapping[str, Any], master: Mapping[str, Any],
        master_hash: str, provisional: Mapping[str, Any], provisional_digest: str,
        final_hash: str,
) -> tuple[dict[str, Any], str]:
    selection = provisional["selection"]
    if sha256_json(master) != master_hash:
        raise ImmutableViolation("acceptance plan master hash changed")
    winner = next((row["candidate_id"] for row in seed_plans(master)["F_SEED_1"]
                   ["candidates"] if selection == {
                       field: row[field] for field in CandidateSelection.model_fields}), None)
    if winner is None:
        raise ImmutableViolation("acceptance plan lacks its provisional winner")
    expected = build_acceptance_plan(
        master, candidate_id=winner,
        provisional_decision_sha256=provisional_digest)
    row = point.conn.execute(
        "SELECT parent_decision_sha256,plan_hash,plan_json FROM phase_plans "
        "WHERE plan_key='F_ACCEPTANCE'").fetchone()
    plan_hash = sha256_json(expected)
    if row != (provisional_digest, plan_hash, canonical_json(expected)):
        raise ImmutableViolation("acceptance plan changed from its frozen template")
    activation_row = point.conn.execute(
        "SELECT activation_json FROM plan_activations "
        "WHERE plan_key='F_ACCEPTANCE'").fetchone()
    if not activation_row:
        raise ImmutableViolation("acceptance plan lacks activation")
    activation = typed(json.loads(str(activation_row[0])), PlanActivation)
    _backup_plan_activation(
        point.conn, header, "F_ACCEPTANCE", expected, provisional_digest,
        final_hash, [])
    validate_b4_backup_activation(
        point.conn, header, "F_ACCEPTANCE", expected, activation)
    return expected, plan_hash


def _acceptance_aggregate_owner(
        point: Checkpoint, master: Mapping[str, Any],
        corpus: PublicCorpus, final: Mapping[str, Any], final_hash: str,
        provisional: Mapping[str, Any], provisional_digest: str,
        plan: Mapping[str, Any], plan_hash: str,
) -> tuple[dict[str, Any], str]:
    d_digest, d_decision = validate_stored_d_owner(point)
    d_key = {"D3": "D3_CONTEXT", "D4": "D4_CONFIRMATION"}[d_decision["phase"]]
    d_row = point.conn.execute(
        "SELECT aggregate_hash,aggregate_json FROM phase_aggregates WHERE plan_key=?",
        (d_key,)).fetchone()
    if not d_row:
        raise ImmutableViolation("acceptance lacks its D50 aggregate owner")
    d_aggregate = canonical_row(str(d_row[1]), str(d_row[0]), "D50 aggregate")
    from .c0b2_runtime_d import _restore_d_category_order
    d_aggregate = _restore_d_category_order(d_aggregate)
    c44 = build_c44_scored_aggregate(
        plan, acceptance_attempt_inputs(point, plan), corpus=corpus)
    winner = final["ranking"]["winner_candidate_id"]
    cancellation = next(row["cancellation_health"]["passed"]
                        for row in final["candidates"] if row["candidate_id"] == winner)
    bad_states = point.conn.execute(
        "SELECT count(*) FROM attempts WHERE state IN "
        "('FAILED_SAFETY','BLOCKED_PROVENANCE','BLOCKED_BUDGET',"
        "'BLOCKED_FILESYSTEM','ABANDONED')").fetchone()[0]
    if bad_states or d_digest != master["parent_decision_sha256"]:
        raise ImmutableViolation("acceptance safety/provenance owner failed")
    try:
        expected = build_acceptance_aggregate(
            plan, provisional_decision=provisional,
            provisional_decision_sha256=provisional_digest, c44_scored=c44,
            final_d_decision=d_decision, d50_source_aggregate=d_aggregate,
            stage_d_decision_sha256=d_digest, stage_f_aggregate=final,
            f_master=master, corpus=corpus,
            cancellation_health_passed=cancellation,
            provenance_passed=True, safety_passed=True)
    except StageFError as exc:
        raise ImmutableViolation("acceptance aggregate cannot re-derive") from exc
    aggregate_hash = sha256_json(expected)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='F_ACCEPTANCE'").fetchone()
    if row != (plan_hash, aggregate_hash, canonical_json(expected)):
        raise ImmutableViolation("acceptance aggregate changed from its exact owners")
    return expected, aggregate_hash


def validate_b4_terminal_owner(conn: Any, header: Mapping[str, Any]) -> str:
    """Rebuild every successful/inconclusive F terminal from durable evidence."""
    point = _ReadonlyPoint(conn, header)
    master, master_hash, corpus, _key = _frozen_f_inputs(point)
    validate_b4_f_control_census(point)
    plans = seed_plans(master)
    seed1, decision, _decision_digest = validate_seed_activation_owner(
        point, master, master_hash, corpus)
    seed1_row = conn.execute(
        "SELECT plan_hash,aggregate_hash FROM phase_aggregates "
        "WHERE plan_key='F_SEED_1'",
    ).fetchone()
    expected_plan_hash = sha256_json(plans["F_SEED_1"])
    if not seed1_row or seed1_row[0] != expected_plan_hash:
        raise ImmutableViolation("F terminal lacks its exact seed-1 aggregate owner")
    expected_controls = tuple(
        str(group[name]["control_id"]) for group in plans["F_SEED_1"]["groups"]
        for name in ("context_control", "cancellation_control", "health_control"))
    actual_controls = tuple(row[0] for row in conn.execute(
        "SELECT control_id FROM runtime_controls WHERE plan_key LIKE 'F_%' ORDER BY rowid"))
    if actual_controls != expected_controls:
        raise ImmutableViolation("F terminal control namespace changed")
    if not decision["qualifier_candidate_ids"]:
        return _validate_no_seed1_terminal_rows(
            point, master, master_hash, expected_plan_hash, seed1,
            str(seed1_row[1]), decision)
    later_plan = plans["F_SEED_20260804"]
    later_row = conn.execute(
        "SELECT activation_json FROM plan_activations "
        "WHERE plan_key='F_SEED_20260804'").fetchone()
    if not later_row:
        raise ImmutableViolation("F terminal lacks later-seed activation")
    later_activation = typed(json.loads(str(later_row[0])), PlanActivation)
    validate_b4_backup_activation(
        conn, header, "F_SEED_20260804", later_plan, later_activation)
    final, final_hash, provisional, provisional_digest = validate_final_f_owner(
        point, master, master_hash, corpus)
    if provisional["outcome"] == "INCONCLUSIVE":
        artifact = build_inconclusive_result(provisional["reason"], final_hash)
        result = _result_completion_owner(
            point, artifact, sha256_json(later_plan), final_hash)
        assert_terminal_f_namespace(
            point, plans, ("F_SEED_1", "F_SEED_20260804"),
            ("F_SEED_1", *LATER_KEYS), "F_SEED_20260804", "INCONCLUSIVE",
            _work_attempt_evidence)
        return result
    plan, plan_hash = _acceptance_plan_owner(
        point, header, master, master_hash, provisional, provisional_digest, final_hash)
    aggregate, aggregate_hash = _acceptance_aggregate_owner(
        point, master, corpus, final, final_hash,
        provisional, provisional_digest, plan, plan_hash)
    if aggregate["passed"]:
        from .c0b2_runtime_d import _stage_c_boundary_parent
        c_digest, _selection = _stage_c_boundary_parent(point)
        artifact = build_final_result(
            master_manifest_sha256=corpus.master_manifest_sha256,
            stage_c_selection_sha256=c_digest, stage_d_decision_sha256=
            master["parent_decision_sha256"], stage_f_aggregate=final,
            provisional_decision=provisional,
            provisional_decision_sha256=provisional_digest, acceptance_plan=plan,
            acceptance_aggregate=aggregate)
        state = "SELECTED"
    else:
        artifact = build_inconclusive_result(
            "complete_corpus_acceptance_failed", aggregate_hash)
        state = "INCONCLUSIVE"
    result = _result_completion_owner(point, artifact, plan_hash, aggregate_hash)
    assert_terminal_f_namespace(
        point, {**plans, "F_ACCEPTANCE": plan},
        ("F_SEED_1", "F_SEED_20260804", "F_ACCEPTANCE"),
        ("F_SEED_1", *LATER_KEYS, "F_ACCEPTANCE"), "F_ACCEPTANCE", state,
        _work_attempt_evidence)
    return result
