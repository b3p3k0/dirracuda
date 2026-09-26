"""Offline storage and tamper tests for the C0B-6 checkpoint."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b6_checkpoint as checkpoint_module
from scripts.analyst_benchmark.c0b6_checkpoint import (
    C0B6Checkpoint,
    C0B6CheckpointError,
    CUMULATIVE_CAP,
    INVOCATION_CAPS,
    LEDGER_LIMITS,
    canonical_json,
    sha256_json,
    status_readonly,
)
from scripts.analyst_benchmark.c0b6_policy import (
    BENCHMARK_PROTOCOL_ID,
    POLICY_ID,
    POLICY_SHA256,
)
from scripts.analyst_benchmark.c0b6_schema import PARENT_BINDING


def _mount() -> dict[str, object]:
    value: dict[str, object] = {
        "canonical_path": "/synthetic/bench",
        "mount_id": "1",
        "mountpoint": "/synthetic/bench",
        "fs_type": "ext4",
        "options": "rw",
        "st_dev": 1,
        "kernel": "test",
        "mergerfs_version": "not-mergerfs",
        "sqlite_version": sqlite3.sqlite_version,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    value["sha256"] = hashlib.sha256(raw).hexdigest()
    return value


def header(run_id: str) -> dict[str, object]:
    digest = "a" * 64
    return {
        "version": "c0b6-run-header-v1",
        "run_type": "public_confirmation",
        "benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": digest,
        "parent_binding": PARENT_BINDING,
        "ollama_endpoint": "http://127.0.0.1:11434",
        "ollama_version": "0.32.5",
        "filesystem_selected_mode": "DELETE",
        "git_head": "b" * 40,
        "declared_dirty_state_sha256": digest,
        "task_tree_sha256": digest,
        "fixture_sha256": digest,
        "master_manifest_sha256": digest,
        "schema_sha256": digest,
        "prompt_sha256": digest,
        "chunker_sha256": digest,
        "detector_sha256": digest,
        "generation_options_sha256": digest,
        "worktree_seal_sha256": digest,
        "filesystem_capability_sha256": digest,
        "model_digests": {
            "qwen3.6:27b":
                "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
        },
        "mount": _mount(),
        "schema_version": 1,
        "journal_mode": "DELETE",
        "cumulative_cap": CUMULATIVE_CAP,
        "run_id": run_id,
        "limits": LEDGER_LIMITS,
        "invocation_caps": INVOCATION_CAPS,
    }


def create(tmp_path: Path) -> C0B6Checkpoint:
    run_id = "c0b6-20260818-220000-" + "c" * 24
    parents = tuple(tmp_path / f"parent-{index}" for index in range(4))
    point = C0B6Checkpoint.create(
        tmp_path, run_id, header=header(run_id), parent_paths=parents)
    # Unit storage tests isolate SQLite behavior; parent descriptor/hash behavior is
    # covered by the lineage and public-flow suites against pinned fixtures.
    point._assert_parents_unchanged = lambda: None  # type: ignore[method-assign]
    return point


def test_create_is_owner_only_and_readonly_status_has_no_side_effect(tmp_path: Path) -> None:
    point = create(tmp_path)
    path = point.path
    assert point.state() == "PREPARED"
    point.close()
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    assert status_readonly(path) == {
        "ok": True, "state": "PREPARED", "charged_calls": 0, "errors": []}
    assert (path.stat().st_size, path.stat().st_mtime_ns) == before
    assert stat_mode(path) == 0o600


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_precharge_is_durable_and_one_request_may_be_in_flight(tmp_path: Path) -> None:
    point = create(tmp_path)
    point.set_state("RUNNING")
    ordinal = point.begin_invocation()
    point.precharge(
        attempt_id="1" * 64, owner_id="2" * 64, call_class="scored",
        invocation_ordinal=ordinal, request_sha256="3" * 64)
    with pytest.raises(C0B6CheckpointError, match="already in flight"):
        point.precharge(
            attempt_id="4" * 64, owner_id="5" * 64, call_class="scored",
            invocation_ordinal=ordinal, request_sha256="6" * 64)
    point.record_attempt("1" * 64, "RAW_VALID", {"answered": True})
    assert point.list_attempts()[0]["payload"] == {"answered": True}
    path = point.path
    point.close()
    with C0B6Checkpoint.open(path) as reopened:
        assert reopened.list_attempts()[0]["state"] == "RAW_VALID"


def test_header_and_artifact_family_tamper_fail_closed(tmp_path: Path) -> None:
    point = create(tmp_path)
    path = point.path
    point.close()
    os.chmod(path, 0o600)
    conn = sqlite3.connect(path)
    value = json.loads(conn.execute("SELECT json FROM run_header").fetchone()[0])
    value["policy_id"] = "c0b4-bounded-grounded-dedup-v1"
    conn.execute("UPDATE run_header SET json=?", (canonical_json(value),))
    conn.commit()
    conn.close()
    assert status_readonly(path)["ok"] is False


def test_initializer_failure_never_publishes_or_leaves_staging(tmp_path: Path) -> None:
    run_id = "c0b6-20260812-120000-" + "d" * 24
    parents = tuple(tmp_path / f"parent-{index}" for index in range(4))

    def fail(_point: C0B6Checkpoint) -> None:
        raise RuntimeError("injected initializer failure")

    with pytest.raises(RuntimeError, match="injected"):
        C0B6Checkpoint.create(
            tmp_path, run_id, header=header(run_id), parent_paths=parents,
            initializer=fail)

    assert not (tmp_path / "runs" / run_id).exists()
    assert not list((tmp_path / "runs").glob(".c0b6-initializing-*"))


def test_post_publish_fsync_failure_removes_published_child(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "c0b6-20260812-130000-" + "e" * 24
    parents = tuple(tmp_path / f"parent-{index}" for index in range(4))
    final = tmp_path / "runs" / run_id
    real_fsync = checkpoint_module.os.fsync

    def fail_after_publish(fd: int) -> None:
        if final.is_dir():
            raise OSError("injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(checkpoint_module.os, "fsync", fail_after_publish)
    with pytest.raises(OSError, match="directory fsync"):
        C0B6Checkpoint.create(
            tmp_path, run_id, header=header(run_id), parent_paths=parents)

    assert not final.exists()
    assert not list((tmp_path / "runs").glob(".c0b6-initializing-*"))


def test_terminal_state_and_artifact_owner_are_immutable(tmp_path: Path) -> None:
    point = create(tmp_path)
    identity = {
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": "a" * 64,
    }
    evidence = {
        "version": "c0b6-failure-evidence-v1",
        **identity,
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "failure_origin": "filesystem_revalidation",
        "lane_id": None,
        "plan_sha256": None,
        "attempt_id": None,
        "control_id": None,
        "charged_call_total": 0,
    }
    evidence["evidence_sha256"] = sha256_json(evidence)
    digest = point.store_artifact("failure_evidence", "terminal", evidence)
    result = {
        "version": "c0b6-failure-v1",
        **identity,
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "failure_origin": "filesystem_revalidation",
        "evidence_sha256": digest,
        "charged_call_total": 0,
    }
    point.finalize("BLOCKED_FILESYSTEM", result)
    with pytest.raises(C0B6CheckpointError, match="immutable"):
        point.set_state("ABANDONED")
    point.close()
