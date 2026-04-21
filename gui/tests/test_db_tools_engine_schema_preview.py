"""DBToolsEngine schema + preview tests split from test_db_tools_engine.py."""

from gui.tests.test_db_tools_engine import *  # noqa: F401,F403

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


