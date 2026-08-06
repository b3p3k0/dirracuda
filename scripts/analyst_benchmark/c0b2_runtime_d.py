"""Durable, transport-independent Stage-D orchestration for C0B-2.

The driver is the only public-runtime path that may dispatch activated Stage-D work.
It re-derives plans and controls before every invocation, keeps D context probes out of
the Stage-F group binder, and makes phase finalization one SQLite transaction.

DISPOSITION: benchmark-only diagnostic; remove after C0B artifacts are accepted.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import goldset, report
from .c0b2_checkpoint import (
    RESUMABLE_STATES, TERMINAL_STATES, Checkpoint, CheckpointError,
    ImmutableViolation, canonical_json, sha256_json,
)
from .c0b2_executor import (
    SERVER_CONTROL_MODEL, CancellationController, ControlRequest, DurableExecutor,
    ExecutionResult,
    FakeResponse, ProvenanceFailure, RetryableTransport, SafetyLimit, WorkRequest,
    SoftWallReached, control_id, resource_probe_id,
)
from .c0b2_fsprobe import GlobalExecutionLock
from .c0b2_public_schema import (
    BackupAnchor, ContextProbeEvidence, PlanActivation, validate_artifact,
)
from .c0b2_plan import attempt_id as stable_attempt_id
from .c0b2_runtime_common import (
    freeze_activate_phase_plan, load_phase_plan, runtime_position,
    runtime_transaction,
)
from .c0b2_stage_d_plan import (
    D50Corpus, StageDContextControl, StageDPlanError, build_d1_plan,
    build_d2_plan, build_d3_plan, build_d4_plan,
    d1_candidates_from_stage_c_selection, derive_d_context_controls, load_d50,
    request_spec_for_d_work, validate_d_plan, verified_run_nonce_key,
)
from .c0b2_stage_d import StageDError
from .c0b2_transport import RequestSpec, request_spec_hash


PHASE_KEYS = ("D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT", "D4_CONFIRMATION")
PARENT_DECISION = {
    "D1_OUTPUT": "stage-c-selection",
    "D2_CHUNK": "stage-d-d1-selection",
    "D3_CONTEXT": "stage-d-d2-selection",
    "D4_CONFIRMATION": "stage-d-d3-selection",
}
NEXT_KEY = {
    "D1_OUTPUT": "D2_CHUNK", "D2_CHUNK": "D3_CONTEXT",
    "D3_CONTEXT": "D4_CONFIRMATION",
}
PHASE_CALL_CAPS = {
    "D1_OUTPUT": 55, "D2_CHUNK": 216,
    "D3_CONTEXT": 243, "D4_CONFIRMATION": 243,
}
_ANSWERED = {"ACCEPTED", "SCHEMA_INVALID"}
_COMPLETE_WORK = {"SUCCEEDED", "COMPLETED_INVALID"}


class StageDRuntimeError(RuntimeError):
    """Stage-D provenance, sequencing, or evidence is not exact."""


@dataclass(frozen=True)
class StageDInputs:
    corpus: D50Corpus
    run_nonce_key: bytes
    master_manifest_sha256: str


@dataclass(frozen=True)
class ActiveDPhase:
    plan: dict[str, Any]
    plan_sha256: str
    controls: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StageDRunResult:
    state: str
    active_plan_key: str
    outcome: str
    retry_not_before: float = 0.0


def _decision_digest(point: Checkpoint, decision_id: str) -> tuple[str, dict[str, Any]]:
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    if not row or row[3] != "ACTIVATED":
        raise ImmutableViolation(f"activated parent decision {decision_id} is missing")
    try:
        value = json.loads(str(row[4]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"decision {decision_id} is not JSON") from exc
    if not isinstance(value, dict) or canonical_json(value) != row[4]:
        raise ImmutableViolation(f"decision {decision_id} is not canonical")
    return sha256_json((decision_id, *row)), value


def _stage_c_boundary_parent(
        point: Checkpoint, *, verify_snapshot: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Bind D1 to the exact immutable and receipted Stage-C boundary."""
    from .c0b2_runtime import (
        _anchor_from_connection, _load_receipt, _readonly_connection,
        _receipt_snapshot_path, _verify_receipt_file,
    )
    from .c0b2_schema import validate_stage_c_selection

    _plan_parent, plan_hash, _plan_raw = point.load_plan("C")
    aggregate = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM stage_aggregates "
        "WHERE stage='C'",
    ).fetchone()
    if not aggregate:
        raise ImmutableViolation("D1 parent lacks the Stage-C aggregate")
    try:
        aggregate_value = json.loads(str(aggregate[2]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation("Stage-C aggregate is not JSON") from exc
    if (aggregate != (plan_hash, sha256_json(aggregate_value),
                       canonical_json(aggregate_value))):
        raise ImmutableViolation("D1 parent Stage-C aggregate changed")
    decision_row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id='stage-c-selection'",
    ).fetchone()
    if not decision_row:
        raise ImmutableViolation("D1 parent lacks the Stage-C selection")
    try:
        selection = validate_stage_c_selection(json.loads(str(decision_row[4])))
    except Exception as exc:
        raise ImmutableViolation("Stage-C selection is not exact") from exc
    if (decision_row[:4] != ("C", plan_hash, aggregate[1], "ACTIVATED")
            or canonical_json(selection) != decision_row[4]
            or (selection["plan_sha256"], selection["aggregate_sha256"])
            != (plan_hash, aggregate[1])):
        raise ImmutableViolation("D1 parent differs from the Stage-C boundary")
    decision_hash = sha256_json(("stage-c-selection", *decision_row))
    anchor = validate_artifact(BackupAnchor, {
        "version": "c0b2-backup-anchor-v1",
        "run_id": point.header()["run_id"], "active_stage": "C",
        "state": "PAUSED_STAGE_BOUNDARY", "f_master_plan_sha256": None,
        "plans": [{"plan_key": "C", "plan_sha256": plan_hash,
                   "activation_sha256": None}],
        "aggregate_sha256": aggregate[1],
        "decision_or_artifact_sha256": decision_hash,
        "charged_call_total": int(point.conn.execute(
            "SELECT count(*) FROM attempts WHERE stage='C'").fetchone()[0]),
    })
    anchor_hash, anchor_raw = sha256_json(anchor), canonical_json(anchor)
    receipt_row = point.conn.execute(
        "SELECT anchor_json,receipt_hash,receipt_json FROM backup_receipts "
        "WHERE anchor_hash=?", (anchor_hash,),
    ).fetchone()
    if not receipt_row or receipt_row[0] != anchor_raw:
        raise ImmutableViolation("D1 parent lacks its exact Stage-C receipt")
    receipt = _load_receipt(
        str(receipt_row[2]), str(receipt_row[1]), anchor_hash=anchor_hash)
    if verify_snapshot:
        _verify_receipt_file(point.path.parent, receipt)
        snapshot = _receipt_snapshot_path(
            point.path.parent, receipt["snapshot_run_relative_path"])
        connection = _readonly_connection(snapshot)
        try:
            if canonical_json(_anchor_from_connection(connection)) != anchor_raw:
                raise ImmutableViolation(
                    "Stage-C receipt snapshot differs from its boundary anchor")
        finally:
            connection.close()
    return decision_hash, selection


def load_stage_d_inputs(
        point: Checkpoint, *, manifest_path: Path = goldset.MANIFEST,
) -> StageDInputs:
    """Load only public D50 bytes after the run key reproduces frozen Stage C."""
    if point.header().get("run_type") != "public":
        raise StageDRuntimeError("Stage D requires a public checkpoint")
    try:
        master_hash, master_raw = point.load_manifest("master")
        _key_hash, key_raw = point.load_manifest("run_nonce_key")
        _c_parent, _c_hash, c_raw = point.load_plan("C")
        key = verified_run_nonce_key(key_raw, c_raw)
        corpus = load_d50(
            master_raw, master_manifest_sha256=master_hash,
            manifest_path=manifest_path)
    except Exception as exc:
        raise StageDRuntimeError(
            "Stage-D fixtures or run nonce provenance changed") from exc
    return StageDInputs(corpus, key, master_hash)


def _validated_d_decision(value: Mapping[str, Any], phase: str) -> dict[str, Any]:
    """Strictly parse a decision; evidence ownership is checked by its caller."""
    from .c0b2_stage_d import FinalDecision, IntermediateDecision

    model = (FinalDecision if phase == "D4" or value.get("outcome") == "FINALISTS"
             else IntermediateDecision)
    try:
        normalized = model.model_validate(value, strict=True).model_dump(mode="json")
    except Exception as exc:
        raise ImmutableViolation(f"{phase} decision is not exact") from exc
    if normalized["phase"] != phase:
        raise ImmutableViolation(f"{phase} decision carries another phase")
    return normalized


def _load_owned_intermediate(
        point: Checkpoint, owner_key: str, decision_id: str,
        inputs: StageDInputs,
) -> tuple[str, dict[str, Any]]:
    """Rebuild a parent aggregate and decision from its durable attempt evidence."""
    from .c0b2_stage_d import (
        build_stage_d_aggregate, build_stage_d_decision,
        validate_stage_d_aggregate,
    )

    owner = _load_exact_phase_by_key(point, owner_key, inputs)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (owner_key,),
    ).fetchone()
    if not row:
        raise ImmutableViolation(f"{owner_key} aggregate is missing")
    try:
        stored_aggregate = validate_stage_d_aggregate(json.loads(str(row[2])))
        rebuilt = build_stage_d_aggregate(
            owner.plan, _phase_evidence(point, owner), corpus=inputs.corpus,
            context_controls=owner.controls,
            context_probes=tuple(_context_evidence_by_candidate(
                point, owner).values()),
        )
    except Exception as exc:
        raise ImmutableViolation(f"{owner_key} aggregate cannot be rebuilt") from exc
    aggregate_raw = canonical_json(stored_aggregate)
    if (row != (owner.plan_sha256, sha256_json(stored_aggregate), aggregate_raw)
            or aggregate_raw != canonical_json(rebuilt)):
        raise ImmutableViolation(f"{owner_key} aggregate changed from attempt evidence")
    expected = build_stage_d_decision(stored_aggregate, owner.plan)
    digest, stored = _decision_digest(point, decision_id)
    decision_row = point.conn.execute(
        "SELECT parent_hash,aggregate_hash FROM decisions WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    if (decision_row != (owner.plan_sha256, row[1])
            or canonical_json(stored) != canonical_json(expected)
            or expected.get("outcome") != "CONTINUE"):
        raise ImmutableViolation(f"{decision_id} differs from its exact owner evidence")
    return digest, _validated_d_decision(stored, owner.plan["phase"])


