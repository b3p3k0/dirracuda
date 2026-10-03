"""Notes indicators and persistent, multi-select quick filters."""

import tkinter as tk
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gui.components.server_list_window import filters, table
from gui.components.server_list_window.actions.templates import ServerListWindowTemplateMixin
from gui.components.server_list_window.filter_dropdown import (
    QUICK_FILTERS, FilterDropdown, count_active, format_button_label,
)
from gui.components.server_list_window.window import ServerListWindow
from gui.utils.style import SMBSeekTheme


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    yield root
    root.destroy()


@pytest.mark.parametrize("server, expected", [
    ({"notes": None}, False), ({"notes": ""}, False),
    ({"notes": "   \n"}, False), ({"notes": "x"}, True), ({}, False),
])
def test_has_notes(server, expected):
    assert filters.has_notes(server) is expected


def test_apply_has_notes_filter():
    servers = [{"notes": "x"}, {"notes": "  "}, {}, {"notes": None}]
    assert filters.apply_has_notes_filter(servers, False) is servers
    assert filters.apply_has_notes_filter(servers, True) == [servers[0]]


def test_count_and_label():
    assert count_active({}) == 0
    variables = {
        "favorites_only": SimpleNamespace(get=lambda: True),
        "has_notes_only": SimpleNamespace(get=lambda: True),
        "shares_filter": SimpleNamespace(get=lambda: False),
        "unknown": SimpleNamespace(get=lambda: True),
    }
    assert count_active(variables) == 2
    assert format_button_label(0) == "Filters ▾"
    assert format_button_label(2) == "Filters (2) ▾"


def test_notes_column_order_and_icons(tk_root):
    _, tree, *_ = table.create_server_table(tk_root, MagicMock(), {})
    columns = list(tree["columns"])
    start = columns.index("extracted")
    assert columns[start:start + 3] == ["extracted", "notes", "Risk"]
    assert tree.heading("notes", "text") == "Notes"
    assert tree.column("notes", "width") == 60
    table.update_table_display(tree, [
        {"row_key": "S:1", "notes": "reviewed"},
        {"row_key": "F:1", "notes": " \n "},
    ], None)
    assert tree.set("S:1", "notes") == "✔"
    assert tree.set("F:1", "notes") == "○"
    assert table.sort_table_by_column(tree, "notes", "Country", "asc", {}, {}) == (
        "Country", "asc"
    )
    assert tree.get_children() == ("S:1", "F:1")


def make_dropdown(root, variables=None):
    if variables is None:
        variables = {key: tk.BooleanVar(root) for key, _ in QUICK_FILTERS}
    changed = MagicMock()
    dropdown = FilterDropdown(root, SMBSeekTheme(), variables, changed)
    assert dropdown.button.winfo_manager() == ""
    dropdown.button.pack()
    root.update()
    return dropdown, variables, changed


def test_dropdown_stays_open_and_tracks_variables(tk_root):
    dropdown, variables, changed = make_dropdown(tk_root)
    dropdown.open()
    tk_root.update()
    assert dropdown.is_open()
    assert dropdown.popover.overrideredirect()
    assert dropdown.popover.grab_current() is None
    assert list(dropdown.option_widgets) == [key for key, _ in QUICK_FILTERS]
    assert [widget.cget("text") for widget in dropdown.option_widgets.values()] == [
        label for _, label in QUICK_FILTERS
    ]
    assert dropdown.popover.winfo_children() == list(dropdown.option_widgets.values())
    for key in ("favorites_only", "has_notes_only"):
        dropdown.option_widgets[key].invoke()
        assert dropdown.is_open()
        assert variables[key].get() is True
    assert changed.call_count == 2
    assert dropdown.button.cget("text") == "Filters (2) ▾"
    variables["exclude_avoid"].set(True)
    assert dropdown.button.cget("text") == "Filters (3) ▾"
    assert changed.call_count == 2
    dropdown.close()
    assert not dropdown.is_open()
    assert dropdown.option_widgets == {}
    dropdown.close()


