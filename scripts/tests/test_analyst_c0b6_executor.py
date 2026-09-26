"""Offline persistence-boundary tests for the C0B-6 executor."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark.c0b2_executor import WorkRequest
from scripts.analyst_benchmark.c0b4_executor import AttemptFinish
from scripts.analyst_benchmark.c0b6_executor import (
    C0B6ExecutorError,
    persist_scored_finish,
    reconcile_runtime_events,
    runtime_event,
)
from scripts.analyst_benchmark.c0b6_policy import (
    POLICY_ID,
    POLICY_SHA256,
)


class Point:
    def __init__(self) -> None:
        self.events = []
        self.attempts = []
        self.finishes = []

    def header(self):
        return {
            "policy_id": POLICY_ID,
            "policy_sha256": POLICY_SHA256,
            "protocol_sha256": "a" * 64,
        }

    def store_runtime_event(self, value):
        self.events.append(value)

    def record_attempt(self, attempt_id, outcome, payload):
        self.finishes.append((attempt_id, outcome, payload))

    def list_runtime_events(self):
        return list(self.events)

    def list_attempts(self):
        return list(self.attempts)


def _work():
    return {"request_sha256": "b" * 64, "nonce": "FENCE_" + "A" * 32}


def test_runtime_event_is_owned_by_c0b6() -> None:
    point = Point()
    value = runtime_event(
        point, event="DISPATCHING", lane_id="F72_20260811",
        attempt_id="c" * 64, work=_work(), occurred_at=1.0)
    assert value["version"] == "c0b6-runtime-event-v1"
    assert value["policy_id"] == POLICY_ID
    assert len(value["event_sha256"]) == 64
    assert "c0b4" not in str(value)


def test_finish_persists_attempt_before_event() -> None:
    point = Point()
    request = WorkRequest(
        stage="F", work_id="b" * 64, model="qwen3.6:27b",
        request_hash="a" * 64, attempt_no=1, call_class="scored")
    finish = AttemptFinish("RAW_VALID", "{}", {"done_reason": "stop"}, None)
    persist_scored_finish(
        point, "F72_20260811", _work(), request, finish)
    assert point.finishes[0][0] == request.attempt_id
    assert point.events[0]["event"] == "RAW_VALID"


def test_reconcile_is_idempotent_and_rejects_foreign_owner() -> None:
    point = Point()
    point.attempts = [{
        "attempt_id": "c" * 64,
        "owner_id": "d" * 64,
        "invocation_ordinal": 1,
        "state": "RAW_VALID",
        "created": 1.0,
        "updated": 2.0,
    }]
    resolver = SimpleNamespace(
        control_ids=set(), work_ids={"d" * 64},
        prepared=SimpleNamespace(resolve_work=lambda _owner: {
            "work": _work(), "lane": {"lane_id": "F72_20260811"}}))
    reconcile_runtime_events(point, resolver)
    assert [row["event"] for row in point.events] == ["DISPATCHING", "RAW_VALID"]
    reconcile_runtime_events(point, resolver)
    assert len(point.events) == 2

    resolver.work_ids.clear()
    point.events.clear()
    with pytest.raises(C0B6ExecutorError, match="outside"):
        reconcile_runtime_events(point, resolver)
