"""Durable public-phase activation and control primitives for C0B-2.

The module owns no network client and does not inspect private corpus data.  It binds
strict public plans to the existing checkpoint ledger and deliberately registers only
work made claimable by an immutable activation row.

DISPOSITION: benchmark-only diagnostic; remove after C0B artifacts are accepted.
"""
from __future__ import annotations

import json
import hashlib
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Optional, TYPE_CHECKING

from .c0b2_checkpoint import (
    CheckpointError, ImmutableViolation, canonical_json, sha256_json,
)
from .c0b2_public_schema import (
    AcceptancePlan, CancellationControl, CancellationHealthEvidence,
    ContextControl, ContextProbeEvidence, DPhasePlan, FSeedPlan, HealthControl,
    PLAN_ORDER, PlanActivation, validate_artifact,
)
from .c0b2_public_scoring import derive_health_answer_evidence, ordered_reasons

if TYPE_CHECKING:
    from .c0b2_checkpoint import Checkpoint


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL_KINDS = {"context_probe", "cancellation_probe", "cancellation_health"}
_PLAN_MODELS = {
    "D1_OUTPUT": DPhasePlan,
    "D2_CHUNK": DPhasePlan,
    "D3_CONTEXT": DPhasePlan,
    "D4_CONFIRMATION": DPhasePlan,
    "F_SEED_1": FSeedPlan,
    "F_SEED_17": FSeedPlan,
    "F_SEED_20260804": FSeedPlan,
    "F_ACCEPTANCE": AcceptancePlan,
}
_PLAN_PARENT_DECISION = {
    "D1_OUTPUT": "stage-c-selection",
    "D2_CHUNK": "stage-d-d1-selection",
    "D3_CONTEXT": "stage-d-d2-selection",
    "D4_CONFIRMATION": "stage-d-d3-selection",
    "F_SEED_1": "stage-d-selection",
    "F_SEED_17": "stage-d-selection",
    "F_SEED_20260804": "stage-d-selection",
    "F_ACCEPTANCE": "stage-f-provisional-selection",
}
_ACTIVATION_PARENT_DECISION = {
    **_PLAN_PARENT_DECISION,
    "F_SEED_17": "stage-f-seed-activation",
    "F_SEED_20260804": "stage-f-seed-activation",
}
_PREVIOUS_PLAN = {
    "D1_OUTPUT": "C",
    "D2_CHUNK": "D1_OUTPUT",
    "D3_CONTEXT": "D2_CHUNK",
    "D4_CONFIRMATION": "D3_CONTEXT",
    "F_SEED_1": "D4_CONFIRMATION",
    "F_SEED_17": "F_SEED_1",
    "F_SEED_20260804": "F_SEED_17",
    "F_ACCEPTANCE": "F_SEED_20260804",
}
_TERMINAL_WORK_STATES = {"SUCCEEDED", "COMPLETED_INVALID"}
_CONTROL_MODELS = {
    "context_probe": ContextControl,
    "cancellation_probe": CancellationControl,
    "cancellation_health": HealthControl,
}
_CONTROL_GROUP_FIELDS = {
    "context_probe": "context_control",
    "cancellation_probe": "cancellation_control",
    "cancellation_health": "health_control",
}


@dataclass(frozen=True)
class RuntimePosition:
    active_stage: str
    active_plan_key: str


@dataclass(frozen=True)
class PhaseActivation:
    plan_key: str
    plan_sha256: str
    activation_sha256: str
    registered_work_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeControlRecord:
    control_id: str
    plan_key: str
    kind: str
    control_sha256: str
    control_json: str
    state: str
    evidence_sha256: Optional[str]
    evidence_json: Optional[str]
    not_before_utc: Optional[str]


@contextmanager
def runtime_transaction(point: "Checkpoint") -> Iterator[None]:
    """Join the caller's transaction, or own a short immediate transaction."""
    owns = not point.conn.in_transaction
    if owns:
        point.conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        if owns:
            point.conn.commit()
    except Exception:
        if owns:
            point.conn.rollback()
        raise


