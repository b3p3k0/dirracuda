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

        def focus_window(self):
            self.focus_calls += 1

    existing = _Existing()
    dorkbook_window._WINDOW_INSTANCE = existing

    constructed = []
    monkeypatch.setattr(
        dorkbook_window,
        "DorkbookWindow",
        lambda *a, **kw: constructed.append((a, kw)),
    )

    dorkbook_window.show_dorkbook_window(MagicMock(), settings_manager=None)

    assert existing.focus_calls == 1
    assert constructed == []


def test_show_dorkbook_window_constructs_new_instance(monkeypatch, tmp_path: Path):
    created = {}

    def _ctor(parent, *, settings_manager=None, db_path=None):
        inst = MagicMock()
        inst.window = MagicMock()
        inst.window.winfo_exists.return_value = True
        created["parent"] = parent
        created["settings_manager"] = settings_manager
        created["db_path"] = db_path
        created["instance"] = inst
        return inst

    monkeypatch.setattr(dorkbook_window, "DorkbookWindow", _ctor)

    parent = MagicMock()
    sm = MagicMock()
    db_path = tmp_path / "dorkbook.db"
    dorkbook_window.show_dorkbook_window(parent, settings_manager=sm, db_path=db_path)

    assert created["parent"] is parent
    assert created["settings_manager"] is sm
    assert created["db_path"] == db_path
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
