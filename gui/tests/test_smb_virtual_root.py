"""
Unit tests for SMB virtual root UX (Card U6).

Pattern: SmbBrowserWindow.__new__ factory (no Tk), MagicMock widgets.
Factory tests patch SmbBrowserWindow.__init__ to capture shares arg.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import tkinter as tk

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.unified_browser_window import SmbBrowserWindow, open_smb_browser


# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------


def _make_window(shares=None, share_credentials=None):
    """Create a minimal SmbBrowserWindow without Tk widgets."""
    win = SmbBrowserWindow.__new__(SmbBrowserWindow)
    win.shares = shares if shares is not None else ["data", "public"]
    win.share_credentials = share_credentials or {}
    win.username = "user"
    win.password = "pass"
    win.current_share = None
    win.current_path = "\\"
    win.pending_path = None
    win.busy = False
    win._at_virtual_root = True
    win._entering_share = False
    win.config = {}

    # Mock Tk widgets
    win.tree = MagicMock()
    win.tree.get_children.return_value = []
    win.path_var = MagicMock()
    win.status_var = MagicMock()
    win.window = MagicMock()

    # Buttons
    for attr in ("btn_up", "btn_refresh", "btn_view", "btn_download", "btn_cancel"):
        btn = MagicMock()
        btn.winfo_exists.return_value = True
        setattr(win, attr, btn)

    # Methods that require real Tk/network
    win._disconnect = MagicMock()
    win._start_list_thread = MagicMock()
    win._set_status = MagicMock()

    return win


# ---------------------------------------------------------------------------
# _populate_virtual_root
# ---------------------------------------------------------------------------


def test_populate_virtual_root_inserts_share_rows():
    win = _make_window(shares=["public", "data", "data"])  # duplicate + unsorted
    win._populate_virtual_root()
    calls = win.tree.insert.call_args_list
    # sorted+deduped: data, public
    assert len(calls) == 2
    assert calls[0][1]["values"][0] == "data"
    assert calls[0][1]["values"][1] == "dir"
    assert calls[1][1]["values"][0] == "public"


def test_populate_virtual_root_deduped_count_in_status():
    win = _make_window(shares=["data", "data", "public"])
    win._populate_virtual_root()
    win._set_status.assert_called_with("2 accessible share(s).")


# ---------------------------------------------------------------------------
# _on_item_double_click
# ---------------------------------------------------------------------------


def test_double_click_at_virtual_root_calls_enter_share():
    win = _make_window()
    win._at_virtual_root = True
    win.tree.selection.return_value = ["item-1"]
    win.tree.item.return_value = {"values": ["data", "dir", "", "", "", ""]}
    win._enter_share = MagicMock()

    win._on_item_double_click()

    win._enter_share.assert_called_once_with("data")


def test_double_click_at_share_level_navigates_dir():
    win = _make_window()
    win._at_virtual_root = False
    win.current_share = "data"
    win.current_path = "\\"
    win.tree.selection.return_value = ["item-1"]
    win.tree.item.return_value = {"values": ["docs", "dir", "", "", "", ""]}
    win._navigate_to = MagicMock()
    win._enter_share = MagicMock()

    win._on_item_double_click()

    win._navigate_to.assert_called_once_with("\\docs")
    win._enter_share.assert_not_called()


# ---------------------------------------------------------------------------
# _on_up
# ---------------------------------------------------------------------------


def test_up_from_virtual_root_is_noop():
    win = _make_window()
    win._at_virtual_root = True
    win._go_to_virtual_root = MagicMock()
    win._navigate_to = MagicMock()

    win._on_up()

    win._go_to_virtual_root.assert_not_called()
    win._navigate_to.assert_not_called()


def test_up_from_share_root_goes_to_virtual_root():
    win = _make_window()
    win._at_virtual_root = False
    win.current_path = "\\"
    win._go_to_virtual_root = MagicMock()
    win._navigate_to = MagicMock()

    win._on_up()

    win._go_to_virtual_root.assert_called_once()
    win._navigate_to.assert_not_called()


def test_up_from_subdir_navigates_to_parent():
    win = _make_window()
    win._at_virtual_root = False
    win.current_share = "data"
    win.current_path = "\\sub\\deep"
    win._go_to_virtual_root = MagicMock()
    win._navigate_to = MagicMock()
    win.busy = False

    win._on_up()

    win._navigate_to.assert_called_once_with("\\sub")
    win._go_to_virtual_root.assert_not_called()


# ---------------------------------------------------------------------------
# _enter_share
# ---------------------------------------------------------------------------


def test_enter_share_sets_state_flags():
    win = _make_window()
    win._at_virtual_root = True
    win._entering_share = False

    win._enter_share("data")

    assert win.current_share == "data"
    assert win._at_virtual_root is False
    assert win._entering_share is True


def test_enter_share_applies_credentials():
    creds = {"data": {"username": "admin", "password": "secret"}}
    win = _make_window(share_credentials=creds)

    win._enter_share("data")

    assert win.username == "admin"
    assert win.password == "secret"


# ---------------------------------------------------------------------------
# _handle_list_error (share-open failure path)
# ---------------------------------------------------------------------------


def test_share_open_error_returns_to_virtual_root():
    win = _make_window()
    win._entering_share = True
    win.current_share = "data"
    win._go_to_virtual_root = MagicMock()

    win._handle_list_error("\\", RuntimeError("perm denied"))

    win._go_to_virtual_root.assert_called_once_with(
        status_override="Cannot open 'data': perm denied"
    )


def test_share_open_error_no_modal_dialog():
    win = _make_window()
    win._entering_share = True
    win.current_share = "data"
    win._go_to_virtual_root = MagicMock()

    with patch("gui.components.unified_browser_window.messagebox.showerror") as mock_err:
        win._handle_list_error("\\", RuntimeError("perm denied"))

    mock_err.assert_not_called()


def test_share_open_error_status_persists():
    win = _make_window(shares=["data", "public"])
    win._entering_share = True
    win.current_share = "data"
    # Let _go_to_virtual_root run real — status_override must survive to _set_status

    win._handle_list_error("\\", RuntimeError("perm denied"))

    win._set_status.assert_called_with("Cannot open 'data': perm denied")


# ---------------------------------------------------------------------------
# _set_path
# ---------------------------------------------------------------------------


def test_set_path_shows_share_prefix():
    win = _make_window()
    win._at_virtual_root = False
    win.current_share = "data"

    win._set_path("\\Documents")

    win.path_var.set.assert_called_with("data\\Documents")


def test_set_path_at_virtual_root_shows_bare_path():
    win = _make_window()
    win._at_virtual_root = True

    win._set_path("\\")

    win.path_var.set.assert_called_with("\\")


# ---------------------------------------------------------------------------
# _refresh
# ---------------------------------------------------------------------------


def test_refresh_at_virtual_root_repopulates():
    win = _make_window()
    win._at_virtual_root = True
    win._populate_virtual_root = MagicMock()

    win._refresh()

    win._populate_virtual_root.assert_called_once()
    win._start_list_thread.assert_not_called()


# ---------------------------------------------------------------------------
# Button state
# ---------------------------------------------------------------------------


def test_action_buttons_disabled_at_virtual_root():
    win = _make_window()
    win._at_virtual_root = True
    win.busy = False

    win._update_action_buttons()

    win.btn_up.configure.assert_called_with(state=tk.DISABLED)
    win.btn_view.configure.assert_called_with(state=tk.DISABLED)
    win.btn_download.configure.assert_called_with(state=tk.DISABLED)


def test_set_busy_false_at_virtual_root_keeps_buttons_disabled():
    win = _make_window()
    win._at_virtual_root = True
    win.busy = True  # simulate previously-busy state

    win._set_busy(False)

    # _set_busy enables all, then _update_action_buttons re-disables at virtual root
    win.btn_up.configure.assert_called_with(state=tk.DISABLED)
    win.btn_view.configure.assert_called_with(state=tk.DISABLED)
    win.btn_download.configure.assert_called_with(state=tk.DISABLED)


def test_update_action_buttons_no_op_when_busy():
    win = _make_window()
    win.busy = True

    win._update_action_buttons()

    win.btn_up.configure.assert_not_called()
    win.btn_view.configure.assert_not_called()
    win.btn_download.configure.assert_not_called()


# ---------------------------------------------------------------------------
# _populate_entries
# ---------------------------------------------------------------------------


def test_populate_entries_clears_entering_share_flag():
    win = _make_window()
    win._entering_share = True
    win._at_virtual_root = True
    win.current_share = "data"

    result = MagicMock()
    result.entries = []
    result.truncated = False
    result.warning = None

    win._populate_entries(result, "\\")

    assert win._entering_share is False
    assert win._at_virtual_root is False


# ---------------------------------------------------------------------------
# open_smb_browser factory
# ---------------------------------------------------------------------------


def _make_factory_test():
    """Return a captured-kwargs dict and a fake __init__."""
    captured = {}

    def _fake_init(self, **kwargs):
        captured.update(kwargs)

    return captured, _fake_init


def test_factory_uses_db_shares_over_caller_shares():
    mock_db = MagicMock()
    mock_db.get_accessible_shares.return_value = [{"share_name": "sysdata"}]
    mock_db.get_smb_shodan_data.return_value = None
    captured, fake_init = _make_factory_test()

    with patch.object(SmbBrowserWindow, "__init__", fake_init):
        open_smb_browser(
            parent=MagicMock(),
            ip_address="1.2.3.4",
            shares=["stale"],
            db_reader=mock_db,
        )

    assert captured["shares"] == ["sysdata"]


def test_factory_uses_empty_db_result_not_stale_caller_shares():
    mock_db = MagicMock()
    mock_db.get_accessible_shares.return_value = []
    mock_db.get_smb_shodan_data.return_value = None
    captured, fake_init = _make_factory_test()

    with patch.object(SmbBrowserWindow, "__init__", fake_init):
        open_smb_browser(
            parent=MagicMock(),
            ip_address="1.2.3.4",
            shares=["stale"],
            db_reader=mock_db,
        )

    assert captured["shares"] == []


def test_factory_falls_back_to_caller_shares_on_db_exception():
    mock_db = MagicMock()
    mock_db.get_accessible_shares.side_effect = RuntimeError("db error")
    mock_db.get_smb_shodan_data.return_value = None
    captured, fake_init = _make_factory_test()

    with patch.object(SmbBrowserWindow, "__init__", fake_init):
        open_smb_browser(
            parent=MagicMock(),
            ip_address="1.2.3.4",
            shares=["clean"],
            db_reader=mock_db,
        )

    assert captured["shares"] == ["clean"]


def test_factory_normalizes_share_names():
    mock_db = MagicMock()
    mock_db.get_accessible_shares.return_value = [
        {"share_name": " DATA$ "},
        {"share_name": "  "},  # whitespace-only — must be dropped
    ]
    mock_db.get_smb_shodan_data.return_value = None
    captured, fake_init = _make_factory_test()

    with patch.object(SmbBrowserWindow, "__init__", fake_init):
        open_smb_browser(
            parent=MagicMock(),
            ip_address="1.2.3.4",
            shares=[],
            db_reader=mock_db,
        )

    assert captured["shares"] == ["DATA$"]


def test_factory_normalizes_fallback_caller_shares():
    mock_db = MagicMock()
    mock_db.get_accessible_shares.side_effect = RuntimeError("db error")
    mock_db.get_smb_shodan_data.return_value = None
    captured, fake_init = _make_factory_test()

    with patch.object(SmbBrowserWindow, "__init__", fake_init):
        open_smb_browser(
            parent=MagicMock(),
            ip_address="1.2.3.4",
            shares=[" DATA$ "],  # dirty caller name
            db_reader=mock_db,
        )

    assert captured["shares"] == ["DATA$"]
