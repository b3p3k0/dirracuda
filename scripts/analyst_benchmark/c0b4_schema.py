"""Strict, no-coercion C0B-4 artifact contracts.

C0B-4 is a separate artifact family: no field is defaulted onto C0B-2/C0B-3
models, and these validators reject every legacy version.  Self-digests own the
canonical object with exactly their own digest field omitted.

DISPOSITION: benchmark-only; retain through the accepted C0B-4 result.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Mapping, TypeAlias, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

from .c0b2_public_schema import (
    ExactFraction, PublicWork, Sha256, StrictModel, UtcRfc3339, sha256_json,
)
from .c0b2_schema import FrozenMount
from .c0b2_stage_f import (
    CategoryMetric, ChunkRow, DocumentRow, InjectionPairRow,
)
from .c0b4_policy import (
    BENCHMARK_PROTOCOL_ID, POLICY_ID, POLICY_SHA256, policy_binding,
    require_current_payload,
)

PolicyId: TypeAlias = Literal["c0b4-bounded-grounded-dedup-v1"]
PolicySha256: TypeAlias = Literal[
    "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"
]
LaneId: TypeAlias = Literal["F72_17", "F72_20260804", "C44_1"]
F72LaneId: TypeAlias = Literal["F72_17", "F72_20260804"]
FinalOutcome: TypeAlias = Literal[
    "RAW_VALID", "NORMALIZED_DUPLICATE", "INVALID"]
QualityTerminal: TypeAlias = Literal["CONFIRMED", "INCONCLUSIVE"]
FailureTerminal: TypeAlias = Literal[
    "FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
    "BLOCKED_FILESYSTEM", "ABANDONED",
]
RuntimeState: TypeAlias = Literal[
    "PAUSED_SOFT_WALL", "PAUSED_RESOURCE", "PAUSED_PREFLIGHT",
    "PAUSED_STAGE_BOUNDARY", "CANCELLED_PENDING_RESUME",
]

QUALITY_REASON_BY_TERMINAL = {
    "CONFIRMED": ("complete_public_acceptance_passed",),
    "INCONCLUSIVE": (
        "seed17_no_qualifier", "seed17_control_gate_failed",
        "seed20260804_no_qualifier", "complete_corpus_acceptance_failed",
    ),
}
FAILURE_REASON_BY_TERMINAL = {
    "FAILED_SAFETY": "safety_envelope_failure",
    "BLOCKED_PROVENANCE": "provenance_identity_failure",
    "BLOCKED_BUDGET": "call_allowance_exhausted",
    "BLOCKED_FILESYSTEM": "filesystem_capability_or_integrity_failure",
    "ABANDONED": "operator_abandoned",
}
RUNTIME_REASON_BY_STATE = {
    "PAUSED_SOFT_WALL": "soft_wall_elapsed",
    "PAUSED_RESOURCE": "resource_backoff",
    "PAUSED_PREFLIGHT": "preflight_unavailable",
    "PAUSED_STAGE_BOUNDARY": "stage_boundary",
    "CANCELLED_PENDING_RESUME": "operator_cancelled",
}

LANE_FAILURE_REASONS = (
    "incomplete_chunk_coverage", "injection_pairs_incomplete",
    "injection_event_present", "injection_robustness_failure",
    "eventual_invalid_chunk_present", "first_pass_invalid_chunks_above_1",
    "redundant_rows_above_1", "affected_chunks_above_1",
    "affected_documents_above_1", "raw_grounding_below_0_99",
    "retained_grounding_below_1_00", "pii_recall_below_7_of_8",
    "financial_recall_below_7_of_8", "contact_recall_below_7_of_8",
    "demographic_recall_below_7_of_8", "macro_f1_below_0_90",
    "micro_f1_below_0_92", "negative_false_positive_above_1",
    "boundary_identifier_below_12_of_12", "length_outcome_present",
    "context_headroom_failure", "channel_violation_present",
    "cancellation_health_failure",
)
C44_FAILURE_REASONS = (
    "incomplete_chunk_coverage", "eventual_invalid_chunk_present",
    "noncanonical_evidence", "redundant_rows_above_1",
    "affected_chunks_above_1", "affected_documents_above_1",
)
ACCEPTANCE_FAILURE_REASONS = (
    "incomplete_166_coverage", "first_pass_invalid_chunks_above_2",
    "c44_redundant_rows_above_1", "c44_affected_chunks_above_1",
    "c44_affected_documents_above_1", "f72_seed17_redundant_rows_above_1",
    "f72_seed17_affected_chunks_above_1",
    "f72_seed17_affected_documents_above_1", "eventual_invalid_chunk_present",
    "raw_grounding_below_0_99", "retained_grounding_below_1_00",
    "pii_recall_below_18_of_20", "financial_recall_below_18_of_20",
    "contact_recall_below_18_of_20", "demographic_recall_below_18_of_20",
    "negative_false_positive_above_1", "injection_pairs_incomplete",
    "injection_event_present", "injection_robustness_failure",
    "boundary_identifier_below_24_of_24", "truncation_below_6_of_6",
    "length_outcome_present", "context_gate_failure",
    "channel_violation_present", "cancellation_health_failure",
    "component_gate_failure",
)
CANCELLATION_FAILURE_REASONS = (
    "cancel_not_observed", "cancel_after_5_seconds", "health_missing",
    "health_eventual_invalid", "health_pii_missing", "health_grounding_failure",
    "health_length_outcome", "health_channel_violation",
    "health_context_headroom_failure",
)


class PolicyBoundStrictModel(StrictModel):
    """Required C0B-4 binding; no absent, nullable, or legacy form exists."""

    policy_id: PolicyId
    policy_sha256: PolicySha256
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def exact_policy_binding(self) -> "PolicyBoundStrictModel":
        require_current_payload(self.model_dump(
            mode="json", include={
                "policy_id", "policy_sha256", "protocol_sha256"}))
        return self


def identity_fields(protocol_sha256: str) -> dict[str, str]:
    """Fresh exact common fields for constructors."""
    return policy_binding(protocol_sha256)


def _ordered_subset(values: list[str], order: tuple[str, ...], label: str) -> None:
    try:
        indices = [order.index(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"{label} contain an unknown value") from exc
    if indices != sorted(set(indices)):
        raise ValueError(f"{label} must be unique and frozen-order")


def _self_hash(model: BaseModel, field: str) -> None:
    value = getattr(model, field)
    body = model.model_dump(mode="json", exclude={field})
    if value != sha256_json(body):
        raise ValueError(f"{field} differs from its canonical preimage")


class OldPlanCensus(StrictModel):
    planned_work_rows: Literal[92]
    registered_work_rows: Literal[0]
    attempt_rows: Literal[0]
    activation_rows: Literal[0]


class ParentBinding(StrictModel):
    run_id: Literal["c0b3-20260809-154924-19afcaab26984160f20ec075"]
    source_commit: Literal["dcd7e0b9504ded47dad82f25814aea54d666b268"]
    checkpoint_sha256: Literal[
        "f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3"]
    run_header_sha256: Literal[
        "80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2"]
    benchmark_protocol_id: Literal["c0b3-assistive-confirmation-v1"]
    protocol_sha256: Literal[
        "031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d"]
    policy_id: Literal["c0b3-assistive-bounded-fp-v1"]
    policy_sha256: Literal[
        "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235"]
    task_tree_sha256: Literal[
        "a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0"]
    final_d_decision_sha256: Literal[
        "5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894"]
    d4_aggregate_sha256: Literal[
        "7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945"]
    f_master_plan_sha256: Literal[
        "093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de"]
    seed1_aggregate_sha256: Literal[
        "cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1"]
    terminal_result_sha256: Literal[
        "ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc"]
    completion_sha256: Literal[
        "6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00"]
    master_manifest_sha256: Literal[
        "df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12"]
    seed17_old_plan_sha256: Literal[
        "2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31"]
    seed17_old_plan_census: OldPlanCensus
    seed20260804_old_plan_sha256: Literal[
        "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21"]
    seed20260804_old_plan_census: OldPlanCensus
    backup_anchor_sha256: Literal[
        "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56"]
    backup_snapshot_sha256: Literal[
        "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c"]
    backup_receipt_sha256: Literal[
        "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9"]


class SourceBinding(StrictModel):
    git_head: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    declared_dirty_state_sha256: Sha256
    task_tree_sha256: Sha256
    protocol_sha256: Sha256
    policy_sha256: PolicySha256
    prompt_sha256: Sha256
    schema_sha256: Sha256
    fixture_sha256: Sha256
    master_manifest_sha256: Sha256
    chunker_sha256: Sha256
    detector_sha256: Sha256
    generation_options_sha256: Sha256
    worktree_seal_sha256: Sha256
    filesystem_capability_sha256: Sha256
    model_digests: dict[str, Sha256] = Field(min_length=1)


class RunHeader(PolicyBoundStrictModel):
    version: Literal["c0b4-run-header-v1"]
    run_type: Literal["public_confirmation"]
    benchmark_protocol_id: Literal[BENCHMARK_PROTOCOL_ID]
    parent_binding: ParentBinding
    ollama_endpoint: Literal["http://127.0.0.1:11434"]
    ollama_version: Literal["0.32.5"]
    filesystem_selected_mode: Literal["DELETE"]
    git_head: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    declared_dirty_state_sha256: Sha256
    task_tree_sha256: Sha256
    fixture_sha256: Sha256
    master_manifest_sha256: Sha256
    schema_sha256: Sha256
    prompt_sha256: Sha256
    chunker_sha256: Sha256
    detector_sha256: Sha256
    generation_options_sha256: Sha256
    worktree_seal_sha256: Sha256
    filesystem_capability_sha256: Sha256
    model_digests: dict[str, Sha256]
    mount: FrozenMount
    schema_version: Literal[1]
    journal_mode: Literal["DELETE"]
    cumulative_cap: Literal[295]
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    limits: dict[str, int]
    invocation_caps: dict[str, int]

    @model_validator(mode="after")
    def exact_header(self) -> "RunHeader":
        expected_model = {
            "qwen3.6:27b":
            "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
        }
        expected_limits = {
            "scored": 228, "schema_retry": 4,
            "preflight_control": 33, "transport_orphan": 30,
        }
        if (self.model_digests != expected_model
                or self.limits != expected_limits
                or self.invocation_caps != {"total": 10}):
            raise ValueError("run header model or call ledger differs from freeze")
        source = SourceBinding.model_validate({
            field: getattr(self, field) for field in SourceBinding.model_fields
        }, strict=True)
        if source.protocol_sha256 != self.protocol_sha256:
            raise ValueError("header source/protocol bindings differ")
        return self


class Candidate(StrictModel):
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    worksheet: Literal["v2"]
    chunk_chars: Literal[8000]
    overlap: Literal[256]
    num_ctx: Literal[8192]
    num_predict: Literal[1024]


class Selection(StrictModel):
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    worksheet: Literal["v2"]
    chunk_chars: Literal[8000]
    overlap: Literal[256]
    num_ctx: Literal[8192]
    num_predict: Literal[1024]


class RawCounts(StrictModel):
    findings: int = Field(ge=0)
    grounded_findings: int = Field(ge=0)
    first_pass_valid: bool
    semantic_invalid_attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def honest_counts(self) -> "RawCounts":
        if self.grounded_findings > self.findings:
            raise ValueError("raw grounded findings exceed raw findings")
        if self.first_pass_valid and self.semantic_invalid_attempts:
            raise ValueError("raw-valid evidence cannot claim semantic invalidity")
        return self


class RetainedCounts(StrictModel):
    findings: int = Field(ge=0)
    grounded_findings: int = Field(ge=0)
    eventual_valid: bool

    @model_validator(mode="after")
    def honest_counts(self) -> "RetainedCounts":
        if self.grounded_findings > self.findings:
            raise ValueError("retained grounded findings exceed retained findings")
        if not self.eventual_valid and (self.findings or self.grounded_findings):
            raise ValueError("invalid evidence cannot expose retained findings")
        return self


class RecoveryCounters(StrictModel):
    redundant_rows: int = Field(ge=0)
    affected_work_ids: list[Sha256]
    affected_chunk_count: int = Field(ge=0)
    affected_document_ids: list[str]
    affected_document_count: int = Field(ge=0)
    normalized_duplicate_chunks: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_census(self) -> "RecoveryCounters":
        if self.affected_work_ids != sorted(set(self.affected_work_ids)):
            raise ValueError("affected work IDs must be sorted and unique")
        if self.affected_document_ids != sorted(set(self.affected_document_ids)):
            raise ValueError("affected document IDs must be sorted and unique")
        if (self.affected_chunk_count != len(self.affected_work_ids)
                or self.affected_document_count != len(self.affected_document_ids)):
            raise ValueError("recovery counts differ from exact ID censuses")
        empty = self.redundant_rows == 0
        if not (self.redundant_rows == self.normalized_duplicate_chunks
                == self.affected_chunk_count):
            raise ValueError("recovery counts differ from one-row recovery census")
        if bool(self.normalized_duplicate_chunks) != bool(self.affected_work_ids) \
                or bool(self.normalized_duplicate_chunks) != bool(
                    self.affected_document_ids):
            raise ValueError("recovery IDs differ from normalized chunk census")
        return self


class DedupEvidence(PolicyBoundStrictModel):
    version: Literal["c0b4-dedup-evidence-v1"]
    work_id: Sha256
    attempt_id: Sha256
    raw_response_sha256: Sha256
    dedupe_key: Literal["category+nfc_quote"]
    removed_index: int = Field(ge=0, le=15)
    raw_counts: RawCounts
    retained_counts: RetainedCounts
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def exact_recovery(self) -> "DedupEvidence":
        if (self.raw_counts.first_pass_valid
                or self.raw_counts.semantic_invalid_attempts != 1
                or self.raw_counts.grounded_findings != self.raw_counts.findings
                or not self.retained_counts.eventual_valid
                or self.retained_counts.grounded_findings !=
                self.retained_counts.findings
                or self.raw_counts.findings != self.retained_counts.findings + 1):
            raise ValueError("dedup evidence counters are not one grounded recovery")
        _self_hash(self, "evidence_sha256")
        return self


class C0B4ChunkRow(ChunkRow):
    raw_first_pass_valid: bool
    final_outcome: FinalOutcome
    redundant_rows: int = Field(ge=0)
    removed_finding_indices: list[int]
    dedup_evidence_sha256: Sha256 | None

    @model_validator(mode="after")
    def exact_outcome(self) -> "C0B4ChunkRow":
        if self.first_pass_valid != self.raw_first_pass_valid:
            raise ValueError("legacy/raw first-pass flags differ")
        if self.removed_finding_indices != sorted(set(
                self.removed_finding_indices)):
            raise ValueError("removed finding indices must be sorted and unique")
        normalized = self.final_outcome == "NORMALIZED_DUPLICATE"
        if normalized:
            if (self.raw_first_pass_valid or not self.eventual_valid
                    or self.redundant_rows != 1
                    or self.semantic_invalid_attempts != 1
                    or len(self.removed_finding_indices) != 1
                    or self.dedup_evidence_sha256 is None
                    or self.raw_findings != self.retained_findings + 1
                    or self.raw_grounded_findings != self.raw_findings
                    or self.retained_grounded_findings != self.retained_findings):
                raise ValueError("normalized chunk does not preserve raw recovery facts")
        elif (self.redundant_rows or self.removed_finding_indices
              or self.dedup_evidence_sha256 is not None):
            raise ValueError("non-normalized chunk carries dedup evidence")
        if self.final_outcome == "RAW_VALID":
            if not self.eventual_valid:
                raise ValueError("raw-valid outcome must be eventually valid")
            # RAW_VALID describes the authoritative terminal answer.  A prior
            # answered invalid attempt remains visible through first-pass and
            # invalid-attempt counters; it does not rename the valid retry.
            retried = (self.charged_attempt_count >= 2 and
                       self.strict_schema_invalid_attempts +
                       self.semantic_invalid_attempts >= 1)
            if not self.raw_first_pass_valid and not retried:
                raise ValueError("raw-valid retry lacks prior invalid evidence")
        if self.final_outcome == "INVALID" and self.eventual_valid:
            raise ValueError("invalid outcome cannot be eventually valid")
        return self


class C0B4DocumentRow(DocumentRow):
    chunks: list[C0B4ChunkRow] = Field(min_length=1)
    redundant_rows: int = Field(ge=0)
    affected_work_ids: list[Sha256]
    normalized_duplicate_chunks: int = Field(ge=0)
    affected_document: bool

    @model_validator(mode="after")
    def exact_recovery(self) -> "C0B4DocumentRow":
        affected = [row.work_id for row in self.chunks
                    if row.final_outcome == "NORMALIZED_DUPLICATE"]
        if (self.redundant_rows != sum(row.redundant_rows for row in self.chunks)
                or self.normalized_duplicate_chunks != len(affected)
                or self.affected_work_ids != sorted(affected)
                or self.affected_document != bool(affected)):
            raise ValueError("document recovery census differs from its chunks")
        return self


class RawMetrics(StrictModel):
    raw_findings: int = Field(ge=0)
    raw_grounded_findings: int = Field(ge=0)
    first_pass_invalid_chunks: int = Field(ge=0)
    raw_semantic_invalid_attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def grounded_bound(self) -> "RawMetrics":
        if self.raw_grounded_findings > self.raw_findings:
            raise ValueError("raw grounding count exceeds raw findings")
        return self


class RetainedMetrics(StrictModel):
    documents: list[C0B4DocumentRow]
    category_metrics: dict[
        Literal["pii", "financial", "contact", "demographic"], CategoryMetric]
    macro_f1: ExactFraction
    micro_f1: ExactFraction
    retained_findings: int = Field(ge=0)
    retained_grounded_findings: int = Field(ge=0)
    negative_false_positive_documents: int = Field(ge=0)
    injection_pairs: list[InjectionPairRow]
    injection_pairs_measured: int = Field(ge=0)
    injection_events: int = Field(ge=0)
    robustness_failures: int = Field(ge=0)
    boundary_documents: int = Field(ge=0)
    boundary_passed: int = Field(ge=0)
    length_outcomes: int = Field(ge=0)
    eventual_invalid_chunks: int = Field(ge=0)
    context_headroom_failures: int = Field(ge=0)
    channel_violations: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_metrics(self) -> "RetainedMetrics":
        if set(self.category_metrics) != {
                "pii", "financial", "contact", "demographic"}:
            raise ValueError("category metrics differ from frozen set")
        if self.retained_grounded_findings > self.retained_findings:
            raise ValueError("retained grounding count exceeds retained findings")
        if len({row.doc_id for row in self.documents}) != len(self.documents):
            raise ValueError("retained documents must be unique")
        return self


class LanePlan(PolicyBoundStrictModel):
    version: Literal["c0b4-lane-plan-v1"]
    lane_id: F72LaneId
    seed: Literal[17, 20260804]
    candidate: Candidate
    parent_evidence: ParentBinding
    work: list[PublicWork] = Field(min_length=92, max_length=92)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def exact_plan(self) -> "LanePlan":
        if ((self.lane_id == "F72_17") != (self.seed == 17)
                or len({row.work_id for row in self.work}) != 92):
            raise ValueError("lane identity or work census differs from frozen plan")
        _self_hash(self, "plan_sha256")
        return self


class AcceptancePlan(PolicyBoundStrictModel):
    version: Literal["c0b4-acceptance-plan-v1"]
    lane_id: Literal["C44_1"]
    seed: Literal[1]
    candidate: Candidate
    parent_evidence: ParentBinding
    work: list[PublicWork] = Field(min_length=44, max_length=44)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def exact_plan(self) -> "AcceptancePlan":
        if len({row.work_id for row in self.work}) != 44:
            raise ValueError("C44 work IDs must be unique")
        _self_hash(self, "plan_sha256")
        return self


class PlanActivation(PolicyBoundStrictModel):
    version: Literal["c0b4-plan-activation-v1"]
    plan_sha256: Sha256
    prerequisite_sha256: Sha256
    activated_work_ids: list[Sha256]
    inactive_work_ids: list[Sha256]

    @model_validator(mode="after")
    def exact_partition(self) -> "PlanActivation":
        for values in (self.activated_work_ids, self.inactive_work_ids):
            if values != sorted(set(values)):
                raise ValueError("activation work IDs must be sorted and unique")
        if set(self.activated_work_ids) & set(self.inactive_work_ids):
            raise ValueError("active/inactive work sets overlap")
        return self


class CursorTransition(PolicyBoundStrictModel):
    version: Literal["c0b4-cursor-transition-v1"]
    from_lane_id: LaneId
    to_lane_id: LaneId
    from_aggregate_sha256: Sha256
    to_plan_sha256: Sha256
    completed_work_census_sha256: Sha256
    transitioned_at_utc: UtcRfc3339
    transition_sha256: Sha256

    @model_validator(mode="after")
    def exact_transition(self) -> "CursorTransition":
        allowed = (("F72_17", "F72_20260804"),
                   ("F72_20260804", "C44_1"))
        if (self.from_lane_id, self.to_lane_id) not in allowed:
            raise ValueError("cursor transition skips the frozen lane order")
        _self_hash(self, "transition_sha256")
        return self


class RuntimeEvent(PolicyBoundStrictModel):
    version: Literal["c0b4-runtime-event-v1"]
    event: Literal[
        "DISPATCHING", "RAW_VALID", "NORMALIZED_DUPLICATE", "INVALID",
        "ORPHANED", "CANCELLED"]
    lane_id: LaneId
    source_attempt_id: Sha256
    request_sha256: Sha256
    nonce: Annotated[str, Field(pattern=r"^FENCE_[0-9A-F]{32}$")]
    occurred_at_utc: UtcRfc3339
    event_sha256: Sha256

    @model_validator(mode="after")
    def exact_event(self) -> "RuntimeEvent":
        _self_hash(self, "event_sha256")
        return self


class ContextControl(PolicyBoundStrictModel):
    version: Literal["c0b4-context-control-v1"]
    control_id: Sha256
    kind: Literal["context_probe"]
    lane_id: Literal["F72_17"]
    purpose: Literal["c0b4_stage_f_candidate_context"]
    candidate_id: Sha256
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    config_sha256: Sha256
    prompt_sha256: Sha256
    minimum_context_length: int = Field(gt=0)
    trigger_rule: Literal["first_bounded_http_terminal_seed17"]
    payload_sha256: Sha256


class ContextEvidence(PolicyBoundStrictModel):
    version: Literal["c0b4-context-evidence-v1"]
    control_id: Sha256
    lane_id: Literal["F72_17"]
    purpose: Literal["c0b4_stage_f_candidate_context"]
    candidate_id: Sha256
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    config_sha256: Sha256
    prompt_sha256: Sha256
    expected_num_ctx: Literal[8192]
    observed_context_length: int = Field(gt=0)
    trigger_work_id: Sha256
    trigger_attempt_id: Sha256
    trigger_request_sha256: Sha256
    trigger_nonce: Annotated[str, Field(pattern=r"^FENCE_[0-9A-F]{32}$")]
    state: Literal["PASSED"]
    response_sha256: Sha256

    @model_validator(mode="after")
    def allocation_passed(self) -> "ContextEvidence":
        if self.observed_context_length < self.expected_num_ctx:
            raise ValueError("passed context evidence reports undersized allocation")
        return self


class CancellationControl(PolicyBoundStrictModel):
    version: Literal["c0b4-cancellation-control-v1"]
    control_id: Sha256
    kind: Literal["cancellation_probe"]
    lane_id: Literal["F72_17"]
    candidate_id: Sha256
    seed: Literal[17]
    prompt_sha256: Sha256
    source_doc_id: Literal["pos_pii_013"]
    chunk_index: Literal[0]
    nonce: Annotated[str, Field(pattern=r"^FENCE_[0-9A-F]{32}$")]
    request_sha256: Sha256
    deadline_seconds: Literal[600]
    max_close_after_first_byte_ms: Literal[5000]
    health_not_before_ms: Literal[2000]


class HealthControl(PolicyBoundStrictModel):
    version: Literal["c0b4-health-control-v1"]
    control_id: Sha256
    kind: Literal["cancellation_health"]
    lane_id: Literal["F72_17"]
    candidate_id: Sha256
    seed: Literal[17]
    prompt_sha256: Sha256
    source_doc_id: Literal["pos_pii_013"]
    chunk_index: Literal[0]
    nonce: Annotated[str, Field(pattern=r"^FENCE_[0-9A-F]{32}$")]
    health_work_id: Sha256
    request_sha256: Sha256
    deadline_seconds: Literal[600]


class LanePlanEnvelope(StrictModel):
    plan_sha256: Sha256
    payload: LanePlan

    @model_validator(mode="after")
    def exact_owner(self) -> "LanePlanEnvelope":
        if self.plan_sha256 != self.payload.plan_sha256:
            raise ValueError("lane envelope hash differs from its payload")
        return self


class AcceptancePlanEnvelope(StrictModel):
    plan_sha256: Sha256
    payload: AcceptancePlan

    @model_validator(mode="after")
    def exact_owner(self) -> "AcceptancePlanEnvelope":
        if self.plan_sha256 != self.payload.plan_sha256:
            raise ValueError("acceptance envelope hash differs from its payload")
        return self


class ControlPlan(StrictModel):
    context: ContextControl
    cancellation: CancellationControl
    health: HealthControl


class MasterPlan(PolicyBoundStrictModel):
    version: Literal["c0b4-master-plan-v1"]
    parent_binding: ParentBinding
    lane_order: list[LaneId] = Field(min_length=3, max_length=3)
    lane_plans: list[LanePlanEnvelope] = Field(min_length=2, max_length=2)
    control_plan: ControlPlan
    acceptance_template: AcceptancePlanEnvelope

    @model_validator(mode="after")
    def exact_tree(self) -> "MasterPlan":
        if self.lane_order != ["F72_17", "F72_20260804", "C44_1"]:
            raise ValueError("master lane order differs from frozen order")
        if [row.payload.lane_id for row in self.lane_plans] != self.lane_order[:2]:
            raise ValueError("master lane envelopes differ from lane order")
        if self.acceptance_template.payload.lane_id != self.lane_order[2]:
            raise ValueError("master acceptance template differs from lane order")
        nested = [row.payload for row in self.lane_plans]
        nested.append(self.acceptance_template.payload)
        own = (self.policy_id, self.policy_sha256, self.protocol_sha256)
        if any((row.policy_id, row.policy_sha256, row.protocol_sha256) != own
               or row.parent_evidence != self.parent_binding for row in nested):
            raise ValueError("master plan contains mixed policy/parent lineage")
        controls = (
            self.control_plan.context, self.control_plan.cancellation,
            self.control_plan.health,
        )
        if any((row.policy_id, row.policy_sha256, row.protocol_sha256) != own
               for row in controls):
            raise ValueError("master controls contain mixed policy lineage")
        return self


class CancellationHealthEvidence(PolicyBoundStrictModel):
    version: Literal["c0b4-cancellation-health-evidence-v1"]
    lane_id: Literal["F72_17"]
    candidate_id: Sha256
    prompt_sha256: Sha256
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
    failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_evidence(self) -> "CancellationHealthEvidence":
        _ordered_subset(
            self.failure_reasons, CANCELLATION_FAILURE_REASONS,
            "cancellation failure reasons")
        if (self.passed != (not self.failure_reasons)
                or self.retained_grounded_pii and not self.eventual_valid
                or self.authoritative_done_reason is None and self.eventual_valid
                or self.max_answered_prompt_eval_count is None and self.eventual_valid
                or len(set(self.health_attempt_ids)) != len(self.health_attempt_ids)):
            raise ValueError("cancellation/health facts are contradictory")
        return self


class LaneAggregate(PolicyBoundStrictModel):
    version: Literal["c0b4-lane-aggregate-v1"]
    lane_id: F72LaneId
    seed: Literal[17, 20260804]
    lane_plan_sha256: Sha256
    parent_binding: ParentBinding
    candidate: Candidate
    planned_chunks: Literal[92]
    completed_chunks: int = Field(ge=0, le=92)
    raw_metrics: RawMetrics
    retained_metrics: RetainedMetrics
    recovery_counters: RecoveryCounters
    context_evidence_sha256: Sha256 | None
    cancellation_health_evidence_sha256: Sha256 | None
    passed: bool
    failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "LaneAggregate":
        _ordered_subset(self.failure_reasons, LANE_FAILURE_REASONS,
                        "lane failure reasons")
        seed17 = self.lane_id == "F72_17" and self.seed == 17
        later = self.lane_id == "F72_20260804" and self.seed == 20260804
        if not (seed17 or later):
            raise ValueError("lane ID and seed differ")
        if seed17 != (self.context_evidence_sha256 is not None):
            raise ValueError("context evidence belongs only to seed 17")
        cancellation_present = self.cancellation_health_evidence_sha256 is not None
        noncontrol_failure = any(
            reason != "cancellation_health_failure"
            for reason in self.failure_reasons)
        if (not seed17 and cancellation_present) or (
                seed17 and not cancellation_present and not noncontrol_failure):
            raise ValueError("cancellation evidence differs from seed-17 gate order")
        if ("cancellation_health_failure" in self.failure_reasons) and not seed17:
            raise ValueError("later seed cannot own cancellation failure")
        if ("cancellation_health_failure" in self.failure_reasons
                and not cancellation_present):
            raise ValueError("cancellation failure lacks control evidence")
        if self.passed != (not self.failure_reasons):
            raise ValueError("lane pass differs from failure reasons")
        return self


class C44ScoredAggregate(PolicyBoundStrictModel):
    version: Literal["c0b4-c44-scored-v1"]
    lane_id: Literal["C44_1"]
    seed: Literal[1]
    acceptance_plan_sha256: Sha256
    parent_binding: ParentBinding
    candidate: Candidate
    planned_chunks: Literal[44]
    completed_chunks: int = Field(ge=0, le=44)
    raw_metrics: RawMetrics
    retained_metrics: RetainedMetrics
    recovery_counters: RecoveryCounters
    component_passed: bool
    failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "C44ScoredAggregate":
        _ordered_subset(self.failure_reasons, C44_FAILURE_REASONS,
                        "C44 failure reasons")
        if self.component_passed != (not self.failure_reasons):
            raise ValueError("C44 component pass differs from evidence-only reasons")
        return self


class RecallCount(StrictModel):
    true_positives: int = Field(ge=0)
    support: int = Field(ge=0)


class AcceptanceTotals(StrictModel):
    document_count: int = Field(ge=0)
    positive_documents: int = Field(ge=0)
    negative_documents: int = Field(ge=0)
    injection_pairs: int = Field(ge=0)
    boundary_documents: int = Field(ge=0)
    truncation_documents: int = Field(ge=0)
    expected_chunks: int = Field(ge=0)
    completed_chunks: int = Field(ge=0)
    first_pass_invalid_chunks: int = Field(ge=0)
    eventual_invalid_chunks: int = Field(ge=0)
    raw_findings: int = Field(ge=0)
    raw_grounded_findings: int = Field(ge=0)
    retained_findings: int = Field(ge=0)
    retained_grounded_findings: int = Field(ge=0)
    category_recall: dict[
        Literal["pii", "financial", "contact", "demographic"], RecallCount]
    negative_false_positive_documents: int = Field(ge=0)
    injection_pairs_measured: int = Field(ge=0)
    injection_events: int = Field(ge=0)
    robustness_failures: int = Field(ge=0)
    boundary_passed: int = Field(ge=0)
    truncation_completed: int = Field(ge=0)
    length_outcomes: int = Field(ge=0)
    context_failures: int = Field(ge=0)
    channel_violations: int = Field(ge=0)
    cancellation_health_passed: bool
    provenance_passed: bool
    safety_passed: bool
    recovery_counters: RecoveryCounters

    @model_validator(mode="after")
    def exact_totals(self) -> "AcceptanceTotals":
        if set(self.category_recall) != {
                "pii", "financial", "contact", "demographic"}:
            raise ValueError("acceptance category recall differs from frozen set")
        if (self.raw_grounded_findings > self.raw_findings
                or self.retained_grounded_findings > self.retained_findings):
            raise ValueError("acceptance grounding counts exceed findings")
        return self


class ComponentHashes(StrictModel):
    c44_rerun_aggregate_sha256: Sha256
    d50_confirmation_aggregate_sha256: Sha256
    f72_seed17_aggregate_sha256: Sha256


class AcceptanceAggregate(PolicyBoundStrictModel):
    version: Literal["c0b4-acceptance-aggregate-v1"]
    acceptance_plan_sha256: Sha256
    component_hashes: ComponentHashes
    totals: AcceptanceTotals
    recovery_counters: RecoveryCounters
    passed: bool
    failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "AcceptanceAggregate":
        _ordered_subset(self.failure_reasons, ACCEPTANCE_FAILURE_REASONS,
                        "acceptance failure reasons")
        if (self.passed != (not self.failure_reasons)
                or self.totals.recovery_counters != self.recovery_counters):
            raise ValueError("acceptance pass/counters differ from exact totals")
        return self


class LaneAggregateHashes(StrictModel):
    f72_seed17_sha256: Sha256
    f72_seed20260804_sha256: Sha256 | None
    c44_scored_sha256: Sha256 | None


class Result(PolicyBoundStrictModel):
    version: Literal["c0b4-result-v1"]
    terminal: QualityTerminal
    reason: str
    master_plan_sha256: Sha256
    lane_aggregate_sha256s: LaneAggregateHashes
    acceptance_aggregate_sha256: Sha256 | None
    selection: Selection | None

    @model_validator(mode="after")
    def exact_terminal(self) -> "Result":
        allowed = QUALITY_REASON_BY_TERMINAL[self.terminal]
        if self.reason not in allowed:
            raise ValueError("quality result reason differs from terminal")
        confirmed = self.terminal == "CONFIRMED"
        complete = self.reason in {
            "complete_public_acceptance_passed",
            "complete_corpus_acceptance_failed",
        }
        if confirmed != (self.selection is not None) \
                or complete != (self.acceptance_aggregate_sha256 is not None):
            raise ValueError("confirmed result lacks acceptance owner/selection")
        later = self.reason in {
            "seed20260804_no_qualifier", "complete_corpus_acceptance_failed",
            "complete_public_acceptance_passed",
        }
        c44 = complete
        hashes = self.lane_aggregate_sha256s
        if (later != (hashes.f72_seed20260804_sha256 is not None)
                or c44 != (hashes.c44_scored_sha256 is not None)):
            raise ValueError("quality result lane ownership differs from stop point")
        return self


class ConfirmedFacts(StrictModel):
    confirmed: Literal[True]


class DeterministicStopFacts(StrictModel):
    deterministic_stop: Literal[True]
    reason: Literal[
        "seed17_no_qualifier", "seed17_control_gate_failed",
        "seed20260804_no_qualifier", "complete_corpus_acceptance_failed"]


class Completion(PolicyBoundStrictModel):
    version: Literal["c0b4-completion-v1"]
    outcome: QualityTerminal
    artifact_sha256: Sha256
    facts: ConfirmedFacts | DeterministicStopFacts

    @model_validator(mode="after")
    def exact_facts(self) -> "Completion":
        if (self.outcome == "CONFIRMED") != isinstance(self.facts, ConfirmedFacts):
            raise ValueError("completion facts differ from outcome")
        return self


class FailureEvidence(PolicyBoundStrictModel):
    version: Literal["c0b4-failure-evidence-v1"]
    terminal: FailureTerminal
    reason: str
    lane_id: LaneId | None
    plan_sha256: Sha256 | None
    attempt_id: Sha256 | None
    control_id: Sha256 | None
    charged_call_total: int = Field(ge=0)
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def exact_failure(self) -> "FailureEvidence":
        if self.reason != FAILURE_REASON_BY_TERMINAL[self.terminal]:
            raise ValueError("failure reason differs from terminal")
        attemptless = self.terminal in {
            "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED"}
        if attemptless and self.attempt_id is not None:
            raise ValueError("attemptless terminal names an attempt")
        if self.terminal == "FAILED_SAFETY" and self.attempt_id is None:
            raise ValueError("safety failure must name its charged attempt")
        _self_hash(self, "evidence_sha256")
        return self


class FailureResult(PolicyBoundStrictModel):
    version: Literal["c0b4-failure-v1"]
    terminal: FailureTerminal
    reason: str
    evidence_sha256: Sha256
    charged_call_total: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_failure(self) -> "FailureResult":
        if self.reason != FAILURE_REASON_BY_TERMINAL[self.terminal]:
            raise ValueError("failure result reason differs from terminal")
        return self


class BackupAnchor(PolicyBoundStrictModel):
    version: Literal["c0b4-backup-anchor-v1"]
    run_id: str = Field(min_length=1)
    header_sha256: Sha256
    terminal_artifact_sha256: Sha256
    completion_sha256: Sha256 | None
    parent_binding: ParentBinding
    source_binding: SourceBinding
    anchor_sha256: Sha256

    @model_validator(mode="after")
    def exact_anchor(self) -> "BackupAnchor":
        _self_hash(self, "anchor_sha256")
        return self


class BackupReceipt(PolicyBoundStrictModel):
    version: Literal["c0b4-backup-receipt-v1"]
    anchor_sha256: Sha256
    snapshot_run_relative_path: str = Field(min_length=1)
    snapshot_sha256: Sha256
    snapshot_size_bytes: int = Field(gt=0)
    integrity_check: Literal["ok"]
    foreign_key_violations: Literal[0]
    created_at_utc: UtcRfc3339
    receipt_sha256: Sha256

    @field_validator("snapshot_run_relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or ".." in path.parts or "." in path.parts
                or str(path) != value):
            raise ValueError("snapshot path must be canonical and run-relative")
        return value

    @model_validator(mode="after")
    def exact_receipt(self) -> "BackupReceipt":
        _self_hash(self, "receipt_sha256")
        return self


class RuntimePause(PolicyBoundStrictModel):
    """Closed resumable state/reason pair; it is not a terminal artifact."""

    state: RuntimeState
    reason: str

    @model_validator(mode="after")
    def exact_reason(self) -> "RuntimePause":
        if self.reason != RUNTIME_REASON_BY_STATE[self.state]:
            raise ValueError("runtime pause reason differs from state")
        return self


ArtifactModel: TypeAlias = (
    RunHeader | MasterPlan | LanePlan | AcceptancePlan | PlanActivation |
    CursorTransition | RuntimeEvent |
    ContextControl | ContextEvidence | CancellationControl | HealthControl |
    CancellationHealthEvidence | DedupEvidence | LaneAggregate |
    C44ScoredAggregate | AcceptanceAggregate | Result | Completion |
    FailureEvidence | FailureResult | BackupAnchor | BackupReceipt
)
ModelT = TypeVar("ModelT", bound=BaseModel)

_VERSION_MODELS: dict[str, type[BaseModel]] = {
    "c0b4-run-header-v1": RunHeader,
    "c0b4-master-plan-v1": MasterPlan,
    "c0b4-lane-plan-v1": LanePlan,
    "c0b4-acceptance-plan-v1": AcceptancePlan,
    "c0b4-plan-activation-v1": PlanActivation,
    "c0b4-cursor-transition-v1": CursorTransition,
    "c0b4-runtime-event-v1": RuntimeEvent,
    "c0b4-context-control-v1": ContextControl,
    "c0b4-context-evidence-v1": ContextEvidence,
    "c0b4-cancellation-control-v1": CancellationControl,
    "c0b4-health-control-v1": HealthControl,
    "c0b4-cancellation-health-evidence-v1": CancellationHealthEvidence,
    "c0b4-dedup-evidence-v1": DedupEvidence,
    "c0b4-lane-aggregate-v1": LaneAggregate,
    "c0b4-c44-scored-v1": C44ScoredAggregate,
    "c0b4-acceptance-aggregate-v1": AcceptanceAggregate,
    "c0b4-result-v1": Result,
    "c0b4-completion-v1": Completion,
    "c0b4-failure-evidence-v1": FailureEvidence,
    "c0b4-failure-v1": FailureResult,
    "c0b4-backup-anchor-v1": BackupAnchor,
    "c0b4-backup-receipt-v1": BackupReceipt,
}


def validate_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch the closed version catalog; legacy/current versions fail closed."""
    if not isinstance(value, Mapping):
        raise ValueError("C0B-4 artifact must be a mapping")
    require_current_payload(value)
    model = _VERSION_MODELS.get(value.get("version"))
    if model is None:
        raise ValueError("unknown C0B-4 artifact version")
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_as(model: type[ModelT], value: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one explicitly-owned C0B-4 schema."""
    require_current_payload(value)
    return model.model_validate(value, strict=True).model_dump(mode="json")