def _expected_plan(point: Checkpoint, key: str,
    inputs: StageDInputs) -> dict[str, Any]:
    if key == "D1_OUTPUT":
        parent_hash, parent = _stage_c_boundary_parent(point)
        candidates = d1_candidates_from_stage_c_selection(parent)
        builder = build_d1_plan
    else:
        owner_key = {
            "D2_CHUNK": "D1_OUTPUT", "D3_CONTEXT": "D2_CHUNK",
            "D4_CONFIRMATION": "D3_CONTEXT",
        }[key]
        parent_hash, decision = _load_owned_intermediate(
            point, owner_key, PARENT_DECISION[key], inputs)
        candidates = list(decision["selections"])
        builder = {
            "D2_CHUNK": build_d2_plan, "D3_CONTEXT": build_d3_plan,
            "D4_CONFIRMATION": build_d4_plan,
        }[key]
        if key == "D4_CONFIRMATION":
            candidates = [row for row in candidates if row["num_ctx"] < 16384]
            if not candidates:
                raise ImmutableViolation("an all-16384 D3 decision cannot create D4")
    return builder(
        parent_hash, candidates, corpus=inputs.corpus,
        run_nonce_key=inputs.run_nonce_key)


def _load_exact_phase_by_key(point: Checkpoint, key: str,
                             inputs: StageDInputs) -> ActiveDPhase:
    _parent, plan_hash, raw = load_phase_plan(point, key)
    try:
        stored = validate_d_plan(
            raw, corpus=inputs.corpus, run_nonce_key=inputs.run_nonce_key)
        expected = _expected_plan(point, key, inputs)
    except Exception as exc:
        raise StageDRuntimeError(f"{key} plan cannot be re-derived") from exc
    if canonical_json(stored) != canonical_json(expected) or sha256_json(stored) != plan_hash:
        raise ImmutableViolation(f"{key} plan changed from its typed parent")
    _verify_activation_and_registry(point, stored, plan_hash)
    _require_serial_history(point, stored)
    controls = _load_exact_controls(point, stored, inputs=inputs)
    return ActiveDPhase(stored, plan_hash, controls)


