"""
Sidecar SQLite store for the Dorkbook module.

DB path: ~/.dirracuda/dorkbook.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() owns setup and commit.
  - create_entry/update_entry/delete_entry/upsert_builtin_pack/list_entries accept
    a caller-supplied connection and do NOT commit.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from experimental.dorkbook.models import (
    DEFAULT_BUILTIN_DORKS,
    PROTOCOLS,
    ROW_KIND_BUILTIN,
    ROW_KIND_CUSTOM,
    BuiltinDork,
    DorkbookEntry,
    DuplicateEntryError,
    ReadOnlyEntryError,
)

_SIDECAR_DEFAULT = Path.home() / ".dirracuda" / "dorkbook.db"

_DDL_ENTRIES = """
CREATE TABLE IF NOT EXISTS dorkbook_entries (
    entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol          TEXT NOT NULL,
    nickname          TEXT NOT NULL DEFAULT '',
    query             TEXT NOT NULL,
    query_normalized  TEXT NOT NULL,
    notes             TEXT NOT NULL DEFAULT '',
    row_kind          TEXT NOT NULL DEFAULT 'custom',
    builtin_key       TEXT UNIQUE,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (protocol IN ('SMB', 'FTP', 'HTTP')),
    CHECK (row_kind IN ('builtin', 'custom')),
    CHECK ((row_kind = 'builtin' AND builtin_key IS NOT NULL) OR row_kind = 'custom')
)
"""

_DDL_UNIQUE_QUERY = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_dorkbook_protocol_query_norm
    ON dorkbook_entries(protocol, query_normalized)
"""

_REQUIRED_COLUMNS = {
    "entry_id",
    "protocol",
    "nickname",
    "query",
    "query_normalized",
    "notes",
    "row_kind",
    "builtin_key",
    "created_at",
    "updated_at",
}


def _utcnow() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _normalize_protocol(protocol: str) -> str:
    value = str(protocol or "").strip().upper()
    if value not in PROTOCOLS:
        raise ValueError(f"unsupported protocol: {protocol!r}")
    return value


def normalize_query(query: str) -> str:
    value = str(query or "").strip()
    if not value:
        raise ValueError("query is required")
    return value


def _normalize_optional_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    return override if override is not None else _SIDECAR_DEFAULT


def init_db(path: Optional[Path] = None) -> None:
    """Create sidecar DB and seed read-only builtins."""
    resolved = get_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_DDL_ENTRIES)
        conn.execute(_DDL_UNIQUE_QUERY)
        upsert_builtin_pack(conn)
        conn.commit()


def _check_schema(conn: sqlite3.Connection) -> None:
    """Validate sidecar schema for runtime safety."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(dorkbook_entries)")}
    missing = _REQUIRED_COLUMNS - present
    if missing:
        raise RuntimeError(f"dorkbook sidecar schema: missing columns {missing}")

    indexes = {
        row[1]: bool(row[2])
        for row in conn.execute("PRAGMA index_list('dorkbook_entries')")
    }

    unique_query_ok = any(
        is_unique
        and {r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")}
        == {"protocol", "query_normalized"}
        for name, is_unique in indexes.items()
    )
    if not unique_query_ok:
        raise RuntimeError(
            "dorkbook sidecar schema: missing UNIQUE(protocol, query_normalized)"
        )

    unique_builtin_ok = any(
        is_unique
        and [r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")] == ["builtin_key"]
        for name, is_unique in indexes.items()
    )
    if not unique_builtin_ok:
        raise RuntimeError(
            "dorkbook sidecar schema: missing UNIQUE(builtin_key)"
        )


def open_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open and validate a write connection to the sidecar DB."""
    resolved = get_db_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"dorkbook sidecar DB not found at {resolved} — call init_db() first"
        )
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema(conn)
    return conn


