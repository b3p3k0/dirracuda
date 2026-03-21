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
    REQUIRED_SHARE_ACCESS_COLUMNS,
    REQUIRED_FILE_MANIFEST_COLUMNS,
)
from shared.db_migrations import run_migrations


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
            assert any(col.endswith(".last_seen") for col in result.missing_columns)
        finally:
            os.unlink(bad_db)

    def test_missing_related_table_fails(self, temp_db):
        """Schema validation fails when merge-required related tables are missing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            conn = sqlite3.connect(bad_db)
            conn.execute("""
                CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT)
            """)
            conn.execute("""
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY,
                    ip_address TEXT UNIQUE,
                    country TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
                )
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert 'share_access' in result.missing_tables
        finally:
            os.unlink(bad_db)

    def test_optional_protocol_columns_validated_when_tables_exist(self, temp_db):
        """Validation fails early when optional protocol sidecar tables exist but miss merge-read columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            run_migrations(bad_db)
            conn = sqlite3.connect(bad_db)
            conn.executescript("""
                ALTER TABLE ftp_access RENAME TO ftp_access_old;
                CREATE TABLE ftp_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    test_timestamp DATETIME
                );
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert any("Missing required columns in ftp_access" in err for err in result.errors)
            assert any(col.startswith("ftp_access.") for col in result.missing_columns)
        finally:
            os.unlink(bad_db)

    def test_share_access_columns_validated(self, temp_db):
        """Schema validation fails when share_access is present but missing merge-read columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            conn = sqlite3.connect(bad_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    country_code TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
                );
                CREATE TABLE share_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL
                );
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert any("Missing required columns in share_access" in err for err in result.errors)
            assert any(col.startswith("share_access.") for col in result.missing_columns)
            assert any(col.endswith(".share_name") for col in result.missing_columns)
            # Sanity check that our required column contract is still non-trivial.
            assert len(REQUIRED_SHARE_ACCESS_COLUMNS) >= 5
        finally:
            os.unlink(bad_db)

    def test_legacy_schema_without_optional_artifact_tables_passes(self, temp_db):
        """Legacy external schemas missing optional artifact tables are still merge-compatible."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            legacy_db = f.name

        try:
            conn = sqlite3.connect(legacy_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    country_code TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
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
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(legacy_db)

            assert result.valid is True
            assert len(result.errors) == 0
        finally:
            os.unlink(legacy_db)

    def test_optional_artifact_columns_validated_when_tables_exist(self, temp_db):
        """Validation fails early when optional artifact tables exist but miss merge-read columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            run_migrations(bad_db)
            conn = sqlite3.connect(bad_db)
            conn.executescript("""
                ALTER TABLE file_manifests RENAME TO file_manifests_old;
                CREATE TABLE file_manifests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL
                );
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.validate_external_schema(bad_db)

            assert result.valid is False
            assert any("Missing required columns in file_manifests" in err for err in result.errors)
            assert any(col.startswith("file_manifests.") for col in result.missing_columns)
            assert any(col.endswith(".file_name") for col in result.missing_columns)
            assert len(REQUIRED_FILE_MANIFEST_COLUMNS) >= 10
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

    def test_preview_missing_required_table_reports_validation_error(self, populated_db):
        """Preview fails early with validation error when merge-required tables are missing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            bad_db = f.name

        try:
            conn = sqlite3.connect(bad_db)
            conn.execute("""
                CREATE TABLE scan_sessions (id INTEGER PRIMARY KEY, scan_type TEXT)
            """)
            conn.execute("""
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY,
                    ip_address TEXT UNIQUE,
                    country TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
                )
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen)
                VALUES ('203.0.113.11', 'US', 'anonymous', '2026-03-01', '2026-03-01')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(populated_db)
            preview = engine.preview_merge(bad_db)

            assert preview['valid'] is False
            assert any("Missing required tables:" in err for err in preview.get('errors', []))
            assert not any("no such table" in err.lower() for err in preview.get('errors', []))
        finally:
            os.unlink(bad_db)

    def test_preview_includes_ftp_http_server_and_access_counts(self):
        """Preview totals include FTP/HTTP protocol rows when those tables are present."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            run_migrations(ext_db)

            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 3)
            """)
            session_id = cur.lastrowid

            cur.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('203.0.113.10', 'US', 'anonymous', '2026-03-01', '2026-03-01', 'active')
            """)
            smb_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('203.0.113.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            ftp_id = cur.lastrowid
            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('203.0.113.30', 'US', 'US', 'http', '2026-03-01', '2026-03-01', 'active')
            """)
            http_id = cur.lastrowid

            cur.execute("""
                INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
                VALUES (?, ?, 'Public', 1, '2026-03-01')
            """, (smb_id, session_id))
            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 1, 1, 4, '2026-03-01')
            """, (ftp_id, session_id))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 2, 3, '2026-03-01')
            """, (http_id, session_id))
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            preview = engine.preview_merge(ext_db)

            assert preview['valid'] is True
            assert preview['external_servers'] == 3
            assert preview['new_servers'] == 3
            assert preview['existing_servers'] == 0
            assert preview['total_shares'] == 3
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_preview_warns_when_target_lacks_protocol_tables(self):
        """Preview reports schema-skip warnings when source has protocol tables absent in target."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            conn = sqlite3.connect(cur_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 2)
            """)
            session_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.51.100.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            ftp_id = cur.lastrowid
            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('198.51.100.30', 'US', 'US', 'https', '2026-03-01', '2026-03-01', 'active')
            """)
            http_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 1, 1, 4, '2026-03-01')
            """, (ftp_id, session_id))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 2, 3, '2026-03-01')
            """, (http_id, session_id))
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            preview = engine.preview_merge(ext_db)

            assert preview['valid'] is True
            warnings = preview.get('warnings', [])
            assert any("ftp_servers" in warning for warning in warnings)
            assert any("http_servers" in warning for warning in warnings)
            assert any("ftp_access" in warning for warning in warnings)
            assert any("http_access" in warning for warning in warnings)
            assert preview['schema_skipped_servers'] == 2
            assert preview['schema_skipped_shares'] == 2
            assert preview['schema_skipped_artifacts'] == 0
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_preview_warns_when_target_protocol_table_missing_columns(self):
        """Preview reports schema-skip warnings when target protocol table exists but is malformed."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            conn = sqlite3.connect(cur_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute("""
                CREATE TABLE ftp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    last_seen DATETIME
                )
            """)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 1)
            """)
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.51.100.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            preview = engine.preview_merge(ext_db)

            assert preview['valid'] is True
            warnings = preview.get('warnings', [])
            assert any(
                "ftp_servers" in warning and "missing required columns" in warning
                for warning in warnings
            )
            assert preview['schema_skipped_servers'] == 1
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_preview_warns_when_target_lacks_optional_artifact_tables(self):
        """Preview reports artifact skip warnings when source has rows in optional artifact tables."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            conn = sqlite3.connect(cur_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
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
            """)
            conn.commit()
            conn.close()

            conn = sqlite3.connect(ext_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute("""
                INSERT INTO scan_sessions (scan_type, status)
                VALUES ('discover', 'completed')
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen)
                VALUES ('198.51.100.10', 'US', 'anonymous', '2026-03-01', '2026-03-01')
            """)
            conn.execute("""
                INSERT INTO share_credentials (server_id, share_name, username, password, source)
                VALUES (1, 'Public', 'guest', 'guest', 'pry')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            preview = engine.preview_merge(ext_db)

            assert preview['valid'] is True
            warnings = preview.get('warnings', [])
            assert any("share_credentials" in warning for warning in warnings)
            assert preview['schema_skipped_artifacts'] == 1
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_preview_warns_when_target_artifact_table_missing_columns(self):
        """Preview reports artifact skip warnings when target artifact table is malformed."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            conn = sqlite3.connect(cur_db)
            conn.executescript("""
                ALTER TABLE share_credentials RENAME TO share_credentials_old;
                CREATE TABLE share_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    share_name TEXT NOT NULL,
                    username TEXT,
                    password TEXT,
                    source TEXT DEFAULT 'pry',
                    last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            conn.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 1)
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('198.51.100.77', 'US', 'anonymous', '2026-03-01', '2026-03-01', 'active')
            """)
            conn.execute("""
                INSERT INTO share_credentials (server_id, share_name, username, password, source)
                VALUES (1, 'Public', 'guest', 'guest', 'pry')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            preview = engine.preview_merge(ext_db)

            assert preview['valid'] is True
            warnings = preview.get('warnings', [])
            assert any(
                "share_credentials" in warning and "missing required columns" in warning
                for warning in warnings
            )
            assert preview['schema_skipped_artifacts'] == 1
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass


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

    def test_merge_succeeds_with_legacy_target_scan_sessions_columns(self, external_db):
        """Merge handles legacy target scan_sessions schemas with minimal columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            target_db = f.name

        try:
            conn = sqlite3.connect(target_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
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
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(target_db)
            result = engine.merge_database(external_db, auto_backup=False)

            assert result.success is True
            assert result.servers_added >= 1

            conn = sqlite3.connect(target_db)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM scan_sessions WHERE scan_type = 'db_import'")
            assert cur.fetchone()[0] >= 1
            conn.close()
        finally:
            try:
                os.unlink(target_db)
            except Exception:
                pass

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

    def test_merge_rollback_on_partial_failure(self, populated_db):
        """
        Merge failure after server staging must rollback all writes.

        External DB is valid; target injects a trigger that forces share_access
        insert failure after smb_servers rows have already been staged.
        """
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            ext_db = f.name

        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
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
                CREATE TABLE share_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    share_name TEXT NOT NULL,
                    username TEXT,
                    password TEXT,
                    source TEXT DEFAULT 'pry',
                    session_id INTEGER,
                    last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("INSERT INTO scan_sessions (scan_type) VALUES ('discover')")
            conn.execute("""
                INSERT INTO smb_servers (
                    ip_address, country, auth_method, first_seen, last_seen, scan_count, status
                ) VALUES (
                    '203.0.113.200', 'US', 'guest', '2024-03-01', '2024-03-01', 1, 'active'
                )
            """)
            conn.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status, permissions, share_type,
                    share_comment, test_timestamp, access_details, error_message
                ) VALUES (
                    1, 1, 'Public', 1, 'ok', 'read', 'disk', 'External share',
                    '2024-03-01', '{}', NULL
                )
            """)
            conn.execute("""
                INSERT INTO share_credentials (server_id, share_name, username, password, source)
                VALUES (1, 'Public', 'guest', 'guest', 'pry')
            """)
            conn.commit()
            conn.close()

            conn = sqlite3.connect(populated_db)
            conn.execute("""
                CREATE TRIGGER fail_share_access_import
                BEFORE INSERT ON share_access
                BEGIN
                    SELECT RAISE(ABORT, 'forced share_access failure');
                END;
            """)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM smb_servers")
            servers_before = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scan_sessions WHERE scan_type = 'db_import'")
            import_sessions_before = cursor.fetchone()[0]
            conn.close()

            engine = DBToolsEngine(populated_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is False
            assert any(
                "forced share_access failure" in err.lower()
                for err in result.errors
            )

            conn = sqlite3.connect(populated_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM smb_servers")
            servers_after = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scan_sessions WHERE scan_type = 'db_import'")
            import_sessions_after = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM smb_servers WHERE ip_address = '203.0.113.200'")
            inserted_rows = cursor.fetchone()[0]
            conn.close()

            assert servers_after == servers_before
            assert import_sessions_after == import_sessions_before
            assert inserted_rows == 0
        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_fails_early_for_invalid_optional_protocol_columns(self):
        """Merge returns validation errors (not runtime SQL errors) for malformed optional protocol schemas."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            run_migrations(ext_db)

            conn = sqlite3.connect(ext_db)
            conn.executescript("""
                ALTER TABLE http_servers RENAME TO http_servers_old;
                CREATE TABLE http_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    country_code TEXT,
                    port INTEGER,
                    scheme TEXT,
                    banner TEXT,
                    shodan_data TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    scan_count INTEGER,
                    status TEXT,
                    notes TEXT
                );
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is False
            assert any("Missing required columns in http_servers" in err for err in result.errors)
            assert not any("no such column" in err.lower() for err in result.errors)
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_fails_early_when_current_share_access_missing_session_id(self, external_db):
        """Merge fails early with explicit current-schema error for malformed target share_access."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name

        try:
            run_migrations(cur_db)
            conn = sqlite3.connect(cur_db)
            conn.executescript("""
                ALTER TABLE share_access RENAME TO share_access_old;
                CREATE TABLE share_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
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
            """)
            conn.commit()

            before_servers = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
            before_import_sessions = conn.execute(
                "SELECT COUNT(*) FROM scan_sessions WHERE scan_type = 'db_import'"
            ).fetchone()[0]
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(external_db, auto_backup=False)

            assert result.success is False
            assert any(
                "Current database table share_access missing required columns: session_id" in err
                for err in result.errors
            )
            assert not any("no such column" in err.lower() for err in result.errors)

            conn = sqlite3.connect(cur_db)
            after_servers = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
            after_import_sessions = conn.execute(
                "SELECT COUNT(*) FROM scan_sessions WHERE scan_type = 'db_import'"
            ).fetchone()[0]
            conn.close()

            assert after_servers == before_servers
            assert after_import_sessions == before_import_sessions
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass

    def test_merge_imports_ftp_http_servers_and_access(self):
        """Merge imports FTP/HTTP server rows and latest access summaries."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            run_migrations(ext_db)

            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 2)
            """)
            session_id = cur.lastrowid

            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.18.0.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            ftp_id = cur.lastrowid
            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('198.18.0.30', 'US', 'US', 'https', '2026-03-01', '2026-03-01', 'active')
            """)
            http_id = cur.lastrowid

            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 1, 1, 8, '2026-03-01')
            """, (ftp_id, session_id))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, tls_verified, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 3, 7, 0, '2026-03-01')
            """, (http_id, session_id))
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)
            assert result.success is True
            assert result.servers_added >= 2
            assert result.shares_imported >= 2

            conn = sqlite3.connect(cur_db)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM ftp_servers WHERE ip_address = '198.18.0.20'")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM http_servers WHERE ip_address = '198.18.0.30'")
            assert cur.fetchone()[0] == 1
            cur.execute("""
                SELECT COUNT(*) FROM ftp_access a
                JOIN ftp_servers f ON f.id = a.server_id
                WHERE f.ip_address = '198.18.0.20'
            """)
            assert cur.fetchone()[0] >= 1
            cur.execute("""
                SELECT COUNT(*) FROM http_access a
                JOIN http_servers h ON h.id = a.server_id
                WHERE h.ip_address = '198.18.0.30'
            """)
            assert cur.fetchone()[0] >= 1
            conn.close()
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_succeeds_with_legacy_external_schema_missing_optional_artifacts(self, populated_db):
        """Merge succeeds when external DB lacks file_manifests/vulnerabilities/failure_logs tables."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            ext_db = f.name

        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    country_code TEXT,
                    auth_method TEXT,
                    shodan_data TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME,
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
            """)
            conn.execute("INSERT INTO scan_sessions (scan_type) VALUES ('discover')")
            conn.execute("""
                INSERT INTO smb_servers (
                    ip_address, country, country_code, auth_method, first_seen, last_seen, scan_count, status
                ) VALUES (
                    '203.0.113.77', 'US', 'US', 'anonymous', '2026-03-01 00:00:00', '2026-03-01 00:00:00', 1, 'active'
                )
            """)
            conn.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status, permissions, share_type,
                    share_comment, test_timestamp, access_details, error_message
                ) VALUES (
                    1, 1, 'Public', 1, 'ok', 'read', 'disk', 'Legacy row',
                    '2026-03-01 00:00:00', '{}', NULL
                )
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(populated_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert result.servers_added >= 1
            assert result.shares_imported >= 1
        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_succeeds_with_legacy_external_smb_optional_columns_missing(self, temp_db):
        """Merge tolerates legacy external smb_servers rows missing optional columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            ext_db = f.name

        try:
            conn = sqlite3.connect(ext_db)
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    auth_method TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME
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
            """)
            conn.execute("INSERT INTO scan_sessions (scan_type) VALUES ('discover')")
            conn.execute("""
                INSERT INTO smb_servers (
                    ip_address, country, auth_method, first_seen, last_seen
                ) VALUES (
                    '203.0.113.88', 'US', 'anonymous', '2026-03-01 00:00:00', '2026-03-01 00:00:00'
                )
            """)
            conn.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status, permissions, share_type,
                    share_comment, test_timestamp, access_details, error_message
                ) VALUES (
                    1, 1, 'Public', 1, 'ok', 'read', 'disk', 'Legacy schema',
                    '2026-03-01 00:00:00', '{}', NULL
                )
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert result.servers_added >= 1
            assert result.shares_imported >= 1

            conn = sqlite3.connect(temp_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ip_address, country, country_code, status FROM smb_servers WHERE ip_address = '203.0.113.88'"
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["ip_address"] == "203.0.113.88"
            assert row["country"] == "US"
            assert row["country_code"] is None
        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_fails_early_for_invalid_optional_artifact_columns(self):
        """Merge returns validation errors (not runtime SQL errors) for malformed optional artifact schemas."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            run_migrations(ext_db)

            conn = sqlite3.connect(ext_db)
            conn.executescript("""
                ALTER TABLE vulnerabilities RENAME TO vulnerabilities_old;
                CREATE TABLE vulnerabilities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL
                );
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is False
            assert any("Missing required columns in vulnerabilities" in err for err in result.errors)
            assert not any("no such column" in err.lower() for err in result.errors)
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_warns_when_target_lacks_protocol_tables(self):
        """Merge succeeds but records warnings when protocol rows are skipped due to target schema."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            conn = sqlite3.connect(cur_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 2)
            """)
            session_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.51.100.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            ftp_id = cur.lastrowid
            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('198.51.100.30', 'US', 'US', 'https', '2026-03-01', '2026-03-01', 'active')
            """)
            http_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 1, 1, 4, '2026-03-01')
            """, (ftp_id, session_id))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 2, 3, '2026-03-01')
            """, (http_id, session_id))
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert any("ftp_servers" in warning for warning in result.warnings)
            assert any("http_servers" in warning for warning in result.warnings)
            assert any("ftp_access" in warning for warning in result.warnings)
            assert any("http_access" in warning for warning in result.warnings)
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_warns_and_skips_when_target_protocol_table_missing_columns(self, temp_db):
        """Merge succeeds and warns when target protocol table exists but lacks required columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            conn = sqlite3.connect(temp_db)
            conn.execute("""
                CREATE TABLE ftp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    last_seen DATETIME
                )
            """)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 2)
            """)
            session_id = cur.lastrowid
            cur.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('203.0.113.50', 'US', 'anonymous', '2026-03-01', '2026-03-01', 'active')
            """)
            smb_id = cur.lastrowid
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.51.100.20', 'US', 'US', '2026-03-01', '2026-03-01', 'active')
            """)
            cur.execute("""
                INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
                VALUES (?, ?, 'Public', 1, '2026-03-01')
            """, (smb_id, session_id))
            conn.commit()
            conn.close()

            engine = DBToolsEngine(temp_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert result.servers_added >= 1  # SMB row imports
            assert any(
                "ftp_servers" in warning and "missing required columns" in warning
                for warning in result.warnings
            )

            conn = sqlite3.connect(temp_db)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM smb_servers WHERE ip_address = '203.0.113.50'")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM ftp_servers")
            # Existing malformed target table remains untouched by merge.
            assert cur.fetchone()[0] == 0
            conn.close()
        finally:
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_warns_and_skips_when_target_lacks_share_credentials_table(self):
        """Merge succeeds and warns when source has share_credentials rows but target lacks the table."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            conn = sqlite3.connect(cur_db)
            conn.execute("DROP TABLE IF EXISTS share_credentials")
            conn.commit()
            conn.close()

            conn = sqlite3.connect(ext_db)
            conn.executescript(MINIMAL_SCHEMA)
            conn.execute("""
                INSERT INTO scan_sessions (scan_type, status)
                VALUES ('discover', 'completed')
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen, scan_count, status)
                VALUES ('198.51.100.10', 'US', 'US', 'anonymous', '2026-03-01', '2026-03-01', 1, 'active')
            """)
            conn.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status, permissions, share_type, share_comment,
                    test_timestamp, access_details, error_message
                ) VALUES (
                    1, 1, 'Public', 1, 'ok', 'read', 'disk', 'comment', '2026-03-01', '{}', NULL
                )
            """)
            conn.execute("""
                INSERT INTO share_credentials (server_id, share_name, username, password, source)
                VALUES (1, 'Public', 'guest', 'guest', 'pry')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert any("share_credentials" in warning for warning in result.warnings)
            # No target share_credentials table means credentials are intentionally skipped.
            assert result.credentials_imported == 0
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass

    def test_merge_warns_and_skips_when_target_share_credentials_missing_columns(self):
        """Merge succeeds and warns when target share_credentials table exists but is malformed."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_cur:
            cur_db = f_cur.name
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f_ext:
            ext_db = f_ext.name

        try:
            run_migrations(cur_db)
            conn = sqlite3.connect(cur_db)
            conn.executescript("""
                ALTER TABLE share_credentials RENAME TO share_credentials_old;
                CREATE TABLE share_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    share_name TEXT NOT NULL,
                    username TEXT,
                    password TEXT,
                    source TEXT DEFAULT 'pry',
                    last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            conn.close()

            run_migrations(ext_db)
            conn = sqlite3.connect(ext_db)
            conn.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 1)
            """)
            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('198.51.100.78', 'US', 'anonymous', '2026-03-01', '2026-03-01', 'active')
            """)
            conn.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status, permissions, share_type,
                    share_comment, test_timestamp, access_details, error_message
                ) VALUES (
                    1, 1, 'Public', 1, 'ok', 'read', 'disk', 'artifact warning test',
                    '2026-03-01', '{}', NULL
                )
            """)
            conn.execute("""
                INSERT INTO share_credentials (server_id, share_name, username, password, source)
                VALUES (1, 'Public', 'guest', 'guest', 'pry')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(cur_db)
            result = engine.merge_database(ext_db, auto_backup=False)

            assert result.success is True
            assert result.servers_added >= 1
            assert result.shares_imported >= 1
            assert result.credentials_imported == 0
            assert any(
                "share_credentials" in warning and "missing required columns" in warning
                for warning in result.warnings
            )
        finally:
            try:
                os.unlink(cur_db)
            except Exception:
                pass
            try:
                os.unlink(ext_db)
            except Exception:
                pass


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

    def test_quick_backup_includes_wal_commits_without_checkpoint(self):
        """Quick backup must include committed WAL data even when not checkpointed."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        writer_conn = None
        backup_path = None
        try:
            run_migrations(db_path)

            writer_conn = sqlite3.connect(db_path)
            writer_conn.execute("PRAGMA journal_mode=WAL")
            writer_conn.execute("PRAGMA wal_autocheckpoint=0")
            writer_conn.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status)
                VALUES ('smbseek', 'discover', 'completed')
            """)
            writer_conn.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('203.0.113.250', 'US', 'anonymous', '2026-03-20', '2026-03-20', 'active')
            """)
            writer_conn.commit()

            with tempfile.TemporaryDirectory() as backup_dir:
                engine = DBToolsEngine(db_path)
                result = engine.quick_backup(backup_dir=backup_dir)

                assert result['success'] is True
                backup_path = result['backup_path']
                assert os.path.exists(backup_path)

                backup_conn = sqlite3.connect(backup_path)
                cursor = backup_conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM smb_servers WHERE ip_address = '203.0.113.250'"
                )
                copied = cursor.fetchone()[0]
                backup_conn.close()

                assert copied == 1
        finally:
            if writer_conn is not None:
                writer_conn.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(f"{db_path}{suffix}")
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

    def test_stats_aggregates_across_smb_ftp_http_tables(self):
        """Statistics include SMB, FTP, and HTTP server/access records."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            run_migrations(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 3)
            """)
            session_id = cur.lastrowid

            cur.execute("""
                INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen, status)
                VALUES ('203.0.113.10', 'United States', 'US', 'anonymous', '2024-01-01 00:00:00', '2024-02-01 00:00:00', 'active')
            """)
            smb_id = cur.lastrowid

            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('203.0.113.20', 'Germany', 'DE', '2024-01-15 00:00:00', '2024-03-01 00:00:00', 'inactive')
            """)
            ftp_id = cur.lastrowid

            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('203.0.113.30', 'United States', 'US', 'https', '2024-02-10 00:00:00', '2024-04-20 00:00:00', 'active')
            """)
            http_id = cur.lastrowid

            cur.execute("""
                INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
                VALUES (?, ?, 'Public', 1, '2024-02-01 12:00:00')
            """, (smb_id, session_id))
            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 0, 0, 0, '2024-03-01 12:00:00')
            """, (ftp_id, session_id))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, tls_verified, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 2, 5, 1, '2024-04-20 12:00:00')
            """, (http_id, session_id))

            conn.commit()
            conn.close()

            engine = DBToolsEngine(db_path)
            stats = engine.get_database_stats()

            assert stats.total_servers == 3
            assert stats.active_servers == 2
            assert stats.total_shares == 3
            assert stats.accessible_shares == 2
            assert stats.oldest_record == '2024-01-01 00:00:00'
            assert stats.newest_record == '2024-04-20 00:00:00'
            assert stats.countries.get('United States') == 2
            assert stats.countries.get('Germany') == 1
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass

    def test_stats_handles_partial_protocol_table_columns(self):
        """Statistics degrade safely when sidecar protocol tables are missing expected columns."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    country TEXT,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    status TEXT
                );

                CREATE TABLE share_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    share_name TEXT NOT NULL,
                    accessible BOOLEAN DEFAULT FALSE,
                    test_timestamp DATETIME
                );

                -- Intentionally missing status/country columns.
                CREATE TABLE ftp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    first_seen DATETIME,
                    last_seen DATETIME
                );

                -- Intentionally missing accessible column.
                CREATE TABLE ftp_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    test_timestamp DATETIME
                );

                -- Intentionally missing first_seen/last_seen/country columns.
                CREATE TABLE http_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    status TEXT
                );
            """)

            conn.execute("""
                INSERT INTO smb_servers (ip_address, country, first_seen, last_seen, status)
                VALUES ('198.51.100.10', 'United States', '2024-01-01 00:00:00', '2024-02-01 00:00:00', 'active')
            """)
            conn.execute("""
                INSERT INTO ftp_servers (ip_address, first_seen, last_seen)
                VALUES ('198.51.100.20', '2024-01-10 00:00:00', '2024-03-05 00:00:00')
            """)
            conn.execute("""
                INSERT INTO http_servers (ip_address, status)
                VALUES ('198.51.100.30', 'inactive')
            """)

            conn.execute("""
                INSERT INTO share_access (server_id, share_name, accessible, test_timestamp)
                VALUES (1, 'Public', 1, '2024-02-01 12:00:00')
            """)
            conn.execute("""
                INSERT INTO ftp_access (server_id, test_timestamp)
                VALUES (1, '2024-03-05 12:00:00')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(db_path)
            stats = engine.get_database_stats()

            assert stats.total_servers == 3
            assert stats.active_servers == 2  # smb active + ftp fallback (no status column)
            assert stats.total_shares == 2
            assert stats.accessible_shares == 1  # ftp_access lacks accessible column
            assert stats.oldest_record == '2024-01-01 00:00:00'
            assert stats.newest_record == '2024-03-05 00:00:00'
            assert stats.countries == {'United States': 1}
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass


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

    def test_purge_includes_ftp_http_rows_and_related_state(self):
        """Purge preview/execute includes SMB, FTP, and HTTP server rows."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            run_migrations(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            cur = conn.cursor()

            # Session for SMB share_access/session-bound artifacts
            cur.execute("""
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
                VALUES ('smbseek', 'discover', 'completed', 3)
            """)
            session_id = cur.lastrowid

            old_seen = "2020-01-01 00:00:00"
            keep_seen = "2099-01-01 00:00:00"

            # SMB old + one keep row
            cur.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('198.51.100.10', 'US', 'anonymous', ?, ?, 'active')
            """, (old_seen, old_seen))
            smb_old_id = cur.lastrowid
            cur.execute("""
                INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, status)
                VALUES ('198.51.100.11', 'US', 'anonymous', ?, ?, 'active')
            """, (keep_seen, keep_seen))

            # FTP old
            cur.execute("""
                INSERT INTO ftp_servers (ip_address, country, country_code, first_seen, last_seen, status)
                VALUES ('198.51.100.20', 'US', 'US', ?, ?, 'active')
            """, (old_seen, old_seen))
            ftp_old_id = cur.lastrowid

            # HTTP old
            cur.execute("""
                INSERT INTO http_servers (ip_address, country, country_code, scheme, first_seen, last_seen, status)
                VALUES ('198.51.100.30', 'US', 'US', 'http', ?, ?, 'active')
            """, (old_seen, old_seen))
            http_old_id = cur.lastrowid

            # Related state rows for old records across protocols
            cur.execute("""
                INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
                VALUES (?, ?, 'Public', 1, ?)
            """, (smb_old_id, session_id, old_seen))
            cur.execute("""
                INSERT INTO ftp_access (server_id, session_id, accessible, root_listing_available, root_entry_count, test_timestamp)
                VALUES (?, ?, 1, 1, 5, ?)
            """, (ftp_old_id, session_id, old_seen))
            cur.execute("""
                INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page, dir_count, file_count, test_timestamp)
                VALUES (?, ?, 1, 200, 1, 2, 3, ?)
            """, (http_old_id, session_id, old_seen))

            cur.execute("INSERT INTO host_user_flags (server_id, favorite) VALUES (?, 1)", (smb_old_id,))
            cur.execute("INSERT INTO ftp_user_flags (server_id, favorite) VALUES (?, 1)", (ftp_old_id,))
            cur.execute("INSERT INTO http_user_flags (server_id, favorite) VALUES (?, 1)", (http_old_id,))

            cur.execute("INSERT INTO host_probe_cache (server_id, status) VALUES (?, 'probed')", (smb_old_id,))
            cur.execute("INSERT INTO ftp_probe_cache (server_id, status) VALUES (?, 'probed')", (ftp_old_id,))
            cur.execute("INSERT INTO http_probe_cache (server_id, status) VALUES (?, 'probed')", (http_old_id,))

            conn.commit()
            conn.close()

            engine = DBToolsEngine(db_path)
            preview = engine.preview_purge(30)

            assert preview.servers_to_delete == 3
            assert preview.shares_to_delete == 3  # share_access + ftp_access + http_access
            assert preview.user_flags_to_delete == 3
            assert preview.probe_cache_to_delete == 3

            result = engine.execute_purge(30)
            assert result['success'] is True
            assert result['servers_deleted'] == 3

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            assert cur.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0] == 1  # keep row remains
            assert cur.execute("SELECT COUNT(*) FROM ftp_servers").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM http_servers").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM share_access").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM ftp_access").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM http_access").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM host_user_flags").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM ftp_user_flags").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM http_user_flags").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM host_probe_cache").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM ftp_probe_cache").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM http_probe_cache").fetchone()[0] == 0
            conn.close()
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass

    def test_purge_skips_protocol_tables_missing_last_seen(self):
        """
        Purge should still process valid tables when another protocol table is missing last_seen.
        """
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript("""
                CREATE TABLE scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT NOT NULL
                );

                CREATE TABLE smb_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    last_seen DATETIME,
                    status TEXT DEFAULT 'active'
                );

                CREATE TABLE share_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    session_id INTEGER,
                    share_name TEXT NOT NULL,
                    accessible BOOLEAN DEFAULT FALSE,
                    test_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
                );

                -- Intentionally malformed legacy protocol table: no last_seen column.
                CREATE TABLE ftp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL UNIQUE,
                    status TEXT DEFAULT 'active'
                );
            """)

            conn.execute("INSERT INTO scan_sessions (scan_type) VALUES ('discover')")
            session_id = conn.execute("SELECT id FROM scan_sessions LIMIT 1").fetchone()[0]

            conn.execute("""
                INSERT INTO smb_servers (ip_address, last_seen, status)
                VALUES ('203.0.113.10', '2020-01-01 00:00:00', 'active')
            """)
            smb_id = conn.execute("SELECT id FROM smb_servers WHERE ip_address = '203.0.113.10'").fetchone()[0]
            conn.execute("""
                INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
                VALUES (?, ?, 'Public', 1, '2020-01-01 00:00:00')
            """, (smb_id, session_id))

            conn.execute("""
                INSERT INTO ftp_servers (ip_address, status)
                VALUES ('203.0.113.20', 'active')
            """)
            conn.commit()
            conn.close()

            engine = DBToolsEngine(db_path)
            preview = engine.preview_purge(30)

            assert preview.servers_to_delete == 1
            assert preview.shares_to_delete == 1

            result = engine.execute_purge(30)
            assert result['success'] is True
            assert result['servers_deleted'] == 1

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            assert cur.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM share_access").fetchone()[0] == 0
            # FTP row remains because table lacks purge timestamp column.
            assert cur.execute("SELECT COUNT(*) FROM ftp_servers").fetchone()[0] == 1
            conn.close()
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass

    def test_purge_cutoff_uses_true_day_subtraction(self, temp_db):
        """
        Purge cutoff must use actual day subtraction across month boundaries.

        Uses a day span greater than today's day-of-month to ensure month rollover.
        """
        engine = DBToolsEngine(temp_db)
        now_floor = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        days = now_floor.day + 5

        preview = engine.preview_purge(days)
        expected = (now_floor - timedelta(days=days)).strftime('%Y-%m-%d')

        assert preview.cutoff_date == expected


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