def _canonical_control(raw: str, digest: str,
                       expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        normalized = StageDContextControl.model_validate(
            value, strict=True).model_dump(mode="json")
    except Exception as exc:
        raise ImmutableViolation("Stage-D context control is malformed") from exc
    if (normalized != expected or canonical_json(value) != raw
            or sha256_json(value) != digest):
        raise ImmutableViolation("Stage-D context control changed")
    return value


def _validate_control_evidence(point: Checkpoint, control: Mapping[str, Any],
                               plan: Mapping[str, Any], state: str,
                               evidence_hash: Any, evidence_raw: Any) -> None:
    attempts = point.conn.execute(
        "SELECT attempt_id,work_id,stage,invocation_ordinal,call_class,attempt_no,"
        "request_hash,state,response,metadata_json FROM attempts WHERE control_id=? "
        "ORDER BY attempt_no",
        (control["control_id"],),
    ).fetchall()
    expected_class: Optional[str] = "preflight_probe"
    allowed_states = {
        "ACCEPTED", "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN",
        "CANCELLED_UNVERIFIED", "DISPATCHING",
    }
    for index, attempt in enumerate(attempts, 1):
        if (attempt[0] != stable_attempt_id(
                f"control:{control['control_id']}", index)
                or attempt[1] is not None or attempt[2] != "D"
                or type(attempt[3]) is not int or attempt[4] != expected_class
                or not point.conn.execute(
                    "SELECT 1 FROM invocations WHERE stage='D' AND ordinal=?",
                    (attempt[3],)).fetchone()
                or attempt[5] != index or attempt[6] != control["payload_sha256"]
                or attempt[7] not in allowed_states
                or attempt[7] != "ACCEPTED" and attempt[8] is not None
                or attempt[7] == "ACCEPTED" and not isinstance(attempt[8], str)):
            raise ImmutableViolation("D context attempt identity or sequence changed")
        expected_class = ({
            "RETRYABLE_TRANSPORT": "transport_orphan",
            "ORPHANED_UNKNOWN": "transport_orphan",
            "CANCELLED_UNVERIFIED": "transport_orphan",
        }.get(str(attempt[7])))
    accepted = [attempt for attempt in attempts if attempt[7] == "ACCEPTED"]
    if state == "PENDING":
        if (evidence_hash is not None or evidence_raw is not None or accepted
                or any(attempt[7] == "DISPATCHING" for attempt in attempts[:-1])):
            raise ImmutableViolation(
                "pending D context control has contradictory evidence")
        return
    if state != "COMPLETE" or not isinstance(evidence_raw, str):
        raise ImmutableViolation("D context control state is invalid")
    try:
        value = json.loads(evidence_raw)
        normalized = validate_artifact(ContextProbeEvidence, value)
    except Exception as exc:
        raise ImmutableViolation("D context evidence is malformed") from exc
    exact = {
        "control_id": control["control_id"], "purpose": control["purpose"],
        "candidate_id": control["candidate_id"], "model": control["model"],
        "model_digest": control["model_digest"],
        "config_sha256": control["config_sha256"],
        "expected_num_ctx": control["minimum_context_length"], "state": "PASSED",
    }
    if (any(normalized[name] != expected for name, expected in exact.items())
            or canonical_json(value) != evidence_raw
            or sha256_json(value) != evidence_hash):
        raise ImmutableViolation("D context evidence differs from its control")
    first_work = next((row["work_id"] for row in plan["work"]
                       if row["candidate_id"] == control["candidate_id"]), None)
    if normalized["trigger_work_id"] != first_work:
        raise ImmutableViolation("D context evidence trigger is not first planned work")
    trigger = point.conn.execute(
        "SELECT 1 FROM attempts WHERE work_id=? AND state IN "
        "('ACCEPTED','SCHEMA_INVALID') LIMIT 1", (normalized["trigger_work_id"],),
    ).fetchone()
    if (len(accepted) != 1 or attempts[-1] != accepted[0] or not trigger
            or any(attempt[7] == "DISPATCHING" for attempt in attempts)):
        raise ImmutableViolation("D context evidence lacks accepted attempts")
    attempt = accepted[0]
    try:
        response = json.loads(str(attempt[8]))
        metadata = json.loads(str(attempt[9]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation("D context attempt evidence is malformed") from exc
    if (not isinstance(response, dict) or set(response) != {
            "purpose", "config_sha256", "model", "digest", "size",
            "size_vram", "context_length"}
            or canonical_json(response) != attempt[8]
            or sha256_json(response) != normalized["response_sha256"]
            or metadata != {"http_status": 200, "control": "ps",
                            "response_sha256": normalized["response_sha256"]}
            or response.get("purpose") != control["purpose"]
            or response.get("config_sha256") != control["config_sha256"]
            or response.get("model") != control["model"]
            or response.get("digest") != control["model_digest"]
            or any(type(response.get(name)) is not int
                   or response[name] < 0
                   for name in ("size", "size_vram", "context_length"))
            or response["context_length"] < control["minimum_context_length"]
            or response.get("context_length") != normalized["observed_context_length"]):
        raise ImmutableViolation("D context evidence differs from its response")


def _load_exact_controls(point: Checkpoint, plan: Mapping[str, Any], *,
                         inputs: StageDInputs) -> tuple[dict[str, Any], ...]:
    expected = (derive_d_context_controls(
        plan, corpus=inputs.corpus, run_nonce_key=inputs.run_nonce_key)
        if plan["phase"] in {"D3", "D4"} else [])
    rows = point.conn.execute(
        "SELECT control_id,kind,control_hash,control_json,state,evidence_hash,"
        "evidence_json,not_before_utc FROM runtime_controls WHERE plan_key=? "
        "ORDER BY rowid", (plan["plan_key"],),
    ).fetchall()
    if [row[0] for row in rows] != [row["control_id"] for row in expected]:
        raise ImmutableViolation("Stage-D context-control set or order changed")
    values: list[dict[str, Any]] = []
    for row, exact in zip(rows, expected):
        if row[1] != "context_probe" or row[7] is not None:
            raise ImmutableViolation("Stage-D context-control columns changed")
        value = _canonical_control(str(row[3]), str(row[2]), exact)
        _validate_control_evidence(
            point, value, plan, str(row[4]), row[5], row[6])
        events = point.conn.execute(
            "SELECT seq,state,evidence_hash,evidence_json,not_before_utc "
            "FROM runtime_control_events WHERE control_id=? ORDER BY seq",
            (row[0],),
        ).fetchall()
        expected_events = [(1, "PENDING", None, None, None)]
        if row[4] == "COMPLETE":
            expected_events.append((2, "COMPLETE", row[5], row[6], None))
        if events != expected_events:
            raise ImmutableViolation("Stage-D context-control history changed")
        values.append(value)
    return tuple(values)


def _verify_activation_and_registry(point: Checkpoint,
                                    plan: Mapping[str, Any],
                                    plan_hash: str) -> None:
    row = point.conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key=?", (plan["plan_key"],),
    ).fetchone()
    if not row:
        raise ImmutableViolation("Stage-D plan is not activated")
    try:
        value = json.loads(str(row[1]))
        activation = validate_artifact(PlanActivation, value)
    except Exception as exc:
        raise ImmutableViolation("Stage-D plan activation is malformed") from exc
    expected_parent, _parent = _decision_digest(
        point, PARENT_DECISION[plan["plan_key"]])
    expected = {
        "version": "c0b2-plan-activation-v1", "run_id": point.header()["run_id"],
        "budget_stage": "D", "plan_key": plan["plan_key"],
        "plan_sha256": plan_hash, "parent_decision_sha256": expected_parent,
        "state": "ACTIVATED", "activated_group_ids": [],
        "evidence_sha256": None,
    }
    if (activation != expected or canonical_json(value) != row[1]
            or sha256_json(value) != row[0]):
        raise ImmutableViolation("Stage-D plan activation changed")
    registry = point.conn.execute(
        "SELECT r.work_id,r.activation_group_id,w.stage,w.cell_id,w.request_hash "
        "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key=? ORDER BY r.rowid", (plan["plan_key"],),
    ).fetchall()
    expected_registry = [
        (item["work_id"], None, "D", item["cell_id"], item["request_sha256"])
        for item in plan["work"]
    ]
    if registry != expected_registry:
        raise ImmutableViolation("Stage-D work registry changed")


def _require_serial_history(point: Checkpoint, plan: Mapping[str, Any]) -> None:
    incomplete_seen = False
    for item in plan["work"]:
        state = point.work(item["work_id"])[0]
        attempts = point.conn.execute(
            "SELECT count(*) FROM attempts WHERE work_id=?", (item["work_id"],),
        ).fetchone()[0]
        if state in _COMPLETE_WORK:
            if incomplete_seen:
                raise ImmutableViolation("Stage-D work completion is out of plan order")
        else:
            if incomplete_seen and attempts:
                raise ImmutableViolation("Stage-D attempts are out of serial plan order")
            incomplete_seen = True


def load_active_d_phase(point: Checkpoint,
                        inputs: StageDInputs) -> ActiveDPhase:
    """Re-derive the active plan, activation, registry, and complete control set."""
    position = runtime_position(point)
    if position.active_stage != "D" or position.active_plan_key not in PHASE_KEYS:
        raise StageDRuntimeError("checkpoint is not at an active Stage-D phase")
    return _load_exact_phase_by_key(point, position.active_plan_key, inputs)


def _insert_controls(point: Checkpoint, plan: Mapping[str, Any], *,
                     inputs: StageDInputs) -> tuple[dict[str, Any], ...]:
    controls = (derive_d_context_controls(
        plan, corpus=inputs.corpus, run_nonce_key=inputs.run_nonce_key)
        if plan["phase"] in {"D3", "D4"} else [])
    for control in controls:
        raw, digest = canonical_json(control), sha256_json(control)
        point.conn.execute(
            "INSERT INTO runtime_controls VALUES(?,?,?,?,?,'PENDING',NULL,NULL,NULL,?)",
            (control["control_id"], plan["plan_key"], "context_probe",
             digest, raw, time.time()),
        )
        point.conn.execute(
            "INSERT INTO runtime_control_events VALUES(?,1,'PENDING',NULL,NULL,NULL,?)",
            (control["control_id"], time.time()),
        )
    return tuple(controls)


def activate_d_phase(point: Checkpoint, key: str,
                     inputs: StageDInputs) -> ActiveDPhase:
    """Atomically freeze one exact derived D plan, controls, registry, and cursor."""
    if key not in PHASE_KEYS:
        raise ValueError("unknown Stage-D plan key")
    plan = _expected_plan(point, key, inputs)
    with runtime_transaction(point):
        activation = freeze_activate_phase_plan(point, plan)
        existing = point.conn.execute(
            "SELECT count(*) FROM runtime_controls WHERE plan_key=?", (key,),
        ).fetchone()[0]
        if existing:
            _load_exact_controls(point, plan, inputs=inputs)
        else:
            _insert_controls(point, plan, inputs=inputs)
    active = load_active_d_phase(point, inputs)
    if active.plan_sha256 != activation.plan_sha256:
        raise ImmutableViolation("activated Stage-D plan hash changed")
    return active


def _attempt_number(point: Checkpoint, *, work_id: str | None = None,
                    control_id_value: str | None = None,
                    first_class: str = "preflight_probe") -> tuple[int, str]:
    if (work_id is None) == (control_id_value is None):
        raise ValueError("exactly one attempt identity is required")
    column, identity = (("work_id", work_id) if work_id is not None
                        else ("control_id", control_id_value))
    row = point.conn.execute(
        f"SELECT attempt_no,state FROM attempts WHERE {column}=? "
        "ORDER BY attempt_no DESC LIMIT 1", (identity,),
    ).fetchone()
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


def _candidate_control(phase: ActiveDPhase,
                       candidate_id: str) -> Optional[dict[str, Any]]:
    rows = [row for row in phase.controls if row["candidate_id"] == candidate_id]
    if len(rows) > 1:
        raise ImmutableViolation("candidate has duplicate Stage-D controls")
    return rows[0] if rows else None


def _answered(point: Checkpoint, work_id: str) -> bool:
    return bool(point.conn.execute(
        "SELECT 1 FROM attempts WHERE work_id=? AND state IN "
        "('ACCEPTED','SCHEMA_INVALID') LIMIT 1", (work_id,),
    ).fetchone())


def _pending_context_control(point: Checkpoint,
                             phase: ActiveDPhase) -> Optional[dict[str, Any]]:
    """Return the exact triggered control; reject work that crossed its barrier."""
    if not phase.controls:
        return None
    for candidate in phase.plan["candidates"]:
        candidate_id = candidate["candidate_id"]
        rows = [row for row in phase.plan["work"]
                if row["candidate_id"] == candidate_id]
        control = _candidate_control(phase, candidate_id)
        assert control is not None
        answered = [row for row in rows if _answered(point, row["work_id"])]
        record = point.conn.execute(
            "SELECT state FROM runtime_controls WHERE control_id=?",
            (control["control_id"],),
        ).fetchone()
        if not answered:
            if any(point.work(row["work_id"])[0] in _COMPLETE_WORK for row in rows):
                raise ImmutableViolation("completed candidate work lacks answered evidence")
            continue
        if answered[0]["work_id"] != rows[0]["work_id"]:
            raise ImmutableViolation("Stage-D context trigger is not first candidate work")
        if record == ("PENDING",):
            if any(point.work(row["work_id"])[0] in _COMPLETE_WORK
                   for row in rows[1:]):
                raise ImmutableViolation("Stage-D work crossed a pending context barrier")
            return control
        if record != ("COMPLETE",):
            raise ImmutableViolation("Stage-D context control state changed")
    return None


def _context_evidence(point: Checkpoint, phase: ActiveDPhase,
                      control: Mapping[str, Any], response: FakeResponse) -> dict[str, Any]:
    try:
        value = json.loads(response.content)
        expected_keys = {
            "purpose", "config_sha256", "model", "digest", "size",
            "size_vram", "context_length",
        }
        trigger = next(
            row["work_id"] for row in phase.plan["work"]
            if row["candidate_id"] == control["candidate_id"]
            and _answered(point, row["work_id"]))
        response_hash = sha256_json(value)
        if (not isinstance(value, dict) or set(value) != expected_keys
                or canonical_json(value) != response.content
                or response.metadata.get("response_sha256") != response_hash
                or value.get("purpose") != control["purpose"]
                or value.get("config_sha256") != control["config_sha256"]
                or value.get("model") != control["model"]
                or value.get("digest") != control["model_digest"]
                or type(value.get("context_length")) is not int
                or value["context_length"] < control["minimum_context_length"]):
            raise ValueError
        return validate_artifact(ContextProbeEvidence, {
            "control_id": control["control_id"], "purpose": control["purpose"],
            "candidate_id": control["candidate_id"], "model": control["model"],
            "model_digest": control["model_digest"],
            "config_sha256": control["config_sha256"],
            "expected_num_ctx": control["minimum_context_length"],
            "observed_context_length": value["context_length"],
            "trigger_work_id": trigger, "state": "PASSED",
            "response_sha256": response_hash,
        })
    except Exception as exc:
        raise ProvenanceFailure("Stage-D context response changed") from exc


def _complete_context(point: Checkpoint, control_id_value: str,
                      evidence: Mapping[str, Any]) -> None:
    raw, digest = canonical_json(dict(evidence)), sha256_json(dict(evidence))
    row = point.conn.execute(
        "SELECT state FROM runtime_controls WHERE control_id=?",
        (control_id_value,),
    ).fetchone()
    if row != ("PENDING",):
        raise CheckpointError("Stage-D context control is not pending")
    changed = point.conn.execute(
        "UPDATE runtime_controls SET state='COMPLETE',evidence_hash=?,evidence_json=?,"
        "updated=? WHERE control_id=? AND state='PENDING'",
        (digest, raw, time.time(), control_id_value),
    ).rowcount
    if changed != 1:
        raise CheckpointError("Stage-D context completion lost its claim")
    point.conn.execute(
        "INSERT INTO runtime_control_events VALUES(?,2,'COMPLETE',?,?,NULL,?)",
        (control_id_value, digest, raw, time.time()),
    )


def run_d_context_probe(executor: DurableExecutor, phase: ActiveDPhase,
                        control: Mapping[str, Any]) -> ExecutionResult:
    """Execute one D-specific context control without the Stage-F group binder."""
    executor._require_lock()
    executor._require_invocation_stage("D")
    if executor.cancellation.event.is_set():
        executor.checkpoint.cancel()
        return ExecutionResult("CANCELLED_PENDING_RESUME")
    executor._require_preflight_complete("D")
    exact = _candidate_control(phase, str(control["candidate_id"]))
    if exact != control or _pending_context_control(executor.checkpoint, phase) != control:
        raise ImmutableViolation("Stage-D context probe is not the exact pending barrier")
    model = str(control["model"])
    backoff = executor.checkpoint.backoff(model)
    if backoff.failures >= 6:
        raise CheckpointError(
            "Stage-D context trigger requires exact recovery replay")
    if backoff.retry_not_before > executor._now():
        return ExecutionResult(
            "RETRY_WAIT", retry_not_before=backoff.retry_not_before)
    attempt_no, call_class = _attempt_number(
        executor.checkpoint, control_id_value=str(control["control_id"]))
    request = ControlRequest(
        "D", str(control["control_id"]), str(control["model"]),
        str(control["payload_sha256"]), attempt_no, call_class)
    try:
        inserted = executor.checkpoint.precharge(
            attempt_id=request.attempt_id, stage="D", call_class=request.call_class,
            request_hash=request.request_hash, attempt_no=request.attempt_no,
            control_id=request.control_id,
            invocation_ordinal=executor.invocation_ordinal,
            claim_guard=executor._guard_soft_wall,
            budget_failure_callback=executor._budget_failure_callback)
    except SoftWallReached:
        executor.checkpoint.transition("PAUSED_SOFT_WALL")
        return ExecutionResult("PAUSED_SOFT_WALL")
    if not inserted:
        state = executor.checkpoint.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?", (request.attempt_id,),
        ).fetchone()[0]
        return ExecutionResult(f"ALREADY_{state}", request.attempt_id)
    executor.current_attempt = request.attempt_id
    try:
        response = executor.transport(request, executor.cancellation.event)
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        executor._require_returned_response(response, {"ACCEPTED"})
        evidence = _context_evidence(executor.checkpoint, phase, control, response)
        executor.checkpoint.finish_attempt(
            request.attempt_id, outcome="ACCEPTED", response=response.content,
            metadata=response.metadata, accept_work=False,
            before_commit=lambda: _complete_context(
                executor.checkpoint, request.control_id, evidence))
        executor._reset_resource(request.model)
        return ExecutionResult("ACCEPTED", request.attempt_id)
    except RetryableTransport:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return executor._finish_retryable_control(request)
    except SafetyLimit:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        from .c0b2_runtime import finish_public_failure_attempt
        finish_public_failure_attempt(
            executor.checkpoint, attempt_id=request.attempt_id,
            terminal="FAILED_SAFETY")
        return ExecutionResult("FAILED_SAFETY", request.attempt_id)
    except ProvenanceFailure:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        from .c0b2_runtime import finish_public_failure_attempt
        finish_public_failure_attempt(
            executor.checkpoint, attempt_id=request.attempt_id,
            terminal="BLOCKED_PROVENANCE")
        return ExecutionResult("BLOCKED_PROVENANCE", request.attempt_id)
    finally:
        executor.current_attempt = None


def _preflight_specs(header: Mapping[str, Any],
                     phase: ActiveDPhase) -> list[tuple[str, str, RequestSpec]]:
    models = []
    for row in phase.plan["candidates"]:
        if row["model"] not in models:
            models.append(row["model"])
    return [
        ("version", SERVER_CONTROL_MODEL,
         RequestSpec(kind="version", expected_version=header["ollama_version"])),
        ("tags", SERVER_CONTROL_MODEL,
         RequestSpec(kind="tags", expected_models=header["model_digests"])),
        *(("show", model, RequestSpec(
            kind="show", expected_model=model,
            expected_digest=header["model_digests"][model])) for model in models),
    ]


def _resource_probe_spec(point: Checkpoint, model: str) -> RequestSpec:
    """Resolve the frozen Stage-C V2/pos_pii_001 payload required by §8."""
    from .c0b2_stage_c import load_c44, resolve_work

    _parent, _digest, raw = point.load_plan("C")
    plan = json.loads(raw)
    matches = [row for row in plan["work"]
               if row.get("model") == model and row.get("worksheet") == "v2"
               and row.get("doc_id") == "pos_pii_001"]
    if len(matches) != 1:
        raise ImmutableViolation("frozen Stage-D resource probe source is missing")
    resolved = resolve_work(plan, matches[0]["work_id"], corpus=load_c44(plan))
    return RequestSpec(
        kind="chat", payload=resolved.payload, worksheet="v2",
        expected_model=model, expected_digest=resolved.item.model_digest)


def _trigger_resource_probe_spec(
        phase: ActiveDPhase, control: Mapping[str, Any], inputs: StageDInputs,
) -> RequestSpec:
    trigger = next((row for row in phase.plan["work"]
                    if row["candidate_id"] == control["candidate_id"]), None)
    if trigger is None:
        raise ImmutableViolation("D context recovery lacks its trigger work")
    return request_spec_for_d_work(
        phase.plan, trigger["work_id"], corpus=inputs.corpus,
        run_nonce_key=inputs.run_nonce_key)


def _validate_d_control_history(
        point: Checkpoint, inputs: StageDInputs,
) -> None:
    """Re-derive every charged D control before contact or a success receipt."""
    _stage_c_boundary_parent(point, verify_snapshot=True)
    header = point.header()
    plan_rows = point.conn.execute(
        "SELECT p.plan_key,p.plan_hash,p.plan_json,a.created FROM phase_plans p "
        "JOIN plan_activations a ON a.plan_key=p.plan_key "
        "WHERE p.budget_stage='D' ORDER BY a.created",
    ).fetchall()
    phases: list[tuple[float, ActiveDPhase]] = []
    for key, _digest, _raw, created in plan_rows:
        # A typed, hash-consistent row is not sufficient ownership evidence: a
        # fabricated future phase could otherwise lend controls to old calls.
        phase = _load_exact_phase_by_key(point, str(key), inputs)
        phases.append((float(created), phase))
    if ([phase.plan["plan_key"] for _created, phase in phases]
            != list(PHASE_KEYS[:len(phases)])):
        raise ImmutableViolation("historical D activation order changed")

    def phase_at(when: float) -> ActiveDPhase:
        active = [phase for activated, phase in phases if activated <= when]
        if not active:
            raise ImmutableViolation("D control precedes its active plan")
        return active[-1]

    invocations = [(int(row[0]), float(row[1])) for row in point.conn.execute(
        "SELECT ordinal,created FROM invocations WHERE stage='D' ORDER BY ordinal")]
    if ([row[0] for row in invocations] != list(range(1, len(invocations) + 1))
            or any(left[1] >= right[1]
                   for left, right in zip(invocations, invocations[1:]))):
        raise ImmutableViolation("D invocation chronology changed")
    windows = {
        ordinal: (created, invocations[index + 1][1]
                  if index + 1 < len(invocations) else None)
        for index, (ordinal, created) in enumerate(invocations)
    }
    generic_hashes = {
        model: request_spec_hash(_resource_probe_spec(point, model))
        for model in header["model_digests"]
    }

    def exact_for(
            identity: str, ordinal: int, attempt_created: float,
    ) -> tuple[str, str, set[str], str, Optional[int]]:
        if ordinal not in windows:
            raise ImmutableViolation("D control lacks invocation ownership")
        invoked, next_invoked = windows[ordinal]
        if (attempt_created < invoked
                or next_invoked is not None and attempt_created >= next_invoked):
            raise ImmutableViolation("D control falls outside its invocation window")
        active = phase_at(attempt_created)
        started = phase_at(invoked)
        for kind, model, spec in _preflight_specs(header, started):
            if identity == control_id("D", ordinal, kind, model):
                if active.plan["plan_key"] != started.plan["plan_key"]:
                    raise ImmutableViolation("D preflight was backdated across a phase")
                return (request_spec_hash(spec), "preflight_probe", {"ACCEPTED"},
                        started.plan["plan_key"], ordinal)
        for control in active.controls:
            if identity == control["control_id"]:
                return (control["payload_sha256"], "preflight_probe", {"ACCEPTED"},
                        active.plan["plan_key"], None)
        models = {row["model"] for row in active.plan["candidates"]}
        recovery = [(model, generic_hashes[model]) for model in models]
        recovery.extend((
            control["model"], request_spec_hash(
                _trigger_resource_probe_spec(active, control, inputs)))
            for control in active.controls)
        for model, request_hash in recovery:
            if identity == resource_probe_id("D", ordinal, model, request_hash):
                return (request_hash, "transport_orphan",
                        {"ACCEPTED", "SCHEMA_INVALID"},
                        active.plan["plan_key"], ordinal)
        raise ImmutableViolation("D control charge has no phase-owned request")

    known_ids = {control["control_id"] for _created, phase in phases
                 for control in phase.controls}
    for ordinal, invoked in invocations:
        started = phase_at(invoked)
        known_ids.update(control_id("D", ordinal, kind, model)
                         for kind, model, _spec in _preflight_specs(header, started))
        possible = [phase for activated, phase in phases
                    if activated <= (windows[ordinal][1] or float("inf"))]
        for phase in possible:
            for model in {row["model"] for row in phase.plan["candidates"]}:
                digest = generic_hashes[model]
                known_ids.add(resource_probe_id("D", ordinal, model, digest))
            for control in phase.controls:
                digest = request_spec_hash(
                    _trigger_resource_probe_spec(phase, control, inputs))
                known_ids.add(resource_probe_id(
                    "D", ordinal, control["model"], digest))
    for control_value, attempts in _group_d_control_attempts(point, known_ids):
        group_exact: tuple[str, str, set[str], str, Optional[int]] | None = None
        expected_class: Optional[str] = None
        for index, attempt in enumerate(attempts, 1):
            ordinal = attempt[3]
            created = attempt[10]
            if (type(ordinal) is not int or type(created) not in {int, float}):
                raise ImmutableViolation("D control lacks invocation ownership")
            exact = exact_for(control_value, ordinal, float(created))
            if group_exact is None:
                group_exact = exact
            elif exact != group_exact:
                raise ImmutableViolation("D control changed phase ownership")
            if index == 1:
                expected_class = exact[1]
            state = str(attempt[7])
            allowed = exact[2] | {
                "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN", "CANCELLED_UNVERIFIED"}
            answered = state in {"ACCEPTED", "SCHEMA_INVALID"}
            if (attempt[0] != stable_attempt_id(
                    f"control:{control_value}", index)
                    or attempt[1] is not None or attempt[2] != "D"
                    or attempt[4] != expected_class or attempt[5] != index
                    or attempt[6] != exact[0] or state not in allowed
                    or answered != isinstance(attempt[8], str)):
                raise ImmutableViolation(
                    "D control charge identity or sequence changed")
            expected_class = ({
                "RETRYABLE_TRANSPORT": "transport_orphan",
                "ORPHANED_UNKNOWN": "transport_orphan",
                "CANCELLED_UNVERIFIED": "transport_orphan",
            }.get(state))


def _group_d_control_attempts(
        point: Checkpoint, known: set[str],
) -> list[tuple[str, list[tuple[Any, ...]]]]:
    grouped: dict[str, list[tuple[Any, ...]]] = {}
    for row in point.conn.execute(
            "SELECT attempt_id,work_id,stage,invocation_ordinal,call_class,attempt_no,"
            "request_hash,state,response,metadata_json,created,control_id FROM attempts "
            "WHERE control_id IS NOT NULL ORDER BY control_id,attempt_no"):
        identity = str(row[11])
        if row[2] == "D" or identity in known:
            grouped.setdefault(identity, []).append(tuple(row[:11]))
    return list(grouped.items())


def _work_attempt_evidence(
        point: Checkpoint, item: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Bind one work row to exact Stage-D invocation-owned attempt evidence."""
    from .c0b2_stage_d import AttemptEvidence

    work = point.conn.execute(
        "SELECT state,accepted_attempt_id FROM work_items WHERE work_id=?",
        (item["work_id"],),
    ).fetchone()
    if not work:
        raise ImmutableViolation("Stage-D evidence lacks its work row")
    rows: list[dict[str, Any]] = []
    expected_class: Optional[str] = "scored"
    for index, attempt in enumerate(point.conn.execute(
            "SELECT attempt_id,attempt_no,call_class,state,response,metadata_json,"
            "request_hash,stage,invocation_ordinal "
            "FROM attempts WHERE work_id=? ORDER BY attempt_no",
            (item["work_id"],)), 1):
        if (attempt[0] != stable_attempt_id(item["work_id"], index)
                or attempt[1] != index or attempt[2] != expected_class
                or attempt[6] != item["request_sha256"]
                or attempt[7] != "D" or type(attempt[8]) is not int
                or not point.conn.execute(
                    "SELECT 1 FROM invocations WHERE stage='D' AND ordinal=?",
                    (attempt[8],),
                ).fetchone()):
            raise ImmutableViolation(
                "Stage-D attempt identity, sequence, or ownership changed")
        try:
            metadata = json.loads(attempt[5]) if attempt[5] is not None else {}
        except (TypeError, json.JSONDecodeError) as exc:
            raise ImmutableViolation("Stage-D attempt metadata is not JSON") from exc
        answered = str(attempt[3]) in _ANSWERED
        raw = {
            "attempt_id": str(attempt[0]), "work_id": item["work_id"],
            "attempt_no": attempt[1], "call_class": str(attempt[2]),
            "request_sha256": str(attempt[6]), "state": str(attempt[3]),
            "response": attempt[4],
            "done_reason": metadata.get("done_reason") if answered else None,
            "prompt_eval_count": (metadata.get("prompt_eval_count")
                                  if answered else None),
            "tools_empty": metadata.get("tools_empty") if answered else None,
            "images_empty": metadata.get("images_empty") if answered else None,
            "unknown_message_fields_empty": (
                metadata.get("unknown_message_fields_empty") if answered else None),
        }
        try:
            rows.append(AttemptEvidence.model_validate(
                raw, strict=True).model_dump(mode="json"))
        except Exception as exc:
            raise ImmutableViolation(
                "Stage-D attempt evidence is incomplete or mistyped") from exc
        expected_class = {
            "SCHEMA_INVALID": "schema_retry",
            "RETRYABLE_TRANSPORT": "transport_orphan",
            "ORPHANED_UNKNOWN": "transport_orphan",
            "CANCELLED_UNVERIFIED": "transport_orphan",
        }.get(str(attempt[3]))
    accepted = [row["attempt_id"] for row in rows if row["state"] == "ACCEPTED"]
    invalid = sum(row["state"] == "SCHEMA_INVALID" for row in rows)
    state, accepted_id = str(work[0]), work[1]
    consistent = (
        state == "SUCCEEDED" and type(accepted_id) is str
        and accepted == [accepted_id] and invalid <= 1
        or state == "COMPLETED_INVALID" and accepted_id is None
        and not accepted and invalid == 2
        or state == "PENDING" and accepted_id is None
        and not accepted and invalid < 2
    )
    if not consistent:
        raise ImmutableViolation(
            "Stage-D work state differs from its authoritative attempts")
    return rows


def _phase_evidence(point: Checkpoint,
                    phase: ActiveDPhase) -> dict[str, list[dict[str, Any]]]:
    """Extract strict charged attempt evidence; the pure scorer revalidates answers."""
    return {item["work_id"]: _work_attempt_evidence(point, item)
            for item in phase.plan["work"]}


def _context_evidence_by_candidate(point: Checkpoint,
                                   phase: ActiveDPhase) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for control in phase.controls:
        row = point.conn.execute(
            "SELECT state,evidence_json FROM runtime_controls WHERE control_id=?",
            (control["control_id"],),
        ).fetchone()
        if row[0] != "COMPLETE" or not isinstance(row[1], str):
            raise CheckpointError("Stage-D finalization requires every context probe")
        values[control["candidate_id"]] = json.loads(row[1])
    return values


def _all_work_complete(point: Checkpoint, phase: ActiveDPhase) -> bool:
    return all(point.work(row["work_id"])[0] in _COMPLETE_WORK
               for row in phase.plan["work"])


def _scored_calls(point: Checkpoint, key: str) -> int:
    return int(point.conn.execute(
        "SELECT count(*) FROM attempts a JOIN phase_work_registry r "
        "ON r.work_id=a.work_id WHERE r.plan_key=? AND a.call_class='scored'", (key,),
    ).fetchone()[0])


def validate_phase_call_cap(point: Checkpoint, phase: ActiveDPhase) -> None:
    used = _scored_calls(point, phase.plan["plan_key"])
    per_work = dict(point.conn.execute(
        "SELECT r.work_id,count(a.attempt_id) FROM phase_work_registry r "
        "LEFT JOIN attempts a ON a.work_id=r.work_id AND a.call_class='scored' "
        "WHERE r.plan_key=? GROUP BY r.work_id",
        (phase.plan["plan_key"],),
    ))
    expected = {row["work_id"] for row in phase.plan["work"]}
    complete = _all_work_complete(point, phase)
    if (set(per_work) != expected or any(count > 1 for count in per_work.values())
            or complete and any(count != 1 for count in per_work.values())
            or used > PHASE_CALL_CAPS[phase.plan["plan_key"]]):
        raise ImmutableViolation("Stage-D phase exceeded its exact scored-call maximum")


def build_phase_outcome(point: Checkpoint, phase: ActiveDPhase,
                        inputs: StageDInputs) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build pure aggregate/decision only after complete durable evidence."""
    from .c0b2_stage_d import (
        build_d4_final_decision, build_stage_d_aggregate,
        build_stage_d_decision,
    )

    if not _all_work_complete(point, phase) or _pending_context_control(point, phase):
        raise CheckpointError("Stage-D phase evidence is incomplete")
    validate_phase_call_cap(point, phase)
    aggregate = build_stage_d_aggregate(
        phase.plan, _phase_evidence(point, phase),
        corpus=inputs.corpus,
        context_controls=phase.controls,
        context_probes=tuple(_context_evidence_by_candidate(
            point, phase).values()),
    )
    if phase.plan["phase"] == "D4":
        d3 = _load_exact_phase_by_key(point, "D3_CONTEXT", inputs)
        d3_row = point.conn.execute(
            "SELECT aggregate_json FROM phase_aggregates "
            "WHERE plan_key='D3_CONTEXT'",
        ).fetchone()
        if not d3_row:
            raise ImmutableViolation("D4 merge lacks its D3 aggregate")
        decision = build_d4_final_decision(
            aggregate, phase.plan,
            d3_aggregate=json.loads(str(d3_row[0])), d3_plan=d3.plan)
    else:
        decision = build_stage_d_decision(aggregate, phase.plan)
    return aggregate, decision


def _decision_id(phase: str, decision: Mapping[str, Any]) -> str:
    if phase == "D3" and decision.get("outcome") == "FINALISTS":
        return "stage-d-selection"
    if phase == "D4":
        return "stage-d-selection"
    return {
        "D1": "stage-d-d1-selection", "D2": "stage-d-d2-selection",
        "D3": "stage-d-d3-selection",
    }[phase]


def _freeze_phase_aggregate(point: Checkpoint, phase: ActiveDPhase,
                            aggregate: Mapping[str, Any]) -> str:
    from .c0b2_stage_d import validate_stage_d_aggregate

    normalized = validate_stage_d_aggregate(aggregate)
    if (normalized["phase"] != phase.plan["phase"]
            or normalized["plan_sha256"] != phase.plan_sha256):
        raise ImmutableViolation("Stage-D aggregate plan hash changed")
    raw, digest = canonical_json(normalized), sha256_json(normalized)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (phase.plan["plan_key"],),
    ).fetchone()
    frozen = (phase.plan_sha256, digest, raw)
    if row and row != frozen:
        raise ImmutableViolation("Stage-D phase aggregate already changed")
    if not row:
        point.conn.execute(
            "INSERT INTO phase_aggregates VALUES(?,?,?,?,?)",
            (phase.plan["plan_key"], *frozen, time.time()),
        )
    return digest


def _freeze_decision_row(point: Checkpoint, decision_id: str,
                         phase: ActiveDPhase, aggregate_hash: str,
                         decision: Mapping[str, Any], activation: str) -> str:
    raw = canonical_json(dict(decision))
    frozen = ("D", phase.plan_sha256, aggregate_hash, activation, raw)
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    if row and row != frozen:
        raise ImmutableViolation(f"Stage-D decision {decision_id} already changed")
    if not row:
        point.conn.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
            (decision_id, *frozen, time.time()),
        )
    return sha256_json((decision_id, *frozen))


