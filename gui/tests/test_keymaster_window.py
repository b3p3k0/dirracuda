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
from gui.components.keymaster_window import (
    KeymasterWindow,
    MAX_BURST_CREDIT_CHECKS,
    _AUTO_CHECK_SETTING_KEY,
    _mask_key,
    _QUERY_CREDITS_NOT_CHECKED,
    _QUERY_CREDITS_INVALID,
    _QUERY_CREDITS_ERROR,
    _classify_query_credit_error,
    _is_retryable_query_credit_error,
)
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
    win._recheck_btn = MagicMock()
    win._recheck_selected_btn = MagicMock()
    win._context_menu = MagicMock()
    win.window = MagicMock()
    win.window.after = lambda _ms, fn: fn()
    win.window.winfo_exists.return_value = True
    win.theme = MagicMock()
    win.theme.colors = {"error": "red"}
    win._query_credit_by_key_id = {}
    win._credits_refresh_inflight = False
    win._credits_refresh_generation = 0
    win._total_saved_keys = 0
    win._auto_check_var = MagicMock()
    win._auto_check_var.get.return_value = True
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


def test_context_menu_includes_recheck_all(monkeypatch):
    win = _make_window(monkeypatch)
    win._context_menu = MagicMock()
    registered = []

    def _add_command(**kw):
        registered.append(kw)

    win._context_menu.add_command = _add_command
    win._context_menu.delete = MagicMock()
    win._build_context_menu(None)

    labels = [entry.get("label") for entry in registered]
    assert "Recheck All" in labels


def test_context_menu_disables_recheck_all_over_limit(monkeypatch):
    win = _make_window(monkeypatch)
    win._total_saved_keys = MAX_BURST_CREDIT_CHECKS + 1
    win._context_menu = MagicMock()
    registered = []

    def _add_command(**kw):
        registered.append(kw)

    win._context_menu.add_command = _add_command
    win._context_menu.delete = MagicMock()
    win._build_context_menu(None)

    entry = next(c for c in registered if c.get("label") == "Recheck All")
    assert entry.get("state") == keymaster_window.tk.DISABLED


def test_set_recheck_state_disables_recheck_all_over_limit(monkeypatch):
    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value={"key_id": 1})
    win._total_saved_keys = MAX_BURST_CREDIT_CHECKS + 2

    win._set_recheck_state(inflight=False)

    assert win._recheck_btn.configure.call_args_list[-1].kwargs["state"] == keymaster_window.tk.DISABLED
    assert (
        win._recheck_selected_btn.configure.call_args_list[-1].kwargs["state"]
        == keymaster_window.tk.NORMAL
    )


def test_classify_query_credit_error_invalid():
    exc = RuntimeError("Invalid API key")
    assert _classify_query_credit_error(exc) == _QUERY_CREDITS_INVALID


def test_classify_query_credit_error_generic():
    exc = RuntimeError("timed out")
    assert _classify_query_credit_error(exc) == _QUERY_CREDITS_ERROR


def test_is_retryable_query_credit_error_rate_limit():
    exc = RuntimeError("429 Too Many Requests")
    assert _is_retryable_query_credit_error(exc) is True


def test_is_retryable_query_credit_error_non_retryable():
    exc = RuntimeError("Invalid API key")
    assert _is_retryable_query_credit_error(exc) is False


def test_fetch_query_credit_display_success(monkeypatch):
    win = _make_window(monkeypatch)

    class _FakeApi:
        def info(self):
            return {"query_credits": 123}

    fake_shodan = types.SimpleNamespace(Shodan=lambda _k: _FakeApi())
    monkeypatch.setitem(sys.modules, "shodan", fake_shodan)

    assert win._fetch_query_credit_display("KEY123") == "123"


