"""Offline creation and identity guards for the C0B-2B1 public runtime."""
from __future__ import annotations

import json
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.analyst_benchmark import c0b2_plan as plan
from scripts.analyst_benchmark import c0b2_checkpoint as checkpoint
from scripts.analyst_benchmark import c0b2_runtime as runtime
from scripts.analyst_benchmark import c0b2_executor as executor
from scripts.analyst_benchmark import c0b2_transport as transport
from scripts.analyst_benchmark.c0b2_leakscan import SealEntry, WorktreeSeal


def test_public_ledger_is_exact_and_nontransferable() -> None:
    assert runtime.PUBLIC_LIMITS == {
        "C": {"scored": 264, "schema_retry": 12,
              "preflight_probe": 18, "transport_orphan": 106},
        "D": {"scored": 757, "schema_retry": 64,
              "preflight_probe": 36, "transport_orphan": 93},
        "F": {"scored": 1142, "schema_retry": 14,
              "preflight_probe": 59, "transport_orphan": 185},
    }
    assert [sum(runtime.PUBLIC_LIMITS[s].values()) for s in ("C", "D", "F")] == [
        400, 950, 1400]
    assert sum(sum(row.values()) for row in runtime.PUBLIC_LIMITS.values()) == 2750
    assert runtime.PUBLIC_CUMULATIVE_CAP == 2750


def test_generated_manifest_and_plan_payloads_reproduce_frozen_hashes() -> None:
    manifest = plan.build_master_manifest()
    stage = plan.build_c_stage_plan(b"k" * 32)
    assert plan.stable_hash(runtime._manifest_payload(manifest)) == manifest.sha256
    assert plan.stable_hash(runtime._plan_payload(stage)) == stage.sha256


def test_run_ids_and_checkpoint_paths_are_opaque(tmp_path: Path) -> None:
    generated = runtime.new_public_run_id()
    assert generated.startswith("c0b2-")
    assert runtime._checkpoint_path(
        generated, tmp_path) == tmp_path / "runs" / generated / "checkpoint.sqlite3"
    for bad in ("../escape", "/absolute", "has space"):
        with pytest.raises(ValueError):
            runtime._checkpoint_path(bad, tmp_path)


def test_task_delta_must_be_committed_but_unrelated_dirty_work_is_allowed() -> None:
    unrelated = SealEntry(
        "docs/dev/kbd_ctrl_improve/notes.md", "??", "file", 0o644, 1, "a" * 64)
    runtime._require_clean_task_delta(WorktreeSeal("b" * 40, (unrelated,)))

    task = SealEntry(
        "scripts/analyst_benchmark/c0b2_runtime.py", " M", "file", 0o644, 1,
        "b" * 64)
    with pytest.raises(runtime.RuntimeGateError, match="commit"):
        runtime._require_clean_task_delta(WorktreeSeal("b" * 40, (unrelated, task)))


def test_content_free_render_is_canonical_json() -> None:
    rendered = runtime.render_public({"state": "PREPARED", "calls_total": 0})
    assert rendered == '{"calls_total":0,"state":"PREPARED"}'
    assert json.loads(rendered) == {"state": "PREPARED", "calls_total": 0}


@pytest.mark.parametrize(
    "key", ("F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE"))
def test_b4_backup_activation_delegates_after_common_identity_checks(
        monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    calls = []
    monkeypatch.setattr(
        evidence, "validate_b4_backup_activation",
        lambda *args: calls.append(args))
    conn = object()
    header = {"run_id": "public-run"}
    plan_value = {"budget_stage": "F"}
    activation = {"run_id": "public-run", "budget_stage": "F"}
    runtime._validate_backup_activation(
        conn, header, key, plan_value, activation)
    assert calls == [(conn, header, key, plan_value, activation)]


@pytest.mark.parametrize("mismatch", ("run", "budget"))
def test_b4_backup_activation_rejects_common_mismatch_before_delegation(
        monkeypatch: pytest.MonkeyPatch, mismatch: str) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    calls = []
    monkeypatch.setattr(
        evidence, "validate_b4_backup_activation",
        lambda *args: calls.append(args))
    activation = {"run_id": "other" if mismatch == "run" else "public-run",
                  "budget_stage": "D" if mismatch == "budget" else "F"}
    with pytest.raises(runtime.RuntimeGateError, match="run or budget stage"):
        runtime._validate_backup_activation(
            object(), {"run_id": "public-run"}, "F_SEED_17",
            {"budget_stage": "F"}, activation)
    assert calls == []


@pytest.mark.parametrize(("tail", "cursor", "accepted"), (
    ("C", "C", True),
    ("F_SEED_20260804", "F_SEED_17", True),
    ("F_SEED_17", "F_SEED_20260804", False),
    ("F_ACCEPTANCE", "F_SEED_17", False),
    ("F_SEED_20260804", "F_SEED_1", False),
))
def test_backup_tail_accepts_only_exact_paired_f17_cursor_exception(
        tail: str, cursor: str, accepted: bool) -> None:
    assert runtime._backup_tail_matches_cursor(tail, cursor) is accepted


def test_acceptance_decision_is_owned_by_f_master_not_preceding_seed_plan() -> None:
    master, seed_plan = "a" * 64, "b" * 64
    assert runtime._decision_parent_hash("F_ACCEPTANCE", seed_plan, master) == master
    assert runtime._decision_parent_hash("F_SEED_17", seed_plan, master) == seed_plan
    with pytest.raises(runtime.RuntimeGateError, match="master owner"):
        runtime._decision_parent_hash("F_ACCEPTANCE", seed_plan, None)


def test_stage_f_terminal_owner_delegates_and_propagates_failure(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    conn, header, calls = object(), {"run_id": "public-run"}, []
    expected = "a" * 64
    monkeypatch.setattr(
        evidence, "validate_b4_terminal_owner",
        lambda *args: calls.append(args) or expected)
    kwargs = {
        "active_stage": "F", "active_plan_key": "F_SEED_1",
        "active_plan_hash": "b" * 64, "aggregate_hash": "c" * 64,
        "charged_total": 0, "header": header,
    }
    assert runtime._stored_public_artifact(
        conn, "INCONCLUSIVE", **kwargs) == expected
    assert calls == [(conn, header)]

    def reject(*_args: object) -> str:
        raise checkpoint.ImmutableViolation("B4 finalizer is not authoritative yet")

    monkeypatch.setattr(evidence, "validate_b4_terminal_owner", reject)
    with pytest.raises(checkpoint.ImmutableViolation, match="not authoritative"):
        runtime._stored_public_artifact(conn, "SELECTED", **kwargs)

    failure_db = sqlite3.connect(":memory:")
    failure_db.execute(
        "CREATE TABLE public_artifacts(terminal TEXT,artifact_hash TEXT,artifact_json TEXT)")
    calls.clear()
    monkeypatch.setattr(
        evidence, "validate_b4_terminal_owner",
        lambda *args: calls.append(args) or expected)
    with pytest.raises(runtime.RuntimeGateError, match="terminal requires"):
        runtime._stored_public_artifact(
            failure_db, "FAILED_SAFETY", **kwargs)
    assert calls == []
    failure_db.close()


def _stage_c_boundary(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str,
) -> tuple[Path, runtime.Checkpoint]:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root = tmp_path / "bench"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    _parent, plan_hash, _raw = point.load_plan("C")
    aggregate = {"version": "test-stage-c-aggregate"}
    aggregate_raw = runtime.canonical_json(aggregate)
    aggregate_hash = runtime.sha256_json(aggregate)
    point.conn.execute(
        "INSERT INTO stage_aggregates VALUES(?,?,?,?,?)",
        ("C", plan_hash, aggregate_hash, aggregate_raw, 1.0),
    )
    decision = {"version": "test-stage-c-selection"}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-c-selection", "C", plan_hash, aggregate_hash, "ACTIVATED",
         runtime.canonical_json(decision), 1.0),
    )
    point.conn.execute(
        "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY' WHERE id=1")
    return root, point


