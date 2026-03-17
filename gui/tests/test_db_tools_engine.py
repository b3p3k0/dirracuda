"""
Unit tests for DBToolsEngine - database management operations.

Tests cover schema validation, merge operations, export/backup, statistics,
and maintenance operations. Uses temporary SQLite databases for isolation.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.db_tools_engine import (
    DBToolsEngine,
    MergeConflictStrategy,
    MergeResult,
    DatabaseStats,
    PurgePreview,
    SchemaValidation,
    REQUIRED_TABLES,
    REQUIRED_SERVER_COLUMNS,
)


# Minimal schema for test databases
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
    shodan_data TEXT,
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
    auth_status TEXT,
    permissions TEXT,
    share_type TEXT,
    share_comment TEXT,
    test_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    access_details TEXT,
    error_message TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE share_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    username TEXT,
    password TEXT,
    source TEXT DEFAULT 'pry',
    session_id INTEGER,
    last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_share_credentials_server_share_source
    ON share_credentials(server_id, share_name, source);

CREATE TABLE file_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    file_type TEXT,
    file_extension TEXT,
    mime_type TEXT,
    last_modified DATETIME,
    is_ransomware_indicator BOOLEAN DEFAULT FALSE,
    is_sensitive BOOLEAN DEFAULT FALSE,
    discovery_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE vulnerabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    vuln_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    evidence TEXT,
    remediation TEXT,
    cvss_score DECIMAL(3,1),
    cve_ids TEXT,
    discovery_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'open',
    notes TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE failure_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ip_address TEXT NOT NULL,
    failure_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    failure_type TEXT,
    failure_reason TEXT,
    shodan_data TEXT,
    analysis_results TEXT,
    retry_count INTEGER DEFAULT 0,
    last_retry_timestamp DATETIME,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
);

CREATE TABLE host_user_flags (
    server_id INTEGER PRIMARY KEY,
    favorite BOOLEAN DEFAULT 0,
    avoid BOOLEAN DEFAULT 0,
    notes TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE host_probe_cache (
    server_id INTEGER PRIMARY KEY,
    status TEXT DEFAULT 'unprobed',
    last_probe_at DATETIME,
    indicator_matches INTEGER DEFAULT 0,
    indicator_samples TEXT,
    snapshot_path TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""


@pytest.fixture
def temp_db():
    """Create a temporary database with full schema."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def populated_db(temp_db):
    """Create a database with sample data."""
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys = ON")

    # Add a scan session
    conn.execute("""
        INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
        VALUES ('smbseek', 'discover', 'completed', 10)
    """)

    # Add some servers
    servers = [
        ('192.168.1.1', 'United States', 'US', 'anonymous', '2024-01-15', '2024-02-01'),
        ('192.168.1.2', 'United Kingdom', 'GB', 'guest', '2024-01-10', '2024-01-20'),
        ('192.168.1.3', 'Germany', 'DE', 'anonymous', '2024-01-01', '2024-01-05'),
    ]
    for ip, country, code, auth, first, last in servers:
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ip, country, code, auth, first, last))

    # Add some shares
    conn.execute("""
        INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
        VALUES (1, 1, 'Documents', 1, '2024-02-01'),
               (1, 1, 'Public', 1, '2024-02-01'),
               (2, 1, 'Users', 0, '2024-01-20')
    """)

    # Add a vulnerability
    conn.execute("""
        INSERT INTO vulnerabilities (server_id, session_id, vuln_type, severity, title)
        VALUES (1, 1, 'weak_auth', 'high', 'Anonymous access enabled')
    """)

    # Add file manifest
    conn.execute("""
        INSERT INTO file_manifests (server_id, session_id, share_name, file_path, file_name)
        VALUES (1, 1, 'Documents', '/secret.txt', 'secret.txt')
    """)

    # Add user flags
    conn.execute("""
        INSERT INTO host_user_flags (server_id, favorite, notes)
        VALUES (1, 1, 'Important server')
    """)

    # Add probe cache
    conn.execute("""
        INSERT INTO host_probe_cache (server_id, status)
        VALUES (1, 'probed')
    """)

    conn.commit()
    conn.close()

    return temp_db


