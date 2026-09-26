"""Strict machine-artifact schemas for the public C0B-2 D/F run.

This module is pure and intentionally contains no checkpoint, filesystem, transport,
Ollama, or private-corpus access.  It implements the common B2 catalog; stage-specific
quality aggregates remain owned by B3/B4.

DISPOSITION: retain through C0B; production consumes only the frozen final selection.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, ClassVar, Literal, Mapping, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .c0b2_schema import canonical_json

Sha256: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonce: TypeAlias = Annotated[str, Field(pattern=r"^FENCE_[0-9A-F]{32}$")]
UtcRfc3339: TypeAlias = Annotated[
    str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")]
Worksheet: TypeAlias = Literal["v1", "v2"]
BudgetStage: TypeAlias = Literal["D", "F"]
Phase: TypeAlias = Literal[
    "D1", "D2", "D3", "D4", "F_SEED_1", "F_SEED_17",
    "F_SEED_20260804", "F_ACCEPTANCE",
]
PlanKey: TypeAlias = Literal[
    "C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT", "D4_CONFIRMATION",
    "F_SEED_1", "F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE",
]
PublicStage: TypeAlias = Literal["C", "D", "F"]
PublicTerminal: TypeAlias = Literal[
    "FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
    "BLOCKED_FILESYSTEM", "ABANDONED",
]

PLAN_PHASE: dict[str, str] = {
    "C": "C",
    "D1_OUTPUT": "D1",
    "D2_CHUNK": "D2",
    "D3_CONTEXT": "D3",
    "D4_CONFIRMATION": "D4",
    "F_SEED_1": "F_SEED_1",
    "F_SEED_17": "F_SEED_17",
    "F_SEED_20260804": "F_SEED_20260804",
    "F_ACCEPTANCE": "F_ACCEPTANCE",
}
PLAN_ORDER = tuple(PLAN_PHASE)
SEED_BY_PLAN = {
    "F_SEED_1": 1,
    "F_SEED_17": 17,
    "F_SEED_20260804": 20260804,
}
FAILURE_REASON_BY_TERMINAL = {
    "FAILED_SAFETY": "safety_envelope_failure",
    "BLOCKED_PROVENANCE": "provenance_identity_failure",
    "BLOCKED_BUDGET": "call_allowance_exhausted",
    "BLOCKED_FILESYSTEM": "filesystem_capability_or_integrity_failure",
    "ABANDONED": "operator_abandoned",
}
FailureArtifactReason: TypeAlias = Literal[
    "safety_envelope_failure", "provenance_identity_failure",
    "call_allowance_exhausted", "filesystem_capability_or_integrity_failure",
    "operator_abandoned",
]


class StrictModel(BaseModel):
    """Common strict/no-extra/no-coercion artifact base."""

    model_config = ConfigDict(strict=True, extra="forbid")


def _identity_text(value: str, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a nonempty exact string")


def _identity_sha256(value: str, label: str) -> None:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{label} is not lowercase SHA-256")


def _identity_int(value: int, allowed: set[int], label: str) -> None:
    if type(value) is not int or value not in allowed:
        raise ValueError(f"{label} is outside its exact identity domain")


def _identity_nonce(value: str) -> None:
    if (type(value) is not str or len(value) != 38
            or not value.startswith("FENCE_")
            or any(char not in "0123456789ABCDEF" for char in value[6:])):
        raise ValueError("nonce is outside its exact identity domain")


def sha256_json(value: Any) -> str:
    """Hash canonical JSON bytes without accepting caller-provided identities."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(canonical_json(value)).hexdigest()


def stage_d_candidate_id(model: str, model_digest: str, worksheet: str) -> str:
    _identity_text(model, "model")
    _identity_sha256(model_digest, "model digest")
    if type(worksheet) is not str or worksheet not in {"v1", "v2"}:
        raise ValueError("worksheet is outside its exact identity domain")
    return sha256_json({
        "domain": "stage-d-candidate-v1", "model": model,
        "model_digest": model_digest, "worksheet": worksheet,
    })


def stage_f_candidate_id(selection: Mapping[str, Any],
                         stage_d_decision_sha256: str) -> str:
    if not isinstance(selection, Mapping):
        raise ValueError("candidate selection must be a mapping")
    normalized = CandidateSelection.model_validate(
        dict(selection), strict=True).model_dump(mode="json")
    _identity_sha256(stage_d_decision_sha256, "Stage-D decision hash")
    return sha256_json({
        "domain": "stage-f-candidate-v1", "selection": normalized,
        "stage_d_decision_sha256": stage_d_decision_sha256,
    })


def activation_group_id(candidate_id: str, plan_key: str) -> str:
    _identity_sha256(candidate_id, "candidate ID")
    if type(plan_key) is not str or plan_key not in SEED_BY_PLAN:
        raise ValueError("activation plan key is outside its exact identity domain")
    return sha256_json({
        "candidate_id": candidate_id, "domain": "stage-f-group-v1",
        "plan_key": plan_key,
    })


