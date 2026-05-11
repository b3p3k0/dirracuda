"""
Sidecar SQLite store for the Keymaster module.

DB path: ~/.dirracuda/data/experimental/keymaster.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() owns setup and commit.
  - create_key/update_key/delete_key/touch_last_used/list_keys accept
    a caller-supplied connection and do NOT commit.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from experimental.keymaster.models import (
    PROVIDERS,
    KeymasterKey,
    DuplicateKeyError,
    InvalidPassphraseError,
    KeymasterLockedError,
    PassphraseRequiredError,
)
from shared.path_service import get_paths, get_legacy_paths, select_existing_path


_DDL_KEYS = """
CREATE TABLE IF NOT EXISTS keymaster_keys (
    key_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT NOT NULL,
    label               TEXT NOT NULL,
    api_key             TEXT NOT NULL,
    api_key_normalized  TEXT NOT NULL,
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_used_at        TEXT NULL,
    key_ciphertext      TEXT NOT NULL DEFAULT '',
    key_fingerprint     TEXT NOT NULL DEFAULT '',
    is_encrypted        INTEGER NOT NULL DEFAULT 0,
    CHECK (provider IN ('SHODAN'))
)
"""

_DDL_UNIQUE_KEY = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_keymaster_provider_key_norm
    ON keymaster_keys(provider, api_key_normalized)
"""

_DDL_META = """
CREATE TABLE IF NOT EXISTS keymaster_meta (
    meta_key            TEXT PRIMARY KEY,
    meta_value          TEXT NOT NULL
)
"""

_REQUIRED_COLUMNS = {
    "key_id",
    "provider",
    "label",
    "api_key",
    "api_key_normalized",
    "notes",
    "created_at",
    "updated_at",
    "last_used_at",
    "key_ciphertext",
    "key_fingerprint",
    "is_encrypted",
}

_REQUIRED_META_COLUMNS = {"meta_key", "meta_value"}

_META_SECURE_MODE = "secure_mode_enabled"
_META_KDF_ALGO = "kdf_algorithm"
_META_KDF_ITERATIONS = "kdf_iterations"
_META_KDF_SALT_HEX = "kdf_salt_hex"
_META_PASS_VERIFIER = "passphrase_verifier_hex"

_KDF_ALGO_VALUE = "PBKDF2_HMAC_SHA256"
_DEFAULT_KDF_ITERATIONS = 600_000
_KDF_SALT_BYTES = 32
_AES_NONCE_BYTES = 12
_MAX_PASSPHRASE_BYTES = 1024


def _utcnow() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().upper()
    if value not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")
    return value


def normalize_api_key(api_key: str) -> str:
    """Strip whitespace only — dedup is case-sensitive per spec."""
    value = str(api_key or "").strip()
    if not value:
        raise ValueError("api_key is required")
    return value


def _normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_nonempty(value: str, field: str) -> str:
    v = str(value or "").strip()
    if not v:
        raise ValueError(f"{field} is required")
    return v


def _normalize_passphrase(passphrase: str) -> bytes:
    if not isinstance(passphrase, str):
        raise ValueError("passphrase must be a string")
    raw = passphrase.encode("utf-8")
    if not raw:
        raise ValueError("passphrase must not be empty")
    if len(raw) > _MAX_PASSPHRASE_BYTES:
        raise ValueError(f"passphrase exceeds {_MAX_PASSPHRASE_BYTES} bytes")
    return raw


def _pbkdf2_root_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    pw = _normalize_passphrase(passphrase)
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 100_000:
        raise ValueError("invalid PBKDF2 iteration count")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise ValueError("invalid PBKDF2 salt")
    return hashlib.pbkdf2_hmac("sha256", pw, bytes(salt), iterations, dklen=32)


def _derive_subkey(root_key: bytes, purpose: bytes) -> bytes:
    return hmac.new(root_key, purpose, hashlib.sha256).digest()


