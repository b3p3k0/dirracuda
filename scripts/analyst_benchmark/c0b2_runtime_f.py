"""Durable Stage-F orchestration and finalization for public C0B-2.

The driver resolves only frozen public plans, executes every request through the bounded
transport, and rebuilds all quality artifacts from durable attempt evidence.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import report
from .c0b2_checkpoint import (
    RESUMABLE_STATES, TERMINAL_STATES, Checkpoint, CheckpointError,
    ImmutableViolation, canonical_json, sha256_json,
)
from .c0b2_executor import (
    PUBLIC_ACCEPTANCE_GATES, SERVER_CONTROL_MODEL, CancellationController,
    ControlRequest, DurableExecutor, ExecutionResult, InvocationCancelled,
    WorkRequest, control_id, resource_probe_id,
)
from .c0b2_fsprobe import GlobalExecutionLock
from .c0b2_public_schema import (
    AcceptancePlan, CandidateSelection, FMasterPlan, FSeedPlan, PlanActivation,
    validate_artifact,
)
from .c0b2_runtime_common import (
    DeferredTransport, PhaseActivation, RuntimePosition, _LiveSignalGuard,
    _decision_digest,
    _register_activated_work, _rfc3339,
    freeze_activate_phase_plan, freeze_phase_plan, freeze_runtime_control,
    load_phase_plan, runtime_position, runtime_transaction,
)
from .c0b2_runtime_f_evidence import (
    attempt_in_invocation as _attempt_in_invocation,
    f17_terminal_census as _f17_terminal_census,
    seed1_inputs as _seed1_inputs,
    transition_value as _evidence_transition_value,
    validate_b4_f_control_census,
    validate_b4_backup_activation,
    validate_b4_terminal_owner,
    validate_final_aggregate_owner,
    validate_seed_activation_owner,
    validate_stored_d_owner as _validate_stored_d_owner,
)
from .c0b2_runtime_f_namespace import (
    assert_acceptance_namespace_clean, assert_f_namespace_empty_before_master,
    assert_seed1_replay_namespace, later_namespace_census,
)
from .c0b2_stage_f import (
    ProvisionalDecision, StageFError,
    build_acceptance_aggregate, build_c44_scored_aggregate, build_final_result,
    build_inconclusive_result, build_no_seed1_provisional_decision,
    build_provisional_decision,
    build_seed1_evidence_from_attempts, build_seed_activation_decision,
    build_stage_f_aggregate_from_attempts, validate_provisional_decision_artifact,
    validate_seed1_evidence, validate_seed1_evidence_artifact,
    validate_seed_activation_artifact,
)
from .c0b2_stage_f_plan import (
    PublicCorpus, StageFPlanError, build_acceptance_plan, build_f_master_plan,
    load_public_corpus, request_specs_for_activated_f_plan,
    request_specs_for_f_plan, resolve_f_seed1_control, validate_f_master_plan,
)
from .c0b2_transport import RequestSpec, request_spec_hash
from .c0b3_policy import CURRENT_POLICY, resolve_header_policy, resolve_payload_policy
from .c0b3_schema import (
    build_completion_value, completion_decision_id, validate_plan_for_header,
)

_SEED_KEYS = ("F_SEED_1", "F_SEED_17", "F_SEED_20260804")
_LATER_KEYS = _SEED_KEYS[1:]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMPLETE_WORK = {"SUCCEEDED", "COMPLETED_INVALID"}
_ANSWERED = {"ACCEPTED", "SCHEMA_INVALID"}


class StageFRuntimeError(RuntimeError):
    """Stage-F provenance, sequencing, or evidence is not exact."""


@dataclass(frozen=True)
class FStartActivation:
    master_sha256: str
    seed1: PhaseActivation


@dataclass(frozen=True)
class FLaterActivation:
    seed1_evidence_sha256: str
    decision_sha256: str
    activated_plan_keys: tuple[str, ...]
    terminal_reason: str | None


@dataclass(frozen=True)
class ActiveFPhase:
    plan: dict[str, Any]
    plan_sha256: str
    work: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StageFRunResult:
    state: str
    active_plan_key: str
    outcome: str
    retry_not_before: float = 0.0


def _before_activation_commit() -> None:
    """Test seam immediately before the owning transaction commits."""


def _before_seed1_transaction() -> None:
    """Test seam between caller-shape parsing and the locked evidence rebuild."""


def _typed(value: Mapping[str, Any], model: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Stage-F artifact must be a mapping")
    return validate_artifact(model, value)


def _run_header(point: Any) -> dict[str, Any]:
    return point.header() if callable(getattr(point, "header", None)) else {}


def _plan_for_point(point: Any, model: Any,
                    value: Mapping[str, Any]) -> dict[str, Any]:
    header = _run_header(point)
    if resolve_header_policy(header).benchmark_protocol_id is None:
        return _typed(value, model)
    return validate_plan_for_header(header, model, value)


def _inconclusive(point: Any, reason: str, digest: str) -> dict[str, Any]:
    policy = resolve_header_policy(_run_header(point))
    if policy.benchmark_protocol_id is None:
        return build_inconclusive_result(reason, digest)
    return build_inconclusive_result(reason, digest, policy=policy)


def _public_inputs(point: Checkpoint) -> tuple[PublicCorpus, bytes]:
    """Load the public corpus and protected nonce key from frozen checkpoint owners."""
    from .c0b2_stage_d_plan import verified_run_nonce_key

    try:
        master_hash, master_raw = point.load_manifest("master")
        _key_hash, key_raw = point.load_manifest("run_nonce_key")
        _c_parent, _c_hash, c_raw = point.load_plan("C")
        key = verified_run_nonce_key(key_raw, c_raw)
        corpus = load_public_corpus(
            json.loads(master_raw), master_manifest_sha256=master_hash)
    except Exception as exc:
        raise ImmutableViolation(
            "Stage-F public fixtures or protected run key changed") from exc
    return corpus, key


def _validate_current_d_boundary(point: Checkpoint) -> tuple[str, dict[str, Any]]:
    from .c0b2_runtime_d import load_stage_d_inputs, validate_final_d_boundary

    return validate_final_d_boundary(point, load_stage_d_inputs(point))


def _canonical_row(raw: str, digest: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"{label} is not valid JSON") from exc
    if (not isinstance(value, dict) or canonical_json(value) != raw
            or sha256_json(value) != digest):
        raise ImmutableViolation(f"{label} hash or canonical encoding changed")
    return value


def _master(point: Checkpoint) -> tuple[dict[str, Any], str]:
    digest, raw = point.load_manifest("f_master")
    value = _plan_for_point(
        point, FMasterPlan, _canonical_row(raw, digest, "F master plan"))
    if (sha256_json(value) != digest
            or value["master_manifest_sha256"]
            != point.header()["master_manifest_sha256"]
            or value["parent_decision_sha256"]
            != _decision_digest(point, "stage-d-selection")):
        raise ImmutableViolation("F master plan changed from its frozen lineage")
    return value, digest


def _seed_plans(master: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["payload"]["plan_key"]): dict(row["payload"])
            for row in master["plans"]}


def _expected_controls(seed1: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [control for group in seed1["groups"] for control in (
        group["context_control"], group["cancellation_control"],
        group["health_control"])]


def _active_f_phase(
        point: Checkpoint, master: Mapping[str, Any], corpus: PublicCorpus,
        run_nonce_key: bytes, *, independently_validated: bool = False,
        namespace_validated: bool = False,
) -> ActiveFPhase:
    """Bind the cursor to one exact activated plan and ordered work registry."""
    position = runtime_position(point)
    if position.active_stage != "F" or position.active_plan_key not in {
            *_SEED_KEYS, "F_ACCEPTANCE"}:
        raise StageFRuntimeError("checkpoint is not at an active Stage-F plan")
    key = position.active_plan_key
    parent, digest, raw = load_phase_plan(point, key)
    plan = _canonical_row(raw, digest, f"active {key} plan")
    if key in _SEED_KEYS:
        if independently_validated:
            normalized = dict(master)
        else:
            try:
                normalized = validate_f_master_plan(
                    master, corpus=corpus, run_nonce_key=run_nonce_key)
            except StageFPlanError as exc:
                raise ImmutableViolation("active F master cannot re-derive") from exc
        expected = _seed_plans(normalized)[key]
        if plan != expected or parent != expected["parent_decision_sha256"]:
            raise ImmutableViolation(f"active {key} differs from F master")
        plan = _plan_for_point(point, FSeedPlan, plan)
    else:
        plan = _plan_for_point(point, AcceptancePlan, plan)
        if (plan["master_plan_sha256"] != sha256_json(master)
                or parent != plan["parent_decision_sha256"]):
            raise ImmutableViolation("active acceptance plan changed lineage")
    row = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key=?", (key,)).fetchone()
    if not row:
        raise ImmutableViolation(f"active {key} lacks its activation")
    activation = _typed(
        _canonical_row(str(row[1]), str(row[0]), f"active {key} activation"),
        PlanActivation)
    if (activation["plan_key"] != key or activation["plan_sha256"] != digest
            or activation["run_id"] != point.header()["run_id"]):
        raise ImmutableViolation(f"active {key} activation changed")
    if key == "F_SEED_1":
        expected_groups = [group["group_id"] for group in plan["groups"]]
        if (activation["activated_group_ids"] != expected_groups
                or activation["parent_decision_sha256"]
                != _decision_digest(point, "stage-d-selection")
                or activation["evidence_sha256"] is not None):
            raise ImmutableViolation("seed-1 activation differs from exact D owner")
    elif not namespace_validated:
        validate_b4_backup_activation(
            point.conn, point.header(), key, plan, activation)
    if key == "F_ACCEPTANCE":
        expected_work = list(plan["work"])
    else:
        groups = list(activation["activated_group_ids"])
        expected_work = [item for group in groups for item in plan["work"]
                         if item["activation_group_id"] == group]
    registry = point.conn.execute(
        "SELECT r.work_id,r.activation_group_id,w.stage,w.cell_id,w.request_hash "
        "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key=? ORDER BY r.rowid", (key,)).fetchall()
    expected_registry = [(item["work_id"], item["activation_group_id"],
                          "F", item["cell_id"], item["request_sha256"])
                         for item in expected_work]
    if registry != expected_registry:
        raise ImmutableViolation(f"active {key} registry changed")
    return ActiveFPhase(plan, digest, tuple(expected_work))


def _attempt_number(
        point: Checkpoint, *, work_id: str | None = None,
        control_id_value: str | None = None,
        first_class: str = "preflight_probe",
) -> tuple[int, str]:
    if (work_id is None) == (control_id_value is None):
        raise ValueError("exactly one attempt identity is required")
    column, identity = (("work_id", work_id) if work_id is not None
                        else ("control_id", control_id_value))
    row = point.conn.execute(
        f"SELECT attempt_no,state FROM attempts WHERE {column}=? "
        "ORDER BY attempt_no DESC LIMIT 1", (identity,)).fetchone()
    if row is None:
        return 1, "scored" if work_id is not None else first_class
    call_class = {
        "SCHEMA_INVALID": "schema_retry",
        "RETRYABLE_TRANSPORT": "transport_orphan",
        "ORPHANED_UNKNOWN": "transport_orphan",
        "CANCELLED_UNVERIFIED": "transport_orphan",
    }.get(str(row[1]))
    if call_class is None:
        raise CheckpointError(f"attempt outcome {row[1]} is not retryable")
    return int(row[0]) + 1, call_class


def _preflight_specs(
        header: Mapping[str, Any], phase: ActiveFPhase,
) -> list[tuple[str, str, RequestSpec]]:
    models: list[str] = []
    for item in phase.work:
        if item["model"] not in models:
            models.append(item["model"])
    return [
        ("version", SERVER_CONTROL_MODEL,
         RequestSpec(kind="version", expected_version=header["ollama_version"])),
        ("tags", SERVER_CONTROL_MODEL,
         RequestSpec(kind="tags", expected_models=header["model_digests"])),
        *(("show", model, RequestSpec(
            kind="show", expected_model=model,
            expected_digest=header["model_digests"][model])) for model in models),
    ]


def _work_spec(specs: Mapping[str, RequestSpec], work_id: str,
               request_hash: str | None = None) -> RequestSpec:
    spec = specs.get(work_id)
    if spec is None or request_hash is not None \
            and request_spec_hash(spec) != request_hash:
        raise ImmutableViolation("F work request differs from its validated plan index")
    return spec


def _work_complete(point: Checkpoint, work_id: str) -> bool:
    return point.work(work_id)[0] in _COMPLETE_WORK


def _answered(point: Checkpoint, work_id: str) -> bool:
    return bool(point.conn.execute(
        "SELECT 1 FROM attempts WHERE work_id=? "
        "AND state IN ('ACCEPTED','SCHEMA_INVALID') LIMIT 1", (work_id,)).fetchone())


def _seed1_next(
        point: Checkpoint, phase: ActiveFPhase,
) -> tuple[str, dict[str, Any]] | None:
    """Return the exact next seed-1 obligation in frozen candidate order."""
    from .c0b2_runtime_common import load_runtime_control

    for group in phase.plan["groups"]:
        rows = [row for row in phase.work
                if row["candidate_id"] == group["candidate_id"]]
        context = load_runtime_control(
            point, group["context_control"]["control_id"])
        cancel = load_runtime_control(
            point, group["cancellation_control"]["control_id"])
        health = load_runtime_control(point, group["health_control"]["control_id"])
        answered = [row for row in rows if _answered(point, row["work_id"])]
        if answered and context.state == "PENDING":
            if answered != [rows[0]] or rows[0]["work_id"] != group["first_work_id"]:
                raise ImmutableViolation("seed-1 work crossed its context barrier")
            return "context", group["context_control"]
        if context.state not in ({"PENDING"} if not answered else {"COMPLETE"}):
            raise ImmutableViolation("seed-1 context state differs from answered work")
        pending = next((row for row in rows
                        if not _work_complete(point, row["work_id"])), None)
        if pending is not None:
            if cancel.state != "PENDING" or health.state != "PENDING":
                raise ImmutableViolation("seed-1 controls precede complete work")
            return "work", pending
        if context.state != "COMPLETE":
            raise ImmutableViolation("complete seed-1 work lacks context evidence")
        if cancel.state == "PENDING":
            if health.state != "PENDING":
                raise ImmutableViolation("seed-1 health precedes cancellation")
            return "cancel", group["cancellation_control"]
        if cancel.state != "CANCELLED_UNVERIFIED":
            raise ImmutableViolation("seed-1 cancellation state changed")
        if health.state == "PENDING":
            return "health", group["health_control"]
        if health.state != "COMPLETE":
            raise ImmutableViolation("seed-1 health state changed")
    return None


def _pending_work(phase: ActiveFPhase, point: Checkpoint
                  ) -> dict[str, Any] | None:
    return next((row for row in phase.work
                 if not _work_complete(point, row["work_id"])), None)


def _pending_health_not_before(point: Checkpoint) -> float | None:
    """Return the exact seed-1 health claim barrier without claiming an invocation."""
    from datetime import datetime
    from .c0b2_runtime_common import _bound_control, load_runtime_control

    position = runtime_position(point)
    if position != RuntimePosition("F", "F_SEED_1"):
        return None
    _parent, _digest, raw = load_phase_plan(point, "F_SEED_1")
    plan = json.loads(raw)
    barriers = []
    for group in plan["groups"]:
        values = (("cancellation_probe", group["cancellation_control"]),
                  ("cancellation_health", group["health_control"]))
        records = []
        for kind, control in values:
            exact = _bound_control(point, "F_SEED_1", kind, control)
            record = load_runtime_control(point, control["control_id"])
            if (exact != control or record.plan_key != "F_SEED_1"
                    or record.kind != kind
                    or record.control_sha256 != sha256_json(control)
                    or record.control_json != canonical_json(control)):
                raise ImmutableViolation("pending health control binding changed")
            records.append(record)
        cancel, health = records
        if cancel.state == "CANCELLED_UNVERIFIED" and health.state == "PENDING":
            if cancel.not_before_utc is None:
                raise ImmutableViolation("pending health lacks durable not-before")
            try:
                barriers.append(datetime.fromisoformat(
                    cancel.not_before_utc.removesuffix("Z") + "+00:00").timestamp())
            except ValueError as exc:
                raise ImmutableViolation("pending health not-before is invalid") from exc
    if len(barriers) > 1:
        raise ImmutableViolation("multiple seed-1 health barriers are pending")
    return barriers[0] if barriers else None


def _f_presence(point: Checkpoint) -> dict[str, Any]:
    return {
        "master": point.conn.execute(
            "SELECT count(*) FROM manifests WHERE name='f_master'").fetchone()[0],
        "plans": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM phase_plans WHERE budget_stage='F' ORDER BY rowid")),
        "activations": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM plan_activations WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "work": tuple(row[0] for row in point.conn.execute(
            "SELECT work_id FROM work_items WHERE stage='F' ORDER BY rowid")),
        "registry": tuple(row for row in point.conn.execute(
            "SELECT work_id,plan_key,activation_group_id FROM phase_work_registry "
            "WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
        "controls": tuple(row[0] for row in point.conn.execute(
            "SELECT control_id FROM runtime_controls WHERE plan_key LIKE 'F_%' ORDER BY rowid")),
    }


def _validate_seed1_tree(point: Checkpoint, master: Mapping[str, Any],
                         master_hash: str) -> PhaseActivation:
    plans = _seed_plans(master)
    for key in _SEED_KEYS:
        parent, digest, raw = load_phase_plan(point, key)
        expected = plans[key]
        if (parent != expected["parent_decision_sha256"]
                or digest != sha256_json(expected) or json.loads(raw) != expected):
            raise ImmutableViolation(f"frozen {key} differs from F master")
    seed1 = plans["F_SEED_1"]
    groups = tuple(group["group_id"] for group in seed1["groups"])
    result = freeze_activate_phase_plan(
        point, seed1, activated_group_ids=groups)
    for control in _expected_controls(seed1):
        freeze_runtime_control(
            point, "F_SEED_1", control["control_id"], control["kind"], control)
    if master_hash != sha256_json(master):
        raise ImmutableViolation("F master hash changed during seed-1 validation")
    return result


def freeze_activate_f_master(
        point: Checkpoint, value: Mapping[str, Any]) -> FStartActivation:
    """Atomically freeze the complete F tree and activate only seed-1 work."""
    _plan_for_point(point, FMasterPlan, value)
    with runtime_transaction(point):
        presence = _f_presence(point)
        fresh = not any((presence["master"], presence["plans"],
                         presence["activations"], presence["work"],
                         presence["registry"], presence["controls"]))
        if fresh:
            assert_f_namespace_empty_before_master(point)
        else:
            assert_seed1_replay_namespace(point)
        corpus, run_nonce_key = _public_inputs(point)
        d_digest, d_decision = (_validate_current_d_boundary(point) if fresh
                                else _validate_stored_d_owner(point))
        try:
            master = validate_f_master_plan(
                value, corpus=corpus, run_nonce_key=run_nonce_key)
        except StageFPlanError as exc:
            raise ImmutableViolation(
                "F master differs from its public fixtures and protected key") from exc
        master_hash = sha256_json(master)
        plans = _seed_plans(master)
        seed1 = plans["F_SEED_1"]
        f_selections = [
            {key: candidate[key] for key in CandidateSelection.model_fields}
            for candidate in seed1["candidates"]]
        d_selections = [row["selection"] for row in d_decision["selections"]]
        if (master["master_manifest_sha256"]
                != point.header()["master_manifest_sha256"]
                or master["parent_decision_sha256"] != d_digest
                or f_selections != d_selections):
            raise ImmutableViolation("F master does not match the exact final D boundary")
        expected_work = tuple(row["work_id"] for row in seed1["work"])
        expected_registry = tuple(
            (row["work_id"], "F_SEED_1", row["activation_group_id"])
            for row in seed1["work"])
        expected_controls = tuple(
            row["control_id"] for row in _expected_controls(seed1))
        complete = (presence == {
            "master": 1, "plans": _SEED_KEYS, "activations": ("F_SEED_1",),
            "work": expected_work, "registry": expected_registry,
            "controls": expected_controls,
        } and runtime_position(point) == RuntimePosition("F", "F_SEED_1")
            and point.state() == "RUNNING")
        if not fresh and not complete:
            raise ImmutableViolation("F master activation is partial or has advanced")
        if fresh:
            point.freeze_manifest("f_master", master)
            for key in _SEED_KEYS:
                freeze_phase_plan(point, plans[key])
        else:
            stored, stored_hash = _master(point)
            if stored != master or stored_hash != master_hash:
                raise ImmutableViolation("F master replay drifted")
        seed1_activation = _validate_seed1_tree(point, master, master_hash)
        _before_activation_commit()
    return FStartActivation(master_hash, seed1_activation)


def expected_seed_activation(
        master: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the only legal paired later-seed activation decision."""
    try:
        return build_seed_activation_decision(master, evidence)
    except StageFError as exc:
        raise ImmutableViolation("seed activation cannot be re-derived") from exc


