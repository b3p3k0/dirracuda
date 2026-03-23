"""
Tests for HttpPersistence behavior (shared/db_http_persistence.py).

Covers:
- upsert_http_server: insert returns id; conflict increments scan_count
- record_http_access: bool coercions stored as integers
- persist_discovery_outcomes_batch: writes both http_servers and http_access rows
- persist_access_outcomes_batch: writes http_probe_cache for accessible and inaccessible
- Re-export contract: FtpPersistence and HttpPersistence importable from shared.database
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from shared.db_migrations import run_migrations
from shared.db_http_persistence import HttpPersistence
from commands.http.models import HttpDiscoveryOutcome, HttpAccessOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> str:
    db = tmp_path / "test.db"
    run_migrations(str(db))
    return str(db)


def _http_discovery_outcome(**kwargs) -> HttpDiscoveryOutcome:
    defaults = dict(
        ip="203.0.113.1",
        country="United States",
        country_code="US",
        port=80,
        scheme="http",
        banner="Apache",
        title="Test",
        shodan_data="{}",
        reason="timeout",
        error_message="timed out",
    )
    defaults.update(kwargs)
    return HttpDiscoveryOutcome(**defaults)


def _http_access_outcome(**kwargs) -> HttpAccessOutcome:
    defaults = dict(
        ip="203.0.113.2",
        country="United States",
        country_code="US",
        port=80,
        scheme="http",
        banner="nginx",
        title="Index of /",
        shodan_data="{}",
        accessible=True,
        status_code=200,
        is_index_page=True,
        dir_count=3,
        file_count=5,
        tls_verified=False,
        reason="",
        error_message="",
        access_details=json.dumps({
            "reason": "",
            "status_code": 200,
            "tls_verified": False,
            "dir_count": 3,
            "file_count": 5,
            "subdirs": [
                {"path": "/pub", "dir_count": 1, "file_count": 2},
                {"path": "/data", "dir_count": 2, "file_count": 3},
            ],
        }),
    )
    defaults.update(kwargs)
    return HttpAccessOutcome(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upsert_http_server_insert_returns_id(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    sid = p.upsert_http_server("1.2.3.4", "US", "US", 80, "http", "", "", "{}")
    assert isinstance(sid, int)
    assert sid > 0


def test_upsert_http_server_conflict_updates_and_increments_scan_count(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    sid1 = p.upsert_http_server("1.2.3.4", "US", "US", 80, "http", "old", "old title", "{}")
    sid2 = p.upsert_http_server("1.2.3.4", "US", "US", 443, "https", "new", "new title", "{}")
    assert sid1 == sid2

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT scan_count, port, scheme, banner, title FROM http_servers WHERE ip_address = ?",
            ("1.2.3.4",),
        ).fetchone()
        assert row[0] == 2        # scan_count incremented
        assert row[1] == 443      # port updated
        assert row[2] == "https"  # scheme updated
        assert row[3] == "new"    # banner updated
        assert row[4] == "new title"
    finally:
        conn.close()


def test_record_http_access_inserts_row_with_bool_coercion(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    sid = p.upsert_http_server("1.2.3.5", "US", "US", 80, "http", "", "", "{}")
    p.record_http_access(
        server_id=sid,
        session_id=None,
        accessible=True,
        status_code=200,
        is_index_page=True,
        dir_count=2,
        file_count=4,
        tls_verified=False,
        error_message="",
        access_details="{}",
    )

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT accessible, is_index_page, tls_verified, dir_count, file_count "
            "FROM http_access WHERE server_id = ?",
            (sid,),
        ).fetchone()
        assert row == (1, 1, 0, 2, 4)   # bools stored as 0/1
    finally:
        conn.close()


def test_persist_discovery_outcomes_batch_writes_access_row(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    o = _http_discovery_outcome(ip="10.0.0.1")
    p.persist_discovery_outcomes_batch([o])

    conn = sqlite3.connect(db)
    try:
        server_row = conn.execute(
            "SELECT id FROM http_servers WHERE ip_address = ?", ("10.0.0.1",)
        ).fetchone()
        assert server_row is not None

        access_row = conn.execute(
            "SELECT accessible, status_code, dir_count, file_count "
            "FROM http_access WHERE server_id = ?",
            (server_row[0],),
        ).fetchone()
        assert access_row is not None
        assert access_row == (0, 0, 0, 0)   # discovery failure: accessible=0, all counts 0
    finally:
        conn.close()


def test_persist_access_outcomes_batch_writes_probe_cache_for_accessible(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    o = _http_access_outcome(ip="10.0.0.2", accessible=True, dir_count=3, file_count=5)
    p.persist_access_outcomes_batch([o])

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT pc.accessible_dirs_count, pc.accessible_files_count, pc.accessible_dirs_list
            FROM http_probe_cache pc
            JOIN http_servers s ON s.id = pc.server_id
            WHERE s.ip_address = ?
            """,
            ("10.0.0.2",),
        ).fetchone()
        assert row is not None
        assert row[0] == 3                  # dir_count
        assert row[1] == 5                  # file_count
        assert "pub" in row[2]              # accessible_dirs_list contains subdir paths
        assert "data" in row[2]
    finally:
        conn.close()


def test_persist_access_outcomes_batch_writes_probe_cache_for_inaccessible(tmp_path):
    db = _make_db(tmp_path)
    p = HttpPersistence(db)
    o = _http_access_outcome(
        ip="10.0.0.3",
        accessible=False,
        status_code=403,
        is_index_page=False,
        dir_count=0,
        file_count=0,
        reason="forbidden",
        error_message="403",
        access_details=json.dumps({"reason": "forbidden", "status_code": 403}),
    )
    p.persist_access_outcomes_batch([o])

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT pc.accessible_dirs_count, pc.accessible_files_count
            FROM http_probe_cache pc
            JOIN http_servers s ON s.id = pc.server_id
            WHERE s.ip_address = ?
            """,
            ("10.0.0.3",),
        ).fetchone()
        assert row is not None          # probe cache written even for inaccessible
        assert row == (0, 0)
    finally:
        conn.close()


def test_shared_database_reexport_contract_http_persistence():
    from shared.database import HttpPersistence as HP
    assert HP is HttpPersistence


def test_shared_database_reexport_contract_ftp_persistence():
    from shared.database import FtpPersistence
    from shared.db_ftp_persistence import FtpPersistence as FP
    assert FtpPersistence is FP
