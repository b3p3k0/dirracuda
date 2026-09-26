"""Non-circular terminal backup receipts for C0B-5 checkpoints.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .c0b2_fsprobe import GlobalExecutionLock
from .c0b2_runtime import _write_backup_snapshot
from .c0b5_checkpoint import (
    C0B5Checkpoint,
    C0B5CheckpointError,
    TERMINAL_STATES,
    canonical_json,
    sha256_json,
    validate_header,
    validate_run_lineage,
)
from .c0b5_schema import validate_artifact

ANCHOR_VERSION = "c0b5-backup-anchor-v1"
RECEIPT_VERSION = "c0b5-backup-receipt-v1"
SOURCE_KEYS = frozenset({
    "git_head", "declared_dirty_state_sha256", "task_tree_sha256",
    "protocol_sha256", "policy_sha256", "prompt_sha256", "schema_sha256",
    "fixture_sha256", "master_manifest_sha256", "chunker_sha256",
    "detector_sha256", "generation_options_sha256", "worktree_seal_sha256",
    "filesystem_capability_sha256", "model_digests",
})


class C0B5BackupError(C0B5CheckpointError):
    """A C0B-5 terminal snapshot or receipt is invalid."""


def _common(header: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(header[key]) for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}


def source_binding_from_header(header: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_header(header)
    source = {key: normalized[key] for key in SOURCE_KEYS}
    if frozenset(source) != SOURCE_KEYS:
        raise C0B5BackupError("C0B-5 source binding is incomplete")
    return source


def validate_anchor(value: Mapping[str, Any],
                    header: Mapping[str, Any]) -> dict[str, Any]:
    try:
        anchor = validate_artifact(value)
    except (TypeError, ValueError) as exc:
        raise C0B5BackupError("backup anchor violates its strict schema") from exc
    if (anchor.get("version") != ANCHOR_VERSION
            or any(anchor.get(key) != item
                   for key, item in _common(header).items())
            or anchor.get("run_id") != header["run_id"]
            or anchor.get("parent_binding") != header["parent_binding"]
            or anchor.get("source_binding") != source_binding_from_header(header)
            or sha256_json(anchor, omit="anchor_sha256") !=
               anchor.get("anchor_sha256")):
        raise C0B5BackupError("backup anchor identity changed")
    return anchor


def validate_receipt(value: Mapping[str, Any],
                     header: Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt = validate_artifact(value)
    except (TypeError, ValueError) as exc:
        raise C0B5BackupError("backup receipt violates its strict schema") from exc
    relative = PurePosixPath(str(receipt.get("snapshot_run_relative_path", "")))
    if (receipt.get("version") != RECEIPT_VERSION
            or any(receipt.get(key) != item
                   for key, item in _common(header).items())
            or receipt.get("integrity_check") != "ok"
            or receipt.get("foreign_key_violations") != 0
            or relative.is_absolute() or len(relative.parts) != 2
            or relative.parts[0] != "backups"
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not relative.parts[1].startswith(
                f"snapshot-{receipt.get('anchor_sha256')}-")
            or not relative.parts[1].endswith(".sqlite3")
            or sha256_json(receipt, omit="receipt_sha256") !=
               receipt.get("receipt_sha256")):
        raise C0B5BackupError("backup receipt identity changed")
    return receipt


def _terminal_rows(conn: sqlite3.Connection, state: str) -> tuple[str, str | None]:
    quality = state in {"CONFIRMED", "INCONCLUSIVE"}
    kind = "result" if quality else "failure"
    rows = conn.execute(
        "SELECT sha256,json FROM artifacts WHERE kind=? AND owner_id='terminal'",
        (kind,)).fetchall()
    if len(rows) != 1:
        raise C0B5BackupError("terminal artifact is absent or ambiguous")
    value = validate_artifact(json.loads(rows[0][1]))
    if canonical_json(value) != rows[0][1] or value["terminal"] != state:
        raise C0B5BackupError("terminal artifact is noncanonical or mismatched")
    completions = conn.execute(
        "SELECT sha256,json FROM artifacts WHERE kind='completion' "
        "AND owner_id='terminal'").fetchall()
    if quality:
        if len(completions) != 1:
            raise C0B5BackupError("quality terminal lacks its completion")
        completion = validate_artifact(json.loads(completions[0][1]))
        if (canonical_json(completion) != completions[0][1]
                or completion["artifact_sha256"] != rows[0][0]
                or completion["outcome"] != state):
            raise C0B5BackupError("completion ownership changed")
        return str(rows[0][0]), str(completions[0][0])
    if completions:
        raise C0B5BackupError("failure terminal owns a quality completion")
    return str(rows[0][0]), None


def _verify_terminal(conn: sqlite3.Connection, state: str,
                     semantic_verifier: Callable[[sqlite3.Connection], None]
                     | None = None) -> tuple[str, str | None]:
    validate_run_lineage(conn)
    if state not in TERMINAL_STATES:
        raise C0B5BackupError("backup source is not terminal")
    if conn.execute(
            "SELECT 1 FROM attempts WHERE state='DISPATCHING'").fetchone():
        raise C0B5BackupError("terminal owns an in-flight attempt")
    result = _terminal_rows(conn, state)
    if semantic_verifier is not None:
        semantic_verifier(conn)
    return result


def build_anchor(point: C0B5Checkpoint, *, terminal_artifact_sha256: str,
                 completion_sha256: str | None,
                 semantic_verifier: Callable[[sqlite3.Connection], None]
                 | None = None) -> dict[str, Any]:
    header, state = point.header(), point.state()
    expected_terminal, expected_completion = _verify_terminal(
        point.conn, state, semantic_verifier=semantic_verifier)
    if (terminal_artifact_sha256 != expected_terminal
            or completion_sha256 != expected_completion):
        raise C0B5BackupError("anchor owner differs from terminal lineage")
    header_row = point.conn.execute(
        "SELECT sha256 FROM run_header WHERE id=1").fetchone()
    anchor: dict[str, Any] = {
        "version": ANCHOR_VERSION,
        **_common(header),
        "run_id": header["run_id"],
        "header_sha256": header_row[0],
        "terminal_artifact_sha256": terminal_artifact_sha256,
        "completion_sha256": completion_sha256,
        "parent_binding": header["parent_binding"],
        "source_binding": source_binding_from_header(header),
    }
    anchor["anchor_sha256"] = sha256_json(anchor)
    return validate_anchor(anchor, header)


def _hash_fd(fd: int) -> str:
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, 1 << 20, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _open_dir(path: str | Path, *, dir_fd: int | None = None) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    st = os.fstat(fd)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700):
        os.close(fd)
        raise PermissionError("backup directory must be owner-only mode 0700")
    return fd


def verify_snapshot(
        run_dir: Path, receipt: Mapping[str, Any], header: Mapping[str, Any],
        anchor: Mapping[str, Any], *,
        semantic_verifier: Callable[[sqlite3.Connection], None] | None = None,
) -> Path:
    value = validate_receipt(receipt, header)
    run_fd = _open_dir(run_dir)
    backup_fd = snapshot_fd = -1
    try:
        backup_fd = _open_dir("backups", dir_fd=run_fd)
        name = PurePosixPath(value["snapshot_run_relative_path"]).parts[-1]
        snapshot_fd = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=backup_fd)
        st = os.fstat(snapshot_fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_size != value["snapshot_size_bytes"]
                or _hash_fd(snapshot_fd) != value["snapshot_sha256"]):
            raise C0B5BackupError("backup snapshot mode, size, or digest changed")
        conn = sqlite3.connect(
            f"file:/proc/self/fd/{snapshot_fd}?mode=ro&immutable=1",
            uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)] \
                    or conn.execute("PRAGMA foreign_key_check").fetchall():
                raise C0B5BackupError("backup snapshot failed SQLite checks")
            raw, digest = conn.execute(
                "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
            if (raw != canonical_json(header) or digest != anchor["header_sha256"]):
                raise C0B5BackupError("backup snapshot header changed")
            state = conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0]
            terminal, completion = _verify_terminal(
                conn, state, semantic_verifier=semantic_verifier)
            if (terminal != anchor["terminal_artifact_sha256"]
                    or completion != anchor["completion_sha256"]):
                raise C0B5BackupError("backup terminal owner changed")
        finally:
            conn.close()
        named = os.stat(name, dir_fd=backup_fd, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
            raise C0B5BackupError("backup snapshot left its pinned inode")
        return run_dir.joinpath(*PurePosixPath(
            value["snapshot_run_relative_path"]).parts)
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if backup_fd >= 0:
            os.close(backup_fd)
        os.close(run_fd)


def ensure_backup_receipt(
        point: C0B5Checkpoint, lock: GlobalExecutionLock, *,
        terminal_artifact_sha256: str, completion_sha256: str | None,
        semantic_verifier: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    """Create or verify the sole terminal anchor/snapshot/receipt tuple."""
    if point.conn.in_transaction or point.state() not in TERMINAL_STATES:
        raise C0B5BackupError("backup requires committed terminal evidence")
    header = point.header()
    if semantic_verifier is None:
        from .c0b5_lineage import ParentPaths, verify_parents_readonly
        from .c0b5_replay import replay_c0b5_connection
        parents = verify_parents_readonly(ParentPaths(*point.parent_paths()))
        semantic_verifier = lambda conn: replay_c0b5_connection(
            conn, parent_facts=parents, require_receipt=False)
    anchor = build_anchor(
        point, terminal_artifact_sha256=terminal_artifact_sha256,
        completion_sha256=completion_sha256,
        semantic_verifier=semantic_verifier)
    row = point.conn.execute(
        "SELECT anchor_json,receipt_sha256,receipt_json FROM backup_receipts "
        "WHERE anchor_sha256=?", (anchor["anchor_sha256"],)).fetchone()
    if row:
        stored_anchor = validate_anchor(json.loads(row[0]), header)
        receipt = validate_receipt(json.loads(row[2]), header)
        if (canonical_json(stored_anchor) != row[0]
                or canonical_json(receipt) != row[2]
                or receipt["receipt_sha256"] != row[1]):
            raise C0B5BackupError("stored backup row changed")
        verify_snapshot(
            point.path.parent, receipt, header, stored_anchor,
            semantic_verifier=semantic_verifier)
        return receipt

    pinned = _write_backup_snapshot(point, lock, anchor["anchor_sha256"])
    try:
        receipt: dict[str, Any] = {
            "version": RECEIPT_VERSION,
            **_common(header),
            "anchor_sha256": anchor["anchor_sha256"],
            "snapshot_run_relative_path":
                pinned.path.relative_to(point.path.parent).as_posix(),
            "snapshot_sha256": pinned.snapshot_hash,
            "snapshot_size_bytes": pinned.size,
            "integrity_check": "ok",
            "foreign_key_violations": 0,
            "created_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z"),
        }
        receipt["receipt_sha256"] = sha256_json(receipt)
        receipt = validate_receipt(receipt, header)
        pinned.verify()
        verify_snapshot(
            point.path.parent, receipt, header, anchor,
            semantic_verifier=semantic_verifier)
        pinned.verify()
        point.store_backup_receipt(anchor, receipt)
        pinned.verify()
        verify_snapshot(
            point.path.parent, receipt, header, anchor,
            semantic_verifier=semantic_verifier)
        return receipt
    finally:
        pinned.close()


def verify_backup_readonly(
        path: Path, *,
        semantic_verifier: Callable[[sqlite3.Connection], None],
) -> dict[str, Any]:
    """Verify source receipt and immutable snapshot with semantic replay."""
    try:
        with C0B5Checkpoint.open(path) as point:
            header, state = point.header(), point.state()
            _verify_terminal(point.conn, state, semantic_verifier=semantic_verifier)
            rows = point.conn.execute(
                "SELECT anchor_json,receipt_sha256,receipt_json "
                "FROM backup_receipts").fetchall()
            if len(rows) != 1:
                raise C0B5BackupError("terminal must have one receipt")
            anchor = validate_anchor(json.loads(rows[0][0]), header)
            receipt = validate_receipt(json.loads(rows[0][2]), header)
            if (canonical_json(anchor) != rows[0][0]
                    or receipt["receipt_sha256"] != rows[0][1]
                    or canonical_json(receipt) != rows[0][2]
                    or receipt["anchor_sha256"] != anchor["anchor_sha256"]):
                raise C0B5BackupError("source receipt row changed")
            snapshot = verify_snapshot(
                point.path.parent, receipt, header, anchor,
                semantic_verifier=semantic_verifier)
            return {
                "ok": True, "errors": [],
                "anchor_sha256": anchor["anchor_sha256"],
                "snapshot_sha256": receipt["snapshot_sha256"],
                "snapshot": str(snapshot),
            }
    except Exception as exc:
        return {"ok": False, "errors": [type(exc).__name__]}
