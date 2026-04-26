"""Deterministic seeded fuzz tests for server-ops running-task invariants."""

from __future__ import annotations

import random

import pytest

from gui.utils.dirracuda_loader import load_dirracuda_module

from gui.utils.running_tasks import (
    _reset_running_task_registry_for_tests,
    get_running_task_registry,
)

from gui.tests._server_ops_harness import (
    BatchStatusHarness,
    FakeDialog,
    JobRecord,
    WrappedDialog,
    create_active_job,
    make_dashboard_scan_task_stub,
    make_main_app_stub,
    patch_main_shutdown_helpers,
)

main_module = load_dirracuda_module()


def setup_function() -> None:
    _reset_running_task_registry_for_tests()


def _active_job_id(harness: BatchStatusHarness):
    if not harness.active_jobs:
        return None
    return list(harness.active_jobs.keys())[-1]


def _assert_registry_consistency(harness: BatchStatusHarness) -> None:
    registry = get_running_task_registry()
    task_ids = []
    for job in harness.active_jobs.values():
        task_id = job.get("task_id")
        assert task_id, "active job must have a registered task id"
        snapshot = registry.get_task(task_id)
        assert snapshot is not None, "active job task id missing from registry"
        assert snapshot.reopen_callback is not None
        assert snapshot.cancel_callback is not None
        task_ids.append(task_id)
    assert registry.count() == len(task_ids)


def _run_sequence(seed: int, *, steps: int) -> None:
    rng = random.Random(seed)
    harness = BatchStatusHarness()
    registry = get_running_task_registry()
    created = 0

    for _ in range(steps):
        job_id = _active_job_id(harness)
        action = rng.choice(["start", "hide", "reopen", "cancel", "progress", "finish"])

        if action == "start" and job_id is None:
            created += 1
            job_type = rng.choice(["probe", "extract", "pry"])
            total = rng.randint(1, 5)
            dialog = WrappedDialog() if rng.random() < 0.35 else FakeDialog()
            job_name = f"{job_type}-{seed}-{created}"
            create_active_job(
                harness,
                JobRecord(job_id=job_name, job_type=job_type, total=total),
                dialog=dialog,
            )
            harness._register_batch_running_task(job_name)
            _assert_registry_consistency(harness)
            continue

        if job_id is None:
            assert registry.count() == 0
            continue

        job = harness.active_jobs[job_id]
        task = registry.get_task(job["task_id"])
        assert task is not None

        if action == "hide":
            dialog = job.get("dialog")
            if hasattr(dialog, "hide"):
                dialog.hide()
        elif action == "reopen":
            if task.reopen_callback:
                task.reopen_callback()
        elif action == "cancel":
            if task.cancel_callback:
                task.cancel_callback()
        elif action == "progress":
            total = int(job.get("total") or 0)
            completed = int(job.get("completed") or 0)
            step = rng.randint(0, 2)
            completed = min(total, completed + step)
            job["completed"] = completed
            harness._update_batch_running_task(
                job_id,
                state="running",
                progress=f"{completed}/{total} targets",
            )
        elif action == "finish":
            job["completed"] = int(job.get("total") or 0)
            harness._finalize_batch_job(job_id, job.get("dialog"), show_summary=False)

        _assert_registry_consistency(harness)

    # Final cleanup so each fuzz run leaves a clean registry.
    for pending_job_id in list(harness.active_jobs.keys()):
        pending = harness.active_jobs[pending_job_id]
        pending["completed"] = int(pending.get("total") or 0)
        harness._finalize_batch_job(pending_job_id, pending.get("dialog"), show_summary=False)

    assert registry.count() == 0


def _assert_dashboard_scan_task_consistency(dash) -> None:
    registry = get_running_task_registry()
    task_id = getattr(dash, "_scan_task_id", None)
    if not task_id:
        return
    snapshot = registry.get_task(task_id)
    assert snapshot is not None, "scan task id must exist in registry when set"
    assert snapshot.task_type == "scan"
    assert snapshot.reopen_callback is not None
    assert snapshot.cancel_callback is not None


def _run_dashboard_scan_sequence(seed: int, *, steps: int) -> None:
    rng = random.Random(seed)
    dash, counters = make_dashboard_scan_task_stub()
    registry = get_running_task_registry()

    protocols_pool = [["smb"], ["ftp"], ["http"], ["smb", "ftp"], ["ftp", "http"]]

    for _ in range(steps):
        action = rng.choice(["queue", "run", "wait", "reopen", "cancel", "clear"])
        task_id = getattr(dash, "_scan_task_id", None)

        if action == "queue":
            protocols = rng.choice(protocols_pool)
            country = rng.choice(["US", "DE", None])
            dash._set_scan_task_queued(protocols, country=country)
        elif action == "run":
            protocol = rng.choice(["SMB", "FTP", "HTTP"])
            total = int(getattr(dash, "_queued_scan_total", 0) or 0)
            if total > 0:
                remaining = rng.randint(0, max(total - 1, 0))
                dash._queued_scan_protocols = [f"p{idx}" for idx in range(remaining)]
            dash._set_scan_task_running(protocol, country=rng.choice(["US", None]))
        elif action == "wait":
            if task_id:
                total = int(getattr(dash, "_queued_scan_total", 0) or 0)
                remaining = rng.randint(0, total) if total > 0 else 0
                dash._queued_scan_protocols = [f"p{idx}" for idx in range(remaining)]
                dash._set_scan_task_waiting_next()
        elif action == "reopen":
            if task_id:
                snapshot = registry.get_task(task_id)
                assert snapshot is not None
                if snapshot.reopen_callback:
                    snapshot.reopen_callback()
        elif action == "cancel":
            if task_id:
                snapshot = registry.get_task(task_id)
                assert snapshot is not None
                if snapshot.cancel_callback:
                    snapshot.cancel_callback()
        elif action == "clear":
            dash._clear_scan_task()

        _assert_dashboard_scan_task_consistency(dash)

    dash._clear_scan_task()
    assert registry.count() == 0
    assert counters["reopen_calls"] >= 0
    assert counters["interrupt_calls"] >= 0


