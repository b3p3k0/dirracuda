"""Strict C0B-3 policy-bound artifact schemas.

The C0B-2 models remain the only authority for legacy v1 bytes.  This module
adds separate current models and explicit dispatch helpers; it never adds an
optional/defaulted policy field to a legacy class.

DISPOSITION: retain through C0B; production consumes only the selected result.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, TypeAlias, TypeVar

from pydantic import BaseModel, Field, model_validator

from .c0b2_public_schema import (
    AcceptancePlan,
    AcceptanceTemplatePayload,
    DInconclusiveResult,
    DInconclusiveReason,
    DPhasePlan,
    FInconclusiveResult,
    FInconclusiveReason,
    FMasterPlan,
    FPlanCandidate,
    FSeedPlan,
    FSelectedResult,
    FailureArtifact,
    FailureEvidence,
    InconclusiveCompletion,
    PublicWork,
    SelectedCompletion,
    Sha256,
    StrictModel,
    sha256_json,
)
from .c0b2_schema import StageCInconclusiveArtifact, StageCSelection
from .c0b3_policy import (
    CURRENT_POLICY,
    POLICY_ID,
    POLICY_SHA256,
    PolicyIdentityError as PolicyError,
    canonical_json_bytes,
    policy_binding,
    require_current_payload as require_policy_binding,
    resolve_header_policy,
    resolve_payload_policy,
)

COMPLETION_DECISION_ID = "c0b3-completion"
PolicyId: TypeAlias = Literal["c0b3-assistive-bounded-fp-v1"]
PolicySha256: TypeAlias = Literal[
    "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235"
]


class PolicyBoundStrictModel(StrictModel):
    """Required current binding, with no absent/null legacy representation."""

    policy_id: PolicyId
    policy_sha256: PolicySha256

    @model_validator(mode="after")
    def exact_policy_binding(self) -> "PolicyBoundStrictModel":
        require_policy_binding(self.model_dump(
            mode="json", include={"policy_id", "policy_sha256"}))
        return self


def policy_binding_fields() -> dict[str, str]:
    """Return fresh exact fields for a current artifact constructor."""
    return {"policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}


class DPhasePlanV2(DPhasePlan, PolicyBoundStrictModel):
    version: Literal["stage-d-phase-plan-v2"]


class FSeedPlanV2(FSeedPlan, PolicyBoundStrictModel):
    version: Literal["stage-f-seed-plan-v2"]


class FSeedPlanEnvelopeV2(StrictModel):
    plan_sha256: Sha256
    payload: FSeedPlanV2

    @model_validator(mode="after")
    def hash_matches(self) -> "FSeedPlanEnvelopeV2":
        if self.plan_sha256 != sha256_json(self.payload):
            raise ValueError("C0B-3 F seed envelope hash differs from its payload")
        return self


class AcceptanceTemplatePayloadV2(
        AcceptanceTemplatePayload, PolicyBoundStrictModel):
    version: Literal["stage-f-acceptance-plan-v2"]


class AcceptanceTemplateEnvelopeV2(StrictModel):
    template_sha256: Sha256
    candidate_id: Sha256
    payload: AcceptanceTemplatePayloadV2

    @model_validator(mode="after")
    def identity_matches(self) -> "AcceptanceTemplateEnvelopeV2":
        if self.template_sha256 != sha256_json(self.payload):
            raise ValueError("C0B-3 acceptance template hash changed")
        if self.candidate_id != self.payload.candidates[0].candidate_id:
            raise ValueError("C0B-3 acceptance candidate differs from its template")
        return self


class FMasterPlanV2(FMasterPlan, PolicyBoundStrictModel):
    version: Literal["stage-f-master-plan-v2"]
    plans: list[FSeedPlanEnvelopeV2] = Field(min_length=3, max_length=3)
    acceptance_templates: list[AcceptanceTemplateEnvelopeV2] = Field(
        min_length=1, max_length=3)

    @model_validator(mode="after")
    def nested_policy_matches(self) -> "FMasterPlanV2":
        own = (self.policy_id, self.policy_sha256)
        nested = [
            (row.payload.policy_id, row.payload.policy_sha256)
            for row in (*self.plans, *self.acceptance_templates)
        ]
        if any(binding != own for binding in nested):
            raise ValueError("C0B-3 F master contains mixed policy lineage")
        return self


class AcceptancePlanV2(PolicyBoundStrictModel):
    version: Literal["stage-f-acceptance-plan-v2"]
    stage: Literal["F"]
    phase: Literal["F_ACCEPTANCE"]
    plan_key: Literal["F_ACCEPTANCE"]
    budget_stage: Literal["F"]
    parent_decision_sha256: Sha256
    master_plan_sha256: Sha256
    template_sha256: Sha256
    candidates: list[FPlanCandidate] = Field(min_length=1, max_length=1)
    work: list[PublicWork] = Field(min_length=44, max_length=44)

    @model_validator(mode="after")
    def exact_c44(self) -> "AcceptancePlanV2":
        raw = self.model_dump(mode="json")
        template = {
            "version": raw["version"], "stage": raw["stage"],
            "phase": raw["phase"], "plan_key": raw["plan_key"],
            "budget_stage": raw["budget_stage"],
            "parent_decision_sha256": None,
            "candidates": raw["candidates"], "work": raw["work"],
            "policy_id": raw["policy_id"],
            "policy_sha256": raw["policy_sha256"],
        }
        AcceptanceTemplatePayloadV2.model_validate(template, strict=True)
        if self.template_sha256 != sha256_json(template):
            raise ValueError("C0B-3 acceptance plan differs from its frozen template")
        return self


class StageCInconclusiveResultV2(
        StageCInconclusiveArtifact, PolicyBoundStrictModel):
    version: Literal["c0b3-result-v1"]


class DInconclusiveResultV2(PolicyBoundStrictModel):
    version: Literal["c0b3-result-v1"]
    terminal: Literal["INCONCLUSIVE"]
    stage: Literal["D"]
    aggregate_sha256: Sha256
    reason: DInconclusiveReason


class FInconclusiveResultV2(PolicyBoundStrictModel):
    version: Literal["c0b3-result-v1"]
    terminal: Literal["INCONCLUSIVE"]
    stage: Literal["F"]
    aggregate_sha256: Sha256
    reason: FInconclusiveReason


class FSelectedResultV2(FSelectedResult, PolicyBoundStrictModel):
    version: Literal["c0b3-result-v1"]


class SelectedCompletionV2(SelectedCompletion, PolicyBoundStrictModel):
    version: Literal["c0b3-completion-v1"]


class InconclusiveCompletionV2(
        InconclusiveCompletion, PolicyBoundStrictModel):
    version: Literal["c0b3-completion-v1"]


class FSeedCursorTransitionV2(PolicyBoundStrictModel):
    version: Literal["c0b3-f-seed-cursor-transition-v1"]
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
    def self_hash_matches(self) -> "FSeedCursorTransitionV2":
        body = self.model_dump(mode="json", exclude={"transition_sha256"})
        if self.transition_sha256 != sha256_json(body):
            raise ValueError("C0B-3 F seed cursor transition self-hash changed")
        return self


CurrentResult: TypeAlias = (
    StageCInconclusiveResultV2 | DInconclusiveResultV2 |
    FInconclusiveResultV2 | FSelectedResultV2
)
CurrentCompletion: TypeAlias = SelectedCompletionV2 | InconclusiveCompletionV2
ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_current(model: type[ModelT], value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one current artifact without accepting absent legacy binding."""
    require_policy_binding(value)
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_versioned_legacy_shape(
        value: Mapping[str, Any], legacy_model: type[ModelT], *,
        legacy_version: str, current_version: str,
) -> dict[str, Any]:
    """Validate v1 exactly or a v2 shape carrying only the frozen policy delta."""
    policy = resolve_payload_policy(value)
    if policy != CURRENT_POLICY:
        return legacy_model.model_validate(
            value, strict=True).model_dump(mode="json")
    if value.get("version") != current_version:
        raise ValueError("C0B-3 artifact version is not exact")
    legacy = dict(value)
    legacy.pop("policy_id")
    legacy.pop("policy_sha256")
    legacy["version"] = legacy_version
    normalized = legacy_model.model_validate(
        legacy, strict=True, context={"policy": CURRENT_POLICY}).model_dump(mode="json")
    if normalized != legacy:
        raise ValueError("C0B-3 artifact differs from its exact v1 structural base")
    return dict(value)