def public_cell_id(*, budget_stage: str, candidate_id: str, chunk_chars: int,
                   num_ctx: int, num_predict: int, phase: str, seed: int) -> str:
    if type(budget_stage) is not str or budget_stage not in {"D", "F"}:
        raise ValueError("budget stage is outside its exact identity domain")
    _identity_sha256(candidate_id, "candidate ID")
    _identity_int(chunk_chars, {2000, 4000, 8000}, "chunk size")
    _identity_int(num_ctx, {4096, 8192, 16384}, "context size")
    _identity_int(num_predict, {1024, 2048, 3072, 4096}, "output budget")
    if (type(phase) is not str or phase not in set(PLAN_PHASE.values()) - {"C"}
            or phase[0] != budget_stage):
        raise ValueError("phase is outside its budget-stage identity domain")
    _identity_int(seed, {1, 17, 20260804}, "seed")
    return sha256_json({
        "budget_stage": budget_stage, "candidate_id": candidate_id,
        "chunk_chars": chunk_chars, "domain": "c0b2-public-cell-v1",
        "num_ctx": num_ctx, "num_predict": num_predict, "overlap": 256,
        "phase": phase, "seed": seed,
    })


def public_work_id(*, cell_id: str, chunk_index: int, chunk_sha256: str,
                   doc_id: str, document_sha256: str, nonce: str,
                   plan_key: str, request_sha256: str,
                   view_id: str | None) -> str:
    _identity_sha256(cell_id, "cell ID")
    if type(chunk_index) is not int or chunk_index < 0:
        raise ValueError("chunk index must be an exact nonnegative integer")
    _identity_sha256(chunk_sha256, "chunk hash")
    _identity_text(doc_id, "document ID")
    _identity_sha256(document_sha256, "document hash")
    _identity_nonce(nonce)
    if type(plan_key) is not str or plan_key not in PLAN_PHASE or plan_key == "C":
        raise ValueError("plan key is outside its exact identity domain")
    _identity_sha256(request_sha256, "request hash")
    if view_id is not None:
        _identity_text(view_id, "view ID")
    return sha256_json({
        "cell_id": cell_id, "chunk_index": chunk_index,
        "chunk_sha256": chunk_sha256, "doc_id": doc_id,
        "document_sha256": document_sha256, "domain": "c0b2-public-work-v1",
        "nonce": nonce, "plan_key": plan_key, "request_sha256": request_sha256,
        "view_id": view_id,
    })


def context_control_id(*, candidate_id: str, config_sha256: str, model: str,
                       model_digest: str, payload_sha256: str,
                       purpose: str) -> str:
    _identity_sha256(candidate_id, "candidate ID")
    _identity_sha256(config_sha256, "config hash")
    _identity_text(model, "model")
    _identity_sha256(model_digest, "model digest")
    _identity_sha256(payload_sha256, "payload hash")
    if type(purpose) is not str or purpose not in {
            "d3_context_16384", "d4_context_selected",
            "stage_f_candidate_context"}:
        raise ValueError("context purpose is outside its exact identity domain")
    return sha256_json({
        "candidate_id": candidate_id, "config_sha256": config_sha256,
        "domain": "c0b2-context-control-v1", "model": model,
        "model_digest": model_digest, "payload_sha256": payload_sha256,
        "purpose": purpose,
    })


def cancellation_control_id(*, candidate_id: str,
                            request_sha256: str) -> str:
    _identity_sha256(candidate_id, "candidate ID")
    _identity_sha256(request_sha256, "request hash")
    return sha256_json({
        "candidate_id": candidate_id, "chunk_index": 0,
        "domain": "c0b2-cancellation-control-v1",
        "request_sha256": request_sha256, "source_doc_id": "pos_pii_013",
    })


def health_control_id(*, candidate_id: str, nonce: str,
                      request_sha256: str) -> str:
    _identity_sha256(candidate_id, "candidate ID")
    _identity_nonce(nonce)
    _identity_sha256(request_sha256, "request hash")
    return sha256_json({
        "candidate_id": candidate_id, "chunk_index": 0,
        "domain": "c0b2-health-control-v1", "nonce": nonce,
        "request_sha256": request_sha256, "source_doc_id": "pos_pii_013",
    })


def health_work_id(*, candidate_id: str, request_sha256: str) -> str:
    _identity_sha256(candidate_id, "candidate ID")
    _identity_sha256(request_sha256, "request hash")
    return sha256_json({
        "candidate_id": candidate_id, "domain": "c0b2-health-work-v1",
        "request_sha256": request_sha256,
    })


class ExactFraction(StrictModel):
    numerator: int
    denominator: int = Field(gt=0)

    @model_validator(mode="after")
    def reduced(self) -> "ExactFraction":
        import math
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("fraction must be reduced")
        return self


class CandidateSelection(StrictModel):
    model: str = Field(min_length=1)
    model_digest: Sha256
    worksheet: Worksheet
    chunk_chars: Literal[2000, 4000, 8000]
    overlap: Literal[256]
    num_ctx: Literal[4096, 8192, 16384]
    num_predict: Literal[1024, 2048, 3072, 4096]


class DPhaseCandidate(StrictModel):
    candidate_id: Sha256
    model: str = Field(min_length=1)
    model_digest: Sha256
    worksheet: Worksheet
    chunk_chars: Literal[2000, 4000, 8000] | None
    overlap: Literal[256] | None
    num_ctx: Literal[4096, 8192, 16384] | None
    num_predict: Literal[1024, 2048, 3072, 4096] | None

    @model_validator(mode="after")
    def identity_matches(self) -> "DPhaseCandidate":
        expected = stage_d_candidate_id(self.model, self.model_digest, self.worksheet)
        if self.candidate_id != expected:
            raise ValueError("D candidate ID differs from its frozen identity")
        return self


class FPlanCandidate(StrictModel):
    candidate_id: Sha256
    model: str = Field(min_length=1)
    model_digest: Sha256
    worksheet: Worksheet
    chunk_chars: Literal[2000, 4000, 8000]
    overlap: Literal[256]
    num_ctx: Literal[4096, 8192, 16384]
    num_predict: Literal[1024, 2048, 3072, 4096]

    def selection(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"candidate_id"})


