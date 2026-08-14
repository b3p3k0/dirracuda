"""Linux worker identity and pure lease-reattachment decisions."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

PROC_RECORD_MAX_BYTES = 16 * 1024


class ProcessIdentityUnavailable(RuntimeError):
    """Process identity could not be verified safely."""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    boot_id: str

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if type(self.start_ticks) is not int or self.start_ticks < 0:
            raise ValueError("start_ticks must be a nonnegative integer")
        _canonical_uuid(self.boot_id)


@dataclass(frozen=True, slots=True)
class LeaseEvidence:
    run_id: str
    owner_token: str
    process: ProcessIdentity
    heartbeat_monotonic_ns: int

    def __post_init__(self) -> None:
        if not self.run_id or not self.owner_token:
            raise ValueError("run_id and owner_token must be nonempty")
        if (type(self.heartbeat_monotonic_ns) is not int
                or self.heartbeat_monotonic_ns < 0):
            raise ValueError("heartbeat must be a nonnegative integer")


class ReattachDecision(str, Enum):
    REATTACH = "reattach"
    CLEAR_STALE = "clear_stale"
    BLOCK_STALE_LIVE = "block_stale_live"
    BLOCK_INVALID_HEARTBEAT = "block_invalid_heartbeat"
    BLOCK_UNVERIFIABLE = "block_unverifiable"


IdentityReader = Callable[[int], ProcessIdentity | None]


def current_process_identity(*, proc_root: Path = Path("/proc")) -> ProcessIdentity:
    identity = read_process_identity(os.getpid(), proc_root=proc_root)
    if identity is None:
        raise ProcessIdentityUnavailable("current process identity disappeared")
    return identity


def read_process_identity(
    pid: int, *, proc_root: Path = Path("/proc")
) -> ProcessIdentity | None:
    """Read PID reuse-resistant identity, or ``None`` only when PID is absent."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    try:
        stat_body = _read_bounded(proc_root / str(pid) / "stat")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ProcessIdentityUnavailable("process stat is unreadable") from exc
    try:
        start_ticks = parse_start_ticks(stat_body, expected_pid=pid)
        boot_body = _read_bounded(proc_root / "sys/kernel/random/boot_id")
        boot_id = _canonical_uuid(boot_body.decode("ascii").strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProcessIdentityUnavailable("process identity is malformed") from exc
    return ProcessIdentity(pid=pid, start_ticks=start_ticks, boot_id=boot_id)


def parse_start_ticks(body: bytes, *, expected_pid: int) -> int:
    """Parse `/proc/<pid>/stat` field 22 without splitting the `(comm)` field."""
    if not isinstance(body, bytes) or len(body) > PROC_RECORD_MAX_BYTES:
        raise ValueError("process stat record is invalid")
    text = body.decode("ascii")
    prefix = f"{expected_pid} ("
    if not text.startswith(prefix):
        raise ValueError("process stat pid does not match")
    close = text.rfind(") ")
    if close < len(prefix):
        raise ValueError("process stat command field is malformed")
    fields = text[close + 2:].split()
    if len(fields) < 20 or len(fields[0]) != 1:
        raise ValueError("process stat fields are incomplete")
    value = fields[19]
    if not value.isascii() or not value.isdigit():
        raise ValueError("process start time is invalid")
    return int(value)


def decide_reattachment(
    lease: LeaseEvidence,
    *,
    max_heartbeat_age_ns: int,
    now_monotonic_ns: int | None = None,
    future_tolerance_ns: int = 1_000_000_000,
    identity_reader: IdentityReader = read_process_identity,
) -> ReattachDecision:
    """Return the only safe action for one persisted global lease."""
    for name, value, allow_zero in (
        ("max_heartbeat_age_ns", max_heartbeat_age_ns, False),
        ("future_tolerance_ns", future_tolerance_ns, True),
    ):
        if type(value) is not int or value < 0 or (not allow_zero and value == 0):
            raise ValueError(f"{name} has an invalid bound")
    now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
    if type(now) is not int or now < 0:
        raise ValueError("now_monotonic_ns must be a nonnegative integer")

    try:
        observed = identity_reader(lease.process.pid)
    except (OSError, ProcessIdentityUnavailable, ValueError):
        return ReattachDecision.BLOCK_UNVERIFIABLE
    if observed is None or observed != lease.process:
        return ReattachDecision.CLEAR_STALE

    heartbeat = lease.heartbeat_monotonic_ns
    if heartbeat > now + future_tolerance_ns:
        return ReattachDecision.BLOCK_INVALID_HEARTBEAT
    age = max(0, now - heartbeat)
    if age > max_heartbeat_age_ns:
        return ReattachDecision.BLOCK_STALE_LIVE
    return ReattachDecision.REATTACH


def _read_bounded(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(4096, PROC_RECORD_MAX_BYTES + 1 - total))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > PROC_RECORD_MAX_BYTES:
                raise ValueError("process identity record exceeded its bound")
    finally:
        os.close(fd)


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("boot id must be text")
    parsed = uuid.UUID(value)
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("boot id must use canonical UUID form")
    return canonical
