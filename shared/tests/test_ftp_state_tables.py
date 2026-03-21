"""
Tests for Card 1: ftp_user_flags and ftp_probe_cache migration.

Covers:
- Fresh DB gets both new tables with all expected columns.
- Existing DB without the new tables is upgraded in-place.
- Legacy ftp_probe_cache (realistic shape, missing parity columns) gets backfilled.
- Running migrations twice is idempotent.
- SMB data (smb_servers, host_user_flags, host_probe_cache) is preserved across migration.
- FK cascade deletes child rows when parent ftp_servers row is removed.
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from shared.db_migrations import run_migrations
from shared.database import FtpPersistence
from commands.ftp.models import FtpAccessOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASELINE_DDL = """
CREATE TABLE IF NOT EXISTS scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type TEXT NOT NULL,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'running',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ftp_servers (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    ip_address      TEXT     NOT NULL UNIQUE,
    country         TEXT,
    country_code    TEXT,
    port            INTEGER  NOT NULL DEFAULT 21,
    anon_accessible BOOLEAN  NOT NULL DEFAULT FALSE,
    banner          TEXT,
    shodan_data     TEXT,
    first_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count      INTEGER  DEFAULT 1,
    status          TEXT     DEFAULT 'active',
    notes           TEXT,
    updated_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_baseline_db(path: Path) -> None:
    """Create minimum required tables so run_migrations has valid FK context."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_BASELINE_DDL)
        conn.commit()
    finally:
        conn.close()


def _table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _column_names(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests: fresh DB
# ---------------------------------------------------------------------------

def test_fresh_db_has_ftp_user_flags(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(str(db))
    assert "ftp_user_flags" in _table_names(db)


def test_fresh_db_has_ftp_probe_cache(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(str(db))
    assert "ftp_probe_cache" in _table_names(db)


def test_ftp_probe_cache_has_all_columns(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(str(db))
    cols = _column_names(db, "ftp_probe_cache")
    assert {"server_id", "status", "last_probe_at", "indicator_matches",
            "indicator_samples", "snapshot_path", "extracted",
            "rce_status", "rce_verdict_summary", "updated_at",
            "accessible_dirs_count", "accessible_dirs_list"} <= cols


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------

def test_migration_idempotent(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(str(db))
    run_migrations(str(db))  # second run must not raise or corrupt
    assert "ftp_user_flags" in _table_names(db)
    assert "ftp_probe_cache" in _table_names(db)
    cols = _column_names(db, "ftp_probe_cache")
    assert {"extracted", "rce_status", "rce_verdict_summary",
            "accessible_dirs_count", "accessible_dirs_list"} <= cols


# ---------------------------------------------------------------------------
# Tests: legacy servers table compatibility
# ---------------------------------------------------------------------------

def test_legacy_servers_table_backfills_smb_servers(tmp_path):
    """
    Legacy DBs that only have a 'servers' table should auto-create smb_servers
    and import rows so dashboard/server-list queries don't fail at startup.
    """
    db = tmp_path / "legacy.db"

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL UNIQUE,
                country TEXT,
                country_code TEXT,
                auth_method TEXT,
                last_seen DATETIME,
                scan_count INTEGER,
                status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO servers (ip_address, country, country_code, auth_method, last_seen, scan_count, status)
            VALUES ('203.0.113.10', 'US', 'US', 'anonymous', '2026-03-01 10:00:00', 5, 'active')
            """
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db))
    run_migrations(str(db))  # idempotent second run should not duplicate rows

    tables = _table_names(db)
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            """
            SELECT ip_address, country, country_code, auth_method, scan_count, status, host_type
            FROM smb_servers
            WHERE ip_address = '203.0.113.10'
            """
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM smb_servers WHERE ip_address = '203.0.113.10'").fetchone()[0]
    finally:
        conn.close()

    assert {"scan_sessions", "smb_servers", "share_access", "file_manifests", "vulnerabilities", "failure_logs"} <= tables
    assert row == ("203.0.113.10", "US", "US", "anonymous", 5, "active", "S")
    assert count == 1


# ---------------------------------------------------------------------------
# Tests: explicit protocol identity columns
# ---------------------------------------------------------------------------

def test_host_type_columns_exist_after_migration(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)
    run_migrations(str(db))

    smb_cols = _column_names(db, "smb_servers")
    ftp_cols = _column_names(db, "ftp_servers")
    assert "host_type" in smb_cols
    assert "host_type" in ftp_cols


def test_existing_rows_backfilled_host_type(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("INSERT INTO smb_servers (ip_address, country) VALUES (?, ?)", ("10.10.10.10", "US"))
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, country_code, status) VALUES (?, ?, ?)",
            ("10.10.10.11", "US", "active"),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    try:
        smb_type = conn.execute(
            "SELECT host_type FROM smb_servers WHERE ip_address = ?",
            ("10.10.10.10",),
        ).fetchone()[0]
        ftp_type = conn.execute(
            "SELECT host_type FROM ftp_servers WHERE ip_address = ?",
            ("10.10.10.11",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert smb_type == "S"
    assert ftp_type == "F"


# ---------------------------------------------------------------------------
# Tests: existing DB upgrade (legacy ftp_probe_cache shape)
# ---------------------------------------------------------------------------

def test_existing_db_upgrade(tmp_path):
    """
    Simulate a DB that has ftp_probe_cache without the three parity columns.
    The realistic legacy shape includes all base columns but omits
    extracted / rce_status / rce_verdict_summary.
    """
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    # Create legacy-shaped ftp_probe_cache (missing parity columns)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE ftp_probe_cache (
                server_id         INTEGER  PRIMARY KEY,
                status            TEXT     DEFAULT 'unprobed',
                last_probe_at     DATETIME,
                indicator_matches INTEGER  DEFAULT 0,
                indicator_samples TEXT,
                snapshot_path     TEXT,
                updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db))

    cols = _column_names(db, "ftp_probe_cache")
    assert "extracted" in cols, "extracted column should be backfilled"
    assert "rce_status" in cols, "rce_status column should be backfilled"
    assert "rce_verdict_summary" in cols, "rce_verdict_summary column should be backfilled"
    assert "accessible_dirs_count" in cols, "accessible_dirs_count column should be backfilled"
    assert "accessible_dirs_list" in cols, "accessible_dirs_list column should be backfilled"


