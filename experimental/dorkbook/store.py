"""
Sidecar SQLite store for the Dorkbook module.

DB path: ~/.dirracuda/data/experimental/dorkbook.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() owns setup and commit.
  - create_entry/update_entry/delete_entry/upsert_builtin_pack/list_entries accept
    a caller-supplied connection and do NOT commit.
"""

from __future__ import annotations

import datetime
from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from experimental.dorkbook.models import (
    DEFAULT_BUILTIN_DORKS,
    DEFAULT_TOPIC,
    PROVIDERS,
    PROVIDER_SHODAN,
    PROVIDER_SELF_HOSTED,
    PROTOCOLS,
    ROW_KIND_BUILTIN,
    ROW_KIND_CUSTOM,
    BuiltinDork,
    DorkbookEntry,
    DuplicateEntryError,
    ReadOnlyEntryError,
)
from experimental.dorkbook.schema import check_schema, ensure_schema
from shared.path_service import get_paths, get_legacy_paths, select_existing_path


def _utcnow() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _normalize_provider(provider: str) -> str:
    if not isinstance(provider, str) or provider.strip().lower() not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")
    return provider.strip().lower()


def _normalize_protocol(protocol: Optional[str], provider: str) -> Optional[str]:
    if provider == PROVIDER_SELF_HOSTED:
        if protocol not in (None, ""):
            raise ValueError("Self-hosted Search dorks do not have a protocol")
        return None
    if not isinstance(protocol, str) or protocol.strip().upper() not in PROTOCOLS:
        raise ValueError(f"unsupported protocol: {protocol!r}")
    return protocol.strip().upper()


def _normalize_topic(topic: Optional[str]) -> str:
    if topic is None:
        return DEFAULT_TOPIC
    if not isinstance(topic, str):
        raise ValueError("topic must be text")
    return topic.strip() or DEFAULT_TOPIC


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
    if override is not None:
        return override
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    return select_existing_path(
        paths.dorkbook_db_file,
        [
            legacy.flat_sidecar_dorkbook_file,
            legacy.legacy_home_root / "dorkbook.db",
        ],
    )


def init_db(path: Optional[Path] = None) -> None:
    """Create or migrate the sidecar and refresh read-only built-ins atomically."""
    resolved = get_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(resolved))) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn, resolved)
            upsert_builtin_pack(conn)
        conn.execute("PRAGMA journal_mode=WAL")


def open_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open and validate a write connection to the sidecar DB."""
    resolved = get_db_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"dorkbook sidecar DB not found at {resolved} — call init_db() first"
        )
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    try:
        check_schema(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conn.close()
        raise
    return conn


def _row_to_entry_dict(row: sqlite3.Row) -> dict:
    entry = DorkbookEntry(
        entry_id=int(row["entry_id"]),
        protocol=row["protocol"],
        provider=str(row["provider"]),
        topic=str(row["topic"]),
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
        "provider": entry.provider,
        "topic": entry.topic,
        "nickname": entry.nickname,
        "query": entry.query,
        "notes": entry.notes,
        "row_kind": entry.row_kind,
        "builtin_key": entry.builtin_key,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def list_entries(
    conn: sqlite3.Connection,
    protocol: Optional[str] = None,
    search_text: str = "",
    *,
    provider: Optional[str] = PROVIDER_SHODAN,
    topic: Optional[str] = None,
) -> list[dict]:
    """List a provider/protocol, or the entire library with provider=None."""
    clauses = []
    params = []
    if provider is not None:
        provider = _normalize_provider(provider)
        clauses.append("provider = ?")
        params.append(provider)
    if protocol is not None:
        protocol = _normalize_protocol(protocol, provider or PROVIDER_SHODAN)
        clauses.append("protocol IS ?")
        params.append(protocol)
    if topic is not None:
        clauses.append("topic = ?")
        params.append(_normalize_topic(topic))
    search = str(search_text or "").strip().lower()
    if search:
        clauses.append("(lower(query) LIKE ? OR lower(nickname) LIKE ? OR lower(notes) LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    where = " AND ".join(clauses) if clauses else "1"
    rows = conn.execute(
        f"""
        SELECT * FROM dorkbook_entries WHERE {where}
         ORDER BY CASE provider WHEN 'shodan' THEN 0 ELSE 1 END,
                  CASE row_kind WHEN 'builtin' THEN 0 ELSE 1 END,
                  lower(CASE WHEN trim(nickname) <> '' THEN nickname ELSE query END),
                  entry_id ASC
        """, params,
    ).fetchall()
    return [_row_to_entry_dict(row) for row in rows]


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Optional[dict]:
    """Return one entry by ID, or None."""
    row = conn.execute(
        """
        SELECT *
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
    protocol: Optional[str],
    query: str,
    *,
    exclude_entry_id: Optional[int] = None,
    provider: str = PROVIDER_SHODAN,
) -> bool:
    """Check trimmed exact equality within a provider and applicable protocol."""
    provider = _normalize_provider(provider)
    protocol = _normalize_protocol(protocol, provider)
    row = conn.execute(
        """SELECT 1 FROM dorkbook_entries
            WHERE provider = ? AND protocol IS ? AND query_normalized = ?
              AND (? IS NULL OR entry_id <> ?) LIMIT 1""",
        (provider, protocol, normalize_query(query), exclude_entry_id, exclude_entry_id),
    ).fetchone()
    return row is not None


