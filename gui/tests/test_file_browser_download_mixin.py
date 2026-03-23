"""
Tests for gui/components/file_browser_download_mixin.py.

Uses __new__-based stubs so no Tk window is required.
"""

import sys
import tkinter as tk
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Break the pre-existing circular import:
#   file_browser_window → server_list_window/__init__ → window.py → file_browser_window
# Stub the package before any real import touches it.
_slw = types.ModuleType("gui.components.server_list_window")
_slw.__path__ = []
_details = types.ModuleType("gui.components.server_list_window.details")
_details._derive_credentials = lambda _auth: ("", "")
_slw.details = _details
sys.modules.setdefault("gui.components.server_list_window", _slw)
sys.modules.setdefault("gui.components.server_list_window.details", _details)

from gui.components.file_browser_window import FileBrowserWindow  # noqa: E402
from gui.components.file_browser_download_mixin import _FileBrowserDownloadMixin  # noqa: E402
from gui.components.batch_extract_dialog import NO_EXTENSION_TOKEN  # noqa: E402


# ---------------------------------------------------------------------------
# Stub factory
# ---------------------------------------------------------------------------

def _make_win() -> FileBrowserWindow:
    """Create a minimal FileBrowserWindow without calling __init__ or Tk."""
    win = FileBrowserWindow.__new__(FileBrowserWindow)
    win.ip_address = "10.0.0.1"
    win.current_share = "share$"
    win.current_path = "\\"
    win.config_path = None
    win.config = {}
    win.theme = None
    win.settings_manager = None
    win.db_reader = None
    win.on_extracted = None
    win.window = MagicMock()
    win.navigator = MagicMock()
    win.download_cancel_event = None
    win.btn_cancel = MagicMock()
    win.download_workers = 1
    win.download_large_mb = 25
    win.workers_var = MagicMock()
    win.workers_var.get.return_value = "1"
    win.large_mb_var = MagicMock()
    win.large_mb_var.get.return_value = "25"
    win.list_thread = None
    win.download_thread = None
    win._set_status = MagicMock()
    win._set_busy = MagicMock()
    win._safe_after = MagicMock()
    return win


# ---------------------------------------------------------------------------
# MRO resolution
# ---------------------------------------------------------------------------

def test_mro_resolution_for_extracted_methods():
    """All 8 extracted method names must resolve from _FileBrowserDownloadMixin."""
    expected_methods = [
        "_on_cancel",
        "_prompt_extract_options",
        "_start_list_thread",
        "_start_download_thread",
        "_expand_directories",
        "_should_include_extension",
        "_handle_extracted_success",
        "_map_download_error",
    ]
    mro_source = {}
    for cls in FileBrowserWindow.__mro__:
        for name in vars(cls):
            if name not in mro_source:
                mro_source[name] = cls
    for method in expected_methods:
        assert mro_source.get(method) is _FileBrowserDownloadMixin, (
            f"{method} not resolved from _FileBrowserDownloadMixin"
        )


# ---------------------------------------------------------------------------
# _should_include_extension
# ---------------------------------------------------------------------------

def test_should_include_extension_download_all():
    win = _make_win()
    assert win._should_include_extension("file.txt", "download_all", [], []) is True
    assert win._should_include_extension("noext", "download_all", [], []) is True


def test_should_include_extension_allow_only_and_no_extension_token():
    win = _make_win()
    token = NO_EXTENSION_TOKEN.lower()
    assert win._should_include_extension("file.txt", "allow_only", [".txt"], []) is True
    assert win._should_include_extension("file.pdf", "allow_only", [".txt"], []) is False
    assert win._should_include_extension("noext", "allow_only", [token], []) is True
    assert win._should_include_extension("noext", "allow_only", [".txt"], []) is False


def test_should_include_extension_deny_only():
    win = _make_win()
    assert win._should_include_extension("evil.exe", "deny_only", [], [".exe"]) is False
    assert win._should_include_extension("doc.txt", "deny_only", [], [".exe"]) is True


# ---------------------------------------------------------------------------
# _map_download_error
# ---------------------------------------------------------------------------

def test_map_download_error_protocol_timeout_cancel_default():
    f = FileBrowserWindow._map_download_error

    result = f(Exception("ProtocolID mismatch"))
    assert "Unexpected SMB response" in result

    result = f(Exception("Connection timed out"))
    assert "timed out" in result.lower()

    result = f(Exception("Operation Cancelled"))
    assert "cancelled" in result.lower()

    result = f(Exception("Some other error"))
    assert result == "Some other error"


