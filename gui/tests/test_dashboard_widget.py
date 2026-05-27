"""Headless unit tests for DashboardWidget button layout and DB surface delegation (C5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gui.dashboard.widget import DashboardWidget
from gui.components import dashboard_database


def _make_stub_widget():
    widget = DashboardWidget.__new__(DashboardWidget)
    widget.parent = MagicMock()
    widget.theme = MagicMock()
    widget.theme.apply_to_widget = MagicMock()
    widget.theme.create_styled_label = MagicMock(return_value=MagicMock(pack=MagicMock()))
    widget.theme.fonts = {"small": ("Arial", 9)}
    widget.theme.colors = {
        "text_secondary": "#888",
        "log_bg": "#111418",
        "log_fg": "#f5f5f5",
        "log_placeholder": "#9ea4b3",
    }
    widget.main_frame = MagicMock()
    widget._theme_toggle_button_text = lambda: "☀️"
    # Required sentinels from __init__
    widget.servers_button = None
    widget.db_tools_button = None
    widget.db_button = None
    widget.experimental_button = None
    widget.config_button = None
    widget.about_button = None
    widget.theme_toggle_button = None
    widget.copy_log_button = None
    widget.reddit_grab_button = None
    widget.running_tasks_button = None
    return widget


def test_db_button_attribute_exists_after_build_header(monkeypatch):
    import tkinter as tk

    monkeypatch.setattr(tk, "Frame", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(tk, "Button", lambda *a, **kw: MagicMock())

    widget = _make_stub_widget()
    widget._build_header_section()

    assert widget.db_button is not None
    # Old individual buttons must remain as None sentinels
    assert widget.servers_button is None
    assert widget.db_tools_button is None


def test_open_db_surface_delegates_to_satellite(monkeypatch):
    widget = _make_stub_widget()
    calls = []
    monkeypatch.setattr(dashboard_database, "open_db_surface", lambda w: calls.append(w))
    widget._open_db_surface()
    assert calls == [widget]


def test_open_db_tools_alias_delegates_to_open_db_surface(monkeypatch):
    widget = _make_stub_widget()
    surface_calls = []
    monkeypatch.setattr(dashboard_database, "open_db_surface", lambda w: surface_calls.append(w))
    widget._open_db_tools()
    assert surface_calls == [widget]