class PublicWork(StrictModel):
    stage: BudgetStage
    phase: Phase
    plan_key: PlanKey
    budget_stage: BudgetStage
    activation_group_id: Sha256 | None
    candidate_id: Sha256
    cell_id: Sha256
    work_id: Sha256
    model: str = Field(min_length=1)
    model_digest: Sha256
    worksheet: Worksheet
    doc_id: str = Field(min_length=1)
    view_id: str | None
    document_sha256: Sha256
    chunk_chars: Literal[2000, 4000, 8000]
    overlap: Literal[256]
    num_ctx: Literal[4096, 8192, 16384]
    num_predict: Literal[1024, 2048, 3072, 4096]
    seed: Literal[1, 17, 20260804]
    chunk_index: int = Field(ge=0)
    chunk_sha256: Sha256
    nonce: Nonce
    prompt_sha256: Sha256
    request_sha256: Sha256

    @model_validator(mode="after")
    def frozen_identity_matches(self) -> "PublicWork":
        if self.view_id == "":
            raise ValueError("non-null work view ID must be nonempty")
        if self.stage != self.budget_stage:
            raise ValueError("work stage and budget stage differ")
        if PLAN_PHASE.get(self.plan_key) != self.phase:
            raise ValueError("work phase and plan key differ")
        seed_plan = self.plan_key in SEED_BY_PLAN
        if self.stage == "D" and not self.plan_key.startswith("D"):
            raise ValueError("D work uses a non-D plan key")
        if self.stage == "D" and self.seed != 1:
            raise ValueError("D work must use the frozen seed 1")
        if self.stage == "F" and not self.plan_key.startswith("F"):
            raise ValueError("F work uses a non-F plan key")
        if seed_plan != (self.activation_group_id is not None):
            raise ValueError("activation group presence differs from work class")
        expected_cell = public_cell_id(
            budget_stage=self.budget_stage, candidate_id=self.candidate_id,
            chunk_chars=self.chunk_chars, num_ctx=self.num_ctx,
            num_predict=self.num_predict, phase=self.phase, seed=self.seed)
        if self.cell_id != expected_cell:
            raise ValueError("cell ID differs from the work configuration")
        expected_work = public_work_id(
            cell_id=self.cell_id, chunk_index=self.chunk_index,
            chunk_sha256=self.chunk_sha256, doc_id=self.doc_id,
            document_sha256=self.document_sha256, nonce=self.nonce,
            plan_key=self.plan_key, request_sha256=self.request_sha256,
            view_id=self.view_id)
        if self.work_id != expected_work:
            raise ValueError("work ID differs from its frozen identity")
        return self


ContextPurpose: TypeAlias = Literal[
    "d3_context_16384", "d4_context_selected", "stage_f_candidate_context"]


class ContextControl(StrictModel):
    control_id: Sha256
    kind: Literal["context_probe"]
    purpose: Literal["stage_f_candidate_context"]
    candidate_id: Sha256
    model: str = Field(min_length=1)
    model_digest: Sha256
    config_sha256: Sha256
    minimum_context_length: int = Field(gt=0)
    trigger_rule: Literal["first_http_terminal_seed1"]
    payload_sha256: Sha256

    @model_validator(mode="after")
    def identity_matches(self) -> "ContextControl":
        expected = context_control_id(
            candidate_id=self.candidate_id, config_sha256=self.config_sha256,
            model=self.model, model_digest=self.model_digest,
            payload_sha256=self.payload_sha256, purpose=self.purpose)
        if self.control_id != expected:
            raise ValueError("context control ID differs from its frozen identity")
        return self


class ContextProbeEvidence(StrictModel):
    control_id: Sha256
    purpose: ContextPurpose
    candidate_id: Sha256
    model: str = Field(min_length=1)
    model_digest: Sha256
    config_sha256: Sha256
    expected_num_ctx: int = Field(gt=0)
    observed_context_length: int = Field(gt=0)
    trigger_work_id: Sha256
    state: Literal["PASSED"]
    response_sha256: Sha256

    @model_validator(mode="after")
    def allocation_passed(self) -> "ContextProbeEvidence":
        if self.observed_context_length < self.expected_num_ctx:
            raise ValueError("passed context evidence reports an undersized allocation")
        return self


class CancellationControl(StrictModel):
    control_id: Sha256
    kind: Literal["cancellation_probe"]
    candidate_id: Sha256
    source_doc_id: Literal["pos_pii_013"]
    chunk_index: Literal[0]
    request_sha256: Sha256
    max_close_after_first_byte_ms: Literal[5000]
    health_not_before_ms: Literal[2000]

    @model_validator(mode="after")
    def identity_matches(self) -> "CancellationControl":
        if self.control_id != cancellation_control_id(
                candidate_id=self.candidate_id,
                request_sha256=self.request_sha256):
            raise ValueError("cancellation control ID differs from its frozen identity")
        return self


class HealthControl(StrictModel):
    control_id: Sha256
    kind: Literal["cancellation_health"]
    candidate_id: Sha256
    source_doc_id: Literal["pos_pii_013"]
    chunk_index: Literal[0]
    nonce: Nonce
    health_work_id: Sha256
    request_sha256: Sha256

    @model_validator(mode="after")
    def identities_match(self) -> "HealthControl":
        if self.control_id != health_control_id(
                candidate_id=self.candidate_id, nonce=self.nonce,
                request_sha256=self.request_sha256):
            raise ValueError("health control ID differs from its frozen identity")
        if self.health_work_id != health_work_id(
                candidate_id=self.candidate_id,
                request_sha256=self.request_sha256):
            raise ValueError("health work ID differs from its frozen identity")
        return self


