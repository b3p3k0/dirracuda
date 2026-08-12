"""Offline terminal backup tests for C0B-5."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.analyst_benchmark.c0b2_fsprobe import GlobalExecutionLock
from scripts.analyst_benchmark.c0b5_backup import (
    C0B5BackupError,
    ensure_backup_receipt,
    verify_backup_readonly,
)
from scripts.analyst_benchmark.c0b5_checkpoint import sha256_json
from scripts.analyst_benchmark.c0b5_policy import POLICY_ID, POLICY_SHA256
from scripts.tests.test_analyst_c0b5_checkpoint import create


def _terminal(point) -> tuple[str, None]:
    identity = {
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": "a" * 64,
    }
    evidence = {
        "version": "c0b5-failure-evidence-v1",
        **identity,
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "lane_id": None,
        "plan_sha256": None,
        "attempt_id": None,
        "control_id": None,
        "charged_call_total": 0,
    }
    evidence["evidence_sha256"] = sha256_json(evidence)
    evidence_sha = point.store_artifact(
        "failure_evidence", "terminal", evidence)
    result = {
        "version": "c0b5-failure-v1",
        **identity,
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "evidence_sha256": evidence_sha,
        "charged_call_total": 0,
    }
    result_sha, _ = point.finalize("BLOCKED_FILESYSTEM", result)
    return result_sha, None


def test_terminal_backup_and_readonly_replay(tmp_path: Path) -> None:
    point = create(tmp_path)
    terminal_sha, completion_sha = _terminal(point)
    with GlobalExecutionLock(tmp_path) as lock:
        receipt = ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=terminal_sha,
            completion_sha256=completion_sha,
            semantic_verifier=lambda _conn: None)
    snapshot = point.path.parent / receipt["snapshot_run_relative_path"]
    assert snapshot.stat().st_mode & 0o777 == 0o600
    path = point.path
    point.close()
    result = verify_backup_readonly(path, semantic_verifier=lambda _conn: None)
    assert result["ok"] is True
    assert result["snapshot_sha256"] == receipt["snapshot_sha256"]


def test_semantic_replay_precedes_receipt_publication(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = create(tmp_path)
    terminal_sha, completion_sha = _terminal(point)
    semantic_calls = []
    original_store = point.store_backup_receipt

    def semantic(_conn) -> None:
        semantic_calls.append("verified")

    def store(anchor, receipt) -> None:
        assert len(semantic_calls) >= 2
        original_store(anchor, receipt)

    monkeypatch.setattr(point, "store_backup_receipt", store)
    with GlobalExecutionLock(tmp_path) as lock:
        ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=terminal_sha,
            completion_sha256=completion_sha, semantic_verifier=semantic)

    assert len(semantic_calls) == 3
    point.close()


def test_snapshot_mode_tamper_fails_closed(tmp_path: Path) -> None:
    point = create(tmp_path)
    terminal_sha, completion_sha = _terminal(point)
    with GlobalExecutionLock(tmp_path) as lock:
        receipt = ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=terminal_sha,
            completion_sha256=completion_sha,
            semantic_verifier=lambda _conn: None)
    snapshot = point.path.parent / receipt["snapshot_run_relative_path"]
    point.close()
    os.chmod(snapshot, 0o640)
    assert verify_backup_readonly(
        snapshot.parent.parent / "checkpoint.sqlite3",
        semantic_verifier=lambda _conn: None)["ok"] is False


def test_nonterminal_backup_is_rejected(tmp_path: Path) -> None:
    point = create(tmp_path)
    with GlobalExecutionLock(tmp_path) as lock, pytest.raises(
            C0B5BackupError, match="terminal"):
        ensure_backup_receipt(
            point, lock, terminal_artifact_sha256="a" * 64,
            completion_sha256=None)
    point.close()
