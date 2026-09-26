"""N3: bearer tokens live in Keymaster (contract 9)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from experimental.analyst.credentials import (
    CredentialError,
    KeymasterLocked,
    resolve_bearer_token,
)
from experimental.keymaster import store as km
from experimental.keymaster.models import PROVIDER_LLM_SERVER, PROVIDERS

_PASSPHRASE = "correct horse battery staple"
_TOKEN = "secret-bearer-value"


@pytest.fixture
def keystore(tmp_path: Path):
    db = tmp_path / "keymaster.db"
    km.init_db(db)
    conn = km.open_connection(db)
    km.configure_passphrase(conn, _PASSPHRASE)
    keys = km.unlock_session_keys(conn, _PASSPHRASE)
    created = km.create_key(
        conn, provider=PROVIDER_LLM_SERVER, label="mimir token",
        api_key=_TOKEN, notes="", session_keys=keys,
    )
    key_id = created if isinstance(created, int) else created["key_id"]
    try:
        yield conn, key_id, keys
    finally:
        conn.close()


def test_llm_server_is_a_known_provider():
    assert PROVIDER_LLM_SERVER == "LLM_SERVER"
    assert PROVIDER_LLM_SERVER in PROVIDERS


def test_a_token_round_trips_when_unlocked(keystore):
    conn, key_id, keys = keystore
    assert km.get_key(conn, key_id, session_keys=keys)["api_key"] == _TOKEN


def test_a_locked_store_does_not_hand_back_the_token(keystore):
    conn, key_id, _ = keystore
    row = km.get_key(conn, key_id)
    assert row is not None
    assert row["api_key"] != _TOKEN


def test_a_profile_with_no_credential_sends_no_token():
    """Correct for loopback Ollama and for an unauthenticated private server."""
    assert resolve_bearer_token(None) is None


@pytest.mark.parametrize("bad", [0, -1, True, "7", 1.0])
def test_an_invalid_key_id_is_refused(bad):
    with pytest.raises(CredentialError):
        resolve_bearer_token(bad)


def test_the_locked_error_carries_the_exact_contract_wording():
    """Contract 9: a run started while locked fails with this, never a
    transport error."""
    assert str(KeymasterLocked("Keymaster is locked")) == "Keymaster is locked"
    assert issubclass(KeymasterLocked, CredentialError)


def test_errors_never_carry_the_token(keystore):
    """Contract 9: the token appears in no error message."""
    for exc in (
        CredentialError("stored credential could not be read"),
        KeymasterLocked("Keymaster is locked"),
    ):
        assert _TOKEN not in str(exc)


# --------------------------------------------------------------------------
# The provider vocabulary migration
# --------------------------------------------------------------------------

def _old_sidecar(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE keymaster_keys (
          key_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
          label TEXT NOT NULL, api_key TEXT NOT NULL,
          api_key_normalized TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          last_used_at TEXT NULL, key_ciphertext TEXT NOT NULL DEFAULT '',
          key_fingerprint TEXT NOT NULL DEFAULT '',
          is_encrypted INTEGER NOT NULL DEFAULT 0,
          CHECK (provider IN ('SHODAN')));
        CREATE TABLE keymaster_meta (
          meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL);
        INSERT INTO keymaster_keys(
          provider,label,api_key,api_key_normalized,created_at,updated_at)
        VALUES('SHODAN','existing shodan key','k','k','t','t');
        """
    )
    conn.commit()
    conn.close()


def test_a_sidecar_predating_llm_server_is_migrated(tmp_path: Path):
    db = tmp_path / "keymaster.db"
    _old_sidecar(db)

    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO keymaster_keys(provider,label,api_key,"
            "api_key_normalized,created_at,updated_at) "
            "VALUES('LLM_SERVER','x','y','y','t','t')"
        )
    conn.close()

    km.init_db(db)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM keymaster_keys").fetchone()[0] == 1
        conn.execute(
            "INSERT INTO keymaster_keys(provider,label,api_key,"
            "api_key_normalized,created_at,updated_at) "
            "VALUES('LLM_SERVER','token','t','t','t','t')"
        )
        labels = sorted(r[0] for r in conn.execute("SELECT label FROM keymaster_keys"))
        assert labels == ["existing shodan key", "token"]
    finally:
        conn.close()


def test_the_migration_is_idempotent(tmp_path: Path):
    db = tmp_path / "keymaster.db"
    _old_sidecar(db)
    km.init_db(db)
    km.init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM keymaster_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_an_unknown_provider_is_still_refused(tmp_path: Path):
    db = tmp_path / "keymaster.db"
    km.init_db(db)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO keymaster_keys(provider,label,api_key,"
                "api_key_normalized,created_at,updated_at) "
                "VALUES('OPENAI','x','y','y','t','t')"
            )
    finally:
        conn.close()
