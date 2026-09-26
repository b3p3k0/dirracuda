"""Filesystem, lock, verification and evidence-preservation helpers for C0B-2A.

The capability probe demonstrates process-crash behavior on the current mount.  It
deliberately makes no claim about power-loss durability.

DISPOSITION: benchmark-only diagnostic; remove after C0B artifacts are accepted.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

from .c0b2_checkpoint import (Checkpoint, CheckpointError, LockUnavailable,
                              _regular_owner_file, _secure_dir, canonical_json,
                              sha256_json)


@dataclass(frozen=True)
class Verification:
    ok: bool
    errors: tuple[str, ...] = ()
@dataclass(frozen=True)
class MountFingerprint:
    canonical_path: str
    mount_id: str
    mountpoint: str
    fs_type: str
    options: str
    st_dev: int
    kernel: str
    mergerfs_version: str
    sqlite_version: str
    sha256: str


@dataclass(frozen=True)
class ModeProbe:
    mode: str
    ok: bool
    checks: tuple[str, ...]
    errors: tuple[str, ...]
@dataclass(frozen=True)
class FilesystemProbe:
    fingerprint: MountFingerprint
    modes: tuple[ModeProbe, ...]
    selected_mode: Optional[str]
    power_loss_tested: bool = False
    capability_sha256: str = ""
class GlobalExecutionLock:
    """One nonblocking advisory lock shared by every run under this root."""

    def __init__(self, benchmark_root: Path):
        self.root = _secure_dir(Path(benchmark_root), create=True)
        self.path = self.root / "c0b2-execution.lock"
        self._fd: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> "GlobalExecutionLock":
        if self.held:
            return self
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise PermissionError(f"unsafe lock file: {self.path}")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LockUnavailable("another C0B-2 executor holds the global lock") from exc
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        return self
    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "GlobalExecutionLock":
        return self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()


def _unescape_mount(value: str) -> str:
    for escaped, plain in (("\\040", " "), ("\\011", "\t"),
                           ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, plain)
    return value
def _mount_row(path: Path, mountinfo: str) -> tuple[str, str, str, str]:
    candidates: list[tuple[int, str, str, str, str]] = []
    target = str(path)
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        mountpoint = _unescape_mount(fields[4])
        try:
            common = os.path.commonpath((target, mountpoint))
        except ValueError:
            continue
        if common == mountpoint:
            options = ",".join(sorted(set(fields[5].split(",") + tail[2].split(","))))
            candidates.append((len(mountpoint), fields[0], mountpoint, tail[0], options))
    if not candidates:
        raise CheckpointError(f"no mountinfo entry for {path}")
    _length, mount_id, mountpoint, fs_type, options = max(candidates)
    return mount_id, mountpoint, fs_type, options
def mount_fingerprint(path: Path, *, mountinfo: Optional[str] = None,
                      kernel: Optional[str] = None,
                      mergerfs_version: Optional[str] = None) -> MountFingerprint:
    canonical = Path(path).resolve(strict=True)
    info = mountinfo if mountinfo is not None else Path("/proc/self/mountinfo").read_text("utf-8")
    mount_id, mountpoint, fs_type, options = _mount_row(canonical, info)
    if mergerfs_version is None:
        exe = shutil.which("mergerfs")
        if exe:
            result = subprocess.run([exe, "-V"], capture_output=True, text=True,
                                    timeout=10, check=False)
            mergerfs_version = (result.stdout or result.stderr).strip()[:160]
        else:
            mergerfs_version = "unavailable"
    body = {
        "canonical_path": str(canonical), "mount_id": mount_id,
        "mountpoint": mountpoint, "fs_type": fs_type, "options": options,
        "st_dev": canonical.stat().st_dev, "kernel": kernel or platform.release(),
        "mergerfs_version": mergerfs_version, "sqlite_version": sqlite3.sqlite_version,
    }
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return MountFingerprint(**body, sha256=digest)
def revalidate_mount(expected: MountFingerprint, path: Path, **kwargs: Any) -> MountFingerprint:
    current = mount_fingerprint(path, **kwargs)
    if current.sha256 != expected.sha256:
        raise CheckpointError("mount fingerprint changed since capability probe")
    return current
def revalidate_filesystem(expected: MountFingerprint, path: Path,
                          selected_mode: str, capability_sha256: str) -> FilesystemProbe:
    """Recheck identity plus the selected mode before a live invocation."""
    result = probe_filesystem(path)
    if (result.fingerprint.sha256 != expected.sha256
            or result.capability_sha256 != capability_sha256
            or result.selected_mode != selected_mode.upper()):
        raise CheckpointError("filesystem capability changed since run creation")
    return result


def verify_connection(conn: sqlite3.Connection) -> Verification:
    errors: list[str] = []
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            errors.extend(str(row[0]) for row in rows)
        errors.extend(f"foreign-key:{row}" for row in
                      conn.execute("PRAGMA foreign_key_check").fetchall())
    except sqlite3.DatabaseError as exc:
        errors.append(type(exc).__name__)
    return Verification(not errors, tuple(errors))
def _readonly_uri(path: Path) -> str:
    # Opening a WAL database read-only may create -shm/-wal when its directory is
    # writable. Refuse live/hot sidecars so status/verify have zero write side effects.
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise CheckpointError("read-only inspection refused while SQLite sidecars exist")
    return f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
def verify_readonly(db_path: Path) -> Verification:
    path = Path(db_path)
    _regular_owner_file(path)
    try:
        conn = sqlite3.connect(_readonly_uri(path), uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        try:
            return verify_connection(conn)
        finally:
            conn.close()
    except (CheckpointError, sqlite3.DatabaseError) as exc:
        return Verification(False, (type(exc).__name__,))
def status_readonly(db_path: Path) -> dict[str, Any]:
    path = Path(db_path)
    _regular_owner_file(path)
    conn = sqlite3.connect(_readonly_uri(path), uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    try:
        state = conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0]
        total = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
        return {"state": state, "calls_total": total}
    finally:
        conn.close()
def backup_snapshot(checkpoint: Checkpoint, snapshot_root: Path,
                    *, lock: GlobalExecutionLock) -> Path:
    if not lock.held or lock.root != checkpoint.root:
        raise LockUnavailable("snapshot requires the matching global lock")
    requested = Path(snapshot_root)
    if os.path.commonpath((str(requested.resolve()), str(lock.root.resolve()))) != str(lock.root.resolve()):
        raise LockUnavailable("snapshot path is outside the held benchmark root")
    root = _secure_dir(requested, create=True)
    path = root / f"snapshot-{secrets.token_hex(12)}.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    dest = sqlite3.connect(path)
    try:
        checkpoint.conn.backup(dest)
        dest.commit()
        result = verify_connection(dest)
        if not result.ok:
            raise CheckpointError(f"snapshot verification failed: {result.errors}")
    except Exception:
        dest.close()
        path.unlink(missing_ok=True)
        raise
    dest.close()
    os.chmod(path, 0o600)
    Checkpoint._fsync_file_and_parent(path)
    return path
def restore_snapshot(snapshot: Path, benchmark_root: Path, *,
                     lock: GlobalExecutionLock) -> Path:
    source, root = Path(snapshot), Path(benchmark_root)
    if not lock.held or lock.root != root:
        raise LockUnavailable("snapshot restore requires the matching global lock")
    if not verify_readonly(source).ok:
        raise CheckpointError("snapshot failed read-only verification")
    conn = sqlite3.connect(_readonly_uri(source), uri=True, timeout=1.0)
    try:
        raw, digest = conn.execute(
            "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
        header = json.loads(raw)
        if sha256_json(header) != digest:
            raise CheckpointError("snapshot run header hash mismatch")
        stored_run_id = header["run_id"]
    finally:
        conn.close()
    if header["mount"]["canonical_path"] != str(root.resolve()):
        raise CheckpointError("restore must remain under the frozen canonical root")
    root = _secure_dir(root, create=True)
    runs = _secure_dir(root / "runs", create=True)
    logical_tag = hashlib.sha256(stored_run_id.encode("utf-8")).hexdigest()[:16]
    storage_id = f"restore-{logical_tag}-{secrets.token_hex(12)}"
    run_dir = runs / storage_id
    run_dir.mkdir(mode=0o700, exist_ok=False)
    _secure_dir(run_dir)
    dest = run_dir / "checkpoint.sqlite3"
    copied_sha256 = _copy_regular(source, dest)
    if not verify_readonly(dest).ok:
        raise CheckpointError("restored snapshot failed verification")
    origin = {
        "kind": "snapshot_restore_v1", "logical_run_id": stored_run_id,
        "storage_id": storage_id, "snapshot_sha256": copied_sha256,
        "header_sha256": digest,
    }
    restored = sqlite3.connect(dest, isolation_level=None, timeout=5.0)
    try:
        Checkpoint._configure(restored, header["journal_mode"])
        restored.execute("BEGIN IMMEDIATE")
        restored.execute(
            "INSERT INTO events(kind,detail_json,created) VALUES('RESTORE_ORIGIN',?,?)",
            (canonical_json(origin), time.time()))
        restored.commit()
        verified = verify_connection(restored)
        if not verified.ok:
            raise CheckpointError(f"restored checkpoint failed verification: {verified.errors}")
    except Exception:
        restored.rollback()
        raise
    finally:
        restored.close()
    record = canonical_json(origin).encode() + b"\n"
    fd = os.open(run_dir / "restore.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, record)
        os.fsync(fd)
    finally:
        os.close(fd)
    Checkpoint._fsync_file_and_parent(dest)
    return dest


def _copy_regular(source: Path, dest: Path) -> str:
    src = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    out: Optional[int] = None
    digest = hashlib.sha256()
    try:
        st = os.fstat(src)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError(f"unsafe SQLite member: {source}")
        out = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                      getattr(os, "O_NOFOLLOW", 0), 0o600)
        while True:
            block = os.read(src, 1 << 20)
            if not block:
                break
            digest.update(block)
            view = memoryview(block)
            while view:
                view = view[os.write(out, view):]
        os.fchmod(out, 0o600)
        os.fsync(out)
    finally:
        os.close(src)
        if out is not None:
            os.close(out)
    return digest.hexdigest()


def quarantine_corrupt(db_path: Path, quarantine_root: Path, *, reason: str,
                       lock: GlobalExecutionLock) -> Path:
    if not lock.held:
        raise LockUnavailable("quarantine requires the global lock")
    if not reason or len(reason) > 80 or not reason.replace("_", "").isalnum():
        raise ValueError("reason must be a bounded enum-like value")
    db, root = Path(db_path), Path(quarantine_root)
    if os.path.commonpath((str(db.resolve()), str(root.resolve()),
                           str(lock.root.resolve()))) != str(lock.root.resolve()):
        raise LockUnavailable("quarantine paths do not match the held benchmark root")
    root = _secure_dir(root, create=True)
    qdir = root / f"quarantine-{secrets.token_hex(12)}"
    qdir.mkdir(mode=0o700, exist_ok=False)
    copied: dict[str, str] = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        source = Path(str(db) + suffix)
        if source.exists():
            copied[source.name] = _copy_regular(source, qdir / source.name)
    record = canonical_json({"reason": reason, "members": copied}).encode() + b"\n"
    fd = os.open(qdir / "quarantine.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, record)
        os.fsync(fd)
    finally:
        os.close(fd)
    Checkpoint._fsync_file_and_parent(qdir / "quarantine.json")
    return qdir


_CRASH_CODE = """
import os, sqlite3, sys
c=sqlite3.connect(sys.argv[1], isolation_level=None, timeout=1)
c.execute('BEGIN IMMEDIATE')
c.execute("INSERT INTO probe(value) VALUES('uncommitted')")
os._exit(17)
"""
_COMMIT_CRASH_CODE = """
import os, sqlite3, sys
c=sqlite3.connect(sys.argv[1], isolation_level=None, timeout=1)
c.execute('BEGIN IMMEDIATE')
c.execute("INSERT INTO probe(value) VALUES('committed')")
c.commit()
os._exit(18)
"""
_SQL_LOCK_CODE = """
import sqlite3, sys
try:
 c=sqlite3.connect(sys.argv[1], isolation_level=None, timeout=0)
 c.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError:
 sys.exit(0)
