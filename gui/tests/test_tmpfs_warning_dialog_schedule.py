"""Regression tests for deferred tmpfs startup warning dialog lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.main import SMBSeekGUI


def _bare_app() -> SMBSeekGUI:
    app = SMBSeekGUI.__new__(SMBSeekGUI)
    app.root = MagicMock()
    app._pending_tmpfs_startup_warning = None
    return app


def test_schedule_tmpfs_warning_queues_after_idle():
    app = _bare_app()
    app._pending_tmpfs_startup_warning = "fallback warning"
    app.root.winfo_exists.return_value = True

    SMBSeekGUI._schedule_tmpfs_startup_warning_dialog(app)

    app.root.after_idle.assert_called_once_with(app._show_pending_tmpfs_startup_warning)


def test_show_pending_tmpfs_warning_requeues_until_mapped(monkeypatch):
    app = _bare_app()
    app._pending_tmpfs_startup_warning = "fallback warning"
    app.root.winfo_exists.return_value = True
    app.root.winfo_ismapped.return_value = False

    warned = []
    monkeypatch.setattr("gui.main.messagebox.showwarning", lambda *args, **kwargs: warned.append((args, kwargs)))

    SMBSeekGUI._show_pending_tmpfs_startup_warning(app)

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
    monkeypatch.setattr("gui.main.messagebox.showwarning", lambda *args, **kwargs: warned.append((args, kwargs)))

    SMBSeekGUI._show_pending_tmpfs_startup_warning(app)

    assert app._pending_tmpfs_startup_warning is None
    assert len(warned) == 1
    assert warned[0][0][0] == "tmpfs Quarantine Fallback"
    assert warned[0][1]["parent"] is app.root


def test_bootstrap_tmpfs_runtime_defers_warning_dialog(monkeypatch):
    app = _bare_app()
    app.config_path = "/tmp/config.json"
    app._schedule_tmpfs_startup_warning_dialog = MagicMock()

    monkeypatch.setattr("gui.main.bootstrap_tmpfs_quarantine", lambda config_path: {"ok": True, "effective_root": "/tmp"})
    monkeypatch.setattr("gui.main.consume_tmpfs_startup_warning", lambda: "fallback warning")

    SMBSeekGUI._bootstrap_tmpfs_runtime(app)

    assert app._pending_tmpfs_startup_warning == "fallback warning"
    app._schedule_tmpfs_startup_warning_dialog.assert_called_once()
