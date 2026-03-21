"""
Tests for DatabaseReader protocol-aware write helpers (Card 3).

Covers:
- Protocol isolation: S writes only SMB tables, F writes only FTP tables
- Same IP in both protocols: S write does not affect FTP state, and vice versa
- All four operation types: flags, probe cache, extracted flag, RCE status
- Compatibility shims: existing methods (upsert_user_flags etc.) still behave as SMB-only
- Edge cases: invalid host_type, IP missing from target protocol
- Snapshot path COALESCE preservation
- RCE status normalization (invalid → 'unknown')
- Resilience: missing FTP tables raise no exception (graceful degradation)
- OperationalError re-raise: non-missing-table errors always propagate
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.database_access import DatabaseReader


# ---------------------------------------------------------------------------
# Minimal schema helpers (copied from test_database_access_protocol_union.py)
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
    """Build a DatabaseReader with run_migrations and _ensure_rce_columns no-op'd."""
    monkeypatch.setattr("shared.db_migrations.run_migrations", lambda *a, **kw: None)
    reader = DatabaseReader(db_path=path)
    return reader


# ---------------------------------------------------------------------------
# Protocol isolation: flags
# ---------------------------------------------------------------------------


def test_flags_s_writes_only_smb_tables(monkeypatch):
    """S write → host_user_flags written; ftp_user_flags untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("1.2.3.4", "S", favorite=True)

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT favorite FROM host_user_flags WHERE server_id = "
            "(SELECT id FROM smb_servers WHERE ip_address='1.2.3.4')"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_user_flags").fetchone()[0]
        conn.close()

        assert smb_row is not None and smb_row[0] == 1
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_flags_f_writes_only_ftp_tables(monkeypatch):
    """F write → ftp_user_flags written; host_user_flags untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("1.2.3.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("1.2.3.4", "F", favorite=True)

        conn = sqlite3.connect(path)
        ftp_row = conn.execute(
            "SELECT favorite FROM ftp_user_flags WHERE server_id = "
            "(SELECT id FROM ftp_servers WHERE ip_address='1.2.3.4')"
        ).fetchone()
        smb_count = conn.execute("SELECT COUNT(*) FROM host_user_flags").fetchone()[0]
        conn.close()

        assert ftp_row is not None and ftp_row[0] == 1
        assert smb_count == 0
    finally:
        os.unlink(path)


def test_same_ip_s_write_does_not_affect_ftp_flags(monkeypatch):
    """Same IP in both tables: S write changes SMB row; pre-seeded FTP row unchanged."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.1", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.1", "active"))
        conn.execute("INSERT INTO ftp_user_flags (server_id, favorite, avoid, notes) VALUES (1, 0, 0, 'original')")
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("10.0.0.1", "S", favorite=True, notes="smb note")

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT favorite, notes FROM host_user_flags WHERE server_id=1"
        ).fetchone()
        ftp_row = conn.execute(
            "SELECT favorite, notes FROM ftp_user_flags WHERE server_id=1"
        ).fetchone()
        conn.close()

        assert smb_row is not None and smb_row[0] == 1 and smb_row[1] == "smb note"
        assert ftp_row[0] == 0 and ftp_row[1] == "original"
    finally:
        os.unlink(path)


def test_same_ip_f_write_does_not_affect_smb_flags(monkeypatch):
    """Same IP in both tables: F write changes FTP row; pre-seeded SMB row unchanged."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.1", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("10.0.0.1", "active"))
        conn.execute("INSERT INTO host_user_flags (server_id, favorite, notes) VALUES (1, 1, 'smb data')")
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("10.0.0.1", "F", avoid=True)

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT favorite, notes FROM host_user_flags WHERE server_id=1"
        ).fetchone()
        ftp_row = conn.execute(
            "SELECT avoid FROM ftp_user_flags WHERE server_id=1"
        ).fetchone()
        conn.close()

        assert smb_row[0] == 1 and smb_row[1] == "smb data"
        assert ftp_row is not None and ftp_row[0] == 1
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Protocol isolation: probe cache, extracted, RCE
# ---------------------------------------------------------------------------


