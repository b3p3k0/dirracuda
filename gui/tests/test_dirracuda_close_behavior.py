"""Tests for dirracuda close behavior with active/queued work."""

from __future__ import annotations

from types import SimpleNamespace

from gui.utils.dirracuda_loader import load_dirracuda_module

DIRRACUDA = load_dirracuda_module()


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
    monkeypatch.setattr(DIRRACUDA, "get_tmpfs_runtime_state", lambda: {"tmpfs_active": False})
    monkeypatch.setattr(DIRRACUDA, "tmpfs_has_quarantined_files", lambda: False)
    monkeypatch.setattr(DIRRACUDA, "cleanup_tmpfs_quarantine", lambda: {"ok": True, "message": ""})


def _bare_app():
    app = DIRRACUDA.XSMBSeekGUI.__new__(DIRRACUDA.XSMBSeekGUI)
    app.root = _FakeRoot()
    app._pending_tmpfs_startup_warning = None
    app.drill_down_windows = {}
    app.db_reader = SimpleNamespace(clear_cache=lambda: None)
    app.ui_dispatcher = None
    app.scan_manager = SimpleNamespace(is_scanning=False, interrupt_scan=lambda: True)
    app.config = SimpleNamespace(save_config=lambda: None)
    return app


def test_close_with_active_work_cancelled_by_user(monkeypatch):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
    app.dashboard = SimpleNamespace(
        has_active_or_queued_work=lambda: True,
        request_cancel_active_or_queued_work=lambda: (_ for _ in ()).throw(AssertionError("should not cancel")),
        teardown_dashboard_monitors=lambda: None,
    )

    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: False)
    app._on_closing()
    assert app.root.destroy_calls == 0


def test_close_with_active_work_confirms_and_closes(monkeypatch, caplog):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()

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

    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(DIRRACUDA.time, "sleep", lambda *_a, **_k: None)
    app._on_closing()

    assert state["cancel_called"] >= 1
    assert state["teardown_called"] == 1
    assert app.root.destroy_calls == 1
    assert "tracked work stopped and cleanup completed" in caplog.text


def test_close_force_terminates_after_retry_when_still_active(monkeypatch, caplog):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
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

    monkeypatch.setattr(DIRRACUDA.time, "time", _fake_time)
    monkeypatch.setattr(DIRRACUDA.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: True)

    app._on_closing()
    assert state["cancel_called"] >= 1
    assert state["force_called"] >= 1
    assert app.root.destroy_calls == 1
    assert "clean shutdown could not be confirmed" in caplog.text
    assert "cleanup completed" not in caplog.text


def test_shutdown_retains_live_future_even_if_registry_is_empty(monkeypatch, caplog):
    from concurrent.futures import Future
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
    future = Future()
    future.set_running_or_notify_cancel()
    closed = []
    app.drill_down_windows = {"server_list": SimpleNamespace(
        active_jobs={}, _close_window=lambda: closed.append(True),
        _batch_delivery=SimpleNamespace(pending_futures=lambda: [future], failed=False),
    )}
    app.dashboard = SimpleNamespace(has_active_or_queued_work=lambda: False)
    tick = iter(range(100))
    monkeypatch.setattr(DIRRACUDA.time, "time", lambda: next(tick))
    monkeypatch.setattr(DIRRACUDA.time, "sleep", lambda _: None)
    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: True)
    app._on_closing()
    assert closed == [True]
    assert "clean shutdown could not be confirmed" in caplog.text
    assert "cleanup completed" not in caplog.text


def test_shutdown_reports_cleanup_error_and_still_destroys_root(monkeypatch, caplog):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
    app.dashboard = None

    def fail():
        raise OSError("settings disk is full")

    app.config.save_config = fail
    app._on_closing()
    assert app.root.destroy_calls == 1
    assert "configuration could not be saved" in caplog.text
    assert "settings disk is full" in caplog.text
    assert "Application closed with errors" in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_shutdown_checks_subprocess_even_when_scan_flag_is_clear(monkeypatch, caplog):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
    app.dashboard = None
    app.scan_manager.backend_interface = SimpleNamespace(
        active_process=SimpleNamespace(poll=lambda: None),
        terminate_current_operation=lambda: None,
    )
    tick = iter(range(100))
    monkeypatch.setattr(DIRRACUDA.time, "time", lambda: next(tick))
    monkeypatch.setattr(DIRRACUDA.time, "sleep", lambda _: None)
    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: True)
    app._on_closing()
    assert "clean shutdown could not be confirmed" in caplog.text


def test_idle_close_is_quiet(monkeypatch, caplog):
    _patch_shutdown_helpers(monkeypatch)
    app = _bare_app()
    app.dashboard = None
    app._on_closing()
    assert app.root.destroy_calls == 1
    assert not caplog.records