def _finalize_inconclusive(point: Checkpoint, phase: ActiveDPhase,
                           aggregate_hash: str, decision: Mapping[str, Any]) -> None:
    from .c0b2_runtime import freeze_public_artifact

    reason = str(decision["reason"])
    artifact = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": aggregate_hash, "reason": reason,
    }
    artifact_hash = freeze_public_artifact(point, "stage-d-result", artifact)
    _freeze_decision_row(
        point, _decision_id(phase.plan["phase"], decision), phase,
        aggregate_hash, decision, "NOT_ACTIVATED")
    completion = {
        "outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
        "facts": {"deterministic_stop": True, "reason": reason},
    }
    _freeze_decision_row(
        point, "c0b2-completion", phase, aggregate_hash,
        completion, "NOT_ACTIVATED")
    point.conn.execute(
        "UPDATE run_state SET state='INCONCLUSIVE',updated=? WHERE id=1",
        (time.time(),),
    )


def _require_no_d4_branch(point: Checkpoint) -> None:
    counts = [point.conn.execute(query).fetchone()[0] for query in (
        "SELECT count(*) FROM phase_plans WHERE plan_key='D4_CONFIRMATION'",
        "SELECT count(*) FROM plan_activations WHERE plan_key='D4_CONFIRMATION'",
        "SELECT count(*) FROM phase_aggregates WHERE plan_key='D4_CONFIRMATION'",
        "SELECT count(*) FROM phase_work_registry WHERE plan_key='D4_CONFIRMATION'",
        "SELECT count(*) FROM runtime_controls WHERE plan_key='D4_CONFIRMATION'",
        "SELECT count(*) FROM decisions WHERE decision_id='stage-d-d3-selection'",
    )]
    if any(counts):
        raise ImmutableViolation("D3 all-reuse finalization cannot coexist with D4")


