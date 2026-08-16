"""Durable, fenced Ollama contact charging and resource scheduling."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .contact_contract import (
    MAX_CHAT_CONTACTS_PER_CHUNK,
    MAX_CONTROL_CONTACTS_PER_RUN,
    PS_REQUEST_SHA256,
    TAGS_REQUEST_SHA256,
    VERSION_REQUEST_SHA256,
    ContactCharge,
    ContactFinish,
    ContactKind,
    ContactStatus,
    ScheduleSnapshot,
    ScheduleState,
    resets_resource_streak,
    semantic_attempt_state,
)
from .lease import LeaseFence
from .resource_policy import RESOURCE_BACKOFF_SECONDS
from .state import AttemptState, RunState
from .store import open_connection, run_immediate


_LOWER_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FENCE_WHERE = (
    "slot=1 AND generation=? AND run_id=? AND owner_token=? AND pid=? "
    "AND start_ticks=? AND boot_id=? AND heartbeat_monotonic_ns=?"
)
_CONTROL_HASHES = {
    ContactKind.VERSION: VERSION_REQUEST_SHA256,
    ContactKind.TAGS: TAGS_REQUEST_SHA256,
    ContactKind.PS: PS_REQUEST_SHA256,
}


class OllamaStateError(RuntimeError):
    """Durable contact or schedule state contradicts the C9B contract."""


class ResourceWaitCancelled(OllamaStateError):
    """The operator cancelled while waiting for a due resource retry."""


def get_schedule(
    run_id: str, *, path: Path | None = None,
) -> ScheduleSnapshot:
    """Return one run's content-free resource schedule."""
    _require_run_id(run_id)
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT * FROM analyst_ollama_schedule WHERE run_id=?", (run_id,),
        ).fetchone()
        if row is None:
            raise OllamaStateError("Analyst run has no Ollama schedule")
        return _schedule_from_row(row)
    finally:
        conn.close()


def remaining_resource_wait(
    schedule: ScheduleSnapshot, *, now_utc: str | None = None,
) -> int:
    """Return a reboot-safe remaining wait clamped to the recorded delay."""
    if not isinstance(schedule, ScheduleSnapshot):
        raise TypeError("schedule must be a ScheduleSnapshot")
    if schedule.not_before_utc is None:
        return 0
    now = _parse_timestamp(_timestamp(now_utc))
    deadline = _parse_timestamp(schedule.not_before_utc)
    remaining = math.ceil((deadline - now).total_seconds())
    return max(0, min(schedule.delay_seconds, remaining))


def wait_for_resource_retry(
    fence: LeaseFence,
    schedule: ScheduleSnapshot,
    *,
    cancelled: Callable[[], bool],
    heartbeat: Callable[[LeaseFence], LeaseFence],
    now_utc: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    pulse_seconds: float = 1.0,
) -> LeaseFence:
    """Wait outside SQLite while polling cancel and refreshing the exact fence."""
    _require_fence_value(fence)
    if not isinstance(schedule, ScheduleSnapshot):
        raise TypeError("schedule must be a ScheduleSnapshot")
    if not callable(cancelled) or not callable(heartbeat):
        raise TypeError("resource wait callbacks must be callable")
    if not callable(monotonic) or not callable(sleep):
        raise TypeError("resource wait clock and sleep must be callable")
    if type(pulse_seconds) not in {int, float} or not 0 < pulse_seconds <= 2:
        raise ValueError("resource wait pulse must be within two seconds")
    remaining = remaining_resource_wait(schedule, now_utc=now_utc)
    if cancelled():
        raise ResourceWaitCancelled("resource wait was cancelled")
    start = float(monotonic())
    if not math.isfinite(start):
        raise OllamaStateError("resource wait monotonic clock is invalid")
    deadline = start + remaining
    current_fence = fence
    last = start
    while last < deadline:
        if cancelled():
            raise ResourceWaitCancelled("resource wait was cancelled")
        updated = heartbeat(current_fence)
        if not isinstance(updated, LeaseFence):
            raise OllamaStateError("resource wait heartbeat returned no valid fence")
        if (
            updated.generation != current_fence.generation
            or updated.run_id != current_fence.run_id
            or updated.owner_token != current_fence.owner_token
            or updated.process != current_fence.process
            or updated.heartbeat_monotonic_ns
            <= current_fence.heartbeat_monotonic_ns
        ):
            raise OllamaStateError(
                "resource wait heartbeat did not advance the exact lease"
            )
        current_fence = updated
        if cancelled():
            raise ResourceWaitCancelled("resource wait was cancelled")
        sleep(min(float(pulse_seconds), deadline - last))
        observed = float(monotonic())
        if not math.isfinite(observed) or observed < last:
            raise OllamaStateError("resource wait monotonic clock moved backwards")
        last = observed
    if cancelled():
        raise ResourceWaitCancelled("resource wait was cancelled")
    return current_fence