def _row_to_entry_dict(row: sqlite3.Row) -> dict:
    entry = DorkbookEntry(
        entry_id=int(row["entry_id"]),
        protocol=str(row["protocol"]),
        nickname=str(row["nickname"] or ""),
        query=str(row["query"] or ""),
        notes=str(row["notes"] or ""),
        row_kind=str(row["row_kind"] or ROW_KIND_CUSTOM),
        builtin_key=row["builtin_key"],
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )
    return {
        "entry_id": entry.entry_id,
        "protocol": entry.protocol,
        "nickname": entry.nickname,
        "query": entry.query,
        "notes": entry.notes,
        "row_kind": entry.row_kind,
        "builtin_key": entry.builtin_key,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def list_entries(conn: sqlite3.Connection, protocol: str, search_text: str = "") -> list[dict]:
    """Return ordered entries for one protocol tab."""
    protocol_norm = _normalize_protocol(protocol)
    search_norm = str(search_text or "").strip().lower()

    if search_norm:
        like = f"%{search_norm}%"
        rows = conn.execute(
            """
            SELECT entry_id, protocol, nickname, query, notes, row_kind, builtin_key, created_at, updated_at
              FROM dorkbook_entries
             WHERE protocol = ?
               AND (
                    lower(query) LIKE ?
                 OR lower(nickname) LIKE ?
                 OR lower(notes) LIKE ?
               )
             ORDER BY
                CASE row_kind WHEN 'builtin' THEN 0 ELSE 1 END,
                lower(CASE WHEN trim(nickname) <> '' THEN nickname ELSE query END),
                entry_id ASC
            """,
            (protocol_norm, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT entry_id, protocol, nickname, query, notes, row_kind, builtin_key, created_at, updated_at
              FROM dorkbook_entries
             WHERE protocol = ?
             ORDER BY
                CASE row_kind WHEN 'builtin' THEN 0 ELSE 1 END,
                lower(CASE WHEN trim(nickname) <> '' THEN nickname ELSE query END),
                entry_id ASC
            """,
            (protocol_norm,),
        ).fetchall()
    return [_row_to_entry_dict(row) for row in rows]


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Optional[dict]:
    """Return one entry by ID, or None."""
    row = conn.execute(
        """
        SELECT entry_id, protocol, nickname, query, notes, row_kind, builtin_key, created_at, updated_at
          FROM dorkbook_entries
         WHERE entry_id = ?
        """,
        (entry_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_entry_dict(row)


def query_exists(
    conn: sqlite3.Connection,
    protocol: str,
    query: str,
    *,
    exclude_entry_id: Optional[int] = None,
) -> bool:
    """Return True if protocol already has the same normalized query."""
    protocol_norm = _normalize_protocol(protocol)
    query_norm = normalize_query(query)
    if exclude_entry_id is None:
        row = conn.execute(
            """
            SELECT 1
              FROM dorkbook_entries
             WHERE protocol = ?
               AND query_normalized = ?
             LIMIT 1
            """,
            (protocol_norm, query_norm),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
              FROM dorkbook_entries
             WHERE protocol = ?
               AND query_normalized = ?
               AND entry_id <> ?
             LIMIT 1
            """,
            (protocol_norm, query_norm, exclude_entry_id),
        ).fetchone()
    return row is not None


def create_entry(
    conn: sqlite3.Connection,
    protocol: str,
    nickname: Optional[str],
    query: str,
    notes: Optional[str],
) -> int:
    """Insert a custom entry and return new entry_id."""
    protocol_norm = _normalize_protocol(protocol)
    query_norm = normalize_query(query)
    if query_exists(conn, protocol_norm, query_norm):
        raise DuplicateEntryError(
            f"query already exists in {protocol_norm} dorkbook"
        )
    nickname_norm = _normalize_optional_text(nickname)
    notes_norm = _normalize_optional_text(notes)
    now = _utcnow()
    cur = conn.execute(
        """
        INSERT INTO dorkbook_entries
            (protocol, nickname, query, query_normalized, notes, row_kind, builtin_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            protocol_norm,
            nickname_norm,
            query_norm,
            query_norm,
            notes_norm,
            ROW_KIND_CUSTOM,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    nickname: Optional[str],
    query: str,
    notes: Optional[str],
) -> None:
    """Update one custom entry."""
    existing = get_entry(conn, entry_id)
    if existing is None:
        raise KeyError(f"dorkbook entry not found: {entry_id}")
    if existing["row_kind"] == ROW_KIND_BUILTIN:
        raise ReadOnlyEntryError("built-in dorks are read-only")

    query_norm = normalize_query(query)
    protocol_norm = _normalize_protocol(existing["protocol"])
    if query_exists(conn, protocol_norm, query_norm, exclude_entry_id=entry_id):
        raise DuplicateEntryError(
            f"query already exists in {protocol_norm} dorkbook"
        )

    conn.execute(
        """
        UPDATE dorkbook_entries
           SET nickname = ?,
               query = ?,
               query_normalized = ?,
               notes = ?,
               updated_at = ?
         WHERE entry_id = ?
        """,
        (
            _normalize_optional_text(nickname),
            query_norm,
            query_norm,
            _normalize_optional_text(notes),
            _utcnow(),
            entry_id,
        ),
    )


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Delete one custom entry. Returns True if deleted."""
    existing = get_entry(conn, entry_id)
    if existing is None:
        return False
    if existing["row_kind"] == ROW_KIND_BUILTIN:
        raise ReadOnlyEntryError("built-in dorks are read-only")
    cur = conn.execute("DELETE FROM dorkbook_entries WHERE entry_id = ?", (entry_id,))
    return cur.rowcount > 0


def upsert_builtin_pack(
    conn: sqlite3.Connection,
    builtins: Optional[Iterable[BuiltinDork]] = None,
) -> int:
    """
    Ensure built-ins exist and are refreshed by stable key.

    Returns number of touched rows.
    """
    pack = tuple(builtins) if builtins is not None else DEFAULT_BUILTIN_DORKS
    touched = 0
    now = _utcnow()
    for spec in pack:
        protocol = _normalize_protocol(spec.protocol)
        query_norm = normalize_query(spec.query)
        nickname = _normalize_optional_text(spec.nickname)
        notes = _normalize_optional_text(spec.notes)

        by_key = conn.execute(
            "SELECT entry_id FROM dorkbook_entries WHERE builtin_key = ?",
            (spec.builtin_key,),
        ).fetchone()

        by_key_entry_id = int(by_key[0]) if by_key is not None else None
        conflict = conn.execute(
            """
            SELECT entry_id
              FROM dorkbook_entries
             WHERE protocol = ?
               AND query_normalized = ?
             LIMIT 1
            """,
            (protocol, query_norm),
        ).fetchone()
        conflict_entry_id = int(conflict[0]) if conflict is not None else None
        if (
            conflict_entry_id is not None
            and (by_key_entry_id is None or conflict_entry_id != by_key_entry_id)
        ):
            # Preserve availability and existing custom data when builtin specs
            # collide with live protocol/query uniqueness constraints.
            continue

        if by_key is not None:
            conn.execute(
                """
                UPDATE dorkbook_entries
                   SET protocol = ?,
                       nickname = ?,
                       query = ?,
                       query_normalized = ?,
                       notes = ?,
                       row_kind = ?,
                       updated_at = ?
                 WHERE builtin_key = ?
                """,
                (
                    protocol,
                    nickname,
                    query_norm,
                    query_norm,
                    notes,
                    ROW_KIND_BUILTIN,
                    now,
                    spec.builtin_key,
                ),
            )
            touched += 1
            continue

        cur = conn.execute(
            """
            INSERT INTO dorkbook_entries
                (protocol, nickname, query, query_normalized, notes, row_kind, builtin_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                protocol,
                nickname,
                query_norm,
                query_norm,
                notes,
                ROW_KIND_BUILTIN,
                spec.builtin_key,
                now,
                now,
            ),
        )
        touched += 1 if cur.rowcount > 0 else 0
    return touched
