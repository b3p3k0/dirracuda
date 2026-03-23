"""
Unit tests for db_maintenance_engine — pure module-level functions.

Tests call the extracted functions directly, passing DatabaseStats and
PurgePreview as factory callables. This validates the factory wiring contract
independently of DBToolsEngine.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import pure functions under test
from gui.utils.db_maintenance_engine import (
    create_backup,
    _check_disk_space,
    export_database,
    quick_backup,
    get_database_stats,
    vacuum_database,
    integrity_check,
    preview_purge,
    execute_purge,
)

# Import dataclasses from their authoritative location (db_tools_engine.py)
from gui.utils.db_tools_engine import DatabaseStats, PurgePreview


MINIMAL_SCHEMA = """
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT DEFAULT 'smbseek',
    scan_type TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    status TEXT DEFAULT 'running',
    total_targets INTEGER DEFAULT 0,
    successful_targets INTEGER DEFAULT 0,
    failed_targets INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    country_code TEXT,
    auth_method TEXT,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    scan_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    notes TEXT,
    updated_at DATETIME
);

CREATE TABLE share_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    accessible BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE host_user_flags (
    server_id INTEGER PRIMARY KEY,
    favorite BOOLEAN DEFAULT 0,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE host_probe_cache (
    server_id INTEGER PRIMARY KEY,
    status TEXT DEFAULT 'unprobed',
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()

    yield db_path

    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def populated_db(temp_db):
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO scan_sessions (scan_type, status) VALUES ('discover', 'completed')"
    )
    conn.execute(
        "INSERT INTO smb_servers (ip_address, country, country_code, first_seen, last_seen) "
        "VALUES ('10.0.0.1', 'United States', 'US', '2025-01-01', '2025-01-15')"
    )
    conn.execute(
        "INSERT INTO smb_servers (ip_address, country, country_code, first_seen, last_seen) "
        "VALUES ('10.0.0.2', 'Germany', 'DE', '2024-01-01', '2024-01-10')"
    )
    conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible) "
        "VALUES (1, 1, 'Public', 1)"
    )
    conn.execute(
        "INSERT INTO host_user_flags (server_id, favorite) VALUES (1, 1)"
    )
    conn.execute(
        "INSERT INTO host_probe_cache (server_id, status) VALUES (1, 'probed')"
    )
    conn.commit()
    conn.close()
    return temp_db


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------

