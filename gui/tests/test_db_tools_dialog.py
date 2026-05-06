"""Unit tests for DBToolsDialog import-button state behavior."""

import sys
import queue
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.db_tools_dialog import DBToolsDialog
from gui.components.data_import_dialog import DataImportDialog


class _StubLabel:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _StubButton:
    def __init__(self):
        self.state = None
        self.text = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]


class _StubEngine:
    def validate_external_schema(self, _path):
        return SimpleNamespace(valid=True, errors=[])

    def preview_merge(self, _path):
        return {
            "valid": True,
            "external_servers": 1,
            "new_servers": 1,
            "existing_servers": 0,
            "total_shares": 1,
            "total_vulnerabilities": 0,
            "total_file_manifests": 0,
            "warnings": [],
        }


def _make_dialog_stub() -> DBToolsDialog:
    dlg = DBToolsDialog.__new__(DBToolsDialog)
    dlg.import_status_label = _StubLabel()
    dlg.merge_button = _StubButton()
    dlg.last_completed_import_source = None
    dlg.engine = _StubEngine()
    dlg.import_source_type = "db"
    dlg._set_import_preview_text = lambda _text: None
    return dlg


def test_lock_import_source_disables_merge_button():
    dlg = _make_dialog_stub()

    dlg._lock_import_source_until_changed("/tmp/source_a.db")

    assert dlg._is_last_completed_import_source("/tmp/source_a.db") is True
    assert dlg.merge_button.state == tk.DISABLED
    assert dlg.merge_button.text == "Start Import"
    assert "Import complete" in dlg.import_status_label.text


def test_validate_db_import_same_source_stays_disabled():
    dlg = _make_dialog_stub()
    source = "/tmp/source_a.db"
    dlg.last_completed_import_source = dlg._normalize_import_source_path(source)

    dlg._validate_db_import_file(source)

    assert dlg.merge_button.state == tk.DISABLED
    assert dlg.merge_button.text == "Start Import"
    assert "already completed for this source" in dlg.import_status_label.text.lower()


def test_validate_db_import_new_source_reenables_merge_button():
    dlg = _make_dialog_stub()
    dlg.last_completed_import_source = dlg._normalize_import_source_path("/tmp/source_a.db")

    dlg._validate_db_import_file("/tmp/source_b.db")

    assert dlg.merge_button.state == tk.NORMAL
    assert dlg.merge_button.text == "Start Merge"
    assert "validated successfully" in dlg.import_status_label.text.lower()


def test_process_operation_queue_invokes_database_changed_callback(monkeypatch):
    dlg = DBToolsDialog.__new__(DBToolsDialog)
    dlg.operation_queue = queue.Queue()
    dlg.operation_queue.put({
        "type": "complete",
        "success": True,
        "message": "done",
        "refresh_needed": True,
    })
    dlg.dialog = SimpleNamespace(winfo_exists=lambda: False)
    dlg._hide_progress = MagicMock()
    dlg._lock_import_source_until_changed = MagicMock()
    dlg._refresh_stats = MagicMock()
    dlg.on_database_changed = MagicMock()
    monkeypatch.setattr(
        "gui.components.db_tools_dialog.messagebox.showinfo",
        MagicMock(),
    )

    dlg._process_operation_queue()

    dlg.on_database_changed.assert_called_once()
    dlg._refresh_stats.assert_called_once()


def test_data_import_notify_database_changed_invokes_callback():
    dlg = DataImportDialog.__new__(DataImportDialog)
    dlg.on_database_changed = MagicMock()

    dlg._notify_database_changed()

    dlg.on_database_changed.assert_called_once()


def test_format_country_distribution_top5_ordered_with_percentages():
    dlg = DBToolsDialog.__new__(DBToolsDialog)
    countries = {
        "US": 50,
        "DE": 30,
        "GB": 10,
        "CA": 5,
        "FR": 3,
        "JP": 2,
    }

    text = dlg._format_country_distribution(countries, limit=5)
    lines = text.splitlines()

    assert len(lines) == 5
    assert lines[0] == "1. US: 50 (50.0%)"
    assert lines[1] == "2. DE: 30 (30.0%)"
    assert lines[2] == "3. GB: 10 (10.0%)"
    assert lines[3] == "4. CA: 5 (5.0%)"
    assert lines[4] == "5. FR: 3 (3.0%)"
    assert "JP" not in text


def test_format_country_distribution_uses_all_countries_as_denominator():
    dlg = DBToolsDialog.__new__(DBToolsDialog)
    countries = {
        "US": 49,
        "DE": 30,
        "GB": 10,
        "CA": 5,
        "FR": 4,
        "JP": 2,
    }

    text = dlg._format_country_distribution(countries, limit=5)
    # Total is 100 including JP; FR should be 4.0% (not 4.1% from top-5 subtotal of 98)
    assert "5. FR: 4 (4.0%)" in text


def test_format_country_distribution_empty():
    dlg = DBToolsDialog.__new__(DBToolsDialog)
    assert dlg._format_country_distribution({}, limit=5) == "No country data available"
