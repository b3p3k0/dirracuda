"""
C4 integration tests for DatabaseReader.get_sherlock_risk_summary_map and the
end-to-end standalone scan path through a real migrated DB.

Covers: empty/guarded returns, fresh vs stale flagging, per-protocol degradation
when a cache table/column is missing, and that the reusable helper persists results
that the summary map then surfaces.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.db_migrations import run_migrations
from shared.sherlock import MatchResult, Severity, SherlockHit, default_settings
from gui.utils.database_access import DatabaseReader
from gui.utils.sherlock_scan import run_sherlock_scan

_CACHE_TABLE = {"S": "host_probe_cache", "F": "ftp_probe_cache", "H": "http_probe_cache"}


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _migrated_db(tmp_path):
    db = str(tmp_path / "current.db")
    run_migrations(db)
    return db


def _insert_server(db_path, host_type, ip, port=80):
    conn = _conn(db_path)
    try:
        if host_type == "S":
            conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", (ip,))
            tbl, where, params = "smb_servers", "ip_address=?", (ip,)
        elif host_type == "F":
            conn.execute("INSERT INTO ftp_servers (ip_address) VALUES (?)", (ip,))
            tbl, where, params = "ftp_servers", "ip_address=?", (ip,)
        else:
            conn.execute("INSERT INTO http_servers (ip_address, port) VALUES (?, ?)", (ip, port))
            tbl, where, params = "http_servers", "ip_address=? AND port=?", (ip, port)
        conn.commit()
        return int(conn.execute(f"SELECT id FROM {tbl} WHERE {where}", params).fetchone()["id"])
    finally:
        conn.close()


def _set_latest_snapshot(db_path, host_type, server_id, snapshot_id):
    cache = _CACHE_TABLE[host_type]
    conn = _conn(db_path)
    try:
        conn.execute(
            f"INSERT INTO {cache} (server_id, latest_snapshot_id) VALUES (?, ?) "
            f"ON CONFLICT(server_id) DO UPDATE SET latest_snapshot_id=excluded.latest_snapshot_id",
            (server_id, snapshot_id),
        )
        conn.commit()
    finally:
        conn.close()


def _make_result(n):
    hits = [
        SherlockHit(Severity.HIGH, "Credentials", "Password files", "*password*", f"s/p{i}.txt")
        for i in range(n)
    ]
    return MatchResult(hits=hits, highest_severity=Severity.HIGH if hits else None, hit_count=n)


def test_empty_when_no_results(tmp_path):
    db = _migrated_db(tmp_path)
    reader = DatabaseReader(db_path=db)
    assert reader.get_sherlock_risk_summary_map() == {}


def test_fresh_result_surfaces(tmp_path):
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.1")
    _set_latest_snapshot(db, "S", sid, 99)
    reader = DatabaseReader(db_path=db)
    reader.store_sherlock_result("10.0.0.1", "S", 99, _make_result(3), protocol_server_id=sid)

    summary = reader.get_sherlock_risk_summary_map()
    key = f"S:{sid}"
    assert key in summary
    assert summary[key]["severity"] == "high"
    assert summary[key]["count"] == 3
    assert summary[key]["stale"] is False


def test_stale_when_snapshot_advances(tmp_path):
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.1")
    _set_latest_snapshot(db, "S", sid, 99)
    reader = DatabaseReader(db_path=db)
    reader.store_sherlock_result("10.0.0.1", "S", 99, _make_result(2), protocol_server_id=sid)

    # A newer snapshot arrives; the stored result is now stale -> blank.
    _set_latest_snapshot(db, "S", sid, 100)
    summary = reader.get_sherlock_risk_summary_map()
    assert summary[f"S:{sid}"]["stale"] is True


def test_per_protocol_degradation_when_cache_column_missing(tmp_path):
    db = _migrated_db(tmp_path)
    s_id = _insert_server(db, "S", "10.0.0.1")
    f_id = _insert_server(db, "F", "10.0.0.2")
    _set_latest_snapshot(db, "S", s_id, 5)
    _set_latest_snapshot(db, "F", f_id, 5)
    reader = DatabaseReader(db_path=db)
    reader.store_sherlock_result("10.0.0.1", "S", 5, _make_result(1), protocol_server_id=s_id)
    reader.store_sherlock_result("10.0.0.2", "F", 5, _make_result(1), protocol_server_id=f_id)

    # Leave FTP with a partial cache table: FTP staleness can't be proven -> stale,
    # but SMB must still report a fresh finding.
    conn = _conn(db)
    try:
        conn.execute("DROP TABLE ftp_probe_cache")
        conn.execute("CREATE TABLE ftp_probe_cache (latest_snapshot_id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    reader2 = DatabaseReader(db_path=db)
    summary = reader2.get_sherlock_risk_summary_map()
    assert summary[f"S:{s_id}"]["stale"] is False
    assert summary[f"S:{s_id}"]["count"] == 1
    assert summary[f"F:{f_id}"]["stale"] is True  # degraded, not missing


def test_helper_persists_what_summary_reads(tmp_path):
    """Standalone helper path == what C5 reuses: scan then summary surfaces it."""
    db = _migrated_db(tmp_path)
    sid = _insert_server(db, "S", "10.0.0.9")
    # A real snapshot with a credential filename, anchored as latest.
    reader = DatabaseReader(db_path=db)
    snap_id = reader.upsert_probe_snapshot_for_host(
        "10.0.0.9", "S",
        {"shares": [{"share": "DOCS", "root_files": ["company_password.txt"]}]},
        protocol_server_id=sid,
    )
    reader.set_latest_probe_snapshot_for_host("10.0.0.9", "S", snap_id, protocol_server_id=sid)

    targets = [{"ip_address": "10.0.0.9", "host_type": "S", "protocol_server_id": sid, "port": None}]
    result = run_sherlock_scan(reader, default_settings(), targets)
    assert result.scanned == 1
    assert result.with_findings == 1

    summary = reader.get_sherlock_risk_summary_map()
    assert summary[f"S:{sid}"]["severity"] == "high"
    assert summary[f"S:{sid}"]["stale"] is False
