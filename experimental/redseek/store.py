"""
Sidecar SQLite store for the redseek module.

DB path: ~/.dirracuda/reddit_od.db (separate from main dirracuda.db)

Transaction ownership:
  - init_db() and wipe_all() own their connections and commit internally.
  - All other functions (upsert_post, upsert_targets, save_ingest_state,
    get_ingest_state) accept a caller-supplied connection and do NOT commit.
    The caller is responsible for BEGIN / commit / rollback.
"""

import sqlite3
from pathlib import Path
from typing import List, Optional

from experimental.redseek.models import RedditIngestState, RedditPost, RedditTarget

_SIDECAR_DEFAULT = Path.home() / ".dirracuda" / "reddit_od.db"

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL_POSTS = """
CREATE TABLE IF NOT EXISTS reddit_posts (
    post_id           TEXT    NOT NULL,
    post_title        TEXT    NOT NULL,
    post_author       TEXT,
    post_created_utc  REAL    NOT NULL,
    is_nsfw           INTEGER NOT NULL DEFAULT 0,
    had_targets       INTEGER NOT NULL DEFAULT 0,
    source_sort       TEXT    NOT NULL,
    last_seen_at      TEXT    NOT NULL,
    PRIMARY KEY (post_id)
)
"""

_DDL_TARGETS = """
CREATE TABLE IF NOT EXISTS reddit_targets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id           TEXT    NOT NULL,
    target_raw        TEXT    NOT NULL,
    target_normalized TEXT    NOT NULL,
    host              TEXT,
    protocol          TEXT,
    notes             TEXT,
    parse_confidence  TEXT,
    created_at        TEXT    NOT NULL,
    dedupe_key        TEXT    NOT NULL UNIQUE,
    FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)
)
"""

_DDL_INGEST_STATE = """
CREATE TABLE IF NOT EXISTS reddit_ingest_state (
    subreddit             TEXT NOT NULL,
    sort_mode             TEXT NOT NULL,
    last_post_created_utc REAL,
    last_post_id          TEXT,
    last_scrape_time      TEXT,
    PRIMARY KEY (subreddit, sort_mode)
)
"""

# Required columns per table — used by _check_schema
_REQUIRED_COLUMNS = {
    "reddit_posts": {
        "post_id", "post_title", "post_author", "post_created_utc",
        "is_nsfw", "had_targets", "source_sort", "last_seen_at",
    },
    "reddit_targets": {
        "id", "post_id", "target_raw", "target_normalized",
        "host", "protocol", "notes", "parse_confidence",
        "created_at", "dedupe_key",
    },
    "reddit_ingest_state": {
        "subreddit", "sort_mode", "last_post_created_utc",
        "last_post_id", "last_scrape_time",
    },
}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def get_db_path(override: Optional[Path] = None) -> Path:
    """Return sidecar DB path. ``override`` enables test injection."""
    return override if override is not None else _SIDECAR_DEFAULT


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
        conn.execute(_DDL_POSTS)
        conn.execute(_DDL_TARGETS)
        conn.execute(_DDL_INGEST_STATE)
        conn.commit()


# ---------------------------------------------------------------------------
# Schema guard
# ---------------------------------------------------------------------------

