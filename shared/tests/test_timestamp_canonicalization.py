"""
Tests for Card 2.5: Timestamp Canonicalization.

Covers:
- get_standard_timestamp() produces canonical format (no T, UTC).
- normalize_db_timestamp() handles T-format, microseconds, Z suffix, offsets.
- Startup migration converts existing T-format rows in smb_servers/ftp_servers.
- Migration is idempotent and preserves already-canonical rows.
- recent_scan_only filtering works correctly after normalization.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from shared.config import get_standard_timestamp, normalize_db_timestamp
from shared.db_migrations import run_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

_BASELINE_DDL = """
CREATE TABLE IF NOT EXISTS scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type TEXT NOT NULL DEFAULT 'test',
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'running',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS smb_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address  TEXT    NOT NULL UNIQUE,
    country     TEXT,
    first_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count  INTEGER DEFAULT 1,
    status      TEXT    DEFAULT 'active',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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


def _make_baseline_db(path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_BASELINE_DDL)
        conn.commit()
    finally:
        conn.close()


def _fetch_one(path, table: str, ip: str) -> dict:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE ip_address = ?", (ip,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_standard_timestamp()
# ---------------------------------------------------------------------------


def test_get_standard_timestamp_no_T():
    ts = get_standard_timestamp()
    assert "T" not in ts, f"Expected no T-separator, got: {ts!r}"


def test_get_standard_timestamp_format():
    ts = get_standard_timestamp()
    assert _CANONICAL_RE.match(ts), f"Expected YYYY-MM-DD HH:MM:SS, got: {ts!r}"


# ---------------------------------------------------------------------------
# normalize_db_timestamp()
# ---------------------------------------------------------------------------


def test_normalize_already_canonical():
    assert normalize_db_timestamp("2025-01-21 14:20:05") == "2025-01-21 14:20:05"


def test_normalize_T_format():
    assert normalize_db_timestamp("2025-01-21T14:20:05") == "2025-01-21 14:20:05"


def test_normalize_microseconds():
    assert normalize_db_timestamp("2025-01-21T14:20:05.123456") == "2025-01-21 14:20:05"


def test_normalize_Z_suffix():
    assert normalize_db_timestamp("2025-01-21T14:20:05Z") == "2025-01-21 14:20:05"


def test_normalize_positive_offset():
    # +05:30 → subtract 5h30m from 14:20 → 08:50
    assert normalize_db_timestamp("2025-01-21T14:20:05+05:30") == "2025-01-21 08:50:05"


def test_normalize_negative_offset():
    # -05:00 → add 5h to 09:00 → 14:00
    assert normalize_db_timestamp("2025-01-21T09:00:00-05:00") == "2025-01-21 14:00:00"


def test_normalize_passthrough_none():
    assert normalize_db_timestamp(None) is None


def test_normalize_passthrough_non_string():
    assert normalize_db_timestamp(12345) == 12345


def test_normalize_empty_string():
    # Empty strings pass through unchanged
    assert normalize_db_timestamp("") == ""


def test_normalize_microseconds_with_offset():
    # +05:30 offset on a timestamp with microseconds
    result = normalize_db_timestamp("2025-01-21T14:20:05.123456+05:30")
    assert result == "2025-01-21 08:50:05"


# ---------------------------------------------------------------------------
# Migration: converts T-format rows
# ---------------------------------------------------------------------------


def test_migration_converts_T_smb(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen) VALUES (?,?,?)",
        ("1.1.1.1", "2025-01-21T14:20:05", "2025-01-21T15:30:00"),
    )
    conn.commit()
    conn.close()

    run_migrations(str(db))

    row = _fetch_one(db, "smb_servers", "1.1.1.1")
    assert "T" not in row["first_seen"], f"first_seen still has T: {row['first_seen']!r}"
    assert "T" not in row["last_seen"], f"last_seen still has T: {row['last_seen']!r}"
    assert row["first_seen"] == "2025-01-21 14:20:05"
    assert row["last_seen"] == "2025-01-21 15:30:00"