@pytest.mark.parametrize("terminal", (None, "ABANDONED"))
def test_c_backup_reentry_source_drift_has_zero_mutation_or_transport(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        terminal: str | None) -> None:
    run_id = f"c0b2-c-reentry-drift-{terminal or 'boundary'}"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    if terminal is not None:
        runtime.finish_public_run_failure(point, terminal=terminal)
    expected_state = point.state()
    receipt_count = point.conn.execute(
        "SELECT count(*) FROM backup_receipts").fetchone()[0]
    path = runtime._checkpoint_path(run_id, root)
    point.close()
    before = path.read_bytes()
    calls: list[str] = []

    def source_drift(_header: object) -> None:
        calls.append("pins")
        raise runtime.RuntimeGateError("source drift")

    monkeypatch.setattr(runtime, "revalidate_source_pins", source_drift)
    with pytest.raises(runtime.RuntimeGateError, match="source drift"):
        runtime.run_public_stage_c(
            run_id, benchmark_root=root,
            transport_factory=lambda *_args: calls.append("transport"))
    assert calls == ["pins"] and path.read_bytes() == before
    reopened = runtime.Checkpoint.open(path, root)
    try:
        assert reopened.state() == expected_state
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == receipt_count
    finally:
        reopened.close()


def test_c_blocked_provenance_reentry_skips_pins_and_completes_receipt(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-c-reentry-blocked-provenance"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    runtime.finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
    point.close()
    monkeypatch.setattr(
        runtime, "revalidate_source_pins",
        lambda _header: pytest.fail("frozen provenance terminal must remain receiptable"))

    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda *_args: pytest.fail(
            "terminal receipt re-entry must not construct transport"))
    assert result["state"] == "BLOCKED_PROVENANCE"
    status = runtime.public_status(run_id, benchmark_root=root)
    assert status["backup"]["receipt_present"] is True


class _CreationCrash(BaseException):
    """Simulate process death, bypassing ordinary exception cleanup."""


def _leave_initializing(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str, *,
        after_promotion: bool,
) -> tuple[Path, Path]:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root = tmp_path / "bench"
    original = runtime.Checkpoint.promote

    def crash(point: runtime.Checkpoint, identity: str) -> None:
        if after_promotion:
            original(point, identity)
        point.close()
        raise _CreationCrash

    monkeypatch.setattr(runtime.Checkpoint, "promote", crash)
    with pytest.raises(_CreationCrash):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    monkeypatch.setattr(runtime.Checkpoint, "promote", original)
    candidates = tuple((root / "runs").iterdir())
    assert len(candidates) == 1
    return root, candidates[0]


def test_public_create_freezes_private_nonce_and_is_idempotent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-idempotent"
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        digest, raw = point.load_manifest("run_nonce_key")
        value = json.loads(raw)
        assert set(value) == {"version", "key_hex"}
        assert value["version"] == "c0b2-run-nonce-key-v1"
        assert len(bytes.fromhex(value["key_hex"])) == 32
        assert digest == runtime.sha256_json(value)
        assert runtime._run_nonce_key(point) == bytes.fromhex(value["key_hex"])
        first_key = value["key_hex"]
    finally:
        point.close()
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        assert json.loads(point.load_manifest("run_nonce_key")[1])["key_hex"] == first_key
        assert point.state() == "PREPARED"
    finally:
        point.close()
    assert len(tuple((root / "snapshots" / run_id).iterdir())) == 1
    rendered = runtime.render_public(runtime.public_status(run_id, benchmark_root=root))
    assert first_key not in rendered and "run_nonce_key" not in rendered
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


@pytest.mark.parametrize("after_promotion", [False, True])
def test_public_create_recovers_exact_initializing_crash_windows(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        after_promotion: bool) -> None:
    run_id = f"c0b2-create-crash-{after_promotion}"
    root, leftover = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=after_promotion)
    if after_promotion:
        assert leftover.name == run_id
    else:
        suffix = leftover.name.removeprefix(f".c0b2-initializing-{run_id}-")
        assert len(suffix) == 32 and set(suffix) <= set("0123456789abcdef")
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    assert runtime.public_status(run_id, benchmark_root=root)["state"] == "PREPARED"
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


def test_public_create_quarantines_ordinary_transaction_failure(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-txn-failure"
    original = runtime.Checkpoint.freeze_plan
    identities: tuple[tuple[int, int], tuple[int, int]] | None = None

    def fail_plan(point: runtime.Checkpoint, *_args: Any, **_kwargs: Any) -> str:
        nonlocal identities
        identities = ((point.path.parent.stat().st_dev, point.path.parent.stat().st_ino),
                      (point.path.stat().st_dev, point.path.stat().st_ino))
        raise OSError("transaction cut")

    monkeypatch.setattr(runtime.Checkpoint, "freeze_plan", fail_plan)
    with pytest.raises(OSError, match="transaction cut"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    retained = tuple((root / "runs").glob(".c0b2-quarantine-*"))
    assert identities is not None and len(retained) == 1
    assert (retained[0].stat().st_dev, retained[0].stat().st_ino) == identities[0]
    retained_db = retained[0] / "checkpoint.sqlite3"
    assert (retained_db.stat().st_dev, retained_db.stat().st_ino) == identities[1]
    assert retained[0].stat().st_mode & 0o777 == 0o700
    assert retained_db.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(retained_db) as retained_conn:
        assert retained_conn.execute(
            "SELECT state FROM run_state WHERE id=1").fetchone() == ("INITIALIZING",)
    assert not (root / "runs" / run_id).exists()
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))
    monkeypatch.setattr(runtime.Checkpoint, "freeze_plan", original)
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id


