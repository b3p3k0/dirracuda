"""
Unit tests for experimental.censys_discovery.store.

All tests use tmp_path for DB injection — no writes to ~/.dirracuda.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.censys_discovery.models import (
    CensysRunOptions,
    RUN_STATUS_DONE,
    RUN_STATUS_RUNNING,
    SearchResultItem,
)
from experimental.censys_discovery.store import (
    build_dedupe_key,
    get_db_path,
    init_db,
    insert_result,
    insert_run,
    list_results,
    open_connection,
    update_run,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_censys.db"
    init_db(p)
    return p


def _options(**kwargs) -> CensysRunOptions:
    defaults = dict(pat="testpat", protocol="FTP", max_pages=3, page_size=50)
    defaults.update(kwargs)
    return CensysRunOptions(**defaults)


def _item(
    ip: str = "1.2.3.4",
    port: int = 21,
    protocol: str = "FTP",
    transport: str = "TCP",
    n: int = 0,
) -> SearchResultItem:
    return SearchResultItem(
        ip_address=ip if n == 0 else f"{ip.rsplit('.', 1)[0]}.{n}",
        port=port,
        protocol=protocol,
        transport_protocol=transport,
        banner=f"220 FTP server {n}",
        scan_time="2026-05-14T00:00:00",
        source_json='{"port": 21}',
    )


# ---------------------------------------------------------------------------
# get_db_path
# ---------------------------------------------------------------------------


def test_get_db_path_returns_override(tmp_path: Path) -> None:
    override = tmp_path / "custom.db"
    assert get_db_path(override) == override


def test_get_db_path_default_points_to_experimental_dir() -> None:
    result = get_db_path(None)
    assert result.name == "censys_discovery.db"
    assert ".dirracuda" in str(result)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_both_tables(tmp_path: Path) -> None:
    p = tmp_path / "init_test.db"
    init_db(p)
    with sqlite3.connect(str(p)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "censys_runs" in tables
    assert "censys_results" in tables


def test_init_db_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "idempotent.db"
    init_db(p)
    init_db(p)  # must not raise


def test_init_db_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "deep" / "censys_discovery.db"
    init_db(p)
    assert p.exists()


# ---------------------------------------------------------------------------
# Schema guard — negative cases
# ---------------------------------------------------------------------------

_RUNS_DDL_BARE = """
CREATE TABLE censys_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    protocol      TEXT NOT NULL,
    query_text    TEXT NOT NULL,
    query_hours   INTEGER,
    max_pages     INTEGER NOT NULL,
    page_size     INTEGER NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    deduped_count INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    error_message TEXT
)
"""


def _seed_runs_table(conn: sqlite3.Connection) -> None:
    conn.execute(_RUNS_DDL_BARE)
    conn.commit()


def test_open_connection_raises_on_missing_unique_constraint(tmp_path: Path) -> None:
    db = tmp_path / "no_unique.db"
    with sqlite3.connect(str(db)) as conn:
        _seed_runs_table(conn)
        # censys_results WITHOUT the UNIQUE constraint
        conn.execute("""
            CREATE TABLE censys_results (
                result_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id             INTEGER NOT NULL,
                protocol           TEXT NOT NULL,
                ip_address         TEXT NOT NULL,
                port               INTEGER NOT NULL,
                transport_protocol TEXT,
                banner             TEXT,
                scan_time          TEXT,
                source_json        TEXT NOT NULL,
                dedupe_key         TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES censys_runs(run_id)
            )
        """)
        conn.commit()

    with pytest.raises(RuntimeError, match="UNIQUE"):
        open_connection(db)


def test_open_connection_raises_on_missing_fk(tmp_path: Path) -> None:
    db = tmp_path / "no_fk.db"
    with sqlite3.connect(str(db)) as conn:
        _seed_runs_table(conn)
        # censys_results WITHOUT the FOREIGN KEY
        conn.execute("""
            CREATE TABLE censys_results (
                result_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id             INTEGER NOT NULL,
                protocol           TEXT NOT NULL,
                ip_address         TEXT NOT NULL,
                port               INTEGER NOT NULL,
                transport_protocol TEXT,
                banner             TEXT,
                scan_time          TEXT,
                source_json        TEXT NOT NULL,
                dedupe_key         TEXT NOT NULL,
                UNIQUE (run_id, dedupe_key)
            )
        """)
        conn.commit()

    with pytest.raises(RuntimeError, match="(?i)(fk|foreign)"):
        open_connection(db)


def test_open_connection_raises_on_missing_column_in_runs(tmp_path: Path) -> None:
    db = tmp_path / "missing_col.db"
    with sqlite3.connect(str(db)) as conn:
        # censys_runs WITHOUT query_text column
        conn.execute("""
            CREATE TABLE censys_runs (
                run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at    TEXT NOT NULL,
                protocol      TEXT NOT NULL,
                max_pages     INTEGER NOT NULL,
                page_size     INTEGER NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                deduped_count INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE censys_results (
                result_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER NOT NULL,
                protocol   TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                port       INTEGER NOT NULL,
                source_json TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES censys_runs(run_id),
                UNIQUE (run_id, dedupe_key)
            )
        """)
        conn.commit()

    with pytest.raises(RuntimeError, match="missing columns"):
        open_connection(db)


# ---------------------------------------------------------------------------
# insert_run
# ---------------------------------------------------------------------------


def test_insert_run_returns_int(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "host.services:(protocol=FTP and port=21)")
    conn.commit()
    conn.close()
    assert isinstance(run_id, int) and run_id > 0


def test_insert_run_status_is_running(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "host.services:(protocol=FTP and port=21)")
    conn.commit()
    row = conn.execute("SELECT status FROM censys_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    assert row[0] == RUN_STATUS_RUNNING


def test_insert_run_stores_options(db_path: Path) -> None:
    opts = _options(protocol="FTP", max_pages=7, page_size=50, query_hours=48)
    query_text = "host.services:(protocol=FTP and port=21)"
    conn = open_connection(db_path)
    run_id = insert_run(conn, opts, "2026-01-01T00:00:00", query_text)
    conn.commit()
    row = conn.execute(
        "SELECT protocol, query_text, max_pages, page_size, query_hours FROM censys_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    conn.close()
    assert row == ("FTP", query_text, 7, 50, 48)


# ---------------------------------------------------------------------------
# update_run
# ---------------------------------------------------------------------------


def test_update_run_sets_status_and_counts(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    update_run(conn, run_id, "2026-01-01T00:01:00", 10, 8, RUN_STATUS_DONE)
    conn.commit()
    row = conn.execute(
        "SELECT status, fetched_count, deduped_count, finished_at FROM censys_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    conn.close()
    assert row == (RUN_STATUS_DONE, 10, 8, "2026-01-01T00:01:00")


def test_update_run_sets_error_message(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    update_run(conn, run_id, "2026-01-01T00:01:00", 0, 0, "error", "network failure")
    conn.commit()
    row = conn.execute(
        "SELECT status, error_message FROM censys_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    conn.close()
    assert row == ("error", "network failure")


# ---------------------------------------------------------------------------
# build_dedupe_key
# ---------------------------------------------------------------------------


def test_build_dedupe_key_normalizes_protocol_case() -> None:
    a = build_dedupe_key("1.2.3.4", 21, "ftp", "tcp")
    b = build_dedupe_key("1.2.3.4", 21, "FTP", "tcp")
    assert a == b


def test_build_dedupe_key_normalizes_transport_case() -> None:
    a = build_dedupe_key("1.2.3.4", 21, "FTP", "TCP")
    b = build_dedupe_key("1.2.3.4", 21, "FTP", "tcp")
    assert a == b


def test_build_dedupe_key_none_transport_same_as_empty() -> None:
    a = build_dedupe_key("1.2.3.4", 21, "FTP", None)
    b = build_dedupe_key("1.2.3.4", 21, "FTP", "")
    assert a == b


# ---------------------------------------------------------------------------
# insert_result
# ---------------------------------------------------------------------------


def test_insert_result_returns_true_first_insert(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    result = insert_result(conn, run_id, _item())
    conn.commit()
    conn.close()
    assert result is True


def test_insert_result_dedupes_same_run_same_key(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    r1 = insert_result(conn, run_id, _item())
    r2 = insert_result(conn, run_id, _item())  # identical item
    conn.commit()
    conn.close()
    assert r1 is True
    assert r2 is False


def test_insert_result_dedupes_case_insensitive(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    item_upper = _item(protocol="FTP", transport="TCP")
    item_lower = SearchResultItem(
        ip_address="1.2.3.4", port=21, protocol="ftp",
        transport_protocol="tcp", banner=None, scan_time=None,
        source_json='{"port":21}',
    )
    r1 = insert_result(conn, run_id, item_upper)
    r2 = insert_result(conn, run_id, item_lower)
    conn.commit()
    conn.close()
    assert r1 is True
    assert r2 is False


def test_insert_result_allows_same_key_different_run(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id_1 = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    run_id_2 = insert_run(conn, _options(), "2026-01-01T01:00:00", "q")
    conn.commit()
    r1 = insert_result(conn, run_id_1, _item())
    r2 = insert_result(conn, run_id_2, _item())
    conn.commit()
    conn.close()
    assert r1 is True
    assert r2 is True


def test_insert_result_stores_all_fields(db_path: Path) -> None:
    item = SearchResultItem(
        ip_address="10.0.0.1",
        port=21,
        protocol="FTP",
        transport_protocol="TCP",
        banner="220 Welcome",
        scan_time="2026-05-14T12:00:00",
        source_json='{"port":21,"banner":"220 Welcome"}',
    )
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    insert_result(conn, run_id, item)
    conn.commit()
    row = conn.execute(
        """
        SELECT ip_address, port, protocol, transport_protocol,
               banner, scan_time, source_json
          FROM censys_results WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    conn.close()
    assert row[0] == "10.0.0.1"
    assert row[1] == 21
    assert row[2] == "FTP"
    assert row[3] == "TCP"
    assert row[4] == "220 Welcome"
    assert row[5] == "2026-05-14T12:00:00"
    assert '"banner"' in row[6]


# ---------------------------------------------------------------------------
# list_results
# ---------------------------------------------------------------------------


def test_list_results_empty_db_returns_empty_list(db_path: Path) -> None:
    conn = open_connection(db_path)
    rows = list_results(conn)
    conn.close()
    assert rows == []


def test_list_results_returns_list_of_dicts_with_expected_keys(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    insert_result(conn, run_id, _item())
    conn.commit()
    rows = list_results(conn)
    conn.close()
    assert len(rows) == 1
    expected_keys = {
        "result_id", "run_id", "protocol", "ip_address", "port",
        "transport_protocol", "banner", "scan_time", "source_json",
    }
    assert expected_keys.issubset(rows[0].keys())


def test_list_results_multi_run_ordering(db_path: Path) -> None:
    conn = open_connection(db_path)
    run_id_1 = insert_run(conn, _options(), "2026-01-01T00:00:00", "q")
    conn.commit()
    run_id_2 = insert_run(conn, _options(), "2026-01-01T01:00:00", "q")
    conn.commit()
    # Use n=0 for all so _item() doesn't transform the IP; pass distinct IPs directly
    insert_result(conn, run_id_1, _item(ip="10.0.0.1", n=0))
    insert_result(conn, run_id_1, _item(ip="10.0.0.2", n=0))
    insert_result(conn, run_id_2, _item(ip="10.0.1.1", n=0))
    conn.commit()
    rows = list_results(conn)
    conn.close()
    assert len(rows) == 3
    # Newer run_id (run_id_2) rows appear before older (run_id_1)
    assert rows[0]["run_id"] == run_id_2
    assert rows[1]["run_id"] == run_id_1
    assert rows[2]["run_id"] == run_id_1
    # Within the same run, lower result_id first
    assert rows[1]["result_id"] < rows[2]["result_id"]
