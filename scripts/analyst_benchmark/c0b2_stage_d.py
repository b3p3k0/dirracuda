"""Pure Stage-D evidence derivation, aggregation, and factor decisions.

Inputs are bounded values already persisted by the runtime.  This module has no
checkpoint, filesystem, transport, Ollama, or private-corpus access.

DISPOSITION: benchmark-only scorer; remove after the frozen C0B selection is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, Mapping, Sequence
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import chunker
from .c0b2_plan import attempt_id as stable_attempt_id
from .c0b2_public_schema import (
    CandidateSelection,
    ContextProbeEvidence,
    DPhaseCandidate,
    DPhasePlan,
    PublicWork,
    Sha256,
    sha256_json,
)
from .c0b2_public_scoring import ordered_reasons
from .c0b2_schema import CATEGORIES, canonical_json
from .c0b2_stage_c import classify_answer
from .c0b2_stage_d_plan import D1_PANEL, D2_CHUNKS, D50Corpus, StageDContextControl
from .metrics import ground_finding

AGGREGATE_VERSION = "stage-d-phase-aggregate-v1"
DECISION_VERSION = "stage-d-decision-v1"
_D1_REASONS = (
    "length_outcome_present", "eventual_invalid_present",
    "raw_grounding_below_0_99", "expected_category_missing",
    "unsupported_category_present",
)
_D2_REASONS = (
    "boundary_identifier_missing", "unsupported_category_present",
    "eventual_invalid_present", "raw_grounding_below_0_99",
    "length_outcome_present", "context_headroom_violation",
)
_D4_REASONS = (
    "eventual_invalid_or_missing_chunk", "first_pass_invalid_above_1",
    "raw_grounding_below_0_99", "retained_grounding_below_1_00",
    "pii_recall_below_6_of_6", "financial_recall_below_6_of_6",
    "contact_recall_below_6_of_6", "demographic_recall_below_6_of_6",
    "negative_false_positive_present", "boundary_identifier_missing",
    "length_outcome_present", "context_headroom_violation",
    "channel_violation_present",
)
_ATTEMPT_STATES = {
    "ACCEPTED", "SCHEMA_INVALID", "RETRYABLE_TRANSPORT",
    "ORPHANED_UNKNOWN", "CANCELLED_UNVERIFIED",
}
_CALL_CLASSES = {"scored", "schema_retry", "transport_orphan"}


class _DecodedObject(list[tuple[str, Any]]):
    """Preserve every decoded object value, including duplicate-key values."""


def _decoded_marker_empty(marker: str, raw: str) -> bool:
    """Scan decoded JSON scalar values using exact Unicode-NFC comparison."""
    try:
        value = json.loads(
            raw, object_pairs_hook=_DecodedObject,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (TypeError, ValueError):
        return True
    target = unicodedata.normalize("NFC", marker)

    def contains(node: Any) -> bool:
        if type(node) is str:
            return target in unicodedata.normalize("NFC", node)
        if isinstance(node, _DecodedObject):
            return any(contains(item) for _key, item in node)
        if type(node) is list:
            return any(contains(item) for item in node)
        return False

    return not contains(value)


class StageDError(RuntimeError):
    """Stage-D evidence is incomplete, malformed, or inconsistent."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class AttemptEvidence(_StrictModel):
    """Exact bounded facts copied from one durable scored-attempt row."""

    attempt_id: Sha256
    work_id: Sha256
    attempt_no: int = Field(gt=0)
    call_class: Literal["scored", "schema_retry", "transport_orphan"]
    request_sha256: Sha256
    state: Literal[
        "ACCEPTED", "SCHEMA_INVALID", "RETRYABLE_TRANSPORT",
        "ORPHANED_UNKNOWN", "CANCELLED_UNVERIFIED",
    ]
    response: str | None
    done_reason: str | None
    prompt_eval_count: int | None = Field(default=None, ge=0)
    tools_empty: bool | None
    images_empty: bool | None
    unknown_message_fields_empty: bool | None

    @model_validator(mode="after")
    def exact_terminal_shape(self) -> "AttemptEvidence":
        answered = self.state in {"ACCEPTED", "SCHEMA_INVALID"}
        present = (
            type(self.response) is str,
            type(self.done_reason) is str and bool(self.done_reason),
            type(self.prompt_eval_count) is int,
            type(self.tools_empty) is bool,
            type(self.images_empty) is bool,
            type(self.unknown_message_fields_empty) is bool,
        )
        if answered != all(present) or (not answered and any(value is not None for value in (
                self.response, self.done_reason, self.prompt_eval_count,
                self.tools_empty, self.images_empty,
                self.unknown_message_fields_empty))):
            raise ValueError("attempt fields contradict its bounded terminal state")
        return self


class _Reasoned(_StrictModel):
    passed: bool
    failure_reasons: list[str]


class D1Quality(_Reasoned):
    planned_chunks: Literal[5]
    completed_chunks: int = Field(ge=0, le=5)
    eventual_invalid_chunks: int = Field(ge=0, le=5)
    raw_findings: int = Field(ge=0)
    raw_grounded_findings: int = Field(ge=0)
    expected_category_pass_documents: int = Field(ge=0, le=5)
    unsupported_category_documents: int = Field(ge=0, le=5)
    length_outcomes: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_reasons(self) -> "D1Quality":
        if self.completed_chunks + self.eventual_invalid_chunks != self.planned_chunks:
            raise ValueError("D1 eventual-valid/invalid counts do not partition work")
        failures = {
            "length_outcome_present": self.length_outcomes > 0,
            "eventual_invalid_present": self.eventual_invalid_chunks > 0,
            "raw_grounding_below_0_99": self.raw_findings > 0 and
            100 * self.raw_grounded_findings < 99 * self.raw_findings,
            "expected_category_missing": self.expected_category_pass_documents
            != len(D1_PANEL),
            "unsupported_category_present": self.unsupported_category_documents > 0,
        }
        _check_reasoned(self, _D1_REASONS, failures)
        return self


class D1Level(_StrictModel):
    num_predict: Literal[1024, 2048, 3072, 4096]
    quality: D1Quality


class D1CandidateAggregate(_Reasoned):
    candidate_id: Sha256
    levels: list[D1Level] = Field(min_length=3, max_length=4)
    selected_num_predict: Literal[1024, 2048, 3072, 4096] | None

    @model_validator(mode="after")
    def exact_selection(self) -> "D1CandidateAggregate":
        budgets = [row.num_predict for row in self.levels]
        if budgets != sorted(set(budgets)):
            raise ValueError("D1 levels must be unique ascending budgets")
        selected = next((row.num_predict for row in self.levels if row.quality.passed), None)
        if (self.selected_num_predict != selected or self.passed != (selected is not None)
                or self.failure_reasons != ([] if selected is not None else
                                            ["no_passing_output_budget"])):
            raise ValueError("D1 candidate selection differs from its levels")
        return self


