"""Regression tests for startup layout-migration notification behavior."""

from __future__ import annotations

from types import SimpleNamespace

from gui.utils.dirracuda_loader import load_dirracuda_module


DIRRACUDA = load_dirracuda_module()


class _FakeRoot:
    def winfo_exists(self):
        return True


def _make_app_stub():
    app = DIRRACUDA.XSMBSeekGUI.__new__(DIRRACUDA.XSMBSeekGUI)
    app.root = _FakeRoot()
    app.settings_manager = SimpleNamespace(settings_file=str(DIRRACUDA._LAYOUT_PATHS.gui_settings_file))
    app._runtime_fallback_paths = []
    app._session_db_fallback_path = None
    app._layout_migration_result = {}
    return app


def _capture_dialogs(monkeypatch):
    dialogs = {"info": [], "warning": []}
    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "showinfo",
        lambda *args, **kwargs: dialogs["info"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "showwarning",
        lambda *args, **kwargs: dialogs["warning"].append((args, kwargs)),
    )
    return dialogs


def test_layout_migration_clean_success_is_silent(monkeypatch):
    app = _make_app_stub()
    dialogs = _capture_dialogs(monkeypatch)
    app._layout_migration_result = {
        "status": "success",
        "migrated": {"ok": 0, "skipped": 42, "errors": 0},
        "fallback_paths": [],
        "sanitized": {"changed": 0, "errors": 0},
    }

    app._show_layout_migration_notification()

    assert dialogs["info"] == []
    assert dialogs["warning"] == []


def test_layout_notification_ignores_canonical_config_path_fallback(monkeypatch):
    app = _make_app_stub()
    dialogs = _capture_dialogs(monkeypatch)
    app._runtime_fallback_paths = [str(DIRRACUDA._LAYOUT_PATHS.config_file)]
    app._layout_migration_result = {
        "status": "success",
        "migrated": {"ok": 0, "skipped": 42, "errors": 0},
        "fallback_paths": [],
        "sanitized": {"changed": 0, "errors": 0},
    }

    app._show_layout_migration_notification()

    assert dialogs["info"] == []
    assert dialogs["warning"] == []


def test_layout_notification_warns_for_real_runtime_fallback(monkeypatch):
    app = _make_app_stub()
    dialogs = _capture_dialogs(monkeypatch)
    fallback_path = str(DIRRACUDA._LAYOUT_LEGACY.repo_config_file.resolve(strict=False))
    app._runtime_fallback_paths = [fallback_path]
    app._layout_migration_result = {
        "status": "success",
        "migrated": {"ok": 0, "skipped": 42, "errors": 0},
        "fallback_paths": [],
        "sanitized": {"changed": 0, "errors": 0},
    }

    app._show_layout_migration_notification()

    assert dialogs["info"] == []
    assert len(dialogs["warning"]) == 1
    body = dialogs["warning"][0][0][1]
    assert "Runtime fallback paths in use:" in body
    assert fallback_path in body


def test_layout_notification_warns_when_sanitize_errors_present(monkeypatch):
    app = _make_app_stub()
    dialogs = _capture_dialogs(monkeypatch)
    app._layout_migration_result = {
        "status": "success",
        "migrated": {"ok": 0, "skipped": 42, "errors": 0},
        "fallback_paths": [],
        "sanitized": {"changed": 0, "errors": 1},
    }

    app._show_layout_migration_notification()

    assert dialogs["info"] == []
    assert len(dialogs["warning"]) == 1
    body = dialogs["warning"][0][0][1]
    assert "Path self-heal warnings: 1" in body