def _freeze_phase_evidence(point: Checkpoint, plan_key: str, plan_hash: str,
                           value: Mapping[str, Any]) -> str:
    raw, digest = canonical_json(value), sha256_json(value)
    frozen = (plan_hash, digest, raw)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (plan_key,)).fetchone()
    if row and row != frozen:
        raise ImmutableViolation(f"{plan_key} evidence already changed")
    if not row:
        point.conn.execute(
            "INSERT INTO phase_aggregates VALUES(?,?,?,?,?)",
            (plan_key, *frozen, time.time()))
    return digest


def _freeze_decision(point: Checkpoint, decision_id: str, parent_hash: str,
                     aggregate_hash: str, activation: str,
                     value: Mapping[str, Any]) -> str:
    raw = canonical_json(value)
    frozen = ("F", parent_hash, aggregate_hash, activation, raw)
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if row and row != frozen:
        raise ImmutableViolation(f"Stage-F decision {decision_id} already changed")
    if not row:
        point.conn.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
            (decision_id, *frozen, time.time()))
    return sha256_json((decision_id, *frozen))


def _activation(point: Checkpoint, plan: Mapping[str, Any], parent: str,
                evidence_hash: str, group_ids: tuple[str, ...]) -> tuple[str, ...]:
    key = str(plan["plan_key"])
    value = _typed({
        "version": "c0b2-plan-activation-v1", "run_id": point.header()["run_id"],
        "budget_stage": "F", "plan_key": key,
        "plan_sha256": sha256_json(plan), "parent_decision_sha256": parent,
        "state": "ACTIVATED", "activated_group_ids": list(group_ids),
        "evidence_sha256": evidence_hash,
    }, PlanActivation)
    raw, digest = canonical_json(value), sha256_json(value)
    existing = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key=?", (key,)).fetchone()
    if existing and existing != (digest, raw):
        raise ImmutableViolation(f"phase activation {key} already changed")
    groups = {group["group_id"] for group in plan["groups"]}
    if not group_ids or any(group not in groups for group in group_ids):
        raise ImmutableViolation(f"phase activation {key} contains unknown groups")
    work = [item for group in group_ids for item in plan["work"]
            if item["activation_group_id"] == group]
    registered = _register_activated_work(point, plan, work)
    if not existing:
        point.conn.execute(
            "INSERT INTO plan_activations VALUES(?,?,?,?)",
            (key, digest, raw, time.time()))
    return registered


