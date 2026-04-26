"""Regression tests for deferred tmpfs startup warning dialog lifecycle (canonical entrypoint)."""

from __future__ import annotations

from unittest.mock import MagicMock

from gui.utils.dirracuda_loader import load_dirracuda_module


DIRRACUDA = load_dirracuda_module()


def _bare_app():
    app = DIRRACUDA.XSMBSeekGUI.__new__(DIRRACUDA.XSMBSeekGUI)
    app.root = MagicMock()
    app._pending_tmpfs_startup_warning = None
    return app


def test_schedule_tmpfs_warning_queues_after_idle():
    app = _bare_app()
    app._pending_tmpfs_startup_warning = "fallback warning"
    app.root.winfo_exists.return_value = True

    DIRRACUDA.XSMBSeekGUI._schedule_tmpfs_startup_warning_dialog(app)

    app.root.after_idle.assert_called_once_with(app._show_pending_tmpfs_startup_warning)


def test_show_pending_tmpfs_warning_requeues_until_mapped(monkeypatch):
    app = _bare_app()
    app._pending_tmpfs_startup_warning = "fallback warning"
    app.root.winfo_exists.return_value = True
    app.root.winfo_ismapped.return_value = False

    warned = []
    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "showwarning",
        lambda *args, **kwargs: warned.append((args, kwargs)),
    )

    DIRRACUDA.XSMBSeekGUI._show_pending_tmpfs_startup_warning(app)

    app.root.after.assert_called_once()
    delay_ms, callback = app.root.after.call_args[0]
    assert delay_ms == 50
    assert callback == app._show_pending_tmpfs_startup_warning
    assert app._pending_tmpfs_startup_warning == "fallback warning"
    assert warned == []


def test_show_pending_tmpfs_warning_displays_once_when_mapped(monkeypatch):
    app = _bare_app()
    app._pending_tmpfs_startup_warning = "fallback warning"
    app.root.winfo_exists.return_value = True
    app.root.winfo_ismapped.return_value = True

    warned = []
    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "showwarning",
        lambda *args, **kwargs: warned.append((args, kwargs)),
    )

    DIRRACUDA.XSMBSeekGUI._show_pending_tmpfs_startup_warning(app)

    assert app._pending_tmpfs_startup_warning is None
    assert len(warned) == 1
    assert warned[0][0][0] == "tmpfs Quarantine Fallback"
    assert warned[0][1]["parent"] is app.root


def test_bootstrap_tmpfs_runtime_defers_warning_dialog(monkeypatch):
    app = _bare_app()
    app.config = MagicMock()
    app.config.get_config_path.return_value = "/tmp/config.json"
    app._schedule_tmpfs_startup_warning_dialog = MagicMock()

    monkeypatch.setattr(
        DIRRACUDA,
        "bootstrap_tmpfs_quarantine",
        lambda config_path: {"ok": True, "effective_root": "/tmp"},
    )
    monkeypatch.setattr(
        DIRRACUDA,
        "consume_tmpfs_startup_warning",
        lambda: "fallback warning",
    )

    DIRRACUDA.XSMBSeekGUI._bootstrap_tmpfs_runtime(app)

    assert app._pending_tmpfs_startup_warning == "fallback warning"
    app._schedule_tmpfs_startup_warning_dialog.assert_called_once()