def wait_until_resource_retry_due(
    fence: LeaseFence,
    schedule: ScheduleSnapshot,
    *,
    cancelled: Callable[[], bool],
    heartbeat: Callable[[LeaseFence], LeaseFence],
    now_utc: str,
    observed_utc: Callable[[], str],
    path: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    pulse_seconds: float = 1.0,
) -> tuple[LeaseFence, ScheduleSnapshot]:
    """Wait a bounded active backoff and durably close wall-clock rollback."""
    if schedule.state is not ScheduleState.BACKOFF:
        raise OllamaStateError("active resource wait requires backoff state")
    if not callable(observed_utc):
        raise TypeError("observed UTC source must be callable")
    successor = wait_for_resource_retry(
        fence,
        schedule,
        cancelled=cancelled,
        heartbeat=heartbeat,
        now_utc=now_utc,
        monotonic=monotonic,
        sleep=sleep,
        pulse_seconds=pulse_seconds,
    )
    timestamp = _timestamp(observed_utc())

    def operation(conn: sqlite3.Connection) -> ScheduleSnapshot:
        _require_running_fence(conn, successor)
        current = _schedule_from_row(_schedule_row(conn, successor.run_id))
        _require_same_schedule(current, schedule)
        if _parse_timestamp(timestamp) < _parse_timestamp(current.not_before_utc):
            cursor = conn.execute(
                "UPDATE analyst_ollama_schedule SET not_before_utc=?,"
                "revision=revision+1,updated_at_utc=? WHERE run_id=? AND revision=? "
                "AND state='backoff'",
                (timestamp, timestamp, successor.run_id, current.revision),
            )
            if cursor.rowcount != 1:
                raise OllamaStateError("resource wait completion fence changed")
        return _schedule_from_row(_schedule_row(conn, successor.run_id))

    return successor, run_immediate(operation, path=path)