def _branch_presence(point: Checkpoint, later_work_ids: set[str]) -> dict[str, Any]:
    return {
        "aggregate": point.conn.execute(
            "SELECT count(*) FROM phase_aggregates WHERE plan_key='F_SEED_1'"
        ).fetchone()[0],
        "seed_decision": point.conn.execute(
            "SELECT count(*) FROM decisions WHERE decision_id='stage-f-seed-activation'"
        ).fetchone()[0],
        "provisional": point.conn.execute(
            "SELECT count(*) FROM decisions "
            "WHERE decision_id='stage-f-provisional-selection'"
        ).fetchone()[0],
        "completion": point.conn.execute(
            "SELECT count(*) FROM decisions WHERE decision_id=?",
            (completion_decision_id(point.header()),)).fetchone()[0],
        "artifact": point.conn.execute(
            "SELECT count(*) FROM public_artifacts WHERE artifact_id='stage-f-result'"
        ).fetchone()[0],
        "later": tuple(row[0] for row in point.conn.execute(
            "SELECT plan_key FROM plan_activations WHERE plan_key IN "
            "('F_SEED_17','F_SEED_20260804') ORDER BY rowid")),
        "registry": tuple(row for row in point.conn.execute(
            "SELECT work_id,plan_key,activation_group_id FROM phase_work_registry "
            "WHERE plan_key IN ('F_SEED_17','F_SEED_20260804') ORDER BY rowid")),
        "work": tuple(row[0] for row in point.conn.execute(
            "SELECT work_id FROM work_items WHERE stage='F' ORDER BY rowid")
            if row[0] in later_work_ids),
    }


