"""
Tests for gui/utils/db_host_read_engine.py

Engine-level tests call engine functions directly via _make_connection_fn.
Adapter-level tests call through DatabaseReader to confirm wiring.
"""

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.db_host_read_engine import (
    get_accessible_shares,
    get_denied_share_counts,
    get_denied_shares,
    get_dual_protocol_count,
    get_ftp_server_count,
    get_ftp_servers,
    get_host_protocols,
    get_http_server_detail,
    get_rce_status,
    get_rce_status_for_host,
    get_server_auth_method,
    get_share_credentials,
)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_SCHEMA_SMB = """
CREATE TABLE IF NOT EXISTS smb_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address  TEXT    NOT NULL UNIQUE,
    auth_method TEXT
);
CREATE TABLE IF NOT EXISTS share_access (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id     INTEGER NOT NULL,
    share_name    TEXT    NOT NULL,
    accessible    BOOLEAN NOT NULL DEFAULT 0,
    auth_status   TEXT,
    error_message TEXT,
    test_timestamp TEXT,
    permissions   TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS share_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       INTEGER NOT NULL,
    share_name      TEXT    NOT NULL,
    username        TEXT,
    password        TEXT,
    source          TEXT,
    last_verified_at TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS host_probe_cache (
    server_id  INTEGER PRIMARY KEY,
    rce_status TEXT    DEFAULT 'not_run',
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""

_SCHEMA_FTP = """
CREATE TABLE IF NOT EXISTS ftp_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address  TEXT    NOT NULL UNIQUE,
    country_code TEXT,
    status      TEXT    DEFAULT 'active',
    last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ftp_probe_cache (
    server_id  INTEGER PRIMARY KEY,
    rce_status TEXT    DEFAULT 'not_run',
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);
"""

_SCHEMA_HTTP = """
CREATE TABLE IF NOT EXISTS http_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address  TEXT    NOT NULL,
    scheme      TEXT,
    port        INTEGER,
    last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS http_probe_cache (
    server_id  INTEGER PRIMARY KEY,
    rce_status TEXT    DEFAULT 'not_run',
    FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
);
"""

# Minimal v_host_protocols view for protocol-count tests.
# Combines rows from smb_servers and ftp_servers.
_VIEW_HOST_PROTOCOLS = """
CREATE VIEW IF NOT EXISTS v_host_protocols AS
SELECT
    ip_address,
    1 AS has_smb,
    0 AS has_ftp,
    'smb_only' AS protocol_presence
FROM smb_servers
UNION ALL
SELECT
    ip_address,
    0 AS has_smb,
    1 AS has_ftp,
    'ftp_only' AS protocol_presence
FROM ftp_servers;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(*schemas, view_ddl: str = "") -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    for schema in schemas:
        conn.executescript(schema)
    if view_ddl:
        conn.executescript(view_ddl)
    conn.commit()
    conn.close()
    return path


def _make_connection_fn(path: str):
    @contextmanager
    def _connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    return _connect


def _reader(path: str, monkeypatch):
    from gui.utils.database_access import DatabaseReader
    monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
    return DatabaseReader(db_path=path)


# ---------------------------------------------------------------------------
# get_server_auth_method
# ---------------------------------------------------------------------------


