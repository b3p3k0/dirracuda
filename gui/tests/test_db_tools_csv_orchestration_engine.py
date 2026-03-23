"""
Tests for gui/utils/db_tools_csv_orchestration_engine.py

Tests cover the module function directly via lightweight engine stubs,
plus one adapter wiring test against the real DBToolsEngine.
"""

import os
import sqlite3
import tempfile
import logging
from unittest.mock import patch, MagicMock

from gui.utils import db_tools_csv_orchestration_engine as _orch
from gui.utils.db_tools_engine import DBToolsEngine, CSVImportResult

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_SCHEMA = """
CREATE TABLE smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_temp_db() -> str:
    """Create a minimal temp SQLite DB; caller is responsible for os.unlink."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _make_temp_csv(content: bytes = b'ip_address,host_type\n') -> str:
    """Create a minimal temp CSV file; caller is responsible for os.unlink."""
    with tempfile.NamedTemporaryFile(
        mode='wb', suffix='.csv', delete=False
    ) as f:
        f.write(content)
        return f.name


class FakeEngine:
    """Minimal engine stub for testing the orchestration module directly."""

    def __init__(self, db_path: str):
        self.current_db_path = db_path

    def create_backup(self):
        return {'success': True, 'backup_path': '/tmp/fake_backup.db'}

    def _check_disk_space(self, required, directory):
        return True

    def _analyze_csv_hosts(self, csv_path, conn, include_rows=False):
        return {
            'rows_total': 0,
            'rows_valid': 0,
            'rows_skipped': 0,
            'protocol_counts': {'S': 0, 'F': 0, 'H': 0},
            'warnings': [],
            'errors': [],
            'rows': [],
        }

    def _create_import_session(self, conn, filename):
        return 1

    def _upsert_csv_smb_row(self, conn, row, strategy):
        return (0, 0, 0)

    def _upsert_csv_ftp_row(self, conn, row, strategy):
        return (0, 0, 0)

    def _upsert_csv_http_row(self, conn, row, strategy):
        return (0, 0, 0)

    def _finalize_import_session(self, conn, session_id, count):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCSVOrchestrationModule:

    def test_import_csv_hosts_missing_file_returns_error(self):
        result = _orch.import_csv_hosts(
            engine=MagicMock(),
            csv_path='/nonexistent/__does_not_exist__.csv',
            strategy=None,
            auto_backup=False,
            progress_callback=None,
            result_factory=CSVImportResult,
            logger=_log,
        )
        assert result.success is False
        assert any('not found' in e for e in result.errors)

    def test_import_csv_hosts_no_valid_rows_returns_error(self):
        db_path = _make_temp_db()
        csv_path = _make_temp_csv(b'ip_address,host_type\n')  # header only
        try:
            engine = FakeEngine(db_path)
            # Default FakeEngine._analyze_csv_hosts returns rows=[], which
            # triggers the "No valid CSV rows" early exit.
            result = _orch.import_csv_hosts(
                engine=engine,
                csv_path=csv_path,
                strategy=None,
                auto_backup=False,
                progress_callback=None,
                result_factory=CSVImportResult,
                logger=_log,
            )
            assert result.success is False
            assert any('No valid CSV rows' in e for e in result.errors)
        finally:
            os.unlink(csv_path)
            os.unlink(db_path)

    def test_import_csv_hosts_success_sets_success_and_counts(self):
        db_path = _make_temp_db()
        csv_path = _make_temp_csv(b'ip_address,host_type\n1.2.3.4,S\n')
        try:
            engine = FakeEngine(db_path)
            engine._analyze_csv_hosts = lambda csv_path, conn, include_rows=False: {
                'rows_total': 1,
                'rows_valid': 1,
                'rows_skipped': 0,
                'protocol_counts': {'S': 1, 'F': 0, 'H': 0},
                'warnings': [],
                'errors': [],
                'rows': [{'host_type': 'S', 'ip_address': '1.2.3.4'}],
            }
            engine._upsert_csv_smb_row = lambda conn, row, strategy: (1, 0, 0)

            result = _orch.import_csv_hosts(
                engine=engine,
                csv_path=csv_path,
                strategy=None,
                auto_backup=False,
                progress_callback=None,
                result_factory=CSVImportResult,
                logger=_log,
            )
            assert result.success is True
            assert result.servers_added == 1
            assert result.servers_updated == 0
        finally:
            os.unlink(csv_path)
            os.unlink(db_path)

    def test_import_csv_hosts_exception_records_error_and_keeps_success_false(self):
        # Inject failure via _create_import_session, which runs after BEGIN IMMEDIATE.
        # This exercises the rollback path.
        db_path = _make_temp_db()
        csv_path = _make_temp_csv(b'ip_address,host_type\n1.2.3.4,S\n')
        try:
            engine = FakeEngine(db_path)
            engine._analyze_csv_hosts = lambda csv_path, conn, include_rows=False: {
                'rows_total': 1,
                'rows_valid': 1,
                'rows_skipped': 0,
                'protocol_counts': {'S': 1, 'F': 0, 'H': 0},
                'warnings': [],
                'errors': [],
                'rows': [{'host_type': 'S', 'ip_address': '1.2.3.4'}],
            }
            engine._create_import_session = MagicMock(
                side_effect=RuntimeError("injected session error")
            )

            result = _orch.import_csv_hosts(
                engine=engine,
                csv_path=csv_path,
                strategy=None,
                auto_backup=False,
                progress_callback=None,
                result_factory=CSVImportResult,
                logger=_log,
            )
            assert result.success is False
            assert result.errors

            # Rollback check: no rows committed to smb_servers.
            conn = sqlite3.connect(db_path)
            row_count = conn.execute("SELECT COUNT(*) FROM smb_servers").fetchone()[0]
            conn.close()
            assert row_count == 0
        finally:
            os.unlink(csv_path)
            os.unlink(db_path)


class TestDBToolsEngineAdapterWiring:

    def test_dbtoolsengine_adapter_delegates_to_orchestration_module(self):
        db_path = _make_temp_db()
        try:
            with patch(
                'gui.utils.db_tools_engine._csv_orch.import_csv_hosts'
            ) as mock_fn:
                mock_fn.return_value = CSVImportResult(success=True)
                engine = DBToolsEngine(db_path)
                engine.import_csv_hosts(csv_path='x.csv', auto_backup=False)

            assert mock_fn.call_count == 1
            kw = mock_fn.call_args.kwargs
            assert kw['engine'] is engine
            assert kw['csv_path'] == 'x.csv'
            assert kw['auto_backup'] is False
            assert kw['result_factory'] is CSVImportResult
            assert kw['logger'] is not None
        finally:
            os.unlink(db_path)