def wait_until_resource_resume_authorized(
    run_id: str,
    schedule: ScheduleSnapshot,
    *,
    cancelled: Callable[[], bool],
    now_utc: str,
    observed_utc: Callable[[], str],
    path: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    pulse_seconds: float = 1.0,
) -> ScheduleSnapshot:
    """Wait a bounded lease-free cooldown and durably authorize its retry."""
    _require_run_id(run_id)
    if schedule.state is not ScheduleState.PAUSED_RESOURCE:
        raise OllamaStateError("resource resume wait requires paused state")
    if not callable(cancelled) or not callable(observed_utc):
        raise TypeError("resource resume callbacks must be callable")
    _wait_without_heartbeat(
        schedule,
        cancelled=cancelled,
        now_utc=now_utc,
        monotonic=monotonic,
        sleep=sleep,
        pulse_seconds=pulse_seconds,
    )
    timestamp = _timestamp(observed_utc())

    def operation(conn: sqlite3.Connection) -> ScheduleSnapshot:
        run = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,),
        ).fetchone()
        if run is None or RunState(str(run["state"])) not in {
            RunState.INTERRUPTED,
            RunState.CANCELLED_PENDING_RESUME,
        }:
            raise OllamaStateError("resource-paused run is not resumable")
        if conn.execute(
            "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND run_id IS NOT NULL"
        ).fetchone() is not None:
            raise OllamaStateError("resource resume requires an unowned worker lease")
        current = _schedule_from_row(_schedule_row(conn, run_id))
        _require_same_schedule(current, schedule)
        if current.resume_authorized_at_utc is None:
            effective_due = (
                timestamp
                if _parse_timestamp(timestamp)
                < _parse_timestamp(current.not_before_utc)
                else current.not_before_utc
            )
            cursor = conn.execute(
                "UPDATE analyst_ollama_schedule SET not_before_utc=?,"
                "resume_authorized_at_utc=?,"
                "revision=revision+1,updated_at_utc=? WHERE run_id=? AND revision=? "
                "AND state='paused_resource' AND resume_authorized_at_utc IS NULL",
                (
                    effective_due, timestamp, timestamp, run_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OllamaStateError("resource resume wait completion changed")
        return _schedule_from_row(_schedule_row(conn, run_id))

    return run_immediate(operation, path=path)


def authorize_resource_resume(
    run_id: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> ScheduleSnapshot:
    """Authorize one operator-requested retry after the frozen cooldown."""
    _require_run_id(run_id)
    timestamp = _timestamp(now_utc)
    now = _parse_timestamp(timestamp)

    def operation(conn: sqlite3.Connection) -> ScheduleSnapshot:
        run = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,),
        ).fetchone()
        if run is None:
            raise OllamaStateError("Analyst run does not exist")
        if RunState(str(run["state"])) not in {
            RunState.INTERRUPTED,
            RunState.CANCELLED_PENDING_RESUME,
        }:
            raise OllamaStateError("resource-paused run is not resumable")
        if conn.execute(
            "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND run_id IS NOT NULL"
        ).fetchone() is not None:
            raise OllamaStateError("resource resume requires an unowned worker lease")
        row = _schedule_row(conn, run_id)
        schedule = _schedule_from_row(row)
        if schedule.state is not ScheduleState.PAUSED_RESOURCE:
            raise OllamaStateError("Analyst run is not paused for shared resources")
        if now < _parse_timestamp(schedule.not_before_utc):
            raise OllamaStateError("resource cooldown has not elapsed")
        if schedule.resume_authorized_at_utc is not None:
            return schedule
        cursor = conn.execute(
            "UPDATE analyst_ollama_schedule SET resume_authorized_at_utc=?,"
            "revision=revision+1,updated_at_utc=? WHERE run_id=? "
            "AND state='paused_resource' AND resume_authorized_at_utc IS NULL",
            (timestamp, timestamp, run_id),
        )
        if cursor.rowcount != 1:
            raise OllamaStateError("resource schedule changed during authorization")
        return _schedule_from_row(_schedule_row(conn, run_id))

    return run_immediate(operation, path=path)


def precharge_control_contact(
    fence: LeaseFence,
    kind: ContactKind,
    request_sha256: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> ContactCharge:
    """Charge one exact control request before any network contact."""
    _require_fence_value(fence)
    if not isinstance(kind, ContactKind) or kind is ContactKind.CHAT:
        raise ValueError("control contact kind is not allowed")
    _require_sha(request_sha256, "request sha256")
    expected = _CONTROL_HASHES.get(kind)
    if expected is not None and request_sha256 != expected:
        raise ValueError("control request hash does not match its frozen intent")
    return _precharge(
        fence,
        kind,
        request_sha256,
        chunk_id=None,
        semantic_attempt_no=None,
        timestamp=_timestamp(now_utc),
        path=path,
    )


def precharge_chat_contact(
    fence: LeaseFence,
    chunk_id: int,
    request_sha256: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> ContactCharge:
    """Reserve the next semantic slot without yet consuming an attempt."""
    _require_fence_value(fence)
    if type(chunk_id) is not int or chunk_id <= 0:
        raise ValueError("chunk id must be a positive integer")
    _require_sha(request_sha256, "request sha256")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> ContactCharge:
        _require_running_fence(conn, fence)
        _require_schedule_dispatchable(conn, fence.run_id, timestamp)
        attempt_rows = conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts "
            "WHERE chunk_id=? ORDER BY attempt_no", (chunk_id,),
        ).fetchall()
        if len(attempt_rows) >= 2:
            raise OllamaStateError("chunk exhausted its two-attempt budget")
        if attempt_rows and str(attempt_rows[-1]["state"]) == "dispatching":
            raise OllamaStateError("chunk already has a live semantic attempt")
        row = conn.execute(
            "SELECT c.state,f.work_state,f.active_generation "
            "FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE c.chunk_id=? AND f.run_id=?",
            (chunk_id, fence.run_id),
        ).fetchone()
        if (
            row is None
            or str(row["state"]) != "pending"
            or str(row["work_state"]) != "active"
            or int(row["active_generation"]) != fence.generation
        ):
            raise OllamaStateError("chunk is not dispatchable by this worker")
        semantic_no = len(attempt_rows) + 1
        return _insert_contact(
            conn,
            fence,
            ContactKind.CHAT,
            request_sha256,
            chunk_id,
            semantic_no,
            timestamp,
        )

    return run_immediate(operation, path=path)


def finish_contact(
    fence: LeaseFence,
    contact_id: str,
    status: ContactStatus,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> ContactFinish:
    """Close one precharged contact and atomically map its durable outcome."""
    _require_fence_value(fence)
    _require_sha(contact_id, "contact id")
    if not isinstance(status, ContactStatus) or status is ContactStatus.DISPATCHING:
        raise ValueError("contact status must be terminal")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> ContactFinish:
        _require_running_fence(conn, fence)
        row = conn.execute(
            "SELECT * FROM analyst_ollama_contacts WHERE contact_id=?",
            (contact_id,),
        ).fetchone()
        if row is None or str(row["state"]) != ContactStatus.DISPATCHING.value:
            raise OllamaStateError("contact is missing or already terminal")
        if (
            str(row["run_id"]) != fence.run_id
            or int(row["lease_generation"]) != fence.generation
        ):
            raise OllamaStateError("contact is not owned by this worker generation")
        kind = ContactKind(str(row["kind"]))
        if (
            status is ContactStatus.MODEL_INVALID
            and kind not in {ContactKind.CHAT, ContactKind.CANCELLATION_HEALTH}
        ):
            raise OllamaStateError("control contact cannot report model validation")
        before = int(row["resource_failures_before"])
        schedule = _schedule_from_row(_schedule_row(conn, fence.run_id))
        if schedule.consecutive_failures != before:
            raise OllamaStateError("contact and resource schedule have drifted")

        attempt_id: str | None = None
        semantic_no = (
            None if row["semantic_attempt_no"] is None
            else int(row["semantic_attempt_no"])
        )
        lease_released = False
        if status is ContactStatus.RESOURCE_BUSY:
            schedule, lease_released = _finish_resource_busy(
                conn, fence, row, schedule, timestamp,
            )
            after = schedule.consecutive_failures
        else:
            after = 0 if resets_resource_streak(kind, status) else before
            if kind is ContactKind.CHAT:
                attempt_id = _materialize_semantic_attempt(
                    conn, row, status, timestamp,
                )
            _close_contact(conn, row, status, timestamp, attempt_id, after)
            if after == 0 and before != 0:
                _reset_schedule(conn, fence.run_id, timestamp)
            schedule = _schedule_from_row(_schedule_row(conn, fence.run_id))

        return ContactFinish(
            contact_id=contact_id,
            kind=kind,
            status=status,
            semantic_attempt_no=semantic_no,
            attempt_id=attempt_id,
            schedule=schedule,
            lease_released=lease_released,
        )

    return run_immediate(operation, path=path)


def reconcile_dispatching_contacts(
    conn: sqlite3.Connection,
    run_id: str,
    timestamp: str,
    *,
    cancelled: bool,
) -> None:
    """Close execution-uncertain contacts inside the caller's recovery txn."""
    _require_run_id(run_id)
    if type(timestamp) is not str or not 1 <= len(timestamp) <= 40:
        raise ValueError("recovery timestamp is invalid")
    status = (
        ContactStatus.CANCELLED_UNVERIFIED
        if cancelled
        else ContactStatus.ORPHANED_UNKNOWN
    )
    rows = conn.execute(
        "SELECT * FROM analyst_ollama_contacts "
        "WHERE run_id=? AND state='dispatching' ORDER BY contact_no",
        (run_id,),
    ).fetchall()
    if len(rows) > 1:
        raise OllamaStateError("multiple dispatching contacts violate the global gate")
    for row in rows:
        kind = ContactKind(str(row["kind"]))
        attempt_id = (
            _materialize_semantic_attempt(conn, row, status, timestamp)
            if kind is ContactKind.CHAT
            else None
        )
        before = int(row["resource_failures_before"])
        _close_contact(conn, row, status, timestamp, attempt_id, before)


def _precharge(
    fence: LeaseFence,
    kind: ContactKind,
    request_sha256: str,
    *,
    chunk_id: int | None,
    semantic_attempt_no: int | None,
    timestamp: str,
    path: Path | None,
) -> ContactCharge:
    def operation(conn: sqlite3.Connection) -> ContactCharge:
        _require_running_fence(conn, fence)
        _require_schedule_dispatchable(conn, fence.run_id, timestamp)
        return _insert_contact(
            conn,
            fence,
            kind,
            request_sha256,
            chunk_id,
            semantic_attempt_no,
            timestamp,
        )

    return run_immediate(operation, path=path)


def _insert_contact(
    conn: sqlite3.Connection,
    fence: LeaseFence,
    kind: ContactKind,
    request_sha256: str,
    chunk_id: int | None,
    semantic_attempt_no: int | None,
    timestamp: str,
) -> ContactCharge:
    if conn.execute(
        "SELECT 1 FROM analyst_model_attempts a "
        "JOIN analyst_chunks c ON c.chunk_id=a.chunk_id "
        "JOIN analyst_files f ON f.file_id=c.file_id "
        "WHERE f.run_id=? AND a.state='dispatching' LIMIT 1",
        (fence.run_id,),
    ).fetchone() is not None:
        raise OllamaStateError(
            "a successful model contact still requires its durable checkpoint"
        )
    if conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts WHERE state='dispatching' LIMIT 1"
    ).fetchone() is not None:
        raise OllamaStateError("another Ollama contact is already dispatching")
    if kind is ContactKind.CHAT:
        count = int(conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts "
            "WHERE kind='chat' AND chunk_id=?", (chunk_id,),
        ).fetchone()[0])
        if count >= MAX_CHAT_CONTACTS_PER_CHUNK:
            raise OllamaStateError("chat contact evidence reached its frozen cap")
    else:
        count = int(conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts "
            "WHERE run_id=? AND kind!='chat'", (fence.run_id,),
        ).fetchone()[0])
        if count >= MAX_CONTROL_CONTACTS_PER_RUN:
            raise OllamaStateError("control contact evidence reached its frozen cap")
    contact_no = int(conn.execute(
        "SELECT coalesce(max(contact_no),0)+1 FROM analyst_ollama_contacts "
        "WHERE run_id=?", (fence.run_id,),
    ).fetchone()[0])
    schedule = _schedule_from_row(_schedule_row(conn, fence.run_id))
    contact_id = _contact_id(
        fence, contact_no, kind, chunk_id, semantic_attempt_no, request_sha256,
    )
    try:
        conn.execute(
            "INSERT INTO analyst_ollama_contacts("
            "contact_id,run_id,contact_no,kind,chunk_id,semantic_attempt_no,"
            "request_sha256,lease_generation,state,charged_at_utc,"
            "resource_failures_before) VALUES(?,?,?,?,?,?,?,?,'dispatching',?,?)",
            (
                contact_id, fence.run_id, contact_no, kind.value, chunk_id,
                semantic_attempt_no, request_sha256, fence.generation,
                timestamp, schedule.consecutive_failures,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise OllamaStateError("contact charge conflicts with durable evidence") from exc
    return ContactCharge(
        contact_id, fence.run_id, contact_no, kind, chunk_id,
        semantic_attempt_no, request_sha256, fence.generation,
        schedule.consecutive_failures,
    )


def _finish_resource_busy(
    conn: sqlite3.Connection,
    fence: LeaseFence,
    row: sqlite3.Row,
    schedule: ScheduleSnapshot,
    timestamp: str,
) -> tuple[ScheduleSnapshot, bool]:
    failures = min(schedule.consecutive_failures + 1, 6)
    delay = RESOURCE_BACKOFF_SECONDS[failures - 1]
    deadline = _format_timestamp(_parse_timestamp(timestamp) + timedelta(seconds=delay))
    state = (
        ScheduleState.PAUSED_RESOURCE
        if failures == 6
        else ScheduleState.BACKOFF
    )
    _close_contact(
        conn, row, ContactStatus.RESOURCE_BUSY, timestamp, None, failures,
    )
    cursor = conn.execute(
        "UPDATE analyst_ollama_schedule SET state=?,consecutive_failures=?,"
        "delay_seconds=?,not_before_utc=?,resume_authorized_at_utc=NULL,"
        "revision=revision+1,updated_at_utc=? WHERE run_id=? AND revision=?",
        (
            state.value, failures, delay, deadline, timestamp, fence.run_id,
            schedule.revision,
        ),
    )
    if cursor.rowcount != 1:
        raise OllamaStateError("resource schedule changed during contact finish")
    released = failures == 6
    if released:
        conn.execute(
            "UPDATE analyst_files SET work_state='pending',active_generation=NULL,"
            "updated_at_utc=?,revision=revision+1 WHERE run_id=? "
            "AND work_state='active' AND active_generation=?",
            (timestamp, fence.run_id, fence.generation),
        )
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='interrupted',updated_at_utc=?,"
            "revision=revision+1 WHERE run_id=? AND state='running'",
            (timestamp, fence.run_id),
        )
        if cursor.rowcount != 1:
            raise OllamaStateError("run changed during resource pause")
        if _clear_lease(conn, fence) != 1:
            raise OllamaStateError("worker fence changed during resource pause")
    return _schedule_from_row(_schedule_row(conn, fence.run_id)), released


def _materialize_semantic_attempt(
    conn: sqlite3.Connection,
    contact: sqlite3.Row,
    status: ContactStatus,
    timestamp: str,
) -> str:
    state = semantic_attempt_state(status)
    if state is None:
        raise OllamaStateError("resource contact cannot consume a semantic attempt")
    chunk_id = int(contact["chunk_id"])
    attempt_no = int(contact["semantic_attempt_no"])
    request_sha256 = str(contact["request_sha256"])
    attempt_id = hashlib.sha256(
        f"{chunk_id}\0{attempt_no}\0{request_sha256}".encode("ascii")
    ).hexdigest()
    finished = None if state is AttemptState.DISPATCHING else timestamp
    failure = None if state is AttemptState.DISPATCHING else state.value
    try:
        conn.execute(
            "INSERT INTO analyst_model_attempts("
            "attempt_id,chunk_id,attempt_no,request_sha256,state,charged_at_utc,"
            "finished_at_utc,failure_code) VALUES(?,?,?,?,?,?,?,?)",
            (
                attempt_id, chunk_id, attempt_no, request_sha256, state.value,
                str(contact["charged_at_utc"]), finished, failure,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise OllamaStateError("semantic attempt slot changed after precharge") from exc
    if attempt_no == 2 and state in {
        AttemptState.SCHEMA_INVALID,
        AttemptState.MODEL_TIMEOUT,
        AttemptState.MODEL_TRANSPORT_ERROR,
    }:
        chunk_state = {
            AttemptState.SCHEMA_INVALID: "model_invalid",
            AttemptState.MODEL_TIMEOUT: "model_timeout",
            AttemptState.MODEL_TRANSPORT_ERROR: "model_transport_error",
        }[state]
        cursor = conn.execute(
            "UPDATE analyst_chunks SET state=? "
            "WHERE chunk_id=? AND state='pending'",
            (chunk_state, chunk_id),
        )
        if cursor.rowcount != 1:
            raise OllamaStateError("chunk changed while closing attempt two")
    return attempt_id


def _close_contact(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    status: ContactStatus,
    timestamp: str,
    attempt_id: str | None,
    resource_after: int,
) -> None:
    cursor = conn.execute(
        "UPDATE analyst_ollama_contacts SET state=?,finished_at_utc=?,"
        "attempt_id=?,resource_failures_after=? "
        "WHERE contact_id=? AND state='dispatching' AND finished_at_utc IS NULL",
        (
            status.value, timestamp, attempt_id, resource_after,
            str(row["contact_id"]),
        ),
    )
    if cursor.rowcount != 1:
        raise OllamaStateError("contact changed while it was being closed")


def _reset_schedule(conn: sqlite3.Connection, run_id: str, timestamp: str) -> None:
    cursor = conn.execute(
        "UPDATE analyst_ollama_schedule SET state='available',"
        "consecutive_failures=0,delay_seconds=0,not_before_utc=NULL,"
        "resume_authorized_at_utc=NULL,revision=revision+1,updated_at_utc=? "
        "WHERE run_id=? AND consecutive_failures!=0",
        (timestamp, run_id),
    )
    if cursor.rowcount != 1:
        raise OllamaStateError("resource schedule did not reset exactly once")


def _require_schedule_dispatchable(
    conn: sqlite3.Connection, run_id: str, timestamp: str,
) -> ScheduleSnapshot:
    schedule = _schedule_from_row(_schedule_row(conn, run_id))
    if schedule.state is ScheduleState.AVAILABLE:
        return schedule
    authorized_pause = (
        schedule.state is ScheduleState.PAUSED_RESOURCE
        and schedule.resume_authorized_at_utc is not None
    )
    if (
        not authorized_pause
        and _parse_timestamp(timestamp) < _parse_timestamp(schedule.not_before_utc)
    ):
        raise OllamaStateError("resource retry is not due")
    if (
        schedule.state is ScheduleState.PAUSED_RESOURCE
        and schedule.resume_authorized_at_utc is None
    ):
        raise OllamaStateError("resource pause requires explicit resume authorization")
    return schedule


def _require_same_schedule(
    current: ScheduleSnapshot, observed: ScheduleSnapshot,
) -> None:
    if current != observed:
        raise OllamaStateError("resource schedule changed during bounded wait")


def _wait_without_heartbeat(
    schedule: ScheduleSnapshot,
    *,
    cancelled: Callable[[], bool],
    now_utc: str,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    pulse_seconds: float,
) -> None:
    if not callable(monotonic) or not callable(sleep):
        raise TypeError("resource wait clock and sleep must be callable")
    if type(pulse_seconds) not in {int, float} or not 0 < pulse_seconds <= 2:
        raise ValueError("resource wait pulse must be within two seconds")
    remaining = remaining_resource_wait(schedule, now_utc=now_utc)
    if cancelled():
        raise ResourceWaitCancelled("resource wait was cancelled")
    start = float(monotonic())
    if not math.isfinite(start):
        raise OllamaStateError("resource wait monotonic clock is invalid")
    deadline = start + remaining
    last = start
    while last < deadline:
        sleep(min(float(pulse_seconds), deadline - last))
        if cancelled():
            raise ResourceWaitCancelled("resource wait was cancelled")
        observed = float(monotonic())
        if not math.isfinite(observed) or observed < last:
            raise OllamaStateError("resource wait monotonic clock moved backwards")
        last = observed


def _schedule_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM analyst_ollama_schedule WHERE run_id=?", (run_id,),
    ).fetchone()
    if row is None:
        raise OllamaStateError("Analyst run has no Ollama schedule")
    return row


def _schedule_from_row(row: sqlite3.Row) -> ScheduleSnapshot:
    try:
        return ScheduleSnapshot(
            state=ScheduleState(str(row["state"])),
            consecutive_failures=int(row["consecutive_failures"]),
            delay_seconds=int(row["delay_seconds"]),
            not_before_utc=(
                None if row["not_before_utc"] is None
                else str(row["not_before_utc"])
            ),
            resume_authorized_at_utc=(
                None if row["resume_authorized_at_utc"] is None
                else str(row["resume_authorized_at_utc"])
            ),
            revision=int(row["revision"]),
        )
    except (TypeError, ValueError) as exc:
        raise OllamaStateError("persisted resource schedule is malformed") from exc


def _require_running_fence(conn: sqlite3.Connection, fence: LeaseFence) -> None:
    if conn.execute(
        "SELECT 1 FROM analyst_gpu_lease WHERE " + _FENCE_WHERE,
        _fence_values(fence),
    ).fetchone() is None:
        raise OllamaStateError("worker lease fence no longer matches")
    if conn.execute(
        "SELECT 1 FROM analyst_runs WHERE run_id=? AND state='running'",
        (fence.run_id,),
    ).fetchone() is None:
        raise OllamaStateError("Analyst run is not running")


def _clear_lease(conn: sqlite3.Connection, fence: LeaseFence) -> int:
    return conn.execute(
        "UPDATE analyst_gpu_lease SET generation=generation+1,run_id=NULL,"
        "owner_token=NULL,pid=NULL,start_ticks=NULL,boot_id=NULL,"
        "heartbeat_monotonic_ns=NULL,claimed_at_utc=NULL,heartbeat_at_utc=NULL "
        "WHERE " + _FENCE_WHERE,
        _fence_values(fence),
    ).rowcount


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation,
        fence.run_id,
        fence.owner_token,
        fence.process.pid,
        fence.process.start_ticks,
        fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _contact_id(
    fence: LeaseFence,
    contact_no: int,
    kind: ContactKind,
    chunk_id: int | None,
    semantic_attempt_no: int | None,
    request_sha256: str,
) -> str:
    encoded = "\0".join(
        (
            fence.run_id,
            str(contact_no),
            kind.value,
            "" if chunk_id is None else str(chunk_id),
            "" if semantic_attempt_no is None else str(semantic_attempt_no),
            request_sha256,
            str(fence.generation),
        )
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: str | None) -> str:
    timestamp = _format_timestamp(datetime.now(timezone.utc)) if value is None else value
    if type(timestamp) is not str or not 1 <= len(timestamp) <= 40:
        raise ValueError("timestamp is invalid")
    _parse_timestamp(timestamp)
    return timestamp


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    timespec = "seconds" if value.microsecond == 0 else "microseconds"
    return value.astimezone(timezone.utc).isoformat(timespec=timespec).replace(
        "+00:00", "Z"
    )


def _require_fence_value(fence: LeaseFence) -> None:
    if not isinstance(fence, LeaseFence):
        raise TypeError("fence must be a LeaseFence")


def _require_run_id(value: str) -> None:
    if type(value) is not str or not value or len(value) > 128:
        raise ValueError("run id is invalid")


def _require_sha(value: str, name: str) -> None:
    if type(value) is not str or _LOWER_SHA.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")


__all__ = [
    "OllamaStateError",
    "ResourceWaitCancelled",
    "authorize_resource_resume",
    "finish_contact",
    "get_schedule",
    "precharge_chat_contact",
    "precharge_control_contact",
    "reconcile_dispatching_contacts",
    "remaining_resource_wait",
    "wait_for_resource_retry",
    "wait_until_resource_resume_authorized",
    "wait_until_resource_retry_due",
]