def create_entry(
    conn: sqlite3.Connection,
    protocol: Optional[str],
    nickname: Optional[str],
    query: str,
    notes: Optional[str],
    *,
    provider: str = PROVIDER_SHODAN,
    topic: Optional[str] = DEFAULT_TOPIC,
) -> int:
    """Insert a custom entry and return new entry_id."""
    provider = _normalize_provider(provider)
    protocol_norm = _normalize_protocol(protocol, provider)
    query_norm = normalize_query(query)
    if query_exists(conn, protocol_norm, query_norm, provider=provider):
        raise DuplicateEntryError(
            f"query already exists in {protocol_norm or 'Self-hosted Search'} Dorkbook"
        )
    nickname_norm = _normalize_optional_text(nickname)
    notes_norm = _normalize_optional_text(notes)
    now = _utcnow()
    cur = conn.execute(
        """
        INSERT INTO dorkbook_entries
            (provider, protocol, topic, nickname, query, query_normalized, notes, row_kind, builtin_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            provider,
            protocol_norm,
            _normalize_topic(topic),
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
    *,
    topic: Optional[str] = None,
) -> None:
    """Update one custom entry."""
    existing = get_entry(conn, entry_id)
    if existing is None:
        raise KeyError(f"dorkbook entry not found: {entry_id}")
    if existing["row_kind"] == ROW_KIND_BUILTIN:
        raise ReadOnlyEntryError("built-in dorks are read-only")

    query_norm = normalize_query(query)
    provider = existing["provider"]
    protocol_norm = _normalize_protocol(existing["protocol"], provider)
    if query_exists(conn, protocol_norm, query_norm, exclude_entry_id=entry_id, provider=provider):
        raise DuplicateEntryError(
            f"query already exists in {protocol_norm or 'Self-hosted Search'} Dorkbook"
        )

    conn.execute(
        """
        UPDATE dorkbook_entries
           SET topic = ?,
               nickname = ?,
               query = ?,
               query_normalized = ?,
               notes = ?,
               updated_at = ?
         WHERE entry_id = ?
        """,
        (
            existing["topic"] if topic is None else _normalize_topic(topic),
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
        provider = _normalize_provider(spec.provider)
        protocol = _normalize_protocol(spec.protocol, provider)
        query = normalize_query(spec.query)
        values = (
            provider, protocol, _normalize_topic(spec.topic),
            _normalize_optional_text(spec.nickname), query, query,
            _normalize_optional_text(spec.notes), ROW_KIND_BUILTIN,
        )
        existing = conn.execute(
            """SELECT entry_id, provider, protocol, topic, nickname, query,
                      query_normalized, notes, row_kind
                 FROM dorkbook_entries WHERE builtin_key = ?""",
            (spec.builtin_key,),
        ).fetchone()
        entry_id = existing[0] if existing else None
        if existing and existing[-1] != ROW_KIND_BUILTIN:
            continue  # A custom row is never converted by a seed refresh.
        if query_exists(conn, protocol, query, provider=provider, exclude_entry_id=entry_id):
            continue
        if existing:
            if tuple(existing)[1:] == values:
                continue  # Reopening the library must not change timestamps.
            conn.execute(
                """UPDATE dorkbook_entries
                      SET provider=?, protocol=?, topic=?, nickname=?, query=?,
                          query_normalized=?, notes=?, row_kind=?, updated_at=?
                    WHERE entry_id=?""", (*values, now, entry_id),
            )
        else:
            conn.execute(
                """INSERT INTO dorkbook_entries
                   (provider, protocol, topic, nickname, query, query_normalized,
                    notes, row_kind, builtin_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, spec.builtin_key, now, now),
            )
        touched += 1
    return touched
