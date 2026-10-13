"""Security lifecycle regressions; every credential and store is synthetic."""

import sqlite3

import pytest

from experimental.keymaster import store as km
from experimental.keymaster.models import KeymasterLockedError


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "keymaster.db"
    km.init_db(path)
    return path


def snapshot(conn):
    return (
        [tuple(row) for row in conn.execute("SELECT * FROM keymaster_keys ORDER BY key_id")],
        [tuple(row) for row in conn.execute("SELECT * FROM keymaster_meta ORDER BY meta_key")],
    )


def seed(conn, phrase="synthetic old passphrase"):
    km.configure_passphrase(conn, phrase)
    keys = km.unlock_session_keys(conn, phrase)
    for provider in km.PROVIDERS:
        km.create_key(conn, provider, "Fixture", "synthetic key", "", session_keys=keys)
    conn.commit()
    return keys


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("target", [False, True])
def test_reset_explicit_mode_persists_without_password(db, populated, target):
    with km.open_connection(db) as conn:
        if populated:
            seed(conn)
        km.destructive_reset(conn, secure_mode=target)
    km.init_db(db)
    with km.open_connection(db) as conn:
        assert km.secure_mode_enabled(conn) is target
        assert not km.passphrase_is_configured(conn)
        assert conn.execute("SELECT COUNT(*) FROM keymaster_keys").fetchone()[0] == 0


def test_reset_failure_rolls_back_even_when_caller_catches_error(db, monkeypatch):
    with km.open_connection(db) as conn:
        seed(conn)
        before = snapshot(conn)
        original = km.clear_passphrase_metadata

        def fail_after_clear(conn):
            original(conn)
            raise sqlite3.OperationalError("injected write failure")

        monkeypatch.setattr(km, "clear_passphrase_metadata", fail_after_clear)
        with pytest.raises(sqlite3.OperationalError):
            km.destructive_reset(conn, secure_mode=False)
        conn.commit()
        assert snapshot(conn) == before


def test_reset_does_not_commit_callers_work(db):
    with km.open_connection(db) as conn:
        seed(conn)
        before = snapshot(conn)
        conn.execute("UPDATE keymaster_keys SET notes = 'uncommitted'")
        km.destructive_reset(conn, secure_mode=False)
        conn.rollback()
        assert snapshot(conn) == before


@pytest.mark.parametrize("phrase", ["synthetic phrase", "  synthetic phrase  ", "café Ω synthetic"])
def test_setup_save_reopen_exact_input(db, phrase):
    with km.open_connection(db) as conn:
        km.set_secure_mode(conn, False)
        key_id = km.create_key(conn, "SHODAN", "Fixture", "synthetic key", "")
        conn.commit()
        keys, count = km.enable_secure_storage(conn, phrase)
        assert count == 1
    km.init_db(db)
    with km.open_connection(db) as conn:
        reopened = km.unlock_session_keys(conn, phrase)
        assert reopened == keys
        assert km.get_key(conn, key_id, session_keys=reopened)["api_key"] == "synthetic key"
        assert km.secure_mode_enabled(conn)
        before = snapshot(conn)
        with pytest.raises(km.InvalidPassphraseError):
            km.unlock_session_keys(conn, phrase + "wrong")
        assert snapshot(conn) == before


def test_enable_rolls_back_metadata_mode_and_partially_converted_rows(db, monkeypatch):
    with km.open_connection(db) as conn:
        km.set_secure_mode(conn, False)
        for i in range(2):
            km.create_key(conn, "SHODAN", "Fixture", f"synthetic key {i}", "")
        conn.commit()
        before = snapshot(conn)
        original = km._encrypt_api_key
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("injected conversion failure")
            return original(*args)

        monkeypatch.setattr(km, "_encrypt_api_key", fail_second)
        with pytest.raises(sqlite3.OperationalError):
            km.enable_secure_storage(conn, "synthetic new phrase")
        conn.commit()
        assert snapshot(conn) == before