def _build_fingerprint(api_key: str, fingerprint_key: bytes) -> str:
    key_norm = normalize_api_key(api_key)
    return hmac.new(
        fingerprint_key,
        key_norm.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _encrypt_api_key(api_key: str, encryption_key: bytes) -> str:
    key_norm = normalize_api_key(api_key)
    nonce = os.urandom(_AES_NONCE_BYTES)
    ciphertext = AESGCM(encryption_key).encrypt(
        nonce,
        key_norm.encode("utf-8"),
        None,
    )
    payload = {
        "v": "v1",
        "n": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "c": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _decrypt_api_key(ciphertext_payload: str, encryption_key: bytes) -> str:
    try:
        payload = json.loads(str(ciphertext_payload or ""))
    except Exception as exc:
        raise ValueError("invalid encrypted payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid encrypted payload")
    if str(payload.get("v") or "") != "v1":
        raise ValueError("unsupported encrypted payload version")
    try:
        nonce = base64.urlsafe_b64decode(str(payload["n"]).encode("ascii"))
        ciphertext = base64.urlsafe_b64decode(str(payload["c"]).encode("ascii"))
    except Exception as exc:
        raise ValueError("invalid encrypted payload encoding") from exc
    try:
        plaintext = AESGCM(encryption_key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("encrypted payload verification failed") from exc
    value = plaintext.decode("utf-8")
    return normalize_api_key(value)


def _compute_passphrase_verifier(root_key: bytes) -> str:
    return hmac.new(root_key, b"keymaster-passphrase-verifier", hashlib.sha256).hexdigest()


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, decl: str) -> None:
    columns = _table_columns(conn, table_name)
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {decl}")


def _meta_get(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute(
        "SELECT meta_value FROM keymaster_meta WHERE meta_key = ?",
        (str(key),),
    ).fetchone()
    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        return str(row["meta_value"])
    return str(row[0])


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO keymaster_meta(meta_key, meta_value)
        VALUES(?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
        """,
        (str(key), str(value)),
    )


def _meta_delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM keymaster_meta WHERE meta_key = ?", (str(key),))


def _ensure_default_meta(conn: sqlite3.Connection) -> None:
    if _meta_get(conn, _META_SECURE_MODE, None) is None:
        _meta_set(conn, _META_SECURE_MODE, "1")


def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    if override is not None:
        return override
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    return select_existing_path(
        paths.keymaster_db_file,
        [
            legacy.flat_sidecar_keymaster_file,
            legacy.legacy_home_root / "keymaster.db",
        ],
    )


def init_db(path: Optional[Path] = None) -> None:
    """Create sidecar DB and schema."""
    resolved = get_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_DDL_KEYS)
        conn.execute(_DDL_UNIQUE_KEY)
        conn.execute(_DDL_META)

        _ensure_column(conn, "keymaster_keys", "key_ciphertext", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "keymaster_keys", "key_fingerprint", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "keymaster_keys", "is_encrypted", "INTEGER NOT NULL DEFAULT 0")

        _ensure_default_meta(conn)
        conn.commit()


def _check_schema(conn: sqlite3.Connection) -> None:
    """Validate sidecar schema for runtime safety."""
    present = _table_columns(conn, "keymaster_keys")
    missing = _REQUIRED_COLUMNS - present
    if missing:
        raise RuntimeError(f"keymaster sidecar schema: missing columns {missing}")

    meta_present = _table_columns(conn, "keymaster_meta")
    meta_missing = _REQUIRED_META_COLUMNS - meta_present
    if meta_missing:
        raise RuntimeError(f"keymaster meta schema: missing columns {meta_missing}")

    indexes = {
        row[1]: bool(row[2])
        for row in conn.execute("PRAGMA index_list('keymaster_keys')")
    }
    unique_key_ok = any(
        is_unique
        and {r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")}
        == {"provider", "api_key_normalized"}
        for name, is_unique in indexes.items()
    )
    if not unique_key_ok:
        raise RuntimeError(
            "keymaster sidecar schema: missing UNIQUE(provider, api_key_normalized)"
        )


def open_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open and validate a write connection to the sidecar DB."""
    resolved = get_db_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"keymaster sidecar DB not found at {resolved} — call init_db() first"
        )
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema(conn)
    _ensure_default_meta(conn)
    return conn


def secure_mode_enabled(conn: sqlite3.Connection) -> bool:
    """Return current secure-mode policy (default-on)."""
    return _coerce_bool(_meta_get(conn, _META_SECURE_MODE, "1"), default=True)


def set_secure_mode(conn: sqlite3.Connection, enabled: bool) -> None:
    _meta_set(conn, _META_SECURE_MODE, "1" if bool(enabled) else "0")


def passphrase_is_configured(conn: sqlite3.Connection) -> bool:
    return all(
        _meta_get(conn, key, None) not in (None, "")
        for key in (
            _META_KDF_ALGO,
            _META_KDF_ITERATIONS,
            _META_KDF_SALT_HEX,
            _META_PASS_VERIFIER,
        )
    )


def _session_keys_from_passphrase(conn: sqlite3.Connection, passphrase: str) -> dict[str, bytes]:
    if not passphrase_is_configured(conn):
        raise PassphraseRequiredError("Keymaster passphrase is not configured")

    algo = str(_meta_get(conn, _META_KDF_ALGO, "") or "")
    if algo != _KDF_ALGO_VALUE:
        raise RuntimeError(f"unsupported keymaster KDF algorithm: {algo}")

    iterations = _coerce_int(
        _meta_get(conn, _META_KDF_ITERATIONS, str(_DEFAULT_KDF_ITERATIONS)),
        default=_DEFAULT_KDF_ITERATIONS,
    )

    try:
        salt = bytes.fromhex(str(_meta_get(conn, _META_KDF_SALT_HEX, "") or ""))
    except Exception as exc:
        raise RuntimeError("invalid keymaster KDF salt metadata") from exc

    root_key = _pbkdf2_root_key(passphrase, salt, iterations)
    verifier = _compute_passphrase_verifier(root_key)
    expected_verifier = str(_meta_get(conn, _META_PASS_VERIFIER, "") or "")
    if not expected_verifier or not hmac.compare_digest(verifier, expected_verifier):
        raise InvalidPassphraseError("invalid passphrase")

    return {
        "root_key": root_key,
        "encryption_key": _derive_subkey(root_key, b"keymaster-encryption"),
        "fingerprint_key": _derive_subkey(root_key, b"keymaster-fingerprint"),
    }


def configure_passphrase(conn: sqlite3.Connection, passphrase: str) -> None:
    """Set or rotate the Keymaster passphrase metadata."""
    salt = os.urandom(_KDF_SALT_BYTES)
    root_key = _pbkdf2_root_key(passphrase, salt, _DEFAULT_KDF_ITERATIONS)
    verifier = _compute_passphrase_verifier(root_key)

    _meta_set(conn, _META_KDF_ALGO, _KDF_ALGO_VALUE)
    _meta_set(conn, _META_KDF_ITERATIONS, str(_DEFAULT_KDF_ITERATIONS))
    _meta_set(conn, _META_KDF_SALT_HEX, salt.hex())
    _meta_set(conn, _META_PASS_VERIFIER, verifier)


def unlock_session_keys(conn: sqlite3.Connection, passphrase: str) -> dict[str, bytes]:
    """Return per-session encryption/fingerprint keys after passphrase verification."""
    return _session_keys_from_passphrase(conn, passphrase)


def clear_passphrase_metadata(conn: sqlite3.Connection) -> None:
    for key in (_META_KDF_ALGO, _META_KDF_ITERATIONS, _META_KDF_SALT_HEX, _META_PASS_VERIFIER):
        _meta_delete(conn, key)


def encrypted_row_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM keymaster_keys WHERE is_encrypted = 1"
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return int(row["n"])
    return int(row[0])


def _require_secure_session(conn: sqlite3.Connection, session_keys: Optional[dict[str, bytes]]) -> dict[str, bytes]:
    if not secure_mode_enabled(conn):
        return {}
    if not passphrase_is_configured(conn):
        raise PassphraseRequiredError("Keymaster passphrase is required")
    if not session_keys:
        raise KeymasterLockedError("Keymaster is locked")
    enc = session_keys.get("encryption_key")
    fp = session_keys.get("fingerprint_key")
    if not isinstance(enc, (bytes, bytearray)) or len(enc) != 32:
        raise KeymasterLockedError("Keymaster session is invalid")
    if not isinstance(fp, (bytes, bytearray)) or len(fp) != 32:
        raise KeymasterLockedError("Keymaster session is invalid")
    return {
        "encryption_key": bytes(enc),
        "fingerprint_key": bytes(fp),
    }


def _session_encryption_key_or_none(session_keys: Optional[dict[str, bytes]]) -> Optional[bytes]:
    if not session_keys:
        return None
    enc = session_keys.get("encryption_key")
    if isinstance(enc, (bytes, bytearray)) and len(enc) == 32:
        return bytes(enc)
    return None


def _row_to_key_dict(row: sqlite3.Row, *, session_keys: Optional[dict[str, bytes]]) -> dict:
    is_encrypted = _coerce_bool(row["is_encrypted"], default=False)
    plaintext_key = str(row["api_key"] or "")
    is_decrypted = not is_encrypted

    if is_encrypted:
        encryption_key = _session_encryption_key_or_none(session_keys)
        if encryption_key is not None:
            plaintext_key = _decrypt_api_key(str(row["key_ciphertext"] or ""), encryption_key)
            is_decrypted = True
        else:
            plaintext_key = ""
            is_decrypted = False

    key = KeymasterKey(
        key_id=int(row["key_id"]),
        provider=str(row["provider"]),
        label=str(row["label"] or ""),
        api_key=plaintext_key,
        api_key_normalized=str(row["api_key_normalized"] or ""),
        notes=str(row["notes"] or ""),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        last_used_at=row["last_used_at"],
    )
    return {
        "key_id": key.key_id,
        "provider": key.provider,
        "label": key.label,
        "api_key": key.api_key,
        "api_key_normalized": key.api_key_normalized,
        "notes": key.notes,
        "created_at": key.created_at,
        "updated_at": key.updated_at,
        "last_used_at": key.last_used_at,
        "is_encrypted": bool(is_encrypted),
        "is_decrypted": bool(is_decrypted),
    }


def _require_secure_session_dummy(session_keys: Optional[dict[str, bytes]]) -> dict[str, bytes]:
    if not session_keys:
        raise KeymasterLockedError("Keymaster is locked")
    enc = session_keys.get("encryption_key")
    if not isinstance(enc, (bytes, bytearray)) or len(enc) != 32:
        raise KeymasterLockedError("Keymaster session is invalid")
    return {"encryption_key": bytes(enc)}


def key_exists(
    conn: sqlite3.Connection,
    provider: str,
    lookup_value: str,
    *,
    exclude_key_id: Optional[int] = None,
) -> bool:
    """Return True if provider already has the same lookup value."""
    provider_norm = _normalize_provider(provider)
    lookup_norm = _require_nonempty(lookup_value, "lookup_value")
    if exclude_key_id is None:
        row = conn.execute(
            "SELECT 1 FROM keymaster_keys WHERE provider = ? AND api_key_normalized = ? LIMIT 1",
            (provider_norm, lookup_norm),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM keymaster_keys WHERE provider = ? AND api_key_normalized = ? AND key_id <> ? LIMIT 1",
            (provider_norm, lookup_norm, exclude_key_id),
        ).fetchone()
    return row is not None


def create_key(
    conn: sqlite3.Connection,
    provider: str,
    label: str,
    api_key: str,
    notes: Optional[str],
    *,
    session_keys: Optional[dict[str, bytes]] = None,
) -> int:
    """Insert a key entry and return new key_id."""
    provider_norm = _normalize_provider(provider)
    label_norm = _require_nonempty(label, "label")
    key_norm = normalize_api_key(_require_nonempty(api_key, "api_key"))
    notes_norm = _normalize_text(notes)
    now = _utcnow()

    if secure_mode_enabled(conn):
        keys = _require_secure_session(conn, session_keys)
        fingerprint = _build_fingerprint(key_norm, keys["fingerprint_key"])
        if key_exists(conn, provider_norm, fingerprint):
            raise DuplicateKeyError(
                f"api_key already exists for provider {provider_norm}"
            )
        ciphertext_payload = _encrypt_api_key(key_norm, keys["encryption_key"])
        cur = conn.execute(
            """
            INSERT INTO keymaster_keys
                (provider, label, api_key, api_key_normalized, notes, created_at, updated_at, last_used_at,
                 key_ciphertext, key_fingerprint, is_encrypted)
            VALUES (?, ?, '', ?, ?, ?, ?, NULL, ?, ?, 1)
            """,
            (
                provider_norm,
                label_norm,
                fingerprint,
                notes_norm,
                now,
                now,
                ciphertext_payload,
                fingerprint,
            ),
        )
        return int(cur.lastrowid)

    if key_exists(conn, provider_norm, key_norm):
        raise DuplicateKeyError(
            f"api_key already exists for provider {provider_norm}"
        )

    cur = conn.execute(
        """
        INSERT INTO keymaster_keys
            (provider, label, api_key, api_key_normalized, notes, created_at, updated_at, last_used_at,
             key_ciphertext, key_fingerprint, is_encrypted)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '', '', 0)
        """,
        (provider_norm, label_norm, key_norm, key_norm, notes_norm, now, now),
    )
    return int(cur.lastrowid)


def get_key(
    conn: sqlite3.Connection,
    key_id: int,
    *,
    session_keys: Optional[dict[str, bytes]] = None,
) -> Optional[dict]:
    """Return one key by ID, or None."""
    row = conn.execute(
        """
        SELECT key_id, provider, label, api_key, api_key_normalized, notes,
               created_at, updated_at, last_used_at, key_ciphertext, key_fingerprint, is_encrypted
          FROM keymaster_keys
         WHERE key_id = ?
        """,
        (key_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_key_dict(row, session_keys=session_keys)


def update_key(
    conn: sqlite3.Connection,
    key_id: int,
    label: str,
    api_key: str,
    notes: Optional[str],
    *,
    session_keys: Optional[dict[str, bytes]] = None,
) -> None:
    """Update one key entry."""
    existing_row = conn.execute(
        "SELECT provider FROM keymaster_keys WHERE key_id = ?",
        (key_id,),
    ).fetchone()
    if existing_row is None:
        raise KeyError(f"keymaster key not found: {key_id}")

    label_norm = _require_nonempty(label, "label")
    key_norm = normalize_api_key(_require_nonempty(api_key, "api_key"))
    notes_norm = _normalize_text(notes)
    now = _utcnow()

    provider_norm = str(existing_row["provider"] if isinstance(existing_row, sqlite3.Row) else existing_row[0])

    if secure_mode_enabled(conn):
        keys = _require_secure_session(conn, session_keys)
        fingerprint = _build_fingerprint(key_norm, keys["fingerprint_key"])
        if key_exists(conn, provider_norm, fingerprint, exclude_key_id=key_id):
            raise DuplicateKeyError(
                f"api_key already exists for provider {provider_norm}"
            )
        ciphertext_payload = _encrypt_api_key(key_norm, keys["encryption_key"])
        conn.execute(
            """
            UPDATE keymaster_keys
               SET label = ?,
                   api_key = '',
                   api_key_normalized = ?,
                   notes = ?,
                   updated_at = ?,
                   key_ciphertext = ?,
                   key_fingerprint = ?,
                   is_encrypted = 1
             WHERE key_id = ?
            """,
            (
                label_norm,
                fingerprint,
                notes_norm,
                now,
                ciphertext_payload,
                fingerprint,
                key_id,
            ),
        )
        return

    if key_exists(conn, provider_norm, key_norm, exclude_key_id=key_id):
        raise DuplicateKeyError(
            f"api_key already exists for provider {provider_norm}"
        )

    conn.execute(
        """
        UPDATE keymaster_keys
           SET label = ?,
               api_key = ?,
               api_key_normalized = ?,
               notes = ?,
               updated_at = ?,
               key_ciphertext = '',
               key_fingerprint = '',
               is_encrypted = 0
         WHERE key_id = ?
        """,
        (label_norm, key_norm, key_norm, notes_norm, now, key_id),
    )


def delete_key(conn: sqlite3.Connection, key_id: int) -> bool:
    """Delete one key entry. Returns True if deleted."""
    cur = conn.execute("DELETE FROM keymaster_keys WHERE key_id = ?", (key_id,))
    return cur.rowcount > 0


def list_keys(
    conn: sqlite3.Connection,
    provider: str,
    search_text: str = "",
    *,
    session_keys: Optional[dict[str, bytes]] = None,
) -> list[dict]:
    """Return ordered key entries for a provider, optionally filtered by search."""
    provider_norm = _normalize_provider(provider)
    search_norm = str(search_text or "").strip().lower()

    if search_norm:
        like = f"%{search_norm}%"
        rows = conn.execute(
            """
            SELECT key_id, provider, label, api_key, api_key_normalized, notes,
                   created_at, updated_at, last_used_at, key_ciphertext, key_fingerprint, is_encrypted
              FROM keymaster_keys
             WHERE provider = ?
               AND (lower(label) LIKE ? OR lower(notes) LIKE ?)
             ORDER BY lower(label), key_id ASC
            """,
            (provider_norm, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT key_id, provider, label, api_key, api_key_normalized, notes,
                   created_at, updated_at, last_used_at, key_ciphertext, key_fingerprint, is_encrypted
              FROM keymaster_keys
             WHERE provider = ?
             ORDER BY lower(label), key_id ASC
            """,
            (provider_norm,),
        ).fetchall()

    return [_row_to_key_dict(row, session_keys=session_keys) for row in rows]


def touch_last_used(conn: sqlite3.Connection, key_id: int) -> None:
    """Update last_used_at for the given key."""
    conn.execute(
        "UPDATE keymaster_keys SET last_used_at = ? WHERE key_id = ?",
        (_utcnow(), key_id),
    )


def migrate_legacy_plaintext_rows(
    conn: sqlite3.Connection,
    *,
    session_keys: dict[str, bytes],
) -> int:
    """Encrypt any legacy plaintext rows in-place and return converted count."""
    keys = _require_secure_session(conn, session_keys)
    rows = conn.execute(
        """
        SELECT key_id, api_key
          FROM keymaster_keys
         WHERE is_encrypted = 0
            OR (key_ciphertext = '' AND api_key <> '')
        """
    ).fetchall()
    converted = 0
    for row in rows:
        key_id = int(row["key_id"] if isinstance(row, sqlite3.Row) else row[0])
        api_key_raw = str(row["api_key"] if isinstance(row, sqlite3.Row) else row[1] or "").strip()
        if not api_key_raw:
            continue
        key_norm = normalize_api_key(api_key_raw)
        fingerprint = _build_fingerprint(key_norm, keys["fingerprint_key"])
        ciphertext_payload = _encrypt_api_key(key_norm, keys["encryption_key"])
        conn.execute(
            """
            UPDATE keymaster_keys
               SET api_key = '',
                   api_key_normalized = ?,
                   key_ciphertext = ?,
                   key_fingerprint = ?,
                   is_encrypted = 1,
                   updated_at = ?
             WHERE key_id = ?
            """,
            (
                fingerprint,
                ciphertext_payload,
                fingerprint,
                _utcnow(),
                key_id,
            ),
        )
        converted += 1
    return converted


def migrate_rows_to_plaintext(
    conn: sqlite3.Connection,
    *,
    session_keys: dict[str, bytes],
) -> int:
    """Decrypt all encrypted rows in-place and return converted count."""
    keys = _require_secure_session_dummy(session_keys)
    rows = conn.execute(
        """
        SELECT key_id, key_ciphertext
          FROM keymaster_keys
         WHERE is_encrypted = 1
        """
    ).fetchall()
    converted = 0
    for row in rows:
        key_id = int(row["key_id"] if isinstance(row, sqlite3.Row) else row[0])
        payload = str(row["key_ciphertext"] if isinstance(row, sqlite3.Row) else row[1] or "")
        api_key = _decrypt_api_key(payload, keys["encryption_key"])
        conn.execute(
            """
            UPDATE keymaster_keys
               SET api_key = ?,
                   api_key_normalized = ?,
                   key_ciphertext = '',
                   key_fingerprint = '',
                   is_encrypted = 0,
                   updated_at = ?
             WHERE key_id = ?
            """,
            (api_key, api_key, _utcnow(), key_id),
        )
        converted += 1
    return converted


def destructive_reset(conn: sqlite3.Connection) -> None:
    """Remove all key rows and passphrase metadata (forgotten passphrase recovery path)."""
    conn.execute("DELETE FROM keymaster_keys")
    clear_passphrase_metadata(conn)
    set_secure_mode(conn, True)


def legacy_plaintext_row_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM keymaster_keys
         WHERE is_encrypted = 0
           AND trim(api_key) <> ''
        """
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return int(row["n"])
    return int(row[0])


def switch_secure_mode(
    conn: sqlite3.Connection,
    enabled: bool,
    *,
    session_keys: Optional[dict[str, bytes]] = None,
) -> int:
    """
    Switch secure mode and convert rows as needed.

    Returns number of converted rows.
    """
    target_enabled = bool(enabled)
    converted = 0

    if target_enabled:
        if not passphrase_is_configured(conn):
            raise PassphraseRequiredError("Keymaster passphrase is required")
        _require_secure_session(conn, session_keys)
        set_secure_mode(conn, True)
        converted = migrate_legacy_plaintext_rows(conn, session_keys=session_keys or {})
        return converted

    # Disabling secure mode: if encrypted rows exist, require an unlock so values
    # can be decrypted and preserved.
    if encrypted_row_count(conn) > 0:
        converted = migrate_rows_to_plaintext(conn, session_keys=session_keys or {})
    set_secure_mode(conn, False)
    return converted
