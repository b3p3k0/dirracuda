"""Tests for SMBSeekGUI close behavior with active/queued work."""

from types import SimpleNamespace

from gui.main import SMBSeekGUI
import gui.main as main_module


class _FakeRoot:
    def __init__(self):
        self.destroy_calls = 0

    def winfo_exists(self):
        return True

    def update_idletasks(self):
        return None

    def update(self):
        return None

    def destroy(self):
        self.destroy_calls += 1


def _patch_shutdown_helpers(monkeypatch):
    monkeypatch.setattr(main_module, "get_tmpfs_runtime_state", lambda: {"tmpfs_active": False})
    monkeypatch.setattr(main_module, "tmpfs_has_quarantined_files", lambda: False)
    monkeypatch.setattr(main_module, "cleanup_tmpfs_quarantine", lambda: {"ok": True, "message": ""})


def test_close_with_active_work_cancelled_by_user(monkeypatch):
    _patch_shutdown_helpers(monkeypatch)

    app = SMBSeekGUI.__new__(SMBSeekGUI)
    app.root = _FakeRoot()
    app._pending_tmpfs_startup_warning = None
    app.drill_down_windows = {}
    app.db_reader = SimpleNamespace(clear_cache=lambda: None)
    app.ui_dispatcher = None
    app.scan_manager = SimpleNamespace(is_scanning=False, interrupt_scan=lambda: True)

    dashboard = SimpleNamespace(
        has_active_or_queued_work=lambda: True,
        request_cancel_active_or_queued_work=lambda: (_ for _ in ()).throw(AssertionError("should not cancel")),
        teardown_dashboard_monitors=lambda: None,
    )
    app.dashboard = dashboard

    monkeypatch.setattr(main_module.messagebox, "askyesno", lambda *a, **k: False)
    app._on_closing()
    assert app.root.destroy_calls == 0


def test_close_with_active_work_confirms_and_closes(monkeypatch):
    _patch_shutdown_helpers(monkeypatch)

    app = SMBSeekGUI.__new__(SMBSeekGUI)
    app.root = _FakeRoot()
    app._pending_tmpfs_startup_warning = None
    app.drill_down_windows = {}
    app.db_reader = SimpleNamespace(clear_cache=lambda: None)
    app.ui_dispatcher = None
    app.scan_manager = SimpleNamespace(is_scanning=False, interrupt_scan=lambda: True)

    state = {"active": True, "cancel_called": 0, "teardown_called": 0}

    def _has_active():
        return state["active"]

    def _cancel():
        state["cancel_called"] += 1
        state["active"] = False

    def _teardown():
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


def test_close_force_terminates_after_retry_when_still_active(monkeypatch):
    _patch_shutdown_helpers(monkeypatch)

    app = SMBSeekGUI.__new__(SMBSeekGUI)
    app.root = _FakeRoot()
    app._pending_tmpfs_startup_warning = None
    app.drill_down_windows = {}
    app.db_reader = SimpleNamespace(clear_cache=lambda: None)
    app.ui_dispatcher = None
    app.scan_manager = SimpleNamespace(is_scanning=False, interrupt_scan=lambda: True)

    state = {"cancel_called": 0, "force_called": 0}

    app.dashboard = SimpleNamespace(
        has_active_or_queued_work=lambda: True,
        request_cancel_active_or_queued_work=lambda: state.__setitem__("cancel_called", state["cancel_called"] + 1),
        force_terminate_active_work=lambda: state.__setitem__("force_called", state["force_called"] + 1),
        teardown_dashboard_monitors=lambda: None,
    )

    tick = {"value": 0.0}

    def _fake_time():
        tick["value"] += 0.5
        return tick["value"]

    monkeypatch.setattr(main_module.time, "time", _fake_time)
    monkeypatch.setattr(main_module.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(main_module.messagebox, "askyesno", lambda *a, **k: True)

    app._on_closing()
    assert state["cancel_called"] >= 1
    assert state["force_called"] >= 1
    assert app.root.destroy_calls == 1

