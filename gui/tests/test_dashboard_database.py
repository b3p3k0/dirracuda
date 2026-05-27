"""Tests for dashboard_database satellite — routing dialog (C5)."""

from __future__ import annotations

from unittest.mock import MagicMock, call


def _make_widget(db_reader=None):
    w = MagicMock()
    w.parent = MagicMock()
    w.theme = MagicMock()
    w.theme.apply_to_widget = MagicMock()
    w.db_reader = db_reader
    return w


def _run_pick(monkeypatch, choice: str, widget=None):
    """Build open_db_surface with Toplevel mocked, capture the _pick closure, call it."""
    import tkinter as tk
    from gui.utils import dialog_helpers
    from gui.utils.style import apply_theme_to_window as _atw
    from gui.components import dashboard_database, dashboard_experimental

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

    monkeypatch.setattr(tk, "Toplevel", lambda *a, **kw: _FakeDialog())
    monkeypatch.setattr(tk, "Button", _FakeButton)
    monkeypatch.setattr(dashboard_database, "ensure_dialog_focus", MagicMock())
    monkeypatch.setattr(dashboard_database, "apply_theme_to_window", MagicMock())

    dashboard_database.open_db_surface(widget)

    # Find the button whose label matches choice content and call its command
    matched = next((cmd for lbl, cmd in pick_registry.items() if choice in lbl), None)
    assert matched is not None, f"No button matched {choice!r}; got {list(pick_registry)}"
    matched()
    return widget, pick_registry


def test_servers_path_calls_open_drill_down(monkeypatch):
    widget = _make_widget()
    _run_pick(monkeypatch, "View Servers", widget)
    widget._open_drill_down.assert_called_once_with("server_list")


def test_tools_path_calls_show_db_tools_dialog(monkeypatch):
    fake_reader = MagicMock()
    fake_reader.db_path = "/tmp/test.db"
    widget = _make_widget(db_reader=fake_reader)

    show_calls = []
    monkeypatch.setattr(
        "gui.components.db_tools_dialog.show_db_tools_dialog",
        lambda **kw: show_calls.append(kw),
    )

    _run_pick(monkeypatch, "DB Tools", widget)

    assert len(show_calls) == 1
    assert show_calls[0]["db_path"] == str(fake_reader.db_path)


def test_tools_path_no_reader_shows_showerror(monkeypatch):
    widget = _make_widget(db_reader=None)
    errors = []

    import gui.components.dashboard_database as _mod
    monkeypatch.setattr(_mod, "_mb", lambda: MagicMock(showerror=lambda *a, **kw: errors.append(True)))

    _run_pick(monkeypatch, "DB Tools", widget)

    assert errors == [True]


def test_sidecar_path_calls_open_sidecar_legacy_db(monkeypatch):
    from gui.components import dashboard_experimental, dashboard_database

    sidecar_calls = []
    monkeypatch.setattr(dashboard_experimental, "open_sidecar_legacy_db",
                        lambda w: sidecar_calls.append(w))

    widget = _make_widget()
    _run_pick(monkeypatch, "Sidecar", widget)

    assert sidecar_calls == [widget]