def _canonical_object(raw: str, digest: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ImmutableViolation(f"{label} is not an object")
    if canonical_json(value) != raw or sha256_json(value) != digest:
        raise ImmutableViolation(f"{label} hash or canonical encoding changed")
    return value


def _decision_digest(point: "Checkpoint", decision_id: str) -> str:
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    if not row or row[3] != "ACTIVATED":
        raise ImmutableViolation(
            f"phase parent decision {decision_id} is absent or not activated")
    try:
        value = json.loads(row[4])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImmutableViolation(f"decision {decision_id} is not valid JSON") from exc
    if canonical_json(value) != row[4]:
        raise ImmutableViolation(f"decision {decision_id} is not canonical")
    return sha256_json((decision_id, *row))


def runtime_position(point: "Checkpoint") -> RuntimePosition:
    row = point.conn.execute(
        "SELECT active_stage,active_plan_key FROM runtime_cursor WHERE id=1"
    ).fetchone()
    if not row or row[0] not in {"C", "D", "F"} or row[1] not in PLAN_ORDER:
        raise ImmutableViolation("runtime cursor is missing or invalid")
    expected_stage = "C" if row[1] == "C" else str(row[1])[0]
    if row[0] != expected_stage:
        raise ImmutableViolation("runtime stage and plan key disagree")
    return RuntimePosition(str(row[0]), str(row[1]))


def _normalized_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("phase plan must be a mapping")
    plan_key = value.get("plan_key")
    model = _PLAN_MODELS.get(plan_key)
    if model is None:
        raise ValueError(f"unknown public phase plan {plan_key!r}")
    return validate_artifact(model, value)


def load_phase_plan(point: "Checkpoint", plan_key: str) -> tuple[Optional[str], str, str]:
    if plan_key not in _PLAN_MODELS:
        raise ValueError(f"unknown public phase plan {plan_key!r}")
    row = point.conn.execute(
        "SELECT parent_decision_sha256,plan_hash,plan_json,budget_stage,phase "
        "FROM phase_plans WHERE plan_key=?", (plan_key,),
    ).fetchone()
    if not row:
        raise CheckpointError(f"unknown phase plan {plan_key}")
    value = _canonical_object(str(row[2]), str(row[1]), f"phase plan {plan_key}")
    normalized = _normalized_plan(value)
    if (normalized != value or normalized["parent_decision_sha256"] != row[0]
            or normalized["budget_stage"] != row[3]
            or normalized["phase"] != row[4]):
        raise ImmutableViolation(f"phase plan {plan_key} columns disagree with its payload")
    expected_parent = _decision_digest(point, _PLAN_PARENT_DECISION[plan_key])
    if row[0] != expected_parent:
        raise ImmutableViolation(f"phase plan {plan_key} parent decision changed")
    return str(row[0]) if row[0] is not None else None, str(row[1]), str(row[2])


def freeze_phase_plan(point: "Checkpoint", value: Mapping[str, Any]) -> str:
    """Freeze a strict phase plan without making its work claimable."""
    normalized = _normalized_plan(value)
    key = normalized["plan_key"]
    expected_parent = _decision_digest(point, _PLAN_PARENT_DECISION[key])
    if normalized["parent_decision_sha256"] != expected_parent:
        raise ImmutableViolation(f"phase plan {key} is not chained to its exact decision")
    raw, digest = canonical_json(normalized), sha256_json(normalized)
    row = point.conn.execute(
        "SELECT parent_decision_sha256,plan_hash,plan_json,budget_stage,phase "
        "FROM phase_plans WHERE plan_key=?", (key,),
    ).fetchone()
    frozen = (expected_parent, digest, raw, normalized["budget_stage"], normalized["phase"])
    if row and row != frozen:
        raise ImmutableViolation(f"phase plan {key} is already frozen")
    if not row:
        point.conn.execute(
            "INSERT INTO phase_plans(plan_key,budget_stage,phase,"
            "parent_decision_sha256,plan_hash,plan_json,created) VALUES(?,?,?,?,?,?,?)",
            (key, normalized["budget_stage"], normalized["phase"],
             expected_parent, digest, raw, time.time()),
        )
    return digest


def _activation_work(plan: Mapping[str, Any], group_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    key = str(plan["plan_key"])
    work = list(plan["work"])
    if key.startswith("D") or key == "F_ACCEPTANCE":
        if group_ids:
            raise ImmutableViolation(f"phase plan {key} cannot activate groups")
        return work
    ordered = tuple(group["group_id"] for group in plan["groups"])
    if plan["plan_key"] == "F_SEED_1" and group_ids != ordered:
        raise ImmutableViolation("F seed-1 must activate every frozen group in order")
    if not group_ids or group_ids != tuple(item for item in ordered if item in group_ids):
        raise ImmutableViolation("activated F groups are absent, unknown, duplicated, or reordered")
    if len(set(group_ids)) != len(group_ids):
        raise ImmutableViolation("activated F groups must be unique")
    return [item for item in work if item["activation_group_id"] in group_ids]


def _register_activated_work(point: "Checkpoint", plan: Mapping[str, Any],
                             work: list[dict[str, Any]]) -> tuple[str, ...]:
    plan_key, stage = str(plan["plan_key"]), str(plan["budget_stage"])
    registered: list[str] = []
    for item in work:
        identity = (stage, item["cell_id"], item["request_sha256"])
        row = point.conn.execute(
            "SELECT stage,cell_id,request_hash FROM work_items WHERE work_id=?",
            (item["work_id"],),
        ).fetchone()
        if row and row != identity:
            raise ImmutableViolation(f"activated work {item['work_id']} identity changed")
        if not row:
            point.conn.execute(
                "INSERT INTO work_items VALUES(?,?,?,?,'PENDING',NULL)",
                (item["work_id"], *identity),
            )
        registry = point.conn.execute(
            "SELECT plan_key,activation_group_id FROM phase_work_registry WHERE work_id=?",
            (item["work_id"],),
        ).fetchone()
        expected = (plan_key, item["activation_group_id"])
        if registry and registry != expected:
            raise ImmutableViolation(f"activated work {item['work_id']} registry changed")
        if not registry:
            point.conn.execute(
                "INSERT INTO phase_work_registry VALUES(?,?,?)",
                (item["work_id"], *expected),
            )
        registered.append(item["work_id"])
    return tuple(registered)


def _advance_cursor(point: "Checkpoint", plan_key: str) -> None:
    position = runtime_position(point)
    target_stage = plan_key[0]
    previous = _PREVIOUS_PLAN[plan_key]
    if plan_key == "F_SEED_1" and position.active_stage == "D":
        if point.state() != "PAUSED_STAGE_BOUNDARY":
            raise CheckpointError("F activation requires the D stage boundary")
    elif plan_key == "D1_OUTPUT" and position.active_stage == "C":
        if point.state() != "PAUSED_STAGE_BOUNDARY":
            raise CheckpointError("D activation requires the C stage boundary")
    elif (position.active_stage != target_stage
          or position.active_plan_key != previous or point.state() != "RUNNING"):
        raise CheckpointError(f"phase activation {plan_key} is out of order")
    point.conn.execute(
        "UPDATE runtime_cursor SET active_stage=?,active_plan_key=?,updated=? WHERE id=1",
        (target_stage, plan_key, time.time()),
    )
    if position.active_stage != target_stage:
        point.conn.execute(
            "UPDATE run_state SET state='RUNNING',updated=? WHERE id=1", (time.time(),))


def freeze_activate_phase_plan(
        point: "Checkpoint", value: Mapping[str, Any], *,
        activated_group_ids: tuple[str, ...] = (),
        evidence_sha256: Optional[str] = None,
        activation_parent_decision_sha256: Optional[str] = None) -> PhaseActivation:
    """Atomically freeze/activate a phase and register only activated items."""
    if not isinstance(activated_group_ids, tuple):
        raise TypeError("activated_group_ids must be an immutable tuple")
    if evidence_sha256 is not None and not _SHA256_RE.fullmatch(evidence_sha256):
        raise ValueError("invalid activation evidence SHA-256")
    requested_key = value.get("plan_key") if isinstance(value, Mapping) else None
    if requested_key in {"F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE"}:
        raise CheckpointError(
            "later F seeds and acceptance require the B4 atomic activation API")
    normalized = _normalized_plan(value)
    key = normalized["plan_key"]
    with runtime_transaction(point):
        plan_hash = freeze_phase_plan(point, normalized)
        _parent, _digest, raw = load_phase_plan(point, key)
        plan = json.loads(raw)
        decision_id = _ACTIVATION_PARENT_DECISION[key]
        exact_activation_parent = _decision_digest(point, decision_id)
        activation_parent = activation_parent_decision_sha256 or normalized[
            "parent_decision_sha256"]
        if activation_parent != exact_activation_parent:
            raise ImmutableViolation(f"phase activation {key} has the wrong parent decision")
        work = _activation_work(plan, activated_group_ids)
        activation = validate_artifact(PlanActivation, {
            "version": "c0b2-plan-activation-v1",
            "run_id": point.header()["run_id"],
            "budget_stage": normalized["budget_stage"],
            "plan_key": key,
            "plan_sha256": plan_hash,
            "parent_decision_sha256": activation_parent,
            "state": "ACTIVATED",
            "activated_group_ids": list(activated_group_ids),
            "evidence_sha256": evidence_sha256,
        })
        activation_raw = canonical_json(activation)
        activation_hash = sha256_json(activation)
        existing = point.conn.execute(
            "SELECT activation_hash,activation_json FROM plan_activations WHERE plan_key=?",
            (key,),
        ).fetchone()
        if existing:
            if existing != (activation_hash, activation_raw):
                raise ImmutableViolation(f"phase activation {key} is already frozen")
            registered = tuple(row[0] for row in point.conn.execute(
                "SELECT work_id FROM phase_work_registry WHERE plan_key=? ORDER BY rowid",
                (key,),
            ))
            expected = tuple(item["work_id"] for item in work)
            if registered != expected:
                raise ImmutableViolation(f"phase activation {key} work registry changed")
            position = runtime_position(point)
            if position != RuntimePosition(normalized["budget_stage"], key):
                raise ImmutableViolation(f"phase activation {key} disagrees with the cursor")
            return PhaseActivation(key, plan_hash, activation_hash, registered)
        _advance_cursor(point, key)
        registered = _register_activated_work(point, plan, work)
        point.conn.execute(
            "INSERT INTO plan_activations VALUES(?,?,?,?)",
            (key, activation_hash, activation_raw, time.time()),
        )
        return PhaseActivation(key, plan_hash, activation_hash, registered)


def _control_record(row: tuple[Any, ...]) -> RuntimeControlRecord:
    raw = str(row[4])
    _canonical_object(raw, str(row[3]), f"runtime control {row[0]}")
    if row[7] is not None:
        _canonical_object(str(row[7]), str(row[6]), f"runtime control evidence {row[0]}")
    if ((row[5] == "PENDING") != (row[6] is None and row[7] is None)
            or row[5] not in {"PENDING", "COMPLETE", "CANCELLED_UNVERIFIED"}
            or (row[8] is not None and not _valid_utc(str(row[8])))):
        raise ImmutableViolation(f"runtime control {row[0]} has inconsistent state")
    return RuntimeControlRecord(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]), raw, str(row[5]),
        str(row[6]) if row[6] is not None else None,
        str(row[7]) if row[7] is not None else None,
        str(row[8]) if row[8] is not None else None,
    )