_CANCEL_REASONS = (
    "cancel_not_observed", "cancel_after_5_seconds", "health_missing",
    "health_eventual_invalid", "health_pii_missing", "health_grounding_failure",
    "health_length_outcome", "health_channel_violation",
    "health_context_headroom_failure",
)
CancellationReason: TypeAlias = Literal[
    "cancel_not_observed", "cancel_after_5_seconds", "health_missing",
    "health_eventual_invalid", "health_pii_missing", "health_grounding_failure",
    "health_length_outcome", "health_channel_violation",
    "health_context_headroom_failure",
]


class CancellationHealthEvidence(StrictModel):
    candidate_id: Sha256
    cancel_control_id: Sha256
    cancel_attempt_id: Sha256
    cancel_state: Literal["CANCELLED_UNVERIFIED"]
    cancel_first_byte_seen: bool
    cancel_elapsed_ms: int = Field(ge=0)
    health_control_id: Sha256
    health_work_id: Sha256
    health_attempt_ids: list[Sha256] = Field(min_length=1)
    not_before_utc: UtcRfc3339
    started_at_utc: UtcRfc3339
    eventual_valid: bool
    retained_grounded_pii: bool
    authoritative_done_reason: str | None = Field(default=None, min_length=1, max_length=80)
    max_answered_prompt_eval_count: int | None = Field(default=None, ge=0)
    length_outcomes: int = Field(ge=0)
    headroom_passed: bool
    tools_empty: bool
    images_empty: bool
    unknown_message_fields_empty: bool
    schema_escape_empty: bool
    passed: bool
    failure_reasons: list[CancellationReason]

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> "CancellationHealthEvidence":
        if len(set(self.health_attempt_ids)) != len(self.health_attempt_ids):
            raise ValueError("health attempt IDs must be unique and ordered")
        if (self.authoritative_done_reason is None) == self.eventual_valid:
            raise ValueError("authoritative done reason exists exactly for valid health")
        answered = self.max_answered_prompt_eval_count is not None
        if self.eventual_valid and not answered:
            raise ValueError("valid health requires bounded HTTP answer evidence")
        if not answered and (self.headroom_passed or self.length_outcomes != 0 or not all((
                self.tools_empty, self.images_empty,
                self.unknown_message_fields_empty, self.schema_escape_empty))):
            raise ValueError("missing health cannot carry answered-response evidence")
        if self.authoritative_done_reason == "length" and self.length_outcomes == 0:
            raise ValueError("authoritative length outcome is absent from its count")
        if self.retained_grounded_pii and not self.eventual_valid:
            raise ValueError("invalid health cannot retain grounded PII")
        indices = [_CANCEL_REASONS.index(reason) for reason in self.failure_reasons]
        if indices != sorted(set(indices)):
            raise ValueError("cancellation reasons must be unique and ordered")
        pii_reasons = [reason for reason in self.failure_reasons if reason in {
            "health_pii_missing", "health_grounding_failure"}]
        pii_failure = self.eventual_valid and not self.retained_grounded_pii
        if (pii_failure and len(pii_reasons) != 1
                or not pii_failure and pii_reasons):
            raise ValueError("PII/grounding reason differs from valid health evidence")
        expected = {
            "cancel_not_observed": not self.cancel_first_byte_seen,
            "cancel_after_5_seconds": self.cancel_elapsed_ms > 5000,
            "health_missing": not answered,
            "health_eventual_invalid": answered and not self.eventual_valid,
            "health_pii_missing": "health_pii_missing" in pii_reasons,
            "health_grounding_failure": "health_grounding_failure" in pii_reasons,
            "health_length_outcome": self.length_outcomes > 0,
            "health_channel_violation": not all((
                self.tools_empty, self.images_empty,
                self.unknown_message_fields_empty, self.schema_escape_empty)),
            "health_context_headroom_failure": not self.headroom_passed,
        }
        exact_reasons = [reason for reason in _CANCEL_REASONS if expected[reason]]
        if self.failure_reasons != exact_reasons or self.passed != (not exact_reasons):
            raise ValueError("cancellation reasons differ from exact evidence facts")
        not_before = datetime.fromisoformat(
            self.not_before_utc.removesuffix("Z") + "+00:00")
        started_at = datetime.fromisoformat(
            self.started_at_utc.removesuffix("Z") + "+00:00")
        if started_at < not_before:
            raise ValueError("health started before its durable not-before time")
        return self


class FActivationGroup(StrictModel):
    group_id: Sha256
    candidate_id: Sha256
    activation_predicate: Literal["unconditional_stage_d_finalist", "seed1_qualifier"]
    first_work_id: Sha256
    last_work_id: Sha256
    planned_work_count: int = Field(gt=0)
    context_control: ContextControl | None
    cancellation_control: CancellationControl | None
    health_control: HealthControl | None