class D2Quality(_Reasoned):
    planned_documents: Literal[12]
    planned_chunks: Literal[24]
    completed_chunks: int = Field(ge=0, le=24)
    boundary_identifier_pass_documents: int = Field(ge=0, le=12)
    unsupported_category_documents: int = Field(ge=0, le=12)
    eventual_invalid_chunks: int = Field(ge=0, le=24)
    raw_findings: int = Field(ge=0)
    raw_grounded_findings: int = Field(ge=0)
    length_outcomes: int = Field(ge=0)
    headroom_violations: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_reasons(self) -> "D2Quality":
        if self.completed_chunks + self.eventual_invalid_chunks != self.planned_chunks:
            raise ValueError("D2 eventual-valid/invalid counts do not partition work")
        failures = {
            "boundary_identifier_missing": self.boundary_identifier_pass_documents
            != self.planned_documents,
            "unsupported_category_present": self.unsupported_category_documents > 0,
            "eventual_invalid_present": self.eventual_invalid_chunks > 0,
            "raw_grounding_below_0_99": self.raw_findings > 0 and
            100 * self.raw_grounded_findings < 99 * self.raw_findings,
            "length_outcome_present": self.length_outcomes > 0,
            "context_headroom_violation": self.headroom_violations > 0,
        }
        _check_reasoned(self, _D2_REASONS, failures)
        return self


class D2Level(_StrictModel):
    chunk_chars: Literal[2000, 4000, 8000]
    quality: D2Quality


class D2CandidateAggregate(_Reasoned):
    candidate_id: Sha256
    num_predict: Literal[1024, 2048, 3072, 4096]
    levels: list[D2Level] = Field(min_length=3, max_length=3)
    selected_chunk_chars: Literal[2000, 4000, 8000] | None
    overlap: Literal[256] | None

    @model_validator(mode="after")
    def exact_selection(self) -> "D2CandidateAggregate":
        if [row.chunk_chars for row in self.levels] != list(D2_CHUNKS):
            raise ValueError("D2 levels differ from the frozen chunk order")
        passing = [row.chunk_chars for row in self.levels if row.quality.passed]
        selected = max(passing) if passing else None
        if (self.selected_chunk_chars != selected
                or self.overlap != (256 if selected is not None else None)
                or self.passed != (selected is not None)
                or self.failure_reasons != ([] if selected is not None else
                                            ["no_passing_chunk"])):
            raise ValueError("D2 candidate selection differs from its levels")
        return self


class CategoryRecall(_StrictModel):
    true_positives: int = Field(ge=0, le=6)
    support: Literal[6]


class D4Quality(_Reasoned):
    expected_chunk_count: int = Field(ge=0)
    completed_eventual_valid_chunks: int = Field(ge=0)
    first_pass_invalid_chunks: int = Field(ge=0)
    raw_findings: int = Field(ge=0)
    raw_grounded_findings: int = Field(ge=0)
    retained_findings: int = Field(ge=0)
    retained_grounded_findings: int = Field(ge=0)
    category_recall: dict[Literal["pii", "financial", "contact", "demographic"],
                          CategoryRecall]
    negative_false_positive_documents: int = Field(ge=0)
    boundary_identifier_pass_documents: int = Field(ge=0)
    boundary_documents: Literal[12]
    length_outcomes: int = Field(ge=0)
    headroom_violations: int = Field(ge=0)
    context_probe_passed: Literal[True]
    tools_empty: bool
    images_empty: bool
    unknown_message_fields_empty: bool
    marker_empty: bool
    schema_escape_empty: bool

    @model_validator(mode="after")
    def exact_reasons(self) -> "D4Quality":
        if tuple(self.category_recall) != tuple(CATEGORIES):
            raise ValueError("D4 recall differs from frozen category order")
        if (self.completed_eventual_valid_chunks > self.expected_chunk_count
                or self.first_pass_invalid_chunks > self.expected_chunk_count
                or self.raw_grounded_findings > self.raw_findings
                or self.retained_grounded_findings > self.retained_findings):
            raise ValueError("D4 counts exceed their owning totals")
        channel = not all((self.tools_empty, self.images_empty,
                           self.unknown_message_fields_empty, self.marker_empty,
                           self.schema_escape_empty))
        failures = {
            "eventual_invalid_or_missing_chunk":
                self.completed_eventual_valid_chunks != self.expected_chunk_count,
            "first_pass_invalid_above_1": self.first_pass_invalid_chunks > 1,
            "raw_grounding_below_0_99": self.raw_findings > 0 and
                100 * self.raw_grounded_findings < 99 * self.raw_findings,
            "retained_grounding_below_1_00":
                self.retained_grounded_findings != self.retained_findings,
            **{f"{category}_recall_below_6_of_6":
               self.category_recall[category].true_positives != 6
               for category in CATEGORIES},
            "negative_false_positive_present":
                self.negative_false_positive_documents > 0,
            "boundary_identifier_missing":
                self.boundary_identifier_pass_documents != self.boundary_documents,
            "length_outcome_present": self.length_outcomes > 0,
            "context_headroom_violation": self.headroom_violations > 0,
            "channel_violation_present": channel,
        }
        _check_reasoned(self, _D4_REASONS, failures)
        return self


class D3CensusRow(_StrictModel):
    num_ctx: Literal[4096, 8192, 16384]
    analytical_output_fit: bool
    measured_fit: bool
    max_prompt_eval_count: int = Field(ge=0)
    num_predict: Literal[1024, 2048, 3072, 4096]
    eligible: bool

    @model_validator(mode="after")
    def exact_fit(self) -> "D3CensusRow":
        analytical = self.num_predict <= (85 * self.num_ctx) // 100
        measured = self.max_prompt_eval_count + self.num_predict <= \
            (85 * self.num_ctx) // 100
        if (self.analytical_output_fit, self.measured_fit) != (analytical, measured):
            raise ValueError("D3 census fit flags differ from exact integer evidence")
        return self


