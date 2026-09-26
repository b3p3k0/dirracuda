"""Non-circular terminal backup receipts for C0B-4 checkpoints.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .c0b2_fsprobe import GlobalExecutionLock
from .c0b2_runtime import _write_backup_snapshot
from .c0b4_checkpoint import (
    C0B4Checkpoint, C0B4CheckpointError, POLICY_ID, POLICY_SHA256,
    canonical_json, sha256_json, validate_failure_terminal_ownership,
    validate_header, validate_quality_terminal_ownership, validate_run_lineage,
)
from .c0b4_schema import validate_artifact

ANCHOR_VERSION = "c0b4-backup-anchor-v1"
RECEIPT_VERSION = "c0b4-backup-receipt-v1"
SHA_RE = re.compile(r"[0-9a-f]{64}")
COMMON_KEYS = frozenset({"policy_id", "policy_sha256", "protocol_sha256"})
SOURCE_KEYS = frozenset({
    "git_head", "declared_dirty_state_sha256", "task_tree_sha256", "protocol_sha256",
    "policy_sha256", "prompt_sha256", "schema_sha256", "fixture_sha256",
    "master_manifest_sha256", "chunker_sha256", "detector_sha256",
    "generation_options_sha256", "worktree_seal_sha256",
    "filesystem_capability_sha256", "model_digests",
})
ANCHOR_KEYS = COMMON_KEYS | frozenset({
    "version", "run_id", "header_sha256", "terminal_artifact_sha256",
    "completion_sha256", "parent_binding", "source_binding", "anchor_sha256",
})
RECEIPT_KEYS = COMMON_KEYS | frozenset({
    "version", "anchor_sha256", "snapshot_run_relative_path", "snapshot_sha256",
    "snapshot_size_bytes", "integrity_check", "foreign_key_violations",
    "created_at_utc", "receipt_sha256",
})


class C0B4BackupError(C0B4CheckpointError):
    """A C0B-4 terminal snapshot or receipt is invalid."""


def source_binding_from_header(header: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_header(header)
    source = {key: value[key] for key in SOURCE_KEYS}
    if frozenset(source) != SOURCE_KEYS:
        raise C0B4BackupError("C0B-4 source binding is incomplete")
    return source


def _common(header: Mapping[str, Any]) -> dict[str, str]:
    return {
        "policy_id": str(header["policy_id"]),
        "policy_sha256": str(header["policy_sha256"]),
        "protocol_sha256": str(header["protocol_sha256"]),
    }


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise C0B4BackupError(f"{label} must be a lowercase SHA-256")
    return value


def validate_anchor(value: Mapping[str, Any], header: Mapping[str, Any]) -> dict[str, Any]:
    try:
        anchor = validate_artifact(value)
    except (TypeError, ValueError) as exc:
        raise C0B4BackupError("backup anchor violates its strict schema") from exc
    if frozenset(anchor) != ANCHOR_KEYS or anchor.get("version") != ANCHOR_VERSION:
        raise C0B4BackupError("backup anchor has an inexact schema or version")
    if any(anchor.get(key) != expected for key, expected in _common(header).items()):
        raise C0B4BackupError("backup anchor policy/protocol identity changed")
    for key in ("header_sha256", "terminal_artifact_sha256", "anchor_sha256"):
        _sha(anchor.get(key), key)
    if anchor.get("completion_sha256") is not None:
        _sha(anchor.get("completion_sha256"), "completion_sha256")
    if anchor.get("run_id") != header["run_id"]:
        raise C0B4BackupError("backup anchor run id changed")
    if anchor.get("parent_binding") != header["parent_binding"]:
        raise C0B4BackupError("backup anchor parent binding changed")
    if anchor.get("source_binding") != source_binding_from_header(header):
        raise C0B4BackupError("backup anchor source binding changed")
    if sha256_json(anchor, omit="anchor_sha256") != anchor["anchor_sha256"]:
        raise C0B4BackupError("backup anchor self-digest changed")
    return anchor


def validate_receipt(value: Mapping[str, Any], header: Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt = validate_artifact(value)
    except (TypeError, ValueError) as exc:
        raise C0B4BackupError("backup receipt violates its strict schema") from exc
    if frozenset(receipt) != RECEIPT_KEYS or receipt.get("version") != RECEIPT_VERSION:
        raise C0B4BackupError("backup receipt has an inexact schema or version")
    if any(receipt.get(key) != expected for key, expected in _common(header).items()):
        raise C0B4BackupError("backup receipt policy/protocol identity changed")
    for key in ("anchor_sha256", "snapshot_sha256", "receipt_sha256"):
        _sha(receipt.get(key), key)
    if receipt.get("integrity_check") != "ok" or receipt.get("foreign_key_violations") != 0:
        raise C0B4BackupError("backup receipt SQLite facts changed")
    if (isinstance(receipt.get("snapshot_size_bytes"), bool)
            or not isinstance(receipt.get("snapshot_size_bytes"), int)
            or receipt["snapshot_size_bytes"] <= 0):
        raise C0B4BackupError("backup receipt size is invalid")
    relative = PurePosixPath(str(receipt.get("snapshot_run_relative_path", "")))
    if (relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "backups"
            or any(part in ("", ".", "..") for part in relative.parts)
            or not relative.parts[1].startswith(f"snapshot-{receipt['anchor_sha256']}-")
            or not relative.parts[1].endswith(".sqlite3")):
        raise C0B4BackupError("backup receipt path is not canonical owner-relative")
    if sha256_json(receipt, omit="receipt_sha256") != receipt["receipt_sha256"]:
        raise C0B4BackupError("backup receipt self-digest changed")
    return receipt


def _hash_fd(fd: int) -> str:
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, 1 << 20, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _open_dir(path: Path | str, *, dir_fd: int | None = None) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    st = os.fstat(fd)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700):
        os.close(fd)
        raise PermissionError("backup directory must be owner-only mode 0700")
    return fd


def _verify_sqlite_fd(fd: int) -> tuple[str, int]:
    conn = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",
                           uri=True, timeout=1.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    if integrity != "ok" or foreign_keys:
        raise C0B4BackupError("backup snapshot failed SQLite verification")
    return integrity, foreign_keys


def _verify_terminal_lineage(conn: sqlite3.Connection, state: str,
                             anchor: Mapping[str, Any]) -> None:
    header_row = conn.execute(
        "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
    header = validate_header(json.loads(header_row[0]))
    if (canonical_json(header) != header_row[0]
            or sha256_json(header) != header_row[1]):
        raise C0B4BackupError("terminal snapshot header changed")
    try:
        validate_run_lineage(conn, header)
    except C0B4CheckpointError as exc:
        raise C0B4BackupError("terminal snapshot run lineage changed") from exc
    if conn.execute(
            "SELECT 1 FROM attempts WHERE state='DISPATCHING' LIMIT 1").fetchone():
        raise C0B4BackupError("terminal snapshot has an in-flight attempt")
    quality = state in {"CONFIRMED", "INCONCLUSIVE"}
    kind = "result" if quality else "failure"
    row = conn.execute(
        "SELECT json,sha256 FROM artifacts WHERE kind=? AND owner_id='terminal'",
        (kind,)).fetchone()
    if not row or row[1] != anchor["terminal_artifact_sha256"]:
        raise C0B4BackupError("terminal artifact lineage changed")
    artifact = validate_artifact(json.loads(row[0]))
    if (canonical_json(artifact) != row[0] or sha256_json(artifact) != row[1]
            or artifact["terminal"] != state):
        raise C0B4BackupError("terminal artifact is noncanonical or mismatched")
    completions = conn.execute(
        "SELECT json,sha256 FROM artifacts WHERE kind='completion'").fetchall()
    if quality:
        if len(completions) != 1 or completions[0][1] != anchor["completion_sha256"]:
            raise C0B4BackupError("quality completion lineage changed")
        completion = validate_artifact(json.loads(completions[0][0]))
        if (canonical_json(completion) != completions[0][0]
                or sha256_json(completion) != completions[0][1]
                or completion["artifact_sha256"] != row[1]
                or completion["outcome"] != state):
            raise C0B4BackupError("quality completion is noncanonical or mismatched")
        expected_facts = ({"confirmed": True} if state == "CONFIRMED" else
                          {"deterministic_stop": True,
                           "reason": artifact["reason"]})
        if completion["facts"] != expected_facts:
            raise C0B4BackupError("quality completion facts differ from owned result")
    elif completions or anchor["completion_sha256"] is not None:
        raise C0B4BackupError("failure terminal has a quality completion")
    if quality:
        try:
            validate_quality_terminal_ownership(conn, artifact)
        except C0B4CheckpointError as exc:
            raise C0B4BackupError("quality terminal ownership changed") from exc
        references = []
    else:
        references = [("failure_evidence", "terminal", artifact["evidence_sha256"])]
    for reference_kind, owner_id, digest in references:
        if digest is not None and not conn.execute(
                "SELECT 1 FROM artifacts WHERE kind=? AND owner_id=? AND sha256=?",
                (reference_kind, owner_id, digest)).fetchone():
            raise C0B4BackupError("terminal artifact references absent or misowned evidence")
    if not quality:
        try:
            validate_failure_terminal_ownership(conn, artifact)
        except C0B4CheckpointError as exc:
            raise C0B4BackupError("failure result/evidence ownership changed") from exc


def build_anchor(point: C0B4Checkpoint, *, terminal_artifact_sha256: str,
                 completion_sha256: str | None) -> dict[str, Any]:
    header = point.header()
    state = point.state()
    quality = state in {"CONFIRMED", "INCONCLUSIVE"}
    terminal_kind = "result" if quality else "failure"
    terminal = point.conn.execute(
        "SELECT 1 FROM artifacts WHERE sha256=? AND kind=?",
        (_sha(terminal_artifact_sha256, "terminal_artifact_sha256"), terminal_kind)).fetchone()
    if quality:
        if completion_sha256 is None:
            raise C0B4BackupError("quality terminal requires a completion artifact")
        completion = point.conn.execute(
            "SELECT 1 FROM artifacts WHERE sha256=? AND kind='completion'",
            (_sha(completion_sha256, "completion_sha256"),)).fetchone()
    else:
        completion = completion_sha256 is None and not point.conn.execute(
            "SELECT 1 FROM artifacts WHERE kind='completion' LIMIT 1").fetchone()
    if not terminal or not completion:
        raise C0B4BackupError("anchor terminal/completion ownership differs from its state")
    raw, header_sha = point.conn.execute(
        "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
    if sha256_json(json.loads(raw)) != header_sha:
        raise C0B4BackupError("run header changed before backup")
    anchor: dict[str, Any] = {
        "version": ANCHOR_VERSION, **_common(header), "run_id": header["run_id"],
        "header_sha256": header_sha,
        "terminal_artifact_sha256": terminal_artifact_sha256,
        "completion_sha256": completion_sha256,
        "parent_binding": header["parent_binding"],
        "source_binding": source_binding_from_header(header),
    }
    anchor["anchor_sha256"] = sha256_json(anchor)
    normalized = validate_anchor(anchor, header)
    _verify_terminal_lineage(point.conn, state, normalized)
    return normalized


def _snapshot_path(run_dir: Path, receipt: Mapping[str, Any]) -> Path:
    relative = PurePosixPath(str(receipt["snapshot_run_relative_path"]))
    return run_dir.joinpath(*relative.parts)


def verify_snapshot(run_dir: Path, receipt: Mapping[str, Any],
                    header: Mapping[str, Any],
                    anchor: Mapping[str, Any] | None = None, *,
                    semantic_verifier: Callable[[sqlite3.Connection], None]
                    | None = None) -> Path:
    value = validate_receipt(receipt, header)
    path = _snapshot_path(Path(run_dir), value)
    run_fd = _open_dir(Path(run_dir))
    backup_fd = snapshot_fd = -1
    try:
        backup_fd = _open_dir("backups", dir_fd=run_fd)
        name = PurePosixPath(value["snapshot_run_relative_path"]).parts[-1]
        snapshot_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                              dir_fd=backup_fd)
        st = os.fstat(snapshot_fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_size != value["snapshot_size_bytes"]
                or _hash_fd(snapshot_fd) != value["snapshot_sha256"]):
            raise C0B4BackupError("backup snapshot path, mode, size or digest changed")
        _verify_sqlite_fd(snapshot_fd)
        if anchor is not None:
            snapshot = sqlite3.connect(
                f"file:/proc/self/fd/{snapshot_fd}?mode=ro&immutable=1",
                uri=True, timeout=1.0)
            try:
                snapshot.execute("PRAGMA query_only=ON")
                raw, digest = snapshot.execute(
                    "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
                if (digest != anchor["header_sha256"] or canonical_json(header) != raw):
                    raise C0B4BackupError("snapshot header lineage changed")
                state = str(snapshot.execute(
                    "SELECT state FROM run_state WHERE id=1").fetchone()[0])
                _verify_terminal_lineage(snapshot, state, anchor)
                if semantic_verifier is not None:
                    try:
                        semantic_verifier(snapshot)
                    except Exception as exc:
                        raise C0B4BackupError(
                            "backup snapshot semantic rederivation failed") from exc
            finally:
                snapshot.close()
        named = os.stat(name, dir_fd=backup_fd, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
            raise C0B4BackupError("backup snapshot path left its pinned inode")
        return path
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if backup_fd >= 0:
            os.close(backup_fd)
        os.close(run_fd)


def ensure_backup_receipt(point: C0B4Checkpoint, lock: GlobalExecutionLock, *,
                          terminal_artifact_sha256: str,
                          completion_sha256: str | None) -> dict[str, Any]:
    """Create or verify the one terminal anchor/snapshot/receipt tuple."""
    if point.conn.in_transaction or point.state() not in {
            "CONFIRMED", "INCONCLUSIVE", "FAILED_SAFETY", "BLOCKED_PROVENANCE",
            "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED"}:
        raise C0B4BackupError("backup requires committed terminal evidence")
    point._assert_parent_unchanged()
    header = point.header()
    anchor = build_anchor(point, terminal_artifact_sha256=terminal_artifact_sha256,
                          completion_sha256=completion_sha256)
    anchor_hash, anchor_raw = anchor["anchor_sha256"], canonical_json(anchor)
    row = point.conn.execute(
        "SELECT anchor_json,receipt_sha256,receipt_json FROM backup_receipts "
        "WHERE anchor_sha256=?", (anchor_hash,)).fetchone()
    if row:
        if row[0] != anchor_raw:
            raise C0B4BackupError("stored backup anchor changed")
        receipt = validate_receipt(json.loads(row[2]), header)
        if canonical_json(receipt) != row[2] or receipt["receipt_sha256"] != row[1]:
            raise C0B4BackupError("stored backup receipt changed")
        verify_snapshot(point.path.parent, receipt, header, anchor)
        point._assert_parent_unchanged()
        return receipt

    pinned = _write_backup_snapshot(point, lock, anchor_hash)
    snapshot_path = pinned.path
    try:
        receipt: dict[str, Any] = {
            "version": RECEIPT_VERSION, **_common(header), "anchor_sha256": anchor_hash,
            "snapshot_run_relative_path": snapshot_path.relative_to(point.path.parent).as_posix(),
            "snapshot_sha256": pinned.snapshot_hash, "snapshot_size_bytes": pinned.size,
            "integrity_check": "ok", "foreign_key_violations": 0,
            "created_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z"),
        }
        receipt["receipt_sha256"] = sha256_json(receipt)
        receipt = validate_receipt(receipt, header)
        pinned.verify()
        point._assert_parent_unchanged()
        point.conn.execute("BEGIN IMMEDIATE")
        try:
            if build_anchor(point, terminal_artifact_sha256=terminal_artifact_sha256,
                            completion_sha256=completion_sha256) != anchor:
                raise C0B4BackupError("terminal anchor changed before receipt commit")
            pinned.verify()
            point.conn.execute("INSERT INTO backup_receipts VALUES(?,?,?,?,?)",
                               (anchor_hash, anchor_raw, receipt["receipt_sha256"],
                                canonical_json(receipt), datetime.now().timestamp()))
            point.conn.commit()
        except Exception:
            point.conn.rollback()
            raise
        pinned.verify()
        verify_snapshot(point.path.parent, receipt, header, anchor)
        point._assert_parent_unchanged()
        return receipt
    except Exception:
        if not point.conn.execute(
                "SELECT 1 FROM backup_receipts WHERE anchor_sha256=?", (anchor_hash,)).fetchone():
            snapshot_path.unlink(missing_ok=True)
        raise
    finally:
        pinned.close()


def verify_backup_readonly(
        path: Path, *,
        semantic_verifier: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    """Verify source receipt plus the immutable pre-receipt snapshot without writes."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if semantic_verifier is None:
            raise C0B4BackupError(
                "authoritative backup verification requires semantic rederivation")
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600):
            raise PermissionError("checkpoint must be an owner-only regular file")
        conn = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",
                               uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            raw, digest = conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
            header = validate_header(json.loads(raw))
            if canonical_json(header) != raw or sha256_json(header) != digest:
                raise C0B4BackupError("source run header changed")
            rows = conn.execute(
                "SELECT anchor_json,receipt_sha256,receipt_json FROM backup_receipts").fetchall()
            state = str(conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0])
        finally:
            conn.close()
        if len(rows) != 1:
            raise C0B4BackupError("terminal checkpoint must have exactly one backup receipt")
        anchor = validate_anchor(json.loads(rows[0][0]), header)
        receipt = validate_receipt(json.loads(rows[0][2]), header)
        if (canonical_json(anchor) != rows[0][0]
                or receipt["receipt_sha256"] != rows[0][1]
                or canonical_json(receipt) != rows[0][2]
                or receipt["anchor_sha256"] != anchor["anchor_sha256"]):
            raise C0B4BackupError("source backup row changed")
        source = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",
                                 uri=True, timeout=1.0)
        try:
            source.execute("PRAGMA query_only=ON")
            _verify_terminal_lineage(source, state, anchor)
            try:
                semantic_verifier(source)
            except Exception as exc:
                raise C0B4BackupError(
                    "source checkpoint semantic rederivation failed") from exc
        finally:
            source.close()
        snapshot = verify_snapshot(
            path.parent, receipt, header, anchor,
            semantic_verifier=semantic_verifier)
        return {"ok": True, "errors": [], "anchor_sha256": anchor["anchor_sha256"],
                "snapshot_sha256": receipt["snapshot_sha256"], "snapshot": str(snapshot)}
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError,
            C0B4CheckpointError) as exc:
        return {"ok": False, "errors": [type(exc).__name__]}
    finally:
        os.close(fd)