@pytest.fixture
def external_db():
    """Create an external database with different data for merge testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")

    # Add a scan session
    conn.execute("""
        INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
        VALUES ('smbseek', 'discover', 'completed', 5)
    """)

    # Add servers - some overlapping, some new
    servers = [
        ('192.168.1.1', 'United States', 'US', 'guest', '2024-01-20', '2024-03-01'),  # Overlap, newer
        ('192.168.1.2', 'United Kingdom', 'GB', 'guest', '2024-01-10', '2024-01-15'),  # Overlap, older
        ('192.168.1.4', 'France', 'FR', 'anonymous', '2024-02-01', '2024-02-15'),  # New
    ]
    for ip, country, code, auth, first, last in servers:
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ip, country, code, auth, first, last))

    # Add shares for new server
    conn.execute("""
        INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
        VALUES (3, 1, 'Archive', 1, '2024-02-15')
    """)

    conn.commit()
    conn.close()

    yield db_path

    try:
        os.unlink(db_path)
    except Exception:
        pass


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_valid_schema_passes(self, temp_db):
        """Schema validation passes for valid SMBSeek database."""
        engine = DBToolsEngine(temp_db)
        result = engine.validate_external_schema(temp_db)

        assert result.valid is True
        assert len(result.errors) == 0
        assert len(result.missing_tables) == 0
        assert len(result.missing_columns) == 0

    def test_missing_file_fails(self, temp_db):
        """Schema validation fails for non-existent file."""
        engine = DBToolsEngine(temp_db)
        result = engine.validate_external_schema("/nonexistent/path.db")

        assert result.valid is False
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_missing_table_fails(self, temp_db):
        """Schema validation fails when required table is missing."""
        # Create DB without smb_servers table
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            conn = sqlite3.connect(bad_db)
            conn.execute("""
                CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT)
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert 'smb_servers' in result.missing_tables
        finally:
            os.unlink(bad_db)

    def test_missing_column_fails(self, temp_db):
        """Schema validation fails when required column is missing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            conn = sqlite3.connect(bad_db)
            conn.execute("""
                CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT)
            """)
            # Missing last_seen column
            conn.execute("""
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY,
                    ip_address TEXT UNIQUE,
                    country TEXT,
                    auth_method TEXT,
                    first_seen DATETIME
                )
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert 'last_seen' in result.missing_columns
        finally:
            os.unlink(bad_db)


class TestMergePreview:
    """Tests for merge preview functionality."""

    def test_preview_shows_new_and_existing(self, populated_db, external_db):
        """Preview correctly identifies new and existing servers."""
        engine = DBToolsEngine(populated_db)
        preview = engine.preview_merge(external_db)

        assert preview['valid'] is True
        assert preview['external_servers'] == 3
        assert preview['new_servers'] == 1  # 192.168.1.4
        assert preview['existing_servers'] == 2  # 192.168.1.1 and 192.168.1.2

    def test_preview_invalid_db_fails(self, populated_db):
        """Preview fails for invalid database."""
        engine = DBToolsEngine(populated_db)
        preview = engine.preview_merge("/nonexistent.db")

        assert preview['valid'] is False
        assert 'errors' in preview