class DPhasePlan(StrictModel):
    version: Literal["stage-d-phase-plan-v1"]
    stage: Literal["D"]
    phase: Literal["D1", "D2", "D3", "D4"]
    plan_key: Literal["D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT", "D4_CONFIRMATION"]
    budget_stage: Literal["D"]
    parent_decision_sha256: Sha256
    candidates: list[DPhaseCandidate] = Field(min_length=1)
    work: list[PublicWork] = Field(min_length=1)

    _PRESENCE: ClassVar[dict[str, tuple[bool, bool, bool, bool]]] = {
        "D1": (False, False, False, False),
        "D2": (False, False, False, True),
        "D3": (True, True, False, True),
        "D4": (True, True, True, True),
    }

    @model_validator(mode="after")
    def plan_is_consistent(self) -> "DPhasePlan":
        if PLAN_PHASE[self.plan_key] != self.phase:
            raise ValueError("D plan phase and key differ")
        ids = [row.candidate_id for row in self.candidates]
        if len(set(ids)) != len(ids) or len({row.work_id for row in self.work}) != len(self.work):
            raise ValueError("D plan identities must be unique")
        presence = self._PRESENCE[self.phase]
        for row in self.candidates:
            actual = tuple(value is not None for value in (
                row.chunk_chars, row.overlap, row.num_ctx, row.num_predict))
            if actual != presence:
                raise ValueError("D candidate carries factors not selected by its phase")
        candidate_map = {row.candidate_id: row for row in self.candidates}
        for item in self.work:
            candidate = candidate_map.get(item.candidate_id)
            if (candidate is None or item.stage != "D" or item.phase != self.phase
                    or item.plan_key != self.plan_key
                    or (item.model, item.model_digest, item.worksheet) !=
                    (candidate.model, candidate.model_digest, candidate.worksheet)):
                raise ValueError("D work does not belong to its phase candidate")
        return self


class FSeedPlan(StrictModel):
    version: Literal["stage-f-seed-plan-v1"]
    stage: Literal["F"]
    phase: Literal["F_SEED_1", "F_SEED_17", "F_SEED_20260804"]
    plan_key: Literal["F_SEED_1", "F_SEED_17", "F_SEED_20260804"]
    budget_stage: Literal["F"]
    parent_decision_sha256: Sha256
    candidates: list[FPlanCandidate] = Field(min_length=1, max_length=3)
    work: list[PublicWork] = Field(min_length=1)
    groups: list[FActivationGroup] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def frozen_groups_partition_work(self) -> "FSeedPlan":
        if self.phase != self.plan_key:
            raise ValueError("F seed plan phase and key differ")
        seed = SEED_BY_PLAN[self.plan_key]
        candidates = {row.candidate_id: row for row in self.candidates}
        ids = list(candidates)
        if len(ids) != len(self.candidates) or [row.candidate_id for row in self.groups] != ids:
            raise ValueError("F groups must match candidate order exactly")
        if len({row.work_id for row in self.work}) != len(self.work):
            raise ValueError("F work IDs must be unique")
        offset = 0
        expected_document_order: list[str] | None = None
        for group in self.groups:
            candidate = candidates[group.candidate_id]
            expected_group = activation_group_id(group.candidate_id, self.plan_key)
            if group.group_id != expected_group:
                raise ValueError("F group ID differs from its frozen identity")
            expected_predicate = (
                "unconditional_stage_d_finalist" if seed == 1 else "seed1_qualifier")
            if group.activation_predicate != expected_predicate:
                raise ValueError("F group activation predicate differs from its seed")
            controls = (group.context_control, group.cancellation_control,
                        group.health_control)
            if (seed == 1) != all(control is not None for control in controls):
                raise ValueError("F controls exist exactly for seed-1 groups")
            if seed != 1 and any(control is not None for control in controls):
                raise ValueError("later seed groups cannot contain controls")
            if any(control is not None and control.candidate_id != group.candidate_id
                   for control in controls):
                raise ValueError("F control candidate differs from its group")
            if (group.context_control is not None
                    and (group.context_control.model,
                         group.context_control.model_digest,
                         group.context_control.minimum_context_length) !=
                    (candidate.model, candidate.model_digest, candidate.num_ctx)):
                raise ValueError("F context control differs from its candidate")
            end = offset + group.planned_work_count
            rows = self.work[offset:end]
            if (len(rows) != group.planned_work_count or not rows
                    or rows[0].work_id != group.first_work_id
                    or rows[-1].work_id != group.last_work_id):
                raise ValueError("F group bounds differ from its contiguous work")
            for item in rows:
                if (item.stage != "F" or item.plan_key != self.plan_key
                        or item.phase != self.phase or item.seed != seed
                        or item.activation_group_id != group.group_id
                        or item.candidate_id != group.candidate_id
                        or (item.model, item.model_digest, item.worksheet,
                            item.chunk_chars, item.overlap, item.num_ctx,
                            item.num_predict) !=
                           (candidate.model, candidate.model_digest,
                            candidate.worksheet, candidate.chunk_chars,
                            candidate.overlap, candidate.num_ctx,
                            candidate.num_predict)):
                    raise ValueError("F work differs from its group/candidate")
            document_order: list[str] = []
            last_doc: str | None = None
            last_chunk = -1
            for item in rows:
                if item.doc_id != last_doc:
                    if item.doc_id in document_order or item.chunk_index != 0:
                        raise ValueError(
                            "F group documents/chunks differ from manifest order")
                    document_order.append(item.doc_id)
                    last_doc = item.doc_id
                    last_chunk = 0
                else:
                    last_chunk += 1
                    if item.chunk_index != last_chunk:
                        raise ValueError("F group chunk indices are not ascending")
            if len(document_order) != 72:
                raise ValueError("F seed group must cover exactly 72 documents")
            if expected_document_order is None:
                expected_document_order = document_order
            elif document_order != expected_document_order:
                raise ValueError("F groups differ in manifest document order")
            if (group.health_control is not None
                    and group.health_control.nonce in {item.nonce for item in rows}):
                raise ValueError("health nonce reuses a scored-work nonce")
            offset = end
        if offset != len(self.work):
            raise ValueError("F groups do not partition plan work")
        for candidate in self.candidates:
            expected = stage_f_candidate_id(
                candidate.selection(), self.parent_decision_sha256)
            if candidate.candidate_id != expected:
                raise ValueError("F candidate ID differs from its D parent")
        return self