def finalize_d_phase(point: Checkpoint, phase: ActiveDPhase,
                     inputs: StageDInputs) -> StageDRunResult:
    """Atomically persist evidence and either activate the successor or stop."""
    aggregate, decision = build_phase_outcome(point, phase, inputs)
    normalized = _validated_d_decision(decision, phase.plan["phase"])
    with runtime_transaction(point):
        aggregate_hash = _freeze_phase_aggregate(point, phase, aggregate)
        if normalized["aggregate_sha256"] != aggregate_hash:
            raise ImmutableViolation("Stage-D decision aggregate hash changed")
        if normalized["outcome"] == "INCONCLUSIVE":
            _finalize_inconclusive(
                point, phase, aggregate_hash, normalized)
            return StageDRunResult(
                point.state(), phase.plan["plan_key"], "INCONCLUSIVE")
        decision_id = _decision_id(phase.plan["phase"], normalized)
        if (phase.plan["phase"] == "D3"
                and normalized["outcome"] == "FINALISTS"):
            _require_no_d4_branch(point)
        _freeze_decision_row(
            point, decision_id, phase, aggregate_hash, normalized, "ACTIVATED")
        if decision_id == "stage-d-selection":
            point.conn.execute(
                "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY',updated=? WHERE id=1",
                (time.time(),),
            )
            return StageDRunResult(
                "PAUSED_STAGE_BOUNDARY", phase.plan["plan_key"], "FINALISTS")
        successor = NEXT_KEY[phase.plan["plan_key"]]
        activate_d_phase(point, successor, inputs)
    return StageDRunResult(point.state(), successor, "CONTINUE")