def _check_schema(conn: sqlite3.Connection) -> None:
    """
    Verify that the open connection has the expected sidecar schema.

    Checks:
      1. All required columns are present in each table.
      2. reddit_targets.dedupe_key has a UNIQUE constraint.
      3. reddit_ingest_state has composite PK (subreddit, sort_mode) in that order.
      4. reddit_targets has FK post_id -> reddit_posts(post_id).

    Raises RuntimeError on any mismatch.
    """
    # Layer 1 — column presence
    for table, required_cols in _REQUIRED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = required_cols - present
        if missing:
            raise RuntimeError(
                f"sidecar schema: {table} missing columns {missing}"
            )

    # Layer 2 — UNIQUE constraint on dedupe_key
    found_unique = False
    for row in conn.execute("PRAGMA index_list(reddit_targets)"):
        idx_name, is_unique = row[1], row[2]
        if is_unique:
            idx_cols = [r[2] for r in conn.execute(f"PRAGMA index_info({idx_name})")]
            if idx_cols == ["dedupe_key"]:
                found_unique = True
                break
    if not found_unique:
        raise RuntimeError(
            "sidecar schema: reddit_targets missing UNIQUE constraint on dedupe_key"
        )

    # Layer 3 — composite PK on reddit_ingest_state (order-sensitive)
    pk_cols = tuple(
        row[1]
        for row in sorted(
            (r for r in conn.execute("PRAGMA table_info(reddit_ingest_state)") if r[5] > 0),
            key=lambda r: r[5],
        )
    )
    if pk_cols != ("subreddit", "sort_mode"):
        raise RuntimeError(
            f"sidecar schema: reddit_ingest_state PK must be (subreddit, sort_mode), got {pk_cols}"
        )

    # Layer 4 — FK post_id -> reddit_posts(post_id)
    fk_list = [
        (row[2], row[3], row[4])
        for row in conn.execute("PRAGMA foreign_key_list(reddit_targets)")
    ]
    if ("reddit_posts", "post_id", "post_id") not in fk_list:
        raise RuntimeError(
            "sidecar schema: reddit_targets missing FK post_id -> reddit_posts(post_id)"
        )


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def open_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open and return a validated connection to the sidecar DB.

    Raises FileNotFoundError if the DB file does not exist (call init_db first).
    Raises RuntimeError if _check_schema detects schema drift.

    Caller owns the connection lifecycle (close / context manager).
    """
    resolved = get_db_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"reddit sidecar DB not found at {resolved} — call init_db() first"
        )
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# CRUD — caller owns transaction
# ---------------------------------------------------------------------------

def upsert_post(conn: sqlite3.Connection, post: RedditPost) -> None:
    """
    Insert or update a reddit post row.

    Immutable on conflict: post_title, post_author, post_created_utc, source_sort.
    Mutable on conflict: is_nsfw, had_targets, last_seen_at.
    """
    conn.execute(
        """
        INSERT INTO reddit_posts
            (post_id, post_title, post_author, post_created_utc,
             is_nsfw, had_targets, source_sort, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            is_nsfw      = excluded.is_nsfw,
            had_targets  = excluded.had_targets,
            last_seen_at = excluded.last_seen_at
        """,
        (
            post.post_id,
            post.post_title,
            post.post_author,
            post.post_created_utc,
            post.is_nsfw,
            post.had_targets,
            post.source_sort,
            post.last_seen_at,
        ),
    )


def upsert_targets(conn: sqlite3.Connection, targets: List[RedditTarget]) -> None:
    """
    Insert targets, silently ignoring rows whose dedupe_key already exists.

    id is omitted — AUTOINCREMENT assigns it.
    """
    conn.executemany(
        """
        INSERT OR IGNORE INTO reddit_targets
            (post_id, target_raw, target_normalized, host, protocol,
             notes, parse_confidence, created_at, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                t.post_id,
                t.target_raw,
                t.target_normalized,
                t.host,
                t.protocol,
                t.notes,
                t.parse_confidence,
                t.created_at,
                t.dedupe_key,
            )
            for t in targets
        ],
    )


def get_ingest_state(
    conn: sqlite3.Connection,
    subreddit: str,
    sort_mode: str,
) -> Optional[RedditIngestState]:
    """Return ingest state for (subreddit, sort_mode), or None if not found."""
    row = conn.execute(
        """
        SELECT subreddit, sort_mode, last_post_created_utc, last_post_id, last_scrape_time
        FROM reddit_ingest_state
        WHERE subreddit = ? AND sort_mode = ?
        """,
        (subreddit, sort_mode),
    ).fetchone()
    if row is None:
        return None
    return RedditIngestState(
        subreddit=row["subreddit"],
        sort_mode=row["sort_mode"],
        last_post_created_utc=row["last_post_created_utc"],
        last_post_id=row["last_post_id"],
        last_scrape_time=row["last_scrape_time"],
    )


def save_ingest_state(conn: sqlite3.Connection, state: RedditIngestState) -> None:
    """Upsert ingest cursor state for (subreddit, sort_mode)."""
    conn.execute(
        """
        INSERT INTO reddit_ingest_state
            (subreddit, sort_mode, last_post_created_utc, last_post_id, last_scrape_time)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(subreddit, sort_mode) DO UPDATE SET
            last_post_created_utc = excluded.last_post_created_utc,
            last_post_id          = excluded.last_post_id,
            last_scrape_time      = excluded.last_scrape_time
        """,
        (
            state.subreddit,
            state.sort_mode,
            state.last_post_created_utc,
            state.last_post_id,
            state.last_scrape_time,
        ),
    )


# ---------------------------------------------------------------------------
# Full wipe
# ---------------------------------------------------------------------------

def wipe_all(path: Optional[Path] = None) -> None:
    """
    Delete all rows from all three sidecar tables in a single transaction.

    Preserves table schema. Safe to call on a DB that has never been initialized
    (init_db is called first to ensure tables exist).

    FK enforcement is kept ON; targets are deleted before posts (FK-safe order).
    """
    resolved = get_db_path(path)
    init_db(resolved)  # ensure tables exist even on first-run replace_cache
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM reddit_targets")
        conn.execute("DELETE FROM reddit_posts")
        conn.execute("DELETE FROM reddit_ingest_state")
        conn.commit()
