"""Tests for canonical dirracuda startup DB-unification UI parity."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gui.utils.dirracuda_loader import load_dirracuda_module

pytestmark = pytest.mark.scenario


DIRRACUDA = load_dirracuda_module()


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


class _FakeRootWithAfter(_FakeRoot):
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


def test_handle_db_unification_schedules_sidecar_prompt_when_flagged(monkeypatch):
    app = _make_app_stub()
    app.root = _FakeRootWithAfter()
    app.dashboard = SimpleNamespace(_show_status_bar=lambda _msg: None)

    app._handle_db_unification_result({
        "success": True,
        "errors": [],
        "prompt_cleanup": False,
        "prompt_sidecar_migration": True,
    })

    assert any(cb == app._check_sidecar_migration_prompt for _delay, cb in app.root.after_calls)


def test_handle_db_unification_no_sidecar_prompt_when_flag_absent(monkeypatch):
    app = _make_app_stub()
    app.root = _FakeRootWithAfter()
    app.dashboard = SimpleNamespace(_show_status_bar=lambda _msg: None)

    app._handle_db_unification_result({
        "success": True,
        "errors": [],
        "prompt_cleanup": False,
        "prompt_sidecar_migration": False,
    })

    assert not any(cb == app._check_sidecar_migration_prompt for _delay, cb in app.root.after_calls)


def test_check_sidecar_migration_prompt_defers_on_no(monkeypatch):
    app = _make_app_stub()
    app.mock_mode = False
    deferred = []
    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(DIRRACUDA, "defer_sidecar_migration",
                        lambda _reader: deferred.append(True))

    app._check_sidecar_migration_prompt()

    assert deferred == [True]


def test_check_sidecar_migration_prompt_starts_worker_on_yes(monkeypatch):
    import threading as _threading
    app = _make_app_stub()
    app.mock_mode = False
    app.dashboard = SimpleNamespace()

    thread_calls = []

    class _FakeThread:
        def __init__(self, target, daemon=False):
            thread_calls.append({"target": target, "daemon": daemon})
        def start(self):
            pass

    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(DIRRACUDA.threading, "Thread", _FakeThread)

    app._check_sidecar_migration_prompt()

    assert len(thread_calls) == 1
    assert thread_calls[0]["daemon"] is True
    # Worker must be callable
    assert callable(thread_calls[0]["target"])


def test_check_sidecar_migration_prompt_noop_in_mock_mode(monkeypatch):
    app = _make_app_stub()
    app.mock_mode = True
    prompt_shown = []
    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno",
                        lambda *a, **k: prompt_shown.append(True) or False)

    app._check_sidecar_migration_prompt()

    assert prompt_shown == []


def test_check_sidecar_migration_prompt_transient_dialog_failure_does_not_consume_prompt(monkeypatch):
    app = _make_app_stub()
    app.mock_mode = False
    deferred = []
    calls = {"count": 0}

    def _askyesno(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient dialog failure")
        return False

    monkeypatch.setattr(DIRRACUDA.messagebox, "askyesno", _askyesno)
    monkeypatch.setattr(DIRRACUDA, "defer_sidecar_migration",
                        lambda _reader: deferred.append(True))

    # First attempt fails before choice is returned; prompt must remain eligible.
    app._check_sidecar_migration_prompt()
    assert calls["count"] == 1
    assert deferred == []
    assert getattr(app, "_sidecar_prompt_shown", False) is False

    # Second attempt should still prompt and process defer.
    app._check_sidecar_migration_prompt()
    assert calls["count"] == 2
    assert deferred == [True]
    assert app._sidecar_prompt_shown is True