def validate_current_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch the closed C0B-3 terminal-result catalog."""
    require_policy_binding(value)
    key = (value.get("stage"), value.get("terminal"))
    models: dict[tuple[Any, Any], type[BaseModel]] = {
        ("C", "INCONCLUSIVE"): StageCInconclusiveResultV2,
        ("D", "INCONCLUSIVE"): DInconclusiveResultV2,
        ("F", "INCONCLUSIVE"): FInconclusiveResultV2,
        ("F", "SELECTED"): FSelectedResultV2,
    }
    model = models.get(key)
    if model is None:
        raise ValueError("unknown C0B-3 result stage/terminal")
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_current_completion(
        decision_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Require the current completion row identity and exact payload shape."""
    if decision_id != COMPLETION_DECISION_ID:
        raise ValueError("C0B-3 completion decision ID is not exact")
    require_policy_binding(value)
    model: type[BaseModel]
    if value.get("outcome") == "SELECTED":
        model = SelectedCompletionV2
    elif value.get("outcome") == "INCONCLUSIVE":
        model = InconclusiveCompletionV2
    else:
        raise ValueError("unknown C0B-3 completion outcome")
    return model.model_validate(value, strict=True).model_dump(mode="json")


def _header_policy(header: Mapping[str, Any] | None) -> Any:
    return resolve_header_policy({} if header is None else header)


