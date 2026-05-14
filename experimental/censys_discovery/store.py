"""
Sidecar SQLite store for the censys_discovery module.

DB path: ~/.dirracuda/data/experimental/censys_discovery.db

Transaction ownership:
  - init_db() owns setup and commits internally.
  - open_connection() validates schema and returns a connection.
  - insert_run(), update_run(), and insert_result() accept a caller-supplied
    connection and do NOT commit. The caller owns BEGIN / commit / rollback.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from experimental.censys_discovery.models import (
    CensysRunOptions,
    RUN_STATUS_RUNNING,
    SearchResultItem,
)
from shared.path_service import get_paths


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS censys_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    protocol       TEXT    NOT NULL,
    query_text     TEXT    NOT NULL,
    query_hours    INTEGER,
    max_pages      INTEGER NOT NULL,
    page_size      INTEGER NOT NULL,
    fetched_count  INTEGER NOT NULL DEFAULT 0,
    deduped_count  INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL,
    error_message  TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS censys_results (
    result_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL,
    protocol           TEXT    NOT NULL,
    ip_address         TEXT    NOT NULL,
    port               INTEGER NOT NULL,
    transport_protocol TEXT,
    banner             TEXT,
    scan_time          TEXT,
    source_json        TEXT    NOT NULL,
    dedupe_key         TEXT    NOT NULL,
    FOREIGN KEY (run_id) REFERENCES censys_runs(run_id),
    UNIQUE (run_id, dedupe_key)
)
"""

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "censys_runs": {
        "run_id", "started_at", "finished_at", "protocol", "query_text",
        "query_hours", "max_pages", "page_size", "fetched_count",
        "deduped_count", "status", "error_message",
    },
    "censys_results": {
        "result_id", "run_id", "protocol", "ip_address", "port",
        "transport_protocol", "banner", "scan_time", "source_json", "dedupe_key",
    },
}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    if override is not None:
        return override
    return get_paths().experimental_dir / "censys_discovery.db"


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------


def init_db(path: Optional[Path] = None) -> None:
    """
    Create sidecar DB and tables if they do not exist.

    Idempotent — safe to call multiple times. Creates parent directories.
    """
    resolved = get_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_DDL_RUNS)
        conn.execute(_DDL_RESULTS)
        conn.commit()


# ---------------------------------------------------------------------------
# Schema guard
# ---------------------------------------------------------------------------


def _check_schema(conn: sqlite3.Connection) -> None:
    """
    Verify the open connection has the expected sidecar schema.

    Checks:
      1. All required columns present in each table.
      2. UNIQUE(run_id, dedupe_key) exists on censys_results — origin in ('u', 'c').
      3. FK censys_results.run_id → censys_runs(run_id) with from and to columns verified.

    Raises RuntimeError on any mismatch.
    """
    for table, required_cols in _REQUIRED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = required_cols - present
        if missing:
            raise RuntimeError(
                f"censys_discovery sidecar schema: {table} missing columns {missing}"
            )

    indexes = {
        row[1]: (bool(row[2]), row[3])
        for row in conn.execute("PRAGMA index_list('censys_results')")
    }
    unique_ok = any(
        is_unique and origin in ("u", "c")
        and {r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")}
           == {"run_id", "dedupe_key"}
        for name, (is_unique, origin) in indexes.items()
    )
    if not unique_ok:
        raise RuntimeError(
            "censys_discovery sidecar schema: "
            "censys_results missing UNIQUE(run_id, dedupe_key)"
        )

    fk_ok = any(
        row[2] == "censys_runs" and row[3] == "run_id" and row[4] == "run_id"
        for row in conn.execute("PRAGMA foreign_key_list('censys_results')")
    )
    if not fk_ok:
        raise RuntimeError(
            "censys_discovery sidecar schema: "
            "censys_results missing FK run_id → censys_runs(run_id)"
        )


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def open_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open a write connection to the sidecar DB with WAL + FK enabled.

    Validates schema before returning. Caller is responsible for
    commit / rollback / close.
    """
    resolved = get_db_path(path)
    conn = sqlite3.connect(str(resolved))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------


def build_dedupe_key(
    ip: str,
    port: int,
    protocol: str,
    transport_protocol: Optional[str],
) -> str:
    """
    Return a canonical dedupe key for one service observation.

    Normalizes protocol to upper-case and transport to lower-case so that
    casing variants from the API payload produce identical keys.
    """
    return "|".join([
        ip.strip(),
        str(port),
        protocol.upper(),
        (transport_protocol or "").lower(),
    ])


# ---------------------------------------------------------------------------
# CRUD (caller owns connection and commits)
# ---------------------------------------------------------------------------


def insert_run(
    conn: sqlite3.Connection,
    options: CensysRunOptions,
    started_at: str,
    query_text: str,
) -> int:
    """
    Insert a new censys_runs row with status=RUN_STATUS_RUNNING.

    Returns the new run_id (ROWID).
    """
    cur = conn.execute(
        """
        INSERT INTO censys_runs
            (started_at, protocol, query_text, query_hours,
             max_pages, page_size, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            options.protocol.upper(),
            query_text,
            options.query_hours,
            options.max_pages,
            options.page_size,
            RUN_STATUS_RUNNING,
        ),
    )
    return cur.lastrowid


def update_run(
    conn: sqlite3.Connection,
    run_id: int,
    finished_at: str,
    fetched_count: int,
    deduped_count: int,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Update run counts, status, and finished_at on an existing run row."""
    conn.execute(
        """
        UPDATE censys_runs
           SET finished_at   = ?,
               fetched_count = ?,
               deduped_count = ?,
               status        = ?,
               error_message = ?
         WHERE run_id = ?
        """,
        (finished_at, fetched_count, deduped_count, status, error_message, run_id),
    )


def insert_result(
    conn: sqlite3.Connection,
    run_id: int,
    item: SearchResultItem,
) -> bool:
    """
    Insert a censys_results row for the given run.

    Deduplication: INSERT OR IGNORE on UNIQUE(run_id, dedupe_key).
    Returns True if the row was inserted, False if it was a duplicate.
    """
    dk = build_dedupe_key(item.ip_address, item.port, item.protocol, item.transport_protocol)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO censys_results
            (run_id, protocol, ip_address, port, transport_protocol,
             banner, scan_time, source_json, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            item.protocol.upper(),
            item.ip_address,
            item.port,
            item.transport_protocol,
            item.banner,
            item.scan_time,
            item.source_json,
            dk,
        ),
    )
    return cur.rowcount == 1
