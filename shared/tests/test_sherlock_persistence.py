"""
Tests for Sherlock C2 persistence: sherlock_results / sherlock_hits.

Covers:
- Migration creates both tables on minimal and current schemas; idempotent.
- Store latest summary + capped hits; latest-result replacement per host/protocol.
- Capped details preserve the full total_hit_count.
- Stale detection via persisted snapshot_id vs probe-cache latest_snapshot_id.
- Missing tables / unscanned hosts degrade to None.
- Partial Sherlock tables (missing key column, missing sherlock_hits.result_id)
  self-heal via migration backfill.
- Child-aware dedupe leaves no orphan hits and a correctly-shaped unique index,
  with foreign keys OFF.
- Wrong-shaped same-name index is dropped and rebuilt.
- Missing probe-cache latest_snapshot_id column -> stale.
- clear_cache() runs after a successful store.
- SMB / FTP / HTTP parity.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.db_migrations import run_migrations
from shared.sherlock import MatchResult, Severity, SherlockHit
from gui.utils.database_access import DatabaseReader
from gui.utils.database_access_sherlock_methods import (
    SHERLOCK_HIT_DETAIL_CAP,
    _unique_index_covers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A pre-v2 minimal baseline: only smb_servers. run_migrations must build the
# rest (ftp/http servers, probe caches, Sherlock tables) on top.
_MINIMAL_DDL = """
CREATE TABLE IF NOT EXISTS smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_PROTOCOLS = ("S", "F", "H")
_CACHE_TABLE = {"S": "host_probe_cache", "F": "ftp_probe_cache", "H": "http_probe_cache"}


def _make_match_result(n_hits: int, total: int | None = None) -> MatchResult:
    """Build a MatchResult with n_hits HIGH hits; total defaults to n_hits."""
    hits = [
        SherlockHit(
            severity=Severity.HIGH,
            category="Credentials",
            label="Password files",
            pattern="*password*",
            display_path=f"share/dir/secret_{i}_password.txt",
        )
        for i in range(n_hits)
    ]
    return MatchResult(
        hits=hits,
        highest_severity=Severity.HIGH if hits else None,
        hit_count=total if total is not None else n_hits,
    )


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _migrated_db(tmp_path) -> str:
    """Empty DB taken through run_migrations (the 'current' schema)."""
    db = str(tmp_path / "current.db")
    run_migrations(db)
    return db