def test_disabled_options_and_escape(tk_root):
    dropdown, _, _ = make_dropdown(tk_root)
    dropdown.set_option_state("favorites_only", False)
    dropdown.open()
    tk_root.update()
    assert dropdown.option_widgets["favorites_only"].cget("state") == "disabled"
    dropdown.set_option_state("favorites_only", True)
    assert dropdown.option_widgets["favorites_only"].cget("state") == "normal"
    dropdown.set_option_state("has_notes_only", False)
    assert dropdown.option_widgets["has_notes_only"].cget("state") == "disabled"
    # Xvfb has no window manager to assign keyboard focus for synthetic events.
    dropdown.popover.focus_force()
    dropdown.popover.event_generate("<Escape>")
    tk_root.update()
    assert not dropdown.is_open()


def test_outside_click_preserves_other_bindings(tk_root):
    observed = MagicMock()
    binding = tk_root.bind("<Button-1>", observed, add="+")
    dropdown, _, _ = make_dropdown(tk_root)
    dropdown.open()
    tk_root.update()
    dropdown.button.event_generate("<Button-1>")
    assert dropdown.is_open()
    option = dropdown.option_widgets["has_notes_only"]
    option.event_generate("<Button-1>")
    option.event_generate("<ButtonRelease-1>")
    assert dropdown.is_open()
    tk_root.event_generate("<Button-1>")
    assert not dropdown.is_open()
    assert binding in tk_root.bind("<Button-1>")
    observed.assert_called()


def test_dropdown_destroy_cleanup_and_missing_keys(tk_root):
    var = tk.BooleanVar(tk_root)
    dropdown, _, _ = make_dropdown(tk_root, {"has_notes_only": var, "unknown": object()})
    dropdown.open()
    assert list(dropdown.option_widgets) == ["has_notes_only"]
    dropdown.popover.destroy()
    dropdown.close()
    dropdown.toggle()
    assert dropdown.is_open()
    dropdown.toggle()
    assert not dropdown.is_open()
    dropdown.open()
    dropdown.button.destroy()
    assert not dropdown.is_open()
    assert not var.trace_info()
    var.set(True)
    dropdown.close()


def test_template_compatibility(tk_root):
    state = ServerListWindowTemplateMixin()
    for key, _ in QUICK_FILTERS:
        setattr(state, key, tk.BooleanVar(tk_root, value=True))
    for key in ("protocol_smb", "protocol_ftp", "protocol_http"):
        setattr(state, key, tk.BooleanVar(tk_root, value=True))
    state.search_text = tk.StringVar(tk_root)
    state.date_filter = tk.StringVar(tk_root)
    state.country_listbox = None
    state.is_advanced_mode = False
    state._apply_filters = MagicMock()
    state._update_mode_display = MagicMock()
    state._apply_filter_state({"exclude_compromised": True})
    assert state.has_notes_only.get() is False
    assert state.exclude_compromised.get() is True
    assert state._capture_filter_state()["has_notes_only"] is False
    state.has_notes_only.set(True)
    saved = state._capture_filter_state()
    assert saved["has_notes_only"] is True
    state.has_notes_only.set(False)
    state._apply_filter_state(saved)
    assert state.has_notes_only.get() is True


def test_notes_saved_updates_cell(tk_root):
    _, tree, *_ = table.create_server_table(tk_root, MagicMock(), {})
    row = {"row_key": "S:1", "notes": ""}
    table.update_table_display(tree, [row], None)
    window = SimpleNamespace(tree=tree)
    for notes, icon in (("reviewed", "✔"), (" \n ", "○")):
        row["notes"] = notes
        ServerListWindow._on_notes_saved(window, row)
        assert tree.set("S:1", "notes") == icon
    ServerListWindow._on_notes_saved(window, {"row_key": "missing", "notes": "x"})
    ServerListWindow._on_notes_saved(window, {})
