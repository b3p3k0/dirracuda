"""Unit tests for _DBToolsDialogImportExportMixin tab construction and handlers."""

import os
import sys
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.db_tools_dialog_import_export_mixin import _DBToolsDialogImportExportMixin
from gui.utils.db_tools_engine import MergeConflictStrategy

import tkinter as tk

# ---------------------------------------------------------------------------
# Shared lightweight stubs
# ---------------------------------------------------------------------------

class _StubWidget:
    """Tracks config/pack calls. Replaces any Tkinter widget."""
    def __init__(self, name="widget"):
        self.name = name
        self.text = ""
        self.state = None
        self.packed = False
        self.forgotten = False
        self.children = []
        self._config = {}

    def config(self, **kw):
        self._config.update(kw)
        if "text" in kw:
            self.text = kw["text"]
        if "state" in kw:
            self.state = kw["state"]

    def pack(self, **kw):
        self.packed = True

    def pack_forget(self):
        self.forgotten = True

    def winfo_children(self):
        return self.children

    def destroy(self):
        pass


class _StubNotebook:
    """Records notebook.add() calls."""
    def __init__(self):
        self.added = []

    def add(self, widget, **kw):
        self.added.append((widget, kw))

    def pack(self, **kw):
        pass


class _StubTheme:
    """No-op theme stub that returns _StubWidget for all label/widget creation."""
    def apply_to_widget(self, widget, style):
        pass

    def create_styled_label(self, parent, text, style):
        w = _StubWidget(f"label:{text[:20]}")
        w.text = text
        return w

    def apply_theme_to_application(self, widget):
        pass


class _StubLabelFrame(_StubWidget):
    """Stub for tk.LabelFrame — records children added via pack."""
    def __init__(self, parent=None, text="", **kw):
        super().__init__(f"labelframe:{text}")
        self.parent = parent

    def pack(self, **kw):
        self.packed = True


# ---------------------------------------------------------------------------
# Base stub for import/export mixin — lightweight, no real Tk needed
# ---------------------------------------------------------------------------

class _StubImportExport(_DBToolsDialogImportExportMixin):
    """Minimal concrete stub wiring the mixin to controllable state."""

    def __init__(self):
        self.db_path = "/fake/smbseek.db"
        self.theme = _StubTheme()
        self.notebook = _StubNotebook()
        self.engine = MagicMock()
        self.operation_queue = queue.Queue()
        self.operation_thread = None

        # Import tab attrs (normally set in __init__ of DBToolsDialog)
        self.import_path_var = None
        self.import_status_label = _StubWidget("import_status")
        self.import_preview_frame = _StubWidget("import_preview")
        self.import_source_type = "db"
        self.merge_strategy_var = None
        self.auto_backup_var = None
        self.merge_button = _StubWidget("merge_button")
        self.last_completed_import_source = None

        self._show_progress_called_with = None

    # Stubs for methods that live in other mixins / the main class
    def _style_labelframe(self, frame):
        pass

    def _show_progress(self, msg):
        self._show_progress_called_with = msg

    def _merge_worker(self, *args, **kw):
        pass

    def _csv_import_worker(self, *args, **kw):
        pass

    def _export_worker(self, *args, **kw):
        pass

    def _backup_worker(self, *args, **kw):
        pass


# ---------------------------------------------------------------------------
# Heavier stub for _create_import_tab / _create_export_tab smoke tests
# ---------------------------------------------------------------------------

class _StubForTabSmoke(_StubImportExport):
    """Stub with patched tk constructors so no real display is needed."""

    def __init__(self):
        super().__init__()
        self._frames_created = []
        self._labelframes_created = []

    def _make_frame(self, parent=None, **kw):
        w = _StubWidget("Frame")
        self._frames_created.append(w)
        return w

    def _make_labelframe(self, parent=None, text="", **kw):
        w = _StubLabelFrame(parent, text)
        self._labelframes_created.append(w)
        return w


# ---------------------------------------------------------------------------
# Test 1 — MRO ownership
# ---------------------------------------------------------------------------

_MOVED_METHODS = [
    "_create_import_tab",
    "_browse_import_file",
    "_set_import_preview_text",
    "_normalize_import_source_path",
    "_is_last_completed_import_source",
    "_lock_import_source_until_changed",
    "_validate_import_file",
    "_validate_db_import_file",
    "_validate_csv_import_file",
    "_start_merge",
    "_create_export_tab",
    "_export_as",
    "_quick_backup",
]