def test_get_server_auth_method_found():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, auth_method) VALUES (?,?)",
                     ("1.2.3.4", "Anonymous"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_server_auth_method(fn, "1.2.3.4") == "Anonymous"
    finally:
        os.unlink(path)


def test_get_server_auth_method_not_found():
    path = _make_db(_SCHEMA_SMB)
    try:
        fn = _make_connection_fn(path)
        assert get_server_auth_method(fn, "9.9.9.9") is None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_accessible_shares
# ---------------------------------------------------------------------------


def test_get_accessible_shares_maps_columns():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute(
            "INSERT INTO share_access (server_id, share_name, accessible, permissions, test_timestamp) "
            "VALUES (?,?,1,?,?)",
            (sid, "Data", "READ", "2025-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_accessible_shares(fn, "1.2.3.4")
        assert len(result) == 1
        assert result[0]["share_name"] == "Data"
        assert result[0]["permissions"] == "READ"
        assert result[0]["last_tested"] == "2025-01-01T00:00:00"
    finally:
        os.unlink(path)


def test_get_accessible_shares_empty():
    path = _make_db(_SCHEMA_SMB)
    try:
        fn = _make_connection_fn(path)
        assert get_accessible_shares(fn, "1.2.3.4") == []
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_denied_shares
# ---------------------------------------------------------------------------


def _insert_denied_share(conn, server_id, name):
    conn.execute(
        "INSERT INTO share_access (server_id, share_name, accessible, auth_status, "
        "error_message, test_timestamp) VALUES (?,?,0,?,?,?)",
        (server_id, name, "ACCESS_DENIED", "permission error", "2025-01-01"),
    )


def test_get_denied_shares_without_limit():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        _insert_denied_share(conn, sid, "Backup")
        _insert_denied_share(conn, sid, "Admin")
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_denied_shares(fn, "1.2.3.4")
        assert len(result) == 2
        assert result[0]["share_name"] == "Admin"   # ORDER BY share_name
        assert "auth_status" in result[0]
        assert "error_message" in result[0]
        assert "last_tested" in result[0]
    finally:
        os.unlink(path)


def test_get_denied_shares_with_limit():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        for name in ("A", "B", "C"):
            _insert_denied_share(conn, sid, name)
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_denied_shares(fn, "1.2.3.4", limit=2)
        assert len(result) == 2
    finally:
        os.unlink(path)


def test_get_denied_shares_limit_zero_matches_no_limit():
    """limit=0 is falsy → no LIMIT clause applied; all rows returned."""
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        for name in ("A", "B", "C"):
            _insert_denied_share(conn, sid, name)
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result_zero = get_denied_shares(fn, "1.2.3.4", limit=0)
        result_none = get_denied_shares(fn, "1.2.3.4", limit=None)
        assert len(result_zero) == len(result_none) == 3
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_denied_share_counts
# ---------------------------------------------------------------------------


def test_get_denied_share_counts_map():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("5.6.7.8",))
        sid1 = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        _insert_denied_share(conn, sid1, "X")
        _insert_denied_share(conn, sid1, "Y")
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_denied_share_counts(fn)
        assert result["1.2.3.4"] == 2
        assert result["5.6.7.8"] == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_share_credentials
# ---------------------------------------------------------------------------


def test_get_share_credentials_mapping():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute(
            "INSERT INTO share_credentials "
            "(server_id, share_name, username, password, source, last_verified_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, "Files", "guest", "", "scan", "2025-01-01"),
        )
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_share_credentials(fn, "1.2.3.4")
        assert len(result) == 1
        row = result[0]
        assert row["share_name"] == "Files"
        assert row["username"] == "guest"
        assert row["password"] == ""
        assert row["source"] == "scan"
        assert row["last_verified_at"] == "2025-01-01"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_rce_status
# ---------------------------------------------------------------------------


def test_get_rce_status_default_not_run():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_rce_status(fn, "1.2.3.4") == "not_run"
    finally:
        os.unlink(path)


def test_get_rce_status_found():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute("INSERT INTO host_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (sid, "flagged"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_rce_status(fn, "1.2.3.4") == "flagged"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_rce_status_for_host
# ---------------------------------------------------------------------------


def test_get_rce_status_for_host_smb_path():
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute("INSERT INTO host_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (sid, "clean"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_rce_status_for_host(fn, "1.2.3.4", "S") == "clean"
    finally:
        os.unlink(path)


def test_get_rce_status_for_host_ftp_found():
    path = _make_db(_SCHEMA_FTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        sid = conn.execute("SELECT id FROM ftp_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute("INSERT INTO ftp_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (sid, "flagged"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_rce_status_for_host(fn, "1.2.3.4", "F") == "flagged"
    finally:
        os.unlink(path)


def test_get_rce_status_for_host_ftp_table_absent():
    path = _make_db(_SCHEMA_SMB)  # no FTP tables
    try:
        fn = _make_connection_fn(path)
        assert get_rce_status_for_host(fn, "1.2.3.4", "F") == "not_run"
    finally:
        os.unlink(path)


def test_get_rce_status_for_host_http_found():
    path = _make_db(_SCHEMA_HTTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO http_servers (ip_address, scheme, port) VALUES (?,?,?)",
                     ("1.2.3.4", "https", 443))
        sid = conn.execute("SELECT id FROM http_servers WHERE ip_address=?", ("1.2.3.4",)).fetchone()[0]
        conn.execute("INSERT INTO http_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (sid, "clean"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_rce_status_for_host(fn, "1.2.3.4", "H") == "clean"
    finally:
        os.unlink(path)


def test_get_rce_status_for_host_http_table_absent():
    path = _make_db(_SCHEMA_SMB)  # no HTTP tables
    try:
        fn = _make_connection_fn(path)
        assert get_rce_status_for_host(fn, "1.2.3.4", "H") == "not_run"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_ftp_servers
# ---------------------------------------------------------------------------


def test_get_ftp_servers_all():
    path = _make_db(_SCHEMA_FTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("5.6.7.8", "active"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_ftp_servers(fn)
        assert len(result) == 2
        assert all(isinstance(r, dict) for r in result)
    finally:
        os.unlink(path)


def test_get_ftp_servers_country_filter():
    path = _make_db(_SCHEMA_FTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, country_code, status) VALUES (?,?,?)",
                     ("1.2.3.4", "US", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, country_code, status) VALUES (?,?,?)",
                     ("5.6.7.8", "GB", "active"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_ftp_servers(fn, country="US")
        assert len(result) == 1
        assert result[0]["ip_address"] == "1.2.3.4"
    finally:
        os.unlink(path)


def test_get_ftp_servers_table_absent_returns_empty():
    path = _make_db(_SCHEMA_SMB)  # no ftp_servers table
    try:
        fn = _make_connection_fn(path)
        assert get_ftp_servers(fn) == []
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_ftp_server_count
# ---------------------------------------------------------------------------


def test_get_ftp_server_count():
    path = _make_db(_SCHEMA_FTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("5.6.7.8", "inactive"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_ftp_server_count(fn) == 1
    finally:
        os.unlink(path)


def test_get_ftp_server_count_table_absent_returns_zero():
    path = _make_db(_SCHEMA_SMB)
    try:
        fn = _make_connection_fn(path)
        assert get_ftp_server_count(fn) == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_http_server_detail
# ---------------------------------------------------------------------------


def test_get_http_server_detail_found():
    path = _make_db(_SCHEMA_HTTP)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO http_servers (ip_address, scheme, port) VALUES (?,?,?)",
                     ("1.2.3.4", "https", 443))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_http_server_detail(fn, "1.2.3.4")
        assert result == {"scheme": "https", "port": 443}
    finally:
        os.unlink(path)


def test_get_http_server_detail_not_found():
    path = _make_db(_SCHEMA_HTTP)
    try:
        fn = _make_connection_fn(path)
        assert get_http_server_detail(fn, "9.9.9.9") is None
    finally:
        os.unlink(path)


def test_get_http_server_detail_exception_returns_none():
    path = _make_db(_SCHEMA_SMB)  # no http_servers table
    try:
        fn = _make_connection_fn(path)
        assert get_http_server_detail(fn, "1.2.3.4") is None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_host_protocols
# ---------------------------------------------------------------------------


def test_get_host_protocols_all_rows():
    path = _make_db(_SCHEMA_SMB, _SCHEMA_FTP, view_ddl=_VIEW_HOST_PROTOCOLS)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("5.6.7.8", "active"))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_host_protocols(fn)
        assert len(result) == 2
        ips = {r["ip_address"] for r in result}
        assert ips == {"1.2.3.4", "5.6.7.8"}
    finally:
        os.unlink(path)


def test_get_host_protocols_single_ip():
    path = _make_db(_SCHEMA_SMB, _SCHEMA_FTP, view_ddl=_VIEW_HOST_PROTOCOLS)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("5.6.7.8",))
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        result = get_host_protocols(fn, ip="1.2.3.4")
        assert len(result) == 1
        assert result[0]["ip_address"] == "1.2.3.4"
    finally:
        os.unlink(path)


def test_get_host_protocols_table_absent_returns_empty():
    path = _make_db(_SCHEMA_SMB)  # no view
    try:
        fn = _make_connection_fn(path)
        assert get_host_protocols(fn) == []
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# get_dual_protocol_count
# ---------------------------------------------------------------------------


def test_get_dual_protocol_count():
    # View as defined returns smb_only and ftp_only rows — none are dual.
    # Insert same IP in both tables and use a view that exposes both rows for that IP.
    path = _make_db(_SCHEMA_SMB, _SCHEMA_FTP)
    try:
        # Use a custom view for this test that computes dual presence correctly.
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.2.3.4",))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("5.6.7.8", "active"))
        conn.executescript("""
            CREATE VIEW v_host_protocols AS
            SELECT ip_address,
                   MAX(has_smb) AS has_smb,
                   MAX(has_ftp) AS has_ftp,
                   CASE WHEN MAX(has_smb)=1 AND MAX(has_ftp)=1 THEN 'both'
                        WHEN MAX(has_smb)=1 THEN 'smb_only'
                        ELSE 'ftp_only' END AS protocol_presence
            FROM (
                SELECT ip_address, 1 AS has_smb, 0 AS has_ftp FROM smb_servers
                UNION ALL
                SELECT ip_address, 0, 1 FROM ftp_servers
            )
            GROUP BY ip_address;
        """)
        conn.commit()
        conn.close()
        fn = _make_connection_fn(path)
        assert get_dual_protocol_count(fn) == 1
    finally:
        os.unlink(path)


def test_get_dual_protocol_count_table_absent_returns_zero():
    path = _make_db(_SCHEMA_SMB)  # no view
    try:
        fn = _make_connection_fn(path)
        assert get_dual_protocol_count(fn) == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Adapter round-trip tests (DatabaseReader → engine)
# ---------------------------------------------------------------------------


def test_adapter_wiring_get_server_auth_method(monkeypatch):
    """DatabaseReader.get_server_auth_method delegates to engine and returns correct value."""
    path = _make_db(_SCHEMA_SMB)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, auth_method) VALUES (?,?)",
                     ("10.0.0.1", "Guest/Blank"))
        conn.commit()
        conn.close()
        reader = _reader(path, monkeypatch)
        assert reader.get_server_auth_method("10.0.0.1") == "Guest/Blank"
        assert reader.get_server_auth_method("9.9.9.9") is None
    finally:
        os.unlink(path)


def test_adapter_wiring_get_rce_status_for_host_branches(monkeypatch):
    """DatabaseReader.get_rce_status_for_host covers S, F, and H dispatch paths."""
    path = _make_db(_SCHEMA_SMB, _SCHEMA_FTP, _SCHEMA_HTTP)
    try:
        conn = sqlite3.connect(path)
        # SMB row with rce_status
        conn.execute("INSERT INTO smb_servers (ip_address) VALUES (?)", ("1.1.1.1",))
        smb_id = conn.execute("SELECT id FROM smb_servers WHERE ip_address=?", ("1.1.1.1",)).fetchone()[0]
        conn.execute("INSERT INTO host_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (smb_id, "flagged"))
        # FTP row with rce_status
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("2.2.2.2", "active"))
        ftp_id = conn.execute("SELECT id FROM ftp_servers WHERE ip_address=?", ("2.2.2.2",)).fetchone()[0]
        conn.execute("INSERT INTO ftp_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (ftp_id, "clean"))
        # HTTP row with rce_status
        conn.execute("INSERT INTO http_servers (ip_address, scheme, port) VALUES (?,?,?)",
                     ("3.3.3.3", "http", 80))
        http_id = conn.execute("SELECT id FROM http_servers WHERE ip_address=?", ("3.3.3.3",)).fetchone()[0]
        conn.execute("INSERT INTO http_probe_cache (server_id, rce_status) VALUES (?,?)",
                     (http_id, "unknown"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        assert reader.get_rce_status_for_host("1.1.1.1", "S") == "flagged"
        assert reader.get_rce_status_for_host("2.2.2.2", "F") == "clean"
        assert reader.get_rce_status_for_host("3.3.3.3", "H") == "unknown"
        # Missing IP returns not_run for each path
        assert reader.get_rce_status_for_host("9.9.9.9", "S") == "not_run"
        assert reader.get_rce_status_for_host("9.9.9.9", "F") == "not_run"
        assert reader.get_rce_status_for_host("9.9.9.9", "H") == "not_run"
    finally:
        os.unlink(path)
