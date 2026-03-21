"""Unit tests for DBToolsDialog import-button state behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.db_tools_dialog import DBToolsDialog


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
