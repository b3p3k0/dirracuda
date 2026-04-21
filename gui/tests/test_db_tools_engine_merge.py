"""DBToolsEngine merge-related tests split from test_db_tools_engine.py."""

from gui.tests.test_db_tools_engine import *  # noqa: F401,F403

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
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tool_name, scan_type FROM scan_sessions WHERE scan_type = 'db_import'"
        )
        import_session = cursor.fetchone()
        conn.close()

        assert import_session is not None
        assert import_session["tool_name"] == "db_import"
        assert import_session["scan_type"] == "db_import"

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
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            ext_db = f.name
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

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            ext_db = f.name
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