sys.exit(2)
"""
_FLOCK_CODE = """
import fcntl, os, sys
f=os.open(sys.argv[1], os.O_RDWR)
try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(0)
sys.exit(2)
"""


def _mode_probe(directory: Path, mode: str) -> ModeProbe:
    checks: list[str] = []
    errors: list[str] = []
    db = directory / f"probe-{mode.lower()}.sqlite3"
    try:
        conn = sqlite3.connect(db, isolation_level=None, timeout=1)
        got = str(conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).upper()
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE probe(value TEXT NOT NULL)")
        conn.execute("INSERT INTO probe VALUES('baseline')")
        conn.close()
        if got != mode:
            raise CheckpointError(f"requested {mode}, got {got}")
        crash = subprocess.run([sys.executable, "-c", _CRASH_CODE, str(db)],
                               timeout=15, check=False)
        if crash.returncode != 17:
            raise CheckpointError(f"crash child returned {crash.returncode}")
        conn = sqlite3.connect(db, isolation_level=None, timeout=1)
        if conn.execute("SELECT value FROM probe").fetchall() != [("baseline",)]:
            raise CheckpointError("uncommitted row survived process crash")
        conn.close()
        committed = subprocess.run(
            [sys.executable, "-c", _COMMIT_CRASH_CODE, str(db)], timeout=15, check=False)
        if committed.returncode != 18:
            raise CheckpointError(f"commit-crash child returned {committed.returncode}")
        conn = sqlite3.connect(db, isolation_level=None, timeout=1)
        if conn.execute("SELECT value FROM probe ORDER BY rowid").fetchall() != [
                ("baseline",), ("committed",)]:
            raise CheckpointError("committed row missing after process crash")
        checks.append("process-crash-old-or-new")
        conn.execute("BEGIN IMMEDIATE")
        locked = subprocess.run([sys.executable, "-c", _SQL_LOCK_CODE, str(db)],
                                timeout=15, check=False)
        conn.rollback()
        if locked.returncode != 0:
            raise CheckpointError("SQLite two-process exclusion failed")
        checks.append("sqlite-exclusion")
        lock_path = directory / f"probe-{mode.lower()}.lock"
        lock_path.touch(mode=0o600, exist_ok=False)
        lock_fd = os.open(lock_path, os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = subprocess.run([sys.executable, "-c", _FLOCK_CODE, str(lock_path)],
                                 timeout=15, check=False)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        if blocked.returncode != 0:
            raise CheckpointError("outer flock exclusion failed")
        checks.append("flock-exclusion")
        conn.execute("INSERT INTO probe VALUES('resumed')")
        conn.close()
        reopened = sqlite3.connect(db)
        if reopened.execute("SELECT count(*) FROM probe").fetchone()[0] != 3:
            raise CheckpointError("resume commit missing")
        verified = verify_connection(reopened)
        reopened.close()
        if not verified.ok:
            raise CheckpointError(f"integrity failed: {verified.errors}")
        checks.extend(("resume", "integrity"))
    except Exception as exc:  # bounded diagnostic only; no source data exists here
        errors.append(f"{type(exc).__name__}:{str(exc)[:160]}")
    return ModeProbe(mode, not errors, tuple(checks), tuple(errors))


def _cleanup_probe(path: Path, parent: Path) -> None:
    if path.parent != parent or not path.name.startswith(".c0b2-fsprobe-"):
        raise CheckpointError("refusing unsafe probe cleanup")
    for entry in os.scandir(path):
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise CheckpointError(f"unexpected probe artifact {entry.name}")
        os.unlink(entry.path)
    path.rmdir()


def probe_filesystem(root: Path, modes: Iterable[str] = ("DELETE", "WAL")) -> FilesystemProbe:
    parent = _secure_dir(Path(root), create=True)
    fingerprint = mount_fingerprint(parent)
    probe_dir = Path(tempfile.mkdtemp(prefix=".c0b2-fsprobe-", dir=parent))
    os.chmod(probe_dir, 0o700)
    results: list[ModeProbe] = []
    try:
        for mode in modes:
            upper = mode.upper()
            if upper not in ("WAL", "DELETE"):
                raise ValueError(f"unsupported probe mode {mode}")
            results.append(_mode_probe(probe_dir, upper))
    finally:
        _cleanup_probe(probe_dir, parent)
    selected = next((r.mode for r in results if r.ok), None)
    body = {"fingerprint": asdict(fingerprint), "modes": [asdict(r) for r in results],
            "selected_mode": selected, "power_loss_tested": False}
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return FilesystemProbe(fingerprint, tuple(results), selected, False, digest)