def load_runtime_control(point: "Checkpoint", control_id: str) -> RuntimeControlRecord:
    row = point.conn.execute(
        "SELECT control_id,plan_key,kind,control_hash,control_json,state,"
        "evidence_hash,evidence_json,not_before_utc FROM runtime_controls "
        "WHERE control_id=?", (control_id,),
    ).fetchone()
    if not row:
        raise CheckpointError(f"unknown runtime control {control_id}")
    return _control_record(row)


def _bound_control(point: "Checkpoint", plan_key: str, kind: str,
                   value: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one control and bind it to an activated F group."""
    model = _CONTROL_MODELS.get(kind)
    if model is None:
        raise ValueError(f"unknown runtime control kind {kind!r}")
    normalized = validate_artifact(model, value)
    _parent, _digest, plan_raw = load_phase_plan(point, plan_key)
    plan = json.loads(plan_raw)
    activation = point.conn.execute(
        "SELECT activation_json FROM plan_activations WHERE plan_key=?", (plan_key,),
    ).fetchone()
    if not activation:
        raise CheckpointError("runtime control requires an activated phase plan")
    activation_row = point.conn.execute(
        "SELECT activation_hash FROM plan_activations WHERE plan_key=?", (plan_key,),
    ).fetchone()
    activation_value = validate_artifact(
        PlanActivation, _canonical_object(
            str(activation[0]), str(activation_row[0]), "runtime control activation"))
    active_groups = tuple(activation_value.get("activated_group_ids", ()))
    field = _CONTROL_GROUP_FIELDS[kind]
    matches = [
        group for group in plan.get("groups", ())
        if group.get("group_id") in active_groups and group.get(field) == normalized
    ]
    if len(matches) != 1:
        raise ImmutableViolation(
            "runtime control is not the exact control of one activated F group")
    if matches[0].get("candidate_id") != normalized.get("candidate_id"):
        raise ImmutableViolation("runtime control candidate differs from its group")
    return normalized


def freeze_runtime_control(point: "Checkpoint", plan_key: str, control_id: str,
                           kind: str, value: Mapping[str, Any]) -> str:
    if not _SHA256_RE.fullmatch(control_id):
        raise ValueError("runtime control ID must be a SHA-256")
    if kind not in _CONTROL_KINDS:
        raise ValueError("invalid runtime control kind")
    if not isinstance(value, Mapping) or value.get("control_id") != control_id:
        raise ImmutableViolation("runtime control payload identity changed")
    if value.get("kind") != kind:
        raise ImmutableViolation("runtime control kind differs from its payload")
    normalized = _bound_control(point, plan_key, kind, value)
    raw, digest = canonical_json(normalized), sha256_json(normalized)
    row = point.conn.execute(
        "SELECT plan_key,kind,control_hash,control_json FROM runtime_controls "
        "WHERE control_id=?", (control_id,),
    ).fetchone()
    frozen = (plan_key, kind, digest, raw)
    if row and row != frozen:
        raise ImmutableViolation(f"runtime control {control_id} is already frozen")
    if not row:
        with runtime_transaction(point):
            point.conn.execute(
                "INSERT INTO runtime_controls VALUES(?,?,?,?,?,'PENDING',NULL,NULL,NULL,?)",
                (control_id, plan_key, kind, digest, raw, time.time()),
            )
            point.conn.execute(
                "INSERT INTO runtime_control_events VALUES(?,1,'PENDING',NULL,NULL,NULL,?)",
                (control_id, time.time()),
            )
    return digest


def _valid_utc(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _cancellation_observation(evidence: Mapping[str, Any],
                              control_id: str) -> dict[str, Any]:
    expected = {
        "version", "cancel_control_id", "cancel_attempt_id", "cancel_state",
        "cancel_first_byte_seen", "cancel_elapsed_ms",
    }
    value = dict(evidence)
    if (set(value) != expected
            or value["version"] != "c0b2-cancellation-observation-v1"
            or value["cancel_control_id"] != control_id
            or not _SHA256_RE.fullmatch(str(value["cancel_attempt_id"]))
            or value["cancel_state"] != "CANCELLED_UNVERIFIED"
            or type(value["cancel_first_byte_seen"]) is not bool
            or type(value["cancel_elapsed_ms"]) is not int
            or value["cancel_elapsed_ms"] < 0):
        raise ImmutableViolation("cancellation observation is not exact")
    return value


def _persist_runtime_control(
        point: "Checkpoint", control_id: str, *, expected_state: str, state: str,
        evidence: Mapping[str, Any],
        not_before_utc: Optional[str] = None) -> RuntimeControlRecord:
    """Persist a control transition, joining attempt finalization when nested."""
    if not isinstance(evidence, Mapping) or not evidence:
        raise ValueError("runtime control evidence must be a nonempty mapping")
    with runtime_transaction(point):
        current = load_runtime_control(point, control_id)
        control = json.loads(current.control_json)
        if current.kind == "context_probe" and state == "COMPLETE":
            normalized = validate_artifact(ContextProbeEvidence, evidence)
            expected = {
                "control_id": control_id, "purpose": control["purpose"],
                "candidate_id": control["candidate_id"], "model": control["model"],
                "model_digest": control["model_digest"],
                "config_sha256": control["config_sha256"],
                "expected_num_ctx": control["minimum_context_length"],
            }
            if any(normalized[key] != value for key, value in expected.items()):
                raise ImmutableViolation("context evidence differs from its frozen control")
        elif current.kind == "cancellation_health" and state == "COMPLETE":
            normalized = validate_artifact(CancellationHealthEvidence, evidence)
            if (normalized["health_control_id"] != control_id
                    or normalized["candidate_id"] != control["candidate_id"]
                    or normalized["health_work_id"] != control["health_work_id"]):
                raise ImmutableViolation("health evidence differs from its frozen control")
            rows = point.conn.execute(
                "SELECT control_id,state,not_before_utc,control_json "
                "FROM runtime_controls WHERE plan_key=? AND kind='cancellation_probe'",
                (current.plan_key,),
            ).fetchall()
            cancel = [row for row in rows
                      if json.loads(str(row[3])).get("candidate_id")
                      == control["candidate_id"]]
            if (len(cancel) != 1 or cancel[0][1] != "CANCELLED_UNVERIFIED"
                    or normalized["cancel_control_id"] != cancel[0][0]
                    or normalized["not_before_utc"] != cancel[0][2]):
                raise ImmutableViolation("health evidence lacks its cancelled predecessor")
            health_rows = point.conn.execute(
                "SELECT attempt_id,created FROM attempts WHERE control_id=? "
                "ORDER BY attempt_no", (control_id,),
            ).fetchall()
            if (not health_rows
                    or normalized["health_attempt_ids"]
                    != [str(row[0]) for row in health_rows]
                    or normalized["started_at_utc"]
                    != _rfc3339(float(health_rows[0][1]))):
                raise ImmutableViolation(
                    "health evidence attempt history or start time changed")
        elif current.kind == "cancellation_probe" and state == "CANCELLED_UNVERIFIED":
            normalized = _cancellation_observation(evidence, control_id)
            attempt = point.conn.execute(
                "SELECT control_id,state,metadata_json FROM attempts WHERE attempt_id=?",
                (normalized["cancel_attempt_id"],),
            ).fetchone()
            metadata = (json.loads(str(attempt[2]))
                        if attempt and attempt[2] is not None else None)
            if (not attempt or attempt[:2] != (control_id, "CANCELLED_UNVERIFIED")
                    or metadata != {
                        "cancel_elapsed_ms": normalized["cancel_elapsed_ms"],
                        "cancel_first_byte_seen": normalized["cancel_first_byte_seen"],
                        "owned_stream_cancelled": True,
                    }):
                raise ImmutableViolation(
                    "cancellation observation differs from its persisted attempt")
        else:
            raise CheckpointError("runtime control evidence/state combination is invalid")
        raw, digest = canonical_json(normalized), sha256_json(normalized)
        if state == "CANCELLED_UNVERIFIED":
            if current.kind != "cancellation_probe" or not_before_utc is None:
                raise CheckpointError("cancellation requires its durable not-before time")
        elif state == "COMPLETE":
            if current.kind == "cancellation_probe":
                not_before_utc = not_before_utc or current.not_before_utc
            elif not_before_utc is not None:
                raise CheckpointError("only cancellation controls retain not-before time")
        else:
            raise ValueError("invalid runtime control state")
        if not_before_utc is not None and not _valid_utc(not_before_utc):
            raise ValueError("invalid runtime control not-before timestamp")
        if state == "CANCELLED_UNVERIFIED":
            attempt = point.conn.execute(
                "SELECT control_id,state,updated FROM attempts WHERE attempt_id=?",
                (normalized["cancel_attempt_id"],),
            ).fetchone()
            threshold = datetime.fromtimestamp(
                float(attempt[2]) + 2.0, timezone.utc) if attempt else None
            observed = datetime.fromisoformat(
                str(not_before_utc).removesuffix("Z") + "+00:00")
            if (not attempt or attempt[:2] != (control_id, "CANCELLED_UNVERIFIED")
                    or observed < threshold):
                raise ImmutableViolation(
                    "cancellation not-before is not two seconds after its attempt")
        exact = (state, digest, raw, not_before_utc)
        observed = (current.state, current.evidence_sha256,
                    current.evidence_json, current.not_before_utc)
        if observed == exact:
            return current
        if current.state != expected_state:
            raise CheckpointError(
                f"runtime control {control_id} is {current.state}, not {expected_state}")
        allowed = ((current.state == "PENDING" and state in {
            "COMPLETE", "CANCELLED_UNVERIFIED"}) or
            (current.state == "CANCELLED_UNVERIFIED" and state == "COMPLETE"))
        if not allowed:
            raise CheckpointError(f"illegal runtime control transition {current.state} -> {state}")
        seq = int(point.conn.execute(
            "SELECT coalesce(max(seq),0)+1 FROM runtime_control_events WHERE control_id=?",
            (control_id,),
        ).fetchone()[0])
        point.conn.execute(
            "UPDATE runtime_controls SET state=?,evidence_hash=?,evidence_json=?,"
            "not_before_utc=?,updated=? WHERE control_id=?",
            (state, digest, raw, not_before_utc, time.time(), control_id),
        )
        point.conn.execute(
            "INSERT INTO runtime_control_events VALUES(?,?,?,?,?,?,?)",
            (control_id, seq, state, digest, raw, not_before_utc, time.time()),
        )
        return load_runtime_control(point, control_id)


def update_runtime_control(
        point: "Checkpoint", control_id: str, *, expected_state: str, state: str,
        evidence: Mapping[str, Any],
        not_before_utc: Optional[str] = None) -> RuntimeControlRecord:
    """Public transition for the intermediate owned-stream cancellation only.

    Context and health completion are deliberately derived by the executor wrappers
    below from persisted attempts; callers cannot submit authored completion evidence.
    """
    current = load_runtime_control(point, control_id)
    if current.kind != "cancellation_probe" or state != "CANCELLED_UNVERIFIED":
        raise CheckpointError(
            "context/health completion must derive from persisted attempt history")
    return _persist_runtime_control(
        point, control_id, expected_state=expected_state, state=state,
        evidence=evidence, not_before_utc=not_before_utc)


class _OwnedCancellationEvent(threading.Event):
    """A per-stream event that records the first-byte cancellation instant."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic):
        super().__init__()
        self._monotonic = monotonic
        self.first_set_at: Optional[float] = None

    def set(self) -> None:
        if self.first_set_at is None:
            self.first_set_at = self._monotonic()
        super().set()


def _active_control(executor: Any, request: Any, kind: str
                    ) -> tuple[RuntimeControlRecord, dict[str, Any], dict[str, Any]]:
    point = executor.checkpoint
    record = load_runtime_control(point, request.control_id)
    if record.kind != kind:
        raise ImmutableViolation("requested runtime control kind changed")
    control = _bound_control(
        point, record.plan_key, kind, json.loads(record.control_json))
    position = runtime_position(point)
    _parent, _digest, plan_raw = load_phase_plan(point, record.plan_key)
    plan = json.loads(plan_raw)
    if (position.active_plan_key != record.plan_key
            or position.active_stage != request.stage
            or plan["budget_stage"] != request.stage
            or request.model != next(
                (row["model"] for row in plan["candidates"]
                 if row["candidate_id"] == control["candidate_id"]), None)):
        raise ImmutableViolation("runtime control is outside the active candidate plan")
    request_field = ("payload_sha256" if kind == "context_probe"
                     else "request_sha256")
    if request.request_hash != control[request_field]:
        raise ImmutableViolation("runtime control request hash changed")
    return record, control, plan


def _precharge_control(executor: Any, request: Any) -> Optional[Any]:
    from .c0b2_executor import ExecutionResult, SoftWallReached

    try:
        inserted = executor.checkpoint.precharge(
            attempt_id=request.attempt_id, stage=request.stage,
            call_class=request.call_class, request_hash=request.request_hash,
            attempt_no=request.attempt_no, control_id=request.control_id,
            invocation_ordinal=executor.invocation_ordinal,
            first_control_class="preflight_probe",
            claim_guard=executor._guard_soft_wall,
            budget_failure_callback=executor._budget_failure_callback)
    except SoftWallReached:
        executor.checkpoint.transition("PAUSED_SOFT_WALL")
        return ExecutionResult("PAUSED_SOFT_WALL")
    if inserted:
        return None
    state = executor.checkpoint.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?", (request.attempt_id,),
    ).fetchone()[0]
    return ExecutionResult(f"ALREADY_{state}", request.attempt_id)