@pytest.mark.parametrize("partial", [False, True])
def test_setup_refuses_existing_or_partial_metadata(db, partial):
    with km.open_connection(db) as conn:
        seed(conn)
        if partial:
            conn.execute("DELETE FROM keymaster_meta WHERE meta_key = 'kdf_salt_hex'")
            conn.commit()
        before = snapshot(conn)
        with pytest.raises(km.PassphraseAlreadyConfiguredError):
            km.configure_passphrase(conn, "replacement phrase")
        conn.commit()
        assert snapshot(conn) == before


@pytest.mark.parametrize("field,value", [
    ("kdf_salt_hex", "broken"),
    ("kdf_iterations", "not a number"),
    ("kdf_iterations", "1"),
    ("passphrase_verifier_hex", "broken"),
    ("kdf_algorithm", "unsupported"),
    ("kdf_salt_hex", ""),
])
def test_malformed_metadata_is_not_reported_as_wrong_password(db, field, value):
    with km.open_connection(db) as conn:
        seed(conn)
        conn.execute("UPDATE keymaster_meta SET meta_value = ? WHERE meta_key = ?", (value, field))
        conn.commit()
        with pytest.raises(km.KeymasterMetadataError):
            km.unlock_session_keys(conn, "synthetic old passphrase")
        km.destructive_reset(conn, secure_mode=False)
        assert not km.passphrase_is_configured(conn)


def test_encrypted_rows_without_metadata_cannot_be_reconfigured(db):
    with km.open_connection(db) as conn:
        seed(conn)
        km.clear_passphrase_metadata(conn)
        conn.commit()
        before = snapshot(conn)
        with pytest.raises(km.KeymasterMetadataError):
            km.passphrase_is_configured(conn)
        with pytest.raises(km.PassphraseAlreadyConfiguredError):
            km.configure_passphrase(conn, "replacement phrase")
        assert snapshot(conn) == before


def test_excessive_kdf_work_factor_is_rejected_before_derivation(db, monkeypatch):
    with km.open_connection(db) as conn:
        seed(conn)
        conn.execute(
            "UPDATE keymaster_meta SET meta_value = ? WHERE meta_key = 'kdf_iterations'",
            (str(km._MAX_KDF_ITERATIONS + 1),),
        )
        def unexpected_derivation(*args):
            pytest.fail("Damaged KDF metadata reached password derivation")
        monkeypatch.setattr(km, "_pbkdf2_root_key", unexpected_derivation)
        with pytest.raises(km.KeymasterMetadataError):
            km.unlock_session_keys(conn, "synthetic old passphrase")


@pytest.mark.parametrize("operation", ["create", "update", "read", "list", "encrypt", "decrypt", "switch"])
def test_old_session_rejected_after_reset_and_new_setup(db, operation):
    with km.open_connection(db) as conn:
        old = seed(conn)
        km.destructive_reset(conn, secure_mode=False)
        fresh, _ = km.enable_secure_storage(conn, "synthetic new phrase")
        key_id = km.create_key(conn, "SHODAN", "New", "synthetic new key", "", session_keys=fresh)
        conn.commit()
        before = snapshot(conn)
        actions = {
            "create": lambda: km.create_key(conn, "SHODAN", "Old", "unreadable", "", session_keys=old),
            "update": lambda: km.update_key(conn, key_id, "Old", "unreadable", "", session_keys=old),
            "read": lambda: km.get_key(conn, key_id, session_keys=old),
            "list": lambda: km.list_keys(conn, "SHODAN", session_keys=old),
            "encrypt": lambda: km.migrate_legacy_plaintext_rows(conn, session_keys=old),
            "decrypt": lambda: km.migrate_rows_to_plaintext(conn, session_keys=old),
            "switch": lambda: km.switch_secure_mode(conn, False, session_keys=old),
        }
        with pytest.raises(KeymasterLockedError):
            actions[operation]()
        conn.commit()
        assert snapshot(conn) == before


def test_session_cannot_mix_current_root_with_old_subkeys(db):
    with km.open_connection(db) as conn:
        old = seed(conn)
        km.destructive_reset(conn, secure_mode=False)
        fresh, _ = km.enable_secure_storage(conn, "synthetic new phrase")
        mixed = dict(old, root_key=fresh["root_key"])
        with pytest.raises(KeymasterLockedError):
            km.create_key(conn, "SHODAN", "Bad", "unreadable", "", session_keys=mixed)