def run_stage_d_invocation(
        point: Checkpoint, lock: GlobalExecutionLock, inputs: StageDInputs, *,
        transport_factory: Callable[[Callable[[Any], RequestSpec], Mapping[str, Any]], Any],
        cancellation: CancellationController | None = None,
) -> StageDRunResult:
    """Run/resume all reachable D phases serially using one bounded invocation."""
    phase = load_active_d_phase(point, inputs)
    header = point.header()
    specs: dict[str, RequestSpec] = {}

    def resolver(request: Any) -> RequestSpec:
        if isinstance(request, WorkRequest):
            active = load_active_d_phase(point, inputs)
            return request_spec_for_d_work(
                active.plan, request.work_id, corpus=inputs.corpus,
                run_nonce_key=inputs.run_nonce_key)
        spec = specs.get(request.control_id)
        if spec is None:
            raise StageDRuntimeError("transport requested an unknown D control")
        return spec

    transport = transport_factory(resolver, header)
    executor = DurableExecutor(
        point, lock, transport, cancellation=cancellation or CancellationController())
    _orphans, ordinal = executor.recover_and_start("D")
    # Recovery first closes any DISPATCHING crash window.  The resulting durable
    # evidence must then be exact before this invocation makes a live request.
    _validate_d_control_history(point, inputs)
    for item in phase.plan["work"]:
        _work_attempt_evidence(point, item)

    def stopped(result: ExecutionResult) -> Optional[StageDRunResult]:
        if result.outcome == "RETRY_WAIT":
            if executor.interruptible_backoff(result.retry_not_before):
                return None
            return StageDRunResult(
                point.state(), runtime_position(point).active_plan_key,
                point.state(), result.retry_not_before)
        if result.outcome in {"ACCEPTED", "SCHEMA_INVALID"}:
            return None
        return StageDRunResult(
            point.state(), runtime_position(point).active_plan_key,
            result.outcome, result.retry_not_before)

    # One exact preflight set belongs to this invocation.  Every later phase load is
    # re-derived before dispatch, but no uncharged adaptive preflight is invented.
    for kind, model, spec in _preflight_specs(header, phase):
        identity = control_id("D", ordinal, kind, model)
        specs[identity] = spec
        while True:
            attempt_no, call_class = _attempt_number(
                point, control_id_value=identity)
            request = ControlRequest(
                "D", identity, model, request_spec_hash(spec), attempt_no, call_class)
            result = executor.run_control(request, kind=kind)
            stop = stopped(result)
            if result.outcome != "RETRY_WAIT" or stop is not None:
                break
        if stop is not None:
            return stop

    def drain_resource(
            context: Mapping[str, Any] | None = None,
    ) -> Optional[StageDRunResult]:
        obligation = (point.backoff(str(context["model"]))
                      if context is not None else executor._resource_obligation())
        if context is not None and obligation.failures < 6:
            return None
        if obligation is None:
            return None
        spec = (_trigger_resource_probe_spec(phase, context, inputs)
                if context is not None else
                _resource_probe_spec(point, obligation.model))
        request_hash = request_spec_hash(spec)
        identity = resource_probe_id(
            "D", ordinal, obligation.model, request_hash)
        specs[identity] = spec
        while True:
            attempt_no, call_class = _attempt_number(
                point, control_id_value=identity, first_class="transport_orphan")
            request = ControlRequest(
                "D", identity, obligation.model, request_hash,
                attempt_no, call_class)
            result = executor.run_resource_probe(
                request, prioritized_obligation_model=(
                    str(context["model"]) if context is not None else None))
            stop = stopped(result)
            if result.outcome != "RETRY_WAIT" or stop is not None:
                return stop

    while True:
        phase = load_active_d_phase(point, inputs)
        validate_phase_call_cap(point, phase)
        control = _pending_context_control(point, phase)
        if control is not None:
            resource_stop = drain_resource(control)
            if resource_stop is not None:
                return resource_stop
            spec = RequestSpec(
                kind="ps", expected_model=control["model"],
                expected_digest=control["model_digest"],
                min_context=control["minimum_context_length"],
                purpose=control["purpose"], config_sha256=control["config_sha256"])
            if request_spec_hash(spec) != control["payload_sha256"]:
                raise ImmutableViolation("Stage-D context request spec changed")
            specs[control["control_id"]] = spec
            result = run_d_context_probe(executor, phase, control)
            stop = stopped(result)
            if stop is not None:
                return stop
            continue
        resource_stop = drain_resource()
        if resource_stop is not None:
            return resource_stop
        pending = next((row for row in phase.plan["work"]
                        if point.work(row["work_id"])[0] not in _COMPLETE_WORK), None)
        if pending is None:
            result = finalize_d_phase(point, phase, inputs)
            if result.outcome != "CONTINUE":
                return result
            continue
        # The first-answer barrier is checked immediately before every precharge.
        if phase.controls:
            candidate_rows = [row for row in phase.plan["work"]
                              if row["candidate_id"] == pending["candidate_id"]]
            if not any(_answered(point, row["work_id"]) for row in candidate_rows):
                if pending["work_id"] != candidate_rows[0]["work_id"]:
                    raise ImmutableViolation(
                        "only first candidate work may precede its context probe")
        attempt_no, call_class = _attempt_number(point, work_id=pending["work_id"])
        result = executor.run(WorkRequest(
            "D", pending["work_id"], pending["model"],
            pending["request_sha256"], attempt_no, call_class))
        if result.outcome in _ANSWERED:
            # The executor has durably committed the response.  Validate the exact
            # usage/provenance shape now so a bad answer cannot permit another call.
            _work_attempt_evidence(point, pending)
        stop = stopped(result)
        if stop is not None:
            return stop