def _finish_no_seed1(point: Checkpoint, master_hash: str, seed1_hash: str,
                     evidence_hash: str, *, master: Mapping[str, Any] | None = None,
                     seed1_evidence: Mapping[str, Any] | None = None) -> None:
    from .c0b2_runtime import freeze_public_artifact

    if (resolve_header_policy(point.header()) == CURRENT_POLICY
            and (master is None or seed1_evidence is None)):
        raise ImmutableViolation("current no-seed1 finalization lacks exact owners")
    if master is None or seed1_evidence is None:
        provisional = _typed({
            "version": "stage-f-selection-v1", "stage": "F",
            "plan_sha256": master_hash, "aggregate_sha256": evidence_hash,
            "outcome": "INCONCLUSIVE", "reason": "no_seed1_qualifier",
            "selection": None,
        }, ProvisionalDecision)
    else:
        provisional = build_no_seed1_provisional_decision(master, seed1_evidence)
    _freeze_decision(
        point, "stage-f-provisional-selection", master_hash,
        evidence_hash, "NOT_ACTIVATED", provisional)
    artifact = _inconclusive(point, "no_seed1_qualifier", evidence_hash)
    artifact_hash = freeze_public_artifact(point, "stage-f-result", artifact)
    completion_id, completion = build_completion_value(
        artifact, artifact_hash,
        {"deterministic_stop": True, "reason": "no_seed1_qualifier"})
    _freeze_decision(
        point, completion_id, seed1_hash, evidence_hash,
        "NOT_ACTIVATED", completion)
    if point.state() != "INCONCLUSIVE":
        point.conn.execute(
            "UPDATE run_state SET state='INCONCLUSIVE',updated=? WHERE id=1",
            (time.time(),))


def activate_f_later_seeds(
        point: Checkpoint, evidence: Mapping[str, Any],
        decision: Mapping[str, Any]) -> FLaterActivation:
    """Atomically freeze seed-1 evidence and activate both later seed subsets."""
    validate_seed1_evidence_artifact(evidence)
    validate_seed_activation_artifact(decision)
    _before_seed1_transaction()
    with runtime_transaction(point):
        validate_b4_f_control_census(point)
        master, master_hash = _master(point)
        corpus, run_nonce_key = _public_inputs(point)
        try:
            validate_f_master_plan(
                master, corpus=corpus, run_nonce_key=run_nonce_key)
            attempts, contexts, health = _seed1_inputs(point, master, corpus)
            normalized = validate_seed1_evidence(
                evidence, master, attempts, contexts, health, corpus=corpus)
        except (StageFError, StageFPlanError) as exc:
            raise ImmutableViolation(
                "seed-1 evidence differs from durable attempts") from exc
        expected_decision = expected_seed_activation(master, normalized)
        if validate_seed_activation_artifact(decision) != expected_decision:
            raise ImmutableViolation("later-seed activation differs from exact rotation")
        evidence_hash = sha256_json(normalized)
        plans = _seed_plans(master)
        seed1_hash = sha256_json(plans["F_SEED_1"])
        qualifiers = tuple(expected_decision["qualifier_candidate_ids"])
        activated_ids = tuple(expected_decision["activated_group_ids"])
        expected_work = tuple(item["work_id"] for group in activated_ids
                              for plan in (plans[key] for key in _LATER_KEYS)
                              for item in plan["work"]
                              if item["activation_group_id"] == group)
        expected_registry = tuple(
            (item["work_id"], plan["plan_key"], item["activation_group_id"])
            for group in activated_ids
            for plan in (plans[key] for key in _LATER_KEYS)
            for item in plan["work"] if item["activation_group_id"] == group)
        later_work_ids = {item["work_id"] for key in _LATER_KEYS
                          for item in plans[key]["work"]}
        presence = _branch_presence(point, later_work_ids)
        namespace = later_namespace_census(point)
        if any(namespace.values()):
            raise ImmutableViolation(
                f"later-seed activation has premature namespace rows: {namespace}")
        fresh = not any((presence["aggregate"], presence["seed_decision"],
                         presence["provisional"], presence["completion"],
                         presence["artifact"], presence["later"],
                         presence["registry"], presence["work"]))
        complete_later = (presence == {
            "aggregate": 1, "seed_decision": 1, "provisional": 0,
            "completion": 0, "artifact": 0, "later": _LATER_KEYS,
            "registry": expected_registry, "work": expected_work,
        } and point.state() == "RUNNING"
            and runtime_position(point) == RuntimePosition("F", "F_SEED_17"))
        complete_terminal = (presence == {
            "aggregate": 1, "seed_decision": 1, "provisional": 1,
            "completion": 1, "artifact": 1, "later": (),
            "registry": (), "work": (),
        } and point.state() == "INCONCLUSIVE"
            and runtime_position(point) == RuntimePosition("F", "F_SEED_1"))
        expected_complete = complete_later if qualifiers else complete_terminal
        if not fresh and not expected_complete:
            raise ImmutableViolation("later-seed activation is partial or conflicting")
        aggregate_hash = _freeze_phase_evidence(
            point, "F_SEED_1", seed1_hash, normalized)
        activation_state = "ACTIVATED" if qualifiers else "NOT_ACTIVATED"
        decision_hash = _freeze_decision(
            point, "stage-f-seed-activation", master_hash,
            aggregate_hash, activation_state, expected_decision)
        if qualifiers:
            parent = decision_hash
            activated = list(expected_decision["activated_group_ids"])
            for key in _LATER_KEYS:
                group_set = {row["group_id"] for row in plans[key]["groups"]}
                groups = tuple(group for group in activated if group in group_set)
                _activation(point, plans[key], parent, aggregate_hash, groups)
            if runtime_position(point) != RuntimePosition("F", "F_SEED_17"):
                point.conn.execute(
                    "UPDATE runtime_cursor SET active_stage='F',"
                    "active_plan_key='F_SEED_17',updated=? WHERE id=1", (time.time(),))
        else:
            _finish_no_seed1(
                point, master_hash, seed1_hash, aggregate_hash,
                master=master, seed1_evidence=normalized)
        _before_activation_commit()
    return FLaterActivation(
        evidence_hash, decision_hash, _LATER_KEYS if qualifiers else (),
        None if qualifiers else "no_seed1_qualifier")


def _provisional_selection(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        aggregate_hash: str, aggregate: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions "
        "WHERE decision_id='stage-f-provisional-selection'").fetchone()
    if not row:
        raise CheckpointError("acceptance requires a provisional Stage-F decision")
    value = validate_provisional_decision_artifact(json.loads(str(row[4])))
    if (resolve_payload_policy(value)
            != resolve_header_policy(point.header())
            or value != build_provisional_decision(aggregate)):
        raise ImmutableViolation("provisional Stage-F selection has mixed lineage")
    raw = canonical_json(value)
    if (row != ("F", master_hash, aggregate_hash, "ACTIVATED", raw)
            or value["plan_sha256"] != master_hash
            or value["aggregate_sha256"] != aggregate_hash
            or value["outcome"] != "PROVISIONAL_SELECTED"):
        raise ImmutableViolation("provisional Stage-F selection changed")
    return value, sha256_json(("stage-f-provisional-selection", *row))