class FSeedPlanEnvelope(StrictModel):
    plan_sha256: Sha256
    payload: FSeedPlan

    @model_validator(mode="after")
    def hash_matches(self) -> "FSeedPlanEnvelope":
        if self.plan_sha256 != sha256_json(self.payload):
            raise ValueError("F seed plan envelope hash differs from its payload")
        return self


class AcceptanceTemplatePayload(StrictModel):
    version: Literal["stage-f-acceptance-plan-v1"]
    stage: Literal["F"]
    phase: Literal["F_ACCEPTANCE"]
    plan_key: Literal["F_ACCEPTANCE"]
    budget_stage: Literal["F"]
    parent_decision_sha256: None
    candidates: list[FPlanCandidate] = Field(min_length=1, max_length=1)
    work: list[PublicWork] = Field(min_length=44, max_length=44)

    @model_validator(mode="after")
    def exact_c44(self) -> "AcceptanceTemplatePayload":
        candidate = self.candidates[0]
        if (len({row.doc_id for row in self.work}) != 44
                or len({row.work_id for row in self.work}) != 44):
            raise ValueError("acceptance template must cover 44 unique C documents")
        for item in self.work:
            if (item.plan_key != "F_ACCEPTANCE" or item.phase != "F_ACCEPTANCE"
                    or item.stage != "F" or item.seed != 1
                    or item.activation_group_id is not None
                    or item.candidate_id != candidate.candidate_id
                    or item.chunk_index != 0
                    or (item.model, item.model_digest, item.worksheet,
                        item.chunk_chars, item.overlap, item.num_ctx,
                        item.num_predict) !=
                       (candidate.model, candidate.model_digest,
                        candidate.worksheet, candidate.chunk_chars,
                        candidate.overlap, candidate.num_ctx,
                        candidate.num_predict)):
                raise ValueError("acceptance template work differs from frozen C44")
        return self


class AcceptanceTemplateEnvelope(StrictModel):
    template_sha256: Sha256
    candidate_id: Sha256
    payload: AcceptanceTemplatePayload

    @model_validator(mode="after")
    def identity_matches(self) -> "AcceptanceTemplateEnvelope":
        if self.template_sha256 != sha256_json(self.payload):
            raise ValueError("acceptance template hash differs from its payload")
        if self.candidate_id != self.payload.candidates[0].candidate_id:
            raise ValueError("acceptance envelope candidate differs from its payload")
        return self


class FMasterPlan(StrictModel):
    version: Literal["stage-f-master-plan-v1"]
    stage: Literal["F"]
    budget_stage: Literal["F"]
    parent_decision_sha256: Sha256
    master_manifest_sha256: Sha256
    base_candidate_order: list[Sha256] = Field(min_length=1, max_length=3)
    seed_order: list[Literal[1, 17, 20260804]] = Field(min_length=3, max_length=3)
    plans: list[FSeedPlanEnvelope] = Field(min_length=3, max_length=3)
    acceptance_templates: list[AcceptanceTemplateEnvelope] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def complete_f_tree(self) -> "FMasterPlan":
        if self.seed_order != [1, 17, 20260804]:
            raise ValueError("F master seed order differs from frozen seed order")
        if tuple(row.payload.plan_key for row in self.plans) != tuple(SEED_BY_PLAN):
            raise ValueError("F master seed plans differ from frozen seed order")
        if len(set(self.base_candidate_order)) != len(self.base_candidate_order):
            raise ValueError("F base candidate order must be unique")
        for envelope in self.plans:
            payload = envelope.payload
            if (payload.parent_decision_sha256 != self.parent_decision_sha256
                    or [row.candidate_id for row in payload.candidates]
                    != self.base_candidate_order):
                raise ValueError("F seed plan differs from master candidates/parent")
        template_ids = [row.candidate_id for row in self.acceptance_templates]
        if template_ids != self.base_candidate_order:
            raise ValueError("F acceptance templates must cover base candidates in order")
        base_candidates = {
            row.candidate_id: row.model_dump(mode="json")
            for row in self.plans[0].payload.candidates
        }
        template_document_order: list[str] | None = None
        seed_nonces = {
            item.nonce for plan in self.plans for item in plan.payload.work
        }
        health_nonces = {
            group.health_control.nonce
            for group in self.plans[0].payload.groups
            if group.health_control is not None
        }
        if (len(health_nonces) != len(self.plans[0].payload.groups)
                or health_nonces & seed_nonces):
            raise ValueError("health nonce collides with F seed work")
        for envelope in self.acceptance_templates:
            template_candidate = envelope.payload.candidates[0].model_dump(mode="json")
            if template_candidate != base_candidates[envelope.candidate_id]:
                raise ValueError("F acceptance candidate differs from seed plans")
            document_order = [item.doc_id for item in envelope.payload.work]
            if template_document_order is None:
                template_document_order = document_order
            elif document_order != template_document_order:
                raise ValueError("F acceptance templates differ in C44 order")
            if any(item.nonce in seed_nonces for item in envelope.payload.work):
                raise ValueError("acceptance template reuses an F seed nonce")
            if any(item.nonce in health_nonces for item in envelope.payload.work):
                raise ValueError("acceptance template reuses a health nonce")
        return self


