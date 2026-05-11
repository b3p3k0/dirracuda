"""Tests for experimental/keymaster/store.py."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.keymaster import store as km_store
from experimental.keymaster.models import (
    DuplicateKeyError,
    InvalidPassphraseError,
    KeymasterLockedError,
    PassphraseRequiredError,
    PROVIDER_SHODAN,
)

_TEST_PASSPHRASE = "unit-test-passphrase"


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "keymaster_test.db"
    km_store.init_db(db_path)
    yield db_path


def _configure_and_unlock(conn: sqlite3.Connection, passphrase: str = _TEST_PASSPHRASE) -> dict[str, bytes]:
    km_store.configure_passphrase(conn, passphrase)
    return km_store.unlock_session_keys(conn, passphrase)


def _unlock(conn: sqlite3.Connection, passphrase: str = _TEST_PASSPHRASE) -> dict[str, bytes]:
    return km_store.unlock_session_keys(conn, passphrase)


def test_init_db_creates_schema_and_secure_defaults(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        assert conn is not None
        assert km_store.secure_mode_enabled(conn) is True
        assert km_store.passphrase_is_configured(conn) is False


def test_create_key_requires_passphrase_in_secure_mode(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(PassphraseRequiredError):
            km_store.create_key(conn, PROVIDER_SHODAN, "My Key", "abc123", "")


def test_unlock_rejects_invalid_passphrase(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        _configure_and_unlock(conn, passphrase="first-pass")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(InvalidPassphraseError):
            km_store.unlock_session_keys(conn, "wrong-pass")


def test_create_key_encrypts_at_rest_by_default(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        key_id = km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Primary",
            "ABC123SECRET",
            "notes",
            session_keys=session_keys,
        )
        conn.commit()

        raw_row = conn.execute(
            """
            SELECT api_key, api_key_normalized, key_ciphertext, key_fingerprint, is_encrypted
              FROM keymaster_keys
             WHERE key_id = ?
            """,
            (key_id,),
        ).fetchone()

    assert raw_row is not None
    assert str(raw_row["api_key"] or "") == ""
    assert str(raw_row["api_key_normalized"] or "") != "ABC123SECRET"
    assert str(raw_row["key_ciphertext"] or "") != ""
    assert str(raw_row["key_fingerprint"] or "") != ""
    assert int(raw_row["is_encrypted"]) == 1


def test_locked_vs_unlocked_reads_for_encrypted_rows(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        key_id = km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Primary",
            "LOCKME",
            "",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        locked_row = km_store.get_key(conn, key_id, session_keys=None)
        unlocked_keys = _unlock(conn)
        unlocked_row = km_store.get_key(conn, key_id, session_keys=unlocked_keys)

    assert locked_row is not None
    assert locked_row["is_encrypted"] is True
    assert locked_row["is_decrypted"] is False
    assert locked_row["api_key"] == ""

    assert unlocked_row is not None
    assert unlocked_row["is_encrypted"] is True
    assert unlocked_row["is_decrypted"] is True
    assert unlocked_row["api_key"] == "LOCKME"


def test_duplicate_key_blocked_in_secure_mode(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Key A",
            "dup-secret",
            "",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        session_keys = _unlock(conn)
        with pytest.raises(DuplicateKeyError):
            km_store.create_key(
                conn,
                PROVIDER_SHODAN,
                "Key B",
                "dup-secret",
                "",
                session_keys=session_keys,
            )


def test_update_key_changes_fields_in_secure_mode(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        key_id = km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Old Label",
            "KEY001",
            "old notes",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        session_keys = _unlock(conn)
        km_store.update_key(
            conn,
            key_id,
            "New Label",
            "KEY001",
            "new notes",
            session_keys=session_keys,
        )
        conn.commit()
        row = km_store.get_key(conn, key_id, session_keys=session_keys)
        raw = conn.execute(
            "SELECT api_key, key_ciphertext, is_encrypted FROM keymaster_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()

    assert row is not None
    assert row["label"] == "New Label"
    assert row["notes"] == "new notes"
    assert row["api_key"] == "KEY001"
    assert raw is not None
    assert str(raw["api_key"] or "") == ""
    assert str(raw["key_ciphertext"] or "") != ""
    assert int(raw["is_encrypted"]) == 1


def test_switch_secure_mode_to_plaintext_converts_rows(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        key_id = km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Primary",
            "TO-PLAINTEXT",
            "",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        session_keys = _unlock(conn)
        converted = km_store.switch_secure_mode(conn, False, session_keys=session_keys)
        conn.commit()
        row = conn.execute(
            "SELECT api_key, is_encrypted FROM keymaster_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()
        secure_enabled = km_store.secure_mode_enabled(conn)

    assert converted == 1
    assert secure_enabled is False
    assert row is not None
    assert str(row["api_key"]) == "TO-PLAINTEXT"
    assert int(row["is_encrypted"]) == 0


def test_migrate_legacy_plaintext_rows_encrypts_existing_data(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        km_store.set_secure_mode(conn, False)
        id_one = km_store.create_key(conn, PROVIDER_SHODAN, "One", "LEGACY1", "")
        id_two = km_store.create_key(conn, PROVIDER_SHODAN, "Two", "LEGACY2", "")
        conn.commit()
        assert id_one > 0 and id_two > 0

    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        km_store.set_secure_mode(conn, True)
        converted = km_store.migrate_legacy_plaintext_rows(conn, session_keys=session_keys)
        conn.commit()
        legacy_count = km_store.legacy_plaintext_row_count(conn)
        encrypted_count = km_store.encrypted_row_count(conn)
        rows = conn.execute(
            "SELECT api_key, key_ciphertext, is_encrypted FROM keymaster_keys ORDER BY key_id",
        ).fetchall()

    assert converted == 2
    assert legacy_count == 0
    assert encrypted_count == 2
    assert len(rows) == 2
    assert all(str(row["api_key"] or "") == "" for row in rows)
    assert all(str(row["key_ciphertext"] or "") != "" for row in rows)
    assert all(int(row["is_encrypted"]) == 1 for row in rows)


def test_destructive_reset_clears_rows_and_passphrase_metadata(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Primary",
            "RESET-ME",
            "",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        km_store.destructive_reset(conn)
        conn.commit()
        row_count = conn.execute("SELECT COUNT(*) AS n FROM keymaster_keys").fetchone()["n"]
        configured = km_store.passphrase_is_configured(conn)
        secure_enabled = km_store.secure_mode_enabled(conn)

    assert int(row_count) == 0
    assert configured is False
    assert secure_enabled is True


def test_list_keys_search_filters_secure_mode(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Primary Paid",
            "ONE-KEY",
            "baseline",
            session_keys=session_keys,
        )
        km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Backup Trial",
            "TWO-KEY",
            "low allotment",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        unlocked = _unlock(conn)
        all_rows = km_store.list_keys(conn, PROVIDER_SHODAN, session_keys=unlocked)
        filtered = km_store.list_keys(
            conn,
            PROVIDER_SHODAN,
            search_text="primary",
            session_keys=unlocked,
        )
    assert len(all_rows) == 2
    assert len(filtered) == 1
    assert filtered[0]["label"] == "Primary Paid"
    assert filtered[0]["api_key"] == "ONE-KEY"


def test_touch_last_used_sets_timestamp_secure_mode(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        session_keys = _configure_and_unlock(conn)
        key_id = km_store.create_key(
            conn,
            PROVIDER_SHODAN,
            "Label",
            "LAST-USED",
            "",
            session_keys=session_keys,
        )
        conn.commit()

    with km_store.open_connection(tmp_db) as conn:
        unlocked = _unlock(conn)
        row_before = km_store.get_key(conn, key_id, session_keys=unlocked)
    assert row_before["last_used_at"] is None

    with km_store.open_connection(tmp_db) as conn:
        km_store.touch_last_used(conn, key_id)
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        unlocked = _unlock(conn)
        row_after = km_store.get_key(conn, key_id, session_keys=unlocked)
    assert row_after["last_used_at"] is not None


def test_secure_mode_requires_session_keys_for_create_update(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        _configure_and_unlock(conn)
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(KeymasterLockedError):
            km_store.create_key(conn, PROVIDER_SHODAN, "Label", "A", "", session_keys=None)