# ---------------------------------------------------------------------------
# Tests: SMB data preservation
# ---------------------------------------------------------------------------

def test_smb_data_preserved(tmp_path):
    """
    Sentinel SMB rows inserted before migration must be intact afterward.
    """
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    # Pre-seed SMB data that run_migrations must not touch
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO smb_servers (ip_address, country) VALUES ('10.0.0.1', 'US')"
        )
        conn.commit()
        smb_id = conn.execute(
            "SELECT id FROM smb_servers WHERE ip_address='10.0.0.1'"
        ).fetchone()[0]

        # host_user_flags and host_probe_cache are created by run_migrations itself,
        # so we insert into them after the first migration pass.
    finally:
        conn.close()

    # First pass creates the SMB state tables
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO host_user_flags (server_id, favorite, avoid) VALUES (?, 1, 0)",
            (smb_id,),
        )
        conn.execute(
            "INSERT INTO host_probe_cache (server_id, status) VALUES (?, 'probed')",
            (smb_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Second pass (simulates app restart) must not disturb SMB rows
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT ip_address, country FROM smb_servers WHERE id=?", (smb_id,)
        ).fetchone()
        assert row == ("10.0.0.1", "US"), "smb_servers sentinel row changed"

        flag_row = conn.execute(
            "SELECT favorite, avoid FROM host_user_flags WHERE server_id=?", (smb_id,)
        ).fetchone()
        assert flag_row == (1, 0), "host_user_flags sentinel row changed"

        probe_row = conn.execute(
            "SELECT status FROM host_probe_cache WHERE server_id=?", (smb_id,)
        ).fetchone()
        assert probe_row == ("probed",), "host_probe_cache sentinel row changed"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests: FK cascade
# ---------------------------------------------------------------------------

def test_fk_cascade_ftp_user_flags(tmp_path):
    """Deleting an ftp_servers row must cascade-delete its ftp_user_flags row."""
    db = tmp_path / "test.db"
    _make_baseline_db(db)  # scan_sessions must exist for ftp_access FK when FK enforcement is ON
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, port, anon_accessible) VALUES ('192.0.2.1', 21, 0)"
        )
        conn.commit()
        ftp_id = conn.execute(
            "SELECT id FROM ftp_servers WHERE ip_address='192.0.2.1'"
        ).fetchone()[0]

        conn.execute(
            "INSERT INTO ftp_user_flags (server_id, favorite) VALUES (?, 1)", (ftp_id,)
        )
        conn.commit()

        conn.execute("DELETE FROM ftp_servers WHERE id=?", (ftp_id,))
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM ftp_user_flags WHERE server_id=?", (ftp_id,)
        ).fetchone()[0]
        assert remaining == 0, "ftp_user_flags row should be cascade-deleted"
    finally:
        conn.close()


def test_fk_cascade_ftp_probe_cache(tmp_path):
    """Deleting an ftp_servers row must cascade-delete its ftp_probe_cache row."""
    db = tmp_path / "test.db"
    _make_baseline_db(db)  # scan_sessions must exist for ftp_access FK when FK enforcement is ON
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO ftp_servers (ip_address, port, anon_accessible) VALUES ('192.0.2.2', 21, 0)"
        )
        conn.commit()
        ftp_id = conn.execute(
            "SELECT id FROM ftp_servers WHERE ip_address='192.0.2.2'"
        ).fetchone()[0]

        conn.execute(
            "INSERT INTO ftp_probe_cache (server_id, status) VALUES (?, 'probed')",
            (ftp_id,),
        )
        conn.commit()

        conn.execute("DELETE FROM ftp_servers WHERE id=?", (ftp_id,))
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM ftp_probe_cache WHERE server_id=?", (ftp_id,)
        ).fetchone()[0]
        assert remaining == 0, "ftp_probe_cache row should be cascade-deleted"
    finally:
        conn.close()


def test_access_batch_persists_ftp_visible_share_fields(tmp_path):
    """persist_access_outcomes_batch should populate ftp_probe_cache visible count/list fields."""
    db = tmp_path / "test.db"
    _make_baseline_db(db)
    run_migrations(str(db))

    persistence = FtpPersistence(str(db))
    outcome = FtpAccessOutcome(
        ip="198.51.100.10",
        country="US",
        country_code="US",
        port=21,
        banner="220 FTP ready",
        shodan_data="{}",
        accessible=True,
        auth_status="anonymous",
        root_listing_available=True,
        root_entry_count=2,
        error_message="",
        access_details=json.dumps({
            "reason": "anonymous",
            "banner": "220 FTP ready",
            "root_entries": ["pub", "incoming"],
        }),
    )
    persistence.persist_access_outcomes_batch([outcome])

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            """
            SELECT pc.accessible_dirs_count, pc.accessible_dirs_list
            FROM ftp_probe_cache pc
            JOIN ftp_servers s ON s.id = pc.server_id
            WHERE s.ip_address = ?
            """,
            ("198.51.100.10",),
        ).fetchone()
        assert row == (2, "pub,incoming")
    finally:
        conn.close()