class AcceptancePlan(StrictModel):
    version: Literal["stage-f-acceptance-plan-v1"]
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
    def exact_c44(self) -> "AcceptancePlan":
        raw = self.model_dump(mode="json")
        template = {
            "version": raw["version"], "stage": raw["stage"],
            "phase": raw["phase"], "plan_key": raw["plan_key"],
            "budget_stage": raw["budget_stage"], "parent_decision_sha256": None,
            "candidates": raw["candidates"], "work": raw["work"],
        }
        AcceptanceTemplatePayload.model_validate(template, strict=True)
        if self.template_sha256 != sha256_json(template):
            raise ValueError("acceptance plan differs from its frozen template")
        return self


class PlanActivation(StrictModel):
    version: Literal["c0b2-plan-activation-v1"]
    run_id: str = Field(min_length=1)
    budget_stage: BudgetStage
    plan_key: PlanKey
    plan_sha256: Sha256
    parent_decision_sha256: Sha256
    state: Literal["ACTIVATED"]
    activated_group_ids: list[Sha256]
    evidence_sha256: Sha256 | None

    @model_validator(mode="after")
    def activation_matches_plan_class(self) -> "PlanActivation":
        if self.plan_key.startswith("D"):
            if self.budget_stage != "D" or self.activated_group_ids or self.evidence_sha256:
                raise ValueError("D plan activation cannot carry F group evidence")
        elif self.plan_key == "F_SEED_1":
            if (self.budget_stage != "F" or not self.activated_group_ids
                    or self.evidence_sha256 is not None):
                raise ValueError("F seed-1 activation has invalid group evidence")
        elif self.plan_key in {"F_SEED_17", "F_SEED_20260804"}:
            if (self.budget_stage != "F" or not self.activated_group_ids
                    or self.evidence_sha256 is None):
                raise ValueError("later F activation requires seed-1 evidence")
        elif self.plan_key == "F_ACCEPTANCE":
            if (self.budget_stage != "F" or self.activated_group_ids
                    or self.evidence_sha256 is None):
                raise ValueError("acceptance activation requires final F evidence")
        else:
            raise ValueError("legacy C has no plan-activation object")
        if len(set(self.activated_group_ids)) != len(self.activated_group_ids):
            raise ValueError("activated group IDs must be unique")
        return self


class FSelectedResult(StrictModel):
    version: Literal["c0b2-result-v1"]
    terminal: Literal["SELECTED"]
    stage: Literal["F"]
    master_manifest_sha256: Sha256
    stage_c_selection_sha256: Sha256
    stage_d_decision_sha256: Sha256
    stage_f_aggregate_sha256: Sha256
    provisional_decision_sha256: Sha256
    acceptance_plan_sha256: Sha256
    acceptance_aggregate_sha256: Sha256
    selection: CandidateSelection


FInconclusiveReason: TypeAlias = Literal[
    "no_seed1_qualifier", "no_all_seed_qualifier", "ranking_not_decisive",
    "complete_corpus_acceptance_failed",
]


class FInconclusiveResult(StrictModel):
    version: Literal["c0b2-result-v1"]
    terminal: Literal["INCONCLUSIVE"]
    stage: Literal["F"]
    aggregate_sha256: Sha256
    reason: FInconclusiveReason


DInconclusiveReason: TypeAlias = Literal[
    "no_d1_output_budget_survivor", "no_d2_chunk_survivor",
    "no_d3_context_survivor", "no_d4_confirmation_finalist",
]


class DInconclusiveResult(StrictModel):
    version: Literal["c0b2-result-v1"]
    terminal: Literal["INCONCLUSIVE"]
    stage: Literal["D"]
    aggregate_sha256: Sha256
    reason: DInconclusiveReason


class CompletionGates(StrictModel):
    strict_validity: Literal[True]
    first_pass_invalid_bound: Literal[True]
    raw_grounding: Literal[True]
    retained_grounding: Literal[True]
    category_recall: Literal[True]
    false_positive_bound: Literal[True]
    injection_robustness: Literal[True]
    boundary_identifiers: Literal[True]
    truncation_complete: Literal[True]
    context_channel_cancellation_provenance_safety: Literal[True]


class SelectedFacts(StrictModel):
    accepted_document_count: Literal[166]
    gates: CompletionGates


class DeterministicStopFacts(StrictModel):
    deterministic_stop: Literal[True]
    reason: str = Field(min_length=1)


class SelectedCompletion(StrictModel):
    outcome: Literal["SELECTED"]
    artifact_sha256: Sha256
    facts: SelectedFacts


class InconclusiveCompletion(StrictModel):
    outcome: Literal["INCONCLUSIVE"]
    artifact_sha256: Sha256
    facts: DeterministicStopFacts


class FailureEvidence(StrictModel):
    version: Literal["c0b2-failure-evidence-v1"]
    terminal: PublicTerminal
    stage: PublicStage
    reason_code: FailureArtifactReason
    attempt_id: Sha256 | None
    control_id: Sha256 | None
    plan_key: PlanKey | None

    @model_validator(mode="after")
    def reason_matches_terminal(self) -> "FailureEvidence":
        if FAILURE_REASON_BY_TERMINAL[self.terminal] != self.reason_code:
            raise ValueError("failure evidence reason differs from terminal")
        if self.plan_key is not None:
            plan_stage = ("C" if self.plan_key == "C" else self.plan_key[0])
            if self.stage != plan_stage:
                raise ValueError("failure evidence plan key differs from its stage")
        return self