def _run_dashboard_scan_close_sequence(seed: int, *, steps: int, monkeypatch) -> None:
    rng = random.Random(seed)
    dash, counters = make_dashboard_scan_task_stub()
    app = make_main_app_stub()
    app.dashboard = dash
    registry = get_running_task_registry()

    patch_main_shutdown_helpers(monkeypatch)
    tick = {"value": 0.0}
    close_response = {"value": True}

    def _fake_time():
        tick["value"] += 0.25
        return tick["value"]

    monkeypatch.setattr(main_module.time, "time", _fake_time)
    monkeypatch.setattr(main_module.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_module.messagebox,
        "askyesno",
        lambda *_a, **_k: close_response["value"],
    )

    protocols_pool = [["smb"], ["ftp"], ["http"], ["smb", "ftp"], ["ftp", "http"]]
    for _ in range(steps):
        action = rng.choice(
            [
                "queue",
                "run",
                "wait",
                "cancel_task",
                "clear",
                "close_confirm",
                "close_cancel",
                "finish",
            ]
        )

        if action == "queue":
            protocols = rng.choice(protocols_pool)
            dash._queued_scan_active = True
            dash._queued_scan_protocols = list(protocols)
            dash._set_scan_task_queued(protocols, country=rng.choice(["US", "DE", None]))
        elif action == "run":
            if dash._scan_task_id:
                dash.scan_manager.is_scanning = True
                total = int(getattr(dash, "_queued_scan_total", 0) or 0)
                if total > 0:
                    remaining = rng.randint(0, max(total - 1, 0))
                    dash._queued_scan_protocols = [f"p{idx}" for idx in range(remaining)]
                dash._set_scan_task_running(rng.choice(["SMB", "FTP", "HTTP"]), country=rng.choice(["US", None]))
        elif action == "wait":
            if dash._scan_task_id:
                dash.scan_manager.is_scanning = False
                total = int(getattr(dash, "_queued_scan_total", 0) or 0)
                remaining = rng.randint(0, total) if total > 0 else 0
                dash._queued_scan_protocols = [f"p{idx}" for idx in range(remaining)]
                dash._set_scan_task_waiting_next()
        elif action == "cancel_task":
            task_id = dash._scan_task_id
            if task_id:
                snapshot = registry.get_task(task_id)
                assert snapshot is not None
                if snapshot.cancel_callback:
                    snapshot.cancel_callback()
        elif action == "clear":
            dash.scan_manager.is_scanning = False
            dash._queued_scan_active = False
            dash._queued_scan_protocols = []
            dash._clear_scan_task()
        elif action == "close_confirm":
            close_response["value"] = True
            app._on_closing()
        elif action == "close_cancel":
            close_response["value"] = False
            app._on_closing()
        elif action == "finish":
            dash.scan_manager.is_scanning = False
            dash._queued_scan_active = False
            dash._queued_scan_protocols = []
            if dash._scan_task_id and rng.random() < 0.7:
                dash._clear_scan_task()

        task_id = getattr(dash, "_scan_task_id", None)
        if task_id:
            snapshot = registry.get_task(task_id)
            assert snapshot is not None, "scan task id must remain resolvable while active"
            assert snapshot.reopen_callback is not None
            assert snapshot.cancel_callback is not None

        assert registry.count() <= 1

    dash.scan_manager.is_scanning = False
    dash._queued_scan_active = False
    dash._queued_scan_protocols = []
    dash._clear_scan_task()
    assert registry.count() == 0
    assert counters["interrupt_calls"] >= 0


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", [7, 29, 101, 2026, 4099])
def test_fuzz_fast_running_task_sequence_invariants(seed: int) -> None:
    _run_sequence(seed, steps=80)


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", [13, 41, 73])
def test_fuzz_fast_dashboard_scan_task_sequences(seed: int) -> None:
    _run_dashboard_scan_sequence(seed, steps=90)


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", [23, 59, 211])
def test_fuzz_fast_dashboard_scan_close_sequences(seed: int, monkeypatch) -> None:
    _run_dashboard_scan_close_sequence(seed, steps=100, monkeypatch=monkeypatch)


@pytest.mark.fuzz_heavy
@pytest.mark.parametrize("seed", [3, 11, 19, 47, 97, 131, 257, 1021])
def test_fuzz_heavy_running_task_sequence_invariants(seed: int) -> None:
    _run_sequence(seed, steps=260)


@pytest.mark.fuzz_heavy
@pytest.mark.parametrize("seed", [5, 17, 31, 67, 149, 313])
def test_fuzz_heavy_dashboard_scan_task_sequences(seed: int) -> None:
    _run_dashboard_scan_sequence(seed, steps=320)


@pytest.mark.fuzz_heavy
@pytest.mark.parametrize("seed", [2, 37, 71, 307, 911])
def test_fuzz_heavy_dashboard_scan_close_sequences(seed: int, monkeypatch) -> None:
    _run_dashboard_scan_close_sequence(seed, steps=360, monkeypatch=monkeypatch)
