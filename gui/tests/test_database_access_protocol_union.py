"""
Tests for DatabaseReader.get_protocol_server_list() — UNION ALL S/F dual-row API.

Covers:
- SMB-only DB returns only S rows
- FTP-only data returns only F rows
- Same IP in both tables yields two rows with distinct row_keys
- Protocol state isolation (flags/probe per-protocol, no cross-contamination)
- Graceful fallback when FTP tables are absent (monkeypatched migration)
- recent_scan_only filter
- limit=None returns all rows without error
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.database_access import DatabaseReader


# ---------------------------------------------------------------------------
# Minimal schema helpers
# ---------------------------------------------------------------------------

_SMB_SCHEMA = """
CREATE TABLE IF NOT EXISTS smb_servers (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    ip_address  TEXT     NOT NULL UNIQUE,
    country     TEXT,
    country_code TEXT,
    auth_method TEXT,
    first_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count  INTEGER  DEFAULT 1,
    status      TEXT     DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS share_access (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER  NOT NULL,
    session_id  INTEGER  NOT NULL DEFAULT 0,
    share_name  TEXT     NOT NULL,
    accessible  BOOLEAN  NOT NULL DEFAULT FALSE,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS host_user_flags (
    server_id INTEGER PRIMARY KEY,
    favorite  INTEGER  DEFAULT 0,
    avoid     INTEGER  DEFAULT 0,
    notes     TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS host_probe_cache (
    server_id         INTEGER PRIMARY KEY,
    status            TEXT    DEFAULT 'unprobed',
    last_probe_at     DATETIME,
    indicator_matches INTEGER DEFAULT 0,
    indicator_samples TEXT,
    snapshot_path     TEXT,
    extracted         INTEGER DEFAULT 0,
    rce_status        TEXT    DEFAULT 'not_run',
    rce_verdict_summary TEXT,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""

_FTP_SCHEMA = """
CREATE TABLE IF NOT EXISTS ftp_servers (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    ip_address      TEXT     NOT NULL UNIQUE,
    country         TEXT,
    country_code    TEXT,
    port            INTEGER  NOT NULL DEFAULT 21,
    anon_accessible BOOLEAN  NOT NULL DEFAULT FALSE,
    banner          TEXT,
    first_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count      INTEGER  DEFAULT 1,
    status          TEXT     DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS ftp_user_flags (
    server_id  INTEGER PRIMARY KEY,
    favorite   INTEGER DEFAULT 0,
    avoid      INTEGER DEFAULT 0,
    notes      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ftp_access (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id            INTEGER NOT NULL,
    session_id           INTEGER,
    accessible           BOOLEAN DEFAULT FALSE,
    auth_status          TEXT,
    root_listing_available BOOLEAN DEFAULT FALSE,
    root_entry_count     INTEGER DEFAULT 0,
    error_message        TEXT,
    access_details       TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ftp_probe_cache (
    server_id           INTEGER PRIMARY KEY,
    status              TEXT    DEFAULT 'unprobed',
    last_probe_at       DATETIME,
    indicator_matches   INTEGER DEFAULT 0,
    indicator_samples   TEXT,
    snapshot_path       TEXT,
    accessible_dirs_count INTEGER DEFAULT 0,
    accessible_dirs_list  TEXT,
    extracted           INTEGER DEFAULT 0,
    rce_status          TEXT    DEFAULT 'not_run',
    rce_verdict_summary TEXT,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);
"""


def _make_db(smb: bool = True, ftp: bool = True) -> str:
    """Create a temp SQLite DB with the requested tables. Returns db path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    if smb:
        conn.executescript(_SMB_SCHEMA)
    if ftp:
        conn.executescript(_FTP_SCHEMA)
    conn.commit()
    conn.close()
    return path


def _reader(path: str, monkeypatch) -> DatabaseReader:
    """Build a DatabaseReader with run_migrations no-op'd."""
    monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
    return DatabaseReader(db_path=path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_smb_only_returns_s_rows(monkeypatch):
    """DB with SMB rows and empty FTP tables → only S rows returned."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("1.2.3.4", "US", "active"),
        )
        conn.execute(
            "INSERT INTO smb_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("5.6.7.8", "GB", "active"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list()

        assert total == 2
        assert all(r["host_type"] == "S" for r in rows)
        assert {r["ip_address"] for r in rows} == {"1.2.3.4", "5.6.7.8"}
    finally:
        os.unlink(path)


def test_ftp_only_returns_f_rows(monkeypatch):
    """DB with FTP rows and empty SMB table → only F rows returned."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("10.0.0.1", "DE", "active"),
        )
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("10.0.0.2", "FR", "active"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list()

        assert total == 2
        assert all(r["host_type"] == "F" for r in rows)
        assert {r["ip_address"] for r in rows} == {"10.0.0.1", "10.0.0.2"}
    finally:
        os.unlink(path)


def test_ftp_probe_cache_populates_accessible_columns(monkeypatch):
    """FTP rows expose probed directory count/list via total_shares/accessible columns."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO ftp_servers (id, ip_address, country_code, status) VALUES (1,?,?,?)",
            ("10.0.0.10", "US", "active"),
        )
        conn.execute(
            "INSERT INTO ftp_probe_cache (server_id, status, accessible_dirs_count, accessible_dirs_list) "
            "VALUES (1, 'clean', 2, 'pub,incoming')"
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list(limit=None)

        assert total == 1
        row = rows[0]
        assert row["host_type"] == "F"
        assert row["total_shares"] == 2
        assert row["accessible_shares"] == 2
        assert row["accessible_shares_list"] == "pub,incoming"
    finally:
        os.unlink(path)


def test_ftp_access_fallback_populates_count_when_probe_cache_missing(monkeypatch):
    """Without ftp_probe_cache row, latest ftp_access root_entry_count is used for FTP shares."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO ftp_servers (id, ip_address, country_code, status) VALUES (1,?,?,?)",
            ("10.0.0.20", "US", "active"),
        )
        conn.execute(
            "INSERT INTO ftp_access (server_id, accessible, root_listing_available, root_entry_count) "
            "VALUES (1, 1, 1, 7)"
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list(limit=None)
        assert total == 1
        row = rows[0]
        assert row["host_type"] == "F"
        assert row["total_shares"] == 7
        assert row["accessible_shares"] == 7
        assert row["accessible_shares_list"] == ""
    finally:
        os.unlink(path)


def test_same_ip_both_returns_two_rows(monkeypatch):
    """Same IP in smb_servers and ftp_servers → 2 rows with distinct row_keys."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status) VALUES (?,?)",
            ("192.168.1.1", "active"),
        )
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)",
            ("192.168.1.1", "active"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list()

        assert total == 2
        assert len(rows) == 2

        types = {r["host_type"] for r in rows}
        assert types == {"S", "F"}

        row_keys = {r["row_key"] for r in rows}
        assert len(row_keys) == 2
        assert any(k.startswith("S:") for k in row_keys)
        assert any(k.startswith("F:") for k in row_keys)
    finally:
        os.unlink(path)


def test_protocol_state_isolation(monkeypatch):
    """Same IP — SMB and FTP flags/probe must not cross-contaminate."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        # Insert SMB entry and flag it as favorite + probed
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)",
            ("10.10.10.10", "active"),
        )
        conn.execute(
            "INSERT INTO host_user_flags (server_id, favorite, avoid) VALUES (1, 1, 0)"
        )
        conn.execute(
            "INSERT INTO host_probe_cache (server_id, status) VALUES (1, 'probed')"
        )

        # Insert FTP entry — not favorite, different probe status
        conn.execute(
            "INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)",
            ("10.10.10.10", "active"),
        )
        conn.execute(
            "INSERT INTO ftp_user_flags (server_id, favorite, avoid) VALUES (1, 0, 0)"
        )
        conn.execute(
            "INSERT INTO ftp_probe_cache (server_id, status) VALUES (1, 'clean')"
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list()

        assert total == 2

        s_row = next(r for r in rows if r["host_type"] == "S")
        f_row = next(r for r in rows if r["host_type"] == "F")

        assert s_row["favorite"] == 1
        assert s_row["probe_status"] == "probed"

        assert f_row["favorite"] == 0
        assert f_row["probe_status"] == "clean"
    finally:
        os.unlink(path)


def test_graceful_fallback_ftp_absent(monkeypatch):
    """When FTP tables are absent, method returns SMB rows without raising."""
    # Create DB with SMB tables only — no FTP tables
    path = _make_db(smb=True, ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status) VALUES (?,?)",
            ("172.16.0.1", "active"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list()

        assert total == 1
        assert rows[0]["host_type"] == "S"
        assert rows[0]["ip_address"] == "172.16.0.1"
    finally:
        os.unlink(path)


def test_recent_scan_only(monkeypatch):
    """recent_scan_only=True filters out rows older than 1h from the most recent."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        # Recent SMB row (now)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status, last_seen) VALUES (?,?,datetime('now'))",
            ("1.1.1.1", "active"),
        )
        # Old SMB row (3 days ago)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status, last_seen) VALUES (?,?,datetime('now','-3 days'))",
            ("2.2.2.2", "active"),
        )
        # Old FTP row (3 days ago)
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, status, last_seen) VALUES (?,?,datetime('now','-3 days'))",
            ("3.3.3.3", "active"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list(recent_scan_only=True)

        assert total == 1
        assert rows[0]["ip_address"] == "1.1.1.1"
        assert rows[0]["host_type"] == "S"
    finally:
        os.unlink(path)


def test_limit_none_returns_all_rows(monkeypatch):
    """limit=None must return all rows without applying a LIMIT clause."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        for i in range(3):
            conn.execute(
                "INSERT INTO smb_servers (ip_address, status) VALUES (?,?)",
                (f"10.0.0.{i + 1}", "active"),
            )
        for i in range(2):
            conn.execute(
                "INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)",
                (f"10.0.1.{i + 1}", "active"),
            )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        rows, total = reader.get_protocol_server_list(limit=None)

        assert total == 5
        assert len(rows) == 5
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Card 5: bulk_delete_rows tests
# ---------------------------------------------------------------------------

_FAILURE_LOGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS failure_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    error_message TEXT,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_full_db(smb: bool = True, ftp: bool = True, failure_logs: bool = True) -> str:
    """Create a temp SQLite DB with requested tables. Returns db path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    if smb:
        conn.executescript(_SMB_SCHEMA)
    if ftp:
        conn.executescript(_FTP_SCHEMA)
    if failure_logs:
        conn.executescript(_FAILURE_LOGS_SCHEMA)
    conn.commit()
    conn.close()
    return path


def test_bulk_delete_rows_smb_only(monkeypatch):
    """Deleting ('S', ip) removes smb_servers row; ftp_servers row for same IP survives."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        result = reader.bulk_delete_rows([("S", "1.2.3.4")])

        assert result["deleted_count"] == 1
        assert "1.2.3.4" in result["deleted_ips"]
        assert "1.2.3.4" in result["deleted_smb_ips"]
        assert result["error"] is None

        conn = sqlite3.connect(path)
        smb_row = conn.execute("SELECT * FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()
        ftp_row = conn.execute("SELECT * FROM ftp_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()
        conn.close()

        assert smb_row is None, "SMB row should be deleted"
        assert ftp_row is not None, "FTP row must survive SMB-only delete"
    finally:
        os.unlink(path)


def test_bulk_delete_rows_ftp_only(monkeypatch):
    """Deleting ('F', ip) removes ftp_servers row; smb_servers row for same IP survives."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        result = reader.bulk_delete_rows([("F", "1.2.3.4")])

        assert result["deleted_count"] == 1
        assert "1.2.3.4" in result["deleted_ips"]
        assert "1.2.3.4" not in result["deleted_smb_ips"], "SMB cache must NOT be cleared on FTP-only delete"
        assert result["error"] is None

        conn = sqlite3.connect(path)
        smb_row = conn.execute("SELECT * FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()
        ftp_row = conn.execute("SELECT * FROM ftp_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()
        conn.close()

        assert smb_row is not None, "SMB row must survive FTP-only delete"
        assert ftp_row is None, "FTP row should be deleted"
    finally:
        os.unlink(path)


def test_bulk_delete_rows_mixed(monkeypatch):
    """[('S', ip1), ('F', ip2)] deletes both; no cross-contamination."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.1.1.1", "active"))
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("2.2.2.2", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("3.3.3.3", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("4.4.4.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        result = reader.bulk_delete_rows([("S", "1.1.1.1"), ("F", "4.4.4.4")])

        assert result["deleted_count"] == 2
        assert set(result["deleted_ips"]) == {"1.1.1.1", "4.4.4.4"}
        assert result["deleted_smb_ips"] == ["1.1.1.1"]
        assert result["error"] is None

        conn = sqlite3.connect(path)
        survivors = {
            row[0] for row in conn.execute(
                "SELECT ip_address FROM smb_servers UNION ALL SELECT ip_address FROM ftp_servers"
            ).fetchall()
        }
        conn.close()

        assert "2.2.2.2" in survivors
        assert "3.3.3.3" in survivors
        assert "1.1.1.1" not in survivors
        assert "4.4.4.4" not in survivors
    finally:
        os.unlink(path)


def test_bulk_delete_rows_both_for_same_ip(monkeypatch):
    """[('S', ip), ('F', ip)] deletes both protocol rows; count=2."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("9.9.9.9", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("9.9.9.9", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        result = reader.bulk_delete_rows([("S", "9.9.9.9"), ("F", "9.9.9.9")])

        assert result["deleted_count"] == 2
        assert result["deleted_smb_ips"] == ["9.9.9.9"]

        conn = sqlite3.connect(path)
        smb_row = conn.execute("SELECT * FROM smb_servers WHERE ip_address=?", ("9.9.9.9",)).fetchone()
        ftp_row = conn.execute("SELECT * FROM ftp_servers WHERE ip_address=?", ("9.9.9.9",)).fetchone()
        conn.close()

        assert smb_row is None
        assert ftp_row is None
    finally:
        os.unlink(path)


def test_bulk_delete_rows_missing_ftp_table(monkeypatch):
    """When ftp_servers absent: SMB deletes succeed, error string non-None, no crash."""
    path = _make_full_db(ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("5.5.5.5", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        result = reader.bulk_delete_rows([("S", "5.5.5.5"), ("F", "5.5.5.5")])

        # SMB delete should succeed
        assert result["deleted_count"] == 1
        assert "5.5.5.5" in result["deleted_smb_ips"]
        # FTP attempt should report an error (table missing) but not crash
        assert result["error"] is not None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Card 5: get_rce_status_for_host tests
# ---------------------------------------------------------------------------


def test_get_rce_status_for_host_smb(monkeypatch):
    """Returns rce_status from host_probe_cache for S row."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.1", "active"))
        conn.execute(
            "INSERT INTO host_probe_cache (server_id, status, rce_status) VALUES (1, 'clean', 'flagged')"
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        assert reader.get_rce_status_for_host("10.0.0.1", "S") == "flagged"
    finally:
        os.unlink(path)


def test_get_rce_status_for_host_ftp(monkeypatch):
    """Returns rce_status from ftp_probe_cache for F row; 'not_run' if table absent."""
    path = _make_full_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.2", "active"))
        conn.execute(
            "INSERT INTO ftp_probe_cache (server_id, status, rce_status) VALUES (1, 'clean', 'clean')"
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        assert reader.get_rce_status_for_host("10.0.0.2", "F") == "clean"
    finally:
        os.unlink(path)

    # ftp tables absent → 'not_run', no crash
    path2 = _make_full_db(ftp=False)
    try:
        reader2 = _reader(path2, monkeypatch)
        assert reader2.get_rce_status_for_host("10.0.0.2", "F") == "not_run"
    finally:
        os.unlink(path2)