def _finish_public_failure(executor: Any, request: Any, terminal: str) -> Any:
    from .c0b2_executor import ExecutionResult
    from .c0b2_runtime import finish_public_failure_attempt

    finish_public_failure_attempt(
        executor.checkpoint, attempt_id=request.attempt_id, terminal=terminal)
    return ExecutionResult(terminal, request.attempt_id)


def _legacy_context_probe(executor: Any, request: Any) -> Any:
    """Preserve the frozen Stage-C context-obligation path byte-for-byte in effect."""
    from .c0b2_executor import (
        ExecutionResult, ProvenanceFailure, RetryableTransport, SafetyLimit,
    )

    point = executor.checkpoint
    obligation = point.pending_context_obligation("C")
    if (obligation is None or request.model != obligation.model
            or request.control_id != obligation.control_id
            or request.request_hash != obligation.request_hash):
        raise CheckpointError("context probe differs from the durable obligation")
    gate = executor._resource_gate(request.model)
    if gate is not None:
        return gate
    duplicate = _precharge_control(executor, request)
    if duplicate is not None:
        return duplicate
    executor.current_attempt = request.attempt_id
    try:
        response = executor.transport(request, executor.cancellation.event)
        if executor.cancellation.event.is_set():
            point.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        executor._require_returned_response(response, {"ACCEPTED"})
        point.finish_attempt(
            request.attempt_id, outcome="ACCEPTED", response=response.content,
            metadata=response.metadata, accept_work=False,
            before_commit=lambda: point.complete_context_obligation(
                control_id=request.control_id, attempt_id=request.attempt_id))
        return ExecutionResult("ACCEPTED", request.attempt_id)
    except RetryableTransport:
        if executor.cancellation.event.is_set():
            point.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return executor._finish_retryable_control(request)
    except SafetyLimit:
        if executor.cancellation.event.is_set():
            point.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        terminal = executor._safety_terminal()
        return _finish_public_failure(executor, request, terminal)
    except ProvenanceFailure:
        return executor._finish_provenance(request.attempt_id)
    finally:
        executor.current_attempt = None


