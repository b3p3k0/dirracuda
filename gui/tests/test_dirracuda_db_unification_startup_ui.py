"""Tests for canonical dirracuda startup DB-unification UI parity."""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.scenario


def _load_dirracuda_module():
    path = Path(__file__).resolve().parents[2] / "dirracuda"
    loader = SourceFileLoader("dirracuda_unification_ui_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


DIRRACUDA = _load_dirracuda_module()


class _FakeRoot:
    def winfo_exists(self):
        return True


def _make_app_stub():
    app = DIRRACUDA.XSMBSeekGUI.__new__(DIRRACUDA.XSMBSeekGUI)
    app.root = _FakeRoot()
    app.db_reader = SimpleNamespace()
    app._db_unification_running = True
    app._pending_db_unification_error = None
    return app


def test_handle_db_unification_failure_sets_pending_warning_and_retries(monkeypatch):
    app = _make_app_stub()
    status_messages = []
    retries = {"count": 0}
    app.dashboard = SimpleNamespace(_show_status_bar=lambda msg: status_messages.append(msg))
    app._start_db_unification_tasks = lambda: retries.__setitem__("count", retries["count"] + 1)

    monkeypatch.setattr(DIRRACUDA.messagebox, "askretrycancel", lambda *a, **k: True)

    app._handle_db_unification_result(
        {
            "success": False,
            "errors": ["sidecar import failed: boom"],
            "prompt_cleanup": False,
        }
    )

    assert app._db_unification_running is False
    assert app._pending_db_unification_error == "sidecar import failed: boom"
    assert status_messages == ["DB unification warning: startup migration failed. Retry available."]
    assert retries["count"] == 1


def test_handle_db_unification_success_does_not_prompt_retry(monkeypatch):
    app = _make_app_stub()
    app.dashboard = SimpleNamespace(_show_status_bar=lambda _msg: None)
    retry_calls = {"count": 0}
    app._start_db_unification_tasks = lambda: retry_calls.__setitem__("count", retry_calls["count"] + 1)

    def _raise_retry(*_args, **_kwargs):
        raise AssertionError("retry prompt should not be shown on success")

    monkeypatch.setattr(DIRRACUDA.messagebox, "askretrycancel", _raise_retry)

    app._handle_db_unification_result(
        {
            "success": True,
            "errors": [],
            "probe_backfill": {"imported": 0},
            "sidecar_import": {"imported": 0},
            "prompt_cleanup": False,
        }
    )

    assert app._db_unification_running is False
    assert app._pending_db_unification_error is None
    assert retry_calls["count"] == 0


@pytest.mark.parametrize("keep_files", [True, False])
def test_handle_db_unification_prompt_cleanup_applies_user_choice(monkeypatch, keep_files):
    app = _make_app_stub()
    app.dashboard = SimpleNamespace(_show_status_bar=lambda _msg: None)
    applied = []

    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: keep_files)
    monkeypatch.setattr(
        DIRRACUDA,
        "apply_probe_cleanup_choice",
        lambda _reader, *, keep_files: applied.append(bool(keep_files)),
    )

    app._handle_db_unification_result(
        {
            "success": True,
            "errors": [],
            "prompt_cleanup": True,
        }
    )

    assert app._db_unification_running is False
    assert applied == [keep_files]


def test_handle_db_unification_failure_prompt_paths_are_non_blocking_when_dialogs_raise(monkeypatch):
    app = _make_app_stub()
    status_messages = []
    app.dashboard = SimpleNamespace(_show_status_bar=lambda msg: status_messages.append(msg))
    retries = {"count": 0}
    app._start_db_unification_tasks = lambda: retries.__setitem__("count", retries["count"] + 1)

    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "askyesno",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cleanup dialog failure")),
    )
    monkeypatch.setattr(
        DIRRACUDA.messagebox,
        "askretrycancel",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("retry dialog failure")),
    )

    app._handle_db_unification_result(
        {
            "success": False,
            "errors": ["probe backfill failed: boom"],
            "prompt_cleanup": True,
        }
    )

    assert app._db_unification_running is False
    assert app._pending_db_unification_error == "probe backfill failed: boom"
    assert status_messages == ["DB unification warning: startup migration failed. Retry available."]
    assert retries["count"] == 0
