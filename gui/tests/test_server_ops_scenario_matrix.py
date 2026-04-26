"""Scenario-matrix tests for server-ops monitor and close behavior."""

from __future__ import annotations

import threading
import time
import types
from types import SimpleNamespace

import pytest

from gui.utils.dirracuda_loader import load_dirracuda_module
import gui.components.server_list_window.actions.batch_operations as batch_ops_module
import gui.components.se_dork_browser_window as se_dork_module
from gui.components.se_dork_browser_window import SeDorkBrowserWindow
from gui.utils.running_tasks import (
    _reset_running_task_registry_for_tests,
    get_running_task_registry,
)

from gui.tests._server_ops_harness import (
    AsyncFakeDialog,
    BatchStatusHarness,
    FakeButton,
    FakeSeDorkConnection,
    FakeSeDorkDialog,
    InlineExecutor,
    JobRecord,
    PryOperationsHarness,
    create_active_job,
    make_dashboard_batch_task_stub,
    make_dashboard_scan_task_stub,
    make_dashboard_tasks_stub,
    make_main_app_stub,
    make_se_dork_probe_window_stub,
    patch_dashboard_batch_dialog_stack,
    patch_main_shutdown_helpers,
)

main_module = load_dirracuda_module()


def setup_function() -> None:
    _reset_running_task_registry_for_tests()


@pytest.mark.scenario
def test_s1_probe_monitor_hide_reopen_and_finalize() -> None:
    registry = get_running_task_registry()
    harness = BatchStatusHarness()
    job = create_active_job(
        harness,
        JobRecord(job_id="probe-1", job_type="probe", total=2),
    )

    harness._register_batch_running_task("probe-1")
    task_id = job["task_id"]
    task = registry.get_task(task_id)
    assert task is not None
    assert registry.count() == 1

    dialog = job["dialog"]
    dialog.hide()
    assert dialog.hidden is True
    assert registry.count() == 1

    assert task.reopen_callback is not None
    task.reopen_callback()
    assert dialog.show_calls == 1
    assert harness.batch_status_dialog is dialog

    job["completed"] = job["total"]
    harness._finalize_batch_job("probe-1", dialog, show_summary=False)
    assert registry.count() == 0


@pytest.mark.scenario
def test_s2_extract_monitor_cancel_and_terminal_cleanup_idempotent() -> None:
    registry = get_running_task_registry()
    harness = BatchStatusHarness()
    job = create_active_job(
        harness,
        JobRecord(job_id="extract-1", job_type="extract", total=3),
    )

    harness._register_batch_running_task("extract-1")
    task_id = job["task_id"]
    task = registry.get_task(task_id)
    assert task is not None
    assert task.cancel_callback is not None

    task.cancel_callback()
    task.cancel_callback()

    assert job["cancel_event"].is_set()
    updated = registry.get_task(task_id)
    assert updated is not None
    assert updated.state == "cancelling"

    job["completed"] = job["total"]
    harness._finalize_batch_job("extract-1", job["dialog"], show_summary=False)
    assert registry.count() == 0


@pytest.mark.scenario
def test_s3_pry_mixed_selection_blocks_launch() -> None:
    selected_targets = [
        {"host_type": "S", "ip_address": "198.51.100.10"},
        {"host_type": "F", "ip_address": "198.51.100.20"},
    ]
    harness = PryOperationsHarness(selected_targets)

    warnings = []

    def _capture_warning(title, _body, **_kwargs):
        warnings.append(title)

    original = batch_ops_module.messagebox.showwarning
    batch_ops_module.messagebox.showwarning = _capture_warning
    try:
        harness._on_pry_selected()
    finally:
        batch_ops_module.messagebox.showwarning = original

    assert warnings == ["Pry Not Supported"]
    assert harness._started_jobs == []