def _insert_server(db_path: str, host_type: str, ip: str, port: int = 80) -> int:
    """Insert a server row for the protocol and return its id."""
    conn = _conn(db_path)
    try:
        if host_type == "S":
            conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", (ip,))
            table = "smb_servers"
            where, params = "ip_address=?", (ip,)
        elif host_type == "F":
            conn.execute("INSERT INTO ftp_servers (ip_address) VALUES (?)", (ip,))
            table = "ftp_servers"
            where, params = "ip_address=?", (ip,)
        else:
            conn.execute("INSERT INTO http_servers (ip_address, port) VALUES (?, ?)", (ip, port))
            table = "http_servers"
            where, params = "ip_address=? AND port=?", (ip, port)
        conn.commit()
        row = conn.execute(f"SELECT id FROM {table} WHERE {where}", params).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def _set_latest_snapshot(db_path: str, host_type: str, server_id: int, snapshot_id) -> None:
    cache = _CACHE_TABLE[host_type]
    conn = _conn(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO {cache} (server_id, latest_snapshot_id) VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET latest_snapshot_id=excluded.latest_snapshot_id
            """,
            (server_id, snapshot_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Migration on minimal + current schema
# ---------------------------------------------------------------------------

def _sherlock_columns(db_path: str, table: str) -> set:
    conn = _conn(db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_migration_creates_tables_on_minimal_schema(tmp_path):
    db = str(tmp_path / "minimal.db")
    conn = sqlite3.connect(db)
    conn.executescript(_MINIMAL_DDL)
    conn.commit()
    conn.close()

    run_migrations(db)

    results_cols = _sherlock_columns(db, "sherlock_results")
    hits_cols = _sherlock_columns(db, "sherlock_hits")
    assert {"host_type", "protocol_server_id", "snapshot_id", "total_hit_count",
            "detail_count", "truncated"}.issubset(results_cols)
    assert {"result_id", "severity", "category", "label", "pattern",
            "display_path"}.issubset(hits_cols)


def test_migration_idempotent(tmp_path):
    db = _migrated_db(tmp_path)
    before = _sherlock_columns(db, "sherlock_results")
    run_migrations(db)  # second run
    after = _sherlock_columns(db, "sherlock_results")
    assert before == after
    conn = _conn(db)
    try:
        assert _unique_index_covers(conn.cursor(), "sherlock_results",
                                    ("host_type", "protocol_server_id"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2 + 10. Store, latest-replacement, same-key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host_type", _PROTOCOLS)
def test_store_and_latest_replacement(tmp_path, host_type):
    db = _migrated_db(tmp_path)
    ip = "10.0.0.1"
    server_id = _insert_server(db, host_type, ip)
    reader = DatabaseReader(db_path=db)

    kwargs = {"protocol_server_id": server_id}
    if host_type == "H":
        kwargs["port"] = 80

    rid1 = reader.store_sherlock_result(ip, host_type, 5, _make_match_result(3), **kwargs)
    assert rid1 is not None

    # Re-store with a new snapshot -> same row replaced, not duplicated.
    rid2 = reader.store_sherlock_result(ip, host_type, 6, _make_match_result(1), **kwargs)
    assert rid2 == rid1

    conn = _conn(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sherlock_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sherlock_hits").fetchone()[0] == 1
    finally:
        conn.close()

    result = reader.get_sherlock_result_for_host(ip, host_type, **kwargs)
    assert result["snapshot_id"] == 6
    assert result["total_hit_count"] == 1
    assert len(result["hits"]) == 1
    assert result["highest_severity"] == "high"


# ---------------------------------------------------------------------------
# 3. Capped details preserve total
# ---------------------------------------------------------------------------

def test_capped_details_preserve_total(tmp_path):
    db = _migrated_db(tmp_path)
    ip = "10.0.0.2"
    sid = _insert_server(db, "S", ip)
    reader = DatabaseReader(db_path=db)

    over = SHERLOCK_HIT_DETAIL_CAP + 17
    rid = reader.store_sherlock_result(ip, "S", 9, _make_match_result(over),
                                       protocol_server_id=sid)
    assert rid is not None

    conn = _conn(db)
    try:
        stored = conn.execute(
            "SELECT total_hit_count, detail_count, truncated FROM sherlock_results"
        ).fetchone()
        hit_rows = conn.execute("SELECT COUNT(*) FROM sherlock_hits").fetchone()[0]
    finally:
        conn.close()

    assert stored["total_hit_count"] == over
    assert stored["detail_count"] == SHERLOCK_HIT_DETAIL_CAP
    assert stored["truncated"] == 1
    assert hit_rows == SHERLOCK_HIT_DETAIL_CAP


# ---------------------------------------------------------------------------
# 4 + 9. Stale detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host_type", _PROTOCOLS)
def test_stale_detection(tmp_path, host_type):
    db = _migrated_db(tmp_path)
    ip = "10.0.0.3"
    sid = _insert_server(db, host_type, ip)
    reader = DatabaseReader(db_path=db)
    kwargs = {"protocol_server_id": sid}
    if host_type == "H":
        kwargs["port"] = 80

    reader.store_sherlock_result(ip, host_type, 5, _make_match_result(2), **kwargs)

    # Current snapshot matches -> not stale.
    _set_latest_snapshot(db, host_type, sid, 5)
    assert reader.get_sherlock_result_for_host(ip, host_type, **kwargs)["stale"] is False

    # Probe advanced -> stale.
    _set_latest_snapshot(db, host_type, sid, 6)
    assert reader.get_sherlock_result_for_host(ip, host_type, **kwargs)["stale"] is True


def test_stale_when_cache_missing_latest_snapshot_column(tmp_path):
    # Build a DB whose host_probe_cache lacks latest_snapshot_id, then create the
    # Sherlock tables directly (bypassing full run_migrations, which would add the
    # column) so the read must treat the anchor as unknown.
    db = str(tmp_path / "nocol.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);
        CREATE TABLE host_probe_cache (server_id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, host_type TEXT, protocol_server_id INTEGER,
            ip_address TEXT, port INTEGER, snapshot_id INTEGER, highest_severity TEXT,
            total_hit_count INTEGER DEFAULT 0, detail_count INTEGER DEFAULT 0,
            truncated INTEGER DEFAULT 0, scanned_at DATETIME, updated_at DATETIME);
        CREATE TABLE sherlock_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, result_id INTEGER, severity TEXT,
            category TEXT, label TEXT, pattern TEXT, display_path TEXT, created_at DATETIME);
        CREATE UNIQUE INDEX idx_sherlock_results_key ON sherlock_results(host_type, protocol_server_id);
        INSERT INTO smb_servers (ip_address) VALUES ('10.0.0.9');
        INSERT INTO host_probe_cache (server_id, status) VALUES (1, 'probed');
        INSERT INTO sherlock_results (host_type, protocol_server_id, snapshot_id, highest_severity, total_hit_count)
            VALUES ('S', 1, 5, 'high', 2);
        """
    )
    conn.commit()
    conn.close()

    # Avoid run_migrations on this handcrafted DB.
    reader = object.__new__(DatabaseReader)
    reader.db_path = db
    reader.cache = {}
    reader.cache_timestamps = {}
    import threading
    reader.connection_lock = threading.Lock()

    result = reader.get_sherlock_result_for_host("10.0.0.9", "S", protocol_server_id=1)
    assert result is not None
    assert result["stale"] is True