def test_mro_ownership_for_moved_methods():
    for name in _MOVED_METHODS:
        method = getattr(_DBToolsDialogImportExportMixin, name)
        assert "_DBToolsDialogImportExportMixin" in method.__qualname__, (
            f"{name}.__qualname__ = {method.__qualname__!r}"
        )


# ---------------------------------------------------------------------------
# Test 2 — normalize_import_source_path
# ---------------------------------------------------------------------------

def test_normalize_import_source_path_normalizes_abs_realpath(tmp_path):
    stub = _StubImportExport()
    real_file = tmp_path / "test.db"
    real_file.touch()
    result = stub._normalize_import_source_path(str(real_file))
    assert result == os.path.abspath(os.path.realpath(str(real_file)))


# ---------------------------------------------------------------------------
# Test 3 — is_last_completed_import_source
# ---------------------------------------------------------------------------

def test_is_last_completed_import_source_true_and_false(tmp_path):
    stub = _StubImportExport()
    f = tmp_path / "src.db"
    f.touch()
    path = str(f)

    # False when last_completed_import_source is None
    assert not stub._is_last_completed_import_source(path)

    # True when it matches
    stub.last_completed_import_source = stub._normalize_import_source_path(path)
    assert stub._is_last_completed_import_source(path)

    # False for empty string
    assert not stub._is_last_completed_import_source("")

    # False for a different path
    other = tmp_path / "other.db"
    other.touch()
    assert not stub._is_last_completed_import_source(str(other))


# ---------------------------------------------------------------------------
# Tests 4 & 5 — validate_import_file routing
# ---------------------------------------------------------------------------

def test_validate_import_file_routes_csv():
    stub = _StubImportExport()
    stub._validate_csv_import_file = MagicMock()
    stub._validate_db_import_file = MagicMock()
    stub._validate_import_file("/some/file.csv")
    stub._validate_csv_import_file.assert_called_once_with("/some/file.csv")
    stub._validate_db_import_file.assert_not_called()


def test_validate_import_file_routes_db():
    stub = _StubImportExport()
    stub._validate_csv_import_file = MagicMock()
    stub._validate_db_import_file = MagicMock()
    stub._validate_import_file("/some/file.db")
    stub._validate_db_import_file.assert_called_once_with("/some/file.db")
    stub._validate_csv_import_file.assert_not_called()


# ---------------------------------------------------------------------------
# Tests 6 & 7 — validate_csv_import_file
# ---------------------------------------------------------------------------

def test_validate_csv_import_file_invalid_disables_button_and_sets_preview_error():
    stub = _StubImportExport()
    stub._set_import_preview_text = MagicMock()
    stub.engine.preview_csv_import.return_value = {
        "valid": False,
        "errors": ["bad header"],
    }
    stub._validate_csv_import_file("/data/hosts.csv")
    assert stub.merge_button.state == tk.DISABLED
    stub._set_import_preview_text.assert_called_once()
    preview_arg = stub._set_import_preview_text.call_args[0][0]
    assert "bad header" in preview_arg


def test_validate_csv_import_file_valid_enables_start_csv_import():
    stub = _StubImportExport()
    stub._set_import_preview_text = MagicMock()
    stub.engine.preview_csv_import.return_value = {
        "valid": True,
        "total_rows": 10,
        "valid_rows": 9,
        "skipped_rows": 1,
        "new_servers": 5,
        "existing_servers": 4,
        "protocol_counts": {"S": 5, "F": 3, "H": 1},
        "warnings": [],
    }
    stub._validate_csv_import_file("/data/hosts.csv")
    assert stub.merge_button.state == tk.NORMAL
    assert stub.merge_button.text == "Start CSV Import"


# ---------------------------------------------------------------------------
# Test 8 — start_merge: invalid path
# ---------------------------------------------------------------------------

def test_start_merge_invalid_path_shows_error():
    stub = _StubImportExport()
    stub.import_path_var = MagicMock()
    stub.import_path_var.get.return_value = "/nonexistent/path.db"
    stub.merge_strategy_var = MagicMock()
    with patch("gui.components.db_tools_dialog_import_export_mixin.messagebox.showerror") as mock_err, \
         patch("gui.components.db_tools_dialog_import_export_mixin.threading.Thread") as mock_thread:
        stub._start_merge()
        mock_err.assert_called_once()
        mock_thread.assert_not_called()


# ---------------------------------------------------------------------------
# Tests 9 & 10 — start_merge: valid paths
# ---------------------------------------------------------------------------

