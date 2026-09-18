"""Best-effort, fenced host READ reduction over durable Phase 2 summaries."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contact_contract import ContactStatus, ScheduleState
from .lease import LeaseError, LeaseFence, pulse_worker
from .ollama_client import OllamaClient
from .ollama_contract import (
    MAX_SOURCE_CHARS,
    ChatResult,
    OllamaStatus,
    build_read_chat_request,
    new_prompt_nonce,
)
from .ollama_state import (
    ResourceWaitCancelled,
    finish_read_contact,
    precharge_read_contact,
    wait_until_resource_retry_due,
)
from .phase2_contract import Phase2Handoff
from .read_worksheet import parse_read
from .store import open_connection, write_host_read
from .worker_contract import WorkerRunContext


HEARTBEAT_INTERVAL_SECONDS = 2.0
WAIT_PULSE_SECONDS = 1.0
MAX_TOP_QUOTES = 16


class ReadReduceError(RuntimeError):
    """A content-free host READ failure."""

    def __init__(self) -> None:
        super().__init__("read_reduce")


class ReadReduceCancelled(ReadReduceError):
    """The local cooperative stop Event cancelled the host READ."""


class ReadReducePausedResource(ReadReduceError):
    """The shared resource schedule released the worker lease."""


@dataclass(frozen=True, slots=True)
class ReadReduceDependencies:
    """Injectable local transport and clocks for the READ reduce."""

    client: Any = field(default_factory=OllamaClient, repr=False)
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)
    monotonic_ns: Callable[[], int] = field(default=time.monotonic_ns, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    utc_now: Callable[[], str] = field(
        default=lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            not callable(getattr(self.client, "chat", None))
            or not callable(self.monotonic)
            or not callable(self.monotonic_ns)
            or not callable(self.sleep)
            or not callable(self.utc_now)
        ):
            raise TypeError("read reduce dependencies are invalid")


@dataclass(frozen=True, slots=True)
class _ReduceInput:
    summary_text: str = field(repr=False)
    files_total: int
    flagged_files: int
    next_attempt_no: int


@dataclass(slots=True)
class _FenceOwner:
    fence: LeaseFence
    stop_event: threading.Event
    dependencies: ReadReduceDependencies
    path: Path | None
    last_pulse: float

    @classmethod
    def create(
        cls,
        fence: LeaseFence,
        stop_event: threading.Event,
        dependencies: ReadReduceDependencies,
        path: Path | None,
    ) -> "_FenceOwner":
        observed = float(dependencies.monotonic())
        if not math.isfinite(observed):
            raise ReadReduceError
        return cls(fence, stop_event, dependencies, path, observed)

    def pulse(self, *, force: bool = False) -> LeaseFence:
        observed = float(self.dependencies.monotonic())
        if not math.isfinite(observed) or observed < self.last_pulse:
            raise ReadReduceError
        if not force and observed - self.last_pulse < HEARTBEAT_INTERVAL_SECONDS:
            return self.fence
        value = self.dependencies.monotonic_ns()
        if type(value) is not int or value < 0:
            raise ReadReduceError
        value = max(value, self.fence.heartbeat_monotonic_ns + 1)
        try:
            result = pulse_worker(
                self.fence,
                heartbeat_monotonic_ns=value,
                now_utc=self.dependencies.utc_now(),
                path=self.path,
            )
        except LeaseError:
            raise ReadReduceError from None
        self.fence = result.fence
        self.last_pulse = observed
        return self.fence

    def poll(self) -> None:
        self.pulse()

    def heartbeat(self, expected: LeaseFence) -> LeaseFence:
        if expected != self.fence:
            raise ReadReduceError
        return self.pulse(force=True)


def run_read_reduce(
    context: WorkerRunContext,
    handoff: Phase2Handoff,
    stop_event: threading.Event,
    *,
    path: Path | None = None,
    dependencies: ReadReduceDependencies | None = None,
) -> None:
    """Generate and persist one host READ, or leave it unavailable."""
    if (
        type(context) is not WorkerRunContext
        or type(handoff) is not Phase2Handoff
        or not isinstance(stop_event, threading.Event)
    ):
        raise TypeError("read reduce requires typed context, handoff and stop Event")
    chosen = ReadReduceDependencies() if dependencies is None else dependencies
    if type(chosen) is not ReadReduceDependencies:
        raise TypeError("dependencies must be ReadReduceDependencies")
    if context.run_id != handoff.fence.run_id:
        raise ReadReduceError

    reduce_input = _load_reduce_input(handoff.fence, path=path)
    if reduce_input is None:
        return
    if stop_event.is_set():
        raise ReadReduceCancelled
    owner = _FenceOwner.create(handoff.fence, stop_event, chosen, path)
    attempt_no = reduce_input.next_attempt_no
    while attempt_no <= 2:
        nonce = new_prompt_nonce(reduce_input.summary_text)
        request = build_read_chat_request(reduce_input.summary_text, nonce=nonce)
        while True:
            if stop_event.is_set():
                raise ReadReduceCancelled
            charge = precharge_read_contact(
                owner.pulse(force=True),
                attempt_no,
                request.request_sha256,
                now_utc=chosen.utc_now(),
                path=path,
            )
            parsed = None
            try:
                response = chosen.client.chat(
                    request,
                    expected_sha256=request.request_sha256,
                    cancel=stop_event.is_set,
                    poll=owner.poll,
                )
            except Exception:
                response = None
            if type(response) is ChatResult:
                status = response.status
            else:
                status = (
                    OllamaStatus.CANCELLED_UNVERIFIED
                    if stop_event.is_set()
                    else OllamaStatus.TRANSPORT_UNAVAILABLE
                )
            if status is OllamaStatus.SUCCESS:
                try:
                    parsed = parse_read(response.content)
                except (TypeError, ValueError):
                    status = OllamaStatus.MODEL_INVALID
            finished = finish_read_contact(
                owner.pulse(force=True),
                charge.contact_id,
                ContactStatus(status.value),
                now_utc=chosen.utc_now(),
                path=path,
            )
            if status is OllamaStatus.CANCELLED_UNVERIFIED:
                raise ReadReduceCancelled
            if status is OllamaStatus.RESOURCE_BUSY:
                if finished.lease_released:
                    raise ReadReducePausedResource
                _wait_resource(owner, finished.schedule)
                continue
            if status is OllamaStatus.SUCCESS and parsed is not None:
                write_host_read(
                    owner.fence,
                    parsed,
                    read_mode="quick" if context.mode == "fast" else "full",
                    files_read=handoff.reviewed_file_count,
                    files_total=reduce_input.files_total,
                    flagged_files=reduce_input.flagged_files,
                    now_utc=chosen.utc_now(),
                    path=path,
                )
                return
            if status in {
                OllamaStatus.MODEL_INVALID,
                OllamaStatus.REQUEST_TIMEOUT,
                OllamaStatus.TRANSPORT_UNAVAILABLE,
            }:
                attempt_no += 1
                break
            raise ReadReduceError


def _load_reduce_input(
    fence: LeaseFence, *, path: Path | None,
) -> _ReduceInput | None:
    conn = open_connection(path, read_only=True)
    try:
        if conn.execute(
            "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND generation=? "
            "AND run_id=? AND owner_token=? AND pid=? AND start_ticks=? "
            "AND boot_id=? AND heartbeat_monotonic_ns=?",
            (
                fence.generation, fence.run_id, fence.owner_token,
                fence.process.pid, fence.process.start_ticks,
                fence.process.boot_id, fence.heartbeat_monotonic_ns,
            ),
        ).fetchone() is None:
            raise ReadReduceError
        if conn.execute(
            "SELECT 1 FROM analyst_read WHERE run_id=?", (fence.run_id,),
        ).fetchone() is not None:
            return None
        chunks = conn.execute(
            "SELECT f.relative_path,c.document_type,c.subject,c.assessment "
            "FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=? AND c.state='model_response_valid' "
            "ORDER BY f.ordinal,c.chunk_index",
            (fence.run_id,),
        ).fetchall()
        if not chunks:
            return None
        attempts = conn.execute(
            "SELECT attempt_no,state FROM analyst_read_contact WHERE run_id=? "
            "ORDER BY attempt_no",
            (fence.run_id,),
        ).fetchall()
        next_attempt = _next_attempt(attempts)
        if next_attempt > 2:
            return None
        category_counts = _category_counts(conn, fence.run_id)
        quotes = conn.execute(
            "SELECT f.relative_path,m.category,m.quote FROM analyst_model_findings m "
            "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=? "
            "ORDER BY CASE m.category WHEN 'financial' THEN 0 WHEN 'pii' THEN 1 "
            "WHEN 'contact' THEN 2 ELSE 3 END,m.finding_id LIMIT ?",
            (fence.run_id, MAX_TOP_QUOTES),
        ).fetchall()
        files_total = int(conn.execute(
            "SELECT count(*) FROM analyst_files WHERE run_id=?", (fence.run_id,),
        ).fetchone()[0])
        flagged_files = int(conn.execute(
            "SELECT count(DISTINCT file_id) FROM ("
            "SELECT h.file_id FROM analyst_detector_hits h JOIN analyst_files f "
            "ON f.file_id=h.file_id WHERE f.run_id=? UNION SELECT c.file_id "
            "FROM analyst_model_findings m JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=?)",
            (fence.run_id, fence.run_id),
        ).fetchone()[0])
        return _ReduceInput(
            _render_summary(chunks, category_counts, quotes),
            files_total,
            flagged_files,
            next_attempt,
        )
    finally:
        conn.close()


def _next_attempt(rows: list[Any]) -> int:
    if tuple(int(row["attempt_no"]) for row in rows) != tuple(
        range(1, len(rows) + 1)
    ) or len(rows) > 2:
        raise ReadReduceError
    if rows and str(rows[-1]["state"]) == ContactStatus.DISPATCHING.value:
        raise ReadReduceError
    if rows and str(rows[-1]["state"]) == ContactStatus.RESOURCE_BUSY.value:
        return int(rows[-1]["attempt_no"])
    return len(rows) + 1


def _category_counts(conn: Any, run_id: str) -> dict[str, int]:
    counts = {name: 0 for name in ("pii", "financial", "contact", "demographic")}
    rows = conn.execute(
        "SELECT category,count(*) AS n FROM ("
        "SELECT m.category AS category FROM analyst_model_findings m "
        "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
        "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=? UNION ALL "
        "SELECT CASE WHEN h.kind IN ('ssn','dob','passport') THEN 'pii' "
        "WHEN h.kind IN ('card','routing','bank_account','iban') THEN 'financial' "
        "WHEN h.kind IN ('email','phone') THEN 'contact' ELSE 'demographic' END "
        "AS category FROM analyst_detector_hits h JOIN analyst_files f "
        "ON f.file_id=h.file_id WHERE f.run_id=?) GROUP BY category",
        (run_id, run_id),
    ).fetchall()
    for row in rows:
        category = str(row["category"])
        if category not in counts:
            raise ReadReduceError
        counts[category] = int(row["n"])
    return counts


def _render_summary(
    chunks: list[Any], category_counts: dict[str, int], quotes: list[Any],
) -> str:
    lines = [
        "HOST READ REDUCE INPUT",
        "Grounded fact counts:",
        *(f"- {name}: {category_counts[name]}" for name in (
            "pii", "financial", "contact", "demographic"
        )),
        "Top grounded quotes:",
    ]
    if quotes:
        lines.extend(
            "- file=" + _text(row["relative_path"], 512)
            + "; category=" + str(row["category"])
            + "; quote=" + _text(row["quote"], 240)
            for row in quotes
        )
    else:
        lines.append("- none")
    lines.append("Model-reviewed chunks:")
    lines.extend(
        "- file=" + _text(row["relative_path"], 512)
        + "; document_type=" + _text(row["document_type"], 80)
        + "; subject=" + _text(row["subject"], 160)
        + "; assessment=" + str(row["assessment"])
        for row in chunks
    )
    rendered = "\n".join(lines)
    if len(rendered) <= MAX_SOURCE_CHARS:
        return rendered
    marker = "\n...[bounded]"
    return rendered[:MAX_SOURCE_CHARS - len(marker)] + marker


def _text(value: object, limit: int) -> str:
    text = str(value)[:limit]
    return json.dumps(text, ensure_ascii=False, allow_nan=False)


def _wait_resource(owner: _FenceOwner, schedule: Any) -> None:
    try:
        successor, updated = wait_until_resource_retry_due(
            owner.fence,
            schedule,
            cancelled=owner.stop_event.is_set,
            heartbeat=owner.heartbeat,
            now_utc=owner.dependencies.utc_now(),
            observed_utc=owner.dependencies.utc_now,
            path=owner.path,
            monotonic=owner.dependencies.monotonic,
            sleep=owner.dependencies.sleep,
            pulse_seconds=WAIT_PULSE_SECONDS,
        )
    except ResourceWaitCancelled:
        raise ReadReduceCancelled from None
    owner.fence = successor
    if updated.state is not ScheduleState.BACKOFF:
        raise ReadReduceError


__all__ = [
    "ReadReduceCancelled",
    "ReadReduceDependencies",
    "ReadReduceError",
    "ReadReducePausedResource",
    "run_read_reduce",
]