def test_public_create_quarantines_failure_before_promotion(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-base-fsync"
    original = runtime.Checkpoint._fsync_file_and_parent
    identity: tuple[int, int] | None = None

    def fail_staging(path: Path) -> None:
        nonlocal identity
        if path.parent.name.startswith(f".c0b2-initializing-{run_id}-"):
            identity = (path.parent.stat().st_dev, path.parent.stat().st_ino)
            raise OSError("base fsync cut")
        original(path)

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", fail_staging)
    with pytest.raises(OSError, match="base fsync cut"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    retained = tuple((root / "runs").glob(".c0b2-quarantine-*"))
    assert identity is not None and len(retained) == 1
    assert (retained[0].stat().st_dev, retained[0].stat().st_ino) == identity
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


def test_public_create_recovers_empty_pre_database_staging(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-empty-staging"
    stage = root / "runs" / f".c0b2-initializing-{run_id}-{'b' * 32}"
    stage.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    (root / "runs").chmod(0o700)
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    assert not stage.exists()


def test_public_create_never_replaces_a_racing_final_directory(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-noreplace"
    original = runtime.Checkpoint.promote
    marker = root / "runs" / run_id / "marker"

    def race(point: runtime.Checkpoint, identity: str) -> None:
        marker.parent.mkdir(mode=0o700)
        marker.write_text("preserve", encoding="utf-8")
        original(point, identity)

    monkeypatch.setattr(runtime.Checkpoint, "promote", race)
    with pytest.raises(FileExistsError):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


def test_public_create_preserves_prepared_on_fsync_failure_and_retries(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-prepared-fsync"
    original = runtime.Checkpoint._fsync_file_and_parent

    def fail_final(path: Path) -> None:
        if path.parent.name == run_id:
            raise OSError("prepared fsync cut")
        original(path)

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", fail_final)
    with pytest.raises(OSError, match="prepared fsync cut"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        assert point.state() == "PREPARED"
        first_key = point.load_manifest("run_nonce_key")[1]
    finally:
        point.close()
    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", original)
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        assert point.load_manifest("run_nonce_key")[1] == first_key
    finally:
        point.close()


def test_public_create_reuses_exact_snapshot_after_return_window_crash(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-snapshot-retry"
    original = runtime.backup_snapshot

    def crash_after_snapshot(*args: Any, **kwargs: Any) -> Path:
        original(*args, **kwargs)
        raise OSError("snapshot return cut")

    monkeypatch.setattr(runtime, "backup_snapshot", crash_after_snapshot)
    with pytest.raises(OSError, match="snapshot return cut"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    snapshots = tuple((root / "snapshots" / run_id).iterdir())
    assert len(snapshots) == 1
    monkeypatch.setattr(runtime, "backup_snapshot", original)
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    assert tuple((root / "snapshots" / run_id).iterdir()) == snapshots


def test_public_create_retry_rejects_logically_changed_initial_snapshot(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-snapshot-tamper"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    snapshot = next((root / "snapshots" / run_id).iterdir())
    conn = sqlite3.connect(snapshot)
    try:
        conn.execute("UPDATE run_state SET updated=updated+1 WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime.RuntimeGateError, match="unexpected evidence"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert snapshot.exists()


def test_public_create_refuses_unsafe_or_advanced_staging(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-create-hostile-staging"
    root, stage = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=False)
    marker = stage / "unexpected"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(PermissionError):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert marker.read_text(encoding="utf-8") == "preserve"
    marker.unlink()
    conn = sqlite3.connect(stage / "checkpoint.sqlite3")
    try:
        conn.execute("UPDATE run_state SET state='PREPARED' WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime.CheckpointError, match="content"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert stage.exists()


def test_public_create_refuses_symlink_staging_without_touching_target(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-symlink"
    runs, outside = root / "runs", tmp_path / "outside"
    runs.mkdir(parents=True, mode=0o700)
    outside.mkdir(mode=0o700)
    marker = outside / "marker"
    marker.write_text("preserve", encoding="utf-8")
    stage = runs / f".c0b2-initializing-{run_id}-{'a' * 32}"
    stage.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert stage.is_symlink() and marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("extra", ["manifest", "plan"])
def test_public_create_retry_rejects_extra_frozen_rows(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: str) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", f"c0b2-create-extra-{extra}"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        if extra == "manifest":
            point.freeze_manifest("extra", {"unexpected": True})
        else:
            raw = runtime.canonical_json({"work": []})
            point.conn.execute(
                "INSERT INTO plans VALUES('X',?,?,?,1.0)",
                ("a" * 64, runtime.sha256_json({"work": []}), raw))
    finally:
        point.close()
    with pytest.raises(runtime.RuntimeGateError, match="work or receipt"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)


def test_initializing_is_rejected_by_status_verify_and_abandon(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-initializing-surfaces"
    root, final = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=True)
    assert final.name == run_id
    with pytest.raises(runtime.RuntimeGateError, match="create-recovery"):
        runtime.public_status(run_id, benchmark_root=root)
    with pytest.raises(runtime.RuntimeGateError, match="create-recovery"):
        runtime.public_verify(run_id, benchmark_root=root)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        with runtime.GlobalExecutionLock(root) as lock:
            worker = executor.DurableExecutor(
                point, lock, lambda *_args: executor.FakeResponse(""))
            with pytest.raises(runtime.CheckpointError):
                worker.abandon()
        assert point.state() == "INITIALIZING"
    finally:
        point.close()


def test_initializing_cannot_be_reentered_from_runtime_state(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-no-initializing-transition"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        point.transition("RUNNING")
        with pytest.raises(runtime.CheckpointError, match="illegal state transition"):
            point.transition("INITIALIZING")
    finally:
        point.close()


def test_nonce_tamper_at_boundary_becomes_idempotent_blocked_provenance(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(monkeypatch, tmp_path, "c0b2-nonce-tamper")
    changed = {"version": "c0b2-run-nonce-key-v1", "key_hex": "ab" * 32}
    point.conn.execute(
        "UPDATE manifests SET manifest_hash=?,manifest_json=? WHERE name='run_nonce_key'",
        (runtime.sha256_json(changed), runtime.canonical_json(changed)))
    with runtime.GlobalExecutionLock(root) as lock:
        receipt = runtime.ensure_backup_receipt(point, lock)
        assert point.state() == "BLOCKED_PROVENANCE"
        assert runtime.ensure_backup_receipt(point, lock) == receipt
    rendered = runtime.render_public(
        runtime.public_status("c0b2-nonce-tamper", benchmark_root=root))
    assert changed["key_hex"] not in rendered and "run_nonce_key" not in rendered
    point.close()


def test_nonce_tamper_at_other_terminal_refuses_receipt(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-terminal-nonce-tamper"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    runtime.finish_public_run_failure(point, terminal="ABANDONED")
    changed = {"version": "c0b2-run-nonce-key-v1", "key_hex": "cd" * 32}
    point.conn.execute(
        "UPDATE manifests SET manifest_hash=?,manifest_json=? WHERE name='run_nonce_key'",
        (runtime.sha256_json(changed), runtime.canonical_json(changed)))
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="does not reproduce"):
            runtime.ensure_backup_receipt(point, lock)
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    point.close()


def test_partial_initializing_recovery_checks_present_evidence_tables(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-partial-initializing-evidence"
    root, stage = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=False)
    conn = sqlite3.connect(stage / "checkpoint.sqlite3")
    try:
        conn.execute("DROP TABLE backup_receipts")
        conn.execute("INSERT INTO events(kind,detail_json,created) VALUES('evil','{}',1)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime.CheckpointError, match="schema"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert stage.exists()


@pytest.mark.parametrize("statement", [
    "INSERT INTO decisions VALUES('evil','C','a','b','ACTIVATED','{}',1)",
    "INSERT INTO stage_aggregates VALUES('C','a','b','{}',1)",
    "INSERT INTO events(kind,detail_json,created) VALUES('evil','{}',1)",
    "INSERT INTO runtime_controls VALUES('evil','C','kind','a','{}','PENDING',"
    "NULL,NULL,NULL,1)",
    "INSERT INTO public_artifacts VALUES('evil','BLOCKED_PROVENANCE','a','{}',1)",
])
def test_final_initializing_discard_rejects_all_evidence_families(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, statement: str) -> None:
    run_id = f"c0b2-init-evidence-{runtime.sha256_json(statement)[:12]}"
    root, final = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=True)
    conn = sqlite3.connect(final / "checkpoint.sqlite3")
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime.CheckpointError, match="schema|content"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert final.exists()


def test_promote_rejects_named_database_replacement(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-promote-inode-swap"
    original = runtime.Checkpoint.promote
    displaced = tmp_path / "promote-original.sqlite3"

    def swap(point: runtime.Checkpoint, identity: str) -> None:
        point.path.rename(displaced)
        shutil.copy2(displaced, point.path)
        point.path.chmod(0o600)
        original(point, identity)

    monkeypatch.setattr(runtime.Checkpoint, "promote", swap)
    with pytest.raises(runtime.CheckpointError, match="pinned inode"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert displaced.exists()


def test_promote_quarantines_whole_directory_swap_at_rename(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-promote-directory-swap"
    original = checkpoint._rename_noreplace
    displaced = tmp_path / "promote-original-directory"

    def swap(parent_fd: int, source: str, target: str) -> None:
        source_path = root / "runs" / source
        if target == run_id:
            source_path.rename(displaced)
            shutil.copytree(displaced, source_path)
        original(parent_fd, source, target)

    monkeypatch.setattr(checkpoint, "_rename_noreplace", swap)
    with pytest.raises(runtime.CheckpointError, match="directory identity changed"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert displaced.exists()
    assert not (root / "runs" / run_id).exists()
    assert len(tuple((root / "runs").glob(".c0b2-quarantine-*"))) == 1


def test_discard_rejects_named_database_replacement(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-discard-inode-swap"
    root, final = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=True)
    point = runtime.Checkpoint.open(final / "checkpoint.sqlite3", root)
    displaced = tmp_path / "discard-original.sqlite3"
    try:
        point.path.rename(displaced)
        shutil.copy2(displaced, point.path)
        point.path.chmod(0o600)
        with pytest.raises(runtime.CheckpointError, match="pinned inode"):
            point.discard_initializing(run_id)
    finally:
        point.close()
    assert final.exists() and displaced.exists()


def test_receipt_reverifies_snapshot_after_database_commit(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-receipt-post-commit-verify")

    def relax(path: Path, _receipt: dict[str, Any]) -> None:
        path.chmod(0o400)

    monkeypatch.setattr(runtime, "_post_receipt_commit_hook", relax)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="identity changed"):
            runtime.ensure_backup_receipt(point, lock)
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 1
    point.close()


def test_backup_directory_creation_fsyncs_run_directory(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-backup-parent-fsync")
    run_identity = (point.path.parent.stat().st_dev, point.path.parent.stat().st_ino)
    original = runtime.os.fsync
    synced: list[tuple[int, int]] = []

    def record(fd: int) -> None:
        st = runtime.os.fstat(fd)
        synced.append((st.st_dev, st.st_ino))
        original(fd)

    monkeypatch.setattr(runtime.os, "fsync", record)
    with runtime.GlobalExecutionLock(root) as lock:
        runtime.ensure_backup_receipt(point, lock)
    assert run_identity in synced
    point.close()


def test_prepared_create_retry_requires_current_exact_header(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-header-drift"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    original = runtime._source_pins

    def changed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        value = original(*args, **kwargs)
        return {**value, "git_head": "f" * 40}

    monkeypatch.setattr(runtime, "_source_pins", changed)
    with pytest.raises(runtime.RuntimeGateError, match="create retry refuses"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)


def test_checkpoint_reopen_rejects_relaxed_modes_without_repair(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-reopen-modes"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    db = runtime._checkpoint_path(run_id, root)
    run_dir = db.parent
    run_dir.chmod(0o750)
    with pytest.raises(PermissionError, match="0700"):
        runtime.Checkpoint.open(db, root)
    assert run_dir.stat().st_mode & 0o777 == 0o750
    run_dir.chmod(0o700)
    db.chmod(0o640)
    with pytest.raises(PermissionError, match="0600"):
        runtime.Checkpoint.open(db, root)
    assert db.stat().st_mode & 0o777 == 0o640


def test_initial_snapshot_retry_rejects_relaxed_mode_without_repair(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-snapshot-mode"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    snapshot = next((root / "snapshots" / run_id).iterdir())
    snapshot.chmod(0o640)
    with pytest.raises(PermissionError, match="0600"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert snapshot.stat().st_mode & 0o777 == 0o640


def test_nonce_rederive_binds_c_plan_to_master_manifest(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-plan-parent-tamper"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    try:
        point.conn.execute("UPDATE plans SET parent_hash=? WHERE stage='C'", ("f" * 64,))
        with pytest.raises(runtime.RuntimeGateError, match="does not reproduce"):
            runtime._run_nonce_key(point)
    finally:
        point.close()


def test_existing_initial_snapshot_is_fsynced_before_retry_success(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-snapshot-retry-fsync"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    snapshot = next((root / "snapshots" / run_id).iterdir())
    snapshot_identity = (snapshot.stat().st_dev, snapshot.stat().st_ino)
    original = runtime.os.fsync
    synced: list[tuple[int, int]] = []

    def record(fd: int) -> None:
        st = runtime.os.fstat(fd)
        synced.append((st.st_dev, st.st_ino))
        original(fd)

    monkeypatch.setattr(runtime.os, "fsync", record)
    assert runtime.create_public_run(benchmark_root=root, run_id=run_id) == run_id
    assert snapshot_identity in synced


@pytest.mark.parametrize("swap", ["directory", "database"])
def test_create_cleanup_refuses_replaced_staging_identity(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, swap: str) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", f"c0b2-create-cleanup-{swap}"
    original = runtime.Checkpoint._fsync_file_and_parent
    displaced = tmp_path / f"displaced-{swap}"
    replacement: Path | None = None

    def replace(path: Path) -> None:
        nonlocal replacement
        if not path.parent.name.startswith(".c0b2-initializing-"):
            original(path)
            return
        if swap == "directory":
            path.parent.rename(displaced)
            path.parent.mkdir(mode=0o700)
            replacement = path
        else:
            path.rename(displaced)
            replacement = path
        replacement.write_bytes(b"replacement")
        replacement.chmod(0o600)
        raise OSError("create cut after replacement")

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", replace)
    with pytest.raises((PermissionError, runtime.CheckpointError)):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert replacement is not None and replacement.read_bytes() == b"replacement"
    assert displaced.exists()


def test_create_cleanup_quarantines_directory_swapped_during_rename(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-cleanup-rename-swap"
    original_fsync = runtime.Checkpoint._fsync_file_and_parent
    original_rename = checkpoint._rename_noreplace
    displaced = tmp_path / "cleanup-original-directory"

    def fail_after_fsync(path: Path) -> None:
        original_fsync(path)
        raise OSError("force cleanup")

    def swap(parent_fd: int, source: str, target: str) -> None:
        source_path = root / "runs" / source
        if target.startswith(".c0b2-quarantine-"):
            source_path.rename(displaced)
            shutil.copytree(displaced, source_path)
        original_rename(parent_fd, source, target)

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", fail_after_fsync)
    monkeypatch.setattr(checkpoint, "_rename_noreplace", swap)
    with pytest.raises(runtime.CheckpointError, match="changed during quarantine"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert displaced.exists()
    retained = tuple((root / "runs").glob(".c0b2-quarantine-*"))
    assert len(retained) == 1
    assert retained[0].stat().st_mode & 0o777 == 0o700
    assert (retained[0] / "checkpoint.sqlite3").exists()
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


def test_create_cleanup_retains_file_swapped_during_quarantine(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-cleanup-file-swap"
    original_fsync = runtime.Checkpoint._fsync_file_and_parent
    original_rename = checkpoint._rename_noreplace
    displaced = tmp_path / "cleanup-original.sqlite3"

    def fail_after_fsync(path: Path) -> None:
        original_fsync(path)
        raise OSError("force cleanup")

    def swap(parent_fd: int, source: str, target: str) -> None:
        source_db = root / "runs" / source / "checkpoint.sqlite3"
        if target.startswith(".c0b2-quarantine-"):
            source_db.rename(displaced)
            source_db.write_bytes(b"retained replacement")
            source_db.chmod(0o600)
        original_rename(parent_fd, source, target)

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", fail_after_fsync)
    monkeypatch.setattr(checkpoint, "_rename_noreplace", swap)
    with pytest.raises(OSError, match="force cleanup"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    retained = tuple((root / "runs").glob(".c0b2-quarantine-*"))
    assert len(retained) == 1 and displaced.exists()
    retained_db = retained[0] / "checkpoint.sqlite3"
    assert retained_db.read_bytes() == b"retained replacement"
    assert retained_db.stat().st_mode & 0o777 == 0o600
    assert not tuple((root / "runs").glob(f".c0b2-initializing-{run_id}-*"))


def test_create_success_validation_rejects_byte_identical_db_swap(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-create-success-inode-swap"
    original = runtime.Checkpoint._fsync_file_and_parent
    displaced = tmp_path / "create-success-original.sqlite3"

    def replace(path: Path) -> None:
        original(path)
        if path.parent.name.startswith(".c0b2-initializing-"):
            path.rename(displaced)
            shutil.copy2(displaced, path)
            path.chmod(0o600)

    monkeypatch.setattr(runtime.Checkpoint, "_fsync_file_and_parent", replace)
    with pytest.raises(runtime.CheckpointError, match="changed"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert displaced.exists()


def test_promote_reopen_rejects_byte_identical_named_db_swap(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-promote-reopen-swap"
    original = runtime.Checkpoint._configure
    displaced = tmp_path / "promote-reopen-original.sqlite3"
    calls = 0

    def replace(conn: sqlite3.Connection, mode: str) -> None:
        nonlocal calls
        original(conn, mode)
        calls += 1
        if calls == 2:
            path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
            path.rename(displaced)
            shutil.copy2(displaced, path)
            path.chmod(0o600)

    monkeypatch.setattr(runtime.Checkpoint, "_configure", staticmethod(replace))
    with pytest.raises(runtime.CheckpointError, match="changed after reopen|pinned inode"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert calls == 2 and displaced.exists()


@pytest.mark.parametrize("after_promotion", [False, True])
@pytest.mark.parametrize(
    "mutation", ["cursor", "limits", "missing_table", "extra_table"])
def test_initializing_recovery_requires_exact_base_shape_and_cursor(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
        after_promotion: bool) -> None:
    run_id = f"c0b2-initializing-{mutation}-{after_promotion}"
    root, stage = _leave_initializing(
        monkeypatch, tmp_path, run_id, after_promotion=after_promotion)
    identity = (stage.stat().st_dev, stage.stat().st_ino)
    conn = sqlite3.connect(stage / "checkpoint.sqlite3")
    try:
        if mutation == "cursor":
            conn.execute(
                "UPDATE runtime_cursor SET active_stage='D',active_plan_key='D1_OUTPUT'")
        elif mutation == "limits":
            conn.execute("UPDATE stage_limits SET hard_cap=hard_cap+1 WHERE stage='C'")
        elif mutation == "missing_table":
            conn.execute("DROP TABLE work_items")
        else:
            conn.execute("CREATE TABLE unexpected_evidence(value TEXT)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(runtime.CheckpointError, match="schema|content"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert stage.exists()
    assert (stage.stat().st_dev, stage.stat().st_ino) == identity


def test_initial_snapshot_pinned_fsync_rejects_named_inode_swap(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-snapshot-fsync-inode"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    snapshot = next((root / "snapshots" / run_id).iterdir())
    identity = (snapshot.stat().st_dev, snapshot.stat().st_ino)
    displaced = tmp_path / "displaced-initial-snapshot.sqlite3"
    original = runtime.os.fsync
    swapped = False

    def replace(fd: int) -> None:
        nonlocal swapped
        st = runtime.os.fstat(fd)
        if not swapped and (st.st_dev, st.st_ino) == identity:
            snapshot.rename(displaced)
            shutil.copy2(displaced, snapshot)
            snapshot.chmod(0o600)
            swapped = True
        original(fd)

    monkeypatch.setattr(runtime.os, "fsync", replace)
    with pytest.raises(runtime.CheckpointError, match="pinned fsync"):
        runtime.create_public_run(benchmark_root=root, run_id=run_id)
    assert swapped and snapshot.exists() and displaced.exists()


def test_backup_receipt_recovers_both_crash_windows_without_reusing_orphan(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-backup-crash-windows")
    with runtime.GlobalExecutionLock(root) as lock:
        monkeypatch.setattr(
            runtime, "_pre_receipt_commit_hook",
            lambda *_args: (_ for _ in ()).throw(OSError("pre-receipt crash")))
        with pytest.raises(OSError, match="pre-receipt crash"):
            runtime.ensure_backup_receipt(point, lock)
        assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
        orphan = tuple((point.path.parent / "backups").glob("snapshot-*.sqlite3"))
        assert len(orphan) == 1

        monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", lambda *_args: None)
        monkeypatch.setattr(
            runtime, "_receipt_return_hook",
            lambda *_args: (_ for _ in ()).throw(OSError("post-receipt crash")))
        with pytest.raises(OSError, match="post-receipt crash"):
            runtime.ensure_backup_receipt(point, lock)
        snapshots = tuple((point.path.parent / "backups").glob("snapshot-*.sqlite3"))
        assert len(snapshots) == 2 and orphan[0] in snapshots
        assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 1

        monkeypatch.setattr(runtime, "_receipt_return_hook", lambda *_args: None)
        receipt = runtime.ensure_backup_receipt(point, lock)
        assert runtime.ensure_backup_receipt(point, lock) == receipt
        assert len(tuple((point.path.parent / "backups").glob(
            "snapshot-*.sqlite3"))) == 2

        changed = runtime._current_backup_anchor(point)
        changed["charged_call_total"] += 1
        with pytest.raises(runtime.RuntimeGateError, match="differs"):
            runtime.ensure_backup_receipt(point, lock, changed)
    point.close()


@pytest.mark.parametrize("swap", ["parent", "leaf"])
def test_backup_receipt_rejects_parent_or_leaf_replacement_before_commit(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, swap: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-backup-swap-{swap}")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)

    def replace(path: Path, _receipt: dict[str, Any]) -> None:
        if swap == "parent":
            moved = path.parent.with_name("backups-original")
            path.parent.rename(moved)
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            moved = path.with_suffix(".original")
            path.rename(moved)
            path.symlink_to(outside / "replacement.sqlite3")

    monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", replace)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises((OSError, PermissionError, runtime.RuntimeGateError)):
            runtime.ensure_backup_receipt(point, lock)
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    assert not tuple(outside.iterdir())
    point.close()


def test_backup_creation_stays_on_pinned_directory_and_rejects_parent_swap(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-backup-create-parent-swap")
    outside = tmp_path / "outside-create"
    outside.mkdir(mode=0o700)
    original_open = runtime.os.open
    swapped = False

    def racing_open(path: Any, flags: int, mode: int = 0o777,
                    *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if (not swapped and dir_fd is not None and isinstance(path, str)
                and path.startswith("snapshot-")):
            backup = point.path.parent / "backups"
            backup.rename(point.path.parent / "backups-original")
            backup.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(runtime.os, "open", racing_open)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="backup"):
            runtime.ensure_backup_receipt(point, lock)
    assert swapped and not tuple(outside.iterdir())
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize("swap", ["parent", "leaf"])
def test_backup_receipt_rejects_byte_identical_inode_replacement_before_commit(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, swap: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-backup-identical-{swap}")
    changed_inode = False

    def replace(path: Path, _receipt: dict[str, Any]) -> None:
        nonlocal changed_inode
        original = path.stat()
        if swap == "parent":
            moved = path.parent.with_name("backups-original")
            path.parent.rename(moved)
            path.parent.mkdir(mode=0o700)
            shutil.copyfile(moved / path.name, path)
        else:
            moved = path.with_suffix(".original")
            path.rename(moved)
            shutil.copyfile(moved, path)
        path.chmod(0o600)
        changed_inode = path.stat().st_ino != original.st_ino

    monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", replace)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="pinned inode"):
            runtime.ensure_backup_receipt(point, lock)
    assert changed_inode
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize(
    "mutation", ["terminal", "stage", "plan", "count", "control"])
def test_backup_anchor_rejects_coherent_failure_cross_record_tampering(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-failure-cross-{mutation}")
    runtime.finish_public_run_failure(point, terminal="ABANDONED")
    rows = point.conn.execute(
        "SELECT artifact_id,artifact_json FROM public_artifacts").fetchall()
    evidence_id, evidence = next(
        (identity, json.loads(raw)) for identity, raw in rows if "evidence-v1" in raw)
    artifact_id, artifact = next(
        (identity, json.loads(raw)) for identity, raw in rows if "c0b2-failure-v1" in raw)
    if mutation == "terminal":
        evidence.update(terminal="BLOCKED_PROVENANCE",
                        reason_code="provenance_identity_failure")
        artifact.update(terminal="BLOCKED_PROVENANCE",
                        reason="provenance_identity_failure")
    elif mutation == "stage":
        evidence.update(stage="D", plan_key="D1_OUTPUT")
        artifact["stage"] = "D"
    elif mutation == "plan":
        evidence["plan_key"] = None
    elif mutation == "control":
        evidence["control_id"] = "f" * 64
    else:
        artifact["charged_call_total"] += 1
    evidence_hash = runtime.sha256_json(evidence)
    artifact["evidence_sha256"] = evidence_hash
    point.conn.execute(
        "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? WHERE artifact_id=?",
        (runtime.canonical_json(evidence), evidence_hash, evidence_id))
    point.conn.execute(
        "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? WHERE artifact_id=?",
        (runtime.canonical_json(artifact), runtime.sha256_json(artifact), artifact_id))
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError):
            runtime.ensure_backup_receipt(point, lock)
    point.close()


def test_result_artifact_must_match_authoritative_aggregate(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-result-aggregate-cross")
    expected, wrong = "a" * 64, "b" * 64
    artifact = {"version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "D", "aggregate_sha256": wrong,
                "reason": "no_d1_output_budget_survivor"}
    artifact_hash = runtime.freeze_public_artifact(point, "result", artifact)
    completion = {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
                  "facts": {"deterministic_stop": True,
                            "reason": "no_d1_output_budget_survivor"}}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("c0b2-completion", "D", "c" * 64, expected, "NOT_ACTIVATED",
         runtime.canonical_json(completion), 1.0))
    with pytest.raises(runtime.RuntimeGateError, match="aggregate"):
        runtime._stored_public_artifact(
            point.conn, "INCONCLUSIVE", active_stage="D",
            active_plan_key="D1_OUTPUT", active_plan_hash="c" * 64,
            aggregate_hash=expected, charged_total=0)
    point.close()


def test_completion_decision_must_match_active_plan_parent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-completion-parent-cross")
    aggregate_hash, active_plan_hash = "a" * 64, "b" * 64
    artifact = {"version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "D", "aggregate_sha256": aggregate_hash,
                "reason": "no_d1_output_budget_survivor"}
    artifact_hash = runtime.freeze_public_artifact(point, "result", artifact)
    completion = {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
                  "facts": {"deterministic_stop": True,
                            "reason": "no_d1_output_budget_survivor"}}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("c0b2-completion", "D", "c" * 64, aggregate_hash, "NOT_ACTIVATED",
         runtime.canonical_json(completion), 1.0))
    with pytest.raises(runtime.RuntimeGateError, match="completion decision"):
        runtime._stored_public_artifact(
            point.conn, "INCONCLUSIVE", active_stage="D",
            active_plan_key="D1_OUTPUT", active_plan_hash=active_plan_hash,
            aggregate_hash=aggregate_hash, charged_total=0)
    point.close()


@pytest.mark.parametrize("damage", ["deleted", "corrupt", "symlink_dir"])
def test_public_verify_detects_missing_corrupt_or_symlinked_receipt_snapshot(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, damage: str) -> None:
    run_id = f"c0b2-backup-{damage}"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    with runtime.GlobalExecutionLock(root) as lock:
        receipt = runtime.ensure_backup_receipt(point, lock)
    snapshot = runtime._receipt_snapshot_path(
        point.path.parent, receipt["snapshot_run_relative_path"])
    point.close()
    if damage == "deleted":
        snapshot.unlink()
    elif damage == "corrupt":
        snapshot.write_bytes(b"not sqlite")
        snapshot.chmod(0o600)
    else:
        backup_root = snapshot.parent
        moved = backup_root.with_name("backups-real")
        backup_root.rename(moved)
        backup_root.symlink_to(moved, target_is_directory=True)
    result = runtime.public_verify(run_id, benchmark_root=root)
    assert result["ok"] is False
    assert any(error.startswith("backup_snapshot_invalid:")
               for error in result["errors"])


@pytest.mark.parametrize("mutation", ["receipt_link", "anchor_row"])
def test_public_verify_reports_receipt_anchor_tampering_without_crashing(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    run_id = f"c0b2-backup-anchor-{mutation}"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    with runtime.GlobalExecutionLock(root) as lock:
        runtime.ensure_backup_receipt(point, lock)
    row = point.conn.execute(
        "SELECT anchor_hash,anchor_json,receipt_json FROM backup_receipts"
    ).fetchone()
    if mutation == "receipt_link":
        value = json.loads(row[2])
        value["anchor_sha256"] = "f" * 64
        point.conn.execute(
            "UPDATE backup_receipts SET receipt_json=?,receipt_hash=?",
            (runtime.canonical_json(value), runtime.sha256_json(value)))
    else:
        value = json.loads(row[1])
        value["charged_call_total"] += 1
        point.conn.execute(
            "UPDATE backup_receipts SET anchor_json=?",
            (runtime.canonical_json(value),))
    point.close()
    result = runtime.public_verify(run_id, benchmark_root=root)
    assert result["ok"] is False
    assert any(error.startswith("backup_anchor_invalid:")
               for error in result["errors"])


def test_live_source_pin_revalidation_fails_closed(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    current = {"git_head": "a" * 40, "ollama_version": runtime.OLLAMA_VERSION}
    monkeypatch.setattr(runtime, "capture_worktree_seal", lambda _root: object())
    monkeypatch.setattr(runtime, "_source_pins", lambda _root, _seal: current)

    runtime.revalidate_source_pins(current, repo_root=tmp_path)
    with pytest.raises(runtime.RuntimeGateError, match="ollama_version"):
        runtime.revalidate_source_pins(
            {**current, "ollama_version": "changed"}, repo_root=tmp_path)


def test_public_stage_c_runs_exact_plan_and_finalizes_no_survivor(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise the whole runtime with a bounded fake, never a network client."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    run_id = runtime.create_public_run(
        benchmark_root=tmp_path / "bench", run_id="c0b2-test-public")
    calls: list[str] = []

    class FakeTransport:
        def __init__(self, resolver):
            self.resolver = resolver

        def cancel_current(self) -> None:
            return None

        def __call__(self, request, _cancel):
            spec = self.resolver(request)
            calls.append(spec.kind)
            if spec.kind != "chat":
                return executor.FakeResponse("{}")
            if spec.worksheet == "v1":
                value = {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings",
                    "categories": [
                        {"category": category, "present": False, "evidence": []}
                        for category in ("pii", "financial", "contact", "demographic")
                    ],
                }
            else:
                value = {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings", "findings": [],
                }
            return executor.FakeResponse(
                json.dumps(value, sort_keys=True, separators=(",", ":")),
                metadata={
                    "done_reason": "stop", "tools_empty": True,
                    "images_empty": True, "unknown_message_fields_empty": True,
                    "strict_schema_invalid": False,
                    "semantic_invalid": False,
                })

    result = runtime.run_public_stage_c(
        run_id, benchmark_root=tmp_path / "bench",
        transport_factory=lambda resolver, _header: FakeTransport(resolver))
    assert result == {
        "run_id": run_id, "stage": "C", "state": "INCONCLUSIVE",
        "calls_total": 272, "survivor_count": 0,
    }
    status = runtime.public_status(run_id, benchmark_root=tmp_path / "bench")
    assert status["state"] == "INCONCLUSIVE" and status["calls_total"] == 272
    assert status["backup"]["required"] is True
    assert status["backup"]["receipt_present"] is True
    assert len(status["backup"]["anchor_sha256"]) == 64
    assert len(status["backup"]["snapshot_sha256"]) == 64
    snapshots = list((tmp_path / "bench" / "runs" / run_id / "backups").glob(
        "snapshot-*.sqlite3"))
    assert len(snapshots) == 1
    assert runtime.status_readonly(snapshots[0])["state"] == "INCONCLUSIVE"
    assert calls.count("chat") == 264
    assert calls.count("ps") == 3
    assert calls.count("version") == 1
    assert calls.count("tags") == 1
    assert calls.count("show") == 3
    verification = runtime.public_verify(run_id, benchmark_root=tmp_path / "bench")
    assert verification == {
        "ok": True, "errors": [],
        **{key: status[key] for key in (
            "benchmark_protocol_id", "policy_id", "policy_sha256")},
        "backup": status["backup"],
    }
    calls_before_reentry = list(calls)
    reentered = runtime.run_public_stage_c(
        run_id, benchmark_root=tmp_path / "bench",
        transport_factory=lambda *_args: pytest.fail(
            "Stage-C terminal re-entry must not construct transport"))
    assert reentered["state"] == "INCONCLUSIVE"
    assert calls == calls_before_reentry


def test_runtime_specs_cross_the_real_bounded_transport_offline(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prove runtime-built specs against the adapter using fake HTTP only."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-transport-seam")

    class Raw:
        def __init__(self, body: bytes):
            self.body = body

        def stream(self, *, amt: int, decode_content: bool):
            assert amt == 64 * 1024 and decode_content is False
            yield self.body

    class Response:
        def __init__(self, body: bytes, content_type: str):
            self.status_code = 200
            self.headers = {"Content-Type": content_type}
            self.raw = Raw(body)

        def close(self) -> None:
            return None

    class Session:
        def __init__(self):
            self.trust_env = True
            self.max_redirects = 30
            self.active_model = ""
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any):
            self.calls += 1
            assert kwargs["allow_redirects"] is False
            assert kwargs["proxies"] == {"http": None, "https": None}
            path = url.removeprefix(runtime.OLLAMA_ENDPOINT)
            if path == "/api/chat":
                payload = kwargs["json"]
                self.active_model = payload["model"]
                answer = ({
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings",
                    "categories": [
                        {"category": category, "present": False, "evidence": []}
                        for category in ("pii", "financial", "contact", "demographic")
                    ],
                } if "categories" in payload["format"]["properties"] else {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings", "findings": [],
                })
                frame = {
                    "model": self.active_model,
                    "message": {"role": "assistant", "content": json.dumps(
                        answer, sort_keys=True, separators=(",", ":"))},
                    "done": True, "done_reason": "stop",
                }
                return Response(
                    json.dumps(frame, separators=(",", ":")).encode() + b"\n",
                    "application/x-ndjson")
            if path == "/api/version":
                value = {"version": runtime.OLLAMA_VERSION}
            elif path == "/api/tags":
                value = {"models": [
                    {"name": model, "model": model, "digest": digest}
                    for model, digest, _think in plan.MODELS]}
            elif path == "/api/show":
                value = {"capabilities": [], "details": {}, "model_info": {}}
            elif path == "/api/ps":
                digest = next(row[1] for row in plan.MODELS
                              if row[0] == self.active_model)
                value = {"models": [{
                    "name": self.active_model, "model": self.active_model,
                    "digest": digest, "size": 1, "size_vram": 0,
                    "context_length": 8192,
                }]}
            else:  # pragma: no cover - exact resolver set is asserted above
                raise AssertionError(path)
            return Response(
                json.dumps(value, separators=(",", ":")).encode(),
                "application/json")

    session = Session()
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda resolver, header: transport.BoundedOllamaTransport(
            resolver, endpoint=header["ollama_endpoint"], session=session))
    assert result["state"] == "INCONCLUSIVE"
    assert result["calls_total"] == session.calls == 272


def test_first_signal_before_recovery_consumes_no_invocation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-early-signal")
    original = executor.DurableExecutor.recover_and_start

    def interrupt_before_recovery(self, stage):
        signal.raise_signal(signal.SIGINT)
        return original(self, stage)

    class NoCallTransport:
        def cancel_current(self) -> None:
            return None

        def __call__(self, *_args):
            pytest.fail("an early signal must prevent transport contact")

    constructed: list[str] = []

    def factory(*_args):
        constructed.append("transport")
        return NoCallTransport()

    monkeypatch.setattr(
        executor.DurableExecutor, "recover_and_start", interrupt_before_recovery)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=factory)
    assert result["state"] == "CANCELLED_PENDING_RESUME"
    assert result["calls_total"] == 0 and constructed == []
    conn = sqlite3.connect(runtime._checkpoint_path(run_id, root))
    try:
        assert conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 0
    finally:
        conn.close()


def test_live_signal_guard_only_publishes_first_signal_intent() -> None:
    cancellation = executor.CancellationController()
    guard = runtime._LiveSignalGuard(cancellation)

    guard._handle(signal.SIGINT, None)

    assert cancellation.event.is_set()
    assert cancellation.forced is False
    assert guard.count == 1


def test_live_signal_guard_cannot_deadlock_on_event_condition() -> None:
    code = """
import signal
from scripts.analyst_benchmark.c0b2_executor import CancellationController
from scripts.analyst_benchmark.c0b2_runtime import _LiveSignalGuard
cancellation = CancellationController()
guard = _LiveSignalGuard(cancellation)
with cancellation.event._cond:
    guard._handle(signal.SIGINT, None)
assert cancellation.event.is_set() and not cancellation.forced
with cancellation.event._cond:
    try:
        guard._handle(signal.SIGINT, None)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("second signal did not force interruption")
assert cancellation.event.is_set() and cancellation.forced
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).parents[2],
        capture_output=True, text=True, timeout=2, check=False)
    assert result.returncode == 0, result.stderr


def test_signal_publication_satisfies_event_wait_contract() -> None:
    immediate = executor.CancellationController()
    immediate.publish_first_signal()
    assert immediate.event.is_set() is True
    assert immediate.event.wait(0) is True

    blocked = executor.CancellationController()
    observed: list[bool] = []
    entered = threading.Event()

    def wait_for_signal() -> None:
        entered.set()
        observed.append(blocked.event.wait(1))

    waiter = threading.Thread(target=wait_for_signal)
    waiter.start()
    assert entered.wait(0.25)
    blocked.publish_first_signal()
    waiter.join(0.25)
    assert not waiter.is_alive()
    assert observed == [True]


def test_first_signal_during_invocation_claim_consumes_no_invocation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The in-transaction claim guard closes the signal/INSERT race."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-claim-signal")
    original = runtime.Checkpoint.claim_invocation

    def interrupt_during_claim(
            self, stage, *, claim_guard=None, budget_failure_callback=None):
        first_guard = True

        def guarded_claim():
            nonlocal first_guard
            if first_guard:
                first_guard = False
                signal.raise_signal(signal.SIGINT)
            assert claim_guard is not None
            claim_guard()

        return original(
            self, stage, claim_guard=guarded_claim,
            budget_failure_callback=budget_failure_callback)

    class NoCallTransport:
        def cancel_current(self) -> None:
            return None

        def __call__(self, *_args):
            pytest.fail("a signal during claim must prevent transport contact")

    monkeypatch.setattr(
        runtime.Checkpoint, "claim_invocation", interrupt_during_claim)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda *_args: NoCallTransport())
    assert result["state"] == "CANCELLED_PENDING_RESUME"
    assert result["calls_total"] == 0
    conn = sqlite3.connect(runtime._checkpoint_path(run_id, root))
    try:
        assert conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 0
    finally:
        conn.close()