class FailureArtifact(StrictModel):
    version: Literal["c0b2-failure-v1"]
    terminal: PublicTerminal
    stage: PublicStage
    reason: FailureArtifactReason
    evidence_sha256: Sha256
    charged_call_total: int = Field(ge=0)

    @model_validator(mode="after")
    def reason_matches_terminal(self) -> "FailureArtifact":
        if FAILURE_REASON_BY_TERMINAL[self.terminal] != self.reason:
            raise ValueError("failure artifact reason differs from terminal")
        return self


class BackupPlan(StrictModel):
    plan_key: PlanKey
    plan_sha256: Sha256
    activation_sha256: Sha256 | None

    @model_validator(mode="after")
    def activation_matches_legacy_status(self) -> "BackupPlan":
        if (self.activation_sha256 is None) != (self.plan_key == "C"):
            raise ValueError("only the legacy C plan lacks activation evidence")
        return self


BackupState: TypeAlias = Literal[
    "PAUSED_STAGE_BOUNDARY", "SELECTED", "INCONCLUSIVE", "FAILED_SAFETY",
    "BLOCKED_PROVENANCE", "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED",
]


class BackupAnchor(StrictModel):
    version: Literal["c0b2-backup-anchor-v1"]
    run_id: str = Field(min_length=1)
    active_stage: PublicStage
    state: BackupState
    f_master_plan_sha256: Sha256 | None
    plans: list[BackupPlan] = Field(min_length=1)
    aggregate_sha256: Sha256 | None
    decision_or_artifact_sha256: Sha256
    charged_call_total: int = Field(ge=0)

    @model_validator(mode="after")
    def anchor_is_unambiguous(self) -> "BackupAnchor":
        if (self.f_master_plan_sha256 is None) == (self.active_stage == "F"):
            raise ValueError("F master hash exists exactly when F is active")
        plan_keys = [row.plan_key for row in self.plans]
        if len(set(plan_keys)) != len(plan_keys) or plan_keys[0] != "C":
            raise ValueError("backup plans must be unique and begin at C")
        if any(key.startswith("F") for key in plan_keys):
            prefix = ["C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT"]
            offset = len(prefix)
            if plan_keys[:offset] != prefix:
                raise ValueError("F backup lineage skipped a required D plan")
            if len(plan_keys) > offset and plan_keys[offset] == "D4_CONFIRMATION":
                offset += 1
            f_keys = plan_keys[offset:]
            f_order = [
                "F_SEED_1", "F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE"]
            if not f_keys or f_keys != f_order[:len(f_keys)]:
                raise ValueError("F backup lineage skipped an activated plan")
        else:
            d_order = ["C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT",
                       "D4_CONFIRMATION"]
            if plan_keys != d_order[:len(plan_keys)]:
                raise ValueError("D backup lineage skipped an activated plan")
        last_plan_stage = (
            "C" if self.plans[-1].plan_key == "C" else self.plans[-1].plan_key[0])
        if last_plan_stage != self.active_stage:
            raise ValueError("backup active stage differs from its latest plan")
        if (self.state == "SELECTED"
                and (self.active_stage != "F"
                     or plan_keys[-1] != "F_ACCEPTANCE")):
            raise ValueError("selected backup lacks activated F acceptance")
        if self.state == "PAUSED_STAGE_BOUNDARY":
            legal_boundary = (
                self.active_stage == "C" and plan_keys == ["C"]
                or self.active_stage == "D"
                and plan_keys[-1] in {"D3_CONTEXT", "D4_CONFIRMATION"}
            )
            if not legal_boundary:
                raise ValueError("backup is not at a legal C/D stage boundary")
        if self.state == "INCONCLUSIVE" and self.active_stage == "F" and \
                plan_keys[-1] not in {
                    "F_SEED_1", "F_SEED_20260804", "F_ACCEPTANCE"}:
            raise ValueError("F inconclusive backup ends at an impossible plan")
        quality_terminal = self.state in {"SELECTED", "INCONCLUSIVE"}
        boundary = self.state == "PAUSED_STAGE_BOUNDARY"
        if (quality_terminal or boundary) and self.aggregate_sha256 is None:
            raise ValueError("quality terminal/boundary requires aggregate evidence")
        return self


class BackupReceipt(StrictModel):
    version: Literal["c0b2-backup-receipt-v1"]
    anchor_sha256: Sha256
    snapshot_run_relative_path: str = Field(min_length=1)
    snapshot_sha256: Sha256
    snapshot_size_bytes: int = Field(gt=0)
    integrity_check: Literal["ok"]
    foreign_key_violations: Literal[0]
    created_at_utc: UtcRfc3339

    @field_validator("snapshot_run_relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("snapshot path must be canonical and run-relative")
        if str(path) != value:
            raise ValueError("snapshot path must use canonical POSIX spelling")
        return value

    @field_validator("created_at_utc")
    @classmethod
    def valid_created_at(cls, value: str) -> str:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        return value


class BackupStatus(StrictModel):
    required: bool
    receipt_present: bool
    anchor_sha256: Sha256 | None
    snapshot_sha256: Sha256 | None

    @model_validator(mode="after")
    def status_is_consistent(self) -> "BackupStatus":
        if not self.required:
            if self.receipt_present or self.anchor_sha256 or self.snapshot_sha256:
                raise ValueError("inapplicable backup cannot expose receipt hashes")
        elif self.anchor_sha256 is None:
            raise ValueError("required backup must expose its anchor hash")
        if self.receipt_present != (self.snapshot_sha256 is not None):
            raise ValueError("receipt presence must match snapshot identity")
        return self


ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_artifact(model: type[ModelT], value: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate and return the canonical JSON-compatible object."""
    return model.model_validate(value, strict=True).model_dump(mode="json")
