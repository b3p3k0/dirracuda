"""Atomic global worker lease, recovery, and cancellation for Analyst."""

from __future__ import annotations

import os
import re
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .process_identity import (
    IdentityReader,
    LeaseEvidence,
    ProcessIdentity,
    ProcessIdentityUnavailable,
    ReattachDecision,
    decide_reattachment,
    read_process_identity,
)
from .state import RESUMABLE_RUN_STATES, RunState
from .store import open_connection, run_immediate


HEARTBEAT_MAX_AGE_NS = 10_000_000_000
HEARTBEAT_FUTURE_TOLERANCE_NS = 1_000_000_000
_TOKEN_RE = re.compile(r"[0-9a-f]{64}\Z")


class LeaseError(RuntimeError):
    """Persisted lease or run state violates the frozen C8 contract."""


class ReconcileResult(str, Enum):
    NO_LEASE = "no_lease"
    REATTACHED = "reattached"
    CLEARED_INTERRUPTED = "cleared_interrupted"
    CLEARED_CANCELLED = "cleared_cancelled"
    BLOCKED_STALE_LIVE = "blocked_stale_live"
    BLOCKED_INVALID_HEARTBEAT = "blocked_invalid_heartbeat"
    BLOCKED_UNVERIFIABLE = "blocked_unverifiable"
    RACE_LOST = "race_lost"


@dataclass(frozen=True, slots=True)
class LeaseFence:
    generation: int
    run_id: str
    owner_token: str
    process: ProcessIdentity
    heartbeat_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("lease generation must be a positive integer")
        if not self.run_id or _TOKEN_RE.fullmatch(self.owner_token) is None:
            raise ValueError("lease run or owner token is invalid")
        if (type(self.heartbeat_monotonic_ns) is not int
                or self.heartbeat_monotonic_ns < 0):
            raise ValueError("lease heartbeat is invalid")

    @property
    def evidence(self) -> LeaseEvidence:
        return LeaseEvidence(
            run_id=self.run_id,
            owner_token=self.owner_token,
            process=self.process,
            heartbeat_monotonic_ns=self.heartbeat_monotonic_ns,
        )


@dataclass(frozen=True, slots=True)
class WorkerPulse:
    """A successor lease fence paired with the same transaction's cancel state."""

    fence: LeaseFence
    cancel_requested: bool

    def __post_init__(self) -> None:
        if not isinstance(self.fence, LeaseFence):
            raise ValueError("worker pulse requires a lease fence")
        if type(self.cancel_requested) is not bool:
            raise ValueError("worker pulse cancel state must be bool")