# ---------------------------------------------------------------------------
# 5. Missing tables / not scanned
# ---------------------------------------------------------------------------

def test_missing_sherlock_tables_returns_none(tmp_path):
    db = str(tmp_path / "bare.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);"
        "INSERT INTO smb_servers (ip_address) VALUES ('1.2.3.4');"
    )
    conn.commit()
    conn.close()

    reader = object.__new__(DatabaseReader)
    reader.db_path = db
    reader.cache = {}
    reader.cache_timestamps = {}
    import threading
    reader.connection_lock = threading.Lock()

    assert reader.get_sherlock_result_for_host("1.2.3.4", "S", protocol_server_id=1) is None
    assert reader.store_sherlock_result("1.2.3.4", "S", 5, _make_match_result(1),
                                        protocol_server_id=1) is None


def test_unscanned_host_returns_none(tmp_path):
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.4")
    reader = DatabaseReader(db_path=db)
    assert reader.get_sherlock_result_for_host("10.0.0.4", "S", protocol_server_id=sid) is None


# ---------------------------------------------------------------------------
# 6. Partial Sherlock tables self-heal
# ---------------------------------------------------------------------------

def test_partial_results_missing_key_column_converges(tmp_path):
    db = str(tmp_path / "partial_results.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);
        CREATE TABLE sherlock_results (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER);
        INSERT INTO sherlock_results (snapshot_id) VALUES (1);
        """
    )
    conn.commit()
    conn.close()

    run_migrations(db)  # must not raise on ALTER and must add the key columns

    cols = _sherlock_columns(db, "sherlock_results")
    assert "host_type" in cols and "protocol_server_id" in cols


def test_partial_hits_missing_result_id_converges_and_stores(tmp_path):
    db = str(tmp_path / "partial_hits.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);
        CREATE TABLE sherlock_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, severity TEXT);
        """
    )
    conn.commit()
    conn.close()

    run_migrations(db)  # backfills result_id etc. before dedupe; no error

    assert "result_id" in _sherlock_columns(db, "sherlock_hits")

    sid = _insert_server(db, "S", "10.0.0.5")
    reader = DatabaseReader(db_path=db)
    rid = reader.store_sherlock_result("10.0.0.5", "S", 3, _make_match_result(2),
                                       protocol_server_id=sid)
    assert rid is not None
    result = reader.get_sherlock_result_for_host("10.0.0.5", "S", protocol_server_id=sid)
    assert len(result["hits"]) == 2