class D3CandidateAggregate(_Reasoned):
    candidate_id: Sha256
    num_predict: Literal[1024, 2048, 3072, 4096]
    chunk_chars: Literal[2000, 4000, 8000]
    overlap: Literal[256]
    high_context_plan_sha256: Sha256
    high_context_quality: D4Quality
    context_probe: ContextProbeEvidence
    context_census: list[D3CensusRow] = Field(min_length=3, max_length=3)
    selected_num_ctx: Literal[4096, 8192, 16384] | None

    @model_validator(mode="after")
    def exact_context(self) -> "D3CandidateAggregate":
        if [row.num_ctx for row in self.context_census] != [4096, 8192, 16384]:
            raise ValueError("D3 census differs from frozen context order")
        maximums = {row.max_prompt_eval_count for row in self.context_census}
        outputs = {row.num_predict for row in self.context_census}
        if maximums != {self.context_census[0].max_prompt_eval_count} or outputs != {
                self.num_predict}:
            raise ValueError("D3 census does not repeat one measured maximum/output")
        expected_eligible = [
            row.analytical_output_fit and row.measured_fit
            and self.high_context_quality.passed for row in self.context_census]
        if [row.eligible for row in self.context_census] != expected_eligible:
            raise ValueError("D3 eligibility differs from high-context evidence")
        selected = next((row.num_ctx for row in self.context_census if row.eligible), None)
        reasons = list(self.high_context_quality.failure_reasons)
        if selected is None:
            reasons.append("no_context_candidate")
        if (self.selected_num_ctx != selected or self.passed != (selected is not None)
                or self.failure_reasons != reasons):
            raise ValueError("D3 selection differs from quality/census evidence")
        return self


class D4CandidateAggregate(_StrictModel):
    candidate_id: Sha256
    selection: CandidateSelection
    context_probe: ContextProbeEvidence
    quality: D4Quality


class _AggregateBase(_StrictModel):
    version: Literal["stage-d-phase-aggregate-v1"]
    stage: Literal["D"]
    plan_sha256: Sha256
    parent_decision_sha256: Sha256
    candidate_order: list[Sha256] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_candidate_order(self) -> "_AggregateBase":
        if len(set(self.candidate_order)) != len(self.candidate_order):
            raise ValueError("aggregate candidate order must be unique")
        return self


class D1Aggregate(_AggregateBase):
    phase: Literal["D1"]
    candidates: list[D1CandidateAggregate] = Field(min_length=1, max_length=3)


class D2Aggregate(_AggregateBase):
    phase: Literal["D2"]
    candidates: list[D2CandidateAggregate] = Field(min_length=1, max_length=3)


class D3Aggregate(_AggregateBase):
    phase: Literal["D3"]
    candidates: list[D3CandidateAggregate] = Field(min_length=1, max_length=3)


class D4Aggregate(_AggregateBase):
    phase: Literal["D4"]
    candidates: list[D4CandidateAggregate] = Field(min_length=1, max_length=3)


class FinalEvidence(_StrictModel):
    candidate_id: Sha256
    selection: CandidateSelection
    evidence_source: Literal["D3_REUSE", "D4_RERUN"]
    source_aggregate_sha256: Sha256
    quality: D4Quality


class IntermediateDecision(_StrictModel):
    version: Literal["stage-d-decision-v1"]
    stage: Literal["D"]
    phase: Literal["D1", "D2", "D3"]
    plan_sha256: Sha256
    aggregate_sha256: Sha256
    outcome: Literal["CONTINUE", "INCONCLUSIVE"]
    reason: Literal[
        "phase_passed", "no_d1_output_budget_survivor",
        "no_d2_chunk_survivor", "no_d3_context_survivor",
    ]
    selections: list[DPhaseCandidate] = Field(max_length=3)

    @model_validator(mode="after")
    def exact_outcome(self) -> "IntermediateDecision":
        zero = not self.selections
        reasons = {
            "D1": "no_d1_output_budget_survivor",
            "D2": "no_d2_chunk_survivor",
            "D3": "no_d3_context_survivor",
        }
        if ((self.outcome, self.reason) !=
                (("INCONCLUSIVE", reasons[self.phase]) if zero else
                 ("CONTINUE", "phase_passed"))):
            raise ValueError("intermediate D outcome differs from its selections")
        presence = DPhasePlan._PRESENCE[
            {"D1": "D2", "D2": "D3", "D3": "D4"}[self.phase]]
        for row in self.selections:
            actual = tuple(value is not None for value in (
                row.chunk_chars, row.overlap, row.num_ctx, row.num_predict))
            if actual != presence:
                raise ValueError("intermediate D selection has wrong factor presence")
        return self


class FinalDecision(_StrictModel):
    version: Literal["stage-d-decision-v1"]
    stage: Literal["D"]
    phase: Literal["D3", "D4"]
    plan_sha256: Sha256
    aggregate_sha256: Sha256
    outcome: Literal["FINALISTS", "INCONCLUSIVE"]
    reason: Literal["finalists_selected", "no_d4_confirmation_finalist"]
    selections: list[FinalEvidence] = Field(max_length=3)

    @model_validator(mode="after")
    def exact_outcome(self) -> "FinalDecision":
        zero = not self.selections
        if ((self.outcome, self.reason) !=
                (("INCONCLUSIVE", "no_d4_confirmation_finalist") if zero else
                 ("FINALISTS", "finalists_selected"))):
            raise ValueError("final D outcome differs from its selections")
        if any(not row.quality.passed for row in self.selections):
            raise ValueError("final D selections require passing quality")
        if len({row.candidate_id for row in self.selections}) != len(self.selections):
            raise ValueError("final D candidate IDs must be unique")
        return self


@dataclass(frozen=True)
class _Finding:
    category: str
    quote: str
    offset: int
    grounded: bool


@dataclass(frozen=True)
class _WorkScore:
    work: PublicWork
    first_pass_valid: bool
    eventual_valid: bool
    findings: tuple[_Finding, ...]
    length_outcomes: int
    max_prompt_eval_count: int
    headroom_passed: bool
    tools_empty: bool
    images_empty: bool
    unknown_message_fields_empty: bool
    marker_empty: bool
    schema_escape_empty: bool


def _check_reasoned(value: _Reasoned, order: Sequence[str],
                    failures: Mapping[str, bool]) -> None:
    expected = ordered_reasons(order, failures)
    if value.failure_reasons != expected or value.passed != (not expected):
        raise ValueError("pass/reasons differ from exact evidence facts")


