"""
Tests for gui/utils/db_server_list_engine.py

Engine-level tests call engine functions directly.
Adapter-level tests call through DatabaseReader.get_server_list.
"""

import os
import sqlite3
import tempfile
from contextlib import contextmanager

import pytest

from gui.utils.db_server_list_engine import (
    get_server_list,
    load_probe_cache_map,
    load_user_flags_map,
    query_server_list,
    query_server_list_enhanced,
    query_server_list_legacy,
)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE smb_servers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address   TEXT    NOT NULL UNIQUE,
    country      TEXT,
    country_code TEXT,
    auth_method  TEXT,
    last_seen    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count   INTEGER  DEFAULT 1,
    status       TEXT     DEFAULT 'active'
);
CREATE TABLE share_access (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id  INTEGER NOT NULL,
    session_id INTEGER NOT NULL DEFAULT 0,
    share_name TEXT    NOT NULL,
    accessible BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE TABLE vulnerabilities (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    status    TEXT    DEFAULT 'open',
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE TABLE host_user_flags (
    server_id  INTEGER PRIMARY KEY,
    favorite   INTEGER DEFAULT 0,
    avoid      INTEGER DEFAULT 0,
    notes      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE TABLE host_probe_cache (
    server_id            INTEGER PRIMARY KEY,
    status               TEXT    DEFAULT 'unprobed',
    last_probe_at        DATETIME,
    indicator_matches    INTEGER DEFAULT 0,
    indicator_samples    TEXT,
    snapshot_path        TEXT,
    extracted            INTEGER DEFAULT 0,
    rce_status           TEXT    DEFAULT 'not_run',
    rce_verdict_summary  TEXT,
    updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""

_VIEW_DDL = """
CREATE VIEW v_host_share_summary AS
SELECT
    s.ip_address,
    s.country,
    s.country_code,
    s.auth_method,
    s.last_seen,
    s.scan_count,
    COALESCE(sa_sum.total_shares, 0)            AS total_shares_discovered,
    COALESCE(sa_sum.accessible_shares, 0)       AS accessible_shares_count,
    COALESCE(sa_sum.accessible_shares_list, '') AS accessible_shares_list,
    CASE WHEN COALESCE(sa_sum.total_shares, 0) > 0
         THEN ROUND(100.0 * COALESCE(sa_sum.accessible_shares, 0)
                    / COALESCE(sa_sum.total_shares, 1), 1)
         ELSE 0.0
    END AS access_rate_percent
FROM smb_servers s
LEFT JOIN (
    SELECT server_id,
           COUNT(share_name) AS total_shares,
           COUNT(CASE WHEN accessible = 1 THEN 1 END) AS accessible_shares,
           GROUP_CONCAT(CASE WHEN accessible = 1 THEN share_name END, ',')
               AS accessible_shares_list
    FROM share_access
    GROUP BY server_id
) sa_sum ON s.id = sa_sum.server_id
WHERE s.status = 'active';
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(with_view: bool = False) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    if with_view:
        conn.executescript(_VIEW_DDL)
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


# ---------------------------------------------------------------------------
# Engine-level tests (1–7)
# ---------------------------------------------------------------------------


def test_mock_mode_pagination_and_filter():
    """mock_mode=True: country_filter and limit are applied correctly."""
    mock_data = {
        "servers": [
            {"ip_address": "1.1.1.1", "country_code": "US"},
            {"ip_address": "2.2.2.2", "country_code": "GB"},
            {"ip_address": "3.3.3.3", "country_code": "US"},
        ]
    }
    rows, total = get_server_list(None, True, mock_data, 1, 0, "US", False)
    assert total == 2
    assert len(rows) == 1
    assert rows[0]["ip_address"] == "1.1.1.1"


def test_mock_mode_limit_none_no_typeerror():
    """mock_mode=True with limit=None must not raise TypeError."""
    mock_data = {
        "servers": [
            {"ip_address": "1.1.1.1", "country_code": "US"},
            {"ip_address": "2.2.2.2", "country_code": "GB"},
        ]
    }
    rows, total = get_server_list(None, True, mock_data, None, 0, None, False)
    assert total == 2
    assert len(rows) == 2


def test_legacy_path_without_view():
    """No v_host_share_summary → legacy path; returned row has expected keys."""
    path = _make_db(with_view=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("10.0.0.1", "US", "active"),
        )
        conn.commit()
        conn.close()

        get_conn = _make_connection_fn(path)
        rows, total = query_server_list(get_conn, 10, 0, None, False)
        assert total == 1
        row = rows[0]
        for key in ("ip_address", "total_shares", "accessible_shares", "vulnerabilities",
                    "favorite", "probe_status"):
            assert key in row, f"missing key: {key}"
        assert row["ip_address"] == "10.0.0.1"
    finally:
        os.unlink(path)


def test_enhanced_path_with_view():
    """v_host_share_summary present → enhanced path; row has access_rate_percent."""
    path = _make_db(with_view=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, country_code, status) VALUES (1,?,?,?)",
            ("10.0.0.2", "US", "active"),
        )
        conn.execute(
            "INSERT INTO share_access (server_id, share_name, accessible) VALUES (1,?,?)",
            ("SHARE1", 1),
        )
        conn.execute(
            "INSERT INTO share_access (server_id, share_name, accessible) VALUES (1,?,?)",
            ("SHARE2", 0),
        )
        conn.commit()
        conn.close()

        get_conn = _make_connection_fn(path)
        rows, total = query_server_list(get_conn, 10, 0, None, False)
        assert total == 1
        row = rows[0]
        assert "access_rate_percent" in row
        assert row["accessible_shares"] == 1
        assert row["total_shares"] == 2
    finally:
        os.unlink(path)


def test_recent_scan_only_filters_old_rows():
    """recent_scan_only=True keeps only rows within 1 hour of most-recent last_seen."""
    path = _make_db(with_view=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status, last_seen) VALUES (?,?,datetime('now'))",
            ("9.9.9.9",  "active"),
        )
        conn.execute(
            "INSERT INTO smb_servers (ip_address, status, last_seen) VALUES (?,?,datetime('now','-3 days'))",
            ("8.8.8.8", "active"),
        )
        conn.commit()
        conn.close()

        get_conn = _make_connection_fn(path)
        rows, total = query_server_list(get_conn, None, 0, None, recent_scan_only=True)
        assert total == 1
        assert rows[0]["ip_address"] == "9.9.9.9"
    finally:
        os.unlink(path)


def test_flags_and_probe_merged_into_rows():
    """host_user_flags and host_probe_cache values appear in the returned row."""
    path = _make_db(with_view=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)",
            ("7.7.7.7", "active"),
        )
        conn.execute(
            "INSERT INTO host_user_flags (server_id, favorite, avoid, notes) VALUES (1,1,0,'mynote')",
        )
        conn.execute(
            "INSERT INTO host_probe_cache "
            "(server_id, status, indicator_matches, extracted, rce_status) "
            "VALUES (1,'probed',3,1,'clean')",
        )
        conn.commit()
        conn.close()

        get_conn = _make_connection_fn(path)
        rows, _ = query_server_list(get_conn, None, 0, None, False)
        row = rows[0]
        assert row["favorite"] == 1
        assert row["notes"] == "mynote"
        assert row["probe_status"] == "probed"
        assert row["indicator_matches"] == 3
        assert row["extracted"] == 1
        assert row["rce_status"] == "clean"
    finally:
        os.unlink(path)


def test_limit_none_returns_all_rows():
    """limit=None returns all rows unpaginated."""
    path = _make_db(with_view=False)
    try:
        conn = sqlite3.connect(path)
        for i in range(5):
            conn.execute(
                "INSERT INTO smb_servers (ip_address, status) VALUES (?,?)",
                (f"10.0.0.{i}", "active"),
            )
        conn.commit()
        conn.close()

        get_conn = _make_connection_fn(path)
        rows, total = query_server_list(get_conn, None, 0, None, False)
        assert total == 5
        assert len(rows) == 5
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Adapter-level tests (8–9)
# ---------------------------------------------------------------------------


def test_adapter_wiring_round_trip(monkeypatch):
    """DatabaseReader.get_server_list delegates correctly to the engine."""
    from gui.utils.database_access import DatabaseReader

    path = _make_db(with_view=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (ip_address, country_code, status) VALUES (?,?,?)",
            ("5.5.5.5", "DE", "active"),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
        reader = DatabaseReader(db_path=path)
        rows, total = reader.get_server_list(limit=10, offset=0)

        assert total == 1
        assert isinstance(rows, list)
        assert rows[0]["ip_address"] == "5.5.5.5"
    finally:
        os.unlink(path)


def test_adapter_limit_none_mock_no_typeerror(monkeypatch):
    """DatabaseReader.get_server_list(limit=None) in mock mode must not raise TypeError."""
    from gui.utils.database_access import DatabaseReader

    path = _make_db(with_view=False)
    try:
        monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
        reader = DatabaseReader(db_path=path)
        reader.mock_mode = True
        reader.mock_data = {
            "servers": [
                {"ip_address": "1.1.1.1", "country_code": "US"},
                {"ip_address": "2.2.2.2", "country_code": "GB"},
            ]
        }
        rows, total = reader.get_server_list(limit=None, offset=0)
        assert total == 2
        assert len(rows) == 2
    finally:
        os.unlink(path)