def _context_trigger(point: "Checkpoint", plan: Mapping[str, Any],
                     candidate_id: str) -> str:
    for item in plan["work"]:
        if item["candidate_id"] != candidate_id:
            continue
        if point.conn.execute(
                "SELECT 1 FROM attempts WHERE work_id=? AND state IN "
                "('ACCEPTED','SCHEMA_INVALID') LIMIT 1", (item["work_id"],)).fetchone():
            return str(item["work_id"])
    raise CheckpointError("context control lacks its first answered trigger work")


def _context_evidence(point: "Checkpoint", control: Mapping[str, Any],
                      plan: Mapping[str, Any], content: str,
                      metadata: Mapping[str, Any]) -> dict[str, Any]:
    from .c0b2_executor import ProvenanceFailure

    try:
        value = json.loads(content)
        expected_keys = {
            "purpose", "config_sha256", "model", "digest", "size",
            "size_vram", "context_length",
        }
        response_hash = sha256_json(value)
        metadata_hash = metadata.get("response_sha256")
        if (not isinstance(value, dict) or set(value) != expected_keys
                or canonical_json(value) != content
                or response_hash != metadata_hash
                or value["purpose"] != control["purpose"]
                or value["config_sha256"] != control["config_sha256"]
                or value["model"] != control["model"]
                or value["digest"] != control["model_digest"]
                or type(value["context_length"]) is not int
                or value["context_length"] < control["minimum_context_length"]):
            raise ValueError
        return validate_artifact(ContextProbeEvidence, {
            "control_id": control["control_id"], "purpose": control["purpose"],
            "candidate_id": control["candidate_id"], "model": control["model"],
            "model_digest": control["model_digest"],
            "config_sha256": control["config_sha256"],
            "expected_num_ctx": control["minimum_context_length"],
            "observed_context_length": value["context_length"],
            "trigger_work_id": _context_trigger(
                point, plan, control["candidate_id"]),
            "state": "PASSED", "response_sha256": response_hash,
        })
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProvenanceFailure("context evidence differs from bounded response") from exc