def test_start_merge_csv_path_starts_csv_worker_thread_when_confirmed(tmp_path):
    stub = _StubImportExport()
    csv_file = tmp_path / "hosts.csv"
    csv_file.touch()

    stub.import_path_var = MagicMock()
    stub.import_path_var.get.return_value = str(csv_file)
    stub.import_source_type = "csv"
    stub.merge_strategy_var = MagicMock()
    stub.merge_strategy_var.get.return_value = MergeConflictStrategy.KEEP_NEWER.value
    stub.auto_backup_var = MagicMock()
    stub.auto_backup_var.get.return_value = True

    with patch("gui.components.db_tools_dialog_import_export_mixin.messagebox.askyesno", return_value=True), \
         patch("gui.components.db_tools_dialog_import_export_mixin.threading.Thread") as mock_thread:
        mock_thread.return_value = MagicMock()
        stub._start_merge()
        assert mock_thread.called
        target = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        assert getattr(target, "__name__", None) == "_csv_import_worker"


def test_start_merge_db_path_starts_merge_worker_thread_when_confirmed(tmp_path):
    stub = _StubImportExport()
    db_file = tmp_path / "ext.db"
    db_file.touch()

    stub.import_path_var = MagicMock()
    stub.import_path_var.get.return_value = str(db_file)
    stub.import_source_type = "db"
    stub.merge_strategy_var = MagicMock()
    stub.merge_strategy_var.get.return_value = MergeConflictStrategy.KEEP_NEWER.value
    stub.auto_backup_var = MagicMock()
    stub.auto_backup_var.get.return_value = True

    with patch("gui.components.db_tools_dialog_import_export_mixin.messagebox.askyesno", return_value=True), \
         patch("gui.components.db_tools_dialog_import_export_mixin.threading.Thread") as mock_thread:
        mock_thread.return_value = MagicMock()
        stub._start_merge()
        assert mock_thread.called
        target = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        assert getattr(target, "__name__", None) == "_merge_worker"


# ---------------------------------------------------------------------------
# Test 11 — export_as
# ---------------------------------------------------------------------------

def test_export_as_with_filename_starts_export_worker_thread(tmp_path):
    stub = _StubImportExport()
    out_path = str(tmp_path / "export.db")

    with patch("gui.components.db_tools_dialog_import_export_mixin.filedialog.asksaveasfilename", return_value=out_path), \
         patch("gui.components.db_tools_dialog_import_export_mixin.threading.Thread") as mock_thread:
        mock_thread.return_value = MagicMock()
        stub._export_as()
        assert mock_thread.called
        target = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        assert getattr(target, "__name__", None) == "_export_worker"


# ---------------------------------------------------------------------------
# Test 12 — quick_backup
# ---------------------------------------------------------------------------

def test_quick_backup_starts_backup_worker_thread():
    stub = _StubImportExport()

    with patch("gui.components.db_tools_dialog_import_export_mixin.threading.Thread") as mock_thread:
        mock_thread.return_value = MagicMock()
        stub._quick_backup()
        assert stub._show_progress_called_with == "Creating backup..."
        assert mock_thread.called
        target = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        assert getattr(target, "__name__", None) == "_backup_worker"


# ---------------------------------------------------------------------------
# Tests 13 & 14 — tab construction smoke tests (no real Tk)
# ---------------------------------------------------------------------------

def test_create_import_tab_smoke():
    stub = _StubImportExport()

    with patch("gui.components.db_tools_dialog_import_export_mixin.tk.Frame", return_value=_StubWidget("Frame")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.LabelFrame", side_effect=lambda *a, **kw: _StubLabelFrame(**kw)), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.Entry", return_value=_StubWidget("Entry")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.Button", return_value=_StubWidget("Button")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.Radiobutton", return_value=_StubWidget("Radiobutton")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.Checkbutton", return_value=_StubWidget("Checkbutton")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.StringVar", return_value=MagicMock()), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.BooleanVar", return_value=MagicMock()):
        stub._create_import_tab()

    assert len(stub.notebook.added) == 1
    _, kw = stub.notebook.added[0]
    assert kw.get("text") == "Import & Merge"
    assert stub.merge_button is not None


def test_create_export_tab_smoke():
    stub = _StubImportExport()

    with patch("gui.components.db_tools_dialog_import_export_mixin.tk.Frame", return_value=_StubWidget("Frame")), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.LabelFrame", side_effect=lambda *a, **kw: _StubLabelFrame(**kw)), \
         patch("gui.components.db_tools_dialog_import_export_mixin.tk.Button", return_value=_StubWidget("Button")):
        stub._create_export_tab()

    assert len(stub.notebook.added) == 1
    _, kw = stub.notebook.added[0]
    assert kw.get("text") == "Export & Backup"