# ---------------------------------------------------------------------------
# _handle_extracted_success
# ---------------------------------------------------------------------------

def test_handle_extracted_success_callback_path():
    win = _make_win()
    callback = MagicMock()
    win.on_extracted = callback
    win._handle_extracted_success()
    callback.assert_called_once_with("10.0.0.1")


def test_handle_extracted_success_db_reader_fallback_path():
    win = _make_win()
    win.on_extracted = None
    mock_db = MagicMock()
    win.db_reader = mock_db
    win._handle_extracted_success()
    mock_db.upsert_extracted_flag.assert_called_once_with("10.0.0.1", True)


def test_handle_extracted_success_callback_takes_priority_over_db_reader():
    win = _make_win()
    callback = MagicMock()
    win.on_extracted = callback
    win.db_reader = MagicMock()
    win._handle_extracted_success()
    callback.assert_called_once_with("10.0.0.1")
    win.db_reader.upsert_extracted_flag.assert_not_called()


# ---------------------------------------------------------------------------
# _prompt_extract_options
# ---------------------------------------------------------------------------

def test_prompt_extract_options_cancel_returns_none():
    win = _make_win()
    with patch("gui.components.file_browser_download_mixin.BatchExtractSettingsDialog") as MockDialog:
        MockDialog.return_value.show.return_value = None
        result = win._prompt_extract_options(3)
    assert result is None


def test_prompt_extract_options_persists_legacy_limits_and_extensions():
    win = _make_win()
    win._persist_folder_limit_defaults = MagicMock()
    dialog_config = {
        "max_directory_depth": "2",
        "max_files_per_target": "100",
        "max_total_size_mb": "500",
        "max_file_size_mb": "10",
        "extension_mode": "allow_only",
        "included_extensions": [".txt", ".log"],
        "excluded_extensions": [],
    }
    with patch("gui.components.file_browser_download_mixin.BatchExtractSettingsDialog") as MockDialog:
        MockDialog.return_value.show.return_value = dialog_config
        result = win._prompt_extract_options(5)

    assert result is not None
    assert result["max_depth"] == 2
    assert result["max_files"] == 100
    assert result["max_total_mb"] == 500
    assert result["extension_mode"] == "allow_only"
    assert ".txt" in result["included_extensions"]
    win._persist_folder_limit_defaults.assert_called_once()


def test_prompt_extract_options_config_path_override_from_settings_manager():
    """settings_manager.get_setting('backend.config_path') overrides self.config_path."""
    win = _make_win()
    win.config_path = "/old/config.json"
    win._persist_folder_limit_defaults = MagicMock()
    mock_sm = MagicMock()
    mock_sm.get_setting.return_value = "/new/config.json"
    win.settings_manager = mock_sm
    dialog_config = {
        "max_directory_depth": 0, "max_files_per_target": 0,
        "max_total_size_mb": 0, "max_file_size_mb": 0,
        "extension_mode": "download_all",
        "included_extensions": [], "excluded_extensions": [],
    }
    with patch("gui.components.file_browser_download_mixin.BatchExtractSettingsDialog") as MockDialog:
        MockDialog.return_value.show.return_value = dialog_config
        win._prompt_extract_options(1)
        _, kwargs = MockDialog.call_args
    assert kwargs.get("config_path") == "/new/config.json"


# ---------------------------------------------------------------------------
# _start_list_thread
# ---------------------------------------------------------------------------

def test_start_list_thread_sets_busy_and_starts_daemon_thread():
    win = _make_win()
    with patch("gui.components.file_browser_download_mixin.threading.Thread") as MockThread:
        mock_t = MagicMock()
        MockThread.return_value = mock_t
        win._start_list_thread("\\some\\path")

    win._set_busy.assert_called_once_with(True)
    _, kwargs = MockThread.call_args
    assert kwargs.get("daemon") is True
    mock_t.start.assert_called_once()


# ---------------------------------------------------------------------------
# _start_download_thread
# ---------------------------------------------------------------------------

def test_start_download_thread_spawns_daemon_thread():
    win = _make_win()
    with patch("gui.components.file_browser_download_mixin.threading.Thread") as MockThread:
        mock_t = MagicMock()
        MockThread.return_value = mock_t
        win._start_download_thread([], [], None)

    _, kwargs = MockThread.call_args
    assert kwargs.get("daemon") is True
    mock_t.start.assert_called_once()


