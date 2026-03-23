"""Unit tests for _DBToolsDialogOperationsMixin worker and progress methods."""

import queue
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.db_tools_dialog_operations_mixin import _DBToolsDialogOperationsMixin
from gui.utils.db_tools_engine import MergeConflictStrategy, MergeResult, CSVImportResult

import tkinter as tk

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

class _StubWidget:
    """Tracks pack/config/start/stop calls. Mirrors _StubLabel/_StubButton pattern."""
    def __init__(self):
        self.text = ""
        self.state = None
        self.packed = False
        self.forgotten = False
        self.started = False
        self.stopped = False

    def config(self, **kw):
        if "text" in kw:
            self.text = kw["text"]
        if "state" in kw:
            self.state = kw["state"]

    def pack(self, **kw):
        self.packed = True

    def pack_forget(self):
        self.forgotten = True

    def start(self, interval):
        self.started = True

    def stop(self):
        self.stopped = True


class _StubDialog:
    def winfo_exists(self):
        return False  # prevents after() reschedule in _process_operation_queue


# Stub A: no method overrides — used for test_show_progress / test_hide_progress
class _StubFull(_DBToolsDialogOperationsMixin):
    def __init__(self):
        self.operation_queue = queue.Queue()
        self.engine = None
        self.progress_label = _StubWidget()
        self.progress_bar = _StubWidget()
        self.close_button = _StubWidget()
        self.notebook = _StubWidget()
        self.on_database_changed = None
        self.dialog = _StubDialog()


# Stub B: overrides _hide_progress; used for queue-processing and worker tests
class _StubQueue(_DBToolsDialogOperationsMixin):
    def __init__(self):
        self.operation_queue = queue.Queue()
        self.engine = MagicMock()
        self.progress_label = _StubWidget()
        self.progress_bar = _StubWidget()
        self.close_button = _StubWidget()
        self.notebook = _StubWidget()
        self.on_database_changed = None
        self.dialog = _StubDialog()
        self._refresh_stats_called = False
        self._lock_source_called_with = None

    def _hide_progress(self):
        pass  # no-op — queue tests don't verify widget state

    def _refresh_stats(self):
        self._refresh_stats_called = True

    def _lock_import_source_until_changed(self, path):
        self._lock_source_called_with = path


_STRATEGY = MergeConflictStrategy.KEEP_NEWER

# ---------------------------------------------------------------------------
# Progress widget tests (use _StubFull so real mixin code runs)
# ---------------------------------------------------------------------------

def test_show_progress_disables_close_and_hides_notebook():
    stub = _StubFull()
    stub._show_progress("Loading...")

    assert stub.progress_label.text == "Loading..."
    assert stub.progress_bar.started is True
    assert stub.close_button.state == tk.DISABLED
    assert stub.notebook.forgotten is True


def test_hide_progress_enables_close_and_shows_notebook():
    stub = _StubFull()
    stub._hide_progress()

    assert stub.progress_bar.stopped is True
    assert stub.progress_bar.forgotten is True
    assert stub.progress_label.forgotten is True
    assert stub.close_button.state == tk.NORMAL
    assert stub.notebook.packed is True


# ---------------------------------------------------------------------------
# Queue processing tests (use _StubQueue)
# ---------------------------------------------------------------------------

def test_process_queue_progress_updates_label():
    stub = _StubQueue()
    stub.operation_queue.put({'type': 'progress', 'percent': 50, 'message': 'halfway'})

    stub._process_operation_queue()

    assert stub.progress_label.text == "halfway"


def test_process_queue_complete_success_runs_refresh_and_callback():
    callback_called = []
    stub = _StubQueue()
    stub.on_database_changed = lambda: callback_called.append(True)
    stub.operation_queue.put({
        'type': 'complete',
        'success': True,
        'message': 'done',
        'refresh_needed': True,
    })

    with patch("gui.components.db_tools_dialog_operations_mixin.messagebox.showinfo"):
        stub._process_operation_queue()

    assert callback_called == [True]
    assert stub._refresh_stats_called is True


def test_process_queue_complete_failure_shows_error():
    stub = _StubQueue()
    stub.operation_queue.put({
        'type': 'complete',
        'success': False,
        'error': 'boom',
    })

    with patch("gui.components.db_tools_dialog_operations_mixin.messagebox.showerror") as mock_err:
        stub._process_operation_queue()

    mock_err.assert_called_once()
    call_args = mock_err.call_args[0]
    assert "boom" in call_args[1]


def test_process_queue_complete_import_completed_locks_source():
    stub = _StubQueue()
    stub.operation_queue.put({
        'type': 'complete',
        'success': True,
        'import_completed': True,
        'import_path': '/x.db',
        'message': 'ok',
    })

    with patch("gui.components.db_tools_dialog_operations_mixin.messagebox.showinfo"):
        stub._process_operation_queue()

    assert stub._lock_source_called_with == '/x.db'