def test_migration_converts_T_ftp(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO ftp_servers (ip_address, first_seen, last_seen) VALUES (?,?,?)",
        ("2.2.2.2", "2025-06-01T08:00:00", "2025-06-01T09:00:00"),
    )
    conn.commit()
    conn.close()

    run_migrations(str(db))

    row = _fetch_one(db, "ftp_servers", "2.2.2.2")
    assert row["first_seen"] == "2025-06-01 08:00:00"
    assert row["last_seen"] == "2025-06-01 09:00:00"


def test_migration_handles_microseconds(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen) VALUES (?,?,?)",
        ("3.3.3.3", "2025-01-21T14:20:05.123456", "2025-01-21T14:20:05.999999"),
    )
    conn.commit()
    conn.close()

    run_migrations(str(db))

    row = _fetch_one(db, "smb_servers", "3.3.3.3")
    assert row["first_seen"] == "2025-01-21 14:20:05"
    assert row["last_seen"] == "2025-01-21 14:20:05"


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen) VALUES (?,?,?)",
        ("4.4.4.4", "2025-01-21T14:20:05", "2025-01-21T14:20:05"),
    )
    conn.commit()
    conn.close()

    run_migrations(str(db))
    run_migrations(str(db))  # second run must not corrupt data

    row = _fetch_one(db, "smb_servers", "4.4.4.4")
    assert row["first_seen"] == "2025-01-21 14:20:05"
    assert row["last_seen"] == "2025-01-21 14:20:05"


def test_migration_preserves_clean_rows(tmp_path):
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen) VALUES (?,?,?)",
        ("5.5.5.5", "2025-01-21 14:20:05", "2025-01-21 15:00:00"),
    )
    conn.commit()
    conn.close()

    run_migrations(str(db))

    row = _fetch_one(db, "smb_servers", "5.5.5.5")
    assert row["first_seen"] == "2025-01-21 14:20:05"
    assert row["last_seen"] == "2025-01-21 15:00:00"


# ---------------------------------------------------------------------------
# recent_scan_only filtering still works after normalization
# ---------------------------------------------------------------------------


def test_recent_scan_only_with_T_format_rows(tmp_path):
    """
    Insert T-format rows (one recent, one old), run migration, then verify
    that the normalized timestamps allow correct recent_scan_only filtering
    via SQLite datetime() comparison.
    """
    db = tmp_path / "test.db"
    _make_baseline_db(db)

    conn = sqlite3.connect(str(db))
    # Recent row (now) in T-format
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen, status) "
        "VALUES (?,datetime('now'),datetime('now'),?)",
        ("10.0.0.1", "active"),
    )
    # Old row (3 days ago) in T-format — simulate pre-migration data
    conn.execute(
        "INSERT INTO smb_servers (ip_address, first_seen, last_seen, status) "
        "VALUES (?,datetime('now','-3 days'),datetime('now','-3 days'),?)",
        ("10.0.0.2", "active"),
    )
    conn.commit()
    conn.close()

    # Simulate T-format by manually overwriting with isoformat-style string
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE smb_servers SET last_seen = REPLACE(last_seen, ' ', 'T') "
        "WHERE ip_address IN ('10.0.0.1','10.0.0.2')"
    )
    conn.commit()
    conn.close()

    # Run migration to normalize
    run_migrations(str(db))

    # Verify T-format is gone
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT ip_address, last_seen FROM smb_servers").fetchall()
    conn.close()
    for ip, ts in rows:
        assert "T" not in ts, f"{ip}: T still present after migration: {ts!r}"

    # Verify recent filter works: only 10.0.0.1 should be "recent"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cutoff_row = conn.execute(
        "SELECT MAX(datetime(last_seen)) AS cutoff FROM smb_servers WHERE status='active'"
    ).fetchone()
    cutoff = cutoff_row["cutoff"]
    recent = conn.execute(
        "SELECT ip_address FROM smb_servers "
        "WHERE datetime(last_seen) >= datetime(?, '-1 hour') AND status='active'",
        (cutoff,),
    ).fetchall()
    conn.close()

    recent_ips = {r[0] for r in recent}
    assert "10.0.0.1" in recent_ips, "Recent row should pass filter"
    assert "10.0.0.2" not in recent_ips, "Old row should not pass filter"