def test_guard_rejects_partial_table_missing_omitted_column(tmp_path):
    # sherlock_results lacks ip_address (a column the store writes and read
    # selects but which earlier guard sets omitted). The expanded guard must make
    # store/read a deterministic no-op before any SQL touches the table.
    db = str(tmp_path / "missing_col.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE);
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, host_type TEXT, protocol_server_id INTEGER,
            port INTEGER, snapshot_id INTEGER, highest_severity TEXT,
            total_hit_count INTEGER DEFAULT 0, detail_count INTEGER DEFAULT 0,
            truncated INTEGER DEFAULT 0, scanned_at DATETIME, updated_at DATETIME);
        CREATE TABLE sherlock_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, result_id INTEGER, severity TEXT,
            category TEXT, label TEXT, pattern TEXT, display_path TEXT, created_at DATETIME);
        INSERT INTO smb_servers (ip_address) VALUES ('10.0.0.30');
        """
    )
    conn.commit()
    conn.close()

    # Bypass run_migrations so the table stays partial (migration would backfill).
    reader = object.__new__(DatabaseReader)
    reader.db_path = db
    reader.cache = {}
    reader.cache_timestamps = {}
    import threading
    reader.connection_lock = threading.Lock()

    assert reader.store_sherlock_result("10.0.0.30", "S", 5, _make_match_result(1),
                                        protocol_server_id=1) is None
    assert reader.get_sherlock_result_for_host("10.0.0.30", "S", protocol_server_id=1) is None

    conn = _conn(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sherlock_results").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Dedupe + no orphans + unique index (foreign keys OFF)
# ---------------------------------------------------------------------------

def test_dedupe_removes_orphans_and_builds_unique_index(tmp_path):
    db = str(tmp_path / "dupes.db")
    conn = sqlite3.connect(db)
    # foreign_keys default OFF; do not enable.
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, host_type TEXT, protocol_server_id INTEGER,
            snapshot_id INTEGER, total_hit_count INTEGER DEFAULT 0);
        CREATE TABLE sherlock_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, result_id INTEGER, severity TEXT);
        INSERT INTO sherlock_results (id, host_type, protocol_server_id, snapshot_id) VALUES
            (1, 'S', 7, 1), (2, 'S', 7, 2), (3, 'S', 7, 3);
        INSERT INTO sherlock_hits (result_id, severity) VALUES (1, 'low'), (2, 'med'), (3, 'high');
        """
    )
    conn.commit()
    conn.close()

    run_migrations(db)

    conn = _conn(db)
    try:
        results = conn.execute("SELECT id FROM sherlock_results").fetchall()
        assert len(results) == 1
        surviving_id = int(results[0]["id"])
        assert surviving_id == 3  # highest id kept

        hit_result_ids = {r["result_id"] for r in conn.execute("SELECT result_id FROM sherlock_hits").fetchall()}
        assert hit_result_ids == {3}  # no orphans

        assert _unique_index_covers(conn.cursor(), "sherlock_results",
                                    ("host_type", "protocol_server_id"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 8. Wrong-shaped same-name index gets rebuilt
# ---------------------------------------------------------------------------

def test_wrong_shaped_index_rebuilt(tmp_path):
    db = str(tmp_path / "badindex.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE smb_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT UNIQUE, country TEXT, first_seen DATETIME, last_seen DATETIME, created_at DATETIME);
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, host_type TEXT, protocol_server_id INTEGER,
            snapshot_id INTEGER);
        CREATE INDEX idx_sherlock_results_key ON sherlock_results(snapshot_id);
        """
    )
    conn.commit()
    # Before migration: a non-unique, wrong-column index named the same.
    cur = conn.cursor()
    assert not _unique_index_covers(cur, "sherlock_results", ("host_type", "protocol_server_id"))
    conn.close()

    run_migrations(db)

    conn = _conn(db)
    try:
        assert _unique_index_covers(conn.cursor(), "sherlock_results",
                                    ("host_type", "protocol_server_id"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 11. clear_cache after store
# ---------------------------------------------------------------------------

def test_store_clears_cache(tmp_path):
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.6")
    reader = DatabaseReader(db_path=db)
    reader.cache["stale_key"] = {"x": 1}
    reader.cache_timestamps["stale_key"] = 123.0

    reader.store_sherlock_result("10.0.0.6", "S", 4, _make_match_result(1),
                                 protocol_server_id=sid)

    assert reader.cache == {}
    assert reader.cache_timestamps == {}


# ---------------------------------------------------------------------------
# Store guards: snapshot None / bad input
# ---------------------------------------------------------------------------

def test_store_noop_on_missing_snapshot(tmp_path):
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.7")
    reader = DatabaseReader(db_path=db)
    assert reader.store_sherlock_result("10.0.0.7", "S", None, _make_match_result(1),
                                        protocol_server_id=sid) is None
    conn = _conn(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sherlock_results").fetchone()[0] == 0
    finally:
        conn.close()
