"""
Card 4 unit tests: row_key-based selection, per-row-field favorites/avoid filter,
and click-callback wiring.

Imports are kept isolated from the full window/package stack so that tests
collect and run without GUI or impacket deps.
"""
import importlib
import sys
import types
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Lightweight import isolation helpers
# ---------------------------------------------------------------------------

def _stub_module(name):
    """Insert an empty stub module so heavy imports don't fail at collection."""
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _import_table_module():
    """
    Import gui/components/server_list_window/table.py directly by file path,
    bypassing the package __init__ (which would pull in window.py → full stack).
    """
    import importlib.util, os
    path = os.path.join(
        os.path.dirname(__file__), "..", "components", "server_list_window", "table.py"
    )
    spec = importlib.util.spec_from_file_location("_table_isolated", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_filters_module():
    import importlib.util, os
    path = os.path.join(
        os.path.dirname(__file__), "..", "components", "server_list_window", "filters.py"
    )
    spec = importlib.util.spec_from_file_location("_filters_isolated", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_window_module():
    """
    Import the real server_list_window.window module with lightweight stubs for
    heavy GUI deps that are irrelevant to callback wiring tests.
    """
    # Stub only modules known to pull optional/runtime-heavy dependencies
    # (impacket, image stack, etc.) during import.
    stubs = {
        "gui.components.file_browser_window": {"FileBrowserWindow": type("FileBrowserWindow", (), {})},
        "gui.components.pry_dialog": {"PryDialog": type("PryDialog", (), {})},
        "gui.components.pry_status_dialog": {"BatchStatusDialog": type("BatchStatusDialog", (), {})},
        "gui.components.batch_extract_dialog": {
            "BatchExtractSettingsDialog": type("BatchExtractSettingsDialog", (), {})
        },
    }
    sentinel = object()
    prior = {name: sys.modules.get(name, sentinel) for name in stubs}
    prior_pkg = sys.modules.get("gui.components.server_list_window", sentinel)
    prior_window = sys.modules.get("gui.components.server_list_window.window", sentinel)

    # Force a fresh import under controlled stubs.
    sys.modules.pop("gui.components.server_list_window.window", None)
    sys.modules.pop("gui.components.server_list_window", None)

    try:
        for module_name, attrs in stubs.items():
            mod = types.ModuleType(module_name)
            for attr_name, value in attrs.items():
                setattr(mod, attr_name, value)
            sys.modules[module_name] = mod

        # Import the real production module so callback wiring assertions track source.
        window_mod = importlib.import_module("gui.components.server_list_window.window")
    finally:
        # Restore global import state to avoid leaking test stubs into unrelated tests.
        for module_name, previous in prior.items():
            if previous is sentinel:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

        if prior_window is sentinel:
            sys.modules.pop("gui.components.server_list_window.window", None)
        else:
            sys.modules["gui.components.server_list_window.window"] = prior_window

        if prior_pkg is sentinel:
            sys.modules.pop("gui.components.server_list_window", None)
        else:
            sys.modules["gui.components.server_list_window"] = prior_pkg

    return window_mod


# Lazy-load so failures are scoped to individual tests, not collection
_table = None
_filters = None
_window = None


def _get_table():
    global _table
    if _table is None:
        # table.py needs tkinter at import time for type hints / constants
        # Stub the minimal ttk/tk surface if not available
        try:
            import tkinter  # noqa: F401
        except ImportError:
            _stub_module("tkinter")
            _stub_module("tkinter.ttk")
            _stub_module("tkinter.messagebox")
        _table = _import_table_module()
    return _table


def _get_filters():
    global _filters
    if _filters is None:
        _filters = _import_filters_module()
    return _filters


def _get_window():
    global _window
    if _window is None:
        _window = _import_window_module()
    return _window


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_tree_stub(selected_iids):
    tree = MagicMock()
    tree.selection.return_value = list(selected_iids)
    return tree


def _servers():
    return [
        {"row_key": "S:1", "ip_address": "1.2.3.4", "host_type": "S",
         "favorite": 1, "avoid": 0, "accessible_shares": 2},
        {"row_key": "F:1", "ip_address": "1.2.3.4", "host_type": "F",
         "favorite": 0, "avoid": 1, "accessible_shares": 0},
        {"row_key": "S:2", "ip_address": "9.9.9.9", "host_type": "S",
         "favorite": 0, "avoid": 0, "accessible_shares": 1},
    ]


# ---------------------------------------------------------------------------
# Test 1: get_selected_server_data uses row_key identity
# ---------------------------------------------------------------------------

class TestGetSelectedServerData:
    """Selection must use row_key iid so same-IP S+F rows never collide."""

    def test_selects_only_smb_row_for_duplicate_ip(self):
        tbl = _get_table()
        tree = _make_tree_stub(["S:1"])
        result = tbl.get_selected_server_data(tree, _servers())
        assert len(result) == 1
        assert result[0]["row_key"] == "S:1"

    def test_selects_only_ftp_row_for_duplicate_ip(self):
        tbl = _get_table()
        tree = _make_tree_stub(["F:1"])
        result = tbl.get_selected_server_data(tree, _servers())
        assert len(result) == 1
        assert result[0]["row_key"] == "F:1"

    def test_multi_select_across_protocols(self):
        tbl = _get_table()
        tree = _make_tree_stub(["S:1", "S:2"])
        result = tbl.get_selected_server_data(tree, _servers())
        assert {r["row_key"] for r in result} == {"S:1", "S:2"}

    def test_empty_selection(self):
        tbl = _get_table()
        tree = _make_tree_stub([])
        assert tbl.get_selected_server_data(tree, _servers()) == []


# ---------------------------------------------------------------------------
# Test 2: apply_favorites_filter uses DB row field
# ---------------------------------------------------------------------------

class TestApplyFavoritesFilter:
    """Filter reads server['favorite'] — settings_manager must not be consulted."""

    def test_shows_only_favorited_row(self):
        f = _get_filters()
        result = f.apply_favorites_filter(_servers(), favorites_only=True)
        assert len(result) == 1
        assert result[0]["row_key"] == "S:1"

    def test_same_ip_ftp_row_excluded_when_unfavorited(self):
        f = _get_filters()
        result = f.apply_favorites_filter(_servers(), favorites_only=True)
        assert all(r["row_key"] != "F:1" for r in result)

    def test_filter_off_returns_all(self):
        f = _get_filters()
        assert len(f.apply_favorites_filter(_servers(), favorites_only=False)) == 3

    def test_settings_manager_not_consulted(self):
        f = _get_filters()
        sm = MagicMock()
        sm.get_favorite_servers.return_value = ["9.9.9.9"]  # S:2's IP, but DB flag is 0
        result = f.apply_favorites_filter(_servers(), favorites_only=True, settings_manager=sm)
        assert all(r["row_key"] != "S:2" for r in result)
        sm.get_favorite_servers.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: apply_exclude_avoid_filter uses DB row field
# ---------------------------------------------------------------------------

class TestApplyExcludeAvoidFilter:
    """Filter reads server['avoid'] — S and F siblings are evaluated independently."""

    def test_excludes_only_avoided_row(self):
        f = _get_filters()
        result = f.apply_exclude_avoid_filter(_servers(), exclude_avoid=True)
        keys = {r["row_key"] for r in result}
        assert "F:1" not in keys
        assert {"S:1", "S:2"}.issubset(keys)

    def test_smb_row_kept_when_ftp_sibling_avoided(self):
        f = _get_filters()
        result = f.apply_exclude_avoid_filter(_servers(), exclude_avoid=True)
        assert any(r["row_key"] == "S:1" for r in result)

    def test_filter_off_returns_all(self):
        f = _get_filters()
        assert len(f.apply_exclude_avoid_filter(_servers(), exclude_avoid=False)) == 3


# ---------------------------------------------------------------------------
# Test 4: apply_protocol_filter keeps only selected host types
# ---------------------------------------------------------------------------

class TestApplyProtocolFilter:
    def test_single_protocol_selection(self):
        f = _get_filters()
        servers = _servers() + [{
            "row_key": "H:1", "ip_address": "7.7.7.7", "host_type": "H",
            "favorite": 0, "avoid": 0, "accessible_shares": 1,
        }]
        result = f.apply_protocol_filter(servers, ["H"])
        assert [row["row_key"] for row in result] == ["H:1"]

    def test_multi_protocol_selection(self):
        f = _get_filters()
        servers = _servers() + [{
            "row_key": "H:1", "ip_address": "7.7.7.7", "host_type": "H",
            "favorite": 0, "avoid": 0, "accessible_shares": 1,
        }]
        result = f.apply_protocol_filter(servers, ["S", "H"])
        keys = {row["row_key"] for row in result}
        assert keys == {"S:1", "S:2", "H:1"}

    def test_empty_selection_returns_no_rows(self):
        f = _get_filters()
        assert f.apply_protocol_filter(_servers(), []) == []


# ---------------------------------------------------------------------------
# Test 5: apply_shares_filter applies equally to SMB and FTP rows
# ---------------------------------------------------------------------------

class TestApplySharesFilter:
    """Shares filter uses accessible_shares for all protocol rows."""

    def test_shares_filter_hides_zero_share_ftp_rows(self):
        f = _get_filters()
        result = f.apply_shares_filter(_servers(), shares_only=True)
        keys = {r["row_key"] for r in result}
        assert "F:1" not in keys

    def test_shares_filter_includes_ftp_when_accessible_count_positive(self):
        f = _get_filters()
        servers = _servers() + [{
            "row_key": "F:2", "ip_address": "8.8.8.8", "host_type": "F",
            "favorite": 0, "avoid": 0, "accessible_shares": 0,
        }]
        servers[-1]["accessible_shares"] = 2
        result = f.apply_shares_filter(servers, shares_only=True)
        keys = {r["row_key"] for r in result}
        assert "F:2" in keys
        assert "S:1" in keys and "S:2" in keys
        assert "F:1" not in keys


# ---------------------------------------------------------------------------
# Test 6: _on_treeview_click callback wiring (no stale method refs)
# ---------------------------------------------------------------------------

class TestClickCallbackWiring:
    """
    Verify callback wiring against the real ServerListWindow._on_treeview_click
    implementation (not a duplicated fake method).
    """

    def _make_window_instance(self):
        """
        Build a minimal instance of the real class without running __init__
        (avoids Tk window construction in unit tests).
        """
        window_mod = _get_window()
        win = window_mod.ServerListWindow.__new__(window_mod.ServerListWindow)
        win.tree = MagicMock()
        win.settings_manager = None
        win._apply_filters = MagicMock()
        win._apply_flag_toggle = MagicMock()
        return window_mod, win

    def test_favorite_toggle_key_calls_apply_flag_toggle(self, monkeypatch):
        window_mod, win = self._make_window_instance()
        captured = {}

        def _capture_handle(tree, event, settings_manager, callbacks):
            captured["tree"] = tree
            captured["settings_manager"] = settings_manager
            captured["callbacks"] = callbacks
            return "break"

        monkeypatch.setattr(window_mod.table, "handle_treeview_click", _capture_handle)
        window_mod.ServerListWindow._on_treeview_click(win, event=object())

        callbacks = captured["callbacks"]
        callbacks["on_favorite_toggle"]("S:1", 1)
        win._apply_flag_toggle.assert_called_once_with("S:1", "favorite", 1)
        assert captured["tree"] is win.tree

    def test_avoid_toggle_key_calls_apply_flag_toggle(self, monkeypatch):
        window_mod, win = self._make_window_instance()
        captured = {}

        def _capture_handle(tree, event, settings_manager, callbacks):
            captured["callbacks"] = callbacks
            return "break"

        monkeypatch.setattr(window_mod.table, "handle_treeview_click", _capture_handle)
        window_mod.ServerListWindow._on_treeview_click(win, event=object())

        callbacks = captured["callbacks"]
        callbacks["on_avoid_toggle"]("F:1", 0)
        win._apply_flag_toggle.assert_called_once_with("F:1", "avoid", 0)

    def test_no_stale_method_on_favorite_toggle_attr(self):
        """_on_favorite_toggle must not exist on the window (was removed)."""
        window_mod, _ = self._make_window_instance()
        assert not hasattr(window_mod.ServerListWindow, "_on_favorite_toggle"), (
            "_on_favorite_toggle still exists — stale method not cleaned up"
        )

    def test_no_stale_method_on_avoid_toggle_attr(self):
        window_mod, _ = self._make_window_instance()
        assert not hasattr(window_mod.ServerListWindow, "_on_avoid_toggle"), (
            "_on_avoid_toggle still exists — stale method not cleaned up"
        )
