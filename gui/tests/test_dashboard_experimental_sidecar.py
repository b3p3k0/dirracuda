"""Tests for open_sidecar_legacy_db migrate branch in dashboard_experimental (C5)."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_widget(db_reader=None):
    w = MagicMock()
    w.parent = MagicMock()
    w.theme = MagicMock()
    w.theme.apply_to_widget = MagicMock()
    w.db_reader = db_reader
    return w


def _run_migrate_pick(monkeypatch, widget=None):
    """Mock tk/dialog helpers and return button-command registry for sidecar picker."""
    import gui.components.dashboard_experimental as _mod

    if widget is None:
        widget = _make_widget(db_reader=MagicMock())

    pick_registry = {}

    class _FakeDialog:
        def __init__(self, *a, **kw): pass
        def title(self, *a): pass
        def transient(self, *a): pass
        def resizable(self, *a): pass
        def destroy(self): pass

    class _FakeButton:
        def __init__(self, parent=None, text="", command=None, **kw):
            pick_registry[text] = command
        def pack(self, **kw): pass

    monkeypatch.setattr(_mod.tk, "Toplevel", lambda *a, **kw: _FakeDialog())
    monkeypatch.setattr(_mod.tk, "Button", _FakeButton)
    monkeypatch.setattr(_mod, "ensure_dialog_focus", MagicMock())
    monkeypatch.setattr(_mod, "apply_theme_to_window", MagicMock())

    _mod.open_sidecar_legacy_db(widget)

    return widget, pick_registry


def _run_pick(monkeypatch, label: str, widget=None):
    widget, pick_registry = _run_migrate_pick(monkeypatch, widget)
    cmd = pick_registry.get(label)
    assert cmd is not None
    return cmd, widget, pick_registry


def _run_migrate_button(monkeypatch, widget=None):
    migrate_cmd, widget, pick_registry = _run_pick(monkeypatch, "Migrate All to Main DB", widget)
    return migrate_cmd, widget, pick_registry


def test_se_dork_branch_calls_open_se_dork_results_db(monkeypatch):
    import gui.components.dashboard_experimental as _mod
    widget = _make_widget(db_reader=MagicMock())
    calls = []
    monkeypatch.setattr(_mod, "open_se_dork_results_db", lambda w: calls.append(w))

    cmd, _widget, _ = _run_pick(monkeypatch, "SearXNG Dork Results", widget)
    cmd()

    assert calls == [widget]


def test_reddit_branch_calls_open_reddit_post_db(monkeypatch):
    import gui.components.dashboard_experimental as _mod
    widget = _make_widget(db_reader=MagicMock())
    calls = []
    monkeypatch.setattr(_mod, "open_reddit_post_db", lambda w: calls.append(w))

    cmd, _widget, _ = _run_pick(monkeypatch, "Reddit Open Directory Posts", widget)
    cmd()

    assert calls == [widget]


def test_migrate_branch_starts_daemon_thread(monkeypatch):
    import gui.components.dashboard_experimental as _mod

    thread_calls = []

    class _FakeThread:
        def __init__(self, target, daemon=False):
            thread_calls.append({"target": target, "daemon": daemon})
        def start(self):
            pass

    monkeypatch.setattr(_mod.threading, "Thread", _FakeThread)
    monkeypatch.setattr(_mod, "execute_sidecar_migration_now", MagicMock())

    migrate_cmd, _widget, _ = _run_migrate_button(monkeypatch)
    migrate_cmd()

    assert len(thread_calls) == 1
    assert thread_calls[0]["daemon"] is True


def test_migrate_worker_calls_execute_sidecar_migration_now(monkeypatch):
    import gui.components.dashboard_experimental as _mod

    executed = []
    monkeypatch.setattr(_mod, "execute_sidecar_migration_now",
                        lambda reader: executed.append(reader))

    captured_targets = []

    class _FakeThread:
        def __init__(self, target, daemon=False):
            captured_targets.append(target)
        def start(self):
            pass

    monkeypatch.setattr(_mod.threading, "Thread", _FakeThread)

    migrate_cmd, widget, _ = _run_migrate_button(monkeypatch)
    migrate_cmd()

    assert len(captured_targets) == 1
    captured_targets[0]()  # run synchronously

    assert executed == [widget.db_reader]


def test_migrate_worker_schedules_refresh_on_completion(monkeypatch):
    import gui.components.dashboard_experimental as _mod

    after_calls = []
    widget = _make_widget(db_reader=MagicMock())
    widget.parent.after = lambda delay, cb: after_calls.append(cb)

    monkeypatch.setattr(_mod, "execute_sidecar_migration_now", MagicMock())

    captured_targets = []

    class _FakeThread:
        def __init__(self, target, daemon=False):
            captured_targets.append(target)
        def start(self):
            pass

    monkeypatch.setattr(_mod.threading, "Thread", _FakeThread)

    migrate_cmd, _, _ = _run_migrate_button(monkeypatch, widget)
    migrate_cmd()
    captured_targets[0]()  # run worker synchronously

    assert widget.refresh_after_database_change in after_calls


def test_migrate_worker_handles_execute_exception_gracefully(monkeypatch):
    import gui.components.dashboard_experimental as _mod

    monkeypatch.setattr(
        _mod, "execute_sidecar_migration_now",
        lambda _r: (_ for _ in ()).throw(RuntimeError("import boom")),
    )

    captured_targets = []

    class _FakeThread:
        def __init__(self, target, daemon=False):
            captured_targets.append(target)
        def start(self):
            pass

    monkeypatch.setattr(_mod.threading, "Thread", _FakeThread)

    migrate_cmd, _widget, _ = _run_migrate_button(monkeypatch)
    migrate_cmd()

    # Must not raise
    captured_targets[0]()


def test_migrate_branch_no_reader_shows_showerror_no_thread(monkeypatch):
    import gui.components.dashboard_experimental as _mod

    widget = _make_widget(db_reader=None)
    errors = []
    thread_starts = []

    monkeypatch.setattr(_mod, "_mb", lambda: MagicMock(showerror=lambda *a, **kw: errors.append(True)))

    class _FakeThread:
        def __init__(self, *a, **kw): pass
        def start(self):
            thread_starts.append(True)

    monkeypatch.setattr(_mod.threading, "Thread", _FakeThread)

    migrate_cmd, _, _ = _run_migrate_button(monkeypatch, widget)
    migrate_cmd()

    assert errors == [True]
    assert thread_starts == []