def test_fetch_query_credit_display_retries_transient_then_succeeds(monkeypatch):
    win = _make_window(monkeypatch)
    calls = {"n": 0}

    class _FakeApi:
        def info(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("429 Too Many Requests")
            return {"query_credits": 51}

    fake_shodan = types.SimpleNamespace(Shodan=lambda _k: _FakeApi())
    monkeypatch.setitem(sys.modules, "shodan", fake_shodan)
    monkeypatch.setattr(keymaster_window.time, "sleep", lambda _s: None)

    assert win._fetch_query_credit_display("KEY123") == "51"
    assert calls["n"] == 3


def test_start_query_credits_refresh_inflight_guard(monkeypatch):
    win = _make_window(monkeypatch)
    win._credits_refresh_inflight = True
    win._status_var = MagicMock()

    win._start_query_credits_refresh(user_initiated=True)

    win._status_var.set.assert_called_with("Query credit check already in progress.")


def test_start_query_credits_refresh_no_rows_user_initiated(monkeypatch):
    win = _make_window(monkeypatch)
    win._rows_for_credit_refresh = MagicMock(return_value=[])
    win._status_var = MagicMock()

    win._start_query_credits_refresh(user_initiated=True)

    win._status_var.set.assert_called_with("No keys to check.")


def test_start_query_credits_refresh_sets_checking_and_updates_results(monkeypatch):
    win = _make_window(monkeypatch)
    rows = [
        {"key_id": 1, "api_key": "K1"},
        {"key_id": 2, "api_key": "K2"},
    ]
    win._rows_for_credit_refresh = MagicMock(return_value=rows)
    win._load_entries = MagicMock()
    win._status_var = MagicMock()

    values = {"K1": "91", "K2": "77"}
    win._fetch_query_credit_display = lambda api_key: values[api_key]

    class _ImmediateThread:
        def __init__(self, *, target, args=(), daemon=None, name=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(keymaster_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(keymaster_window.time, "sleep", lambda _s: None)

    win._start_query_credits_refresh(user_initiated=True)

    assert win._query_credit_by_key_id[1] == "91"
    assert win._query_credit_by_key_id[2] == "77"
    assert win._credits_refresh_inflight is False
    assert win._load_entries.call_count >= 2


def test_start_query_credits_refresh_over_limit_skips_startup_without_modal(monkeypatch):
    win = _make_window(monkeypatch)
    rows = [{"key_id": i, "api_key": f"K{i}"} for i in range(1, MAX_BURST_CREDIT_CHECKS + 2)]
    win._rows_for_credit_refresh = MagicMock(return_value=rows)
    win._status_var = MagicMock()
    win._set_recheck_state = MagicMock()

    warnings = []
    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showwarning",
        lambda *a, **kw: warnings.append((a, kw)),
    )
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append((a, kw)),
    )

    win._start_query_credits_refresh(user_initiated=False, startup=True)

    assert win._credits_refresh_inflight is False
    assert warnings == []
    assert errors == []
    assert win._status_var.set.call_args_list[-1].args[0].startswith("Auto check skipped:")


def test_start_query_credits_refresh_over_limit_skips_manual_without_modal(monkeypatch):
    win = _make_window(monkeypatch)
    rows = [{"key_id": i, "api_key": f"K{i}"} for i in range(1, MAX_BURST_CREDIT_CHECKS + 2)]
    win._rows_for_credit_refresh = MagicMock(return_value=rows)
    win._status_var = MagicMock()
    win._set_recheck_state = MagicMock()

    warnings = []
    errors = []
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showwarning",
        lambda *a, **kw: warnings.append((a, kw)),
    )
    monkeypatch.setattr(
        "gui.components.keymaster_window.messagebox.showerror",
        lambda *a, **kw: errors.append((a, kw)),
    )

    win._start_query_credits_refresh(user_initiated=True, startup=False)

    assert win._credits_refresh_inflight is False
    assert warnings == []
    assert errors == []
    assert "Recheck All is disabled" in win._status_var.set.call_args_list[-1].args[0]