def start_stage_d(point: Checkpoint, inputs: StageDInputs) -> ActiveDPhase:
    """Cross the reviewed C boundary by activating only the exact D1 plan."""
    position = runtime_position(point)
    if (point.state(), position.active_stage, position.active_plan_key) != (
            "PAUSED_STAGE_BOUNDARY", "C", "C"):
        raise CheckpointError("Stage-D start requires the exact Stage-C boundary")
    return activate_d_phase(point, "D1_OUTPUT", inputs)


def validate_final_d_boundary(
        point: Checkpoint, inputs: StageDInputs,
) -> tuple[str, dict[str, Any]]:
    """Rebuild and validate the exact activated final D boundary for Stage F."""
    from .c0b2_stage_d import validate_final_stage_d_decision

    position = runtime_position(point)
    if (point.state() != "PAUSED_STAGE_BOUNDARY" or position.active_stage != "D"
            or position.active_plan_key not in {"D3_CONTEXT", "D4_CONFIRMATION"}):
        raise StageDRuntimeError("final D validation requires the exact D boundary")
    owner = _load_exact_phase_by_key(point, position.active_plan_key, inputs)
    rebuilt_aggregate, expected = build_phase_outcome(point, owner, inputs)
    row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (position.active_plan_key,),
    ).fetchone()
    if (not row or row != (owner.plan_sha256, sha256_json(rebuilt_aggregate),
                           canonical_json(rebuilt_aggregate))
            or expected.get("outcome") != "FINALISTS"):
        raise ImmutableViolation("final D aggregate changed from durable evidence")
    digest, stored = _decision_digest(point, "stage-d-selection")
    decision_owner = point.conn.execute(
        "SELECT parent_hash,aggregate_hash FROM decisions "
        "WHERE decision_id='stage-d-selection'",
    ).fetchone()
    if (decision_owner != (owner.plan_sha256, row[1])
            or canonical_json(stored) != canonical_json(expected)):
        raise ImmutableViolation("final D decision changed from its exact owner")
    if position.active_plan_key == "D3_CONTEXT":
        _require_no_d4_branch(point)
        normalized = validate_final_stage_d_decision(
            stored, owner_plan=owner.plan, owner_aggregate=rebuilt_aggregate)
    else:
        _load_owned_intermediate(
            point, "D3_CONTEXT", "stage-d-d3-selection", inputs)
        d3 = _load_exact_phase_by_key(point, "D3_CONTEXT", inputs)
        d3_row = point.conn.execute(
            "SELECT aggregate_json FROM phase_aggregates WHERE plan_key='D3_CONTEXT'",
        ).fetchone()
        if not d3_row:
            raise ImmutableViolation("D4 final decision lacks D3 evidence")
        normalized = validate_final_stage_d_decision(
            stored, owner_plan=owner.plan, owner_aggregate=rebuilt_aggregate,
            d3_plan=d3.plan, d3_aggregate=json.loads(str(d3_row[0])))
    _validate_d_control_history(point, inputs)
    return digest, normalized