def _strict_plan(value: Mapping[str, Any]) -> tuple[dict[str, Any], DPhasePlan]:
    if type(value) is not dict:
        raise StageDError("Stage-D plan must be an exact JSON object")
    try:
        model = DPhasePlan.model_validate(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise StageDError("Stage-D plan fails strict validation") from exc
    payload = model.model_dump(mode="json")
    return payload, model


def _source_chunk(work: PublicWork, corpus: D50Corpus) -> tuple[str, int]:
    document = corpus.by_id().get(work.doc_id)
    if document is None or work.document_sha256 != document.document_sha256:
        raise StageDError("work document differs from the selective D50 corpus")
    view = document.view_for(work.chunk_chars)
    if document.stratum == "boundary":
        if view is None or work.view_id != view.view_id:
            raise StageDError("boundary work differs from its frozen derived view")
        source = view.text
    else:
        if view is not None or work.view_id is not None:
            raise StageDError("ordinary work unexpectedly names a derived view")
        source = document.text
    chunks = chunker.chunk(source, chunk_chars=work.chunk_chars,
                           overlap_chars=work.overlap)
    if work.chunk_index >= len(chunks):
        raise StageDError("planned chunk is absent from the selective source")
    selected = chunks[work.chunk_index]
    import hashlib
    if hashlib.sha256(selected.text.encode("utf-8")).hexdigest() != work.chunk_sha256:
        raise StageDError("planned chunk hash differs from selective source")
    return selected.text, selected.start


def derive_work_evidence(
        work: PublicWork | Mapping[str, Any], source_chunk: str, chunk_start: int,
        attempts: Sequence[AttemptEvidence | Mapping[str, Any]],
) -> _WorkScore:
    """Derive one work score only from exact work, source, and bounded attempts."""
    try:
        item = work if isinstance(work, PublicWork) else PublicWork.model_validate(
            work, strict=True)
        rows = [row if isinstance(row, AttemptEvidence) else
                AttemptEvidence.model_validate(row, strict=True)
                if type(row) is dict else (_ for _ in ()).throw(
                    TypeError("attempt must be an exact object")) for row in attempts]
    except (TypeError, ValueError) as exc:
        raise StageDError("bounded attempt evidence fails strict validation") from exc
    if type(source_chunk) is not str or type(chunk_start) is not int or chunk_start < 0:
        raise StageDError("source chunk identity has invalid types")
    if not rows:
        raise StageDError("completed Stage-D work lacks bounded attempt evidence")
    if [row.attempt_no for row in rows] != list(range(1, len(rows) + 1)):
        raise StageDError("attempt numbers must be contiguous ascending from one")
    if any((row.work_id, row.request_sha256) !=
           (item.work_id, item.request_sha256) for row in rows):
        raise StageDError("attempt authority differs from its frozen work")
    for index, row in enumerate(rows):
        if row.attempt_id != stable_attempt_id(item.work_id, row.attempt_no):
            raise StageDError("attempt ID differs from work/attempt-number identity")
        expected_class = ("scored" if index == 0 else
                          "schema_retry" if rows[index - 1].state == "SCHEMA_INVALID"
                          else "transport_orphan" if rows[index - 1].state in {
                              "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN",
                              "CANCELLED_UNVERIFIED"} else None)
        if row.call_class != expected_class:
            raise StageDError("attempt class differs from prior durable terminal state")
    answered: list[tuple[AttemptEvidence, Any]] = []
    closed = False
    for row in rows:
        if closed:
            raise StageDError("attempt appears after authoritative answer history closed")
        if row.state not in _ATTEMPT_STATES or row.call_class not in _CALL_CLASSES:
            raise StageDError("attempt has an unsupported state or class")
        if row.response is None:
            continue
        result = classify_answer(item.worksheet, row.response)
        expected = "ACCEPTED" if result.valid else "SCHEMA_INVALID"
        if row.state != expected:
            raise StageDError("attempt state contradicts independent answer validation")
        answered.append((row, result))
        if len(answered) > 2:
            raise StageDError("work exceeds the one-schema-retry entitlement")
        closed = result.valid or len(answered) == 2
    if not answered:
        raise StageDError("completed Stage-D work lacks a bounded HTTP answer")
    accepted = next(((row, result) for row, result in answered if result.valid), None)
    findings: list[_Finding] = []
    if accepted is not None:
        value = accepted[1].value
        assert value is not None
        raw = _answer_findings(item.worksheet, value)
        for finding in raw:
            verdict = ground_finding(finding["quote"], finding["offset"], source_chunk)
            offset = (chunk_start + verdict.canonical_offset
                      if verdict.canonical_offset is not None else chunk_start)
            findings.append(_Finding(
                finding["category"], finding["quote"], offset, verdict.grounded))
    counts = [row.prompt_eval_count for row, _result in answered]
    if any(type(value) is not int for value in counts):  # guarded by strict model
        raise StageDError("answered attempt lacks context usage")
    typed_counts = [int(value) for value in counts]
    limit = (85 * item.num_ctx) // 100
    return _WorkScore(
        item, bool(answered[0][1].valid), accepted is not None, tuple(findings),
        sum(row.done_reason == "length" for row, _result in answered),
        max(typed_counts), all(value + item.num_predict <= limit for value in typed_counts),
        all(bool(row.tools_empty) for row, _result in answered),
        all(bool(row.images_empty) for row, _result in answered),
        all(bool(row.unknown_message_fields_empty) for row, _result in answered),
        all(_decoded_marker_empty(item.nonce, row.response or "")
            for row, _result in answered),
        all(result.schema_escape_empty for _row, result in answered),
    )


def _answer_findings(worksheet: str, value: Mapping[str, Any]) -> list[dict[str, Any]]:
    if worksheet == "v2":
        return [dict(row) for row in value["findings"]]
    return [{"category": row["category"], **dict(item)}
            for row in value["categories"] for item in row["evidence"]]


def _score_all(plan: DPhasePlan, corpus: D50Corpus,
               evidence: Mapping[str, Sequence[AttemptEvidence | Mapping[str, Any]]]
               ) -> list[_WorkScore]:
    if type(evidence) is not dict or set(evidence) != {row.work_id for row in plan.work}:
        raise StageDError("attempt evidence must cover exactly the frozen phase work")
    scores = []
    for work in plan.work:
        source, start = _source_chunk(work, corpus)
        scores.append(derive_work_evidence(work, source, start, evidence[work.work_id]))
    return scores


def _doc_facts(scores: Sequence[_WorkScore], corpus: D50Corpus) -> list[dict[str, Any]]:
    grouped: dict[str, list[_WorkScore]] = {}
    for score in scores:
        grouped.setdefault(score.work.doc_id, []).append(score)
    rows = []
    documents = corpus.by_id()
    for doc_id, chunks in grouped.items():
        document = documents[doc_id]
        raw = [finding for chunk in chunks for finding in chunk.findings]
        grounded = [finding for finding in raw if finding.grounded]
        retained = {(row.category, row.quote, row.offset) for row in grounded}
        predicted = [category for category in CATEGORIES
                     if any(row[0] == category for row in retained)]
        boundary_pass = (document.stratum == "boundary" and any(
            category == "pii" and any(identifier in quote
                                       for identifier in document.expected_identifiers)
            for category, quote, _offset in retained))
        rows.append({
            "doc_id": doc_id, "stratum": document.stratum,
            "expected": list(document.categories_present), "predicted": predicted,
            "planned": len(chunks), "completed": sum(row.eventual_valid for row in chunks),
            "first_invalid": sum(not row.first_pass_valid for row in chunks),
            "eventual_invalid": sum(not row.eventual_valid for row in chunks),
            "raw": len(raw), "grounded": len(grounded),
            "retained": len(retained), "retained_grounded": len(retained),
            "boundary_pass": boundary_pass,
            "length": sum(row.length_outcomes for row in chunks),
            "headroom": sum(not row.headroom_passed for row in chunks),
            "tools": all(row.tools_empty for row in chunks),
            "images": all(row.images_empty for row in chunks),
            "unknown": all(row.unknown_message_fields_empty for row in chunks),
            "marker": all(row.marker_empty for row in chunks),
            "schema": all(row.schema_escape_empty for row in chunks),
        })
    return rows


def _quality_d1(scores: Sequence[_WorkScore], corpus: D50Corpus) -> dict[str, Any]:
    docs = _doc_facts(scores, corpus)
    raw, grounded = sum(row["raw"] for row in docs), sum(row["grounded"] for row in docs)
    facts = {
        "length_outcome_present": sum(row["length"] for row in docs) > 0,
        "eventual_invalid_present": sum(row["eventual_invalid"] for row in docs) > 0,
        "raw_grounding_below_0_99": raw > 0 and 100 * grounded < 99 * raw,
        "expected_category_missing": any(
            not set(row["expected"]).issubset(row["predicted"]) for row in docs),
        "unsupported_category_present": any(
            set(row["predicted"]) - set(row["expected"]) for row in docs),
    }
    reasons = ordered_reasons(_D1_REASONS, facts)
    return D1Quality.model_validate({
        "planned_chunks": len(scores),
        "completed_chunks": sum(row["completed"] for row in docs),
        "eventual_invalid_chunks": sum(row["eventual_invalid"] for row in docs),
        "raw_findings": raw, "raw_grounded_findings": grounded,
        "expected_category_pass_documents": sum(
            set(row["expected"]).issubset(row["predicted"]) for row in docs),
        "unsupported_category_documents": sum(
            bool(set(row["predicted"]) - set(row["expected"])) for row in docs),
        "length_outcomes": sum(row["length"] for row in docs),
        "passed": not reasons, "failure_reasons": reasons,
    }, strict=True).model_dump(mode="json")


def _quality_d2(scores: Sequence[_WorkScore], corpus: D50Corpus) -> dict[str, Any]:
    docs = _doc_facts(scores, corpus)
    raw, grounded = sum(row["raw"] for row in docs), sum(row["grounded"] for row in docs)
    facts = {
        "boundary_identifier_missing": not all(row["boundary_pass"] for row in docs),
        "unsupported_category_present": any(
            set(row["predicted"]) - set(row["expected"]) for row in docs),
        "eventual_invalid_present": any(row["eventual_invalid"] for row in docs),
        "raw_grounding_below_0_99": raw > 0 and 100 * grounded < 99 * raw,
        "length_outcome_present": any(row["length"] for row in docs),
        "context_headroom_violation": any(row["headroom"] for row in docs),
    }
    reasons = ordered_reasons(_D2_REASONS, facts)
    return D2Quality.model_validate({
        "planned_documents": len(docs), "planned_chunks": len(scores),
        "completed_chunks": sum(row["completed"] for row in docs),
        "boundary_identifier_pass_documents": sum(row["boundary_pass"] for row in docs),
        "unsupported_category_documents": sum(
            bool(set(row["predicted"]) - set(row["expected"])) for row in docs),
        "eventual_invalid_chunks": sum(row["eventual_invalid"] for row in docs),
        "raw_findings": raw, "raw_grounded_findings": grounded,
        "length_outcomes": sum(row["length"] for row in docs),
        "headroom_violations": sum(row["headroom"] for row in docs),
        "passed": not reasons, "failure_reasons": reasons,
    }, strict=True).model_dump(mode="json")


def _selection(candidate: DPhaseCandidate, *, chunk_chars: int, num_ctx: int,
               num_predict: int) -> dict[str, Any]:
    return CandidateSelection.model_validate({
        "model": candidate.model, "model_digest": candidate.model_digest,
        "worksheet": candidate.worksheet, "chunk_chars": chunk_chars,
        "overlap": 256, "num_ctx": num_ctx, "num_predict": num_predict,
    }, strict=True).model_dump(mode="json")


def _probe(candidate: DPhaseCandidate, plan: DPhasePlan,
           control_value: Mapping[str, Any], evidence_value: Mapping[str, Any]
           ) -> dict[str, Any]:
    try:
        control = StageDContextControl.model_validate(control_value, strict=True)
        evidence = ContextProbeEvidence.model_validate(evidence_value, strict=True)
    except (TypeError, ValueError) as exc:
        raise StageDError("Stage-D context probe/control fails strict validation") from exc
    first = next((row for row in plan.work if row.candidate_id == candidate.candidate_id), None)
    expected_purpose = "d3_context_16384" if plan.phase == "D3" else "d4_context_selected"
    expected_ctx = 16384 if plan.phase == "D3" else candidate.num_ctx
    if first is None or expected_ctx is None or (
            control.candidate_id, control.model, control.model_digest, control.purpose,
            control.minimum_context_length) != (
            candidate.candidate_id, candidate.model, candidate.model_digest,
            expected_purpose, expected_ctx) or (
            evidence.control_id, evidence.purpose, evidence.candidate_id,
            evidence.model, evidence.model_digest, evidence.config_sha256,
            evidence.expected_num_ctx, evidence.trigger_work_id, evidence.state) != (
            control.control_id, control.purpose, control.candidate_id, control.model,
            control.model_digest, control.config_sha256,
            control.minimum_context_length, first.work_id, "PASSED"):
        raise StageDError("persisted context probe differs from its exact plan/control")
    return evidence.model_dump(mode="json")


def _quality_d4(scores: Sequence[_WorkScore], corpus: D50Corpus) -> dict[str, Any]:
    docs = _doc_facts(scores, corpus)
    controls = [row for row in docs if row["stratum"] in {
        "positive_control", "negative_clean", "negative_near_miss"}]
    recall = {}
    for category in CATEGORIES:
        positive = [row for row in controls if category in row["expected"]]
        recall[category] = {"true_positives": sum(
            category in row["predicted"] for row in positive), "support": len(positive)}
    negative = [row for row in controls if not row["expected"]]
    boundaries = [row for row in docs if row["stratum"] == "boundary"]
    raw, grounded = sum(row["raw"] for row in docs), sum(row["grounded"] for row in docs)
    retained = sum(row["retained"] for row in docs)
    retained_grounded = sum(row["retained_grounded"] for row in docs)
    channel = not all(all(row[name] for row in docs) for name in
                      ("tools", "images", "unknown", "marker", "schema"))
    facts = {
        "eventual_invalid_or_missing_chunk":
            sum(row["completed"] for row in docs) != len(scores),
        "first_pass_invalid_above_1": sum(row["first_invalid"] for row in docs) > 1,
        "raw_grounding_below_0_99": raw > 0 and 100 * grounded < 99 * raw,
        "retained_grounding_below_1_00": retained_grounded != retained,
        **{f"{category}_recall_below_6_of_6":
           recall[category]["true_positives"] != 6 or recall[category]["support"] != 6
           for category in CATEGORIES},
        "negative_false_positive_present": any(row["predicted"] for row in negative),
        "boundary_identifier_missing": not all(row["boundary_pass"] for row in boundaries),
        "length_outcome_present": any(row["length"] for row in docs),
        "context_headroom_violation": any(row["headroom"] for row in docs),
        "channel_violation_present": channel,
    }
    reasons = ordered_reasons(_D4_REASONS, facts)
    return D4Quality.model_validate({
        "expected_chunk_count": len(scores),
        "completed_eventual_valid_chunks": sum(row["completed"] for row in docs),
        "first_pass_invalid_chunks": sum(row["first_invalid"] for row in docs),
        "raw_findings": raw, "raw_grounded_findings": grounded,
        "retained_findings": retained, "retained_grounded_findings": retained_grounded,
        "category_recall": recall,
        "negative_false_positive_documents": sum(bool(row["predicted"]) for row in negative),
        "boundary_identifier_pass_documents": sum(row["boundary_pass"] for row in boundaries),
        "boundary_documents": len(boundaries),
        "length_outcomes": sum(row["length"] for row in docs),
        "headroom_violations": sum(row["headroom"] for row in docs),
        "context_probe_passed": True,
        "tools_empty": all(row["tools"] for row in docs),
        "images_empty": all(row["images"] for row in docs),
        "unknown_message_fields_empty": all(row["unknown"] for row in docs),
        "marker_empty": all(row["marker"] for row in docs),
        "schema_escape_empty": all(row["schema"] for row in docs),
        "passed": not reasons, "failure_reasons": reasons,
    }, strict=True).model_dump(mode="json")


def build_stage_d_aggregate(
        phase_plan: Mapping[str, Any],
        evidence_by_work: Mapping[str, Sequence[AttemptEvidence | Mapping[str, Any]]], *,
        corpus: D50Corpus,
        context_controls: Sequence[Mapping[str, Any]] = (),
        context_probes: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Rebuild one exact phase aggregate from plan-owned bounded evidence."""
    payload, plan = _strict_plan(phase_plan)
    scores = _score_all(plan, corpus, evidence_by_work)
    plan_hash = sha256_json(payload)
    rows: list[dict[str, Any]] = []
    controls = {row.get("candidate_id"): row for row in context_controls
                if type(row) is dict}
    probes = {row.get("candidate_id"): row for row in context_probes
              if type(row) is dict}
    if len(controls) != len(context_controls) or len(probes) != len(context_probes):
        raise StageDError("context evidence candidates must be unique exact objects")
    if plan.phase in {"D1", "D2"} and (controls or probes):
        raise StageDError("D1/D2 aggregates cannot carry context probes")
    if plan.phase in {"D3", "D4"} and (set(controls) != {
            row.candidate_id for row in plan.candidates} or set(probes) != set(controls)):
        raise StageDError("context evidence must exactly cover phase candidates")
    for candidate in plan.candidates:
        candidate_scores = [row for row in scores
                            if row.work.candidate_id == candidate.candidate_id]
        if plan.phase == "D1":
            levels = []
            for budget in sorted({row.work.num_predict for row in candidate_scores}):
                subset = [row for row in candidate_scores if row.work.num_predict == budget]
                levels.append({"num_predict": budget,
                               "quality": _quality_d1(subset, corpus)})
            selected = next((row["num_predict"] for row in levels
                             if row["quality"]["passed"]), None)
            rows.append({"candidate_id": candidate.candidate_id, "levels": levels,
                         "selected_num_predict": selected, "passed": selected is not None,
                         "failure_reasons": [] if selected is not None else
                         ["no_passing_output_budget"]})
        elif plan.phase == "D2":
            levels = [{"chunk_chars": size, "quality": _quality_d2(
                [row for row in candidate_scores if row.work.chunk_chars == size], corpus)}
                for size in D2_CHUNKS]
            passing = [row["chunk_chars"] for row in levels if row["quality"]["passed"]]
            selected = max(passing) if passing else None
            rows.append({"candidate_id": candidate.candidate_id,
                         "num_predict": candidate.num_predict, "levels": levels,
                         "selected_chunk_chars": selected,
                         "overlap": 256 if selected is not None else None,
                         "passed": selected is not None,
                         "failure_reasons": [] if selected is not None else
                         ["no_passing_chunk"]})
        else:
            probe = _probe(candidate, plan, controls[candidate.candidate_id],
                           probes[candidate.candidate_id])
            quality = _quality_d4(candidate_scores, corpus)
            if plan.phase == "D3":
                maximum = max(row.max_prompt_eval_count for row in candidate_scores)
                census = []
                for context in (4096, 8192, 16384):
                    analytical = candidate.num_predict <= (85 * context) // 100
                    measured = maximum + candidate.num_predict <= (85 * context) // 100
                    census.append({"num_ctx": context,
                                   "analytical_output_fit": analytical,
                                   "measured_fit": measured,
                                   "max_prompt_eval_count": maximum,
                                   "num_predict": candidate.num_predict,
                                   "eligible": analytical and measured and quality["passed"]})
                selected = next((row["num_ctx"] for row in census if row["eligible"]), None)
                reasons = list(quality["failure_reasons"])
                if selected is None:
                    reasons.append("no_context_candidate")
                rows.append({
                    "candidate_id": candidate.candidate_id,
                    "num_predict": candidate.num_predict,
                    "chunk_chars": candidate.chunk_chars, "overlap": candidate.overlap,
                    "high_context_plan_sha256": plan_hash,
                    "high_context_quality": quality, "context_probe": probe,
                    "context_census": census, "selected_num_ctx": selected,
                    "passed": selected is not None, "failure_reasons": reasons,
                })
            else:
                rows.append({
                    "candidate_id": candidate.candidate_id,
                    "selection": _selection(
                        candidate, chunk_chars=int(candidate.chunk_chars),
                        num_ctx=int(candidate.num_ctx), num_predict=int(candidate.num_predict)),
                    "context_probe": probe, "quality": quality,
                })
    aggregate = {
        "version": AGGREGATE_VERSION, "stage": "D", "phase": plan.phase,
        "plan_sha256": plan_hash,
        "parent_decision_sha256": plan.parent_decision_sha256,
        "candidate_order": [row.candidate_id for row in plan.candidates],
        "candidates": rows,
    }
    return validate_stage_d_aggregate(aggregate)


_AGGREGATES = {"D1": D1Aggregate, "D2": D2Aggregate,
               "D3": D3Aggregate, "D4": D4Aggregate}


def validate_stage_d_aggregate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate an exact stored Stage-D aggregate object."""
    if type(value) is not dict or value.get("phase") not in _AGGREGATES:
        raise StageDError("Stage-D aggregate has an unknown shape/phase")
    try:
        parsed = _AGGREGATES[value["phase"]].model_validate(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise StageDError("Stage-D aggregate fails strict validation") from exc
    if [row.candidate_id for row in parsed.candidates] != parsed.candidate_order:
        raise StageDError("Stage-D aggregate differs from frozen candidate order")
    return parsed.model_dump(mode="json")


def _require_aggregate_plan_shape(aggregate: Mapping[str, Any],
                                  plan: DPhasePlan) -> None:
    """Bind stored count/factor rows to the exact immutable phase-plan shape."""
    rows = aggregate["candidates"]
    for candidate, row in zip(plan.candidates, rows, strict=True):
        work = [item for item in plan.work if item.candidate_id == candidate.candidate_id]
        if not work:
            raise StageDError("aggregate candidate has no owned plan work")
        if plan.phase == "D1":
            factors = list(dict.fromkeys(item.num_predict for item in work))
            if [item["num_predict"] for item in row["levels"]] != factors:
                raise StageDError("D1 aggregate levels differ from exact plan factors")
            for level in row["levels"]:
                count = sum(item.num_predict == level["num_predict"] for item in work)
                if level["quality"]["planned_chunks"] != count:
                    raise StageDError("D1 aggregate count differs from exact plan work")
        elif plan.phase == "D2":
            factors = list(dict.fromkeys(item.chunk_chars for item in work))
            if [item["chunk_chars"] for item in row["levels"]] != factors:
                raise StageDError("D2 aggregate levels differ from exact plan factors")
            for level in row["levels"]:
                owned = [item for item in work
                         if item.chunk_chars == level["chunk_chars"]]
                quality = level["quality"]
                if (quality["planned_chunks"] != len(owned)
                        or quality["planned_documents"] !=
                        len({item.doc_id for item in owned})):
                    raise StageDError("D2 aggregate counts differ from exact plan work")
        else:
            quality = row["high_context_quality"] if plan.phase == "D3" else row["quality"]
            if quality["expected_chunk_count"] != len(work):
                raise StageDError("D3/D4 quality count differs from exact plan work")
            probe = row["context_probe"]
            first = work[0]
            purpose = "d3_context_16384" if plan.phase == "D3" else "d4_context_selected"
            expected_ctx = 16384 if plan.phase == "D3" else candidate.num_ctx
            if (probe["candidate_id"], probe["model"], probe["model_digest"],
                    probe["purpose"], probe["expected_num_ctx"],
                    probe["trigger_work_id"]) != (
                    candidate.candidate_id, candidate.model, candidate.model_digest,
                    purpose, expected_ctx, first.work_id):
                raise StageDError("aggregate probe differs from exact plan candidate")
            if plan.phase == "D3":
                if (row["high_context_plan_sha256"] != aggregate["plan_sha256"]
                        or (row["chunk_chars"], row["overlap"], row["num_predict"]) !=
                        (candidate.chunk_chars, candidate.overlap, candidate.num_predict)):
                    raise StageDError("D3 aggregate factors differ from exact plan")
            else:
                expected = _selection(
                    candidate, chunk_chars=int(candidate.chunk_chars),
                    num_ctx=int(candidate.num_ctx), num_predict=int(candidate.num_predict))
                if canonical_json(row["selection"]) != canonical_json(expected):
                    raise StageDError("D4 aggregate selection differs from exact plan")


def _candidate_row(base: DPhaseCandidate, **updates: Any) -> dict[str, Any]:
    value = base.model_dump(mode="json")
    value.update(updates)
    return DPhaseCandidate.model_validate(value, strict=True).model_dump(mode="json")


def build_stage_d_decision(aggregate: Mapping[str, Any],
                           phase_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Build a D1/D2/D3 decision; D3 all-reuse becomes the final decision."""
    normalized = validate_stage_d_aggregate(aggregate)
    payload, plan = _strict_plan(phase_plan)
    if (normalized["phase"] != plan.phase
            or normalized["plan_sha256"] != sha256_json(payload)
            or normalized["parent_decision_sha256"] != plan.parent_decision_sha256
            or normalized["candidate_order"] != [row.candidate_id for row in plan.candidates]):
        raise StageDError("aggregate ownership differs from its exact phase plan")
    _require_aggregate_plan_shape(normalized, plan)
    aggregate_hash = sha256_json(normalized)
    if plan.phase == "D1":
        selections = [_candidate_row(base, num_predict=row["selected_num_predict"])
                      for base, row in zip(plan.candidates, normalized["candidates"], strict=True)
                      if row["passed"]]
        reason = "phase_passed" if selections else "no_d1_output_budget_survivor"
        outcome = "CONTINUE" if selections else "INCONCLUSIVE"
    elif plan.phase == "D2":
        selections = [_candidate_row(
            base, chunk_chars=row["selected_chunk_chars"], overlap=row["overlap"])
            for base, row in zip(plan.candidates, normalized["candidates"], strict=True)
            if row["passed"]]
        reason = "phase_passed" if selections else "no_d2_chunk_survivor"
        outcome = "CONTINUE" if selections else "INCONCLUSIVE"
    elif plan.phase == "D3":
        passing = [row for row in normalized["candidates"] if row["passed"]]
        if passing and all(row["selected_num_ctx"] == 16384 for row in passing):
            selections = [_d3_final_row(plan, normalized, row) for row in passing]
            value = {"version": DECISION_VERSION, "stage": "D", "phase": "D3",
                     "plan_sha256": normalized["plan_sha256"],
                     "aggregate_sha256": aggregate_hash, "outcome": "FINALISTS",
                     "reason": "finalists_selected", "selections": selections}
            return FinalDecision.model_validate(value, strict=True).model_dump(mode="json")
        selections = [_candidate_row(base, num_ctx=row["selected_num_ctx"])
                      for base, row in zip(plan.candidates, normalized["candidates"], strict=True)
                      if row["passed"]]
        reason = "phase_passed" if selections else "no_d3_context_survivor"
        outcome = "CONTINUE" if selections else "INCONCLUSIVE"
    else:
        raise StageDError("D4 requires the exact merge decision builder")
    value = {"version": DECISION_VERSION, "stage": "D", "phase": plan.phase,
             "plan_sha256": normalized["plan_sha256"],
             "aggregate_sha256": aggregate_hash, "outcome": outcome,
             "reason": reason, "selections": selections}
    return IntermediateDecision.model_validate(value, strict=True).model_dump(mode="json")


def _d3_final_row(plan: DPhasePlan, aggregate: Mapping[str, Any],
                  row: Mapping[str, Any]) -> dict[str, Any]:
    base = next(item for item in plan.candidates
                if item.candidate_id == row["candidate_id"])
    return {
        "candidate_id": row["candidate_id"],
        "selection": _selection(
            base, chunk_chars=row["chunk_chars"], num_ctx=row["selected_num_ctx"],
            num_predict=row["num_predict"]),
        "evidence_source": "D3_REUSE",
        "source_aggregate_sha256": sha256_json(aggregate),
        "quality": row["high_context_quality"],
    }


def d3_decision_record_sha256(decision: Mapping[str, Any]) -> str:
    """Return the immutable checkpoint-row identity of a D3 CONTINUE decision."""
    if type(decision) is not dict:
        raise StageDError("D3 decision must be an exact JSON object")
    try:
        normalized = IntermediateDecision.model_validate(
            decision, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageDError("D3 decision fails strict validation") from exc
    if (normalized != decision or normalized["phase"] != "D3"
            or normalized["outcome"] != "CONTINUE"):
        raise StageDError("D4 requires the exact stored D3 CONTINUE decision")
    return sha256_json((
        "stage-d-d3-selection", "D", normalized["plan_sha256"],
        normalized["aggregate_sha256"], "ACTIVATED",
        canonical_json(normalized).decode("utf-8"),
    ))


def build_d4_final_decision(
        d4_aggregate: Mapping[str, Any], d4_plan: Mapping[str, Any], *,
        d3_aggregate: Mapping[str, Any], d3_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge passing lower-context reruns with untouched D3 reuse evidence."""
    d4 = validate_stage_d_aggregate(d4_aggregate)
    d3 = validate_stage_d_aggregate(d3_aggregate)
    d4_payload, d4_model = _strict_plan(d4_plan)
    d3_payload, d3_model = _strict_plan(d3_plan)
    if (d4["phase"], d3["phase"]) != ("D4", "D3"):
        raise StageDError("D4 merge requires exact D4 and D3 aggregates")
    if (d4["plan_sha256"] != sha256_json(d4_payload)
            or d3["plan_sha256"] != sha256_json(d3_payload)
            or d4["parent_decision_sha256"] != d4_model.parent_decision_sha256
            or d3["parent_decision_sha256"] != d3_model.parent_decision_sha256
            or d4["candidate_order"] != [row.candidate_id for row in d4_model.candidates]
            or d3["candidate_order"] != [row.candidate_id for row in d3_model.candidates]):
        raise StageDError("D4 merge aggregate ownership differs from its plans")
    _require_aggregate_plan_shape(d4, d4_model)
    _require_aggregate_plan_shape(d3, d3_model)
    d3_decision = build_stage_d_decision(d3, d3_payload)
    if (d3_decision["outcome"] != "CONTINUE"
            or d4_model.parent_decision_sha256 !=
            d3_decision_record_sha256(d3_decision)):
        raise StageDError("D4 parent is not the exact D3 CONTINUE decision")
    d3_by_id = {row["candidate_id"]: row for row in d3["candidates"]}
    d4_by_id = {row["candidate_id"]: row for row in d4["candidates"]}
    expected_reruns = [row["candidate_id"] for row in d3["candidates"]
                       if row["passed"] and row["selected_num_ctx"] != 16384]
    if list(d4["candidate_order"]) != expected_reruns:
        raise StageDError("D4 rerun subset differs from lower-context D3 survivors")
    expected_candidates = [row for row in d3_decision["selections"]
                           if row["num_ctx"] != 16384]
    if canonical_json([row.model_dump(mode="json") for row in d4_model.candidates]) != \
            canonical_json(expected_candidates):
        raise StageDError("D4 candidate factors differ from lower-context D3 selections")
    for candidate, aggregate_row in zip(
            d4_model.candidates, d4["candidates"], strict=True):
        expected_selection = _selection(
            candidate, chunk_chars=int(candidate.chunk_chars),
            num_ctx=int(candidate.num_ctx), num_predict=int(candidate.num_predict))
        if canonical_json(aggregate_row["selection"]) != canonical_json(expected_selection):
            raise StageDError("D4 aggregate selection differs from its exact plan")
    selections = []
    for candidate_id in d3["candidate_order"]:
        d3_row = d3_by_id[candidate_id]
        if not d3_row["passed"]:
            continue
        if d3_row["selected_num_ctx"] == 16384:
            selections.append(_d3_final_row(d3_model, d3, d3_row))
        else:
            d4_row = d4_by_id[candidate_id]
            if d4_row["quality"]["passed"]:
                selections.append({
                    "candidate_id": candidate_id, "selection": d4_row["selection"],
                    "evidence_source": "D4_RERUN",
                    "source_aggregate_sha256": sha256_json(d4),
                    "quality": d4_row["quality"],
                })
    value = {
        "version": DECISION_VERSION, "stage": "D", "phase": "D4",
        "plan_sha256": d4["plan_sha256"], "aggregate_sha256": sha256_json(d4),
        "outcome": "FINALISTS" if selections else "INCONCLUSIVE",
        "reason": "finalists_selected" if selections else
                  "no_d4_confirmation_finalist",
        "selections": selections,
    }
    return FinalDecision.model_validate(value, strict=True).model_dump(mode="json")


def validate_final_stage_d_decision(
        decision: Mapping[str, Any], *, owner_plan: Mapping[str, Any],
        owner_aggregate: Mapping[str, Any], d3_plan: Mapping[str, Any] | None = None,
        d3_aggregate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the stored final decision and its exact D3/D4 evidence owners."""
    if type(decision) is not dict:
        raise StageDError("final Stage-D decision must be an exact JSON object")
    try:
        parsed = FinalDecision.model_validate(decision, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageDError("final Stage-D decision fails strict validation") from exc
    _owner_payload, owner = _strict_plan(owner_plan)
    if owner.phase == "D3":
        expected = build_stage_d_decision(owner_aggregate, owner_plan)
        if expected.get("outcome") != "FINALISTS":
            raise StageDError("D3 owner does not produce an all-reuse final decision")
    elif owner.phase == "D4" and d3_plan is not None and d3_aggregate is not None:
        expected = build_d4_final_decision(
            owner_aggregate, owner_plan, d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    else:
        raise StageDError("final Stage-D owner lineage is incomplete or not D3/D4")
    if canonical_json(parsed) != canonical_json(expected):
        raise StageDError("final Stage-D decision differs from exact owned evidence")
    return parsed
