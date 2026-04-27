"""
Sidecar SQLite store for the se_dork module.

DB path: ~/.dirracuda/data/experimental/se_dork.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() and open_connection() own their setup; init_db() commits internally.
  - insert_run(), update_run(), and insert_result() accept a caller-supplied
    connection and do NOT commit. The caller owns BEGIN / commit / rollback.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Optional

from experimental.se_dork.models import RunOptions, RUN_STATUS_RUNNING
from shared.path_service import get_paths, get_legacy_paths, select_existing_path


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS dork_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    instance_url   TEXT    NOT NULL,
    query          TEXT    NOT NULL,
    max_results    INTEGER NOT NULL,
    fetched_count  INTEGER NOT NULL DEFAULT 0,
    deduped_count  INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL,
    error_message  TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS dork_results (
    result_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL,
    url                 TEXT    NOT NULL,
    url_normalized      TEXT    NOT NULL,
    title               TEXT,
    snippet             TEXT,
    source_engine       TEXT,
    source_engines_json TEXT,
    verdict             TEXT,
    reason_code         TEXT,
    http_status         INTEGER,
    checked_at          TEXT,
    probe_status        TEXT    NOT NULL DEFAULT 'unprobed',
    probe_indicator_matches INTEGER NOT NULL DEFAULT 0,
    probe_preview       TEXT,
    probe_checked_at    TEXT,
    probe_error         TEXT,
    FOREIGN KEY (run_id) REFERENCES dork_runs(run_id),
    UNIQUE (run_id, url_normalized)
)
"""

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "dork_runs": {
        "run_id", "started_at", "finished_at", "instance_url", "query",
        "max_results", "fetched_count", "deduped_count", "verified_count",
        "status", "error_message",
    },
    "dork_results": {
        "result_id", "run_id", "url", "url_normalized", "title", "snippet",
        "source_engine", "source_engines_json", "verdict", "reason_code",
        "http_status", "checked_at", "probe_status", "probe_indicator_matches",
        "probe_preview", "probe_checked_at", "probe_error",
    },
}

_PROBE_COLUMN_ALTERS = (
    "ALTER TABLE dork_results ADD COLUMN probe_status TEXT NOT NULL DEFAULT 'unprobed'",
    "ALTER TABLE dork_results ADD COLUMN probe_indicator_matches INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dork_results ADD COLUMN probe_preview TEXT",
    "ALTER TABLE dork_results ADD COLUMN probe_checked_at TEXT",
    "ALTER TABLE dork_results ADD COLUMN probe_error TEXT",
)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    if override is not None:
        return override
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    return select_existing_path(
        paths.se_dork_db_file,
        [
            legacy.flat_sidecar_se_dork_file,
            legacy.legacy_home_root / "se_dork.db",
        ],
    )


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
        _ensure_probe_columns(conn)
        conn.commit()


def _ensure_probe_columns(conn: sqlite3.Connection) -> None:
    """
    Backfill probe columns on older sidecar DBs.

    Safe to call repeatedly; only missing columns are added.
    """
    present = {row[1] for row in conn.execute("PRAGMA table_info(dork_results)")}
    for alter_sql in _PROBE_COLUMN_ALTERS:
        col = alter_sql.split(" ADD COLUMN ", 1)[1].split(" ", 1)[0]
        if col not in present:
            conn.execute(alter_sql)
            present.add(col)


# ---------------------------------------------------------------------------
# Schema guard
# ---------------------------------------------------------------------------