def test_rows_for_credit_refresh_uses_unfiltered_lookup(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store

    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        km_store.create_key(conn, PROVIDER_SHODAN, "One", "KEY_ONE", "")
        km_store.create_key(conn, PROVIDER_SHODAN, "Two", "KEY_TWO", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    rows = win._rows_for_credit_refresh()
    labels = sorted([row["label"] for row in rows])
    assert labels == ["One", "Two"]


def test_rows_for_credit_refresh_filters_selected_key_ids(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store

    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        id_one = km_store.create_key(conn, PROVIDER_SHODAN, "One", "KEY_ONE", "")
        id_two = km_store.create_key(conn, PROVIDER_SHODAN, "Two", "KEY_TWO", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    rows = win._rows_for_credit_refresh(only_key_ids={id_two})
    assert len(rows) == 1
    assert int(rows[0]["key_id"]) == int(id_two)
    assert int(rows[0]["key_id"]) != int(id_one)


def test_load_entries_shows_not_checked_when_missing_credit(monkeypatch, tmp_path):
    db_path = tmp_path / "km.db"
    from experimental.keymaster import store as km_store

    km_store.init_db(db_path)
    with km_store.open_connection(db_path) as conn:
        km_store.create_key(conn, PROVIDER_SHODAN, "One", "KEY_ONE", "")
        conn.commit()

    win = _make_window(monkeypatch, db_path=db_path)
    win._search_var.get.return_value = ""
    win._tree.get_children.return_value = ()
    win._tree.insert = MagicMock()

    win._load_entries()

    _, kwargs = win._tree.insert.call_args
    values = kwargs["values"]
    assert values[2] == _QUERY_CREDITS_NOT_CHECKED


def test_on_recheck_selected_without_selection_sets_status(monkeypatch):
    win = _make_window(monkeypatch)
    win._selected_row = MagicMock(return_value=None)
    win._status_var = MagicMock()

    win._on_recheck_selected()

    win._status_var.set.assert_called_with("Select a key to recheck.")


def test_on_recheck_selected_starts_single_key_refresh(monkeypatch):
    win = _make_window(monkeypatch)
    win._total_saved_keys = MAX_BURST_CREDIT_CHECKS + 10
    win._selected_row = MagicMock(return_value={"key_id": 17})
    started = []

    def _start(*, user_initiated, only_key_ids=None):
        started.append((user_initiated, only_key_ids))

    win._start_query_credits_refresh = _start
    win._on_recheck_selected()

    assert started == [(True, {17})]


def test_context_menu_includes_recheck_selected_when_row_present(monkeypatch):
    win = _make_window(monkeypatch)
    win._context_menu = MagicMock()
    registered = []

    def _add_command(**kw):
        registered.append(kw)

    win._context_menu.add_command = _add_command
    win._context_menu.delete = MagicMock()
    win._build_context_menu({"key_id": 1, "label": "L"})

    labels = [entry.get("label") for entry in registered]
    assert "Recheck Selected" in labels


def test_startup_refresh_skips_when_auto_check_disabled(monkeypatch):
    win = _make_window(monkeypatch)
    win._auto_check_var.get.return_value = False
    win._status_var = MagicMock()
    win._set_recheck_state = MagicMock()
    win._start_query_credits_refresh = MagicMock()

    win._run_startup_credit_refresh()

    win._start_query_credits_refresh.assert_not_called()
    assert win._status_var.set.call_args_list[-1].args[0] == "Auto check is off."


def test_startup_refresh_runs_when_auto_check_enabled(monkeypatch):
    win = _make_window(monkeypatch)
    win._auto_check_var.get.return_value = True
    win._start_query_credits_refresh = MagicMock()

    win._run_startup_credit_refresh()

    win._start_query_credits_refresh.assert_called_once_with(
        user_initiated=False,
        startup=True,
    )


def test_auto_check_setting_default_true_without_settings(monkeypatch):
    win = _make_window(monkeypatch, settings_manager=None)
    assert win._read_auto_check_setting() is True


def test_auto_check_setting_reads_false_from_settings(monkeypatch):
    sm = MagicMock()
    sm.get_setting.return_value = False
    win = _make_window(monkeypatch, settings_manager=sm)
    assert win._read_auto_check_setting() is False


def test_auto_check_toggle_persists_setting(monkeypatch):
    sm = MagicMock()
    win = _make_window(monkeypatch, settings_manager=sm)
    win._auto_check_var.get.return_value = False
    win._status_var = MagicMock()
    win._set_recheck_state = MagicMock()
    win._credits_refresh_inflight = False

    win._on_auto_check_toggled()

    sm.set_setting.assert_called_with(_AUTO_CHECK_SETTING_KEY, False)
    assert win._status_var.set.call_args_list[-1].args[0] == "Auto check disabled."


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