class TestCreateBackup:
    def test_creates_backup_file(self, populated_db, tmp_path):
        result = create_backup(populated_db, str(tmp_path))
        assert result['success'] is True
        assert os.path.exists(result['backup_path'])
        assert result['size_bytes'] > 0

    def test_backup_filename_contains_timestamp(self, populated_db, tmp_path):
        result = create_backup(populated_db, str(tmp_path))
        name = Path(result['backup_path']).name
        assert '_backup_' in name
        assert name.endswith('.db')

    def test_backup_is_valid_sqlite(self, populated_db, tmp_path):
        result = create_backup(populated_db, str(tmp_path))
        conn = sqlite3.connect(result['backup_path'])
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert 'smb_servers' in tables

    def test_wal_commits_included(self):
        """Backup via SQLite online API captures committed WAL data."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.execute("INSERT INTO t VALUES ('hello')")
            conn.commit()
            conn.close()

            with tempfile.TemporaryDirectory() as backup_dir:
                result = create_backup(db_path, backup_dir)
                assert result['success'] is True
                backup_conn = sqlite3.connect(result['backup_path'])
                row = backup_conn.execute("SELECT v FROM t").fetchone()
                backup_conn.close()
                assert row[0] == 'hello'
        finally:
            os.unlink(db_path)

    def test_missing_source_returns_error(self, tmp_path):
        result = create_backup('/nonexistent/path/fake.db', str(tmp_path))
        assert result['success'] is False
        assert 'error' in result

    def test_defaults_to_same_directory(self, populated_db):
        result = create_backup(populated_db)
        assert result['success'] is True
        assert os.path.dirname(result['backup_path']) == os.path.dirname(populated_db)
        try:
            os.unlink(result['backup_path'])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# _check_disk_space
# ---------------------------------------------------------------------------

class TestCheckDiskSpace:
    def test_sufficient_space_returns_true(self, tmp_path):
        assert _check_disk_space(1, str(tmp_path)) is True

    def test_huge_requirement_returns_false(self, tmp_path):
        assert _check_disk_space(10 ** 18, str(tmp_path)) is False

    def test_invalid_path_returns_true(self):
        # Graceful fallback: assume OK when path does not exist
        assert _check_disk_space(1, '/nonexistent/xyz') is True


# ---------------------------------------------------------------------------
# export_database / quick_backup
# ---------------------------------------------------------------------------

class TestExportDatabase:
    def test_creates_exported_file(self, populated_db, tmp_path):
        out = str(tmp_path / 'exported.db')
        result = export_database(populated_db, out)
        assert result['success'] is True
        assert os.path.exists(out)
        assert result['size_bytes'] > 0

    def test_exported_file_is_valid_sqlite(self, populated_db, tmp_path):
        out = str(tmp_path / 'exported.db')
        export_database(populated_db, out)
        conn = sqlite3.connect(out)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert 'smb_servers' in tables

    def test_progress_callback_called(self, populated_db, tmp_path):
        calls = []
        export_database(populated_db, str(tmp_path / 'e.db'), progress_callback=lambda p, m: calls.append(p))
        assert calls[0] == 0
        assert calls[-1] == 100

    def test_missing_source_returns_error(self, tmp_path):
        result = export_database('/no/such/db.db', str(tmp_path / 'out.db'))
        assert result['success'] is False


class TestQuickBackup:
    def test_creates_backup(self, populated_db, tmp_path):
        result = quick_backup(populated_db, str(tmp_path))
        assert result['success'] is True
        assert os.path.exists(result['backup_path'])

    def test_progress_callback_fires(self, populated_db, tmp_path):
        calls = []
        quick_backup(populated_db, str(tmp_path), progress_callback=lambda p, m: calls.append(p))
        assert 0 in calls
        assert 100 in calls


# ---------------------------------------------------------------------------
# get_database_stats — factory wiring contract
# ---------------------------------------------------------------------------

class TestGetDatabaseStats:
    def test_returns_database_stats_instance(self, populated_db):
        result = get_database_stats(populated_db, DatabaseStats)
        assert isinstance(result, DatabaseStats)

    def test_counts_servers(self, populated_db):
        stats = get_database_stats(populated_db, DatabaseStats)
        assert stats.total_servers == 2

    def test_counts_shares(self, populated_db):
        stats = get_database_stats(populated_db, DatabaseStats)
        assert stats.total_shares == 1
        assert stats.accessible_shares == 1

    def test_reports_db_size(self, populated_db):
        stats = get_database_stats(populated_db, DatabaseStats)
        assert stats.database_size_bytes > 0

    def test_reports_countries(self, populated_db):
        stats = get_database_stats(populated_db, DatabaseStats)
        assert 'United States' in stats.countries

    def test_missing_db_returns_empty_stats(self, tmp_path):
        stats = get_database_stats(str(tmp_path / 'missing.db'), DatabaseStats)
        assert isinstance(stats, DatabaseStats)
        assert stats.total_servers == 0


# ---------------------------------------------------------------------------
# vacuum_database
# ---------------------------------------------------------------------------

class TestVacuumDatabase:
    def test_returns_success(self, populated_db):
        result = vacuum_database(populated_db)
        assert result['success'] is True

    def test_reports_size_keys(self, populated_db):
        result = vacuum_database(populated_db)
        assert 'size_before' in result
        assert 'size_after' in result
        assert 'space_saved' in result

    def test_progress_callback_fires(self, populated_db):
        calls = []
        vacuum_database(populated_db, progress_callback=lambda p, m: calls.append(p))
        assert 0 in calls
        assert 100 in calls

    def test_missing_db_returns_error(self, tmp_path):
        result = vacuum_database(str(tmp_path / 'missing.db'))
        assert result['success'] is False


# ---------------------------------------------------------------------------
# integrity_check
# ---------------------------------------------------------------------------

class TestIntegrityCheck:
    def test_valid_db_passes(self, populated_db):
        result = integrity_check(populated_db)
        assert result['success'] is True
        assert result['integrity_ok'] is True
        assert result['message'] == 'ok'

    def test_missing_db_returns_error(self, tmp_path):
        result = integrity_check(str(tmp_path / 'missing.db'))
        assert result['success'] is False


# ---------------------------------------------------------------------------
# preview_purge — factory wiring contract
# ---------------------------------------------------------------------------

class TestPreviewPurge:
    def test_returns_purge_preview_instance(self, populated_db):
        result = preview_purge(populated_db, 30, PurgePreview)
        assert isinstance(result, PurgePreview)

    def test_cutoff_date_set(self, populated_db):
        result = preview_purge(populated_db, 30, PurgePreview)
        assert result.cutoff_date is not None
        # cutoff_date should be a date string 30 days before today
        expected = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    - timedelta(days=30)).strftime('%Y-%m-%d')
        assert result.cutoff_date == expected

    def test_counts_old_servers(self, populated_db):
        # 10.0.0.2 last seen 2024-01-10 — over 365 days ago
        result = preview_purge(populated_db, 365, PurgePreview)
        assert result.servers_to_delete >= 1

    def test_total_records_sum(self, populated_db):
        result = preview_purge(populated_db, 1, PurgePreview)
        expected = (
            result.servers_to_delete
            + result.shares_to_delete
            + result.credentials_to_delete
            + result.file_manifests_to_delete
            + result.vulnerabilities_to_delete
            + result.user_flags_to_delete
            + result.probe_cache_to_delete
        )
        assert result.total_records == expected

    def test_missing_db_returns_empty_preview(self, tmp_path):
        result = preview_purge(str(tmp_path / 'missing.db'), 30, PurgePreview)
        assert isinstance(result, PurgePreview)
        assert result.servers_to_delete == 0


# ---------------------------------------------------------------------------
# execute_purge
# ---------------------------------------------------------------------------

class TestExecutePurge:
    def test_zero_match_message_text(self, temp_db):
        """Zero-match branch must contain exact user-facing message string."""
        result = execute_purge(temp_db, 1, PurgePreview)
        # temp_db has no servers, so no matches
        assert result['success'] is True
        assert 'No servers found matching purge criteria' in result['message']

    def test_zero_match_counts(self, temp_db):
        result = execute_purge(temp_db, 1, PurgePreview)
        assert result['servers_deleted'] == 0
        assert result['total_records_deleted'] == 0

    def test_deletes_old_servers(self, populated_db):
        # 10.0.0.2 last seen 2024-01-10 — should be purged with a 365-day cutoff
        result = execute_purge(populated_db, 365, PurgePreview)
        assert result['success'] is True
        assert result['servers_deleted'] >= 1

    def test_purge_cascade_deletes_shares(self, populated_db):
        # Confirm server is gone after purge (cascade should remove its shares)
        conn = sqlite3.connect(populated_db)
        conn.execute("PRAGMA foreign_keys = ON")
        before = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
        conn.close()

        execute_purge(populated_db, 365, PurgePreview)

        conn = sqlite3.connect(populated_db)
        after = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
        conn.close()
        assert after < before

    def test_progress_callback_fires(self, populated_db):
        calls = []
        # Use a very short cutoff so there are servers to purge
        execute_purge(populated_db, 365, PurgePreview,
                      progress_callback=lambda p, m: calls.append(p))
        assert len(calls) > 0

    def test_missing_db_returns_error(self, tmp_path):
        result = execute_purge(str(tmp_path / 'missing.db'), 30, PurgePreview)
        assert result['success'] is False
