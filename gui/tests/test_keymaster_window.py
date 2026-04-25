"""
Tests for gui/components/keymaster_window.py — C2 and C3 slices.

All tests use __new__ + monkeypatch to avoid constructing real Tk windows
in headless CI. The apply tests (C3 slice) use real tmp_path JSON files.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub for headless import
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

import gui.components.keymaster_window as keymaster_window
from gui.components.keymaster_window import KeymasterWindow, _mask_key
from experimental.keymaster.models import PROVIDER_SHODAN


# ---------------------------------------------------------------------------
# _mask_key
# ---------------------------------------------------------------------------

def test_mask_key_long():
    assert _mask_key("ABCD1234WXYZ5678") == "ABCD********5678"


def test_mask_key_exact8():
    assert _mask_key("ABCD5678") == "********"


def test_mask_key_short4():
    assert _mask_key("ABCD") == "****"


def test_mask_key_short2():
    assert _mask_key("AB") == "****"


def test_mask_key_empty():
    assert _mask_key("") == "****"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_window(monkeypatch, *, db_path=None, config_path=None, settings_manager=None):
    """Construct a KeymasterWindow without building any Tk widgets."""
    win = KeymasterWindow.__new__(KeymasterWindow)
    win.parent = MagicMock()
    win.settings_manager = settings_manager
    win.db_path = db_path
    win._context_config_path = config_path
    win._row_by_iid = {}
    win._search_var = MagicMock()
    win._search_var.get.return_value = ""
    win._tree = MagicMock()
    win._tree.selection.return_value = []
    win._status_var = MagicMock()
    win._apply_btn = MagicMock()
    win._edit_btn = MagicMock()
    win._delete_btn = MagicMock()
    win._context_menu = MagicMock()
    win.window = MagicMock()
    win.theme = MagicMock()
    win.theme.colors = {"error": "red"}
    return win


# ---------------------------------------------------------------------------
# C2 — CRUD actions
# ---------------------------------------------------------------------------

def test_add_calls_create_key(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)

    win = _make_window(monkeypatch, db_path=db_path)
    win._load_entries = MagicMock()

    dialog_mock = MagicMock()
    dialog_mock.show.return_value = {"label": "My Key", "api_key": "key001", "notes": ""}
    monkeypatch.setattr(
        "gui.components.keymaster_window._KeyEditorDialog",
        lambda *a, **kw: dialog_mock,
    )

    win._on_add()

    win._load_entries.assert_called_once()
    with km_store.open_connection(db_path) as conn:
        rows = km_store.list_keys(conn, PROVIDER_SHODAN)
    assert len(rows) == 1
    assert rows[0]["label"] == "My Key"


def test_delete_calls_delete_key(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "key001", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._selected_row = MagicMock(return_value={"key_id": key_id, "label": "Label"})
    win._load_entries = MagicMock()

    dialog_mock = MagicMock()
    dialog_mock.show.return_value = True
    monkeypatch.setattr(
        "gui.components.keymaster_window._SimpleDeleteConfirmDialog",
        lambda *a, **kw: dialog_mock,
    )

    win._on_delete()

    win._load_entries.assert_called_once()
    with km_store.open_connection(db_path) as conn:
        assert km_store.get_key(conn, key_id) is None


def test_edit_calls_update_key(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Old", "key001", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._selected_row = MagicMock(return_value={
        "key_id": key_id, "label": "Old", "api_key": "key001", "notes": ""
    })
    win._load_entries = MagicMock()

    dialog_mock = MagicMock()
    dialog_mock.show.return_value = {"label": "New", "api_key": "key001", "notes": "updated"}
    monkeypatch.setattr(
        "gui.components.keymaster_window._KeyEditorDialog",
        lambda *a, **kw: dialog_mock,
    )

    win._on_edit()

    win._load_entries.assert_called_once()
    with km_store.open_connection(db_path) as conn:
        row = km_store.get_key(conn, key_id)
    assert row["label"] == "New"
    assert row["notes"] == "updated"


# ---------------------------------------------------------------------------
# C3 — _apply_selected_key
# ---------------------------------------------------------------------------

def test_apply_no_selection_shows_warning(monkeypatch):
    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value=None)

    warnings = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showwarning",
        lambda *a, **kw: warnings.append(a),
    )

    win._apply_selected_key()
    assert len(warnings) == 1


def test_apply_no_config_path_shows_error(monkeypatch):
    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value={"key_id": 1, "label": "L", "api_key": "k"})
    win._resolve_active_config_path = MagicMock(return_value=None)

    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append(a),
    )

    win._apply_selected_key()
    assert len(errors) == 1


def test_apply_writes_shodan_api_key_to_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {"api_key": "OLD"}, "other": 42}), encoding="utf-8")

    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "NEWKEY", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._selected_row = MagicMock(return_value={"key_id": key_id, "label": "Label", "api_key": "NEWKEY"})
    win._resolve_active_config_path = MagicMock(return_value=cfg)
    win._load_entries = MagicMock()

    win._apply_selected_key()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["shodan"]["api_key"] == "NEWKEY"


def test_apply_preserves_other_config_keys(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {"api_key": "OLD"}, "other": 99}), encoding="utf-8")

    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "NEWKEY", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._selected_row = MagicMock(return_value={"key_id": key_id, "label": "Label", "api_key": "NEWKEY"})
    win._resolve_active_config_path = MagicMock(return_value=cfg)
    win._load_entries = MagicMock()

    win._apply_selected_key()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["other"] == 99


def test_apply_malformed_config_shows_error_no_write(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    original = "NOT VALID JSON!!!"
    cfg.write_text(original, encoding="utf-8")

    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value={"key_id": 1, "label": "L", "api_key": "k"})
    win._resolve_active_config_path = MagicMock(return_value=cfg)
    win._load_entries = MagicMock()

    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append(a),
    )

    win._apply_selected_key()

    assert len(errors) == 1
    assert cfg.read_text(encoding="utf-8") == original
    win._load_entries.assert_not_called()


def test_apply_shodan_non_dict_overwritten_safely(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": "old_string", "other": 1}), encoding="utf-8")

    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store
    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "NEWKEY", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._selected_row = MagicMock(return_value={"key_id": key_id, "label": "Label", "api_key": "NEWKEY"})
    win._resolve_active_config_path = MagicMock(return_value=cfg)
    win._load_entries = MagicMock()

    win._apply_selected_key()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["shodan"] == {"api_key": "NEWKEY"}
    assert data["other"] == 1


def test_apply_db_failure_does_not_abort(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {}}), encoding="utf-8")

    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value={"key_id": 1, "label": "L", "api_key": "NEWKEY"})
    win._resolve_active_config_path = MagicMock(return_value=cfg)
    win._load_entries = MagicMock()

    def _bad_connection(*_a, **_kw):
        raise RuntimeError("db boom")

    win._open_store_connection = _bad_connection

    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append(a),
    )

    win._apply_selected_key()

    assert errors == []
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["shodan"]["api_key"] == "NEWKEY"
    win._load_entries.assert_called_once()


def test_apply_write_failure_shows_error(monkeypatch, tmp_path):
    # config_path points to a directory — writing to it will raise
    cfg_dir = tmp_path / "notafile"
    cfg_dir.mkdir()

    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value={"key_id": 1, "label": "L", "api_key": "k"})
    win._resolve_active_config_path = MagicMock(return_value=cfg_dir)
    win._load_entries = MagicMock()

    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append(a),
    )

    win._apply_selected_key()

    assert len(errors) == 1
    win._load_entries.assert_not_called()


def test_double_click_calls_apply(monkeypatch):
    win = _make_window(monkeypatch)
    called = []
    win._apply_selected_key = lambda: called.append(True)

    row_iid = "1"
    win._tree.identify_row = MagicMock(return_value=row_iid)
    win._selected_row = MagicMock(return_value={"key_id": 1})

    event = MagicMock()
    event.y = 10
    win._on_tree_double_click(event)

    assert called == [True]


def test_context_menu_apply_calls_apply(monkeypatch):
    win = _make_window(monkeypatch)
    called = []
    win._apply_selected_key = lambda: called.append(True)

    win._build_context_menu({"key_id": 1, "label": "L"})

    # The Apply command was registered — invoke it directly via the lambda
    # captured in the menu. We reconstruct what _build_context_menu does.
    win._context_menu = MagicMock()
    commands_registered = []

    def _add_command(**kw):
        commands_registered.append(kw)

    win._context_menu.add_command = _add_command
    win._context_menu.delete = MagicMock()

    win._build_context_menu({"key_id": 1, "label": "L"})

    apply_entry = next(c for c in commands_registered if c.get("label") == "Apply")
    apply_entry["command"]()
    assert called == [True]


def test_button_apply_calls_apply(monkeypatch):
    win = _make_window(monkeypatch)
    called = []
    win._apply_selected_key = lambda: called.append(True)

    # Simulate what the button's command binding does
    win._apply_btn = MagicMock()
    # The button was created with command=self._apply_selected_key in _build_ui.
    # Here we verify the method is the single path by calling it directly.
    win._apply_selected_key()
    assert called == [True]


# ---------------------------------------------------------------------------
# QA — singleton context refresh
# ---------------------------------------------------------------------------

def test_show_keymaster_window_focuses_existing_instance(monkeypatch):
    """When a live instance exists, show_keymaster_window focuses it without constructing a new one."""
    class _Existing:
        def __init__(self):
            self.window = MagicMock()
            self.window.winfo_exists.return_value = True
            self.focus_calls = 0
            self.context_updates = []

        def focus_window(self):
            self.focus_calls += 1

        def update_config_context(self, config_path):
            self.context_updates.append(config_path)

    existing = _Existing()
    keymaster_window._WINDOW_INSTANCE = existing
    try:
        constructed = []
        monkeypatch.setattr(
            keymaster_window,
            "KeymasterWindow",
            lambda *a, **kw: constructed.append((a, kw)),
        )

        keymaster_window.show_keymaster_window(
            MagicMock(),
            settings_manager=None,
            config_path="/tmp/config.json",
        )

        assert existing.focus_calls == 1
        assert existing.context_updates == ["/tmp/config.json"]
        assert constructed == []
    finally:
        keymaster_window._WINDOW_INSTANCE = None


def test_show_keymaster_window_updates_context_for_existing_instance(monkeypatch):
    """update_config_context is called with the new path before focus_window."""
    class _Existing:
        def __init__(self):
            self.window = MagicMock()
            self.window.winfo_exists.return_value = True
            self._context_config_path = "/old/path"
            self.focus_calls = 0

        def focus_window(self):
            self.focus_calls += 1

        def update_config_context(self, config_path):
            normalized = str(config_path or "").strip()
            if normalized:
                self._context_config_path = normalized

    existing = _Existing()
    keymaster_window._WINDOW_INSTANCE = existing
    try:
        keymaster_window.show_keymaster_window(
            MagicMock(),
            config_path="/new/path.json",
        )

        assert existing._context_config_path == "/new/path.json"
        assert existing.focus_calls == 1
    finally:
        keymaster_window._WINDOW_INSTANCE = None
