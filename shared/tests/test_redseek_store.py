"""
Unit tests for redseek/store.py — sidecar DB init, schema guard, CRUD, wipe.

All tests use tmp_path fixture for isolation. No conftest.py.
Transaction ownership: upsert_*/save_*/get_* do not commit; tests commit or
rollback explicitly.
"""

import sqlite3
import datetime

import pytest

from experimental.redseek.models import RedditIngestState, RedditPost, RedditTarget
from experimental.redseek.store import (
    _check_schema,
    get_ingest_state,
    init_db,
    open_connection,
    save_ingest_state,
    upsert_post,
    upsert_targets,
    wipe_all,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

REQUIRED_TABLES = {"reddit_posts", "reddit_targets", "reddit_ingest_state"}


def _tables(db_path) -> set:
    conn = sqlite3.connect(str(db_path))
    result = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    conn.close()
    return result


def _make_post(post_id="p1", **kwargs) -> RedditPost:
    defaults = dict(
        post_title="Test Post",
        post_author="alice",
        post_created_utc=1_700_000_000.0,
        is_nsfw=0,
        had_targets=0,
        source_sort="new",
        last_seen_at=_NOW,
    )
    defaults.update(kwargs)
    return RedditPost(post_id=post_id, **defaults)


def _make_target(post_id="p1", dedupe_key="key1", **kwargs) -> RedditTarget:
    defaults = dict(
        id=None,
        target_raw="http://x.com",
        target_normalized="http://x.com",
        host="x.com",
        protocol="http",
        notes=None,
        parse_confidence="high",
        created_at=_NOW,
    )
    defaults.update(kwargs)
    return RedditTarget(post_id=post_id, dedupe_key=dedupe_key, **defaults)


# ---------------------------------------------------------------------------
# Gate A3 — init_db idempotency
# ---------------------------------------------------------------------------

def test_init_db_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)  # must not raise
    assert REQUIRED_TABLES <= _tables(db)


# ---------------------------------------------------------------------------
# Gate A4 — wipe_all on fresh (never-initialized) DB
# ---------------------------------------------------------------------------

def test_wipe_all_fresh_db(tmp_path):
    db = tmp_path / "test.db"
    assert not db.exists()
    wipe_all(db)  # no prior init_db call
    assert REQUIRED_TABLES <= _tables(db)


# ---------------------------------------------------------------------------
# wipe_all on populated DB clears rows, preserves schema
# ---------------------------------------------------------------------------

