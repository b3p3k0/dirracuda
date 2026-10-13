"""Desktop recovery actions against disposable real stores, with no network."""

import sqlite3
from unittest.mock import MagicMock

import pytest

from experimental.keymaster import store as km
from gui.components import keymaster_security as security
from gui.tests.test_keymaster_window import _make_window, _seed_secure_key


@pytest.fixture
def win(tmp_path, monkeypatch):
    db = tmp_path / "keymaster.db"
    km.init_db(db)
    window = _make_window(monkeypatch, db_path=db)
    window._security_action_btn = MagicMock()
    window._reset_btn = MagicMock()
    window._security_status_var = MagicMock()
    window._ask_passphrase = MagicMock(side_effect=AssertionError("Unexpected password prompt"))
    window._load_entries = MagicMock()
    monkeypatch.setattr(security.messagebox, "showerror", MagicMock())
    return window


@pytest.mark.parametrize("configured", [False, True])
def test_open_reads_state_without_prompt(win, configured):
    if configured:
        _seed_secure_key(win.db_path)
    win._initialize_security_session()
    assert win._security_state == ("Locked" if configured else "Setup needed")
    win._ask_passphrase.assert_not_called()


@pytest.mark.parametrize("state", ["empty", "locked", "unlocked", "off", "damaged"])
def test_delete_disable_never_needs_password_and_invalidates_old_results(win, monkeypatch, state):
    if state in {"locked", "unlocked", "damaged"}:
        _seed_secure_key(win.db_path)
    with km.open_connection(win.db_path) as conn:
        if state == "unlocked":
            win._session_keys = km.unlock_session_keys(conn, "window-test-passphrase")
        if state == "off":
            km.set_secure_mode(conn, False)
            km.create_key(conn, "LLM_SERVER", "Fixture", "synthetic token", "")
        if state == "damaged":
            conn.execute("DELETE FROM keymaster_meta WHERE meta_key = 'kdf_salt_hex'")
    win._query_credit_by_key_id = {1: "Checking..."}
    win._row_by_iid = {"1": {"api_key": "synthetic token"}}
    win._credits_refresh_inflight = True
    previous_generation = win._credits_refresh_generation
    monkeypatch.setattr(security.messagebox, "askyesno", MagicMock(return_value=True))
    win._on_forgot_passphrase_reset()
    win._ask_passphrase.assert_not_called()
    assert win._security_state == "Off"
    assert win._session_keys is None
    assert not win._row_by_iid
    assert not win._query_credit_by_key_id
    assert not win._credits_refresh_inflight
    win._finish_query_credit_refresh(previous_generation, {1: "99"})
    assert not win._query_credit_by_key_id
    with km.open_connection(win.db_path) as conn:
        assert not km.secure_mode_enabled(conn)
        assert not km.passphrase_is_configured(conn)
        assert conn.execute("SELECT COUNT(*) FROM keymaster_keys").fetchone()[0] == 0


def test_reset_cancel_changes_nothing(win, monkeypatch):
    _seed_secure_key(win.db_path)
    win._initialize_security_session()
    win._query_credit_by_key_id = {1: "12"}
    monkeypatch.setattr(security.messagebox, "askyesno", lambda *a, **kw: False)
    win._on_forgot_passphrase_reset()
    assert win._security_state == "Locked"
    assert win._query_credit_by_key_id == {1: "12"}
    win._load_entries.assert_not_called()
    with km.open_connection(win.db_path) as conn:
        assert km.encrypted_row_count(conn) == 1


def test_cancel_setup_then_disable_is_not_a_dead_end(win, monkeypatch):
    win._ask_passphrase = MagicMock(return_value=None)
    win._on_security_action()
    assert win._security_state == "Setup needed"
    win._ask_passphrase.assert_called_once_with(setup=True)
    monkeypatch.setattr(security.messagebox, "askyesno", lambda *a, **kw: True)
    win._on_forgot_passphrase_reset()
    assert win._security_state == "Off"
    assert win._ask_passphrase.call_count == 1


