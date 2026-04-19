"""
Unit tests for experimental.se_dork.store.

All tests use tmp_path for DB injection — no writes to ~/.dirracuda.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.se_dork.models import RunOptions, RUN_STATUS_RUNNING, RUN_STATUS_DONE
from experimental.se_dork.store import (
    count_open_index_results,
    delete_non_open_results,
    get_all_results,
    get_db_path,
    get_results_for_run,
    init_db,
    insert_result,
    insert_run,
    normalize_url,
    open_connection,
    update_result_probe,
    update_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_se_dork.db"
    init_db(p)
    return p


def _minimal_options() -> RunOptions:
    return RunOptions(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results=50,
    )


# ---------------------------------------------------------------------------
# get_db_path
# ---------------------------------------------------------------------------


def test_get_db_path_returns_override(tmp_path: Path) -> None:
    override = tmp_path / "custom.db"
    assert get_db_path(override) == override


def test_get_db_path_returns_default_when_none() -> None:
    result = get_db_path(None)
    assert result.name == "se_dork.db"
    assert ".dirracuda" in str(result)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_tables(tmp_path: Path) -> None:
    p = tmp_path / "init_test.db"
    init_db(p)
    with sqlite3.connect(str(p)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dork_runs" in tables
    assert "dork_results" in tables


def test_init_db_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "idempotent.db"
    init_db(p)
    init_db(p)  # must not raise


def test_init_db_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "deep" / "se_dork.db"
    init_db(p)
    assert p.exists()


def test_init_db_backfills_probe_columns_on_existing_schema(tmp_path: Path) -> None:
    """Older DBs without probe columns are migrated in-place by init_db()."""
    p = tmp_path / "migrate_probe_columns.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute(
            """
            CREATE TABLE dork_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                instance_url TEXT NOT NULL,
                query TEXT NOT NULL,
                max_results INTEGER NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                deduped_count INTEGER NOT NULL DEFAULT 0,
                verified_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dork_results (
                result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                url_normalized TEXT NOT NULL,
                title TEXT,
                snippet TEXT,
                source_engine TEXT,
                source_engines_json TEXT,
                verdict TEXT,
                reason_code TEXT,
                http_status INTEGER,
                checked_at TEXT,
                FOREIGN KEY (run_id) REFERENCES dork_runs(run_id),
                UNIQUE (run_id, url_normalized)
            )
            """
        )
        conn.commit()

    init_db(p)

    with sqlite3.connect(str(p)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(dork_results)")}

    assert "probe_status" in cols
    assert "probe_indicator_matches" in cols
    assert "probe_preview" in cols
    assert "probe_checked_at" in cols
    assert "probe_error" in cols


# ---------------------------------------------------------------------------
# normalize_url — locked policy: lowercase, strip trailing slash, drop query + fragment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("http://Example.COM/files/", "http://example.com/files"),
    ("HTTP://EXAMPLE.COM/path/", "http://example.com/path"),
    ("http://example.com/files/?sort=asc", "http://example.com/files"),
    ("http://example.com/files/#section", "http://example.com/files"),
    ("http://example.com/files/?q=test#top", "http://example.com/files"),
    ("http://example.com/files", "http://example.com/files"),
    ("http://example.com/", "http://example.com"),
])
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_normalize_url_same_url_different_trailing_slash() -> None:
    a = normalize_url("http://example.com/files/")
    b = normalize_url("http://example.com/files")
    assert a == b


# ---------------------------------------------------------------------------
# insert_run
# ---------------------------------------------------------------------------


def test_insert_run_returns_integer(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
    assert isinstance(run_id, int)
    assert run_id > 0


def test_insert_run_status_is_running(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        row = conn.execute("SELECT status FROM dork_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row[0] == RUN_STATUS_RUNNING


def test_insert_run_stores_options(db_path: Path) -> None:
    opts = RunOptions(instance_url="http://test:9000", query="test query", max_results=25)
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, opts, "2026-01-01T00:00:00")
        conn.commit()
        row = conn.execute(
            "SELECT instance_url, query, max_results FROM dork_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row == ("http://test:9000", "test query", 25)


# ---------------------------------------------------------------------------
# update_run
# ---------------------------------------------------------------------------


def test_update_run_sets_status_and_counts(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        update_run(conn, run_id, "2026-01-01T00:01:00", 10, 8, RUN_STATUS_DONE)
        conn.commit()
        row = conn.execute(
            "SELECT status, fetched_count, deduped_count, finished_at FROM dork_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row == (RUN_STATUS_DONE, 10, 8, "2026-01-01T00:01:00")


def test_update_run_sets_error_message(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        update_run(conn, run_id, "2026-01-01T00:01:00", 0, 0, "error", "network failure")
        conn.commit()
        row = conn.execute(
            "SELECT status, error_message FROM dork_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    assert row == ("error", "network failure")


# ---------------------------------------------------------------------------
# insert_result + dedupe
# ---------------------------------------------------------------------------


def test_insert_result_returns_true_on_first_insert(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        result = insert_result(conn, run_id, {"url": "http://example.com/files/"})
        conn.commit()
    assert result is True


def test_insert_result_dedupes_by_normalized_url(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        r1 = insert_result(conn, run_id, {"url": "http://example.com/files/"})
        # Same URL with trailing slash stripped — same normalized form
        r2 = insert_result(conn, run_id, {"url": "http://example.com/files"})
        conn.commit()
    assert r1 is True
    assert r2 is False


def test_insert_result_dedupes_query_string(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        r1 = insert_result(conn, run_id, {"url": "http://example.com/files/"})
        r2 = insert_result(conn, run_id, {"url": "http://example.com/files/?sort=asc"})
        conn.commit()
    assert r1 is True
    assert r2 is False


def test_dedupe_allows_same_url_in_different_run(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id_1 = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        run_id_2 = insert_run(conn, _minimal_options(), "2026-01-01T01:00:00")
        conn.commit()
        r1 = insert_result(conn, run_id_1, {"url": "http://example.com/files/"})
        r2 = insert_result(conn, run_id_2, {"url": "http://example.com/files/"})
        conn.commit()
    assert r1 is True
    assert r2 is True


def test_insert_result_stores_metadata(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        insert_result(conn, run_id, {
            "url": "http://example.com/files/",
            "title": "Index of /files",
            "content": "Parent directory listing",
            "engine": "bing",
            "engines": ["bing", "google"],
        })
        conn.commit()
        row = conn.execute(
            "SELECT title, snippet, source_engine, source_engines_json FROM dork_results WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row[0] == "Index of /files"
    assert row[1] == "Parent directory listing"
    assert row[2] == "bing"
    assert row[3] == '["bing", "google"]'


def test_get_results_for_run_returns_result_id_and_url(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        insert_result(conn, run_id, {"url": "http://example.com/open/"})
        conn.commit()
        rows = get_results_for_run(conn, run_id)

    assert len(rows) == 1
    assert isinstance(rows[0]["result_id"], int)
    assert rows[0]["url"] == "http://example.com/open/"


def test_update_result_probe_persists_probe_fields(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        insert_result(conn, run_id, {"url": "http://example.com/open/"})
        conn.commit()
        result_id = conn.execute(
            "SELECT result_id FROM dork_results WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]

        update_result_probe(
            conn,
            result_id=result_id,
            probe_status="issue",
            probe_indicator_matches=2,
            probe_preview="notes,[[loose files]]",
            probe_checked_at="2026-01-01T00:05:00",
            probe_error=None,
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT probe_status, probe_indicator_matches, probe_preview,
                   probe_checked_at, probe_error
              FROM dork_results
             WHERE result_id=?
            """,
            (result_id,),
        ).fetchone()

    assert row == ("issue", 2, "notes,[[loose files]]", "2026-01-01T00:05:00", None)