def validate_inconclusive_d_terminal(
        point: Checkpoint, inputs: StageDInputs,
) -> tuple[str, dict[str, Any]]:
    """Rebuild the active D zero-result and bind every terminal artifact row."""
    from .c0b2_runtime import load_public_artifact

    position = runtime_position(point)
    if (point.state() != "INCONCLUSIVE" or position.active_stage != "D"
            or position.active_plan_key not in PHASE_KEYS):
        raise StageDRuntimeError(
            "D inconclusive validation requires its exact terminal cursor")
    owner = _load_exact_phase_by_key(
        point, position.active_plan_key, inputs)
    rebuilt, expected = build_phase_outcome(point, owner, inputs)
    expected = _validated_d_decision(expected, owner.plan["phase"])
    if expected["outcome"] != "INCONCLUSIVE" or expected["selections"]:
        raise ImmutableViolation(
            "D inconclusive terminal is not a rebuilt zero-result decision")
    aggregate_hash = sha256_json(rebuilt)
    aggregate_row = point.conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key=?", (position.active_plan_key,),
    ).fetchone()
    if aggregate_row != (
            owner.plan_sha256, aggregate_hash, canonical_json(rebuilt)):
        raise ImmutableViolation(
            "D inconclusive aggregate changed from durable evidence")
    decision_id = _decision_id(owner.plan["phase"], expected)
    decision_row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    if decision_row != (
            "D", owner.plan_sha256, aggregate_hash, "NOT_ACTIVATED",
            canonical_json(expected)):
        raise ImmutableViolation(
            "D inconclusive decision changed from its active aggregate")
    artifact_hash, artifact = load_public_artifact(point, "stage-d-result")
    expected_artifact = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": aggregate_hash, "reason": expected["reason"],
    }
    if artifact != expected_artifact:
        raise ImmutableViolation(
            "D inconclusive result differs from its zero-result decision")
    completion = {
        "outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
        "facts": {"deterministic_stop": True, "reason": expected["reason"]},
    }
    completion_row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id='c0b2-completion'",
    ).fetchone()
    if completion_row != (
            "D", owner.plan_sha256, aggregate_hash, "NOT_ACTIVATED",
            canonical_json(completion)):
        raise ImmutableViolation(
            "D inconclusive completion changed from its public result")
    _validate_d_control_history(point, inputs)
    return artifact_hash, expected


def _public_result(point: Checkpoint, run_id: str, outcome: str = "STATUS",
                   retry_not_before: float = 0.0) -> dict[str, Any]:
    value: dict[str, Any] = {
        "run_id": run_id, "stage": "D", "state": point.state(),
        "active_plan_key": runtime_position(point).active_plan_key,
        "outcome": outcome, "calls_total": point.usage()["total"],
    }
    if retry_not_before > 0:
        value["retry_not_before"] = int(retry_not_before)
    return value


def run_public_stage_d(
        run_id: str, *, resume: bool = False,
        benchmark_root: Path | None = None,
        transport_factory: Callable[
            [Callable[[Any], RequestSpec], Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Gate, run, and receipt one live Stage-D command under the global lock."""
    from .c0b2_runtime import (
        _LiveSignalGuard, _checkpoint_path, ensure_backup_receipt,
        finish_public_run_failure, revalidate_source_pins,
    )
    from .c0b2_transport import BoundedOllamaTransport

    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    path = _checkpoint_path(run_id, root)
    with GlobalExecutionLock(root) as lock:
        point = Checkpoint.open(path, root)
        try:
            position = runtime_position(point)
            state = point.state()
            if state in TERMINAL_STATES:
                if position.active_stage != "D":
                    raise StageDRuntimeError(
                        "terminal re-entry stage differs from command stage D")
                if state == "BLOCKED_PROVENANCE":
                    # Exact frozen provenance failures remain receiptable even when
                    # the source seal or nonce key is the fact that drifted.
                    ensure_backup_receipt(point, lock)
                    return _public_result(point, run_id)
                revalidate_source_pins(point.header())
                terminal_inputs = load_stage_d_inputs(point)
                if state == "INCONCLUSIVE":
                    validate_inconclusive_d_terminal(point, terminal_inputs)
                ensure_backup_receipt(point, lock)
                return _public_result(point, run_id)
            if state == "PAUSED_STAGE_BOUNDARY" and position.active_stage == "D":
                try:
                    revalidate_source_pins(point.header())
                    boundary_inputs = load_stage_d_inputs(point)
                    validate_final_d_boundary(point, boundary_inputs)
                except Exception:
                    finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                    raise
                ensure_backup_receipt(point, lock)
                return _public_result(point, run_id)
            if position.active_stage == "C":
                if resume or state != "PAUSED_STAGE_BOUNDARY" or position.active_plan_key != "C":
                    raise StageDRuntimeError(
                        "run D requires the exact receipted Stage-C boundary")
            elif position.active_stage == "D":
                if not resume:
                    raise StageDRuntimeError("an existing Stage-D run requires resume")
                if state not in RESUMABLE_STATES or state in {
                        "PREPARED", "PAUSED_STAGE_BOUNDARY"}:
                    raise StageDRuntimeError("Stage-D state is not ordinarily resumable")
            else:
                raise StageDRuntimeError("Stage-D command cannot run from this cursor")

            try:
                revalidate_source_pins(point.header())
                inputs = load_stage_d_inputs(point)
                if position.active_stage == "D":
                    load_active_d_phase(point, inputs)
            except Exception:
                if point.state() not in TERMINAL_STATES:
                    finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                raise
            if position.active_stage == "C":
                ensure_backup_receipt(point, lock)
                try:
                    start_stage_d(point, inputs)
                except Exception:
                    finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                    raise

            cancellation = CancellationController()
            guard: Any = None

            def guarded_factory(resolver: Callable[[Any], RequestSpec],
                                header: Mapping[str, Any]) -> Any:
                nonlocal guard
                transport = (transport_factory(resolver, header)
                             if transport_factory is not None else
                             BoundedOllamaTransport(
                                 resolver, endpoint=header["ollama_endpoint"]))
                cancel_current = getattr(transport, "cancel_current", lambda: None)
                guard = _LiveSignalGuard(cancellation, cancel_current)
                guard.__enter__()
                return transport

            try:
                result = run_stage_d_invocation(
                    point, lock, inputs, transport_factory=guarded_factory,
                    cancellation=cancellation)
            except (ImmutableViolation, StageDRuntimeError, StageDPlanError, StageDError):
                if point.state() not in TERMINAL_STATES:
                    finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
                    ensure_backup_receipt(point, lock)
                raise
            finally:
                if guard is not None:
                    guard.__exit__(None, None, None)
            if point.state() in TERMINAL_STATES | {"PAUSED_STAGE_BOUNDARY"}:
                if point.state() == "PAUSED_STAGE_BOUNDARY":
                    validate_final_d_boundary(point, inputs)
                elif point.state() == "INCONCLUSIVE":
                    validate_inconclusive_d_terminal(point, inputs)
                ensure_backup_receipt(point, lock)
            return _public_result(
                point, run_id, result.outcome, result.retry_not_before)
        finally:
            point.close()