def claim_worker(
    run_id: str,
    process: ProcessIdentity,
    *,
    owner_token: str | None = None,
    heartbeat_monotonic_ns: int | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> LeaseFence | None:
    """Claim the global slot atomically; return ``None`` when another owner won."""
    _require_run_id(run_id)
    token = os.urandom(32).hex() if owner_token is None else owner_token
    _require_token(token)
    heartbeat = (
        time.monotonic_ns()
        if heartbeat_monotonic_ns is None
        else _require_nonnegative_int(heartbeat_monotonic_ns, "heartbeat")
    )
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> LeaseFence | None:
        lease = _lease_row(conn)
        if lease["run_id"] is not None:
            existing = _fence_from_row(lease)
            if (
                existing.run_id == run_id
                and existing.owner_token == token
                and existing.process == process
            ):
                state_row = conn.execute(
                    "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if state_row is None or str(state_row["state"]) != "running":
                    raise LeaseError("Analyst run no longer permits an idempotent claim")
                return existing
            return None

        run = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LeaseError("Analyst run does not exist")
        state = RunState(str(run["state"]))
        if state not in RESUMABLE_RUN_STATES:
            raise LeaseError("Analyst run is not claimable")
        schedule = conn.execute(
            "SELECT state,not_before_utc,resume_authorized_at_utc "
            "FROM analyst_ollama_schedule WHERE run_id=?", (run_id,),
        ).fetchone()
        if schedule is None:
            raise LeaseError("Analyst run has no Ollama schedule")
        schedule_state = str(schedule["state"])
        if schedule_state == "backoff" or (
            schedule_state == "paused_resource"
            and schedule["resume_authorized_at_utc"] is None
        ):
            deadline = _parse_utc(str(schedule["not_before_utc"]))
            if _parse_utc(timestamp) < deadline:
                raise LeaseError("Analyst resource retry is not due")
        if (
            schedule_state == "paused_resource"
            and schedule["resume_authorized_at_utc"] is None
        ):
            raise LeaseError("Analyst resource pause requires explicit authorization")

        generation = int(lease["generation"]) + 1
        cursor = conn.execute(
            "UPDATE analyst_gpu_lease SET generation=?,run_id=?,owner_token=?,"
            "pid=?,start_ticks=?,boot_id=?,heartbeat_monotonic_ns=?,"
            "claimed_at_utc=?,heartbeat_at_utc=? "
            "WHERE slot=1 AND generation=? AND run_id IS NULL",
            (
                generation, run_id, token, process.pid, process.start_ticks,
                process.boot_id, heartbeat, timestamp, timestamp,
                int(lease["generation"]),
            ),
        )
        if cursor.rowcount != 1:
            return None
        if state is RunState.CANCELLED_PENDING_RESUME:
            conn.execute(
                "UPDATE analyst_files SET work_state='pending',revision=revision+1,"
                "updated_at_utc=? WHERE run_id=? "
                "AND work_state='cancelled_pending_resume'",
                (timestamp, run_id),
            )
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='running',cancel_requested_at_utc=NULL,"
            "revision=revision+1,updated_at_utc=? WHERE run_id=? AND state=?",
            (timestamp, run_id, state.value),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst run changed during lease claim")
        return LeaseFence(generation, run_id, token, process, heartbeat)

    return run_immediate(operation, path=path)


def heartbeat(
    fence: LeaseFence,
    *,
    heartbeat_monotonic_ns: int | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> LeaseFence:
    """Advance one exact owner's heartbeat or reject a stale worker."""
    value = (
        time.monotonic_ns()
        if heartbeat_monotonic_ns is None
        else _require_nonnegative_int(heartbeat_monotonic_ns, "heartbeat")
    )
    if value < fence.heartbeat_monotonic_ns:
        raise LeaseError("Analyst heartbeat cannot move backwards")
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> LeaseFence:
        cursor = conn.execute(
            "UPDATE analyst_gpu_lease SET heartbeat_monotonic_ns=?,heartbeat_at_utc=? "
            "WHERE " + _FENCE_WHERE,
            (value, timestamp, *_fence_values(fence)),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst worker lease fence no longer matches")
        return LeaseFence(
            fence.generation, fence.run_id, fence.owner_token, fence.process, value
        )

    return run_immediate(operation, path=path)


def pulse_worker(
    fence: LeaseFence,
    *,
    heartbeat_monotonic_ns: int | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> WorkerPulse:
    """Advance the exact fence and read durable cancellation in one transaction."""
    if not isinstance(fence, LeaseFence):
        raise TypeError("fence must be a LeaseFence")
    value = (
        time.monotonic_ns()
        if heartbeat_monotonic_ns is None
        else _require_nonnegative_int(heartbeat_monotonic_ns, "heartbeat")
    )
    if value <= fence.heartbeat_monotonic_ns:
        raise LeaseError("Analyst pulse heartbeat must advance")
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> WorkerPulse:
        _require_exact_lease(conn, fence)
        run = conn.execute(
            "SELECT state,cancel_requested_at_utc FROM analyst_runs WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if run is None:
            raise LeaseError("Analyst run does not exist")
        state = RunState(str(run["state"]))
        cancel_timestamp = run["cancel_requested_at_utc"]
        if state in {RunState.RUNNING, RunState.FINALIZING} and cancel_timestamp is None:
            cancel_requested = False
        elif (
            state is RunState.CANCEL_REQUESTED
            and isinstance(cancel_timestamp, str)
            and cancel_timestamp
        ):
            cancel_requested = True
        else:
            raise LeaseError("Analyst run state contradicts its cancel intent")
        cursor = conn.execute(
            "UPDATE analyst_gpu_lease SET heartbeat_monotonic_ns=?,"
            "heartbeat_at_utc=? WHERE " + _FENCE_WHERE,
            (value, timestamp, *_fence_values(fence)),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst worker lease fence no longer matches")
        successor = LeaseFence(
            fence.generation, fence.run_id, fence.owner_token, fence.process, value,
        )
        return WorkerPulse(successor, cancel_requested)

    return run_immediate(operation, path=path)


def release_worker(
    fence: LeaseFence, *, now_utc: str | None = None, path: Path | None = None,
) -> RunState:
    """Gracefully checkpoint an unfinished run and release its exact lease."""
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> RunState:
        _require_exact_lease(conn, fence)
        row = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (fence.run_id,)
        ).fetchone()
        if row is None:
            raise LeaseError("Analyst run does not exist")
        state = RunState(str(row["state"]))
        cancelled = state is RunState.CANCEL_REQUESTED
        if not cancelled and state not in {RunState.RUNNING, RunState.FINALIZING}:
            raise LeaseError("Analyst run cannot release an active lease")
        target = (
            RunState.CANCELLED_PENDING_RESUME if cancelled else RunState.INTERRUPTED
        )
        _cancel_inflight(conn, fence.run_id, timestamp, cancelled=cancelled)
        cursor = conn.execute(
            "UPDATE analyst_runs SET state=?,finalization_token=NULL,"
            "updated_at_utc=?,revision=revision+1 "
            "WHERE run_id=? AND state=?",
            (target.value, timestamp, fence.run_id, state.value),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst run changed during worker release")
        if _clear_exact_lease(conn, fence) != 1:
            raise LeaseError("Analyst worker lease fence no longer matches")
        return target

    return run_immediate(operation, path=path)


def request_cancel(
    run_id: str, *, now_utc: str | None = None, path: Path | None = None,
) -> LeaseFence | None:
    """Persist cancellation intent first and return the worker fence to signal."""
    _require_run_id(run_id)
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> LeaseFence | None:
        row = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise LeaseError("Analyst run does not exist")
        state = RunState(str(row["state"]))
        if state is RunState.INTERRUPTED:
            schedule = conn.execute(
                "SELECT state FROM analyst_ollama_schedule WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if schedule is not None and str(schedule["state"]) == "paused_resource":
                _cancel_inflight(conn, run_id, timestamp, cancelled=True)
                cursor = conn.execute(
                    "UPDATE analyst_runs SET state='cancelled_pending_resume',"
                    "cancel_requested_at_utc=?,updated_at_utc=?,revision=revision+1 "
                    "WHERE run_id=? AND state='interrupted'",
                    (timestamp, timestamp, run_id),
                )
                if cursor.rowcount != 1:
                    raise LeaseError("Analyst paused cancel state changed")
                schedule_cursor = conn.execute(
                    "UPDATE analyst_ollama_schedule SET "
                    "resume_authorized_at_utc=NULL,revision=revision+1,"
                    "updated_at_utc=? WHERE run_id=? AND state='paused_resource'",
                    (timestamp, run_id),
                )
                if schedule_cursor.rowcount != 1:
                    raise LeaseError("Analyst paused schedule changed during cancel")
                return None
        if state is RunState.RUNNING:
            conn.execute(
                "UPDATE analyst_runs SET state='cancel_requested',"
                "cancel_requested_at_utc=?,updated_at_utc=?,revision=revision+1 "
                "WHERE run_id=? AND state='running'",
                (timestamp, timestamp, run_id),
            )
        elif state is not RunState.CANCEL_REQUESTED:
            raise LeaseError("Analyst run is not cancellable")
        lease = _lease_row(conn)
        if lease["run_id"] is not None:
            return _require_run_fence(lease, run_id)
        _cancel_inflight(conn, run_id, timestamp, cancelled=True)
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='cancelled_pending_resume',"
            "updated_at_utc=?,revision=revision+1 "
            "WHERE run_id=? AND state='cancel_requested'",
            (timestamp, run_id),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst cancel state changed without a worker lease")
        return None

    return run_immediate(operation, path=path)


def signal_cancel(
    fence: LeaseFence,
    *,
    sig: int = signal.SIGTERM,
    identity_reader: IdentityReader = read_process_identity,
) -> bool:
    """Signal through pidfd only after exact process-identity revalidation."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return False
    try:
        pidfd = os.pidfd_open(fence.process.pid, 0)
    except OSError:
        return False
    try:
        try:
            observed = identity_reader(fence.process.pid)
        except (OSError, ProcessIdentityUnavailable, ValueError):
            return False
        if observed != fence.process:
            return False
        try:
            signal.pidfd_send_signal(pidfd, sig)
        except OSError:
            return False
        return True
    finally:
        os.close(pidfd)


def acknowledge_cancel(
    fence: LeaseFence, *, now_utc: str | None = None, path: Path | None = None,
) -> None:
    """Make a normal cancellation resumable and release the exact lease."""
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> None:
        _require_exact_lease(conn, fence)
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='cancelled_pending_resume',"
            "updated_at_utc=?,revision=revision+1 "
            "WHERE run_id=? AND state='cancel_requested'",
            (timestamp, fence.run_id),
        )
        if cursor.rowcount != 1:
            raise LeaseError("Analyst run is not awaiting cancellation")
        _cancel_inflight(conn, fence.run_id, timestamp, cancelled=True)
        if _clear_exact_lease(conn, fence) != 1:
            raise LeaseError("Analyst worker lease fence no longer matches")

    run_immediate(operation, path=path)


def reconcile_lease(
    *,
    path: Path | None = None,
    now_monotonic_ns: int | None = None,
    now_utc: str | None = None,
    identity_reader: IdentityReader = read_process_identity,
) -> ReconcileResult:
    """Reattach, block, or atomically recover one persisted worker lease."""
    conn = open_connection(path, read_only=True)
    try:
        row = _lease_row(conn)
        if row["run_id"] is None:
            return ReconcileResult.NO_LEASE
        observed = _fence_from_row(row)
    finally:
        conn.close()

    decision = decide_reattachment(
        observed.evidence,
        max_heartbeat_age_ns=HEARTBEAT_MAX_AGE_NS,
        now_monotonic_ns=now_monotonic_ns,
        future_tolerance_ns=HEARTBEAT_FUTURE_TOLERANCE_NS,
        identity_reader=identity_reader,
    )
    if decision is ReattachDecision.REATTACH:
        return ReconcileResult.REATTACHED
    if decision is ReattachDecision.BLOCK_STALE_LIVE:
        return ReconcileResult.BLOCKED_STALE_LIVE
    if decision is ReattachDecision.BLOCK_INVALID_HEARTBEAT:
        return ReconcileResult.BLOCKED_INVALID_HEARTBEAT
    if decision is ReattachDecision.BLOCK_UNVERIFIABLE:
        return ReconcileResult.BLOCKED_UNVERIFIABLE

    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> ReconcileResult:
        row = _lease_row(conn)
        if row["run_id"] is None or not _row_matches_fence(row, observed):
            return ReconcileResult.RACE_LOST
        run = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (observed.run_id,)
        ).fetchone()
        if run is None:
            raise LeaseError("Analyst lease references a missing run")
        state = RunState(str(run["state"]))
        cancelled = state is RunState.CANCEL_REQUESTED
        if not cancelled and state not in {RunState.RUNNING, RunState.FINALIZING}:
            raise LeaseError("Analyst lease references an impossible run state")
        target = (
            RunState.CANCELLED_PENDING_RESUME if cancelled else RunState.INTERRUPTED
        )
        _cancel_inflight(conn, observed.run_id, timestamp, cancelled=cancelled)
        cursor = conn.execute(
            "UPDATE analyst_runs SET state=?,finalization_token=NULL,"
            "updated_at_utc=?,revision=revision+1 "
            "WHERE run_id=? AND state=?",
            (target.value, timestamp, observed.run_id, state.value),
        )
        if cursor.rowcount != 1 or _clear_exact_lease(conn, observed) != 1:
            raise LeaseError("Analyst recovery fence changed")
        return (
            ReconcileResult.CLEARED_CANCELLED
            if cancelled
            else ReconcileResult.CLEARED_INTERRUPTED
        )

    return run_immediate(operation, path=path)


def current_lease(*, path: Path | None = None) -> LeaseFence | None:
    """Return the current durable fence for read-only UI hydration."""
    conn = open_connection(path, read_only=True)
    try:
        row = _lease_row(conn)
        return None if row["run_id"] is None else _fence_from_row(row)
    finally:
        conn.close()


_FENCE_WHERE = (
    "slot=1 AND generation=? AND run_id=? AND owner_token=? AND pid=? "
    "AND start_ticks=? AND boot_id=? AND heartbeat_monotonic_ns=?"
)


def _lease_row(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM analyst_gpu_lease WHERE slot=1").fetchone()
    if row is None:
        raise LeaseError("Analyst GPU lease singleton is missing")
    return row


def _fence_from_row(row: sqlite3.Row) -> LeaseFence:
    try:
        return LeaseFence(
            generation=int(row["generation"]),
            run_id=str(row["run_id"]),
            owner_token=str(row["owner_token"]),
            process=ProcessIdentity(
                pid=int(row["pid"]),
                start_ticks=int(row["start_ticks"]),
                boot_id=str(row["boot_id"]),
            ),
            heartbeat_monotonic_ns=int(row["heartbeat_monotonic_ns"]),
        )
    except (TypeError, ValueError) as exc:
        raise LeaseError("Analyst GPU lease evidence is malformed") from exc


def _require_run_fence(row: sqlite3.Row, run_id: str) -> LeaseFence:
    fence = _fence_from_row(row)
    if fence.run_id != run_id:
        raise LeaseError("Analyst run does not own the global worker lease")
    return fence


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation, fence.run_id, fence.owner_token, fence.process.pid,
        fence.process.start_ticks, fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _row_matches_fence(row: sqlite3.Row, fence: LeaseFence) -> bool:
    try:
        return _fence_values(_fence_from_row(row)) == _fence_values(fence)
    except LeaseError:
        return False


def _require_exact_lease(conn: sqlite3.Connection, fence: LeaseFence) -> None:
    if not _row_matches_fence(_lease_row(conn), fence):
        raise LeaseError("Analyst worker lease fence no longer matches")


def _clear_exact_lease(conn: sqlite3.Connection, fence: LeaseFence) -> int:
    cursor = conn.execute(
        "UPDATE analyst_gpu_lease SET generation=generation+1,run_id=NULL,"
        "owner_token=NULL,pid=NULL,start_ticks=NULL,boot_id=NULL,"
        "heartbeat_monotonic_ns=NULL,claimed_at_utc=NULL,heartbeat_at_utc=NULL "
        "WHERE " + _FENCE_WHERE,
        _fence_values(fence),
    )
    return cursor.rowcount


def _cancel_inflight(
    conn: sqlite3.Connection, run_id: str, timestamp: str, *, cancelled: bool,
) -> None:
    # Imported lazily because the contact module uses LeaseFence as its public fence.
    from .ollama_state import reconcile_dispatching_contacts

    reconcile_dispatching_contacts(
        conn, run_id, timestamp, cancelled=cancelled,
    )
    attempt_state = "cancelled_unverified" if cancelled else "orphaned_unknown"
    conn.execute(
        "UPDATE analyst_model_attempts SET state=?,finished_at_utc=?,failure_code=? "
        "WHERE state='dispatching' AND chunk_id IN ("
        "SELECT c.chunk_id FROM analyst_chunks c JOIN analyst_files f "
        "ON f.file_id=c.file_id WHERE f.run_id=?)",
        (attempt_state, timestamp, attempt_state, run_id),
    )
    file_state = "cancelled_pending_resume" if cancelled else "pending"
    source_states = "('pending','active')" if cancelled else "('active')"
    conn.execute(
        "UPDATE analyst_files SET work_state=?,active_generation=NULL,"
        "updated_at_utc=?,revision=revision+1 WHERE run_id=? "
        f"AND work_state IN {source_states}",
        (file_state, timestamp, run_id),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LeaseError("Analyst resource deadline is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise LeaseError("Analyst resource deadline is not UTC")
    return parsed.astimezone(timezone.utc)


def _require_run_id(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("run_id is invalid")


def _require_token(value: str) -> None:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ValueError("owner token must be 64 lowercase hex characters")


def _require_nonnegative_int(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be nonempty text")
    return value


__all__ = [
    "HEARTBEAT_FUTURE_TOLERANCE_NS",
    "HEARTBEAT_MAX_AGE_NS",
    "LeaseError",
    "LeaseFence",
    "ReconcileResult",
    "WorkerPulse",
    "acknowledge_cancel",
    "claim_worker",
    "current_lease",
    "heartbeat",
    "pulse_worker",
    "reconcile_lease",
    "release_worker",
    "request_cancel",
    "signal_cancel",
]