@pytest.mark.scenario
def test_s4_running_task_count_sync_across_dashboard_and_server_list() -> None:
    registry = get_running_task_registry()

    server_harness = BatchStatusHarness()
    server_harness.running_tasks_button = FakeButton()
    server_harness._initialize_running_tasks_button()

    dashboard_harness = make_dashboard_tasks_stub()
    registry.subscribe(dashboard_harness._on_running_tasks_changed)

    try:
        assert server_harness.running_tasks_button._text == "Running Tasks (0)"
        assert server_harness.running_tasks_button._state == "disabled"
        assert dashboard_harness.running_tasks_button._text == "Running Tasks (0)"
        assert dashboard_harness.running_tasks_button._state == "disabled"

        task_id = registry.create_task(task_type="probe", name="Probe")
        assert server_harness.running_tasks_button._text == "Running Tasks (1)"
        assert server_harness.running_tasks_button._state == "normal"
        assert dashboard_harness.running_tasks_button._text == "Running Tasks (1)"
        assert dashboard_harness.running_tasks_button._state == "normal"

        registry.remove_task(task_id)
        assert server_harness.running_tasks_button._text == "Running Tasks (0)"
        assert server_harness.running_tasks_button._state == "disabled"
        assert dashboard_harness.running_tasks_button._text == "Running Tasks (0)"
        assert dashboard_harness.running_tasks_button._state == "disabled"
    finally:
        registry.unsubscribe(server_harness._on_running_tasks_changed)
        registry.unsubscribe(dashboard_harness._on_running_tasks_changed)


@pytest.mark.scenario
def test_s5_app_close_cancel_path_with_active_work(monkeypatch) -> None:
    patch_main_shutdown_helpers(monkeypatch)

    app = make_main_app_stub()
    state = {"cancel_called": 0}

    app.dashboard = SimpleNamespace(
        has_active_or_queued_work=lambda: True,
        request_cancel_active_or_queued_work=lambda: state.__setitem__(
            "cancel_called", state["cancel_called"] + 1
        ),
        teardown_dashboard_monitors=lambda: None,
    )

    monkeypatch.setattr(main_module.messagebox, "askyesno", lambda *a, **k: False)
    app._on_closing()

    assert state["cancel_called"] == 0
    assert app.root.destroy_calls == 0


