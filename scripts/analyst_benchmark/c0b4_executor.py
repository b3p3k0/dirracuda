"""Serial, precharged execution boundary for C0B-4 scored requests.

The durable store and transport are injected.  This module owns the crucial ordering:
charge first, make one request, classify the untouched raw answer, then durably finish.
Schedulers own lane activation and controls; this module never advances a cursor.

DISPOSITION: benchmark-only; port the answer handling to C1 deliberately.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .c0b2_executor import (
    FakeResponse,
    ProvenanceFailure,
    RetryableTransport,
    SafetyLimit,
    WorkRequest,
)
from .c0b2_plan import stable_hash
from .c0b2_public_schema import sha256_json
from .c0b4_answer import AnswerAssessment, assess_answer


class ExecutorContractError(RuntimeError):
    """Injected storage or transport evidence violated the frozen contract."""


@dataclass(frozen=True)
class ScoredWork:
    work_id: str
    request_sha256: str
    model: str
    worksheet: str
    source: str

    def __post_init__(self) -> None:
        if (len(self.work_id) != 64
                or any(char not in "0123456789abcdef" for char in self.work_id)
                or self.model != "qwen3.6:27b" or self.worksheet != "v2"
                or type(self.source) is not str
                or len(self.request_sha256) != 64
                or any(char not in "0123456789abcdef"
                       for char in self.request_sha256)):
            raise ValueError("scored work identity is invalid")


@dataclass(frozen=True)
class AttemptFinish:
    outcome: str
    response: str | None
    metadata: Mapping[str, Any]
    assessment: AnswerAssessment | None


@dataclass(frozen=True)
class ScoredExecutionResult:
    attempt_id: str
    outcome: str
    http_terminal: bool
    retry_class: str | None
    assessment: AnswerAssessment | None


def _runtime_event(point: Any, *, event: str, lane_id: str,
                   attempt_id: str, work: Mapping[str, Any],
                   occurred_at: float | None = None) -> None:
    header = point.header()
    value = {
        "version": "c0b4-runtime-event-v1",
        **{key: str(header[key]) for key in (
            "policy_id", "policy_sha256", "protocol_sha256")},
        "event": event, "lane_id": lane_id,
        "source_attempt_id": attempt_id,
        "request_sha256": work["request_sha256"], "nonce": work["nonce"],
        "occurred_at_utc": datetime.fromtimestamp(
            occurred_at, timezone.utc).isoformat(timespec="microseconds").replace(
                "+00:00", "Z") if occurred_at is not None else
            datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
                "+00:00", "Z"),
    }
    value["event_sha256"] = sha256_json(value)
    point.store_runtime_event(value)


def reconcile_runtime_events(point: Any, resolver: Any) -> None:
    """Idempotently close scored attempt/event crash gaps before resuming."""
    existing = {(row["event"], row["source_attempt_id"])
                for row in point.list_runtime_events()}
    terminal_events = {
        "RAW_VALID": "RAW_VALID",
        "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
        "SCHEMA_INVALID": "INVALID", "INVALID": "INVALID",
        "RETRYABLE_TRANSPORT": "ORPHANED",
        "ORPHANED_UNKNOWN": "ORPHANED", "CANCELLED": "CANCELLED",
    }
    attempts = point.list_attempts()
    ordinals = {row["invocation_ordinal"] for row in attempts
                if type(row.get("invocation_ordinal")) is int}
    preflight_ids = {stable_hash({
        "c0b4_preflight": kind, "invocation": ordinal})
        for ordinal in ordinals for kind in ("version", "tags", "show")}
    for attempt in attempts:
        owner_id = attempt["owner_id"]
        if owner_id in resolver.control_ids or owner_id in preflight_ids:
            continue
        if owner_id not in resolver.work_ids:
            raise ExecutorContractError("attempt owner is outside the frozen plan")
        resolved = resolver.prepared.resolve_work(owner_id)
        work, lane_id = resolved["work"], resolved["lane"]["lane_id"]
        expected = [("DISPATCHING", attempt.get("created"))]
        terminal = terminal_events.get(attempt["state"])
        if terminal is not None:
            expected.append((terminal, attempt.get("updated")))
        for event, occurred_at in expected:
            key = event, attempt["attempt_id"]
            if key not in existing:
                _runtime_event(
                    point, event=event, lane_id=lane_id,
                    attempt_id=attempt["attempt_id"], work=work,
                    occurred_at=occurred_at)
                existing.add(key)


def persist_scored_finish(point: Any, lane_id: str,
                          work: Mapping[str, Any], request: WorkRequest,
                          result: AttemptFinish) -> None:
    point.record_attempt(request.attempt_id, result.outcome, {
        "answered": result.response is not None,
        "response": result.response, "metadata": dict(result.metadata),
    })
    event = {
        "RAW_VALID": "RAW_VALID", "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
        "SCHEMA_INVALID": "INVALID", "RETRYABLE_TRANSPORT": "ORPHANED",
        "CANCELLED": "CANCELLED",
    }.get(result.outcome)
    if event is not None:
        _runtime_event(point, event=event, lane_id=lane_id,
                       attempt_id=request.attempt_id, work=work)


class Precharge(Protocol):
    def __call__(self, request: WorkRequest) -> None: ...


class Finish(Protocol):
    def __call__(self, request: WorkRequest, result: AttemptFinish) -> None: ...


Transport = Callable[[WorkRequest, threading.Event], FakeResponse]


def execute_scored(
        work: ScoredWork, *, attempt_no: int, call_class: str,
        answered_attempts_before: int,
        precharge: Precharge, finish: Finish, transport: Transport,
        cancellation: threading.Event | None = None,
) -> ScoredExecutionResult:
    """Execute one precharged attempt and preserve raw/normalized distinctions."""
    if type(attempt_no) is not int or attempt_no < 1:
        raise ValueError("attempt number must be positive")
    if type(answered_attempts_before) is not int \
            or answered_attempts_before not in {0, 1}:
        raise ValueError("answered-attempt census is outside the retry contract")
    if call_class not in {"scored", "schema_retry", "transport_orphan"}:
        raise ValueError("unknown scored-work call class")
    if ((attempt_no == 1) != (call_class == "scored")
            or call_class == "schema_retry" and answered_attempts_before != 1
            or attempt_no > 1 and call_class == "scored"):
        raise ValueError("attempt history differs from its call class")
    request = WorkRequest(
        stage="F", work_id=work.work_id, model=work.model,
        request_hash=work.request_sha256, attempt_no=attempt_no,
        call_class=call_class)
    precharge(request)
    cancel = cancellation or threading.Event()
    try:
        response = transport(request, cancel)
    except RetryableTransport:
        if cancel.is_set():
            result = AttemptFinish(
                "CANCELLED", None, {"answered": False}, None)
            finish(request, result)
            return ScoredExecutionResult(
                request.attempt_id, result.outcome, False, None, None)
        result = AttemptFinish(
            "RETRYABLE_TRANSPORT", None, {"answered": False}, None)
        finish(request, result)
        return ScoredExecutionResult(
            request.attempt_id, result.outcome, False, "transport_orphan", None)
    except SafetyLimit:
        result = AttemptFinish("FAILED_SAFETY", None, {"answered": False}, None)
        finish(request, result)
        return ScoredExecutionResult(
            request.attempt_id, result.outcome, False, None, None)
    except ProvenanceFailure:
        result = AttemptFinish(
            "BLOCKED_PROVENANCE", None, {"answered": False}, None)
        finish(request, result)
        return ScoredExecutionResult(
            request.attempt_id, result.outcome, False, None, None)

    try:
        _validate_response_shape(response)
        assessment = assess_answer(work.worksheet, response.content, work.source)
        _require_transport_agreement(response, assessment)
    except Exception:
        result = AttemptFinish(
            "FAILED_SAFETY", response.content, {
                "answered": True,
                "raw_response_sha256": hashlib.sha256(
                    response.content.encode("utf-8")).hexdigest(),
            }, None)
        finish(request, result)
        return ScoredExecutionResult(
            request.attempt_id, result.outcome, True, None, None)
    metadata = {
        **dict(response.metadata),
        "raw_response_sha256": hashlib.sha256(
            response.content.encode("utf-8")).hexdigest(),
        "raw_first_pass_valid": assessment.raw_first_pass_valid,
        "final_outcome": assessment.final_outcome,
        "semantic_errors": list(assessment.semantic_errors),
        "redundant_rows": assessment.redundant_rows,
        "removed_finding_indices": list(assessment.removed_finding_indices),
        "raw_counts": assessment.raw_counts.as_dict(),
        "retained_counts": assessment.retained_counts.as_dict(),
    }
    final = assessment.final_outcome
    persisted = final if final != "INVALID" else "SCHEMA_INVALID"
    result = AttemptFinish(persisted, response.content, metadata, assessment)
    finish(request, result)
    retry_class = ("schema_retry"
                   if assessment.schema_retry_allowed
                   and answered_attempts_before == 0 else None)
    return ScoredExecutionResult(
        request.attempt_id, persisted, True, retry_class, assessment)


def _validate_response_shape(response: FakeResponse) -> None:
    if (not isinstance(response, FakeResponse)
            or type(response.content) is not str
            or not isinstance(response.metadata, Mapping)
            or response.outcome not in {"ACCEPTED", "SCHEMA_INVALID"}
            or response.accepted != (response.outcome == "ACCEPTED")):
        raise SafetyLimit("transport_response_contract")
    metadata = response.metadata
    if (type(metadata.get("done_reason")) is not str
            or not 1 <= len(metadata["done_reason"]) <= 80
            or type(metadata.get("prompt_eval_count")) is not int
            or metadata["prompt_eval_count"] < 0
            or any(type(metadata.get(key)) is not bool for key in (
                "tools_empty", "images_empty", "unknown_message_fields_empty"))):
        raise SafetyLimit("transport_evidence_metadata")


def _require_transport_agreement(
        response: FakeResponse, assessment: AnswerAssessment) -> None:
    strict = response.metadata.get("strict_schema_invalid")
    semantic = response.metadata.get("semantic_invalid")
    if type(strict) is not bool or type(semantic) is not bool or strict and semantic:
        raise SafetyLimit("transport_assessment_contract")
    if assessment.final_outcome == "RAW_VALID":
        agrees = response.outcome == "ACCEPTED" and not strict and not semantic
    elif assessment.final_outcome == "NORMALIZED_DUPLICATE":
        agrees = response.outcome == "SCHEMA_INVALID" and not strict and semantic
    else:
        agrees = response.outcome == "SCHEMA_INVALID" and (strict or semantic)
    if not agrees:
        raise SafetyLimit("transport_assessment_mismatch")