def test_probe_protocol_isolation(monkeypatch):
    """Same IP: S probe → host_probe_cache; F probe → ftp_probe_cache with different values."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("5.5.5.5", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("5.5.5.5", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_probe_cache_for_host("5.5.5.5", "S", status="probed", indicator_matches=3)
        reader.upsert_probe_cache_for_host("5.5.5.5", "F", status="clean", indicator_matches=0)

        conn = sqlite3.connect(path)
        smb_cache = conn.execute(
            "SELECT status, indicator_matches FROM host_probe_cache WHERE server_id=1"
        ).fetchone()
        ftp_cache = conn.execute(
            "SELECT status, indicator_matches FROM ftp_probe_cache WHERE server_id=1"
        ).fetchone()
        conn.close()

        assert smb_cache[0] == "probed" and smb_cache[1] == 3
        assert ftp_cache[0] == "clean" and ftp_cache[1] == 0
    finally:
        os.unlink(path)


def test_extracted_protocol_isolation(monkeypatch):
    """S extracted write → host_probe_cache.extracted=1; ftp_probe_cache untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("6.6.6.6", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("6.6.6.6", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_extracted_flag_for_host("6.6.6.6", "S", extracted=True)

        conn = sqlite3.connect(path)
        smb_extracted = conn.execute(
            "SELECT extracted FROM host_probe_cache WHERE server_id=1"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_probe_cache").fetchone()[0]
        conn.close()

        assert smb_extracted is not None and smb_extracted[0] == 1
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_rce_protocol_isolation(monkeypatch):
    """S rce='flagged' → host_probe_cache; F rce='clean' → ftp_probe_cache."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (id, ip_address, status) VALUES (1,?,?)", ("7.7.7.7", "active"))
        conn.execute("INSERT INTO ftp_servers (id, ip_address, status) VALUES (1,?,?)", ("7.7.7.7", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_rce_status_for_host("7.7.7.7", "S", "flagged", '{"count":1}')
        reader.upsert_rce_status_for_host("7.7.7.7", "F", "clean", None)

        conn = sqlite3.connect(path)
        smb_rce = conn.execute(
            "SELECT rce_status, rce_verdict_summary FROM host_probe_cache WHERE server_id=1"
        ).fetchone()
        ftp_rce = conn.execute(
            "SELECT rce_status, rce_verdict_summary FROM ftp_probe_cache WHERE server_id=1"
        ).fetchone()
        conn.close()

        assert smb_rce[0] == "flagged" and smb_rce[1] == '{"count":1}'
        assert ftp_rce[0] == "clean" and ftp_rce[1] is None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Compatibility shims: all four existing methods default to SMB
# ---------------------------------------------------------------------------


def test_wrapper_upsert_user_flags_defaults_to_smb(monkeypatch):
    """Old upsert_user_flags signature writes SMB only; ftp_user_flags untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("8.8.8.8", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags("8.8.8.8", favorite=True, notes="legacy call")

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT favorite, notes FROM host_user_flags WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='8.8.8.8')"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_user_flags").fetchone()[0]
        conn.close()

        assert smb_row is not None and smb_row[0] == 1 and smb_row[1] == "legacy call"
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_wrapper_upsert_probe_cache_defaults_to_smb(monkeypatch):
    """Old upsert_probe_cache signature writes SMB only; ftp_probe_cache untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("8.8.8.8", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_probe_cache("8.8.8.8", status="probed", indicator_matches=2)

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT status FROM host_probe_cache WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='8.8.8.8')"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_probe_cache").fetchone()[0]
        conn.close()

        assert smb_row is not None and smb_row[0] == "probed"
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_wrapper_upsert_extracted_flag_defaults_to_smb(monkeypatch):
    """Old upsert_extracted_flag signature writes SMB only; ftp_probe_cache untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("8.8.8.8", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_extracted_flag("8.8.8.8", True)

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT extracted FROM host_probe_cache WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='8.8.8.8')"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_probe_cache").fetchone()[0]
        conn.close()

        assert smb_row is not None and smb_row[0] == 1
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_wrapper_upsert_rce_status_defaults_to_smb(monkeypatch):
    """Old upsert_rce_status signature writes SMB only; ftp_probe_cache untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("8.8.8.8", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_rce_status("8.8.8.8", "flagged", '{"x":1}')

        conn = sqlite3.connect(path)
        smb_row = conn.execute(
            "SELECT rce_status FROM host_probe_cache WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='8.8.8.8')"
        ).fetchone()
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_probe_cache").fetchone()[0]
        conn.close()

        assert smb_row is not None and smb_row[0] == "flagged"
        assert ftp_count == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_invalid_host_type_no_write(monkeypatch):
    """host_type='X' → no-op; both tables untouched."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("9.9.9.9", "active"))
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("9.9.9.9", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("9.9.9.9", "X", favorite=True)

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_user_flags").fetchone()[0]
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_user_flags").fetchone()[0]
        conn.close()

        assert smb_count == 0
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_missing_target_no_cross_write(monkeypatch):
    """IP only in ftp_servers; host_type='S' → SMB miss, no FTP cross-write."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("10.10.10.10", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("10.10.10.10", "S", favorite=True)

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_user_flags").fetchone()[0]
        ftp_count = conn.execute("SELECT COUNT(*) FROM ftp_user_flags").fetchone()[0]
        conn.close()

        assert smb_count == 0
        assert ftp_count == 0
    finally:
        os.unlink(path)


def test_snapshot_path_coalesce_preserved(monkeypatch):
    """Write probe with snapshot_path; update status without snapshot_path; snapshot_path preserved."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("11.11.11.11", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_probe_cache_for_host(
            "11.11.11.11", "S", status="probed", indicator_matches=1, snapshot_path="/tmp/snap.json"
        )
        # Update without providing snapshot_path — should not overwrite existing value
        reader.upsert_probe_cache_for_host(
            "11.11.11.11", "S", status="clean", indicator_matches=0, snapshot_path=None
        )

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT status, snapshot_path FROM host_probe_cache WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='11.11.11.11')"
        ).fetchone()
        conn.close()

        assert row[0] == "clean"
        assert row[1] == "/tmp/snap.json"
    finally:
        os.unlink(path)