def activate_f_acceptance(
        point: Checkpoint, value: Mapping[str, Any], *,
        final_aggregate_sha256: str) -> PhaseActivation:
    """Atomically activate the exact frozen C44 template for one provisional winner."""
    if not _SHA256.fullmatch(final_aggregate_sha256):
        raise ValueError("invalid final F aggregate SHA-256")
    _plan_for_point(point, AcceptancePlan, value)
    with runtime_transaction(point):
        validate_b4_f_control_census(point)
        assert_acceptance_namespace_clean(point)
        d_digest, d_decision = _validate_stored_d_owner(point)
        master, master_hash = _master(point)
        corpus, run_nonce_key = _public_inputs(point)
        try:
            validate_f_master_plan(
                master, corpus=corpus, run_nonce_key=run_nonce_key)
        except StageFPlanError as exc:
            raise ImmutableViolation("frozen F master no longer re-derives") from exc
        plans = _seed_plans(master)
        d_selections = [row["selection"] for row in d_decision["selections"]]
        f_selections = [
            {key: candidate[key] for key in CandidateSelection.model_fields}
            for candidate in plans["F_SEED_1"]["candidates"]]
        if master["parent_decision_sha256"] != d_digest or f_selections != d_selections:
            raise ImmutableViolation("F master differs from its stored final D owner")
        aggregate_value, _seed1, _seed_decision, seed_decision_digest = \
            validate_final_aggregate_owner(
                point, master, master_hash, corpus, final_aggregate_sha256)
        if (aggregate_value["plan_sha256"] != master_hash
                or aggregate_value["parent_decision_sha256"] != d_digest
                or aggregate_value["master_manifest_sha256"]
                != point.header()["master_manifest_sha256"]
                or aggregate_value["seed_activation_decision_sha256"]
                != seed_decision_digest
                or aggregate_value["candidate_order"]
                != master["base_candidate_order"]):
            raise ImmutableViolation("final F aggregate differs from activated lineage")
        provisional, parent = _provisional_selection(
            point, master, master_hash, final_aggregate_sha256, aggregate_value)
        plan = _plan_for_point(point, AcceptancePlan, value)
        selection = provisional["selection"]
        assert selection is not None
        candidate_id = next((
            row["candidate_id"] for row in plans["F_SEED_1"]["candidates"]
            if all(row[key] == selection[key] for key in selection)), None)
        template = next((row for row in master["acceptance_templates"]
                         if row["candidate_id"] == candidate_id), None)
        c_docs = {row.get("doc_id")
                  for row in json.loads(point.load_plan("C")[2])["work"]}
        if (not template or plan["parent_decision_sha256"] != parent
                or plan["master_plan_sha256"] != master_hash
                or plan["template_sha256"] != template["template_sha256"]
                or plan["candidates"] != template["payload"]["candidates"]
                or plan["work"] != template["payload"]["work"]
                or len(c_docs) != 44
                or {row["doc_id"] for row in plan["work"]} != c_docs
                or aggregate_value["ranking"]["winner_candidate_id"]
                != candidate_id):
            raise ImmutableViolation(
                "acceptance plan differs from the selected frozen C44")
        plan_hash = sha256_json(plan)
        expected_work = tuple(row["work_id"] for row in plan["work"])
        template_work = {
            item["work_id"] for row in master["acceptance_templates"]
            for item in row["payload"]["work"]}
        rows = {
            "plan": point.conn.execute(
                "SELECT count(*) FROM phase_plans WHERE plan_key='F_ACCEPTANCE'"
            ).fetchone()[0],
            "activation": point.conn.execute(
                "SELECT count(*) FROM plan_activations WHERE plan_key='F_ACCEPTANCE'"
            ).fetchone()[0],
            "registry": tuple(row[0] for row in point.conn.execute(
                "SELECT work_id FROM phase_work_registry "
                "WHERE plan_key='F_ACCEPTANCE' ORDER BY rowid")),
            "work": tuple(row[0] for row in point.conn.execute(
                "SELECT work_id FROM work_items WHERE stage='F' ORDER BY rowid")
                if row[0] in template_work),
        }
        fresh = rows == {"plan": 0, "activation": 0, "registry": (), "work": ()}
        complete = (rows == {"plan": 1, "activation": 1,
                             "registry": expected_work, "work": expected_work}
                    and runtime_position(point)
                    == RuntimePosition("F", "F_ACCEPTANCE")
                    and point.state() == "RUNNING")
        if not fresh and not complete:
            raise ImmutableViolation("acceptance activation is partial or conflicting")
        if fresh and (runtime_position(point)
                      != RuntimePosition("F", "F_SEED_20260804")
                      or point.state() != "RUNNING"):
            raise CheckpointError("acceptance activation is out of order")
        frozen_hash = freeze_phase_plan(point, plan)
        if frozen_hash != plan_hash:
            raise ImmutableViolation("acceptance plan hash changed during freeze")
        activation = _typed({
            "version": "c0b2-plan-activation-v1",
            "run_id": point.header()["run_id"], "budget_stage": "F",
            "plan_key": "F_ACCEPTANCE", "plan_sha256": plan_hash,
            "parent_decision_sha256": parent, "state": "ACTIVATED",
            "activated_group_ids": [],
            "evidence_sha256": final_aggregate_sha256,
        }, PlanActivation)
        activation_raw, activation_hash = (
            canonical_json(activation), sha256_json(activation))
        existing = point.conn.execute(
            "SELECT activation_hash,activation_json FROM plan_activations "
            "WHERE plan_key='F_ACCEPTANCE'").fetchone()
        if existing and existing != (activation_hash, activation_raw):
            raise ImmutableViolation("acceptance activation already changed")
        registered = _register_activated_work(point, plan, list(plan["work"]))
        if not existing:
            point.conn.execute(
                "INSERT INTO plan_activations VALUES(?,?,?,?)",
                ("F_ACCEPTANCE", activation_hash, activation_raw, time.time()))
        if runtime_position(point) != RuntimePosition("F", "F_ACCEPTANCE"):
            point.conn.execute(
                "UPDATE runtime_cursor SET active_stage='F',"
                "active_plan_key='F_ACCEPTANCE',updated=? WHERE id=1", (time.time(),))
        _before_activation_commit()
    return PhaseActivation("F_ACCEPTANCE", plan_hash, activation_hash, registered)


