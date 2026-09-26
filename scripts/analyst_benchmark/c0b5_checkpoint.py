"""Strict SQLite storage boundary for the C0B-5 confirmation.

The checkpoint stores raw model responses only in an owner-only run directory. All
policy-sensitive public values use the closed C0B-5 artifact family and are validated on
write and replayed again by :mod:`c0b5_replay`.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .c0b2_checkpoint import _rename_noreplace, _secure_dir
from .c0b2_public_schema import sha256_json as _sha256_json
from .c0b2_schema import canonical_json
from .c0b5_policy import BENCHMARK_PROTOCOL_ID, POLICY_ID, POLICY_SHA256
from .c0b5_schema import validate_artifact

PROTOCOL_ID = BENCHMARK_PROTOCOL_ID
SCHEMA_VERSION = 1
HEADER_VERSION = "c0b5-run-header-v1"
RUN_ID_RE = re.compile(r"c0b5-[0-9]{8}-[0-9]{6}-[0-9a-f]{24}")
SHA_RE = re.compile(r"[0-9a-f]{64}")

LEDGER_LIMITS = {
    "scored": 228,
    "schema_retry": 4,
    "preflight_control": 33,
    "transport_orphan": 30,
}
CUMULATIVE_CAP = sum(LEDGER_LIMITS.values())
INVOCATION_CAPS = {"total": 10}
ATTEMPT_OUTCOMES = {
    "RAW_VALID", "NORMALIZED_DUPLICATE", "SCHEMA_INVALID",
    "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN", "CANCELLED",
    "CANCELLED_UNVERIFIED", "FAILED_SAFETY", "BLOCKED_PROVENANCE",
}
ACTIVE_STATES = {
    "PREPARED", "RUNNING", "PAUSED_SOFT_WALL", "PAUSED_RESOURCE",
    "PAUSED_PREFLIGHT", "PAUSED_STAGE_BOUNDARY", "CANCELLED_PENDING_RESUME",
}
TERMINAL_STATES = {
    "CONFIRMED", "INCONCLUSIVE", "FAILED_SAFETY", "BLOCKED_PROVENANCE",
    "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED",
}
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES

_SCHEMA = """
CREATE TABLE run_header(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE run_state(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE parent_files(id INTEGER PRIMARY KEY CHECK(id=1), c0b3_db_path TEXT NOT NULL, c0b3_snapshot_path TEXT NOT NULL, c0b4_db_path TEXT NOT NULL, c0b4_snapshot_path TEXT NOT NULL);
CREATE TABLE class_limits(call_class TEXT PRIMARY KEY, allowance INTEGER NOT NULL CHECK(allowance>=0));
CREATE TABLE invocations(ordinal INTEGER PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, call_class TEXT NOT NULL REFERENCES class_limits(call_class), invocation_ordinal INTEGER REFERENCES invocations(ordinal), request_sha256 TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE attempt_history(seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), state TEXT NOT NULL, payload_json TEXT, created REAL NOT NULL);
CREATE TABLE artifacts(kind TEXT NOT NULL, owner_id TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, json TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(kind,owner_id));
CREATE TABLE protected_values(name TEXT PRIMARY KEY, value BLOB NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, detail_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE backup_receipts(anchor_sha256 TEXT PRIMARY KEY, anchor_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL UNIQUE, receipt_json TEXT NOT NULL, created REAL NOT NULL);
"""


class C0B5CheckpointError(RuntimeError):
    """The checkpoint is absent, malformed, stale, or internally inconsistent."""


class C0B5BudgetError(C0B5CheckpointError):
    """A request would exceed its frozen class or cumulative allowance."""


def sha256_json(value: Mapping[str, Any], *, omit: str | None = None) -> str:
    """Hash canonical JSON, optionally excluding one self-digest field."""
    body = dict(value)
    if omit is not None:
        body.pop(omit, None)
    return _sha256_json(body)


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise C0B5CheckpointError(f"{label} must be lowercase SHA-256")
    return value


def _json(value: Mapping[str, Any]) -> str:
    return canonical_json(dict(value))


def _decode(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise C0B5CheckpointError(f"{label} is not canonical JSON") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise C0B5CheckpointError(f"{label} is not a canonical object")
    return value


def validate_header(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the strict C0B-5 header and storage constants."""
    try:
        normalized = validate_artifact(value)
    except (TypeError, ValueError) as exc:
        raise C0B5CheckpointError("run header violates the C0B-5 schema") from exc
    if (normalized.get("version") != HEADER_VERSION
            or normalized.get("benchmark_protocol_id") != PROTOCOL_ID
            or normalized.get("policy_id") != POLICY_ID
            or normalized.get("policy_sha256") != POLICY_SHA256
            or normalized.get("schema_version") != SCHEMA_VERSION
            or normalized.get("cumulative_cap") != CUMULATIVE_CAP
            or normalized.get("limits") != LEDGER_LIMITS
            or normalized.get("invocation_caps") != INVOCATION_CAPS
            or not RUN_ID_RE.fullmatch(str(normalized.get("run_id", "")))):
        raise C0B5CheckpointError("run header identity or limits changed")
    return normalized


def _owner_file(path: Path, *, write: bool = False) -> int:
    flags = (os.O_RDWR if write else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600):
        os.close(fd)
        raise PermissionError("checkpoint must be an owner-only regular file")
    return fd


def _assert_named(path: Path, fd: int) -> None:
    named, pinned = os.stat(path, follow_symlinks=False), os.fstat(fd)
    if (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise C0B5CheckpointError("pinned file path changed during verification")


def _connect_fd(fd: int, *, readonly: bool) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(
        f"file:/proc/self/fd/{fd}?mode={mode}", uri=True, timeout=5.0)
    conn.execute("PRAGMA foreign_keys=ON")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while block := os.read(fd, 1024 * 1024):
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _utc_timestamp(value: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def validate_run_lineage(conn: sqlite3.Connection,
                         header: Mapping[str, Any] | None = None) -> None:
    """Recheck canonical storage shape before semantic replay."""
    row = conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
    if not row:
        raise C0B5CheckpointError("run header is absent")
    stored = validate_header(_decode(row[0], "run header"))
    if sha256_json(stored) != row[1] or header is not None and stored != dict(header):
        raise C0B5CheckpointError("run header digest or caller binding changed")
    state_rows = conn.execute("SELECT state FROM run_state").fetchall()
    if len(state_rows) != 1 or state_rows[0][0] not in ALL_STATES:
        raise C0B5CheckpointError("run state is invalid")
    limits = dict(conn.execute("SELECT call_class,allowance FROM class_limits"))
    if limits != LEDGER_LIMITS:
        raise C0B5CheckpointError("class limits changed")
    invocations = [row[0] for row in conn.execute(
        "SELECT ordinal FROM invocations ORDER BY ordinal")]
    if (len(invocations) > INVOCATION_CAPS["total"]
            or invocations != list(range(1, len(invocations) + 1))):
        raise C0B5CheckpointError("invocation ledger changed")
    attempt_rows = conn.execute(
        "SELECT attempt_id,call_class,invocation_ordinal,request_sha256,state,"
        "payload_json FROM attempts ORDER BY rowid").fetchall()
    if len(attempt_rows) > CUMULATIVE_CAP:
        raise C0B5CheckpointError("cumulative call allowance changed")
    for call_class, allowance in LEDGER_LIMITS.items():
        if sum(row[1] == call_class for row in attempt_rows) > allowance:
            raise C0B5CheckpointError("class call allowance changed")
    for attempt_id, call_class, ordinal, request_sha, outcome, payload in attempt_rows:
        _sha(attempt_id, "attempt_id")
        _sha(request_sha, "request_sha256")
        if (call_class not in LEDGER_LIMITS or ordinal not in invocations
                or outcome not in ATTEMPT_OUTCOMES | {"DISPATCHING"}):
            raise C0B5CheckpointError("attempt ledger contains an invalid row")
        if payload is not None:
            _decode(payload, "attempt payload")
        history = conn.execute(
            "SELECT state,payload_json FROM attempt_history WHERE attempt_id=? "
            "ORDER BY seq", (attempt_id,)).fetchall()
        if not history or history[0] != ("DISPATCHING", None) \
                or history[-1] != (outcome, payload):
            raise C0B5CheckpointError("attempt history differs from current row")
    if sum(row[4] == "DISPATCHING" for row in attempt_rows) > 1:
        raise C0B5CheckpointError("more than one request is in flight")
    for kind, owner, digest, raw in conn.execute(
            "SELECT kind,owner_id,sha256,json FROM artifacts"):
        value = _decode(raw, f"{kind}:{owner}")
        try:
            normalized = validate_artifact(value)
        except (TypeError, ValueError) as exc:
            raise C0B5CheckpointError("stored artifact violates C0B-5 schema") from exc
        if (normalized != value or sha256_json(value) != digest
                or any(value.get(key) != stored[key] for key in (
                    "policy_id", "policy_sha256", "protocol_sha256"))):
            raise C0B5CheckpointError("stored artifact digest changed")
    event_rows = conn.execute(
        "SELECT kind,detail_json FROM events ORDER BY seq").fetchall()
    seen_events: set[tuple[str, str]] = set()
    for kind, raw in event_rows:
        try:
            event = validate_artifact(_decode(raw, "runtime event"))
        except (TypeError, ValueError) as exc:
            raise C0B5CheckpointError("runtime event violates C0B-5 schema") from exc
        key = event.get("event"), event.get("source_attempt_id")
        if (event.get("version") != "c0b5-runtime-event-v1"
                or kind != event.get("event") or key in seen_events
                or any(event.get(name) != stored[name] for name in (
                    "policy_id", "policy_sha256", "protocol_sha256"))):
            raise C0B5CheckpointError("runtime event identity changed")
        seen_events.add(key)
    parent_rows = conn.execute("SELECT * FROM parent_files").fetchall()
    if len(parent_rows) != 1 or len(parent_rows[0]) != 5:
        raise C0B5CheckpointError("parent path census changed")
    for anchor_raw, anchor_sha, receipt_raw, receipt_sha in conn.execute(
            "SELECT anchor_json,anchor_sha256,receipt_json,receipt_sha256 "
            "FROM backup_receipts"):
        anchor = validate_artifact(_decode(anchor_raw, "backup anchor"))
        receipt = validate_artifact(_decode(receipt_raw, "backup receipt"))
        if (anchor.get("anchor_sha256") != anchor_sha
                or receipt.get("receipt_sha256") != receipt_sha
                or receipt.get("anchor_sha256") != anchor_sha):
            raise C0B5CheckpointError("backup receipt chain changed")
    if state_rows[0][0] in TERMINAL_STATES and any(
            row[4] == "DISPATCHING" for row in attempt_rows):
        raise C0B5CheckpointError("terminal state owns an in-flight attempt")


def status_readonly(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    state: str | None = None
    charged = 0
    fd = -1
    try:
        fd = _owner_file(Path(path))
        conn = _connect_fd(fd, readonly=True)
        try:
            validate_run_lineage(conn)
            state = conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0]
            charged = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        errors.append(type(exc).__name__)
    finally:
        if fd >= 0:
            os.close(fd)
    return {"ok": not errors, "state": state, "charged_calls": charged,
            "errors": errors}


verify_readonly = status_readonly


class C0B5Checkpoint:
    """One validated writable C0B-5 checkpoint handle."""

    def __init__(self, path: Path, fd: int, conn: sqlite3.Connection):
        self.path, self._fd, self.conn = Path(path), fd, conn
        self.root = self.path.parents[2]

    @classmethod
    def create(
            cls, root: Path, run_id: str, *, header: Mapping[str, Any],
            parent_paths: tuple[Path, Path, Path, Path],
            initializer: Callable[["C0B5Checkpoint"], None] | None = None,
    ) -> "C0B5Checkpoint":
        if not RUN_ID_RE.fullmatch(run_id) or header.get("run_id") != run_id:
            raise ValueError("invalid C0B-5 run identity")
        normalized = validate_header(header)
        root = _secure_dir(Path(root), create=True)
        runs = _secure_dir(root / "runs", create=True)
        staging_name = f".c0b5-initializing-{run_id}-{os.urandom(16).hex()}"
        staging, final = runs / staging_name, runs / run_id
        staging.mkdir(mode=0o700, exist_ok=False)
        path = staging / "checkpoint.sqlite3"
        conn: sqlite3.Connection | None = None
        point: C0B5Checkpoint | None = None
        published = False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            conn = sqlite3.connect(path)
            os.chmod(path, 0o600)
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            raw = _json(normalized)
            conn.execute("INSERT INTO run_header VALUES(1,?,?)",
                         (raw, sha256_json(normalized)))
            conn.execute("INSERT INTO run_state VALUES(1,'PREPARED',?)", (time.time(),))
            conn.execute("INSERT INTO parent_files VALUES(1,?,?,?,?)",
                         tuple(str(Path(item)) for item in parent_paths))
            conn.executemany("INSERT INTO class_limits VALUES(?,?)",
                             LEDGER_LIMITS.items())
            conn.commit()
            conn.close()
            conn = None
            staged_fd = _owner_file(path, write=True)
            try:
                staged_conn = _connect_fd(staged_fd, readonly=False)
                validate_run_lineage(staged_conn, normalized)
            except Exception:
                os.close(staged_fd)
                raise
            point = cls(path, staged_fd, staged_conn)
            if initializer is not None:
                initializer(point)
            validate_run_lineage(point.conn, normalized)
            point.close()
            point = None
            db_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(db_fd)
            finally:
                os.close(db_fd)
            runs_fd = os.open(runs, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                _rename_noreplace(runs_fd, staging_name, run_id)
                published = True
                os.fsync(runs_fd)
            finally:
                os.close(runs_fd)
            return cls.open(final / "checkpoint.sqlite3", writable=True)
        except BaseException:
            if point is not None:
                point.close()
            elif conn is not None:
                conn.close()
            target = final if published else staging
            try:
                (target / "checkpoint.sqlite3").unlink()
                target.rmdir()
            except OSError:
                pass
            raise

    @classmethod
    def open(cls, path: Path, *, writable: bool = False) -> "C0B5Checkpoint":
        path = Path(path)
        if (path.name != "checkpoint.sqlite3" or path.parent.parent.name != "runs"
                or not RUN_ID_RE.fullmatch(path.parent.name)):
            raise PermissionError("checkpoint is outside a C0B-5 run directory")
        for directory in (path.parent.parent.parent, path.parent.parent, path.parent):
            st = directory.lstat()
            if (not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o700):
                raise PermissionError("checkpoint directory is not owner-only")
        fd = _owner_file(path, write=writable)
        try:
            conn = _connect_fd(fd, readonly=not writable)
            validate_run_lineage(conn)
            header = validate_header(_decode(
                conn.execute("SELECT json FROM run_header WHERE id=1").fetchone()[0],
                "run header"))
            if header["run_id"] != path.parent.name:
                raise C0B5CheckpointError("storage/run identity mismatch")
            _assert_named(path, fd)
        except Exception:
            os.close(fd)
            raise
        return cls(Path(path), fd, conn)

    def close(self) -> None:
        self.conn.close()
        os.close(self._fd)

    def __enter__(self) -> "C0B5Checkpoint":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def header(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
        value = validate_header(_decode(row[0], "run header"))
        if sha256_json(value) != row[1]:
            raise C0B5CheckpointError("run header digest changed")
        return value

    def state(self) -> str:
        return str(self.conn.execute(
            "SELECT state FROM run_state WHERE id=1").fetchone()[0])

    def _assert_parents_unchanged(self) -> None:
        """Recheck all four pinned parent files before every mutation."""
        binding = self.header()["parent_binding"]
        expected = (
            binding["execution_parent"]["checkpoint_sha256"],
            binding["execution_parent"]["backup_snapshot_sha256"],
            binding["observed_c0b4"]["checkpoint_sha256"],
            binding["observed_c0b4"]["backup_snapshot_sha256"],
        )
        for path, digest in zip(self.parent_paths(), expected, strict=True):
            fd = _owner_file(path)
            try:
                if _hash_fd(fd) != digest:
                    raise C0B5CheckpointError("immutable parent evidence changed")
                _assert_named(path, fd)
            finally:
                os.close(fd)

    def set_state(self, state: str) -> None:
        if state not in ALL_STATES:
            raise ValueError("unknown C0B-5 state")
        old = self.state()
        if old in TERMINAL_STATES:
            raise C0B5CheckpointError("terminal state is immutable")
        allowed = ({"RUNNING"} if old == "PREPARED"
                   else ACTIVE_STATES - {"PREPARED"})
        if state != old and state not in allowed:
            raise C0B5CheckpointError(f"illegal state transition {old} -> {state}")
        if state in TERMINAL_STATES and self.conn.execute(
                "SELECT 1 FROM attempts WHERE state='DISPATCHING'").fetchone():
            raise C0B5CheckpointError("terminal transition owns an in-flight attempt")
        self._assert_parents_unchanged()
        with self.conn:
            self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                              (state, time.time()))

    transition = set_state

    def begin_invocation(self) -> int:
        self._assert_parents_unchanged()
        if self.state() != "RUNNING":
            raise C0B5CheckpointError("invocation claim requires RUNNING")
        current = self.conn.execute("SELECT count(*) FROM invocations").fetchone()[0]
        if current >= INVOCATION_CAPS["total"]:
            raise C0B5BudgetError("invocation allowance exhausted")
        ordinal = current + 1
        with self.conn:
            self.conn.execute("INSERT INTO invocations VALUES(?,?)",
                              (ordinal, time.time()))
        return ordinal

    claim_invocation = begin_invocation

    def store_artifact(self, kind: str, owner_id: str,
                       value: Mapping[str, Any]) -> str:
        self._assert_parents_unchanged()
        try:
            normalized = validate_artifact(value)
        except (TypeError, ValueError) as exc:
            raise C0B5CheckpointError("artifact violates C0B-5 schema") from exc
        raw, digest = _json(normalized), sha256_json(normalized)
        with self.conn:
            prior = self.conn.execute(
                "SELECT sha256,json FROM artifacts WHERE kind=? AND owner_id=?",
                (kind, owner_id)).fetchone()
            if prior and prior != (digest, raw):
                raise C0B5CheckpointError("artifact owner is immutable")
            if not prior:
                self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                                  (kind, owner_id, digest, raw, time.time()))
        return digest

    def read_artifact(self, kind: str, owner_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT json,sha256 FROM artifacts WHERE kind=? AND owner_id=?",
            (kind, owner_id)).fetchone()
        if not row:
            return None
        value = validate_artifact(_decode(row[0], f"{kind}:{owner_id}"))
        if sha256_json(value) != row[1]:
            raise C0B5CheckpointError("artifact digest changed")
        return value

    def set_nonce_key(self, value: bytes) -> None:
        if type(value) is not bytes or len(value) != 32:
            raise ValueError("nonce key must contain exactly 32 bytes")
        self._assert_parents_unchanged()
        if self.state() != "PREPARED":
            raise C0B5CheckpointError("nonce key may only be frozen before execution")
        digest = hashlib.sha256(value).hexdigest()
        with self.conn:
            self.conn.execute(
                "INSERT INTO protected_values VALUES('run_nonce_key',?,?)",
                (value, digest))

    def nonce_key(self) -> bytes:
        row = self.conn.execute(
            "SELECT value,sha256 FROM protected_values WHERE name='run_nonce_key'").fetchone()
        if not row or hashlib.sha256(row[0]).hexdigest() != row[1] or len(row[0]) != 32:
            raise C0B5CheckpointError("protected nonce key changed")
        return bytes(row[0])

    read_nonce_key = nonce_key

    def precharge(self, *, attempt_id: str, owner_id: str, call_class: str,
                  invocation_ordinal: int, request_sha256: str) -> None:
        self._assert_parents_unchanged()
        if self.state() != "RUNNING":
            raise C0B5CheckpointError("precharge requires RUNNING")
        _sha(attempt_id, "attempt_id")
        _sha(request_sha256, "request_sha256")
        if call_class not in LEDGER_LIMITS:
            raise ValueError("unknown call class")
        if self.conn.execute(
                "SELECT 1 FROM attempts WHERE state='DISPATCHING'").fetchone():
            raise C0B5CheckpointError("another request is already in flight")
        if not self.conn.execute(
                "SELECT 1 FROM invocations WHERE ordinal=?",
                (invocation_ordinal,)).fetchone():
            raise C0B5CheckpointError("attempt names an unknown invocation")
        class_count = self.conn.execute(
            "SELECT count(*) FROM attempts WHERE call_class=?",
            (call_class,)).fetchone()[0]
        total = self.conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
        if class_count >= LEDGER_LIMITS[call_class] or total >= CUMULATIVE_CAP:
            raise C0B5BudgetError("call allowance exhausted")
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,'DISPATCHING',NULL,?,?)",
                (attempt_id, owner_id, call_class, invocation_ordinal,
                 request_sha256, now, now))
            self.conn.execute(
                "INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,?)",
                (attempt_id, now))

    def record_attempt(self, attempt_id: str, state: str,
                       payload: Mapping[str, Any]) -> None:
        self._assert_parents_unchanged()
        if state not in ATTEMPT_OUTCOMES:
            raise ValueError("unknown attempt outcome")
        raw, now = _json(payload), time.time()
        with self.conn:
            changed = self.conn.execute(
                "UPDATE attempts SET state=?,payload_json=?,updated=? "
                "WHERE attempt_id=? AND state='DISPATCHING'",
                (state, raw, now, attempt_id)).rowcount
            if changed != 1:
                raise C0B5CheckpointError("attempt is absent or already final")
            self.conn.execute(
                "INSERT INTO attempt_history VALUES(NULL,?,?,?,?)",
                (attempt_id, state, raw, now))

    def record_cancelled_attempt(
            self, attempt_id: str, *, first_byte_seen: bool,
            cancel_elapsed_ms: int) -> str:
        """Finish cancellation and freeze the health deadline atomically."""
        if (type(first_byte_seen) is not bool or type(cancel_elapsed_ms) is not int
                or cancel_elapsed_ms < 0):
            raise C0B5CheckpointError("cancellation timing values are invalid")
        self._assert_parents_unchanged()
        now = time.time()
        not_before = _utc_timestamp(now + 2.0)
        raw = _json({
            "answered": False, "first_byte_seen": first_byte_seen,
            "cancel_elapsed_ms": cancel_elapsed_ms,
            "health_not_before_utc": not_before,
        })
        with self.conn:
            changed = self.conn.execute(
                "UPDATE attempts SET state='CANCELLED_UNVERIFIED',"
                "payload_json=?,updated=? WHERE attempt_id=? AND state='DISPATCHING'",
                (raw, now, attempt_id)).rowcount
            if changed != 1:
                raise C0B5CheckpointError("attempt is absent or already final")
            self.conn.execute(
                "INSERT INTO attempt_history VALUES(NULL,?,'CANCELLED_UNVERIFIED',?,?)",
                (attempt_id, raw, now))
        return not_before

    def recover_dispatching(self) -> list[dict[str, Any]]:
        """Charge crash-left requests once and classify them as orphaned."""
        self._assert_parents_unchanged()
        ids = [row[0] for row in self.conn.execute(
            "SELECT attempt_id FROM attempts WHERE state='DISPATCHING' ORDER BY rowid")]
        now = time.time()
        with self.conn:
            for attempt_id in ids:
                self.conn.execute(
                    "UPDATE attempts SET state='ORPHANED_UNKNOWN',updated=? "
                    "WHERE attempt_id=?", (now, attempt_id))
                self.conn.execute(
                    "INSERT INTO attempt_history VALUES(NULL,?,'ORPHANED_UNKNOWN',NULL,?)",
                    (attempt_id, now))
        return [row for row in self.list_attempts() if row["attempt_id"] in ids]

    def list_attempts(self) -> list[dict[str, Any]]:
        keys = ("attempt_id", "owner_id", "call_class", "invocation_ordinal",
                "request_sha256", "state", "payload_json", "created", "updated")
        rows = []
        for raw in self.conn.execute(
                "SELECT attempt_id,owner_id,call_class,invocation_ordinal,"
                "request_sha256,state,payload_json,created,updated FROM attempts "
                "ORDER BY rowid"):
            row = dict(zip(keys, raw, strict=True))
            payload = row.pop("payload_json")
            row["payload"] = (_decode(payload, "attempt payload")
                              if payload is not None else None)
            rows.append(row)
        return rows

    def store_runtime_event(self, value: Mapping[str, Any]) -> str:
        normalized = validate_artifact(value)
        if normalized.get("version") != "c0b5-runtime-event-v1":
            raise C0B5CheckpointError("runtime event has the wrong artifact family")
        self._assert_parents_unchanged()
        raw = _json(normalized)
        with self.conn:
            self.conn.execute("INSERT INTO events VALUES(NULL,?,?,?)",
                              (normalized["event"], raw, time.time()))
        return str(normalized["event_sha256"])

    def list_runtime_events(self) -> list[dict[str, Any]]:
        return [validate_artifact(_decode(row[1], "runtime event"))
                for row in self.conn.execute(
                    "SELECT kind,detail_json FROM events ORDER BY seq")]

    def store_backup_receipt(self, anchor: Mapping[str, Any],
                             receipt: Mapping[str, Any]) -> None:
        self._assert_parents_unchanged()
        if self.state() not in TERMINAL_STATES:
            raise C0B5CheckpointError("backup receipt requires a terminal state")
        normalized_anchor = validate_artifact(anchor)
        normalized_receipt = validate_artifact(receipt)
        if (normalized_anchor.get("version") != "c0b5-backup-anchor-v1"
                or normalized_receipt.get("version") != "c0b5-backup-receipt-v1"
                or normalized_receipt["anchor_sha256"] !=
                   normalized_anchor["anchor_sha256"]):
            raise C0B5CheckpointError("backup receipt chain is invalid")
        anchor_raw, receipt_raw = _json(normalized_anchor), _json(normalized_receipt)
        with self.conn:
            self.conn.execute(
                "INSERT INTO backup_receipts VALUES(?,?,?,?,?)",
                (normalized_anchor["anchor_sha256"], anchor_raw,
                 normalized_receipt["receipt_sha256"], receipt_raw, time.time()))

    def parent_paths(self) -> tuple[Path, Path, Path, Path]:
        row = self.conn.execute(
            "SELECT c0b3_db_path,c0b3_snapshot_path,c0b4_db_path,c0b4_snapshot_path "
            "FROM parent_files WHERE id=1").fetchone()
        if not row:
            raise C0B5CheckpointError("parent paths are absent")
        return tuple(Path(item) for item in row)  # type: ignore[return-value]

    def finalize(self, terminal: str, terminal_artifact: Mapping[str, Any], *,
                 completion: Mapping[str, Any] | None = None,
                 ) -> tuple[str, str | None]:
        """Atomically persist exact terminal ownership and state."""
        if terminal not in TERMINAL_STATES or self.state() not in ACTIVE_STATES:
            raise C0B5CheckpointError("invalid terminal transition")
        quality = terminal in {"CONFIRMED", "INCONCLUSIVE"}
        if quality != (completion is not None):
            raise C0B5CheckpointError("quality completion ownership differs")
        artifact = validate_artifact(terminal_artifact)
        header = self.header()
        if (artifact.get("terminal") != terminal or any(
                artifact.get(key) != header[key] for key in (
                    "policy_id", "policy_sha256", "protocol_sha256"))):
            raise C0B5CheckpointError("terminal artifact differs from run lineage")
        artifact_raw, artifact_hash = _json(artifact), sha256_json(artifact)
        normalized = validate_artifact(completion) if completion is not None else None
        completion_hash = sha256_json(normalized) if normalized is not None else None
        if normalized is not None and (
                normalized["outcome"] != terminal
                or normalized["artifact_sha256"] != artifact_hash):
            raise C0B5CheckpointError("completion does not own terminal result")
        self._assert_parents_unchanged()
        if self.conn.execute(
                "SELECT 1 FROM attempts WHERE state='DISPATCHING'").fetchone():
            raise C0B5CheckpointError("terminal transition owns an in-flight attempt")
        with self.conn:
            self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)", (
                "result" if quality else "failure", "terminal", artifact_hash,
                artifact_raw, time.time()))
            if normalized is not None:
                self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)", (
                    "completion", "terminal", completion_hash, _json(normalized),
                    time.time()))
            self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                              (terminal, time.time()))
        return artifact_hash, completion_hash