def test_ftp_probe_writes_accessible_dirs_fields(monkeypatch):
    """FTP probe cache write stores accessible directory count/list when provided."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO ftp_servers (ip_address, status) VALUES (?,?)", ("13.13.13.13", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_probe_cache_for_host(
            "13.13.13.13",
            "F",
            status="clean",
            indicator_matches=0,
            snapshot_path="/tmp/ftp_probe.json",
            accessible_dirs_count=2,
            accessible_dirs_list="pub,incoming",
        )

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT status, accessible_dirs_count, accessible_dirs_list FROM ftp_probe_cache "
            "WHERE server_id=(SELECT id FROM ftp_servers WHERE ip_address='13.13.13.13')"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "clean"
        assert row[1] == 2
        assert row[2] == "pub,incoming"
    finally:
        os.unlink(path)


def test_rce_invalid_status_normalized_to_unknown(monkeypatch):
    """Invalid rce_status value → stored as 'unknown' (normalization regression test)."""
    path = _make_db(smb=True, ftp=True)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("12.12.12.12", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_rce_status_for_host("12.12.12.12", "S", "GARBAGE_STATUS")

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT rce_status FROM host_probe_cache WHERE server_id="
            "(SELECT id FROM smb_servers WHERE ip_address='12.12.12.12')"
        ).fetchone()
        conn.close()

        assert row is not None and row[0] == "unknown"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Resilience: missing FTP tables (one per _for_host method)
# ---------------------------------------------------------------------------


def test_flags_for_host_ftp_missing_tables_no_exception(monkeypatch):
    """SMB-only DB; upsert_user_flags_for_host('F') → no exception; SMB table untouched."""
    path = _make_db(smb=True, ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.1.1.1", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_user_flags_for_host("1.1.1.1", "F", favorite=True)  # must not raise

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_user_flags").fetchone()[0]
        conn.close()

        assert smb_count == 0
    finally:
        os.unlink(path)


def test_probe_for_host_ftp_missing_tables_no_exception(monkeypatch):
    """SMB-only DB; upsert_probe_cache_for_host('F') → no exception; SMB table untouched."""
    path = _make_db(smb=True, ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.1.1.2", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_probe_cache_for_host("1.1.1.2", "F", status="probed", indicator_matches=1)

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_probe_cache").fetchone()[0]
        conn.close()

        assert smb_count == 0
    finally:
        os.unlink(path)


def test_extracted_for_host_ftp_missing_tables_no_exception(monkeypatch):
    """SMB-only DB; upsert_extracted_flag_for_host('F') → no exception; SMB table untouched."""
    path = _make_db(smb=True, ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.1.1.3", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_extracted_flag_for_host("1.1.1.3", "F", extracted=True)

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_probe_cache").fetchone()[0]
        conn.close()

        assert smb_count == 0
    finally:
        os.unlink(path)


def test_rce_for_host_ftp_missing_tables_no_exception(monkeypatch):
    """SMB-only DB; upsert_rce_status_for_host('F') → no exception; SMB table untouched."""
    path = _make_db(smb=True, ftp=False)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smb_servers (ip_address, status) VALUES (?,?)", ("1.1.1.4", "active"))
        conn.commit()
        conn.close()

        reader = _reader(path, monkeypatch)
        reader.upsert_rce_status_for_host("1.1.1.4", "F", "flagged")

        conn = sqlite3.connect(path)
        smb_count = conn.execute("SELECT COUNT(*) FROM host_probe_cache").fetchone()[0]
        conn.close()

        assert smb_count == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# OperationalError re-raise: non-missing-table errors always propagate
# ---------------------------------------------------------------------------


def test_operational_error_on_smb_path_is_reraised(monkeypatch):
    """OperationalError('database is locked') on SMB path always propagates."""
    path = _make_db(smb=True, ftp=True)
    try:
        reader = _reader(path, monkeypatch)

        @contextmanager
        def _broken_conn():
            raise sqlite3.OperationalError("database is locked")
            yield  # unreachable; satisfies contextmanager

        monkeypatch.setattr(reader, "_get_connection", _broken_conn)

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            reader.upsert_user_flags_for_host("1.2.3.4", "S", favorite=True)
    finally:
        os.unlink(path)


def test_non_missing_table_ftp_error_is_reraised(monkeypatch):
    """OperationalError('database is locked') on FTP path always propagates (not 'no such table: ftp_')."""
    path = _make_db(smb=True, ftp=True)
    try:
        reader = _reader(path, monkeypatch)

        @contextmanager
        def _broken_conn():
            raise sqlite3.OperationalError("database is locked")
            yield

        monkeypatch.setattr(reader, "_get_connection", _broken_conn)

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            reader.upsert_user_flags_for_host("1.2.3.4", "F", favorite=True)
    finally:
        os.unlink(path)


def test_ftp_non_ftp_table_error_is_reraised(monkeypatch):
    """OperationalError('no such table: host_probe_cache') on FTP path propagates.
    Only 'no such table: ftp_' is suppressed; wrong-table errors are bugs and must surface."""
    path = _make_db(smb=True, ftp=True)
    try:
        reader = _reader(path, monkeypatch)

        @contextmanager
        def _broken_conn():
            raise sqlite3.OperationalError("no such table: host_probe_cache")
            yield

        monkeypatch.setattr(reader, "_get_connection", _broken_conn)

        with pytest.raises(sqlite3.OperationalError, match="no such table: host_probe_cache"):
            reader.upsert_user_flags_for_host("1.2.3.4", "F", favorite=True)
    finally:
        os.unlink(path)