def _stored_plan_activation(
        point: Checkpoint, key: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    _parent, plan_hash, plan_raw = load_phase_plan(point, key)
    plan = _plan_for_point(point, FSeedPlan, json.loads(plan_raw))
    row = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key=?", (key,)).fetchone()
    if not row:
        raise CheckpointError(f"missing paired activation {key}")
    activation = _typed(_canonical_row(
        str(row[1]), str(row[0]), f"{key} activation"), PlanActivation)
    return plan, plan_hash, activation, str(row[0])


def advance_f_seed_cursor(point: Checkpoint) -> dict[str, Any]:
    """Atomically mark terminal F17 ownership and advance to preactivated F20260804."""
    with runtime_transaction(point):
        validate_b4_f_control_census(point)
        if point.state() != "RUNNING":
            raise CheckpointError("F seed transition requires a running checkpoint")
        master, master_hash = _master(point)
        corpus, run_nonce_key = _public_inputs(point)
        d_digest, d_decision = _validate_stored_d_owner(point)
        try:
            validate_f_master_plan(
                master, corpus=corpus, run_nonce_key=run_nonce_key)
        except StageFPlanError as exc:
            raise ImmutableViolation("F seed transition master cannot re-derive") from exc
        f_selections = [
            {field: candidate[field] for field in CandidateSelection.model_fields}
            for candidate in _seed_plans(master)["F_SEED_1"]["candidates"]]
        if (master["parent_decision_sha256"] != d_digest
                or f_selections != [row["selection"]
                                    for row in d_decision["selections"]]):
            raise ImmutableViolation("F seed transition differs from stored D owner")
        from_plan, from_hash, from_activation, from_activation_hash = \
            _stored_plan_activation(point, "F_SEED_17")
        to_plan, to_hash, to_activation, to_activation_hash = \
            _stored_plan_activation(point, "F_SEED_20260804")
        if (from_plan != _seed_plans(master)["F_SEED_17"]
                or to_plan != _seed_plans(master)["F_SEED_20260804"]):
            raise ImmutableViolation("paired seed plans differ from F master")
        _evidence, decision, decision_digest = validate_seed_activation_owner(
            point, master, master_hash, corpus)
        from_groups = list(from_activation["activated_group_ids"])
        to_groups = list(to_activation["activated_group_ids"])
        expected_from = [group for group in decision["activated_group_ids"]
                         if group in {row["group_id"] for row in from_plan["groups"]}]
        expected_to = [group for group in decision["activated_group_ids"]
                       if group in {row["group_id"] for row in to_plan["groups"]}]
        if (from_groups != expected_from or to_groups != expected_to
                or from_activation["parent_decision_sha256"] != decision_digest
                or to_activation["parent_decision_sha256"] != decision_digest):
            raise ImmutableViolation("paired seed activation or rotation changed")
        existing_marker = point.conn.execute(
            "SELECT created FROM events WHERE kind='F_SEED_CURSOR_TRANSITION'"
        ).fetchall()
        if len(existing_marker) == 1:
            marker_time = float(existing_marker[0][0])
            if point.conn.execute(
                    "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
                    "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_17' "
                    "AND a.updated>?", (marker_time,)).fetchone()[0]:
                raise ImmutableViolation("F17 attempt crossed its cursor transition")
            if point.conn.execute(
                    "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
                    "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_20260804' "
                    "AND a.created<?", (marker_time,)).fetchone()[0]:
                raise ImmutableViolation("F20260804 attempt predates its cursor transition")
        census, completed_ids = _f17_terminal_census(
            point, from_plan, from_groups)
        if point.conn.execute(
                "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
                "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_20260804'"
        ).fetchone()[0] and runtime_position(point) == RuntimePosition("F", "F_SEED_17"):
            raise ImmutableViolation("F20260804 attempt predates its cursor transition")
        marker_rows = point.conn.execute(
            "SELECT detail_json,created FROM events "
            "WHERE kind='F_SEED_CURSOR_TRANSITION' ORDER BY seq").fetchall()
        position = runtime_position(point)
        if position == RuntimePosition("F", "F_SEED_17"):
            if marker_rows:
                raise ImmutableViolation("F seed transition marker precedes its cursor")
            now = time.time()
            value = _evidence_transition_value(
                point.header()["run_id"], master_hash, decision_digest, from_hash,
                from_activation_hash, to_hash, to_activation_hash,
                from_groups, to_groups, census, _rfc3339(now),
                header=point.header())
            point.conn.execute(
                "INSERT INTO events(kind,detail_json,created) VALUES(?,?,?)",
                ("F_SEED_CURSOR_TRANSITION", canonical_json(value), now))
            point.conn.execute(
                "UPDATE runtime_cursor SET active_plan_key='F_SEED_20260804',"
                "updated=? WHERE id=1", (now,))
        elif position == RuntimePosition("F", "F_SEED_20260804"):
            if len(marker_rows) != 1:
                raise ImmutableViolation("F seed transition marker census changed")
            raw, created = str(marker_rows[0][0]), float(marker_rows[0][1])
            expected = _evidence_transition_value(
                point.header()["run_id"], master_hash, decision_digest, from_hash,
                from_activation_hash, to_hash, to_activation_hash,
                from_groups, to_groups, census, _rfc3339(created),
                header=point.header())
            value = expected
            cursor_updated = point.conn.execute(
                "SELECT updated FROM runtime_cursor WHERE id=1").fetchone()[0]
            if (canonical_json(expected) != raw or json.loads(raw) != expected
                    or float(cursor_updated) != created):
                raise ImmutableViolation("F seed transition replay changed")
        else:
            raise CheckpointError("F seed cursor transition is out of order")
        marker = point.conn.execute(
            "SELECT detail_json,created FROM events "
            "WHERE kind='F_SEED_CURSOR_TRANSITION'").fetchone()
        marker_time = float(marker[1])
        placeholders = ",".join("?" for _ in completed_ids)
        if point.conn.execute(
                f"SELECT count(*) FROM attempts WHERE work_id IN ({placeholders}) "
                "AND updated>?", (*completed_ids, marker_time)).fetchone()[0]:
            raise ImmutableViolation("F17 attempt crossed its cursor transition")
        if point.conn.execute(
                "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
                "ON r.work_id=a.work_id WHERE r.plan_key='F_SEED_20260804' "
                "AND a.created<?", (marker_time,)).fetchone()[0]:
            raise ImmutableViolation("F20260804 attempt predates its cursor transition")
        _before_activation_commit()
    return value


def start_stage_f(point: Checkpoint) -> ActiveFPhase:
    """Cross the reviewed D boundary and atomically activate the exact F master."""
    position = runtime_position(point)
    if (point.state(), position.active_stage, position.active_plan_key) not in {
            ("PAUSED_STAGE_BOUNDARY", "D", "D3_CONTEXT"),
            ("PAUSED_STAGE_BOUNDARY", "D", "D4_CONFIRMATION")}:
        raise CheckpointError("Stage-F start requires the exact final D boundary")
    corpus, key = _public_inputs(point)
    d_digest, decision = _validate_current_d_boundary(point)
    master = build_f_master_plan(
        d_digest, [row["selection"] for row in decision["selections"]],
        corpus=corpus, run_nonce_key=key,
        policy=resolve_header_policy(point.header()))
    freeze_activate_f_master(point, master)
    stored, _digest = _master(point)
    return _active_f_phase(point, stored, corpus, key)


def _terminal_artifact(
        point: Checkpoint, *, owner_plan_hash: str, aggregate_hash: str,
        artifact: Mapping[str, Any],
) -> str:
    """Join one transaction that freezes result, completion, and terminal state."""
    from .c0b2_runtime import freeze_public_artifact

    terminal = str(artifact["terminal"])
    artifact_hash = freeze_public_artifact(point, "stage-f-result", artifact)
    if terminal == "SELECTED":
        facts = {
            "accepted_document_count": 166,
            "gates": {name: True for name in sorted(PUBLIC_ACCEPTANCE_GATES)},
        }
        activation = "ACTIVATED"
    elif terminal == "INCONCLUSIVE":
        facts = {"deterministic_stop": True, "reason": artifact["reason"]}
        activation = "NOT_ACTIVATED"
    else:  # pragma: no cover - strict result builders protect callers
        raise StageFRuntimeError("unsupported Stage-F quality terminal")
    completion_id, completion = build_completion_value(
        artifact, artifact_hash, facts)
    _freeze_decision(
        point, completion_id, owner_plan_hash, aggregate_hash,
        activation, completion)
    if point.state() != terminal:
        if point.state() != "RUNNING":
            raise CheckpointError("Stage-F terminal requires a running checkpoint")
        point.conn.execute(
            "UPDATE run_state SET state=?,updated=? WHERE id=1",
            (terminal, time.time()))
    return artifact_hash


def _finalize_seed1(
        point: Checkpoint, master: Mapping[str, Any], corpus: PublicCorpus,
) -> FLaterActivation:
    attempts, contexts, health = _seed1_inputs(point, master, corpus)
    try:
        evidence = build_seed1_evidence_from_attempts(
            master, attempts, contexts, health, corpus=corpus)
        decision = build_seed_activation_decision(master, evidence)
    except StageFError as exc:
        raise ImmutableViolation("seed-1 final evidence cannot re-derive") from exc
    result = activate_f_later_seeds(point, evidence, decision)
    if result.terminal_reason is not None:
        validate_b4_terminal_owner(point.conn, point.header())
    return result


def _finalize_all_seeds(
        point: Checkpoint, master: Mapping[str, Any], master_hash: str,
        corpus: PublicCorpus,
) -> str:
    """Freeze the attempt-derived all-seed aggregate and select its exact branch."""
    from .c0b2_runtime_f_evidence import all_seed_attempt_inputs

    plan = _seed_plans(master)["F_SEED_20260804"]
    with runtime_transaction(point):
        if (point.state() != "RUNNING" or runtime_position(point)
                != RuntimePosition("F", "F_SEED_20260804")):
            raise CheckpointError("all-seed finalization is out of order")
        validate_b4_f_control_census(point)
        seed1, activation, activation_digest = validate_seed_activation_owner(
            point, master, master_hash, corpus)
        try:
            aggregate = build_stage_f_aggregate_from_attempts(
                master, seed1, activation,
                all_seed_attempt_inputs(point, master, seed1),
                seed_activation_decision_sha256=activation_digest,
                corpus=corpus)
            provisional = build_provisional_decision(aggregate)
        except StageFError as exc:
            raise ImmutableViolation("final F aggregate cannot re-derive") from exc
        aggregate_hash = sha256_json(aggregate)
        stored_hash = _freeze_phase_evidence(
            point, "F_SEED_20260804", sha256_json(plan), aggregate)
        if stored_hash != aggregate_hash:
            raise ImmutableViolation("final F aggregate changed during freeze")
        activation_state = ("ACTIVATED" if provisional["outcome"]
                            == "PROVISIONAL_SELECTED" else "NOT_ACTIVATED")
        provisional_digest = _freeze_decision(
            point, "stage-f-provisional-selection", master_hash,
            aggregate_hash, activation_state, provisional)
        if provisional["outcome"] == "INCONCLUSIVE":
            artifact = _inconclusive(
                point, provisional["reason"], aggregate_hash)
            _terminal_artifact(
                point, owner_plan_hash=sha256_json(plan),
                aggregate_hash=aggregate_hash, artifact=artifact)
            validate_b4_terminal_owner(point.conn, point.header())
            return provisional["reason"]
    winner = aggregate["ranking"]["winner_candidate_id"]
    if winner is None:
        raise ImmutableViolation("selected F branch lacks one winner")
    acceptance = build_acceptance_plan(
        master, candidate_id=winner,
        provisional_decision_sha256=provisional_digest)
    activate_f_acceptance(
        point, acceptance, final_aggregate_sha256=aggregate_hash)
    return "ACCEPTANCE_ACTIVATED"


def _d50_source_aggregate(point: Checkpoint,
                          decision: Mapping[str, Any]) -> dict[str, Any]:
    from .c0b2_runtime_d import _restore_d_category_order

    key = {"D3": "D3_CONTEXT", "D4": "D4_CONFIRMATION"}.get(
        decision.get("phase"))
    if key is None:
        raise ImmutableViolation("final D decision lacks its aggregate owner")
    row = point.conn.execute(
        "SELECT aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (key,)).fetchone()
    if not row:
        raise ImmutableViolation("final D aggregate owner is absent")
    return _restore_d_category_order(_canonical_row(
        str(row[1]), str(row[0]), "final D source aggregate"))


def _finalize_acceptance(
        point: Checkpoint, phase: ActiveFPhase, master: Mapping[str, Any],
        master_hash: str, corpus: PublicCorpus,
) -> str:
    """Freeze C44/166-document evidence and the exact public result atomically."""
    from .c0b2_runtime_f_evidence import (
        acceptance_attempt_inputs, validate_final_f_owner,
    )

    with runtime_transaction(point):
        if (point.state() != "RUNNING" or runtime_position(point)
                != RuntimePosition("F", "F_ACCEPTANCE")):
            raise CheckpointError("acceptance finalization is out of order")
        validate_b4_f_control_census(point)
        f_aggregate, _f_hash, provisional, provisional_digest = \
            validate_final_f_owner(point, master, master_hash, corpus)
        d_digest, d_decision = _validate_stored_d_owner(point)
        d_aggregate = _d50_source_aggregate(point, d_decision)
        try:
            c44 = build_c44_scored_aggregate(
                phase.plan, acceptance_attempt_inputs(point, phase.plan),
                corpus=corpus)
            winner = next(row for row in f_aggregate["candidates"]
                          if row["candidate_id"]
                          == f_aggregate["ranking"]["winner_candidate_id"])
            acceptance = build_acceptance_aggregate(
                phase.plan, provisional_decision=provisional,
                provisional_decision_sha256=provisional_digest,
                c44_scored=c44, final_d_decision=d_decision,
                d50_source_aggregate=d_aggregate,
                stage_d_decision_sha256=d_digest,
                stage_f_aggregate=f_aggregate, f_master=master, corpus=corpus,
                cancellation_health_passed=winner["cancellation_health"]["passed"],
                provenance_passed=True, safety_passed=True)
            artifact = build_final_result(
                master_manifest_sha256=corpus.master_manifest_sha256,
                stage_c_selection_sha256=_decision_digest(
                    point, "stage-c-selection"),
                stage_d_decision_sha256=d_digest,
                stage_f_aggregate=f_aggregate,
                provisional_decision=provisional,
                provisional_decision_sha256=provisional_digest,
                acceptance_plan=phase.plan, acceptance_aggregate=acceptance)
        except (StopIteration, StageFError) as exc:
            raise ImmutableViolation("acceptance evidence cannot re-derive") from exc
        aggregate_hash = sha256_json(acceptance)
        if _freeze_phase_evidence(
                point, "F_ACCEPTANCE", phase.plan_sha256,
                acceptance) != aggregate_hash:
            raise ImmutableViolation("acceptance aggregate changed during freeze")
        artifact_hash = _terminal_artifact(
            point, owner_plan_hash=phase.plan_sha256,
            aggregate_hash=aggregate_hash, artifact=artifact)
        if validate_b4_terminal_owner(
                point.conn, point.header()) != artifact_hash:
            raise ImmutableViolation("Stage-F terminal validator changed result identity")
    return artifact["terminal"]


def _preclaim_f_phase_index(
        point: Checkpoint, master: Mapping[str, Any], corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> tuple[ActiveFPhase, dict[str, RequestSpec]]:
    """Validate the active namespace and index every claimable request pre-contact."""
    from .c0b2_runtime_f_namespace import validate_active_f_namespace

    validate_active_f_namespace(point)
    key = runtime_position(point).active_plan_key
    if key == "F_ACCEPTANCE":
        _parent, plan_hash, plan_raw = load_phase_plan(point, key)
        active_plan = _plan_for_point(
            point, AcceptancePlan,
            _canonical_row(plan_raw, plan_hash, "active acceptance plan"))
        indexed = request_specs_for_activated_f_plan(
            master, active_plan, corpus=corpus, run_nonce_key=run_nonce_key)
    else:
        indexed = request_specs_for_f_plan(
            master, key, corpus=corpus, run_nonce_key=run_nonce_key)
    phase = _active_f_phase(
        point, master, corpus, run_nonce_key, independently_validated=True,
        namespace_validated=True)
    work_specs = {row["work_id"]: _work_spec(
        indexed, row["work_id"], row["request_sha256"]) for row in phase.work}
    if list(work_specs) != [row["work_id"] for row in phase.work]:
        raise ImmutableViolation("active F request index differs from registry order")
    return phase, work_specs


def run_stage_f_invocation(
        point: Checkpoint, lock: GlobalExecutionLock, *,
        transport_factory: Callable[
            [Callable[[Any], RequestSpec], Mapping[str, Any]], Any],
        cancellation: CancellationController | None = None,
) -> StageFRunResult:
    """Run one bounded F invocation without crossing an activation boundary."""
    master, master_hash = _master(point)
    corpus, key = _public_inputs(point)
    header = point.header()
    specs: dict[str, RequestSpec] = {}
    phase, work_specs = _preclaim_f_phase_index(point, master, corpus, key)

    def resolver(request: Any) -> RequestSpec:
        if isinstance(request, WorkRequest):
            return _work_spec(work_specs, request.work_id, request.request_hash)
        spec = specs.get(request.control_id)
        if spec is None:
            raise StageFRuntimeError("transport requested an unknown F control")
        return spec

    transport = DeferredTransport(lambda: transport_factory(resolver, header))
    executor = DurableExecutor(
        point, lock, transport,
        cancellation=cancellation or CancellationController(),
        enforce_public_budget_contract=True)
    health_not_before = _pending_health_not_before(point)
    if (health_not_before is not None
            and not executor.interruptible_backoff(health_not_before)):
        return StageFRunResult(
            point.state(), runtime_position(point).active_plan_key,
            point.state(), health_not_before)

    def recovered_guard() -> None:
        from .c0b2_runtime_f_namespace import validate_active_f_namespace

        validate_active_f_namespace(point, strict_controls=True)
        recovered = _active_f_phase(
            point, master, corpus, key, independently_validated=True,
            namespace_validated=True)
        if recovered != phase:
            raise ImmutableViolation("active F phase changed during recovery")

    _orphans, ordinal = executor.recover_and_start(
        "F", post_recovery_guard=recovered_guard)
    from .c0b2_runtime_f_namespace import validate_active_f_namespace
    validate_active_f_namespace(point, strict_controls=True)
    if _active_f_phase(
            point, master, corpus, key, independently_validated=True,
            namespace_validated=True) != phase:
        raise ImmutableViolation("active F phase changed during invocation claim")

    def result(outcome: str, retry_not_before: float = 0.0) -> StageFRunResult:
        return StageFRunResult(
            point.state(), runtime_position(point).active_plan_key,
            outcome, retry_not_before)

    def stopped(value: ExecutionResult) -> Optional[StageFRunResult]:
        if value.outcome == "RETRY_WAIT":
            if executor.interruptible_backoff(value.retry_not_before):
                return None
            return result(point.state(), value.retry_not_before)
        if value.outcome in _ANSWERED | {"ALREADY_COMPLETE"}:
            return None
        return result(value.outcome, value.retry_not_before)

    for kind, model, spec in _preflight_specs(header, phase):
        identity = control_id("F", ordinal, kind, model)
        specs[identity] = spec
        while True:
            attempt_no, call_class = _attempt_number(
                point, control_id_value=identity)
            request = ControlRequest(
                "F", identity, model, request_spec_hash(spec),
                attempt_no, call_class)
            answer = executor.run_control(request, kind=kind)
            stop = stopped(answer)
            if answer.outcome != "RETRY_WAIT" or stop is not None:
                break
        if stop is not None:
            return stop
    validate_b4_f_control_census(point)

    def drain_resource() -> Optional[StageFRunResult]:
        priority = executor._f_context_recovery_owner()
        obligation = (point.backoff(priority.model) if priority is not None
                      else executor._resource_obligation())
        if priority is not None and obligation.failures < 6:
            return None
        if obligation is None:
            return None
        if priority is not None:
            resolved = resolve_f_seed1_control(
                master, priority.context_control_id,
                corpus=corpus, run_nonce_key=key)
            if resolved.source_work_id != priority.trigger_work_id:
                raise ImmutableViolation("context recovery trigger changed")
            spec = _work_spec(
                work_specs, resolved.source_work_id,
                priority.trigger_request_hash)
            prioritized = priority.context_control_id
        else:
            from .c0b2_runtime_d import _resource_probe_spec
            spec = _resource_probe_spec(point, obligation.model)
            prioritized = None
        request_hash = request_spec_hash(spec)
        identity = resource_probe_id(
            "F", ordinal, obligation.model, request_hash)
        specs[identity] = spec
        while True:
            attempt_no, call_class = _attempt_number(
                point, control_id_value=identity,
                first_class="transport_orphan")
            request = ControlRequest(
                "F", identity, obligation.model, request_hash,
                attempt_no, call_class)
            answer = executor.run_resource_probe(
                request, prioritized_context_control_id=prioritized)
            stop = stopped(answer)
            if answer.outcome != "RETRY_WAIT" or stop is not None:
                return stop

    def run_seed1_control(kind: str, control: Mapping[str, Any]
                          ) -> Optional[StageFRunResult]:
        resolved = resolve_f_seed1_control(
            master, str(control["control_id"]),
            corpus=corpus, run_nonce_key=key)
        spec = resolved.request_spec
        request_hash = request_spec_hash(spec)
        expected_hash = (control["payload_sha256"] if kind == "context"
                         else control["request_sha256"])
        if request_hash != expected_hash or spec.expected_model is None:
            raise ImmutableViolation("resolved F control request changed")
        specs[str(control["control_id"])] = spec
        attempt_no, call_class = _attempt_number(
            point, control_id_value=str(control["control_id"]))
        request = ControlRequest(
            "F", str(control["control_id"]), spec.expected_model,
            request_hash, attempt_no, call_class)
        if kind == "context":
            answer = executor.run_context_probe(request)
        elif kind == "cancel":
            answer = executor.run_cancellation_probe(request)
        else:
            cancel = next(
                group["cancellation_control"] for group in phase.plan["groups"]
                if group["candidate_id"] == control["candidate_id"])
            row = point.conn.execute(
                "SELECT evidence_json FROM runtime_controls WHERE control_id=?",
                (cancel["control_id"],)).fetchone()
            if not row:
                raise ImmutableViolation("health lacks cancellation evidence")
            cancel_evidence = json.loads(str(row[0]))
            options = spec.payload["options"]
            answer = executor.run_cancellation_health(
                request, cancelled_attempt_id=cancel_evidence["cancel_attempt_id"],
                source=str(resolved.source_chunk), worksheet=str(spec.worksheet),
                num_predict=int(options["num_predict"]),
                num_ctx=int(options["num_ctx"]))
        stop = stopped(answer)
        if kind == "cancel" and answer.outcome == "CANCELLED_UNVERIFIED":
            return result("CANCELLED_UNVERIFIED")
        return stop

    while True:
        seed1_obligation = (_seed1_next(point, phase)
                            if phase.plan["plan_key"] == "F_SEED_1" else None)
        unattempted_health = (seed1_obligation is not None
                              and seed1_obligation[0] == "health"
                              and point.conn.execute(
                                  "SELECT 1 FROM attempts WHERE control_id=? LIMIT 1",
                                  (seed1_obligation[1]["control_id"],)).fetchone()
                              is None)
        if unattempted_health:
            stop = run_seed1_control(*seed1_obligation)
            if stop is not None:
                return stop
            continue
        resource_stop = drain_resource()
        if resource_stop is not None:
            return resource_stop
        if phase.plan["plan_key"] == "F_SEED_1":
            obligation = seed1_obligation
            if obligation is None:
                branch = _finalize_seed1(point, master, corpus)
                return result(branch.terminal_reason or "LATER_SEEDS_ACTIVATED")
            kind, item = obligation
            if kind == "work":
                attempt_no, call_class = _attempt_number(
                    point, work_id=item["work_id"])
                answer = executor.run(WorkRequest(
                    "F", item["work_id"], item["model"],
                    item["request_sha256"], attempt_no, call_class))
                stop = stopped(answer)
            else:
                stop = run_seed1_control(kind, item)
            if stop is not None:
                return stop
            continue
        pending = _pending_work(phase, point)
        if pending is not None:
            attempt_no, call_class = _attempt_number(
                point, work_id=pending["work_id"])
            answer = executor.run(WorkRequest(
                "F", pending["work_id"], pending["model"],
                pending["request_sha256"], attempt_no, call_class))
            stop = stopped(answer)
            if stop is not None:
                return stop
            continue
        if phase.plan["plan_key"] == "F_SEED_17":
            advance_f_seed_cursor(point)
            return result("F20260804_ACTIVATED")
        if phase.plan["plan_key"] == "F_SEED_20260804":
            return result(_finalize_all_seeds(
                point, master, master_hash, corpus))
        return result(_finalize_acceptance(
            point, phase, master, master_hash, corpus))


def _public_result(
        point: Checkpoint, run_id: str, outcome: str = "STATUS",
        retry_not_before: float = 0.0,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "run_id": run_id, "stage": "F", "state": point.state(),
        "active_plan_key": runtime_position(point).active_plan_key,
        "outcome": outcome, "calls_total": point.usage()["total"],
    }
    if retry_not_before > 0:
        value["retry_not_before"] = int(retry_not_before)
    return value


def run_public_stage_f(
        run_id: str, *, resume: bool = False,
        benchmark_root: Path | None = None,
        transport_factory: Callable[
            [Callable[[Any], RequestSpec], Mapping[str, Any]], Any] | None = None,
        expected_protocol_id: str | None = None,
) -> dict[str, Any]:
    """Gate, run, finalize, and receipt one live Stage-F invocation."""
    from .c0b2_runtime import (
        _checkpoint_path, ensure_backup_receipt, finish_public_run_failure,
        revalidate_source_pins,
    )
    from .c0b2_transport import BoundedOllamaTransport
    from .c0b3_policy import require_checkpoint_header, require_expected_header

    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    path = _checkpoint_path(run_id, root)
    require_checkpoint_header(path, expected_protocol_id)
    with GlobalExecutionLock(root) as lock:
        point = Checkpoint.open(path, root)
        try:
            require_expected_header(point.header(), expected_protocol_id)
            position, state = runtime_position(point), point.state()
            if state in TERMINAL_STATES:
                if position.active_stage != "F":
                    raise StageFRuntimeError(
                        "terminal re-entry stage differs from command stage F")
                if state != "BLOCKED_PROVENANCE":
                    revalidate_source_pins(point.header())
                if state in {"SELECTED", "INCONCLUSIVE"}:
                    validate_b4_terminal_owner(point.conn, point.header())
                ensure_backup_receipt(point, lock)
                return _public_result(point, run_id)
            starting = position.active_stage == "D"
            if starting:
                if (resume or state != "PAUSED_STAGE_BOUNDARY"
                        or position.active_plan_key not in {
                            "D3_CONTEXT", "D4_CONFIRMATION"}):
                    raise StageFRuntimeError(
                        "run F requires the exact receipted final D boundary")
            elif position.active_stage == "F":
                if not resume:
                    raise StageFRuntimeError("an existing Stage-F run requires resume")
                if state not in RESUMABLE_STATES or state in {
                        "PREPARED", "PAUSED_STAGE_BOUNDARY"}:
                    raise StageFRuntimeError("Stage-F state is not ordinarily resumable")
            else:
                raise StageFRuntimeError("Stage-F command cannot run from this cursor")
            try:
                revalidate_source_pins(point.header())
                if not starting:
                    master, _master_hash = _master(point)
                    corpus, key = _public_inputs(point)
            except Exception:
                finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                ensure_backup_receipt(point, lock)
                raise
            if starting:
                ensure_backup_receipt(point, lock)
                try:
                    start_stage_f(point)
                    master, _master_hash = _master(point)
                    corpus, key = _public_inputs(point)
                except Exception:
                    finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                    raise
            cancellation = CancellationController()
            guard = _LiveSignalGuard(cancellation)
            guard.__enter__()

            def guarded_factory(
                    resolver: Callable[[Any], RequestSpec],
                    header: Mapping[str, Any],
            ) -> Any:
                transport = (transport_factory(resolver, header)
                             if transport_factory is not None else
                             BoundedOllamaTransport(
                                 resolver, endpoint=header["ollama_endpoint"]))
                return transport

            try:
                result = run_stage_f_invocation(
                    point, lock, transport_factory=guarded_factory,
                    cancellation=cancellation)
            except InvocationCancelled:
                return _public_result(point, run_id, point.state())
            except (ImmutableViolation, StageFRuntimeError,
                    StageFPlanError, StageFError):
                if point.state() not in TERMINAL_STATES:
                    finish_public_run_failure(
                        point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                raise
            finally:
                guard.__exit__(None, None, None)
            if point.state() in TERMINAL_STATES:
                if point.state() != "BLOCKED_PROVENANCE":
                    revalidate_source_pins(point.header())
                if point.state() in {"SELECTED", "INCONCLUSIVE"}:
                    validate_b4_terminal_owner(point.conn, point.header())
                ensure_backup_receipt(point, lock)
            return _public_result(
                point, run_id, result.outcome, result.retry_not_before)
        finally:
            point.close()