# ---------------------------------------------------------------------------
# _merge_worker tests
# ---------------------------------------------------------------------------

def test_merge_worker_success_enqueues_complete_payload():
    stub = _StubQueue()
    stub.engine.merge_database.return_value = MergeResult(
        success=True,
        servers_added=3,
        servers_updated=1,
        servers_skipped=0,
        shares_imported=5,
        vulnerabilities_imported=2,
        file_manifests_imported=0,
        duration_seconds=1.2,
    )

    stub._merge_worker("/fake/path.db", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert payload['refresh_needed'] is True
    assert payload['import_completed'] is True
    assert payload['import_path'] == "/fake/path.db"


def test_merge_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.merge_database.side_effect = Exception("connection refused")

    stub._merge_worker("/fake/path.db", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == "connection refused"


def test_merge_worker_structured_failure_enqueues_error_text():
    stub = _StubQueue()
    stub.engine.merge_database.return_value = MergeResult(
        success=False,
        errors=["schema mismatch"],
    )

    stub._merge_worker("/fake.db", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert "schema mismatch" in payload['error']


# ---------------------------------------------------------------------------
# _csv_import_worker tests
# ---------------------------------------------------------------------------

def test_csv_import_worker_success_enqueues_complete_payload():
    stub = _StubQueue()
    stub.engine.import_csv_hosts.return_value = CSVImportResult(
        success=True,
        rows_total=10,
        rows_valid=9,
        rows_skipped=1,
        servers_added=8,
        servers_updated=1,
        protocol_counts={'S': 5, 'F': 3, 'H': 1},
        duration_seconds=0.5,
    )

    stub._csv_import_worker("/fake/hosts.csv", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert payload['import_completed'] is True
    assert payload['import_path'] == "/fake/hosts.csv"


def test_csv_import_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.import_csv_hosts.side_effect = Exception("csv fail")

    stub._csv_import_worker("/fake/hosts.csv", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == "csv fail"


def test_csv_import_worker_structured_failure_enqueues_error_text():
    stub = _StubQueue()
    stub.engine.import_csv_hosts.return_value = CSVImportResult(
        success=False,
        errors=["bad column"],
    )

    stub._csv_import_worker("/fake.csv", _STRATEGY, False)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert "bad column" in payload['error']


# ---------------------------------------------------------------------------
# _export_worker tests
# ---------------------------------------------------------------------------

def test_export_worker_success_enqueues_complete_payload():
    stub = _StubQueue()
    stub.engine.export_database.return_value = {
        'success': True,
        'output_path': '/tmp/out.db',
        'size_bytes': 1048576,
    }

    stub._export_worker("/tmp/out.db")

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert "1.00 MB" in payload['message']


def test_export_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.export_database.return_value = {
        'success': False,
        'error': 'disk full',
    }

    stub._export_worker("/tmp/out.db")

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == 'disk full'


# ---------------------------------------------------------------------------
# _backup_worker tests
# ---------------------------------------------------------------------------

def test_backup_worker_success_enqueues_complete_payload():
    stub = _StubQueue()
    stub.engine.quick_backup.return_value = {
        'success': True,
        'backup_path': '/tmp/backup.db',
        'size_bytes': 524288,
    }

    stub._backup_worker()

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert "0.50 MB" in payload['message']


def test_backup_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.quick_backup.return_value = {
        'success': False,
        'error': 'permission denied',
    }

    stub._backup_worker()

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == 'permission denied'


# ---------------------------------------------------------------------------
# _vacuum_worker tests
# ---------------------------------------------------------------------------

def test_vacuum_worker_success_enqueues_refresh_needed():
    stub = _StubQueue()
    stub.engine.vacuum_database.return_value = {
        'success': True,
        'space_saved': 204800,
        'size_before': 2097152,
        'size_after': 1892352,
    }

    stub._vacuum_worker()

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert payload.get('refresh_needed') is True
    assert "200.0 KB" in payload['message']


def test_vacuum_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.vacuum_database.return_value = {
        'success': False,
        'error': 'locked',
    }

    stub._vacuum_worker()

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == 'locked'


# ---------------------------------------------------------------------------
# _purge_worker tests
# ---------------------------------------------------------------------------

def test_purge_worker_success_enqueues_refresh_needed():
    stub = _StubQueue()
    stub.engine.execute_purge.return_value = {
        'success': True,
        'servers_deleted': 5,
        'total_records_deleted': 20,
    }

    stub._purge_worker(30)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is True
    assert payload.get('refresh_needed') is True


def test_purge_worker_failure_enqueues_error():
    stub = _StubQueue()
    stub.engine.execute_purge.return_value = {
        'success': False,
        'error': 'constraint violated',
    }

    stub._purge_worker(30)

    payload = stub.operation_queue.get_nowait()
    assert payload['type'] == 'complete'
    assert payload['success'] is False
    assert payload['error'] == 'constraint violated'