def _check_schema(conn: sqlite3.Connection) -> None:
    """
    Verify the open connection has the expected sidecar schema.

    Checks:
      1. All required columns are present in each table.
      2. UNIQUE (run_id, url_normalized) exists on dork_results — exact match,
         origin in ('u', 'c') so both UNIQUE constraint and CREATE UNIQUE INDEX
         are accepted.
      3. FK dork_results.run_id → dork_runs(run_id) with both from and to columns
         verified.

    Raises RuntimeError on any mismatch.
    """
    for table, required_cols in _REQUIRED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = required_cols - present
        if missing:
            raise RuntimeError(
                f"se_dork sidecar schema: {table} missing columns {missing}"
            )

    # Verify exact UNIQUE(run_id, url_normalized) on dork_results
    indexes = {
        row[1]: (bool(row[2]), row[3])
        for row in conn.execute("PRAGMA index_list('dork_results')")
    }
    unique_ok = any(
        is_unique and origin in ("u", "c")
        and {r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")}
           == {"run_id", "url_normalized"}
        for name, (is_unique, origin) in indexes.items()
    )
    if not unique_ok:
        raise RuntimeError(
            "se_dork sidecar schema: dork_results missing UNIQUE(run_id, url_normalized)"
        )

    # Verify FK dork_results.run_id → dork_runs(run_id)
    # PRAGMA foreign_key_list columns: id, seq, table, from, to, ...
    fk_ok = any(
        row[2] == "dork_runs" and row[3] == "run_id" and row[4] == "run_id"
        for row in conn.execute("PRAGMA foreign_key_list('dork_results')")
    )
    if not fk_ok:
        raise RuntimeError(
            "se_dork sidecar schema: dork_results missing FK run_id → dork_runs(run_id)"
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
# URL normalization
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """
    Return a canonical form of url for dedupe purposes.

    Locked policy:
      - Lowercase scheme and netloc
      - Strip trailing slash from path
      - Drop query string
      - Drop fragment
    """
    try:
        parsed = urllib.parse.urlparse(url)
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=parsed.path.rstrip("/"),
            query="",
            fragment="",
        )
        return urllib.parse.urlunparse(normalized)
    except Exception:
        return url.lower().rstrip("/")


# ---------------------------------------------------------------------------
# CRUD (caller owns connection and commits)
# ---------------------------------------------------------------------------


def insert_run(
    conn: sqlite3.Connection,
    options: RunOptions,
    started_at: str,
) -> int:
    """
    Insert a new dork_runs row with status=RUN_STATUS_RUNNING.

    Returns the new run_id (ROWID).
    """
    cur = conn.execute(
        """
        INSERT INTO dork_runs
            (started_at, instance_url, query, max_results, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (started_at, options.instance_url, options.query,
         options.max_results, RUN_STATUS_RUNNING),
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
        UPDATE dork_runs
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
    row: dict,
) -> bool:
    """
    Insert a dork_results row for the given run.

    Deduplication: INSERT OR IGNORE on UNIQUE (run_id, url_normalized).
    Returns True if the row was inserted, False if it was a duplicate.

    Expected dict keys: url, title, content (->snippet), engine (->source_engine),
    engines (->source_engines_json list).
    """
    url = row.get("url", "")
    url_norm = normalize_url(url)
    engines = row.get("engines")
    engines_json = json.dumps(engines) if isinstance(engines, list) else None

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO dork_results
            (run_id, url, url_normalized, title, snippet,
             source_engine, source_engines_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            url,
            url_norm,
            row.get("title"),
            row.get("content"),
            row.get("engine"),
            engines_json,
        ),
    )
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# C4: Classification read/write helpers (caller owns connection and commits)
# ---------------------------------------------------------------------------


def get_pending_results(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[dict]:
    """
    Return unclassified rows for a run (verdict IS NULL).

    Returns list of dicts with keys: result_id, url.
    """
    cursor = conn.execute(
        "SELECT result_id, url FROM dork_results WHERE run_id = ? AND verdict IS NULL",
        (run_id,),
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_results_for_run(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[dict]:
    """
    Return all retained rows for a run.

    Returns list of dicts with keys: result_id, url.
    """
    cursor = conn.execute(
        "SELECT result_id, url FROM dork_results WHERE run_id = ? ORDER BY result_id ASC",
        (run_id,),
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def update_result_verdict(
    conn: sqlite3.Connection,
    result_id: int,
    verdict: str,
    reason_code: Optional[str],
    http_status: Optional[int],
    checked_at: str,
) -> None:
    """Write verdict/reason_code/http_status/checked_at on a single result row."""
    conn.execute(
        """
        UPDATE dork_results
           SET verdict    = ?,
               reason_code = ?,
               http_status = ?,
               checked_at  = ?
         WHERE result_id = ?
        """,
        (verdict, reason_code, http_status, checked_at, result_id),
    )


def update_result_probe(
    conn: sqlite3.Connection,
    result_id: int,
    probe_status: str,
    probe_indicator_matches: int,
    probe_preview: Optional[str],
    probe_checked_at: Optional[str],
    probe_error: Optional[str],
) -> None:
    """Write probe fields on one dork_results row."""
    conn.execute(
        """
        UPDATE dork_results
           SET probe_status = ?,
               probe_indicator_matches = ?,
               probe_preview = ?,
               probe_checked_at = ?,
               probe_error = ?
         WHERE result_id = ?
        """,
        (
            probe_status,
            probe_indicator_matches,
            probe_preview,
            probe_checked_at,
            probe_error,
            result_id,
        ),
    )


def update_run_verified_count(
    conn: sqlite3.Connection,
    run_id: int,
    verified_count: int,
) -> None:
    """Update verified_count on the run row."""
    conn.execute(
        "UPDATE dork_runs SET verified_count = ? WHERE run_id = ?",
        (verified_count, run_id),
    )


def delete_non_open_results(
    conn: sqlite3.Connection,
    run_id: Optional[int] = None,
) -> int:
    """
    Delete non-OPEN_INDEX rows from dork_results.

    Deletion condition:
      verdict IS NULL OR verdict != 'OPEN_INDEX'

    If run_id is provided, only that run is purged.
    If run_id is None, purge across all runs.

    Returns number of deleted rows.
    """
    if run_id is None:
        cur = conn.execute(
            """
            DELETE FROM dork_results
             WHERE verdict IS NULL OR verdict != 'OPEN_INDEX'
            """
        )
    else:
        cur = conn.execute(
            """
            DELETE FROM dork_results
             WHERE run_id = ?
               AND (verdict IS NULL OR verdict != 'OPEN_INDEX')
            """,
            (run_id,),
        )
    return cur.rowcount


def count_open_index_results(
    conn: sqlite3.Connection,
    run_id: int,
) -> int:
    """Return count of retained OPEN_INDEX rows for a run."""
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM dork_results
         WHERE run_id = ?
           AND verdict = 'OPEN_INDEX'
        """,
        (run_id,),
    ).fetchone()
    return int(row[0] if row else 0)


def get_all_results(conn: sqlite3.Connection) -> list[dict]:
    """
    Return all result rows for browser display (all runs), newest run_id first.

    Dict keys match DB column names exactly:
    result_id, run_id, url, title, verdict, reason_code, http_status, checked_at,
    probe_status, probe_indicator_matches, probe_preview, probe_checked_at, probe_error.
    """
    cursor = conn.execute(
        """
        SELECT result_id, run_id, url, title, verdict, reason_code,
               http_status, checked_at,
               probe_status, probe_indicator_matches, probe_preview,
               probe_checked_at, probe_error
          FROM dork_results
         ORDER BY run_id DESC, result_id ASC
        """
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