def test_get_all_results_includes_probe_fields(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        insert_result(conn, run_id, {"url": "http://example.com/open/"})
        conn.commit()
        result_id = conn.execute("SELECT result_id FROM dork_results").fetchone()[0]
        update_result_probe(
            conn,
            result_id=result_id,
            probe_status="clean",
            probe_indicator_matches=0,
            probe_preview="pub,movies",
            probe_checked_at="2026-01-01T00:04:00",
            probe_error=None,
        )
        conn.commit()

        rows = get_all_results(conn)

    assert len(rows) == 1
    row = rows[0]
    assert row["probe_status"] == "clean"
    assert row["probe_indicator_matches"] == 0
    assert row["probe_preview"] == "pub,movies"
    assert row["probe_checked_at"] == "2026-01-01T00:04:00"


# ---------------------------------------------------------------------------
# OPEN_INDEX-only retention helpers
# ---------------------------------------------------------------------------


def test_delete_non_open_results_run_scoped(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()
        insert_result(conn, run_id, {"url": "http://example.com/open/"})
        insert_result(conn, run_id, {"url": "http://example.com/maybe/"})
        insert_result(conn, run_id, {"url": "http://example.com/error/"})
        conn.execute(
            "UPDATE dork_results SET verdict='OPEN_INDEX' WHERE url LIKE '%/open/%'"
        )
        conn.execute(
            "UPDATE dork_results SET verdict='MAYBE' WHERE url LIKE '%/maybe/%'"
        )
        conn.execute(
            "UPDATE dork_results SET verdict='ERROR' WHERE url LIKE '%/error/%'"
        )
        conn.commit()

        deleted = delete_non_open_results(conn, run_id=run_id)
        conn.commit()

        remaining = conn.execute(
            "SELECT verdict FROM dork_results WHERE run_id=?", (run_id,)
        ).fetchall()

    assert deleted == 2
    assert remaining == [("OPEN_INDEX",)]


def test_delete_non_open_results_global(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_1 = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        run_2 = insert_run(conn, _minimal_options(), "2026-01-01T01:00:00")
        conn.commit()

        insert_result(conn, run_1, {"url": "http://example.com/r1/open/"})
        insert_result(conn, run_1, {"url": "http://example.com/r1/noise/"})
        insert_result(conn, run_2, {"url": "http://example.com/r2/open/"})
        insert_result(conn, run_2, {"url": "http://example.com/r2/maybe/"})
        conn.execute(
            "UPDATE dork_results SET verdict='OPEN_INDEX' WHERE url LIKE '%/open/%'"
        )
        conn.execute(
            "UPDATE dork_results SET verdict='NOISE' WHERE url LIKE '%/noise/%'"
        )
        conn.execute(
            "UPDATE dork_results SET verdict='MAYBE' WHERE url LIKE '%/maybe/%'"
        )
        conn.commit()

        deleted = delete_non_open_results(conn, run_id=None)
        conn.commit()

        remaining = conn.execute("SELECT verdict FROM dork_results").fetchall()

    assert deleted == 2
    assert len(remaining) == 2
    assert all(row[0] == "OPEN_INDEX" for row in remaining)


def test_count_open_index_results_per_run(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, _minimal_options(), "2026-01-01T00:00:00")
        conn.commit()

        insert_result(conn, run_id, {"url": "http://example.com/open/"})
        insert_result(conn, run_id, {"url": "http://example.com/noise/"})
        conn.execute(
            "UPDATE dork_results SET verdict='OPEN_INDEX' WHERE url LIKE '%/open/%'"
        )
        conn.execute(
            "UPDATE dork_results SET verdict='NOISE' WHERE url LIKE '%/noise/%'"
        )
        conn.commit()

        count_before = count_open_index_results(conn, run_id)
        delete_non_open_results(conn, run_id=run_id)
        conn.commit()
        count_after = count_open_index_results(conn, run_id)

    assert count_before == 1
    assert count_after == 1


# ---------------------------------------------------------------------------
# Schema-drift negative tests (C4)
# ---------------------------------------------------------------------------

_DORK_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS dork_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_url  TEXT    NOT NULL,
    query         TEXT    NOT NULL,
    max_results   INTEGER NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'running',
    fetched_count INTEGER NOT NULL DEFAULT 0,
    deduped_count INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
)
"""


def _create_runs_table(conn):
    conn.execute(_DORK_RUNS_DDL)
    conn.commit()


def test_check_schema_raises_on_missing_unique_constraint(tmp_path: Path) -> None:
    """open_connection raises RuntimeError when dork_results lacks UNIQUE(run_id, url_normalized)."""
    db_path = tmp_path / "bad_schema.db"
    with sqlite3.connect(str(db_path)) as conn:
        _create_runs_table(conn)
        # Create dork_results WITHOUT the UNIQUE constraint
        conn.execute("""
            CREATE TABLE dork_results (
                result_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL,
                url               TEXT    NOT NULL,
                url_normalized    TEXT    NOT NULL,
                title             TEXT,
                snippet           TEXT,
                source_engine     TEXT,
                source_engines_json TEXT,
                verdict           TEXT,
                reason_code       TEXT,
                http_status       INTEGER,
                checked_at        TEXT,
                probe_status      TEXT NOT NULL DEFAULT 'unprobed',
                probe_indicator_matches INTEGER NOT NULL DEFAULT 0,
                probe_preview     TEXT,
                probe_checked_at  TEXT,
                probe_error       TEXT,
                FOREIGN KEY (run_id) REFERENCES dork_runs(run_id)
            )
        """)
        conn.commit()

    with pytest.raises(RuntimeError, match="UNIQUE"):
        open_connection(db_path)


def test_check_schema_raises_on_missing_fk(tmp_path: Path) -> None:
    """open_connection raises RuntimeError when dork_results lacks FK run_id → dork_runs(run_id)."""
    db_path = tmp_path / "no_fk.db"
    with sqlite3.connect(str(db_path)) as conn:
        _create_runs_table(conn)
        # Create dork_results WITHOUT the FOREIGN KEY
        conn.execute("""
            CREATE TABLE dork_results (
                result_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL,
                url               TEXT    NOT NULL,
                url_normalized    TEXT    NOT NULL,
                title             TEXT,
                snippet           TEXT,
                source_engine     TEXT,
                source_engines_json TEXT,
                verdict           TEXT,
                reason_code       TEXT,
                http_status       INTEGER,
                checked_at        TEXT,
                probe_status      TEXT NOT NULL DEFAULT 'unprobed',
                probe_indicator_matches INTEGER NOT NULL DEFAULT 0,
                probe_preview     TEXT,
                probe_checked_at  TEXT,
                probe_error       TEXT,
                UNIQUE (run_id, url_normalized)
            )
        """)
        conn.commit()

    with pytest.raises(RuntimeError, match="(?i)(fk|foreign)"):
        open_connection(db_path)
