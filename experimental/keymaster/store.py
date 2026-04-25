"""
Sidecar SQLite store for the Keymaster module.

DB path: ~/.dirracuda/keymaster.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() owns setup and commit.
  - create_key/update_key/delete_key/touch_last_used/list_keys accept
    a caller-supplied connection and do NOT commit.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Optional

from experimental.keymaster.models import (
    PROVIDERS,
    KeymasterKey,
    DuplicateKeyError,
)

_SIDECAR_DEFAULT = Path.home() / ".dirracuda" / "keymaster.db"

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
    CHECK (provider IN ('SHODAN'))
)
"""

_DDL_UNIQUE_KEY = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_keymaster_provider_key_norm
    ON keymaster_keys(provider, api_key_normalized)
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
}


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


def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    return override if override is not None else _SIDECAR_DEFAULT


def init_db(path: Optional[Path] = None) -> None:
    """Create sidecar DB and schema."""
    resolved = get_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_DDL_KEYS)
        conn.execute(_DDL_UNIQUE_KEY)
        conn.commit()


def _check_schema(conn: sqlite3.Connection) -> None:
    """Validate sidecar schema for runtime safety."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(keymaster_keys)")}
    missing = _REQUIRED_COLUMNS - present
    if missing:
        raise RuntimeError(f"keymaster sidecar schema: missing columns {missing}")

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
    return conn


def _row_to_key_dict(row: sqlite3.Row) -> dict:
    key = KeymasterKey(
        key_id=int(row["key_id"]),
        provider=str(row["provider"]),
        label=str(row["label"] or ""),
        api_key=str(row["api_key"] or ""),
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
    }


def key_exists(
    conn: sqlite3.Connection,
    provider: str,
    api_key: str,
    *,
    exclude_key_id: Optional[int] = None,
) -> bool:
    """Return True if provider already has the same normalized api_key."""
    provider_norm = _normalize_provider(provider)
    key_norm = normalize_api_key(api_key)
    if exclude_key_id is None:
        row = conn.execute(
            "SELECT 1 FROM keymaster_keys WHERE provider = ? AND api_key_normalized = ? LIMIT 1",
            (provider_norm, key_norm),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM keymaster_keys WHERE provider = ? AND api_key_normalized = ? AND key_id <> ? LIMIT 1",
            (provider_norm, key_norm, exclude_key_id),
        ).fetchone()
    return row is not None


def create_key(
    conn: sqlite3.Connection,
    provider: str,
    label: str,
    api_key: str,
    notes: Optional[str],
) -> int:
    """Insert a key entry and return new key_id."""
    provider_norm = _normalize_provider(provider)
    label_norm = _require_nonempty(label, "label")
    key_norm = normalize_api_key(_require_nonempty(api_key, "api_key"))
    if key_exists(conn, provider_norm, key_norm):
        raise DuplicateKeyError(
            f"api_key already exists for provider {provider_norm}"
        )
    notes_norm = _normalize_text(notes)
    now = _utcnow()
    cur = conn.execute(
        """
        INSERT INTO keymaster_keys
            (provider, label, api_key, api_key_normalized, notes, created_at, updated_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (provider_norm, label_norm, key_norm, key_norm, notes_norm, now, now),
    )
    return int(cur.lastrowid)


def get_key(conn: sqlite3.Connection, key_id: int) -> Optional[dict]:
    """Return one key by ID, or None."""
    row = conn.execute(
        """
        SELECT key_id, provider, label, api_key, api_key_normalized, notes,
               created_at, updated_at, last_used_at
          FROM keymaster_keys
         WHERE key_id = ?
        """,
        (key_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_key_dict(row)


def update_key(
    conn: sqlite3.Connection,
    key_id: int,
    label: str,
    api_key: str,
    notes: Optional[str],
) -> None:
    """Update one key entry."""
    existing = get_key(conn, key_id)
    if existing is None:
        raise KeyError(f"keymaster key not found: {key_id}")
    label_norm = _require_nonempty(label, "label")
    key_norm = normalize_api_key(_require_nonempty(api_key, "api_key"))
    provider_norm = existing["provider"]
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
               updated_at = ?
         WHERE key_id = ?
        """,
        (label_norm, key_norm, key_norm, _normalize_text(notes), _utcnow(), key_id),
    )


def delete_key(conn: sqlite3.Connection, key_id: int) -> bool:
    """Delete one key entry. Returns True if deleted."""
    if get_key(conn, key_id) is None:
        return False
    cur = conn.execute("DELETE FROM keymaster_keys WHERE key_id = ?", (key_id,))
    return cur.rowcount > 0


def list_keys(
    conn: sqlite3.Connection,
    provider: str,
    search_text: str = "",
) -> list[dict]:
    """Return ordered key entries for a provider, optionally filtered by search."""
    provider_norm = _normalize_provider(provider)
    search_norm = str(search_text or "").strip().lower()

    if search_norm:
        like = f"%{search_norm}%"
        rows = conn.execute(
            """
            SELECT key_id, provider, label, api_key, api_key_normalized, notes,
                   created_at, updated_at, last_used_at
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
                   created_at, updated_at, last_used_at
              FROM keymaster_keys
             WHERE provider = ?
             ORDER BY lower(label), key_id ASC
            """,
            (provider_norm,),
        ).fetchall()
    return [_row_to_key_dict(row) for row in rows]


def touch_last_used(conn: sqlite3.Connection, key_id: int) -> None:
    """Update last_used_at for the given key."""
    conn.execute(
        "UPDATE keymaster_keys SET last_used_at = ? WHERE key_id = ?",
        (_utcnow(), key_id),
    )