# ---------------------------------------------------------------------------
# _on_cancel
# ---------------------------------------------------------------------------

def test_on_cancel_sets_cancel_event_updates_status_and_button_state():
    win = _make_win()
    mock_event = MagicMock()
    win.download_cancel_event = mock_event

    win._on_cancel()

    win.navigator.cancel.assert_called_once()
    mock_event.set.assert_called_once()
    win._set_status.assert_called_once_with("Cancellation requested…")
    win.btn_cancel.configure.assert_called_once_with(state=tk.DISABLED)


def test_on_cancel_no_cancel_event_skips_set():
    """When download_cancel_event is None the guard must not raise."""
    win = _make_win()
    win.download_cancel_event = None

    win._on_cancel()  # must not raise AttributeError

    win.navigator.cancel.assert_called_once()
    win._set_status.assert_called_once_with("Cancellation requested…")
    win.btn_cancel.configure.assert_called_once_with(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# _expand_directories
# ---------------------------------------------------------------------------

def _make_file_entry(name: str, size: int = 100, mtime: float = 1.0):
    entry = MagicMock()
    entry.name = name
    entry.is_dir = False
    entry.size = size
    entry.modified_time = mtime
    return entry


def _make_dir_entry(name: str):
    entry = MagicMock()
    entry.name = name
    entry.is_dir = True
    entry.size = 0
    entry.modified_time = None
    return entry


def test_expand_directories_applies_extension_filter_and_max_files():
    win = _make_win()
    mock_result = MagicMock()
    mock_result.entries = [
        _make_file_entry("a.txt"),
        _make_file_entry("b.txt"),
        _make_file_entry("c.exe"),
    ]
    win.navigator.list_dir.return_value = mock_result

    limits = {
        "max_depth": 0, "max_files": 1, "max_total_mb": 0, "max_file_mb": 0,
        "extension_mode": "deny_only",
        "included_extensions": [],
        "excluded_extensions": [".exe"],
    }
    expanded, skipped, errors = win._expand_directories(["\\share"], limits)

    # .exe excluded by deny_only; max_files=1 stops after the first allowed file
    assert len(expanded) == 1
    assert expanded[0][0].endswith("a.txt")
    assert not errors


def test_expand_directories_collects_list_errors():
    win = _make_win()
    win.navigator.list_dir.side_effect = Exception("Access denied")

    limits = {
        "max_depth": 0, "max_files": 0, "max_total_mb": 0, "max_file_mb": 0,
        "extension_mode": "download_all", "included_extensions": [], "excluded_extensions": [],
    }
    expanded, skipped, errors = win._expand_directories(["\\baddir"], limits)

    assert expanded == []
    assert len(errors) == 1
    assert "Access denied" in errors[0][1]


# ---------------------------------------------------------------------------
# Cross-boundary MRO (test 16)
# ---------------------------------------------------------------------------

def test_on_download_directory_selection_calls_prompt_then_start_thread():
    """
    Cross-boundary MRO test: _on_download (stays in FileBrowserWindow) calls
    _prompt_extract_options and _start_download_thread that were moved to the mixin.
    """
    # Verify MRO resolution is intact
    assert FileBrowserWindow._prompt_extract_options is _FileBrowserDownloadMixin._prompt_extract_options
    assert FileBrowserWindow._start_download_thread is _FileBrowserDownloadMixin._start_download_thread

    win = FileBrowserWindow.__new__(FileBrowserWindow)
    win.busy = False
    win.current_share = "share$"
    win.current_path = "\\"
    win.max_batch_files = 50
    win.config = {"max_download_size_mb": 0}
    win.window = MagicMock()

    mock_tree = MagicMock()
    mock_tree.selection.return_value = ["item1"]
    mock_tree.item.return_value = {"values": ["mydir", "dir", "", "", "", ""]}
    win.tree = mock_tree

    mock_prompt = MagicMock(return_value={
        "max_depth": 0, "max_files": 0, "max_total_mb": 0, "max_file_mb": 0,
        "extension_mode": "download_all", "included_extensions": [], "excluded_extensions": [],
    })
    mock_start = MagicMock()
    win._prompt_extract_options = mock_prompt
    win._start_download_thread = mock_start

    win._on_download()

    mock_prompt.assert_called_once_with(1)
    mock_start.assert_called_once()