def _complete_context_control(point: "Checkpoint", control_id: str,
                              attempt_id: str) -> RuntimeControlRecord:
    record = load_runtime_control(point, control_id)
    control = _bound_control(
        point, record.plan_key, "context_probe", json.loads(record.control_json))
    _parent, _digest, plan_raw = load_phase_plan(point, record.plan_key)
    row = point.conn.execute(
        "SELECT control_id,state,response,metadata_json FROM attempts "
        "WHERE attempt_id=?", (attempt_id,),
    ).fetchone()
    if (not row or row[0] != control_id or row[1] != "ACCEPTED"
            or not isinstance(row[2], str) or not isinstance(row[3], str)):
        raise ImmutableViolation("context completion lacks its accepted attempt")
    evidence = _context_evidence(
        point, control, json.loads(plan_raw), row[2], json.loads(row[3]))
    return _persist_runtime_control(
        point, control_id, expected_state="PENDING", state="COMPLETE",
        evidence=evidence)


def run_context_probe(executor: Any, request: Any) -> Any:
    """Run the legacy C obligation or a strict activated F context control."""
    from .c0b2_executor import (
        ExecutionResult, ProvenanceFailure, RetryableTransport, SafetyLimit,
    )

    executor._require_lock()
    executor._require_invocation_stage(request.stage)
    if executor.cancellation.event.is_set():
        executor.checkpoint.cancel()
        return ExecutionResult("CANCELLED_PENDING_RESUME")
    executor._require_preflight_complete(request.stage)
    if request.stage == "C":
        return _legacy_context_probe(executor, request)
    record, control, plan = _active_control(executor, request, "context_probe")
    if record.state == "COMPLETE":
        return ExecutionResult("ALREADY_COMPLETE")
    if record.state != "PENDING":
        raise CheckpointError("context runtime control is not pending")
    _context_trigger(executor.checkpoint, plan, control["candidate_id"])
    gate = executor._resource_gate(request.model)
    if gate is not None:
        return gate
    duplicate = _precharge_control(executor, request)
    if duplicate is not None:
        return duplicate
    executor.current_attempt = request.attempt_id
    try:
        response = executor.transport(request, executor.cancellation.event)
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        executor._require_returned_response(response, {"ACCEPTED"})
        executor.checkpoint.finish_attempt(
            request.attempt_id, outcome="ACCEPTED", response=response.content,
            metadata=response.metadata, accept_work=False,
            before_commit=lambda: _complete_context_control(
                executor.checkpoint, request.control_id, request.attempt_id))
        return ExecutionResult("ACCEPTED", request.attempt_id)
    except RetryableTransport:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return executor._finish_retryable_control(request)
    except SafetyLimit:
        return _finish_public_failure(executor, request, "FAILED_SAFETY")
    except ProvenanceFailure:
        return _finish_public_failure(executor, request, "BLOCKED_PROVENANCE")
    finally:
        executor.current_attempt = None


