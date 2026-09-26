"""C0B-5 persistence callbacks around the frozen scored executor.

Only the policy-neutral scored request types/function are imported from C0B-4.  C0B-5
owns every durable event and attempt transition so artifact families cannot mix.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .c0b2_plan import stable_hash
from .c0b2_public_schema import sha256_json
from .c0b2_executor import WorkRequest
from .c0b4_executor import (
    AttemptFinish,
    ScoredExecutionResult,
    ScoredWork,
    execute_scored,
)

__all__ = [
    "AttemptFinish",
    "ScoredExecutionResult",
    "ScoredWork",
    "execute_scored",
    "persist_scored_finish",
    "reconcile_runtime_events",
    "runtime_event",
]


class C0B5ExecutorError(RuntimeError):
    """Injected storage or resolver evidence violated the frozen C0B-5 contract."""


def _occurred_at(value: float | None) -> str:
    moment = (datetime.fromtimestamp(value, timezone.utc) if value is not None
              else datetime.now(timezone.utc))
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def runtime_event(point: Any, *, event: str, lane_id: str,
                  attempt_id: str, work: Mapping[str, Any],
                  occurred_at: float | None = None) -> dict[str, Any]:
    """Store one self-digested C0B-5 runtime event."""
    if event not in {
            "DISPATCHING", "RAW_VALID", "NORMALIZED_DUPLICATE", "INVALID",
            "ORPHANED", "CANCELLED"}:
        raise C0B5ExecutorError("unknown C0B-5 runtime event")
    header = point.header()
    value = {
        "version": "c0b5-runtime-event-v1",
        **{key: str(header[key]) for key in (
            "policy_id", "policy_sha256", "protocol_sha256")},
        "event": event,
        "lane_id": lane_id,
        "source_attempt_id": attempt_id,
        "request_sha256": work["request_sha256"],
        "nonce": work["nonce"],
        "occurred_at_utc": _occurred_at(occurred_at),
    }
    value["event_sha256"] = sha256_json(value)
    point.store_runtime_event(value)
    return value


def persist_scored_finish(point: Any, lane_id: str,
                          work: Mapping[str, Any], request: WorkRequest,
                          result: AttemptFinish) -> None:
    """Finish one charged attempt, then publish its matching C0B-5 event."""
    point.record_attempt(request.attempt_id, result.outcome, {
        "answered": result.response is not None,
        "response": result.response,
        "metadata": dict(result.metadata),
    })
    event = {
        "RAW_VALID": "RAW_VALID",
        "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
        "SCHEMA_INVALID": "INVALID",
        "RETRYABLE_TRANSPORT": "ORPHANED",
        "CANCELLED": "CANCELLED",
    }.get(result.outcome)
    if event is not None:
        runtime_event(
            point, event=event, lane_id=lane_id,
            attempt_id=request.attempt_id, work=work)


def reconcile_runtime_events(point: Any, resolver: Any) -> None:
    """Idempotently close scored attempt/event crash gaps before resume."""
    existing = {(row["event"], row["source_attempt_id"])
                for row in point.list_runtime_events()}
    terminal_events = {
        "RAW_VALID": "RAW_VALID",
        "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
        "SCHEMA_INVALID": "INVALID",
        "INVALID": "INVALID",
        "RETRYABLE_TRANSPORT": "ORPHANED",
        "ORPHANED_UNKNOWN": "ORPHANED",
        "CANCELLED": "CANCELLED",
    }
    attempts = point.list_attempts()
    ordinals = {row["invocation_ordinal"] for row in attempts
                if type(row.get("invocation_ordinal")) is int}
    preflight_ids = {stable_hash({
        "c0b5_preflight": kind, "invocation": ordinal})
        for ordinal in ordinals for kind in ("version", "tags", "show")}
    for attempt in attempts:
        owner_id = attempt["owner_id"]
        if owner_id in resolver.control_ids or owner_id in preflight_ids:
            continue
        if owner_id not in resolver.work_ids:
            raise C0B5ExecutorError("attempt owner is outside the frozen plan")
        resolved = resolver.prepared.resolve_work(owner_id)
        work, lane_id = resolved["work"], resolved["lane"]["lane_id"]
        expected = [("DISPATCHING", attempt.get("created"))]
        terminal = terminal_events.get(attempt["state"])
        if terminal is not None:
            expected.append((terminal, attempt.get("updated")))
        for event, occurred_at in expected:
            key = event, attempt["attempt_id"]
            if key not in existing:
                runtime_event(
                    point, event=event, lane_id=lane_id,
                    attempt_id=attempt["attempt_id"], work=work,
                    occurred_at=occurred_at)
                existing.add(key)