@pytest.mark.scenario
def test_s6_app_close_confirm_path_with_active_work(monkeypatch) -> None:
    patch_main_shutdown_helpers(monkeypatch)

    app = make_main_app_stub()
    state = {
        "active": True,
        "cancel_called": 0,
        "teardown_called": 0,
    }

    def _has_active() -> bool:
        return state["active"]

    def _cancel() -> None:
        state["cancel_called"] += 1
        state["active"] = False

    def _teardown() -> None:
        state["teardown_called"] += 1

    app.dashboard = SimpleNamespace(
        has_active_or_queued_work=_has_active,
        request_cancel_active_or_queued_work=_cancel,
        force_terminate_active_work=lambda: None,
        teardown_dashboard_monitors=_teardown,
    )

    monkeypatch.setattr(main_module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(main_module.time, "sleep", lambda *_a, **_k: None)

    app._on_closing()

    assert state["cancel_called"] >= 1
    assert state["teardown_called"] == 1
    assert app.root.destroy_calls == 1


@pytest.mark.scenario
def test_s7_dashboard_scan_task_lifecycle_and_callbacks() -> None:
    registry = get_running_task_registry()
    dash, counters = make_dashboard_scan_task_stub()

    dash._set_scan_task_queued(["smb", "ftp"], country="US")
    task_id = dash._scan_task_id
    assert task_id is not None

    snapshot = registry.get_task(task_id)
    assert snapshot is not None
    assert snapshot.state == "queued"
    assert snapshot.progress == "0/2 protocols"
    assert snapshot.reopen_callback is not None
    assert snapshot.cancel_callback is not None

    snapshot.reopen_callback()
    snapshot.cancel_callback()
    assert counters["reopen_calls"] == 1
    assert counters["interrupt_calls"] == 1

    dash._queued_scan_protocols = ["FTP"]
    dash._set_scan_task_running("SMB", country="US")
    snapshot = registry.get_task(task_id)
    assert snapshot is not None
    assert snapshot.state == "running"
    assert snapshot.progress == "1/2 protocols"
    assert snapshot.name == "SMB Scan (US)"

    dash._queued_scan_protocols = []
    dash._set_scan_task_waiting_next()
    snapshot = registry.get_task(task_id)
    assert snapshot is not None
    assert snapshot.state == "queued"
    assert snapshot.progress == "2/2 protocols"

    dash._clear_scan_task()
    assert dash._scan_task_id is None
    assert registry.get_task(task_id) is None


@pytest.mark.scenario
def test_s12_dashboard_scan_cancel_idempotent_and_task_entry_not_duplicated() -> None:
    registry = get_running_task_registry()
    dash, counters = make_dashboard_scan_task_stub()

    def _interrupt():
        counters["interrupt_calls"] += 1
        dash.scan_manager.is_scanning = False

    dash.scan_manager.interrupt_scan = _interrupt
    dash.scan_manager.is_scanning = True
    dash._queued_scan_active = True
    dash._queued_scan_protocols = ["smb", "ftp"]

    dash._set_scan_task_queued(["smb", "ftp"], country="US")
    first_task_id = dash._scan_task_id
    assert first_task_id is not None
    assert registry.count() == 1

    dash._set_scan_task_queued(["smb", "ftp"], country="US")
    assert dash._scan_task_id == first_task_id
    assert registry.count() == 1

    dash.request_cancel_active_or_queued_work()
    dash.request_cancel_active_or_queued_work()

    assert counters["clear_queue_calls"] >= 1
    assert counters["interrupt_calls"] >= 1
    assert registry.count() == 1  # cancellation is callback-only until terminal cleanup

    dash._clear_scan_task()
    assert dash._scan_task_id is None
    assert registry.count() == 0


@pytest.mark.scenario
def test_s8_dashboard_post_scan_probe_monitor_task_lifecycle(monkeypatch) -> None:
    patch_dashboard_batch_dialog_stack(monkeypatch)
    registry = get_running_task_registry()
    release = threading.Event()
    dash, state = make_dashboard_batch_task_stub(probe_release_event=release)

    failures = []

    def _runner():
        try:
            dash._execute_batch_probe([{"ip_address": "198.51.100.50", "host_type": "S"}])
        except Exception as exc:  # pragma: no cover - debugging aid
            failures.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()

    deadline = time.time() + 1.5
    while registry.count() == 0 and time.time() < deadline:
        time.sleep(0.01)

    assert registry.count() == 1
    task = registry.list_tasks()[0]
    assert task.task_type == "probe"
    assert task.reopen_callback is not None
    assert task.cancel_callback is not None

    dialog = AsyncFakeDialog.created[-1]
    dialog.trigger_close()
    assert dialog.hidden is True

    task.reopen_callback()
    time.sleep(0.05)
    assert dialog.hidden is False

    task.cancel_callback()
    if state["probe_cancel_event"] is not None:
        deadline = time.time() + 1.0
        while not state["probe_cancel_event"].is_set() and time.time() < deadline:
            time.sleep(0.01)
        assert state["probe_cancel_event"].is_set() is True

    release.set()
    thread.join(timeout=2.0)
    assert failures == []
    assert registry.count() == 0


@pytest.mark.scenario
def test_s9_dashboard_post_scan_extract_monitor_task_lifecycle(monkeypatch) -> None:
    patch_dashboard_batch_dialog_stack(monkeypatch)
    registry = get_running_task_registry()
    release = threading.Event()
    dash, state = make_dashboard_batch_task_stub(extract_release_event=release)

    failures = []

    def _runner():
        try:
            dash._execute_batch_extract([{"ip_address": "198.51.100.60", "host_type": "S"}])
        except Exception as exc:  # pragma: no cover - debugging aid
            failures.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()

    deadline = time.time() + 1.5
    while registry.count() == 0 and time.time() < deadline:
        time.sleep(0.01)

    assert registry.count() == 1
    task = registry.list_tasks()[0]
    assert task.task_type == "extract"
    assert task.reopen_callback is not None
    assert task.cancel_callback is not None

    dialog = AsyncFakeDialog.created[-1]
    dialog.trigger_close()
    assert dialog.hidden is True

    task.reopen_callback()
    time.sleep(0.05)
    assert dialog.hidden is False

    task.cancel_callback()
    if state["extract_cancel_event"] is not None:
        deadline = time.time() + 1.0
        while not state["extract_cancel_event"].is_set() and time.time() < deadline:
            time.sleep(0.01)
        assert state["extract_cancel_event"].is_set() is True

    release.set()
    thread.join(timeout=2.0)
    assert failures == []
    assert registry.count() == 0


@pytest.mark.scenario
def test_s10_se_dork_probe_task_lifecycle_success(monkeypatch) -> None:
    registry = get_running_task_registry()
    events = []
    registry.subscribe(lambda tasks: events.append(list(tasks)))

    rows = {
        "7": {"result_id": 7, "url": "http://example.local/files/"},
    }
    browser = make_se_dork_probe_window_stub(SeDorkBrowserWindow, rows)
    reload_calls = {"count": 0}
    browser._load_rows = lambda: reload_calls.__setitem__("count", reload_calls["count"] + 1)

    fake_conn = FakeSeDorkConnection()
    outcome = types.SimpleNamespace(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub",
        probe_checked_at="2026-04-25T12:00:00",
        probe_error=None,
    )

    FakeSeDorkDialog.created.clear()
    monkeypatch.setattr(se_dork_module, "BatchStatusDialog", FakeSeDorkDialog)
    monkeypatch.setattr(se_dork_module, "ThreadPoolExecutor", InlineExecutor)
    monkeypatch.setattr("experimental.se_dork.store.init_db", lambda _db_path: None)
    monkeypatch.setattr("experimental.se_dork.store.open_connection", lambda _db_path: fake_conn)
    monkeypatch.setattr("experimental.se_dork.store.update_result_probe", lambda *_a, **_k: None)
    monkeypatch.setattr("experimental.se_dork.probe.probe_url", lambda *_a, **_k: outcome)
    monkeypatch.setattr(se_dork_module.messagebox, "showinfo", lambda *_a, **_k: None)

    browser._on_probe_selected()

    assert fake_conn.commit_calls == 1
    assert fake_conn.close_calls == 1
    assert reload_calls["count"] == 1
    assert any(len(batch) == 1 for batch in events)
    assert len(events[-1]) == 0

    active_snapshots = [batch[0] for batch in events if len(batch) == 1]
    snapshot = active_snapshots[0]
    assert snapshot.task_type == "probe"
    assert snapshot.reopen_callback is not None
    assert snapshot.cancel_callback is not None
    assert any(s.progress == "1/1 targets" for s in active_snapshots)

    dialog = FakeSeDorkDialog.created[0]
    before = dialog.show_calls
    snapshot.reopen_callback()
    assert dialog.show_calls == before + 1
    assert registry.count() == 0


@pytest.mark.scenario
def test_s11_se_dork_probe_task_cleanup_on_failure(monkeypatch) -> None:
    registry = get_running_task_registry()
    events = []
    registry.subscribe(lambda tasks: events.append(list(tasks)))

    rows = {
        "9": {"result_id": 9, "url": "http://example.local/error/"},
    }
    browser = make_se_dork_probe_window_stub(SeDorkBrowserWindow, rows)
    browser._load_rows = lambda: None

    fake_conn = FakeSeDorkConnection()
    outcome = types.SimpleNamespace(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub",
        probe_checked_at="2026-04-25T12:00:00",
        probe_error=None,
    )
    infos = []

    FakeSeDorkDialog.created.clear()
    monkeypatch.setattr(se_dork_module, "BatchStatusDialog", FakeSeDorkDialog)
    monkeypatch.setattr(se_dork_module, "ThreadPoolExecutor", InlineExecutor)
    monkeypatch.setattr("experimental.se_dork.store.init_db", lambda _db_path: None)
    monkeypatch.setattr("experimental.se_dork.store.open_connection", lambda _db_path: fake_conn)
    monkeypatch.setattr(
        "experimental.se_dork.store.update_result_probe",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("experimental.se_dork.probe.probe_url", lambda *_a, **_k: outcome)
    monkeypatch.setattr(
        se_dork_module.messagebox,
        "showinfo",
        lambda title, *_args, **_kwargs: infos.append(title),
    )

    browser._on_probe_selected()

    assert registry.count() == 0
    assert any(len(batch) == 1 for batch in events)
    assert len(events[-1]) == 0
    assert FakeSeDorkDialog.created[0].finished_calls[-1][0] == "failed"
    assert infos == ["Probe failed"]


@pytest.mark.scenario
def test_s13_close_confirm_race_scan_finishes_during_shutdown(monkeypatch) -> None:
    patch_main_shutdown_helpers(monkeypatch)

    app = make_main_app_stub()
    state = {
        "active": True,
        "cancel_called": 0,
        "force_called": 0,
        "active_checks": 0,
    }

    def _has_active() -> bool:
        state["active_checks"] += 1
        if state["active_checks"] >= 3:
            state["active"] = False
        return state["active"]

    def _cancel() -> None:
        state["cancel_called"] += 1

    def _force() -> None:
        state["force_called"] += 1
        state["active"] = False

    app.dashboard = SimpleNamespace(
        has_active_or_queued_work=_has_active,
        request_cancel_active_or_queued_work=_cancel,
        force_terminate_active_work=_force,
        teardown_dashboard_monitors=lambda: None,
    )

    monkeypatch.setattr(main_module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(main_module.time, "sleep", lambda *_a, **_k: None)

    app._on_closing()

    assert state["cancel_called"] >= 1
    assert state["force_called"] == 0
    assert app.root.destroy_calls == 1