def _rfc3339(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _persist_planned_cancellation(executor: Any, request: Any,
                                  control: Mapping[str, Any], elapsed_ms: int) -> str:
    point = executor.checkpoint
    observed_at = executor._now()
    not_before = _rfc3339(
        observed_at + max(2000, control["health_not_before_ms"]) / 1000.0)
    evidence = {
        "version": "c0b2-cancellation-observation-v1",
        "cancel_control_id": request.control_id,
        "cancel_attempt_id": request.attempt_id,
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": True,
        "cancel_elapsed_ms": elapsed_ms,
    }
    with runtime_transaction(point):
        row = point.conn.execute(
            "SELECT control_id,state FROM attempts WHERE attempt_id=?",
            (request.attempt_id,),
        ).fetchone()
        if row != (request.control_id, "DISPATCHING"):
            raise CheckpointError("planned cancellation attempt is not dispatching")
        point.conn.execute(
            "UPDATE attempts SET state='CANCELLED_UNVERIFIED',response=NULL,"
            "metadata_json=?,updated=? WHERE attempt_id=?",
            (canonical_json({
                "cancel_elapsed_ms": elapsed_ms, "cancel_first_byte_seen": True,
                "owned_stream_cancelled": True,
            }), observed_at,
             request.attempt_id),
        )
        update_runtime_control(
            point, request.control_id, expected_state="PENDING",
            state="CANCELLED_UNVERIFIED", evidence=evidence,
            not_before_utc=not_before)
    return not_before


def run_cancellation_probe(executor: Any, request: Any) -> Any:
    """Cancel only the frozen Stage-F response stream, never the operator event."""
    from .c0b2_executor import (
        ExecutionResult, ProvenanceFailure, RetryableTransport, SafetyLimit,
    )

    executor._require_lock()
    executor._require_invocation_stage(request.stage)
    if request.stage != "F":
        raise CheckpointError("cancellation probes require Stage F")
    if executor.cancellation.event.is_set():
        executor.checkpoint.cancel()
        return ExecutionResult("CANCELLED_PENDING_RESUME")
    executor._require_preflight_complete("F")
    record, control, _plan = _active_control(
        executor, request, "cancellation_probe")
    if record.state != "PENDING":
        return ExecutionResult(f"ALREADY_{record.state}")
    executor._require_f_cancellation_ready(control["candidate_id"])
    gate = executor._resource_gate(request.model)
    if gate is not None:
        return gate
    duplicate = _precharge_control(executor, request)
    if duplicate is not None:
        return duplicate
    owned = _OwnedCancellationEvent()
    executor.current_attempt = request.attempt_id
    try:
        executor.transport(request, owned)
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return _finish_public_failure(executor, request, "FAILED_SAFETY")
    except RetryableTransport:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        if owned.first_set_at is None:
            return executor._finish_retryable_control(request)
        elapsed_ms = max(0, int((time.monotonic() - owned.first_set_at) * 1000))
        _persist_planned_cancellation(executor, request, control, elapsed_ms)
        return ExecutionResult("CANCELLED_UNVERIFIED", request.attempt_id)
    except SafetyLimit:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return _finish_public_failure(executor, request, "FAILED_SAFETY")
    except ProvenanceFailure:
        return _finish_public_failure(executor, request, "BLOCKED_PROVENANCE")
    finally:
        executor.current_attempt = None


def _health_attempts(point: "Checkpoint", control_id: str
                     ) -> tuple[list[dict[str, Any]], list[tuple[Any, ...]]]:
    rows = point.conn.execute(
        "SELECT attempt_id,state,response,metadata_json,created FROM attempts "
        "WHERE control_id=? ORDER BY attempt_no", (control_id,),
    ).fetchall()
    values: list[dict[str, Any]] = []
    for attempt_id, state, response, metadata_raw, _created in rows:
        values.append({
            "attempt_id": str(attempt_id), "state": str(state),
            "response": response,
            "metadata": json.loads(metadata_raw) if metadata_raw is not None else {},
        })
    return values, rows


def _answer_has_pii(worksheet: str, response: str) -> bool:
    from .c0b2_stage_c import classify_answer

    classified = classify_answer(worksheet, response)
    if not classified.valid or classified.value is None:
        return False
    if worksheet == "v2":
        return any(row["category"] == "pii" for row in classified.value["findings"])
    return any(row["category"] == "pii" and row["evidence"]
               for row in classified.value["categories"])


def _health_evidence(point: "Checkpoint", health: Mapping[str, Any],
                     cancel: RuntimeControlRecord, *, source: str,
                     worksheet: str, num_predict: int, num_ctx: int) -> dict[str, Any]:
    record = load_runtime_control(point, health["control_id"])
    _parent, _digest, plan_raw = load_phase_plan(point, record.plan_key)
    plan = json.loads(plan_raw)
    candidate = next(
        (row for row in plan["candidates"]
         if row["candidate_id"] == health["candidate_id"]), None)
    source_rows = [
        row for row in plan["work"]
        if (row["candidate_id"] == health["candidate_id"]
            and row["doc_id"] == health["source_doc_id"]
            and row["chunk_index"] == health["chunk_index"])
    ]
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if (not candidate or len(source_rows) != 1
            or source_rows[0]["chunk_sha256"] != source_hash
            or (worksheet, num_predict, num_ctx) != (
                candidate["worksheet"], candidate["num_predict"],
                candidate["num_ctx"])):
        raise ImmutableViolation(
            "health scoring inputs differ from the frozen candidate/source chunk")
    attempts, rows = _health_attempts(point, health["control_id"])
    derived = derive_health_answer_evidence(
        attempts, worksheet=worksheet, source=source,
        num_predict=num_predict, num_ctx=num_ctx)
    cancel_evidence = _cancellation_observation(
        json.loads(str(cancel.evidence_json)), cancel.control_id)
    cancel_attempt = point.conn.execute(
        "SELECT control_id,state,metadata_json FROM attempts WHERE attempt_id=?",
        (cancel_evidence["cancel_attempt_id"],),
    ).fetchone()
    expected_cancel_metadata = {
        "cancel_elapsed_ms": cancel_evidence["cancel_elapsed_ms"],
        "cancel_first_byte_seen": cancel_evidence["cancel_first_byte_seen"],
        "owned_stream_cancelled": True,
    }
    if (not cancel_attempt
            or cancel_attempt[:2]
            != (cancel.control_id, "CANCELLED_UNVERIFIED")
            or json.loads(str(cancel_attempt[2])) != expected_cancel_metadata):
        raise ImmutableViolation(
            "cancel observation differs from its exact attempt evidence")
    authoritative = next(
        (row["response"] for row in attempts if row["state"] == "ACCEPTED"), None)
    pii_present = (isinstance(authoritative, str)
                   and _answer_has_pii(worksheet, authoritative))
    answered = derived["max_answered_prompt_eval_count"] is not None
    flags = {
        "cancel_not_observed": not cancel_evidence["cancel_first_byte_seen"],
        "cancel_after_5_seconds": cancel_evidence["cancel_elapsed_ms"] > 5000,
        "health_missing": not answered,
        "health_eventual_invalid": bool(answered and not derived["eventual_valid"]),
        "health_pii_missing": bool(derived["eventual_valid"] and not pii_present),
        "health_grounding_failure": bool(
            derived["eventual_valid"] and pii_present
            and not derived["retained_grounded_pii"]),
        "health_length_outcome": derived["length_outcomes"] != 0,
        "health_channel_violation": not all((
            derived["tools_empty"], derived["images_empty"],
            derived["unknown_message_fields_empty"],
            derived["schema_escape_empty"])),
        "health_context_headroom_failure": not derived["headroom_passed"],
    }
    reason_order = (
        "cancel_not_observed", "cancel_after_5_seconds", "health_missing",
        "health_eventual_invalid", "health_pii_missing", "health_grounding_failure",
        "health_length_outcome", "health_channel_violation",
        "health_context_headroom_failure",
    )
    reasons = ordered_reasons(reason_order, flags)
    return validate_artifact(CancellationHealthEvidence, {
        "candidate_id": health["candidate_id"],
        "cancel_control_id": cancel.control_id,
        "cancel_attempt_id": cancel_evidence["cancel_attempt_id"],
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": cancel_evidence["cancel_first_byte_seen"],
        "cancel_elapsed_ms": cancel_evidence["cancel_elapsed_ms"],
        "health_control_id": health["control_id"],
        "health_work_id": health["health_work_id"],
        "health_attempt_ids": [str(row[0]) for row in rows],
        "not_before_utc": cancel.not_before_utc,
        "started_at_utc": _rfc3339(float(rows[0][4])),
        **derived, "passed": not reasons, "failure_reasons": reasons,
    })


def _complete_health_control(
        point: "Checkpoint", health: Mapping[str, Any],
        cancel: RuntimeControlRecord, *, source: str, worksheet: str,
        num_predict: int, num_ctx: int) -> RuntimeControlRecord:
    evidence = _health_evidence(
        point, health, cancel, source=source, worksheet=worksheet,
        num_predict=num_predict, num_ctx=num_ctx)
    return _persist_runtime_control(
        point, health["control_id"], expected_state="PENDING",
        state="COMPLETE", evidence=evidence)


def _cancelled_predecessor(point: "Checkpoint", health: Mapping[str, Any],
                           cancelled_attempt_id: str) -> RuntimeControlRecord:
    rows = point.conn.execute(
        "SELECT control_id FROM runtime_controls WHERE plan_key=? "
        "AND kind='cancellation_probe'", (runtime_position(point).active_plan_key,),
    ).fetchall()
    matched = []
    for row in rows:
        record = load_runtime_control(point, str(row[0]))
        value = json.loads(record.control_json)
        if value["candidate_id"] == health["candidate_id"]:
            matched.append(record)
    if len(matched) != 1:
        raise CheckpointError("health control lacks one cancelled predecessor")
    cancel = matched[0]
    evidence = (_cancellation_observation(
        json.loads(str(cancel.evidence_json)), cancel.control_id)
        if cancel.evidence_json is not None else {})
    invocation = point.conn.execute(
        "SELECT invocation_ordinal FROM attempts WHERE attempt_id=?",
        (cancelled_attempt_id,),
    ).fetchone()
    latest = point.conn.execute(
        "SELECT max(ordinal) FROM invocations WHERE stage='F'").fetchone()[0]
    if (cancel.state != "CANCELLED_UNVERIFIED"
            or evidence.get("cancel_attempt_id") != cancelled_attempt_id
            or cancel.not_before_utc is None or not invocation
            or invocation[0] is None or latest is None
            or int(invocation[0]) >= int(latest)):
        raise CheckpointError("health predecessor is not durably cancelled")
    return cancel


def run_cancellation_health(
        executor: Any, request: Any, *, cancelled_attempt_id: str,
        source: str, worksheet: str, num_predict: int, num_ctx: int) -> Any:
    """Run one strict health request with answered-history schema entitlement."""
    from .c0b2_executor import (
        ExecutionResult, ProvenanceFailure, RetryableTransport, SafetyLimit,
    )

    executor._require_lock()
    executor._require_invocation_stage(request.stage)
    if request.stage != "F":
        raise CheckpointError("cancellation health requires Stage F")
    if executor.cancellation.event.is_set():
        executor.checkpoint.cancel()
        return ExecutionResult("CANCELLED_PENDING_RESUME")
    executor._require_preflight_complete("F")
    record, health, _plan = _active_control(
        executor, request, "cancellation_health")
    if record.state == "COMPLETE":
        return ExecutionResult("ALREADY_COMPLETE")
    cancel = _cancelled_predecessor(
        executor.checkpoint, health, cancelled_attempt_id)
    not_before = datetime.fromisoformat(
        str(cancel.not_before_utc).removesuffix("Z") + "+00:00").timestamp()
    if executor._now() < not_before:
        return ExecutionResult("RETRY_WAIT", retry_not_before=not_before)
    gate = executor._resource_gate(request.model)
    if gate is not None:
        return gate
    duplicate = _precharge_control(executor, request)
    if duplicate is not None:
        return duplicate
    executor.current_attempt = request.attempt_id
    try:
        response = executor.transport(request, executor.cancellation.event)
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        executor._require_returned_response(response, {"ACCEPTED", "SCHEMA_INVALID"})

        def finish_health() -> None:
            executor._reset_resource(request.model)
            attempts, _rows = _health_attempts(
                executor.checkpoint, request.control_id)
            answered = sum(row["state"] in {"ACCEPTED", "SCHEMA_INVALID"}
                           for row in attempts)
            if response.outcome == "ACCEPTED" or answered >= 2:
                _complete_health_control(
                    executor.checkpoint, health, cancel, source=source,
                    worksheet=worksheet, num_predict=num_predict, num_ctx=num_ctx)

        executor.checkpoint.finish_attempt(
            request.attempt_id, outcome=response.outcome, response=response.content,
            metadata=response.metadata, accept_work=False,
            before_commit=finish_health)
        return ExecutionResult(response.outcome, request.attempt_id)
    except RetryableTransport:
        if executor.cancellation.event.is_set():
            executor.checkpoint.cancel(request.attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
        return executor._finish_retryable_control(request)
    except SafetyLimit:
        return _finish_public_failure(executor, request, "FAILED_SAFETY")
    except ProvenanceFailure:
        return _finish_public_failure(executor, request, "BLOCKED_PROVENANCE")
    finally:
        executor.current_attempt = None
