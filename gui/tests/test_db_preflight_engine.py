"""
Headless unit tests for db_preflight_engine free functions.

Tests do NOT import DBToolsEngine — they exercise the free functions directly
using temporary SQLite databases, so no display or Tkinter required.
"""

import csv
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.db_preflight_engine import (
    validate_external_schema,
    preview_merge,
    preview_csv_import,
    REQUIRED_TABLES,
)
from gui.utils.db_tools_engine import SchemaValidation
from gui.utils import db_merge_engine as _db_merge


# ---------------------------------------------------------------------------
# Minimal schema helpers
# ---------------------------------------------------------------------------

CORE_SCHEMA = """
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type TEXT NOT NULL,
    status TEXT DEFAULT 'running'
);

CREATE TABLE smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    country_code TEXT,
    auth_method TEXT,
    shodan_data TEXT,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    scan_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    notes TEXT
);

CREATE TABLE share_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    accessible BOOLEAN DEFAULT FALSE,
    auth_status TEXT,
    permissions TEXT,
    share_type TEXT,
    share_comment TEXT,
    test_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    access_details TEXT,
    error_message TEXT
);
"""

FTP_SERVER_SCHEMA = """
CREATE TABLE ftp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    country_code TEXT,
    port INTEGER DEFAULT 21,
    anon_accessible BOOLEAN DEFAULT FALSE,
    banner TEXT,
    shodan_data TEXT,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    scan_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    notes TEXT
);
"""


