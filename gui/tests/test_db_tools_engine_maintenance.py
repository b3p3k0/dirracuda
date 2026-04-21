"""DBToolsEngine export/statistics/maintenance tests split from test_db_tools_engine.py."""

from gui.tests.test_db_tools_engine import *  # noqa: F401,F403

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

    def test_stats_counts_mixed_legacy_and_canonical_session_labels(self):
        """Statistics include both legacy and canonical SMB session labels."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            run_migrations(db_path)
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets, successful_targets, failed_targets)
                VALUES ('smbseek', 'discover', 'completed', 1, 1, 0)
                """
            )
            cur.execute(
                """
                INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets, successful_targets, failed_targets)
                VALUES ('dirracuda', 'smbseek_unified', 'completed', 1, 1, 0)
                """
            )
            conn.commit()
            conn.close()

            engine = DBToolsEngine(db_path)
            stats = engine.get_database_stats()

            assert stats.total_sessions == 2
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