def completion_decision_id(header: Mapping[str, Any] | None) -> str:
    """Select the sole completion row identity for the stored header family."""
    return (COMPLETION_DECISION_ID
            if _header_policy(header) == CURRENT_POLICY else "c0b2-completion")


def validate_result_for_header(
        header: Mapping[str, Any] | None,
        value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a result only under the exact policy family stored in its header."""
    policy = _header_policy(header)
    if resolve_payload_policy(value) != policy:
        raise PolicyError("result policy binding differs from its run header")
    if policy == CURRENT_POLICY:
        return validate_current_result(value)
    models: dict[tuple[Any, Any], type[BaseModel]] = {
        ("C", "INCONCLUSIVE"): StageCInconclusiveArtifact,
        ("D", "INCONCLUSIVE"): DInconclusiveResult,
        ("F", "INCONCLUSIVE"): FInconclusiveResult,
        ("F", "SELECTED"): FSelectedResult,
    }
    model = models.get((value.get("stage"), value.get("terminal")))
    if model is None:
        raise ValueError("unknown legacy result stage/terminal")
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_public_artifact_for_header(
        header: Mapping[str, Any] | None,
        value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate generic failures or a policy-bound terminal result."""
    terminal = value.get("terminal")
    model = (FailureEvidence if value.get("version") == "c0b2-failure-evidence-v1"
             else FailureArtifact if terminal in {
                 "FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
                 "BLOCKED_FILESYSTEM", "ABANDONED"} else None)
    return (model.model_validate(value, strict=True).model_dump(mode="json")
            if model is not None else validate_result_for_header(header, value))


def validate_completion_for_header(
        header: Mapping[str, Any] | None, decision_id: str,
        value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a completion value and decision ID under its stored header family."""
    policy = _header_policy(header)
    if decision_id != completion_decision_id(header):
        raise ValueError("completion decision ID differs from its run header")
    if resolve_payload_policy(value) != policy:
        raise PolicyError("completion policy binding differs from its run header")
    if policy == CURRENT_POLICY:
        return validate_current_completion(decision_id, value)
    model = SelectedCompletion if value.get("outcome") == "SELECTED" \
        else InconclusiveCompletion
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_stage_c_terminal_event(
        header: Mapping[str, Any], detail: Mapping[str, Any],
        aggregate_sha256: str,
) -> str:
    """Validate the exact legacy/current Stage-C terminal event and return its hash."""
    expected = build_stage_c_inconclusive_result(header, aggregate_sha256)
    if (not isinstance(detail, Mapping)
            or set(detail) != {"state", "sha256", "artifact"}
            or detail.get("state") != "INCONCLUSIVE"
            or detail.get("artifact") != expected
            or detail.get("sha256") != sha256_json(expected)):
        raise ValueError("Stage-C final artifact changed")
    return str(detail["sha256"])


def validate_stage_c_completion_owner(
        conn: Any, header: Mapping[str, Any] | None, *,
        plan_sha256: str, aggregate_sha256: str, artifact_sha256: str,
) -> None:
    """Require one exact policy-family completion for a Stage-C terminal."""
    import json

    decision_id = completion_decision_id(header)
    rows = conn.execute(
        "SELECT decision_id,stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id IN ('c0b2-completion','c0b3-completion')"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != decision_id:
        raise ValueError("Stage-C terminal has a mixed completion namespace")
    row = rows[0]
    value = validate_completion_for_header(
        header, decision_id, json.loads(str(row[5])))
    if (row[1:5] != ("C", plan_sha256, aggregate_sha256, "NOT_ACTIVATED")
            or canonical_json_bytes(value).decode("utf-8") != row[5]
            or value["artifact_sha256"] != artifact_sha256
            or value["outcome"] != "INCONCLUSIVE"
            or value["facts"] != {
                "deterministic_stop": True, "reason": "no_stage_c_survivor"}):
        raise ValueError("Stage-C completion differs from its public result")


def validate_d_aggregate_for_header(
        header: Mapping[str, Any], value: Mapping[str, Any], *,
        plan_sha256: str,
) -> dict[str, Any]:
    """Validate a D aggregate under the stored header and exact plan owner."""
    from .c0b2_runtime_d import _restore_d_category_order
    from .c0b2_stage_d import validate_stage_d_aggregate

    value = _restore_d_category_order(value)
    if resolve_payload_policy(value) != resolve_header_policy(header):
        raise PolicyError("D aggregate policy differs from its run header")
    parsed = validate_stage_d_aggregate(value)
    if parsed["plan_sha256"] != plan_sha256:
        raise ValueError("D aggregate differs from its plan owner")
    return parsed


def validate_d_decision_owner(
        conn: Any, header: Mapping[str, Any], decision_id: str,
        value: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild a D decision from its stored policy-bound plan and aggregate."""
    import json
    from .c0b2_stage_d import (
        build_d4_final_decision, build_stage_d_decision,
    )

    if resolve_payload_policy(value) != resolve_header_policy(header):
        raise PolicyError("D decision policy differs from its run header")
    phase = value.get("phase")
    keys = {"D1": "D1_OUTPUT", "D2": "D2_CHUNK",
            "D3": "D3_CONTEXT", "D4": "D4_CONFIRMATION"}
    key = keys.get(phase)
    if key is None:
        raise ValueError("D decision phase is unknown")
    row = conn.execute(
        "SELECT p.plan_hash,p.plan_json,a.aggregate_hash,a.aggregate_json "
        "FROM phase_plans p JOIN phase_aggregates a ON a.plan_key=p.plan_key "
        "WHERE p.plan_key=?", (key,)).fetchone()
    if not row:
        raise ValueError("D decision lacks its plan/aggregate owner")
    plan = validate_plan_for_header(header, DPhasePlan, json.loads(str(row[1])))
    aggregate = validate_d_aggregate_for_header(
        header, json.loads(str(row[3])), plan_sha256=str(row[0]))
    if sha256_json(plan) != row[0] or sha256_json(aggregate) != row[2]:
        raise ValueError("D decision owner hashes changed")
    if phase == "D4":
        d3 = conn.execute(
            "SELECT p.plan_hash,p.plan_json,a.aggregate_json FROM phase_plans p JOIN "
            "phase_aggregates a ON a.plan_key=p.plan_key WHERE p.plan_key='D3_CONTEXT'"
        ).fetchone()
        if not d3:
            raise ValueError("D4 decision lacks D3 merge owners")
        d3_plan = validate_plan_for_header(header, DPhasePlan, json.loads(str(d3[1])))
        d3_aggregate = validate_d_aggregate_for_header(
            header, json.loads(str(d3[2])), plan_sha256=str(d3[0]))
        expected = build_d4_final_decision(
            aggregate, plan, d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    else:
        expected = build_stage_d_decision(aggregate, plan)
    expected_id = ("stage-d-selection" if phase == "D4" or phase == "D3"
                   and expected.get("outcome") == "FINALISTS" else {
                       "D1": "stage-d-d1-selection", "D2": "stage-d-d2-selection",
                       "D3": "stage-d-d3-selection"}[phase])
    if decision_id != expected_id or dict(value) != expected:
        raise ValueError("D decision differs from its exact evidence owner")
    return expected


def _validate_stored_d_policy_lineage(conn: Any, header: Mapping[str, Any]) -> None:
    """Validate all frozen D owners without consulting nonce-bound attempt bytes."""
    import json

    keys = {"D1": "D1_OUTPUT", "D2": "D2_CHUNK",
            "D3": "D3_CONTEXT", "D4": "D4_CONFIRMATION"}
    aggregates: dict[str, tuple[str, str]] = {}
    for key, plan_hash, aggregate_hash, aggregate_raw in conn.execute(
            "SELECT plan_key,plan_hash,aggregate_hash,aggregate_json "
            "FROM phase_aggregates").fetchall():
        if key not in keys.values() or key in aggregates:
            raise PolicyError("blocked D aggregate namespace is not exact")
        plan_row = conn.execute(
            "SELECT plan_json FROM phase_plans WHERE plan_key=?", (key,)
        ).fetchone()
        if not plan_row:
            raise PolicyError("blocked D aggregate lacks its phase plan")
        plan = validate_plan_for_header(
            header, DPhasePlan, json.loads(str(plan_row[0])))
        aggregate = validate_d_aggregate_for_header(
            header, json.loads(str(aggregate_raw)), plan_sha256=str(plan_hash))
        if (plan.get("plan_key") != key
                or keys.get(aggregate.get("phase")) != key
                or canonical_json_bytes(plan).decode("utf-8") != plan_row[0]
                or canonical_json_bytes(aggregate).decode("utf-8") != aggregate_raw
                or sha256_json(plan) != plan_hash
                or sha256_json(aggregate) != aggregate_hash):
            raise PolicyError("blocked D aggregate owner changed")
        aggregates[str(key)] = (str(plan_hash), str(aggregate_hash))
    decisions = conn.execute(
        "SELECT decision_id,stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE stage='D'").fetchall()
    seen: set[str] = set()
    for decision_id, stage, parent_hash, aggregate_hash, activation, raw in decisions:
        value = json.loads(str(raw))
        expected = validate_d_decision_owner(
            conn, header, str(decision_id), value)
        key = keys[str(expected["phase"])]
        if (key in seen or key not in aggregates
                or canonical_json_bytes(expected).decode("utf-8") != raw
                or (stage, parent_hash, aggregate_hash, activation) !=
                ("D", *aggregates[key], "ACTIVATED")):
            raise PolicyError("blocked D decision owner changed")
        seen.add(key)
    if seen != set(aggregates):
        raise PolicyError("blocked D aggregate lacks its atomic decision")


def validate_d_backup_history(conn: Any, header: Mapping[str, Any]) -> None:
    """Rebuild current D backup owners from durable attempt evidence."""
    if resolve_header_policy(header) != CURRENT_POLICY:
        return
    from .c0b2_runtime_d import (
        load_stage_d_inputs, validate_inconclusive_d_terminal,
        validate_stored_d_history,
    )
    from .c0b2_runtime_f_evidence import _ReadonlyPoint

    point = _ReadonlyPoint(conn, header)
    if point.state() == "BLOCKED_PROVENANCE":
        # The generic anchor validates activations and the frozen failure
        # artifact after this policy-lineage check.  Do not load the nonce:
        # its drift may be the provenance failure being receipted (§18.3).
        _validate_stored_d_policy_lineage(conn, header)
        return
    inputs = load_stage_d_inputs(point)
    if point.state() == "INCONCLUSIVE":
        validate_inconclusive_d_terminal(point, inputs)
    else:
        validate_stored_d_history(point, inputs)


def validate_plan_for_header(
        header: Mapping[str, Any], legacy_model: type[ModelT],
        value: Mapping[str, Any],
) -> dict[str, Any]:
    """Dispatch strict phase/master plan validation by stored header identity."""
    policy = resolve_header_policy(header)
    if resolve_payload_policy(value) != policy:
        raise PolicyError("plan policy binding differs from its run header")
    if policy != CURRENT_POLICY:
        return legacy_model.model_validate(
            value, strict=True).model_dump(mode="json")
    models: dict[type[BaseModel], type[BaseModel]] = {
        DPhasePlan: DPhasePlanV2,
        FSeedPlan: FSeedPlanV2,
        FMasterPlan: FMasterPlanV2,
        AcceptancePlan: AcceptancePlanV2,
    }
    model = models.get(legacy_model)
    if model is None:
        raise ValueError("unsupported C0B-3 plan schema")
    return validate_current(model, value)


def build_stage_c_inconclusive_result(
        header: Mapping[str, Any], aggregate_sha256: str) -> dict[str, Any]:
    """Build the exact legacy/current Stage-C terminal from stored header policy."""
    value = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "C",
        "aggregate_sha256": aggregate_sha256, "reason": "no_stage_c_survivor",
    }
    if resolve_header_policy(header) != CURRENT_POLICY:
        return StageCInconclusiveArtifact.model_validate(
            value, strict=True).model_dump(mode="json")
    value.update(version="c0b3-result-v1", **policy_binding())
    return validate_current_result(value)


def build_d_inconclusive_result(
        header: Mapping[str, Any], aggregate_sha256: str, reason: str,
) -> dict[str, Any]:
    """Build an exact policy-family Stage-D inconclusive result."""
    value = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": aggregate_sha256, "reason": reason,
    }
    if resolve_header_policy(header) != CURRENT_POLICY:
        from .c0b2_public_schema import DInconclusiveResult
        return DInconclusiveResult.model_validate(
            value, strict=True).model_dump(mode="json")
    value.update(version="c0b3-result-v1", **policy_binding())
    return validate_current_result(value)


def build_completion_value(
        artifact: Mapping[str, Any], artifact_sha256: str,
        facts: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Build the exact completion decision ID/value for one recognized result."""
    outcome = artifact.get("terminal")
    value = {"outcome": outcome, "artifact_sha256": artifact_sha256, "facts": facts}
    if resolve_payload_policy(artifact) != CURRENT_POLICY:
        model = SelectedCompletion if outcome == "SELECTED" else InconclusiveCompletion
        return "c0b2-completion", model.model_validate(
            value, strict=True).model_dump(mode="json")
    validate_current_result(artifact)
    value.update(version="c0b3-completion-v1", **policy_binding())
    return COMPLETION_DECISION_ID, validate_current_completion(
        COMPLETION_DECISION_ID, value)


def require_policy_parent(
        child: Mapping[str, Any], parent: Mapping[str, Any],
        computed_parent_record_sha256: str, expected_parent_record_sha256: str,
        *, allow_stage_c_root: bool = False,
) -> None:
    """Validate a decision-record digest and the sole D1 Stage-C root exception."""
    require_policy_binding(child)
    if allow_stage_c_root:
        if (child.get("version") != "stage-d-phase-plan-v2"
                or child.get("phase") != "D1"):
            raise PolicyError("Stage-C root exception is restricted to a D1 v2 plan")
        StageCSelection.model_validate(parent, strict=True)
    else:
        require_policy_binding(parent)
    digests = (computed_parent_record_sha256, expected_parent_record_sha256)
    if any(type(value) is not str or len(value) != 64
           or any(char not in "0123456789abcdef" for char in value)
           for value in digests):
        raise PolicyError("C0B-3 parent decision-record digest is malformed")
    declared = child.get("parent_decision_sha256")
    if (computed_parent_record_sha256 != expected_parent_record_sha256
            or declared is not None and declared != expected_parent_record_sha256):
        raise PolicyError("C0B-3 parent decision-record digest changed")


def reject_legacy_plan_with_current_validator(value: Mapping[str, Any]) -> None:
    """Named fail-closed seam used by dispatch tests and runtime delegates."""
    require_policy_binding(value)
    if value.get("version") in {
            "stage-d-phase-plan-v1", "stage-f-seed-plan-v1",
            "stage-f-master-plan-v1", "stage-f-acceptance-plan-v1"}:
        raise PolicyError("legacy plan version cannot use the C0B-3 validator")
