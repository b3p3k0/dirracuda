"""Tests for protocol-agnostic immediate scan cohort selection."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.database_access import DatabaseReader


_SCHEMA = """
CREATE TABLE IF NOT EXISTS smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'active',
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS share_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    accessible BOOLEAN NOT NULL DEFAULT FALSE,
    test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ftp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'active'
);

-- Intentionally omits test_timestamp to exercise created_at fallback.
CREATE TABLE IF NOT EXISTS ftp_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    accessible BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS http_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS http_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    accessible BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
);
"""


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


def _reader(path: str, monkeypatch) -> DatabaseReader:
    monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
    return DatabaseReader(db_path=path)


def test_scan_cohort_ids_smb_uses_accessible_share_window(monkeypatch):
    path = _make_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, status, last_seen) VALUES (1,?,?,?)",
            ("198.51.100.1", "active", "2026-03-24 18:05:00"),
        )
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, status, last_seen) VALUES (2,?,?,?)",
            ("198.51.100.2", "active", "2026-03-24 17:40:00"),
        )
        conn.execute(
            "INSERT INTO share_access (server_id, accessible, test_timestamp) VALUES (1, 1, ?)",
            ("2026-03-24 18:05:00",),
        )
        conn.execute(
            "INSERT INTO share_access (server_id, accessible, test_timestamp) VALUES (2, 0, ?)",
            ("2026-03-24 18:06:00",),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        ids = reader.get_protocol_scan_cohort_server_ids(
            "S",
            "2026-03-24T14:00:00-04:00",
            "2026-03-24T14:10:00-04:00",
        )
        assert ids == {1}
    finally:
        os.unlink(path)


def test_scan_cohort_ids_smb_accepts_local_naive_access_timestamps(monkeypatch):
    path = _make_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO smb_servers (id, ip_address, status, last_seen) VALUES (1,?,?,?)",
            ("198.51.100.10", "active", "2026-03-24 14:05:00"),
        )
        # Simulate legacy/local SMB write path (datetime.now().isoformat() shape).
        conn.execute(
            "INSERT INTO share_access (server_id, accessible, test_timestamp) VALUES (1, 1, ?)",
            ("2026-03-24T14:05:00",),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        ids = reader.get_protocol_scan_cohort_server_ids(
            "S",
            "2026-03-24T14:00:00-04:00",
            "2026-03-24T14:10:00-04:00",
        )
        assert ids == {1}
    finally:
        os.unlink(path)


def test_scan_cohort_ids_ftp_excludes_stage1_reachability_rows(monkeypatch):
    path = _make_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("203.0.113.1", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (2,?,?)", ("203.0.113.2", "active"))
        conn.execute(
            "INSERT INTO ftp_access (server_id, accessible, error_message, created_at) VALUES (1, 0, ?, ?)",
            ("Port 21 unreachable: timeout", "2026-03-24 18:04:00"),
        )
        conn.execute(
            "INSERT INTO ftp_access (server_id, accessible, error_message, created_at) VALUES (2, 1, ?, ?)",
            ("", "2026-03-24 18:05:00"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        ids = reader.get_protocol_scan_cohort_server_ids(
            "F",
            "2026-03-24T14:00:00-04:00",
            "2026-03-24T14:10:00-04:00",
        )
        assert ids == {2}
    finally:
        os.unlink(path)


def test_scan_cohort_ids_http_uses_accessible_only(monkeypatch):
    path = _make_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO http_servers (id, ip_address, status) VALUES (11,?,?)", ("192.0.2.11", "active"))
        conn.execute("INSERT INTO http_servers (id, ip_address, status) VALUES (12,?,?)", ("192.0.2.12", "active"))
        conn.execute(
            "INSERT INTO http_access (server_id, accessible, error_message, test_timestamp) VALUES (11, 0, ?, ?)",
            ("Port 80 unreachable: connect_fail", "2026-03-24 18:02:00"),
        )
        conn.execute(
            "INSERT INTO http_access (server_id, accessible, error_message, test_timestamp) VALUES (12, 1, ?, ?)",
            ("", "2026-03-24 18:06:00"),
        )
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        ids = reader.get_protocol_scan_cohort_server_ids(
            "H",
            "2026-03-24T14:00:00-04:00",
            "2026-03-24T14:10:00-04:00",
        )
        assert ids == {12}
    finally:
        os.unlink(path)


def test_scan_cohort_ids_unknown_protocol_returns_empty(monkeypatch):
    path = _make_db()
    try:
        reader = _reader(path, monkeypatch)
        ids = reader.get_protocol_scan_cohort_server_ids(
            "X",
            "2026-03-24T14:00:00-04:00",
            "2026-03-24T14:10:00-04:00",
        )
        assert ids == set()
    finally:
        os.unlink(path)
