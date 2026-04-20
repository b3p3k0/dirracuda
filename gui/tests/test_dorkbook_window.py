"""Unit tests for gui.components.dorkbook_window."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so GUI modules import cleanly in headless test env.
if "impacket" not in sys.modules:
    _imod = types.ModuleType("impacket")
    _ismb = types.ModuleType("impacket.smb")
    _ismb.SMB2_DIALECT_002 = object()
    _iconn = types.ModuleType("impacket.smbconnection")
    _iconn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _iconn.SessionError = _SessionError
    _imod.smb = _ismb
    sys.modules["impacket"] = _imod
    sys.modules["impacket.smb"] = _ismb
    sys.modules["impacket.smbconnection"] = _iconn

import gui.components.dorkbook_window as dorkbook_window
from experimental.dorkbook.models import PROTOCOL_HTTP, ROW_KIND_BUILTIN


@pytest.fixture(autouse=True)
def _reset_singleton():
    dorkbook_window._WINDOW_INSTANCE = None
    yield
    dorkbook_window._WINDOW_INSTANCE = None


def test_resolve_initial_protocol_uses_default_when_missing_settings_manager():
    assert dorkbook_window._resolve_initial_protocol(None) == "SMB"


def test_resolve_initial_protocol_reads_settings_manager():
    sm = MagicMock()
    sm.get_setting.return_value = "http"
    assert dorkbook_window._resolve_initial_protocol(sm) == PROTOCOL_HTTP


def test_is_builtin_row_detects_builtin():
    assert dorkbook_window._is_builtin_row({"row_kind": ROW_KIND_BUILTIN}) is True
    assert dorkbook_window._is_builtin_row({"row_kind": "custom"}) is False
    assert dorkbook_window._is_builtin_row(None) is False


def test_clipboard_payload_is_query_only():
    row = {"nickname": "x", "query": "site:* intitle:\"index of /\"", "notes": "n"}
    assert dorkbook_window._clipboard_payload_for_row(row) == "site:* intitle:\"index of /\""


def test_show_dorkbook_window_focuses_existing_instance(monkeypatch):
    class _Existing:
        def __init__(self):
            self.window = MagicMock()
            self.window.winfo_exists.return_value = True
            self.focus_calls = 0
            self.context_updates = []

        def focus_window(self):
            self.focus_calls += 1

        def update_scan_query_context(self, scan_query_config_path):
            self.context_updates.append(scan_query_config_path)

    existing = _Existing()
    dorkbook_window._WINDOW_INSTANCE = existing

    constructed = []
    monkeypatch.setattr(
        dorkbook_window,
        "DorkbookWindow",
        lambda *a, **kw: constructed.append((a, kw)),
    )

    dorkbook_window.show_dorkbook_window(
        MagicMock(),
        settings_manager=None,
        scan_query_config_path="/tmp/config.json",
    )

    assert existing.focus_calls == 1
    assert existing.context_updates == ["/tmp/config.json"]
    assert constructed == []


def test_show_dorkbook_window_constructs_new_instance(monkeypatch, tmp_path: Path):
    created = {}

    def _ctor(parent, *, settings_manager=None, db_path=None, scan_query_config_path=None):
        inst = MagicMock()
        inst.window = MagicMock()
        inst.window.winfo_exists.return_value = True
        created["parent"] = parent
        created["settings_manager"] = settings_manager
        created["db_path"] = db_path
        created["scan_query_config_path"] = scan_query_config_path
        created["instance"] = inst
        return inst

    monkeypatch.setattr(dorkbook_window, "DorkbookWindow", _ctor)

    parent = MagicMock()
    sm = MagicMock()
    db_path = tmp_path / "dorkbook.db"
    dorkbook_window.show_dorkbook_window(
        parent,
        settings_manager=sm,
        db_path=db_path,
        scan_query_config_path="/tmp/config.json",
    )

    assert created["parent"] is parent
    assert created["settings_manager"] is sm
    assert created["db_path"] == db_path
    assert created["scan_query_config_path"] == "/tmp/config.json"
    assert dorkbook_window._WINDOW_INSTANCE is created["instance"]


def test_show_dorkbook_window_handles_constructor_failure(monkeypatch):
    def _raise_ctor(*_a, **_kw):
        raise RuntimeError("boom")

    errors = []
    monkeypatch.setattr(dorkbook_window, "DorkbookWindow", _raise_ctor)
    monkeypatch.setattr(
        dorkbook_window.messagebox,
        "showerror",
        lambda title, message, parent=None: errors.append((title, message, parent)),
    )

    parent = MagicMock()
    dorkbook_window.show_dorkbook_window(parent, settings_manager=None)

    assert dorkbook_window._WINDOW_INSTANCE is None
    assert errors
    assert errors[0][0] == "Dorkbook Unavailable"
    assert "boom" in errors[0][1]


def test_confirm_delete_skips_dialog_when_session_muted(monkeypatch):
    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win.window = MagicMock()
    win.theme = MagicMock()

    monkeypatch.setattr(dorkbook_window, "get_flag", lambda key, default=False: True)
    dialog_calls = []

    class _Dialog:
        def __init__(self, *a, **kw):
            dialog_calls.append(True)

    monkeypatch.setattr(dorkbook_window, "_DeleteConfirmDialog", _Dialog)

    assert win._confirm_delete({"query": "x"}) is True
    assert dialog_calls == []


def test_confirm_delete_sets_mute_flag_when_requested(monkeypatch):
    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win.window = MagicMock()
    win.theme = MagicMock()

    monkeypatch.setattr(dorkbook_window, "get_flag", lambda key, default=False: False)
    set_calls = []
    monkeypatch.setattr(dorkbook_window, "set_flag", lambda key, value=True: set_calls.append((key, value)))

    class _Dialog:
        def __init__(self, *a, **kw):
            pass

        def show(self):
            return True, True

    monkeypatch.setattr(dorkbook_window, "_DeleteConfirmDialog", _Dialog)

    assert win._confirm_delete({"query": "x"}) is True
    assert set_calls == [(dorkbook_window.DORKBOOK_DELETE_CONFIRM_MUTE_KEY, True)]


def test_on_use_in_discovery_dorks_populates_editor_and_sets_status(monkeypatch):
    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win.window = MagicMock()
    win.settings_manager = MagicMock()
    win._scan_query_config_path = "/tmp/config.json"

    class _Status:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    status = _Status()
    win._tab_by_protocol = {
        "HTTP": {"status_var": status},
    }
    win._selected_row = lambda _protocol: {"query": "http.title:\"Index of /\""}
    win._resolve_scan_query_config_path = lambda: "/tmp/config.json"

    calls = []
    monkeypatch.setattr(
        dorkbook_window,
        "populate_discovery_dork_from_dorkbook",
        lambda **kwargs: calls.append(kwargs),
    )

    win._on_use_in_discovery_dorks("HTTP")

    assert len(calls) == 1
    assert calls[0]["protocol"] == "HTTP"
    assert calls[0]["query"] == "http.title:\"Index of /\""
    assert calls[0]["config_path"] == "/tmp/config.json"
    assert "Click Save there to persist" in status.value


def test_on_use_in_discovery_dorks_warns_when_context_missing(monkeypatch):
    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win.window = MagicMock()
    win._tab_by_protocol = {"SMB": {"status_var": MagicMock()}}
    win._selected_row = lambda _protocol: {"query": "smb authentication: disabled"}
    win._resolve_scan_query_config_path = lambda: None

    warnings = []
    monkeypatch.setattr(
        dorkbook_window.messagebox,
        "showwarning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    win._on_use_in_discovery_dorks("SMB")

    assert len(warnings) == 1
    assert warnings[0][0][0] == "Discovery Dorks Context Missing"


def test_tree_double_click_invokes_use_action():
    class _Tree:
        def __init__(self):
            self.selection = []
            self.focused = None

        def identify_row(self, _y):
            return "row-1"

        def selection_set(self, row_iid):
            self.selection = [row_iid]

        def focus(self, row_iid):
            self.focused = row_iid

    tree = _Tree()
    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win._tab_by_protocol = {"FTP": {"tree": tree}}
    win._selected_row = lambda _protocol: {"query": "port:21"}
    win._set_action_visibility = lambda _protocol, _row: None

    calls = []
    win._on_use_in_discovery_dorks = lambda protocol: calls.append(protocol)

    class _Evt:
        y = 10

    win._on_tree_double_click("FTP", _Evt())

    assert tree.selection == ["row-1"]
    assert tree.focused == "row-1"
    assert calls == ["FTP"]


def test_build_context_menu_includes_use_action():
    labels = []

    class _Menu:
        def delete(self, *_args, **_kwargs):
            labels.clear()

        def add_command(self, *, label, command):
            labels.append(label)

    win = dorkbook_window.DorkbookWindow.__new__(dorkbook_window.DorkbookWindow)
    win._tab_by_protocol = {"SMB": {"context_menu": _Menu()}}
    win._on_add = lambda _protocol: None
    win._on_copy = lambda _protocol: None
    win._on_use_in_discovery_dorks = lambda _protocol: None
    win._on_edit = lambda _protocol: None
    win._on_delete = lambda _protocol: None

    win._build_context_menu("SMB", {"query": "smb authentication: disabled", "row_kind": "builtin"})

    assert "Add" in labels
    assert "Copy" in labels
    assert "Use in Discovery Dorks" in labels
