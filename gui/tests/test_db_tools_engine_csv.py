"""DBToolsEngine CSV import tests split from test_db_tools_engine.py."""

from gui.tests.test_db_tools_engine import *  # noqa: F401,F403

class TestCSVHostImport:
    """Tests for CSV host preview/import in DB Tools engine."""

    def _write_csv(self, headers, rows) -> str:
        """Create a temporary CSV file for import tests."""
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.csv',
            delete=False,
            newline='',
            encoding='utf-8',
        ) as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return f.name

    def test_preview_csv_import_counts_valid_new_and_existing(self, temp_db):
        """Preview reports valid/skipped/new/existing counts by protocol."""
        run_migrations(temp_db)

        conn = sqlite3.connect(temp_db)
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, scan_count, status)
            VALUES ('198.51.100.1', 'US', 'guest', '2026-03-01 00:00:00', '2026-03-01 00:00:00', 1, 'active')
        """)
        conn.commit()
        conn.close()

        csv_path = self._write_csv(
            headers=['ip_address', 'host_type', 'last_seen', 'country', 'scheme'],
            rows=[
                {'ip_address': '198.51.100.1', 'host_type': 'S', 'last_seen': '2026-03-02 00:00:00', 'country': 'US', 'scheme': ''},
                {'ip_address': '198.51.100.2', 'host_type': 'F', 'last_seen': '2026-03-02 00:00:00', 'country': 'US', 'scheme': ''},
                {'ip_address': '198.51.100.3', 'host_type': 'H', 'last_seen': '2026-03-02 00:00:00', 'country': 'US', 'scheme': 'https'},
                {'ip_address': '198.51.100.4', 'host_type': 'X', 'last_seen': '2026-03-02 00:00:00', 'country': 'US', 'scheme': ''},
                {'ip_address': '', 'host_type': 'S', 'last_seen': '2026-03-02 00:00:00', 'country': 'US', 'scheme': ''},
            ],
        )

        try:
            engine = DBToolsEngine(temp_db)
            preview = engine.preview_csv_import(csv_path)

            assert preview['valid'] is True
            assert preview['total_rows'] == 5
            assert preview['valid_rows'] == 3
            assert preview['skipped_rows'] == 2
            assert preview['new_servers'] == 2
            assert preview['existing_servers'] == 1
            assert preview['protocol_counts']['S'] == 1
            assert preview['protocol_counts']['F'] == 1
            assert preview['protocol_counts']['H'] == 1
        finally:
            os.unlink(csv_path)

    def test_import_csv_keep_newer_updates_only_when_newer(self, temp_db):
        """KEEP_NEWER updates newer row, skips older row, and inserts new row."""
        conn = sqlite3.connect(temp_db)
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, auth_method, first_seen, last_seen, scan_count, status)
            VALUES ('203.0.113.10', 'US', 'guest', '2026-03-01 00:00:00', '2026-03-10 00:00:00', 1, 'active')
        """)
        conn.commit()
        conn.close()

        csv_path = self._write_csv(
            headers=['ip_address', 'host_type', 'last_seen', 'auth_method', 'country'],
            rows=[
                {'ip_address': '203.0.113.10', 'host_type': 'S', 'last_seen': '2026-03-09 00:00:00', 'auth_method': 'anonymous', 'country': 'US'},
                {'ip_address': '203.0.113.10', 'host_type': 'S', 'last_seen': '2026-03-11 00:00:00', 'auth_method': 'anonymous', 'country': 'US'},
                {'ip_address': '203.0.113.20', 'host_type': 'S', 'last_seen': '2026-03-11 00:00:00', 'auth_method': 'anonymous', 'country': 'US'},
            ],
        )

        try:
            engine = DBToolsEngine(temp_db)
            result = engine.import_csv_hosts(
                csv_path,
                strategy=MergeConflictStrategy.KEEP_NEWER,
                auto_backup=False,
            )

            assert result.success is True
            assert result.rows_total == 3
            assert result.rows_valid == 3
            assert result.servers_added == 1
            assert result.servers_updated == 1
            assert result.servers_skipped == 1

            conn = sqlite3.connect(temp_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT auth_method, last_seen FROM smb_servers WHERE ip_address = '203.0.113.10'"
            ).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
            conn.close()

            assert count == 2
            assert row['auth_method'] == 'anonymous'
            assert row['last_seen'] == '2026-03-11 00:00:00'
        finally:
            os.unlink(csv_path)

    def test_import_csv_skips_unsupported_protocol_rows_on_legacy_db(self, temp_db):
        """Legacy DBs without FTP/HTTP tables still import SMB CSV rows safely."""
        csv_path = self._write_csv(
            headers=['ip_address', 'host_type', 'last_seen', 'country'],
            rows=[
                {'ip_address': '192.0.2.10', 'host_type': 'S', 'last_seen': '2026-03-05 00:00:00', 'country': 'US'},
                {'ip_address': '192.0.2.20', 'host_type': 'F', 'last_seen': '2026-03-05 00:00:00', 'country': 'US'},
            ],
        )

        try:
            engine = DBToolsEngine(temp_db)
            preview = engine.preview_csv_import(csv_path)
            assert preview['valid'] is True
            assert preview['valid_rows'] == 1
            assert preview['skipped_rows'] == 1
            assert any("ftp_servers" in warning for warning in preview.get('warnings', []))

            result = engine.import_csv_hosts(
                csv_path,
                strategy=MergeConflictStrategy.KEEP_SOURCE,
                auto_backup=False,
            )
            assert result.success is True
            assert result.rows_valid == 1
            assert result.rows_skipped == 1
            assert result.servers_added == 1

            conn = sqlite3.connect(temp_db)
            count = conn.execute(
                "SELECT COUNT(*) FROM smb_servers WHERE ip_address = '192.0.2.10'"
            ).fetchone()[0]
            conn.close()
            assert count == 1
        finally:
            os.unlink(csv_path)

    def test_preview_csv_import_requires_ip_address_column(self, temp_db):
        """Preview fails fast when required ip_address column is missing."""
        csv_path = self._write_csv(
            headers=['ip', 'host_type', 'last_seen'],
            rows=[
                {'ip': '198.51.100.50', 'host_type': 'S', 'last_seen': '2026-03-10 00:00:00'},
            ],
        )

        try:
            engine = DBToolsEngine(temp_db)
            preview = engine.preview_csv_import(csv_path)
            assert preview['valid'] is False
            assert any("ip_address" in err for err in preview.get('errors', []))

            result = engine.import_csv_hosts(csv_path, auto_backup=False)
            assert result.success is False
            assert any("ip_address" in err for err in result.errors)
        finally:
            os.unlink(csv_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