def _make_db(schema: str, extra_sql: str = "") -> str:
    """Create a temp SQLite DB, execute schema + extra_sql, return path."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    if extra_sql:
        conn.executescript(extra_sql)
    conn.commit()
    conn.close()
    return path


def _cleanup(*paths):
    for p in paths:
        try:
            os.unlink(p)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# validate_external_schema
# ---------------------------------------------------------------------------

def test_validate_external_schema_file_not_found():
    result = validate_external_schema("/nonexistent/path/db.sqlite", SchemaValidation)
    assert result.valid is False
    assert any("not found" in e for e in result.errors)


def test_validate_external_schema_missing_tables():
    # DB with only scan_sessions and smb_servers — missing share_access
    schema = """
    CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT NOT NULL);
    CREATE TABLE smb_servers (
        id INTEGER PRIMARY KEY, ip_address TEXT NOT NULL UNIQUE,
        country TEXT, auth_method TEXT, last_seen DATETIME, first_seen DATETIME
    );
    """
    db = _make_db(schema)
    try:
        result = validate_external_schema(db, SchemaValidation)
        assert result.valid is False
        assert 'share_access' in result.missing_tables
    finally:
        _cleanup(db)


def test_validate_external_schema_missing_columns():
    # smb_servers missing last_seen
    schema = """
    CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT NOT NULL);
    CREATE TABLE smb_servers (
        id INTEGER PRIMARY KEY, ip_address TEXT NOT NULL UNIQUE,
        country TEXT, auth_method TEXT
    );
    CREATE TABLE share_access (
        id INTEGER PRIMARY KEY, server_id INTEGER, session_id INTEGER,
        share_name TEXT, accessible BOOLEAN, auth_status TEXT,
        permissions TEXT, share_type TEXT, share_comment TEXT,
        test_timestamp DATETIME, access_details TEXT, error_message TEXT
    );
    """
    db = _make_db(schema)
    try:
        result = validate_external_schema(db, SchemaValidation)
        assert result.valid is False
        missing_col_names = [c.split('.')[1] for c in result.missing_columns]
        assert 'last_seen' in missing_col_names or 'first_seen' in missing_col_names
    finally:
        _cleanup(db)


def test_validate_external_schema_valid():
    db = _make_db(CORE_SCHEMA)
    try:
        result = validate_external_schema(db, SchemaValidation)
        assert result.valid is True
        assert result.errors == []
    finally:
        _cleanup(db)


# ---------------------------------------------------------------------------
# preview_merge
# ---------------------------------------------------------------------------

def test_preview_merge_invalid_schema():
    # external DB has no tables at all
    ext = _make_db("CREATE TABLE unrelated (x INTEGER);")
    cur = _make_db(CORE_SCHEMA)
    try:
        result = preview_merge(
            ext, cur, SchemaValidation,
            _db_merge.table_columns, _db_merge.table_has_required_columns,
        )
        assert result['valid'] is False
        assert 'errors' in result
    finally:
        _cleanup(ext, cur)


def test_preview_merge_counts():
    # ext has 3 IPs; cur has 1 of them → new=2, existing=1
    ext = _make_db(CORE_SCHEMA, """
        INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen)
        VALUES ('10.0.0.1','US','anonymous','2024-01-01','2024-01-01'),
               ('10.0.0.2','US','anonymous','2024-01-01','2024-01-01'),
               ('10.0.0.3','US','anonymous','2024-01-01','2024-01-01');
    """)
    cur = _make_db(CORE_SCHEMA, """
        INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen)
        VALUES ('10.0.0.1','US','anonymous','2024-01-01','2024-01-01');
    """)
    try:
        result = preview_merge(
            ext, cur, SchemaValidation,
            _db_merge.table_columns, _db_merge.table_has_required_columns,
        )
        assert result['valid'] is True
        assert result['new_servers'] == 2
        assert result['existing_servers'] == 1
        assert result['external_servers'] == 3
    finally:
        _cleanup(ext, cur)


def test_preview_merge_warning_text():
    # ext has ftp_servers, cur does not → warning must contain "Target DB missing"
    ext = _make_db(CORE_SCHEMA + FTP_SERVER_SCHEMA, """
        INSERT INTO ftp_servers (ip_address, country, country_code, port, anon_accessible,
            banner, shodan_data, first_seen, last_seen, scan_count, status, notes)
        VALUES ('10.0.0.5','US','US',21,1,'','','2024-01-01','2024-01-01',1,'active','');
    """)
    cur = _make_db(CORE_SCHEMA)
    try:
        result = preview_merge(
            ext, cur, SchemaValidation,
            _db_merge.table_columns, _db_merge.table_has_required_columns,
        )
        assert result['valid'] is True
        warnings = result.get('warnings', [])
        assert any("Target DB missing" in w for w in warnings), \
            f"Expected 'Target DB missing' in warnings; got: {warnings}"
    finally:
        _cleanup(ext, cur)


# ---------------------------------------------------------------------------
# preview_csv_import
# ---------------------------------------------------------------------------

def test_preview_csv_import_file_not_found():
    cur = _make_db(CORE_SCHEMA)
    try:
        # Use a dummy analyze_fn that shouldn't be called
        result = preview_csv_import(
            "/nonexistent/file.csv", cur,
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
        )
        assert result['valid'] is False
        assert any("not found" in e for e in result['errors'])
    finally:
        _cleanup(cur)


def test_preview_csv_import_valid(tmp_path):
    # Write a minimal CSV and use a stub analyze_fn
    csv_path = str(tmp_path / "hosts.csv")
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['ip_address', 'country'])
        writer.writerow(['10.0.0.1', 'US'])
        writer.writerow(['10.0.0.2', 'US'])

    cur = _make_db(CORE_SCHEMA)

    def fake_analyze(path, conn, include_rows):
        return {
            'rows_total': 2, 'rows_valid': 2, 'rows_skipped': 0,
            'new_servers': 2, 'existing_servers': 0,
            'protocol_counts': {'S': 2},
            'errors': [], 'warnings': [],
        }

    try:
        result = preview_csv_import(csv_path, cur, fake_analyze)
        assert result['valid'] is True
        assert result['total_rows'] == 2
        assert result['new_servers'] == 2
        assert result['protocol_counts'] == {'S': 2}
    finally:
        _cleanup(cur)