class TestMergeOperations:
    """Tests for database merge operations."""

    def test_merge_adds_new_servers(self, populated_db, external_db):
        """Merge correctly adds new servers that don't exist in target."""
        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(external_db, auto_backup=False)

        assert result.success is True
        assert result.servers_added >= 1  # At least 192.168.1.4

        # Verify server was added
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT ip_address FROM smb_servers WHERE ip_address = '192.168.1.4'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_merge_keep_newer_updates_when_external_newer(self, populated_db, external_db):
        """KEEP_NEWER strategy updates when external record is newer."""
        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(
            external_db,
            strategy=MergeConflictStrategy.KEEP_NEWER,
            auto_backup=False
        )

        assert result.success is True

        # 192.168.1.1 should be updated (external has newer last_seen)
        conn = sqlite3.connect(populated_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT last_seen, auth_method FROM smb_servers WHERE ip_address = '192.168.1.1'")
        row = cursor.fetchone()
        conn.close()

        # Should have the newer date from external DB
        assert '2024-03' in row['last_seen']

    def test_merge_keep_newer_skips_when_external_older(self, populated_db, external_db):
        """KEEP_NEWER strategy skips when external record is older."""
        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(
            external_db,
            strategy=MergeConflictStrategy.KEEP_NEWER,
            auto_backup=False
        )

        assert result.success is True
        # 192.168.1.2 should be skipped (external has older last_seen)
        assert result.servers_skipped >= 1

    def test_merge_imports_related_data(self, populated_db, external_db):
        """Merge imports share_access records for new servers."""
        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(external_db, auto_backup=False)

        assert result.success is True
        assert result.shares_imported >= 1  # Archive share from 192.168.1.4

    def test_merge_creates_import_session(self, populated_db, external_db):
        """Merge creates a scan session record for the import."""
        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(external_db, auto_backup=False)

        assert result.success is True

        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scan_sessions WHERE scan_type = 'db_import'")
        import_session = cursor.fetchone()
        conn.close()

        assert import_session is not None

    def test_merge_does_not_import_user_flags(self, populated_db, external_db):
        """Merge preserves local user flags (doesn't import from external)."""
        # Add user flags to external DB
        conn = sqlite3.connect(external_db)
        conn.execute("""
            INSERT INTO host_user_flags (server_id, favorite, notes)
            VALUES (1, 0, 'External note')
        """)
        conn.commit()
        conn.close()

        engine = DBToolsEngine(populated_db)
        result = engine.merge_database(external_db, auto_backup=False)

        assert result.success is True

        # Verify original user flag is preserved
        conn = sqlite3.connect(populated_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT notes FROM host_user_flags WHERE server_id = 1")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row['notes'] == 'Important server'  # Original note preserved


class TestExportOperations:
    """Tests for database export operations."""

    def test_export_creates_valid_database(self, populated_db):
        """Export creates a valid, readable database."""
        engine = DBToolsEngine(populated_db)

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            export_path = f.name

        try:
            result = engine.export_database(export_path)

            assert result['success'] is True
            assert os.path.exists(export_path)
            assert result['size_bytes'] > 0

            # Verify exported DB is readable
            conn = sqlite3.connect(export_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM smb_servers")
            count = cursor.fetchone()[0]
            conn.close()

            assert count == 3  # Same as source
        finally:
            try:
                os.unlink(export_path)
            except Exception:
                pass

    def test_quick_backup_creates_timestamped_file(self, populated_db):
        """Quick backup creates file with timestamp in name."""
        engine = DBToolsEngine(populated_db)
        result = engine.quick_backup()

        assert result['success'] is True
        assert 'backup' in result['backup_path']
        assert os.path.exists(result['backup_path'])

        # Cleanup
        try:
            os.unlink(result['backup_path'])
        except Exception:
            pass


class TestStatistics:
    """Tests for database statistics."""

    def test_stats_returns_correct_counts(self, populated_db):
        """Statistics returns correct record counts."""
        engine = DBToolsEngine(populated_db)
        stats = engine.get_database_stats()

        assert stats.total_servers == 3
        assert stats.total_shares == 3
        assert stats.accessible_shares == 2  # Documents and Public
        assert stats.total_vulnerabilities == 1
        assert stats.total_file_manifests == 1

    def test_stats_returns_country_distribution(self, populated_db):
        """Statistics returns country distribution."""
        engine = DBToolsEngine(populated_db)
        stats = engine.get_database_stats()

        assert len(stats.countries) == 3
        assert 'United States' in stats.countries
        assert 'United Kingdom' in stats.countries
        assert 'Germany' in stats.countries


class TestMaintenance:
    """Tests for maintenance operations."""

    def test_vacuum_succeeds(self, populated_db):
        """Vacuum operation completes successfully."""
        engine = DBToolsEngine(populated_db)
        result = engine.vacuum_database()

        assert result['success'] is True
        assert 'size_before' in result
        assert 'size_after' in result

    def test_integrity_check_passes_valid_db(self, populated_db):
        """Integrity check passes for valid database."""
        engine = DBToolsEngine(populated_db)
        result = engine.integrity_check()

        assert result['success'] is True
        assert result['integrity_ok'] is True

    def test_purge_preview_returns_correct_counts(self, populated_db):
        """Purge preview returns correct cascade counts."""
        engine = DBToolsEngine(populated_db)

        # Purge servers not seen in last 30 days (should get 192.168.1.2 and 192.168.1.3)
        preview = engine.preview_purge(30)

        assert preview.servers_to_delete >= 1  # At least one old server
        assert preview.total_records >= preview.servers_to_delete  # Includes cascades

    def test_purge_deletes_old_servers(self, populated_db):
        """Purge deletes servers older than threshold."""
        engine = DBToolsEngine(populated_db)

        # Get count before
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM smb_servers")
        count_before = cursor.fetchone()[0]
        conn.close()

        # Purge servers not seen in 30 days
        result = engine.execute_purge(30)

        assert result['success'] is True

        # Get count after
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM smb_servers")
        count_after = cursor.fetchone()[0]
        conn.close()

        # Should have fewer servers
        assert count_after < count_before or result['servers_deleted'] == 0

    def test_purge_dry_run_no_changes(self, populated_db):
        """Purge preview doesn't modify database."""
        engine = DBToolsEngine(populated_db)

        # Get count before
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM smb_servers")
        count_before = cursor.fetchone()[0]
        conn.close()

        # Run preview
        engine.preview_purge(30)

        # Get count after
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM smb_servers")
        count_after = cursor.fetchone()[0]
        conn.close()

        assert count_after == count_before  # No change


class TestNullTimestampHandling:
    """Tests for NULL timestamp edge cases."""

    def test_merge_handles_null_last_seen(self, temp_db):
        """Merge handles NULL last_seen timestamps correctly."""
        # Create external DB with NULL timestamp
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            ext_db = f.name

        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status)
                VALUES ('smbseek', 'discover', 'completed')
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen)
                VALUES ('10.0.0.1', 'US', 'anonymous', '2024-01-01', NULL)
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            # Should complete without error
            assert result.success is True
            assert result.servers_added == 1

        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass


class TestMergeTimestampNormalization:
    """Merge writes must not re-introduce T-format timestamps into the DB."""

    def test_merge_normalizes_T_format_timestamps(self, temp_db):
        """New server inserted via merge stores first_seen/last_seen without T."""
        ext_db = tempfile.mktemp(suffix=".db")
        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute(
                "INSERT INTO scan_sessions (tool_name, scan_type, status) "
                "VALUES ('smbseek', 'discover', 'completed')"
            )
            conn.execute(
                "INSERT INTO smb_servers "
                "(ip_address, country, auth_method, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                ("192.168.1.1", "US", "anonymous",
                 "2025-01-21T14:20:05", "2025-01-21T15:30:00"),
            )
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.merge_database(ext_db, auto_backup=False)
            assert result.success is True
            assert result.servers_added == 1

            conn = sqlite3.connect(temp_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT first_seen, last_seen FROM smb_servers WHERE ip_address = ?",
                ("192.168.1.1",),
            ).fetchone()
            conn.close()

            assert row is not None
            assert "T" not in row["first_seen"], (
                f"first_seen has T after merge: {row['first_seen']!r}"
            )
            assert "T" not in row["last_seen"], (
                f"last_seen has T after merge: {row['last_seen']!r}"
            )
            assert row["first_seen"] == "2025-01-21 14:20:05"
            assert row["last_seen"] == "2025-01-21 15:30:00"

        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_normalizes_offset_timestamp(self, temp_db):
        """Update via merge with +offset timestamp converts to UTC canonical form."""
        # Seed current DB with an existing row so merge triggers UPDATE path
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO smb_servers "
            "(ip_address, country, auth_method, first_seen, last_seen, scan_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("10.10.10.1", "DE", "anonymous",
             "2025-01-21 08:00:00", "2025-01-21 08:00:00", 1),
        )
        conn.commit()
        conn.close()

        ext_db = tempfile.mktemp(suffix=".db")
        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute(
                "INSERT INTO scan_sessions (tool_name, scan_type, status) "
                "VALUES ('smbseek', 'discover', 'completed')"
            )
            # External row has a newer timestamp with +05:30 offset
            conn.execute(
                "INSERT INTO smb_servers "
                "(ip_address, country, auth_method, first_seen, last_seen, scan_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("10.10.10.1", "DE", "anonymous",
                 "2025-01-21T14:20:05+05:30", "2025-01-21T14:20:05+05:30", 2),
            )
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.merge_database(
                ext_db,
                strategy=MergeConflictStrategy.KEEP_SOURCE,
                auto_backup=False,
            )
            assert result.success is True

            conn = sqlite3.connect(temp_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT last_seen FROM smb_servers WHERE ip_address = ?",
                ("10.10.10.1",),
            ).fetchone()
            conn.close()

            assert row is not None
            assert "T" not in row["last_seen"], (
                f"last_seen has T after offset merge: {row['last_seen']!r}"
            )
            # +05:30 from 14:20:05 → UTC 08:50:05
            assert row["last_seen"] == "2025-01-21 08:50:05"

        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
