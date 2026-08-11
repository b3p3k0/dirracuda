"""Offline ordering and duplicate-recovery tests for the C0B-4 executor."""
from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from scripts.analyst_benchmark.c0b2_executor import (
    FakeResponse,
    RetryableTransport,
)
from scripts.analyst_benchmark.c0b4_executor import (
    AttemptFinish,
    ScoredWork,
    execute_scored,
)


def _raw(findings: list[dict[str, Any]]) -> str:
    return json.dumps({
        "document_type": "fixture", "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": findings,
    }, separators=(",", ":"))


def _work(source: str = "email a@example.test then a@example.test") -> ScoredWork:
    return ScoredWork("b" * 64, "a" * 64, "qwen3.6:27b", "v2", source)


def _metadata(*, strict: bool, semantic: bool) -> dict[str, Any]:
    return {
        "strict_schema_invalid": strict, "semantic_invalid": semantic,
        "done_reason": "stop", "prompt_eval_count": 100,
        "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True,
    }


def test_precharge_precedes_transport_and_duplicate_uses_no_retry() -> None:
    events: list[str] = []
    finished: list[AttemptFinish] = []
    raw = _raw([
        {"category": "contact", "quote": "a@example.test", "offset": 6},
        {"category": "contact", "quote": "a@example.test", "offset": 26},
    ])

    def transport(_request, _cancel):
        events.append("transport")
        return FakeResponse(
            raw, _metadata(strict=False, semantic=True),
            accepted=False, outcome="SCHEMA_INVALID")

    result = execute_scored(
        _work(), attempt_no=1, call_class="scored",
        answered_attempts_before=0,
        precharge=lambda _request: events.append("precharge"),
        finish=lambda _request, value: (events.append("finish"), finished.append(value)),
        transport=transport)
    assert events == ["precharge", "transport", "finish"]
    assert result.outcome == "NORMALIZED_DUPLICATE"
    assert result.retry_class is None
    assert finished[0].metadata["raw_first_pass_valid"] is False
    assert finished[0].metadata["retained_counts"]["findings"] == 1


def test_invalid_first_answer_permits_exactly_one_schema_retry() -> None:
    response = FakeResponse(
        "{}", _metadata(strict=True, semantic=False),
        accepted=False, outcome="SCHEMA_INVALID")
    results = []
    first = execute_scored(
        _work(), attempt_no=1, call_class="scored", answered_attempts_before=0,
        precharge=lambda _r: None,
        finish=lambda _r, value: results.append(value),
        transport=lambda _r, _c: response)
    second = execute_scored(
        _work(), attempt_no=2, call_class="schema_retry", answered_attempts_before=1,
        precharge=lambda _r: None,
        finish=lambda _r, value: results.append(value),
        transport=lambda _r, _c: response)
    assert first.retry_class == "schema_retry"
    assert second.retry_class is None
    assert [row.outcome for row in results] == ["SCHEMA_INVALID", "SCHEMA_INVALID"]


def test_ungrounded_duplicate_is_a_quality_failure_without_retry() -> None:
    source = "No identifier is present."
    raw = _raw([
        {"category": "pii", "quote": "900-12-3456", "offset": 0},
        {"category": "pii", "quote": "900-12-3456", "offset": 0},
    ])
    response = FakeResponse(
        raw, _metadata(strict=False, semantic=True),
        accepted=False, outcome="SCHEMA_INVALID")
    result = execute_scored(
        _work(source), attempt_no=1, call_class="scored",
        answered_attempts_before=0, precharge=lambda _r: None,
        finish=lambda *_: None, transport=lambda *_: response)
    assert result.outcome == "SCHEMA_INVALID"
    assert result.assessment is not None
    assert result.assessment.schema_retry_allowed is False
    assert result.retry_class is None


def test_transport_failure_is_answerless_and_retryable() -> None:
    finished = []

    def fail(_request, _cancel):
        raise RetryableTransport("resource")

    result = execute_scored(
        _work(), attempt_no=1, call_class="scored", answered_attempts_before=0,
        precharge=lambda _r: None,
        finish=lambda _r, value: finished.append(value), transport=fail)
    assert result.http_terminal is False
    assert result.retry_class == "transport_orphan"
    assert finished[0].response is None


def test_operator_cancellation_is_not_classified_as_resource_or_orphan() -> None:
    cancel, finished = threading.Event(), []

    def stopped(_request, event):
        event.set()
        raise RetryableTransport("cancelled")

    result = execute_scored(
        _work(), attempt_no=1, call_class="scored", answered_attempts_before=0,
        precharge=lambda _r: None,
        finish=lambda _r, value: finished.append(value),
        transport=stopped, cancellation=cancel)
    assert result.outcome == "CANCELLED"
    assert result.retry_class is None
    assert finished[0].outcome == "CANCELLED"


def test_call_class_and_transport_classification_fail_closed() -> None:
    with pytest.raises(ValueError):
        execute_scored(
            _work(), attempt_no=2, call_class="scored", precharge=lambda _r: None,
            answered_attempts_before=0,
            finish=lambda *_: None, transport=lambda *_: pytest.fail())
    invalid = FakeResponse(
        _raw([]), _metadata(strict=False, semantic=True),
        accepted=False, outcome="SCHEMA_INVALID")
    finished = []
    result = execute_scored(
        _work(), attempt_no=1, call_class="scored", answered_attempts_before=0,
        precharge=lambda _r: None,
        finish=lambda _r, value: finished.append(value),
        transport=lambda *_: invalid)
    assert result.outcome == "FAILED_SAFETY"
    assert result.retry_class is None
    assert finished[0].response == invalid.content
    assert finished[0].metadata["answered"] is True
    assert len(finished[0].metadata["raw_response_sha256"]) == 64


def test_missing_evidence_metadata_fails_before_raw_valid() -> None:
    finished = []
    metadata = _metadata(strict=False, semantic=False)
    metadata.pop("prompt_eval_count")
    result = execute_scored(
        _work(), attempt_no=1, call_class="scored", answered_attempts_before=0,
        precharge=lambda _r: None,
        finish=lambda _r, value: finished.append(value),
        transport=lambda *_: FakeResponse(_raw([]), metadata))
    assert result.outcome == "FAILED_SAFETY"
    assert finished[0].outcome == "FAILED_SAFETY"