def test_wipe_all_clears_rows_preserves_schema(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with open_connection(db) as conn:
        upsert_post(conn, _make_post())
        upsert_targets(conn, [_make_target()])
        save_ingest_state(conn, RedditIngestState("opendirectories", "new", 1.0, "p1", _NOW))
        conn.commit()

    wipe_all(db)

    raw = sqlite3.connect(str(db))
    raw.row_factory = sqlite3.Row
    assert raw.execute("SELECT COUNT(*) FROM reddit_posts").fetchone()[0] == 0
    assert raw.execute("SELECT COUNT(*) FROM reddit_targets").fetchone()[0] == 0
    assert raw.execute("SELECT COUNT(*) FROM reddit_ingest_state").fetchone()[0] == 0
    raw.close()
    assert REQUIRED_TABLES <= _tables(db)


# ---------------------------------------------------------------------------
# Gate A5 — upsert_post does not cascade-delete child targets
# ---------------------------------------------------------------------------

def test_upsert_post_no_cascade_delete(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with open_connection(db) as conn:
        upsert_post(conn, _make_post())
        upsert_targets(conn, [_make_target()])
        # Re-upsert same post (simulates re-ingestion)
        upsert_post(conn, _make_post())
        count = conn.execute(
            "SELECT COUNT(*) FROM reddit_targets WHERE post_id=?", ("p1",)
        ).fetchone()[0]
        conn.commit()
    assert count == 1, f"expected 1 target, got {count}"


# ---------------------------------------------------------------------------
# Gate A6 — upsert_post field policy: immutable vs mutable
# ---------------------------------------------------------------------------

def test_upsert_post_immutable_fields(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with open_connection(db) as conn:
        upsert_post(conn, _make_post(post_title="Original", post_author="alice", source_sort="new"))
        upsert_post(conn, _make_post(post_title="Changed", post_author="bob", source_sort="top"))
        row = conn.execute("SELECT * FROM reddit_posts WHERE post_id=?", ("p1",)).fetchone()
        conn.commit()
    assert row["post_title"] == "Original"
    assert row["post_author"] == "alice"
    assert row["source_sort"] == "new"


def test_upsert_post_mutable_fields(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with open_connection(db) as conn:
        upsert_post(conn, _make_post(is_nsfw=0, had_targets=0))
        upsert_post(conn, _make_post(is_nsfw=1, had_targets=1))
        row = conn.execute("SELECT * FROM reddit_posts WHERE post_id=?", ("p1",)).fetchone()
        conn.commit()
    assert row["is_nsfw"] == 1
    assert row["had_targets"] == 1


# ---------------------------------------------------------------------------
# _check_schema — raises on missing column
# ---------------------------------------------------------------------------

def test_check_schema_raises_on_missing_column(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    # reddit_posts with a missing column (last_seen_at omitted)
    conn.execute(
        """
        CREATE TABLE reddit_posts (
            post_id TEXT PRIMARY KEY,
            post_title TEXT NOT NULL,
            post_author TEXT,
            post_created_utc REAL NOT NULL,
            is_nsfw INTEGER NOT NULL DEFAULT 0,
            had_targets INTEGER NOT NULL DEFAULT 0,
            source_sort TEXT NOT NULL
            -- last_seen_at intentionally omitted
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_normalized TEXT NOT NULL,
            host TEXT,
            protocol TEXT,
            notes TEXT,
            parse_confidence TEXT,
            created_at TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_ingest_state (
            subreddit TEXT NOT NULL,
            sort_mode TEXT NOT NULL,
            last_post_created_utc REAL,
            last_post_id TEXT,
            last_scrape_time TEXT,
            PRIMARY KEY (subreddit, sort_mode)
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="missing columns"):
        _check_schema(conn)
    conn.close()


# ---------------------------------------------------------------------------
# _check_schema — raises on missing UNIQUE constraint on dedupe_key
# ---------------------------------------------------------------------------

def test_check_schema_raises_on_missing_unique(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE reddit_posts (
            post_id TEXT PRIMARY KEY,
            post_title TEXT NOT NULL,
            post_author TEXT,
            post_created_utc REAL NOT NULL,
            is_nsfw INTEGER NOT NULL DEFAULT 0,
            had_targets INTEGER NOT NULL DEFAULT 0,
            source_sort TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    # dedupe_key NOT UNIQUE
    conn.execute(
        """
        CREATE TABLE reddit_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_normalized TEXT NOT NULL,
            host TEXT,
            protocol TEXT,
            notes TEXT,
            parse_confidence TEXT,
            created_at TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_ingest_state (
            subreddit TEXT NOT NULL,
            sort_mode TEXT NOT NULL,
            last_post_created_utc REAL,
            last_post_id TEXT,
            last_scrape_time TEXT,
            PRIMARY KEY (subreddit, sort_mode)
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="UNIQUE constraint on dedupe_key"):
        _check_schema(conn)
    conn.close()


# ---------------------------------------------------------------------------
# _check_schema — raises on single-col PK (instead of composite)
# ---------------------------------------------------------------------------

def test_check_schema_raises_on_missing_pk(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE reddit_posts (
            post_id TEXT PRIMARY KEY,
            post_title TEXT NOT NULL,
            post_author TEXT,
            post_created_utc REAL NOT NULL,
            is_nsfw INTEGER NOT NULL DEFAULT 0,
            had_targets INTEGER NOT NULL DEFAULT 0,
            source_sort TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_normalized TEXT NOT NULL,
            host TEXT,
            protocol TEXT,
            notes TEXT,
            parse_confidence TEXT,
            created_at TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)
        )
        """
    )
    # Single-col PK on subreddit only
    conn.execute(
        """
        CREATE TABLE reddit_ingest_state (
            subreddit TEXT PRIMARY KEY,
            sort_mode TEXT NOT NULL,
            last_post_created_utc REAL,
            last_post_id TEXT,
            last_scrape_time TEXT
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="PK must be"):
        _check_schema(conn)
    conn.close()


# ---------------------------------------------------------------------------
# _check_schema — raises on reversed PK order (sort_mode, subreddit)
# ---------------------------------------------------------------------------

def test_check_schema_raises_on_reversed_pk(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE reddit_posts (
            post_id TEXT PRIMARY KEY,
            post_title TEXT NOT NULL,
            post_author TEXT,
            post_created_utc REAL NOT NULL,
            is_nsfw INTEGER NOT NULL DEFAULT 0,
            had_targets INTEGER NOT NULL DEFAULT 0,
            source_sort TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_normalized TEXT NOT NULL,
            host TEXT,
            protocol TEXT,
            notes TEXT,
            parse_confidence TEXT,
            created_at TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)
        )
        """
    )
    # PK declared in wrong order: (sort_mode, subreddit)
    conn.execute(
        """
        CREATE TABLE reddit_ingest_state (
            subreddit TEXT NOT NULL,
            sort_mode TEXT NOT NULL,
            last_post_created_utc REAL,
            last_post_id TEXT,
            last_scrape_time TEXT,
            PRIMARY KEY (sort_mode, subreddit)
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="PK must be"):
        _check_schema(conn)
    conn.close()


# ---------------------------------------------------------------------------
# _check_schema — raises on missing FK
# ---------------------------------------------------------------------------

def test_check_schema_raises_on_missing_fk(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE reddit_posts (
            post_id TEXT PRIMARY KEY,
            post_title TEXT NOT NULL,
            post_author TEXT,
            post_created_utc REAL NOT NULL,
            is_nsfw INTEGER NOT NULL DEFAULT 0,
            had_targets INTEGER NOT NULL DEFAULT 0,
            source_sort TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    # reddit_targets without FK on post_id
    conn.execute(
        """
        CREATE TABLE reddit_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_normalized TEXT NOT NULL,
            host TEXT,
            protocol TEXT,
            notes TEXT,
            parse_confidence TEXT,
            created_at TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE reddit_ingest_state (
            subreddit TEXT NOT NULL,
            sort_mode TEXT NOT NULL,
            last_post_created_utc REAL,
            last_post_id TEXT,
            last_scrape_time TEXT,
            PRIMARY KEY (subreddit, sort_mode)
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="missing FK"):
        _check_schema(conn)
    conn.close()
