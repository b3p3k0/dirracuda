"""Strict, no-coercion artifact contracts for C0B-6.

No model in this module accepts a C0B-2/C0B-3/C0B-4 artifact version.  The
public summary is a deterministic derived view and is intentionally excluded
from the durable artifact dispatcher.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Mapping, TypeAlias, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

from .c0b2_public_schema import (
    ExactFraction, Nonce, Sha256, StrictModel, UtcRfc3339, sha256_json,
)
from .c0b2_schema import FrozenMount
from .c0b2_stage_f import CategoryMetric, ChunkRow, DocumentRow, InjectionPairRow
from .c0b6_policy import (
    ACCEPTANCE_FAILURE_REASONS, BENCHMARK_PROTOCOL_ID, C44_FAILURE_REASONS,
    LANE_FAILURE_REASONS, POLICY_ID, POLICY_SHA256, policy_binding,
    require_current_payload,
)

PolicyId: TypeAlias = Literal["c0b6-assistive-bounded-fp-v1"]
PolicySha256: TypeAlias = Literal[
    "bf22aed2c077dec6e27ecd4121ca14c2e546c1a036855db7e3a58d6b1f2f55d3"]
LaneId: TypeAlias = Literal["F72_20260811", "F72_20260818", "C44_1"]
F72LaneId: TypeAlias = Literal["F72_20260811", "F72_20260818"]
Category: TypeAlias = Literal["pii", "financial", "contact", "demographic"]
Component: TypeAlias = Literal[
    "C44_RERUN", "D50_CONFIRMATION", "F72_SEED20260811", "F72_SEED20260818"]
FinalOutcome: TypeAlias = Literal["RAW_VALID", "NORMALIZED_DUPLICATE", "INVALID"]
QualityTerminal: TypeAlias = Literal["CONFIRMED", "INCONCLUSIVE"]
FailureTerminal: TypeAlias = Literal[
    "FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
    "BLOCKED_FILESYSTEM", "ABANDONED"]
FailureOrigin: TypeAlias = Literal[
    "safety_transport", "budget_claim", "filesystem_revalidation",
    "parent_replay", "source_revalidation", "master_replay",
    "resume_history", "resume_control_replay", "preflight",
    "lane_activation", "lane_execution", "lane_derivation",
    "cursor_transition", "acceptance_derivation", "terminal_recheck",
    "backup_live_replay", "backup_snapshot_replay", "backup_publication",
    "operator_abandon"]
RuntimeState: TypeAlias = Literal[
    "PAUSED_SOFT_WALL", "PAUSED_RESOURCE", "PAUSED_PREFLIGHT",
    "PAUSED_STAGE_BOUNDARY", "CANCELLED_PENDING_RESUME"]

QUALITY_REASON_BY_TERMINAL = {
    "CONFIRMED": ("complete_public_acceptance_passed",),
    "INCONCLUSIVE": (
        "seed20260811_no_qualifier", "seed20260811_control_gate_failed",
        "seed20260818_no_qualifier", "complete_corpus_acceptance_failed",
    ),
}
FAILURE_REASON_BY_TERMINAL = {
    "FAILED_SAFETY": "safety_envelope_failure",
    "BLOCKED_PROVENANCE": "provenance_identity_failure",
    "BLOCKED_BUDGET": "call_allowance_exhausted",
    "BLOCKED_FILESYSTEM": "filesystem_capability_or_integrity_failure",
    "ABANDONED": "operator_abandoned",
}
FAILURE_ORIGINS_BY_TERMINAL = {
    "FAILED_SAFETY": frozenset({"safety_transport"}),
    "BLOCKED_BUDGET": frozenset({"budget_claim"}),
    "BLOCKED_FILESYSTEM": frozenset({"filesystem_revalidation"}),
    "ABANDONED": frozenset({"operator_abandon"}),
    "BLOCKED_PROVENANCE": frozenset({
        "parent_replay", "source_revalidation", "master_replay",
        "resume_history", "resume_control_replay", "preflight",
        "lane_activation", "lane_execution", "lane_derivation",
        "cursor_transition", "acceptance_derivation", "terminal_recheck",
        "backup_live_replay", "backup_snapshot_replay", "backup_publication",
    }),
}
RUNTIME_REASON_BY_STATE = {
    "PAUSED_SOFT_WALL": "soft_wall_elapsed",
    "PAUSED_RESOURCE": "resource_backoff",
    "PAUSED_PREFLIGHT": "preflight_unavailable",
    "PAUSED_STAGE_BOUNDARY": "stage_boundary",
    "CANCELLED_PENDING_RESUME": "operator_cancelled",
}
CANCELLATION_FAILURE_REASONS = (
    "cancel_not_observed", "cancel_after_5_seconds", "health_missing",
    "health_eventual_invalid", "health_pii_missing", "health_grounding_failure",
    "health_length_outcome", "health_channel_violation",
    "health_context_headroom_failure",
)
LANE_SEEDS = {"F72_20260811": 20260811, "F72_20260818": 20260818, "C44_1": 1}
LANE_PHASES = {
    "F72_20260811": "F_SEED_20260811",
    "F72_20260818": "F_SEED_20260818",
    "C44_1": "F_ACCEPTANCE",
}
PUBLIC_TEMPLATE_FAMILY_SHA256 = (
    "9f47d270d66e904135f76927a66d4c5eb69b15626bd5a2d5d58f2c2053670169")
PUBLIC_TEMPLATE_FAMILY_PAYLOAD = {
    "doc_id_rules": {
        "neg_clean_": {
            "0": "clean_sprint_retrospective",
            "1": "clean_boiler_maintenance_log",
            "2": "clean_library_acquisition_notes",
            "3": "clean_cafeteria_menu_cycle",
            "4": "clean_parking_structure_survey",
        },
        "neg_nearmiss_": {
            "0": "near_miss_checksum_failed_barcode",
            "1": "near_miss_ssn_shaped_part_number",
            "2": "near_miss_phone_shaped_chassis_serial",
            "3": "near_miss_invalid_routing_cost_centre",
            "4": "near_miss_invalid_iban_template_placeholder",
        },
    },
    "numeric_suffix_range": [1, 20],
}

EXECUTION_PARENT_BINDING = {
    "run_id": "c0b3-20260809-154924-19afcaab26984160f20ec075",
    "source_commit": "dcd7e0b9504ded47dad82f25814aea54d666b268",
    "checkpoint_sha256": "f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3",
    "run_header_sha256": "80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2",
    "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
    "protocol_sha256": "031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d",
    "policy_id": "c0b3-assistive-bounded-fp-v1",
    "policy_sha256": "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    "task_tree_sha256": "a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0",
    "final_d_decision_sha256": "5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894",
    "d4_aggregate_sha256": "7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945",
    "f_master_plan_sha256": "093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de",
    "seed1_aggregate_sha256": "cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1",
    "terminal_result_sha256": "ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc",
    "completion_sha256": "6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00",
    "master_manifest_sha256": "df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12",
    "seed17_old_plan_sha256": "2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31",
    "seed17_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0,
                                "attempt_rows": 0, "activation_rows": 0},
    "seed20260804_old_plan_sha256": "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21",
    "seed20260804_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0,
                                     "attempt_rows": 0, "activation_rows": 0},
    "backup_anchor_sha256": "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56",
    "backup_snapshot_sha256": "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c",
    "backup_receipt_sha256": "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9",
}
OBSERVED_C0B4_BINDING = {
    "run_id": "c0b4-20260811-210848-d2b52272f3aabb156f55d166",
    "source_commit": "377e4eb9e277d24d9ef1699d3a427253c052df75",
    "checkpoint_sha256": "c6d3e8e8dfeba129911ab034bb8301f028722227bf6c3e1d3817b1fa461d4285",
    "run_header_sha256": "301719b3a4d570bb87017f01bfb27d16db2d66c652ed251c56e71c423b2e7f0b",
    "benchmark_protocol_id": "c0b4-grounded-duplicate-confirmation-v1",
    "protocol_sha256": "71bde3bdd02f338216aa9a964a21207db3d1d4c80f0e676dab04776f7f833ae0",
    "policy_id": "c0b4-bounded-grounded-dedup-v1",
    "policy_sha256": "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43",
    "task_tree_sha256": "2e6c04acee48ce4b01f591239568b260b7dc6d5f4273c579c083513852f459fe",
    "master_plan_sha256": "7faea74d2d2d856658a3854af04576c83ba3f1cacb1fbbe939ad87db58e11832",
    "lane_plan_sha256s": {
        "F72_17": "945298c296a86dde850e2e8253aaebe1c99ee86886a8a93bf794261212929cd5",
        "F72_20260804": "ed9e9ac2ac9937a5460b9a6be63ea017a2a53d7a6630772d141910f2c3250169",
        "C44_1": "3333d49c849fb36eea7695be5338664cda60b9843d47648e279fd3bd191f6f6f",
    },
    "inactive_lane_census": {
        "F72_20260804": {"planned_work_rows": 92, "activation_rows": 0,
                          "attempt_rows": 0, "aggregate_rows": 0},
        "C44_1": {"planned_work_rows": 44, "activation_rows": 0,
                   "attempt_rows": 0, "aggregate_rows": 0},
    },
    "f72_seed17_aggregate_sha256": "4b86e1fc4a3e9ccf198247da8782a9be688c606f4a8a2dce7fd7b0a5c717215e",
    "terminal_result_sha256": "7c9a387e2b3b17bb028eb3c98156a54059ce23d316174b6ec81030ed0ac73497",
    "completion_sha256": "5b2144227b15a89e17a1ec235976cee4a26e193b5dee31c5b91d09ab7f0e051c",
    "backup_anchor_sha256": "60ac16a8962a5b87b16cc5bf7beeaae3d8009cf4d26a5441656ef125d2602358",
    "backup_snapshot_sha256": "f31a38d269a13c6df8b9e264f8d149d161504e3e3cdcae1ea0f1fd2a253fe94b",
    "backup_receipt_sha256": "e758b8f1bbe8f1a2d2c4edf048b64ff7f8be82392c26df18427e6a3e87546c75",
}
PARENT_BINDING = {"execution_parent": EXECUTION_PARENT_BINDING,
                  "observed_c0b4": OBSERVED_C0B4_BINDING}
CANDIDATE_ID = (
    "7c6864367346c171a2ad008965e26d536ac4218bce0b26324018828add52e6c9")


def _ordered_subset(values: list[str], order: tuple[str, ...], label: str) -> None:
    try:
        positions = [order.index(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"{label} contain an unknown value") from exc
    if positions != sorted(set(positions)):
        raise ValueError(f"{label} must be unique and frozen-order")


def _self_hash(model: BaseModel, field: str) -> None:
    if getattr(model, field) != sha256_json(model.model_dump(mode="json", exclude={field})):
        raise ValueError(f"{field} differs from its canonical preimage")


def _exact_model(model: BaseModel, expected: Mapping[str, Any], label: str) -> None:
    if model.model_dump(mode="json") != expected:
        raise ValueError(f"{label} differs from frozen evidence")


class PolicyBoundStrictModel(StrictModel):
    policy_id: PolicyId
    policy_sha256: PolicySha256
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def exact_policy(self) -> "PolicyBoundStrictModel":
        require_current_payload(self.model_dump(
            mode="json", include={"policy_id", "policy_sha256", "protocol_sha256"}))
        return self


def identity_fields(protocol_sha256: str) -> dict[str, str]:
    return policy_binding(protocol_sha256)


def public_template_family(document_id: str) -> str:
    """Derive the exact public label for one frozen negative document ID."""
    if type(document_id) is not str:
        raise ValueError("negative document ID must be exact text")
    for prefix, rules in PUBLIC_TEMPLATE_FAMILY_PAYLOAD["doc_id_rules"].items():
        suffix = document_id.removeprefix(prefix)
        if (suffix != document_id and len(suffix) == 3 and suffix.isascii()
                and suffix.isdigit() and 1 <= int(suffix) <= 20):
            return rules[str(int(suffix) % 5)]
    raise ValueError("negative document ID is outside frozen template rules")


class OldPlanCensus(StrictModel):
    planned_work_rows: Literal[92]
    registered_work_rows: Literal[0]
    attempt_rows: Literal[0]
    activation_rows: Literal[0]


class ExecutionParent(StrictModel):
    run_id: str; source_commit: str; checkpoint_sha256: Sha256; run_header_sha256: Sha256
    benchmark_protocol_id: str; protocol_sha256: Sha256; policy_id: str
    policy_sha256: Sha256; task_tree_sha256: Sha256; final_d_decision_sha256: Sha256
    d4_aggregate_sha256: Sha256; f_master_plan_sha256: Sha256; seed1_aggregate_sha256: Sha256
    terminal_result_sha256: Sha256; completion_sha256: Sha256; master_manifest_sha256: Sha256
    seed17_old_plan_sha256: Sha256; seed17_old_plan_census: OldPlanCensus
    seed20260804_old_plan_sha256: Sha256; seed20260804_old_plan_census: OldPlanCensus
    backup_anchor_sha256: Sha256; backup_snapshot_sha256: Sha256; backup_receipt_sha256: Sha256

    @model_validator(mode="after")
    def exact_parent(self) -> "ExecutionParent":
        _exact_model(self, EXECUTION_PARENT_BINDING, "execution parent")
        return self


class InactiveLaneCensus(StrictModel):
    planned_work_rows: Literal[44, 92]
    activation_rows: Literal[0]
    attempt_rows: Literal[0]
    aggregate_rows: Literal[0]


class ObservedC0B4(StrictModel):
    run_id: str; source_commit: str; checkpoint_sha256: Sha256; run_header_sha256: Sha256
    benchmark_protocol_id: str; protocol_sha256: Sha256; policy_id: str
    policy_sha256: Sha256; task_tree_sha256: Sha256; master_plan_sha256: Sha256
    lane_plan_sha256s: dict[str, Sha256]
    inactive_lane_census: dict[str, InactiveLaneCensus]
    f72_seed17_aggregate_sha256: Sha256; terminal_result_sha256: Sha256
    completion_sha256: Sha256; backup_anchor_sha256: Sha256
    backup_snapshot_sha256: Sha256; backup_receipt_sha256: Sha256

    @model_validator(mode="after")
    def exact_observation(self) -> "ObservedC0B4":
        _exact_model(self, OBSERVED_C0B4_BINDING, "observed C0B-4")
        return self


class ParentBinding(StrictModel):
    execution_parent: ExecutionParent
    observed_c0b4: ObservedC0B4


class SourceBinding(StrictModel):
    git_head: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    declared_dirty_state_sha256: Sha256; task_tree_sha256: Sha256
    protocol_sha256: Sha256; policy_sha256: PolicySha256; prompt_sha256: Sha256
    schema_sha256: Sha256; fixture_sha256: Sha256; master_manifest_sha256: Sha256
    chunker_sha256: Sha256; detector_sha256: Sha256; generation_options_sha256: Sha256
    worktree_seal_sha256: Sha256; filesystem_capability_sha256: Sha256
    model_digests: dict[str, Sha256] = Field(min_length=1)


class RunHeader(PolicyBoundStrictModel):
    version: Literal["c0b6-run-header-v1"]
    run_type: Literal["public_confirmation"]
    benchmark_protocol_id: Literal[BENCHMARK_PROTOCOL_ID]
    parent_binding: ParentBinding
    ollama_endpoint: Literal["http://127.0.0.1:11434"]
    ollama_version: Literal["0.32.5"]
    filesystem_selected_mode: Literal["DELETE"]
    git_head: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    declared_dirty_state_sha256: Sha256; task_tree_sha256: Sha256
    fixture_sha256: Sha256; master_manifest_sha256: Sha256; schema_sha256: Sha256
    prompt_sha256: Sha256; chunker_sha256: Sha256; detector_sha256: Sha256
    generation_options_sha256: Sha256; worktree_seal_sha256: Sha256
    filesystem_capability_sha256: Sha256; model_digests: dict[str, Sha256]
    mount: FrozenMount; schema_version: Literal[1]; journal_mode: Literal["DELETE"]
    cumulative_cap: Literal[295]
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    limits: dict[str, int]; invocation_caps: dict[str, int]

    @model_validator(mode="after")
    def exact_header(self) -> "RunHeader":
        model = {"qwen3.6:27b":
                 "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"}
        limits = {"scored": 228, "schema_retry": 4,
                  "preflight_control": 33, "transport_orphan": 30}
        if self.model_digests != model or self.limits != limits \
                or self.invocation_caps != {"total": 10}:
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
    chunk_chars: Literal[8000]; overlap: Literal[256]; num_ctx: Literal[8192]
    num_predict: Literal[1024]


class Selection(Candidate):
    pass


def c0b6_activation_group_id(candidate_id: str, plan_key: str) -> str:
    return sha256_json({"candidate_id": candidate_id,
                        "domain": "c0b6-stage-f-group-v1", "plan_key": plan_key})


def c0b6_cell_id(*, candidate_id: str, phase: str, seed: int) -> str:
    return sha256_json({
        "budget_stage": "F", "candidate_id": candidate_id, "chunk_chars": 8000,
        "domain": "c0b6-public-cell-v1", "num_ctx": 8192,
        "num_predict": 1024, "overlap": 256, "phase": phase, "seed": seed,
    })


def c0b6_work_id(*, cell_id: str, chunk_index: int, chunk_sha256: str,
                  doc_id: str, document_sha256: str, nonce: str,
                  plan_key: str, request_sha256: str, view_id: str | None) -> str:
    return sha256_json({
        "cell_id": cell_id, "chunk_index": chunk_index, "chunk_sha256": chunk_sha256,
        "doc_id": doc_id, "document_sha256": document_sha256,
        "domain": "c0b6-public-work-v1", "nonce": nonce, "plan_key": plan_key,
        "request_sha256": request_sha256, "view_id": view_id,
    })


class C0B6PublicWork(StrictModel):
    stage: Literal["F"]; phase: Literal["F_SEED_20260811", "F_SEED_20260818", "F_ACCEPTANCE"]
    plan_key: Literal["F_SEED_20260811", "F_SEED_20260818", "F_ACCEPTANCE"]
    budget_stage: Literal["F"]; activation_group_id: Sha256 | None
    candidate_id: Sha256; cell_id: Sha256; work_id: Sha256
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    worksheet: Literal["v2"]; doc_id: str = Field(min_length=1); view_id: str | None
    document_sha256: Sha256; chunk_chars: Literal[8000]; overlap: Literal[256]
    num_ctx: Literal[8192]; num_predict: Literal[1024]
    seed: Literal[1, 20260811, 20260818]; chunk_index: int = Field(ge=0)
    chunk_sha256: Sha256; nonce: Nonce; prompt_sha256: Sha256; request_sha256: Sha256

    @model_validator(mode="after")
    def exact_identity(self) -> "C0B6PublicWork":
        lane = next((key for key, phase in LANE_PHASES.items() if phase == self.phase), None)
        if (self.phase != self.plan_key or lane is None or self.seed != LANE_SEEDS[lane]
                or self.candidate_id != CANDIDATE_ID
                or (lane == "C44_1") != (self.activation_group_id is None)
                or self.view_id == ""):
            raise ValueError("work lane, seed, phase, or activation differs")
        if lane != "C44_1" and self.activation_group_id != c0b6_activation_group_id(
                self.candidate_id, self.plan_key):
            raise ValueError("work activation group differs from C0B-6 identity")
        if self.cell_id != c0b6_cell_id(
                candidate_id=self.candidate_id, phase=self.phase, seed=self.seed):
            raise ValueError("work cell differs from C0B-6 identity")
        if self.work_id != c0b6_work_id(
                cell_id=self.cell_id, chunk_index=self.chunk_index,
                chunk_sha256=self.chunk_sha256, doc_id=self.doc_id,
                document_sha256=self.document_sha256, nonce=self.nonce,
                plan_key=self.plan_key, request_sha256=self.request_sha256,
                view_id=self.view_id):
            raise ValueError("work ID differs from C0B-6 identity")
        return self


class RawCounts(StrictModel):
    findings: int = Field(ge=0); grounded_findings: int = Field(ge=0)
    first_pass_valid: bool; semantic_invalid_attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def honest(self) -> "RawCounts":
        if self.grounded_findings > self.findings \
                or self.first_pass_valid and self.semantic_invalid_attempts:
            raise ValueError("raw recovery counts are contradictory")
        return self


class RetainedCounts(StrictModel):
    findings: int = Field(ge=0); grounded_findings: int = Field(ge=0)
    eventual_valid: bool

    @model_validator(mode="after")
    def honest(self) -> "RetainedCounts":
        if self.grounded_findings > self.findings \
                or not self.eventual_valid and (self.findings or self.grounded_findings):
            raise ValueError("retained recovery counts are contradictory")
        return self


class RecoveryCounters(StrictModel):
    redundant_rows: int = Field(ge=0); affected_work_ids: list[Sha256]
    affected_chunk_count: int = Field(ge=0); affected_document_ids: list[str]
    affected_document_count: int = Field(ge=0); normalized_duplicate_chunks: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_census(self) -> "RecoveryCounters":
        if (self.affected_work_ids != sorted(set(self.affected_work_ids))
                or self.affected_document_ids != sorted(set(self.affected_document_ids))
                or self.affected_chunk_count != len(self.affected_work_ids)
                or self.affected_document_count != len(self.affected_document_ids)
                or self.redundant_rows != self.normalized_duplicate_chunks
                or self.redundant_rows != self.affected_chunk_count
                or bool(self.redundant_rows) != bool(self.affected_document_ids)):
            raise ValueError("recovery counters differ from exact one-row census")
        return self


class DedupEvidence(PolicyBoundStrictModel):
    version: Literal["c0b6-dedup-evidence-v1"]
    work_id: Sha256; attempt_id: Sha256; raw_response_sha256: Sha256
    dedupe_key: Literal["category+nfc_quote"]; removed_index: int = Field(ge=0, le=15)
    raw_counts: RawCounts; retained_counts: RetainedCounts; evidence_sha256: Sha256

    @model_validator(mode="after")
    def exact_recovery(self) -> "DedupEvidence":
        if (self.raw_counts.first_pass_valid
                or self.raw_counts.semantic_invalid_attempts != 1
                or self.raw_counts.grounded_findings != self.raw_counts.findings
                or not self.retained_counts.eventual_valid
                or self.retained_counts.grounded_findings != self.retained_counts.findings
                or self.raw_counts.findings != self.retained_counts.findings + 1):
            raise ValueError("dedup evidence is not one grounded recovery")
        _self_hash(self, "evidence_sha256")
        return self


class C0B6ChunkRow(ChunkRow):
    raw_first_pass_valid: bool; final_outcome: FinalOutcome
    redundant_rows: int = Field(ge=0); removed_finding_indices: list[int]
    dedup_evidence_sha256: Sha256 | None

    @model_validator(mode="after")
    def exact_outcome(self) -> "C0B6ChunkRow":
        if self.first_pass_valid != self.raw_first_pass_valid \
                or self.removed_finding_indices != sorted(set(self.removed_finding_indices)):
            raise ValueError("raw outcome flags or removed indices differ")
        normalized = self.final_outcome == "NORMALIZED_DUPLICATE"
        if normalized != bool(self.redundant_rows):
            raise ValueError("normalized outcome differs from recovery count")
        if normalized and not (
                not self.raw_first_pass_valid and self.eventual_valid
                and self.redundant_rows == len(self.removed_finding_indices) == 1
                and self.dedup_evidence_sha256 is not None
                and self.raw_findings == self.retained_findings + 1
                and self.raw_grounded_findings == self.raw_findings
                and self.retained_grounded_findings == self.retained_findings):
            raise ValueError("normalized chunk does not preserve recovery facts")
        if not normalized and (self.removed_finding_indices
                               or self.dedup_evidence_sha256 is not None):
            raise ValueError("non-normalized chunk carries dedup evidence")
        if self.final_outcome == "RAW_VALID" and not self.eventual_valid \
                or self.final_outcome == "INVALID" and self.eventual_valid:
            raise ValueError("final outcome differs from eventual validity")
        if self.final_outcome == "RAW_VALID" and not self.raw_first_pass_valid:
            retried = (self.charged_attempt_count >= 2 and
                       self.strict_schema_invalid_attempts +
                       self.semantic_invalid_attempts >= 1)
            if not retried:
                raise ValueError("raw-valid retry lacks prior invalid evidence")
        return self


class C0B6DocumentRow(DocumentRow):
    chunks: list[C0B6ChunkRow] = Field(min_length=1)
    redundant_rows: int = Field(ge=0); affected_work_ids: list[Sha256]
    normalized_duplicate_chunks: int = Field(ge=0); affected_document: bool

    @model_validator(mode="after")
    def exact_recovery(self) -> "C0B6DocumentRow":
        affected = [row.work_id for row in self.chunks
                    if row.final_outcome == "NORMALIZED_DUPLICATE"]
        if (self.redundant_rows != sum(row.redundant_rows for row in self.chunks)
                or self.normalized_duplicate_chunks != len(affected)
                or self.affected_work_ids != sorted(affected)
                or self.affected_document != bool(affected)):
            raise ValueError("document recovery differs from chunks")
        return self


class RawMetrics(StrictModel):
    raw_findings: int = Field(ge=0); raw_grounded_findings: int = Field(ge=0)
    first_pass_invalid_chunks: int = Field(ge=0)
    raw_semantic_invalid_attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def grounding(self) -> "RawMetrics":
        if self.raw_grounded_findings > self.raw_findings:
            raise ValueError("raw grounding exceeds findings")
        return self


class RetainedMetrics(StrictModel):
    documents: list[C0B6DocumentRow]
    category_metrics: dict[Category, CategoryMetric]
    macro_f1: ExactFraction; micro_f1: ExactFraction
    retained_findings: int = Field(ge=0); retained_grounded_findings: int = Field(ge=0)
    negative_false_positive_documents: int = Field(ge=0)
    negative_retained_findings: int = Field(ge=0)
    injection_pairs: list[InjectionPairRow]; injection_pairs_measured: int = Field(ge=0)
    injection_events: int = Field(ge=0); robustness_failures: int = Field(ge=0)
    boundary_documents: int = Field(ge=0); boundary_passed: int = Field(ge=0)
    length_outcomes: int = Field(ge=0); eventual_invalid_chunks: int = Field(ge=0)
    context_headroom_failures: int = Field(ge=0); channel_violations: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_metrics(self) -> "RetainedMetrics":
        negatives = [row for row in self.documents if not row.expected_categories and
                     row.stratum in {"negative_clean", "negative_near_miss"}]
        if (set(self.category_metrics) != {"pii", "financial", "contact", "demographic"}
                or self.retained_grounded_findings > self.retained_findings
                or len({row.doc_id for row in self.documents}) != len(self.documents)
                or self.negative_false_positive_documents != sum(
                    row.retained_findings > 0 for row in negatives)
                or self.negative_retained_findings != sum(
                    row.retained_findings for row in negatives)
                or self.negative_false_positive_documents > self.negative_retained_findings):
            raise ValueError("retained metrics differ from document evidence")
        return self


class LanePlan(PolicyBoundStrictModel):
    version: Literal["c0b6-lane-plan-v1"]
    lane_id: F72LaneId; seed: Literal[20260811, 20260818]; candidate: Candidate
    parent_evidence: ParentBinding; work: list[C0B6PublicWork] = Field(min_length=92, max_length=92)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def exact_plan(self) -> "LanePlan":
        if self.seed != LANE_SEEDS[self.lane_id] \
                or len({row.work_id for row in self.work}) != 92 \
                or len({row.candidate_id for row in self.work}) != 1 \
                or any(row.phase != LANE_PHASES[self.lane_id] for row in self.work):
            raise ValueError("lane plan identity or work census differs")
        _self_hash(self, "plan_sha256")
        return self


class AcceptancePlan(PolicyBoundStrictModel):
    version: Literal["c0b6-acceptance-plan-v1"]
    lane_id: Literal["C44_1"]; seed: Literal[1]; candidate: Candidate
    parent_evidence: ParentBinding; work: list[C0B6PublicWork] = Field(min_length=44, max_length=44)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def exact_plan(self) -> "AcceptancePlan":
        if len({row.work_id for row in self.work}) != 44 \
                or len({row.candidate_id for row in self.work}) != 1 \
                or any(row.phase != "F_ACCEPTANCE" for row in self.work):
            raise ValueError("C44 work differs from frozen acceptance lane")
        _self_hash(self, "plan_sha256")
        return self


class PlanActivation(PolicyBoundStrictModel):
    version: Literal["c0b6-plan-activation-v1"]
    plan_sha256: Sha256; prerequisite_sha256: Sha256
    activated_work_ids: list[Sha256]; inactive_work_ids: list[Sha256]

    @model_validator(mode="after")
    def exact_partition(self) -> "PlanActivation":
        if any(rows != sorted(set(rows)) for rows in (
                self.activated_work_ids, self.inactive_work_ids)) \
                or set(self.activated_work_ids) & set(self.inactive_work_ids):
            raise ValueError("activation work partition is not exact")
        return self


class CursorTransition(PolicyBoundStrictModel):
    version: Literal["c0b6-cursor-transition-v1"]
    from_lane_id: LaneId; to_lane_id: LaneId; from_aggregate_sha256: Sha256
    to_plan_sha256: Sha256; completed_work_census_sha256: Sha256
    transitioned_at_utc: UtcRfc3339; transition_sha256: Sha256

    @model_validator(mode="after")
    def exact_transition(self) -> "CursorTransition":
        if (self.from_lane_id, self.to_lane_id) not in {
                ("F72_20260811", "F72_20260818"), ("F72_20260818", "C44_1")}:
            raise ValueError("cursor transition skips frozen order")
        _self_hash(self, "transition_sha256")
        return self


class RuntimeEvent(PolicyBoundStrictModel):
    version: Literal["c0b6-runtime-event-v1"]
    event: Literal["DISPATCHING", "RAW_VALID", "NORMALIZED_DUPLICATE", "INVALID",
                   "ORPHANED", "CANCELLED"]
    lane_id: LaneId; source_attempt_id: Sha256; request_sha256: Sha256
    nonce: Nonce; occurred_at_utc: UtcRfc3339; event_sha256: Sha256

    @model_validator(mode="after")
    def exact_event(self) -> "RuntimeEvent":
        _self_hash(self, "event_sha256")
        return self


class ContextControl(PolicyBoundStrictModel):
    version: Literal["c0b6-context-control-v1"]
    control_id: Sha256; kind: Literal["context_probe"]
    lane_id: Literal["F72_20260811"]; purpose: Literal["c0b6_stage_f_candidate_context"]
    candidate_id: Sha256; model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    config_sha256: Sha256; prompt_sha256: Sha256; minimum_context_length: Literal[8192]
    trigger_rule: Literal["first_bounded_http_terminal_seed20260811"]
    payload_sha256: Sha256


class ContextEvidence(PolicyBoundStrictModel):
    version: Literal["c0b6-context-evidence-v1"]
    control_id: Sha256; lane_id: Literal["F72_20260811"]
    purpose: Literal["c0b6_stage_f_candidate_context"]; candidate_id: Sha256
    model: Literal["qwen3.6:27b"]
    model_digest: Literal[
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"]
    config_sha256: Sha256; prompt_sha256: Sha256; expected_num_ctx: Literal[8192]
    observed_context_length: int = Field(gt=0); trigger_work_id: Sha256
    trigger_attempt_id: Sha256; trigger_request_sha256: Sha256; trigger_nonce: Nonce
    state: Literal["PASSED"]; response_sha256: Sha256

    @model_validator(mode="after")
    def allocation(self) -> "ContextEvidence":
        if self.observed_context_length < self.expected_num_ctx:
            raise ValueError("passed context evidence is undersized")
        return self


class CancellationControl(PolicyBoundStrictModel):
    version: Literal["c0b6-cancellation-control-v1"]
    control_id: Sha256; kind: Literal["cancellation_probe"]
    lane_id: Literal["F72_20260811"]; candidate_id: Sha256; seed: Literal[20260811]
    prompt_sha256: Sha256; source_doc_id: Literal["pos_pii_013"]; chunk_index: Literal[0]
    nonce: Nonce; request_sha256: Sha256; deadline_seconds: Literal[600]
    max_close_after_first_byte_ms: Literal[5000]; health_not_before_ms: Literal[2000]


class HealthControl(PolicyBoundStrictModel):
    version: Literal["c0b6-health-control-v1"]
    control_id: Sha256; kind: Literal["cancellation_health"]
    lane_id: Literal["F72_20260811"]; candidate_id: Sha256; seed: Literal[20260811]
    prompt_sha256: Sha256; source_doc_id: Literal["pos_pii_013"]; chunk_index: Literal[0]
    nonce: Nonce; health_work_id: Sha256; request_sha256: Sha256
    deadline_seconds: Literal[600]


class LanePlanEnvelope(StrictModel):
    plan_sha256: Sha256; payload: LanePlan

    @model_validator(mode="after")
    def exact_owner(self) -> "LanePlanEnvelope":
        if self.plan_sha256 != self.payload.plan_sha256:
            raise ValueError("lane envelope differs from payload")
        return self


class AcceptancePlanEnvelope(StrictModel):
    plan_sha256: Sha256; payload: AcceptancePlan

    @model_validator(mode="after")
    def exact_owner(self) -> "AcceptancePlanEnvelope":
        if self.plan_sha256 != self.payload.plan_sha256:
            raise ValueError("acceptance envelope differs from payload")
        return self


class ControlPlan(StrictModel):
    context: ContextControl; cancellation: CancellationControl; health: HealthControl


class MasterPlan(PolicyBoundStrictModel):
    version: Literal["c0b6-master-plan-v1"]
    parent_binding: ParentBinding; lane_order: list[LaneId] = Field(min_length=3, max_length=3)
    lane_plans: list[LanePlanEnvelope] = Field(min_length=2, max_length=2)
    control_plan: ControlPlan; acceptance_template: AcceptancePlanEnvelope

    @model_validator(mode="after")
    def exact_tree(self) -> "MasterPlan":
        expected = ["F72_20260811", "F72_20260818", "C44_1"]
        nested = [row.payload for row in self.lane_plans] + [self.acceptance_template.payload]
        own = (self.policy_id, self.policy_sha256, self.protocol_sha256)
        controls = (self.control_plan.context, self.control_plan.cancellation,
                    self.control_plan.health)
        if (self.lane_order != expected
                or [row.lane_id for row in nested] != expected
                or any((row.policy_id, row.policy_sha256, row.protocol_sha256) != own
                       or row.parent_evidence != self.parent_binding for row in nested)
                or any((row.policy_id, row.policy_sha256, row.protocol_sha256) != own
                       for row in controls)):
            raise ValueError("master plan contains mixed or reordered lineage")
        return self


class CancellationHealthEvidence(PolicyBoundStrictModel):
    version: Literal["c0b6-cancellation-health-evidence-v1"]
    lane_id: Literal["F72_20260811"]; candidate_id: Sha256; prompt_sha256: Sha256
    cancel_control_id: Sha256; cancel_attempt_id: Sha256
    cancel_state: Literal["CANCELLED_UNVERIFIED"]; cancel_first_byte_seen: bool
    cancel_elapsed_ms: int = Field(ge=0); health_control_id: Sha256; health_work_id: Sha256
    health_attempt_ids: list[Sha256] = Field(min_length=1)
    not_before_utc: UtcRfc3339; started_at_utc: UtcRfc3339; eventual_valid: bool
    retained_grounded_pii: bool
    authoritative_done_reason: str | None = Field(default=None, min_length=1, max_length=80)
    max_answered_prompt_eval_count: int | None = Field(default=None, ge=0)
    length_outcomes: int = Field(ge=0); headroom_passed: bool; tools_empty: bool
    images_empty: bool; unknown_message_fields_empty: bool; schema_escape_empty: bool
    passed: bool; failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_evidence(self) -> "CancellationHealthEvidence":
        _ordered_subset(self.failure_reasons, CANCELLATION_FAILURE_REASONS,
                        "cancellation failure reasons")
        if (self.passed != (not self.failure_reasons)
                or self.retained_grounded_pii and not self.eventual_valid
                or self.authoritative_done_reason is None and self.eventual_valid
                or self.max_answered_prompt_eval_count is None and self.eventual_valid
                or len(set(self.health_attempt_ids)) != len(self.health_attempt_ids)):
            raise ValueError("cancellation/health facts are contradictory")
        return self


class LaneAggregate(PolicyBoundStrictModel):
    version: Literal["c0b6-lane-aggregate-v1"]
    lane_id: F72LaneId; seed: Literal[20260811, 20260818]; lane_plan_sha256: Sha256
    parent_binding: ParentBinding; candidate: Candidate; planned_chunks: Literal[92]
    completed_chunks: int = Field(ge=0, le=92); raw_metrics: RawMetrics
    retained_metrics: RetainedMetrics; recovery_counters: RecoveryCounters
    context_evidence_sha256: Sha256 | None
    cancellation_health_evidence_sha256: Sha256 | None
    passed: bool; failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "LaneAggregate":
        _ordered_subset(self.failure_reasons, LANE_FAILURE_REASONS, "lane failure reasons")
        first = self.lane_id == "F72_20260811" and self.seed == 20260811
        second = self.lane_id == "F72_20260818" and self.seed == 20260818
        noncontrol = any(reason != "cancellation_health_failure"
                         for reason in self.failure_reasons)
        expected = {
            "negative_false_positive_above_2":
                self.retained_metrics.negative_false_positive_documents > 2,
            "negative_retained_findings_above_2":
                self.retained_metrics.negative_retained_findings > 2,
        }
        if (not (first or second)
                or first != (self.context_evidence_sha256 is not None)
                or second and self.cancellation_health_evidence_sha256 is not None
                or first and self.cancellation_health_evidence_sha256 is None and not noncontrol
                or "cancellation_health_failure" in self.failure_reasons and not first
                or "cancellation_health_failure" in self.failure_reasons
                   and self.cancellation_health_evidence_sha256 is None
                or any((reason in self.failure_reasons) != applies
                       for reason, applies in expected.items())
                or self.passed != (not self.failure_reasons)):
            raise ValueError("lane result differs from controls or exact review gates")
        return self


class C44ScoredAggregate(PolicyBoundStrictModel):
    version: Literal["c0b6-c44-scored-v1"]
    lane_id: Literal["C44_1"]; seed: Literal[1]; acceptance_plan_sha256: Sha256
    parent_binding: ParentBinding; candidate: Candidate; planned_chunks: Literal[44]
    completed_chunks: int = Field(ge=0, le=44); raw_metrics: RawMetrics
    retained_metrics: RetainedMetrics; recovery_counters: RecoveryCounters
    component_passed: bool; failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "C44ScoredAggregate":
        _ordered_subset(self.failure_reasons, C44_FAILURE_REASONS, "C44 failure reasons")
        if self.component_passed != (not self.failure_reasons):
            raise ValueError("C44 pass differs from evidence reasons")
        return self


class RecallCount(StrictModel):
    true_positives: int = Field(ge=0); support: int = Field(ge=0)


class AcceptanceTotals(StrictModel):
    document_count: int = Field(ge=0); positive_documents: int = Field(ge=0)
    negative_documents: int = Field(ge=0); injection_pairs: int = Field(ge=0)
    boundary_documents: int = Field(ge=0); truncation_documents: int = Field(ge=0)
    expected_chunks: int = Field(ge=0); completed_chunks: int = Field(ge=0)
    first_pass_invalid_chunks: int = Field(ge=0); eventual_invalid_chunks: int = Field(ge=0)
    raw_findings: int = Field(ge=0); raw_grounded_findings: int = Field(ge=0)
    retained_findings: int = Field(ge=0); retained_grounded_findings: int = Field(ge=0)
    category_recall: dict[Category, RecallCount]
    negative_false_positive_documents: int = Field(ge=0)
    negative_retained_findings: int = Field(ge=0)
    injection_pairs_measured: int = Field(ge=0); injection_events: int = Field(ge=0)
    robustness_failures: int = Field(ge=0); boundary_passed: int = Field(ge=0)
    truncation_completed: int = Field(ge=0); length_outcomes: int = Field(ge=0)
    context_failures: int = Field(ge=0); channel_violations: int = Field(ge=0)
    cancellation_health_passed: bool; provenance_passed: bool; safety_passed: bool
    recovery_counters: RecoveryCounters

    @model_validator(mode="after")
    def exact_totals(self) -> "AcceptanceTotals":
        if (set(self.category_recall) != {"pii", "financial", "contact", "demographic"}
                or self.raw_grounded_findings > self.raw_findings
                or self.retained_grounded_findings > self.retained_findings
                or self.negative_false_positive_documents > self.negative_retained_findings):
            raise ValueError("acceptance totals are contradictory")
        return self


class ComponentHashes(StrictModel):
    c44_rerun_aggregate_sha256: Sha256
    d50_confirmation_aggregate_sha256: Sha256
    f72_seed20260811_aggregate_sha256: Sha256


class AcceptanceAggregate(PolicyBoundStrictModel):
    version: Literal["c0b6-acceptance-aggregate-v1"]
    acceptance_plan_sha256: Sha256; component_hashes: ComponentHashes
    totals: AcceptanceTotals; recovery_counters: RecoveryCounters
    passed: bool; failure_reasons: list[str]

    @model_validator(mode="after")
    def exact_gate(self) -> "AcceptanceAggregate":
        _ordered_subset(self.failure_reasons, ACCEPTANCE_FAILURE_REASONS,
                        "acceptance failure reasons")
        expected = {
            "negative_false_positive_above_4":
                self.totals.negative_false_positive_documents > 4,
            "negative_retained_findings_above_4":
                self.totals.negative_retained_findings > 4,
        }
        if (self.passed != (not self.failure_reasons)
                or self.totals.recovery_counters != self.recovery_counters
                or any((reason in self.failure_reasons) != applies
                       for reason, applies in expected.items())):
            raise ValueError("acceptance result differs from exact review gates")
        return self


class LaneAggregateHashes(StrictModel):
    f72_seed20260811_sha256: Sha256
    f72_seed20260818_sha256: Sha256 | None
    c44_scored_sha256: Sha256 | None


class Result(PolicyBoundStrictModel):
    version: Literal["c0b6-result-v1"]
    terminal: QualityTerminal; reason: str; master_plan_sha256: Sha256
    lane_aggregate_sha256s: LaneAggregateHashes
    acceptance_aggregate_sha256: Sha256 | None; selection: Selection | None

    @model_validator(mode="after")
    def exact_terminal(self) -> "Result":
        if self.reason not in QUALITY_REASON_BY_TERMINAL[self.terminal]:
            raise ValueError("quality result reason differs from terminal")
        confirmed = self.terminal == "CONFIRMED"
        complete = self.reason in {
            "complete_public_acceptance_passed", "complete_corpus_acceptance_failed"}
        second = self.reason in {
            "seed20260818_no_qualifier", "complete_public_acceptance_passed",
            "complete_corpus_acceptance_failed"}
        hashes = self.lane_aggregate_sha256s
        if (confirmed != (self.selection is not None)
                or complete != (self.acceptance_aggregate_sha256 is not None)
                or second != (hashes.f72_seed20260818_sha256 is not None)
                or complete != (hashes.c44_scored_sha256 is not None)):
            raise ValueError("quality result ownership differs from stop point")
        return self


class ConfirmedFacts(StrictModel):
    confirmed: Literal[True]


class DeterministicStopFacts(StrictModel):
    deterministic_stop: Literal[True]
    reason: Literal[
        "seed20260811_no_qualifier", "seed20260811_control_gate_failed",
        "seed20260818_no_qualifier", "complete_corpus_acceptance_failed"]


class Completion(PolicyBoundStrictModel):
    version: Literal["c0b6-completion-v1"]
    outcome: QualityTerminal; artifact_sha256: Sha256
    facts: ConfirmedFacts | DeterministicStopFacts

    @model_validator(mode="after")
    def exact_facts(self) -> "Completion":
        if (self.outcome == "CONFIRMED") != isinstance(self.facts, ConfirmedFacts):
            raise ValueError("completion facts differ from outcome")
        return self


class FailureEvidence(PolicyBoundStrictModel):
    version: Literal["c0b6-failure-evidence-v1"]
    terminal: FailureTerminal; reason: str; failure_origin: FailureOrigin
    lane_id: LaneId | None
    plan_sha256: Sha256 | None; attempt_id: Sha256 | None; control_id: Sha256 | None
    charged_call_total: int = Field(ge=0); evidence_sha256: Sha256

    @model_validator(mode="after")
    def exact_failure(self) -> "FailureEvidence":
        attemptless = self.terminal in {"BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED"}
        if (self.reason != FAILURE_REASON_BY_TERMINAL[self.terminal]
                or self.failure_origin not in
                FAILURE_ORIGINS_BY_TERMINAL[self.terminal]
                or attemptless and self.attempt_id is not None
                or self.terminal == "FAILED_SAFETY" and self.attempt_id is None):
            raise ValueError("failure evidence differs from terminal contract")
        _self_hash(self, "evidence_sha256")
        return self


class FailureResult(PolicyBoundStrictModel):
    version: Literal["c0b6-failure-v1"]
    terminal: FailureTerminal; reason: str; failure_origin: FailureOrigin
    evidence_sha256: Sha256
    charged_call_total: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_failure(self) -> "FailureResult":
        if (self.reason != FAILURE_REASON_BY_TERMINAL[self.terminal]
                or self.failure_origin not in
                FAILURE_ORIGINS_BY_TERMINAL[self.terminal]):
            raise ValueError("failure result reason differs from terminal")
        return self


class BackupAnchor(PolicyBoundStrictModel):
    version: Literal["c0b6-backup-anchor-v1"]
    run_id: str = Field(min_length=1); header_sha256: Sha256
    terminal_artifact_sha256: Sha256; completion_sha256: Sha256 | None
    parent_binding: ParentBinding; source_binding: SourceBinding; anchor_sha256: Sha256

    @model_validator(mode="after")
    def exact_anchor(self) -> "BackupAnchor":
        _self_hash(self, "anchor_sha256")
        return self


class BackupReceipt(PolicyBoundStrictModel):
    version: Literal["c0b6-backup-receipt-v1"]
    anchor_sha256: Sha256; snapshot_run_relative_path: str = Field(min_length=1)
    snapshot_sha256: Sha256; snapshot_size_bytes: int = Field(gt=0)
    integrity_check: Literal["ok"]; foreign_key_violations: Literal[0]
    created_at_utc: UtcRfc3339; receipt_sha256: Sha256

    @field_validator("snapshot_run_relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts or str(path) != value:
            raise ValueError("snapshot path must be canonical and run-relative")
        return value

    @model_validator(mode="after")
    def exact_receipt(self) -> "BackupReceipt":
        _self_hash(self, "receipt_sha256")
        return self


class RuntimePause(PolicyBoundStrictModel):
    state: RuntimeState; reason: str

    @model_validator(mode="after")
    def exact_reason(self) -> "RuntimePause":
        if self.reason != RUNTIME_REASON_BY_STATE[self.state]:
            raise ValueError("runtime pause reason differs from state")
        return self


class FalsePositiveDocument(StrictModel):
    component: Component; document_id: str = Field(min_length=1)
    categories: list[Category]; public_template_family: Literal[
        "clean_sprint_retrospective", "clean_boiler_maintenance_log",
        "clean_library_acquisition_notes", "clean_cafeteria_menu_cycle",
        "clean_parking_structure_survey", "near_miss_checksum_failed_barcode",
        "near_miss_ssn_shaped_part_number", "near_miss_phone_shaped_chassis_serial",
        "near_miss_invalid_routing_cost_centre",
        "near_miss_invalid_iban_template_placeholder"]
    negative_retained_findings: int = Field(gt=0)

    @model_validator(mode="after")
    def exact_categories(self) -> "FalsePositiveDocument":
        order = ("pii", "financial", "contact", "demographic")
        if not self.categories \
                or self.categories != sorted(set(self.categories), key=order.index):
            raise ValueError("false-positive categories are not frozen-order unique")
        if self.public_template_family != public_template_family(self.document_id):
            raise ValueError("public template family differs from frozen document ID")
        return self


class ComponentCount(StrictModel):
    negative_false_positive_documents: int = Field(ge=0)
    negative_retained_findings: int = Field(ge=0)

    @model_validator(mode="after")
    def invariant(self) -> "ComponentCount":
        if self.negative_false_positive_documents > self.negative_retained_findings:
            raise ValueError("component document count exceeds retained rows")
        return self


class PublicSummary(PolicyBoundStrictModel):
    version: Literal["c0b6-public-summary-v1"]
    run_id: str = Field(min_length=1); terminal: QualityTerminal; reason: str
    result_sha256: Sha256; completion_sha256: Sha256
    lane_aggregate_sha256s: LaneAggregateHashes
    acceptance_aggregate_sha256: Sha256 | None
    false_positive_documents: list[FalsePositiveDocument]
    fresh_f_union_document_ids: list[str] | None
    fresh_f_intersection_document_ids: list[str] | None
    component_counts: dict[Component, ComponentCount | None]
    total_human_rejection_rows: int | None = Field(default=None, ge=0)
    summary_sha256: Sha256

    @model_validator(mode="after")
    def exact_summary(self) -> "PublicSummary":
        components = ("C44_RERUN", "D50_CONFIRMATION",
                      "F72_SEED20260811", "F72_SEED20260818")
        rows = self.false_positive_documents
        second = self.lane_aggregate_sha256s.f72_seed20260818_sha256 is not None
        final = self.acceptance_aggregate_sha256 is not None
        expected_presence = {
            "C44_RERUN": final, "D50_CONFIRMATION": True,
            "F72_SEED20260811": True, "F72_SEED20260818": second,
        }
        complete = self.reason in {
            "complete_public_acceptance_passed", "complete_corpus_acceptance_failed"}
        expected_second = self.reason in {
            "seed20260818_no_qualifier", "complete_public_acceptance_passed",
            "complete_corpus_acceptance_failed"}
        confirmed = self.terminal == "CONFIRMED"
        if (self.reason not in QUALITY_REASON_BY_TERMINAL[self.terminal]
                or set(self.component_counts) != set(components)
                or second != expected_second or final != complete
                or (self.lane_aggregate_sha256s.c44_scored_sha256 is not None) != complete
                or confirmed != (self.reason == "complete_public_acceptance_passed")
                or any((self.component_counts[key] is not None) != present
                       for key, present in expected_presence.items())
                or rows != sorted(rows, key=lambda row: (row.component, row.document_id))
                or len({(row.component, row.document_id) for row in rows}) != len(rows)
                or (self.fresh_f_union_document_ids is not None) != second
                or (self.fresh_f_intersection_document_ids is not None) != second
                or (self.total_human_rejection_rows is not None) != final):
            raise ValueError("public summary ownership, order, or nullability differs")
        for key, count in self.component_counts.items():
            owned = [row for row in rows if row.component == key]
            if count is None and owned:
                raise ValueError("inactive component owns public summary rows")
            if count is not None and (
                    count.negative_false_positive_documents != len(owned)
                    or count.negative_retained_findings != sum(
                        row.negative_retained_findings for row in owned)):
                raise ValueError("public summary rows differ from component counts")
        if final:
            included = ("C44_RERUN", "D50_CONFIRMATION", "F72_SEED20260811")
            expected_total = sum(
                self.component_counts[key].negative_retained_findings
                for key in included if self.component_counts[key] is not None)
            if self.total_human_rejection_rows != expected_total:
                raise ValueError("human rejection rows differ from final components")
        if second:
            first_ids = {row.document_id for row in rows
                         if row.component == "F72_SEED20260811"}
            second_ids = {row.document_id for row in rows
                          if row.component == "F72_SEED20260818"}
            if (self.fresh_f_union_document_ids != sorted(first_ids | second_ids)
                    or self.fresh_f_intersection_document_ids != sorted(
                        first_ids & second_ids)):
                raise ValueError("fresh-seed union/intersection differs from rows")
        _self_hash(self, "summary_sha256")
        return self


ArtifactModel: TypeAlias = (
    RunHeader | MasterPlan | LanePlan | AcceptancePlan | PlanActivation |
    CursorTransition | RuntimeEvent | ContextControl | ContextEvidence |
    CancellationControl | HealthControl | CancellationHealthEvidence |
    DedupEvidence | LaneAggregate | C44ScoredAggregate | AcceptanceAggregate |
    Result | Completion | FailureEvidence | FailureResult | BackupAnchor | BackupReceipt)
ModelT = TypeVar("ModelT", bound=BaseModel)
_VERSION_MODELS: dict[str, type[BaseModel]] = {
    "c0b6-run-header-v1": RunHeader, "c0b6-master-plan-v1": MasterPlan,
    "c0b6-lane-plan-v1": LanePlan, "c0b6-acceptance-plan-v1": AcceptancePlan,
    "c0b6-plan-activation-v1": PlanActivation,
    "c0b6-cursor-transition-v1": CursorTransition,
    "c0b6-runtime-event-v1": RuntimeEvent,
    "c0b6-context-control-v1": ContextControl,
    "c0b6-context-evidence-v1": ContextEvidence,
    "c0b6-cancellation-control-v1": CancellationControl,
    "c0b6-health-control-v1": HealthControl,
    "c0b6-cancellation-health-evidence-v1": CancellationHealthEvidence,
    "c0b6-dedup-evidence-v1": DedupEvidence,
    "c0b6-lane-aggregate-v1": LaneAggregate,
    "c0b6-c44-scored-v1": C44ScoredAggregate,
    "c0b6-acceptance-aggregate-v1": AcceptanceAggregate,
    "c0b6-result-v1": Result, "c0b6-completion-v1": Completion,
    "c0b6-failure-evidence-v1": FailureEvidence,
    "c0b6-failure-v1": FailureResult, "c0b6-backup-anchor-v1": BackupAnchor,
    "c0b6-backup-receipt-v1": BackupReceipt,
}


def validate_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("C0B-6 artifact must be a mapping")
    require_current_payload(value)
    model = _VERSION_MODELS.get(value.get("version"))
    if model is None:
        raise ValueError("unknown C0B-6 artifact version")
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_as(model: type[ModelT], value: Mapping[str, Any]) -> dict[str, Any]:
    require_current_payload(value)
    return model.model_validate(value, strict=True).model_dump(mode="json")


def validate_public_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the non-durable derived view outside the artifact dispatcher."""
    require_current_payload(value)
    return PublicSummary.model_validate(value, strict=True).model_dump(mode="json")


if sha256_json(PUBLIC_TEMPLATE_FAMILY_PAYLOAD) != PUBLIC_TEMPLATE_FAMILY_SHA256:
    raise RuntimeError("frozen public template mapping differs from its SHA-256")