def test_enable_from_off_then_reopen_and_unlock_preserves_exact_input(win):
    with km.open_connection(win.db_path) as conn:
        km.set_secure_mode(conn, False)
        km.create_key(conn, "SHODAN", "Fixture", "synthetic key", "")
    win._ask_passphrase = MagicMock(return_value="  typed café Ω  ")
    win._on_security_action()
    assert win._security_state == "Unlocked"
    win._initialize_security_session()
    assert win._security_state == "Locked"
    win._on_security_action()
    assert win._security_state == "Unlocked"
    with km.open_connection(win.db_path) as conn:
        assert km.list_keys(conn, "SHODAN", session_keys=win._session_keys)[0]["api_key"] == "synthetic key"


def test_setup_failure_does_not_publish_keys_or_change_mode(win, monkeypatch):
    with km.open_connection(win.db_path) as conn:
        km.set_secure_mode(conn, False)
    win._ask_passphrase = MagicMock(return_value="synthetic phrase")
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive diagnostic sentinel")
    monkeypatch.setattr(km, "migrate_legacy_plaintext_rows", fail)
    assert win._setup_initial_passphrase() is False
    assert win._session_keys is None
    assert win._security_state == "Off"
    assert "sensitive diagnostic sentinel" not in str(security.messagebox.showerror.call_args)
    with km.open_connection(win.db_path) as conn:
        assert not km.passphrase_is_configured(conn)
        assert not km.secure_mode_enabled(conn)


def test_reset_failure_preserves_data_and_caches(win, monkeypatch):
    _seed_secure_key(win.db_path)
    win._initialize_security_session()
    win._query_credit_by_key_id = {1: "12"}
    monkeypatch.setattr(security.messagebox, "askyesno", lambda *a, **kw: True)
    def fail(conn):
        raise sqlite3.OperationalError("sensitive diagnostic sentinel")
    monkeypatch.setattr(km, "clear_passphrase_metadata", fail)
    win._on_forgot_passphrase_reset()
    assert win._security_state == "Locked"
    assert win._query_credit_by_key_id == {1: "12"}
    assert "sensitive diagnostic sentinel" not in str(security.messagebox.showerror.call_args)
    with km.open_connection(win.db_path) as conn:
        assert km.encrypted_row_count(conn) == 1


def test_error_categories_are_distinct_and_do_not_echo_details():
    classes = [km.InvalidPassphraseError, km.PassphraseRequiredError,
               km.KeymasterMetadataError, sqlite3.OperationalError]
    messages = [security.security_error_message(cls("sensitive sentinel")) for cls in classes]
    assert len(set(messages)) == 4
    assert all("sensitive sentinel" not in message for message in messages)


def test_old_window_session_cannot_apply_cached_key_after_external_reset(win):
    key_id = _seed_secure_key(win.db_path)
    with km.open_connection(win.db_path) as conn:
        win._session_keys = km.unlock_session_keys(conn, "window-test-passphrase")
        row = km.get_key(conn, key_id, session_keys=win._session_keys)
    win._load_security_policy()
    win._selected_row = MagicMock(return_value=row)
    win._resolve_active_config_path = MagicMock()
    with km.open_connection(win.db_path) as conn:
        km.destructive_reset(conn, secure_mode=False)
        km.enable_secure_storage(conn, "synthetic new phrase")
    win._apply_selected_key()
    assert win._session_keys is None
    assert win._security_state == "Locked"
    win._resolve_active_config_path.assert_not_called()


def test_old_credit_worker_stops_before_another_request_after_reset(win):
    win._credits_refresh_generation = 4
    win._fetch_query_credit_display = MagicMock()
    win._run_query_credit_refresh_worker(3, [{"key_id": 1, "api_key": "synthetic token"}])
    win._fetch_query_credit_display.assert_not_called()
