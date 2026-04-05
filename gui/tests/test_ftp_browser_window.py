"""
Unit tests for FTP browser viewer integration (no Tk display required).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.unified_browser_window import FtpBrowserWindow


class _IntVar:
    """Minimal IntVar stub — .get() returns a fixed int."""
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _NoopThread:
    """Prevents real thread creation in init-load tests."""
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class _CaptureTree:
    def __init__(self):
        self.rows = []
        self._iid = 0

    def get_children(self):
        return tuple(range(len(self.rows)))

    def delete(self, *_items):
        self.rows.clear()

    def insert(self, _parent, _index, values):
        self._iid += 1
        self.rows.append(values)
        return f"item-{self._iid}"


def _make_window() -> FtpBrowserWindow:
    """Create a minimal FtpBrowserWindow instance without building Tk widgets."""
    win = FtpBrowserWindow.__new__(FtpBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.window = MagicMock()
    win.theme = None
    win._set_status = MagicMock()
    win._start_download_thread = MagicMock()
    return win


def test_open_viewer_uses_shared_file_viewer_and_save_callback_downloads():
    win = _make_window()
    captured = {}

    def _fake_open_file_viewer(**kwargs):
        captured.update(kwargs)

    with patch("gui.components.unified_browser_window.open_file_viewer", side_effect=_fake_open_file_viewer):
        win._open_viewer("/pub/readme.txt", b"hello", 123)

    assert captured["file_path"] == "10.20.30.40/ftp_root/pub/readme.txt"
    assert captured["content"] == b"hello"
    assert captured["file_size"] == 123
    assert callable(captured["on_save_callback"])

    captured["on_save_callback"]()
    win._start_download_thread.assert_called_once_with([("/pub/readme.txt", 123)])


def test_open_image_viewer_uses_shared_image_viewer_and_save_callback_downloads():
    win = _make_window()
    captured = {}

    def _fake_open_image_viewer(**kwargs):
        captured.update(kwargs)

    with patch("gui.components.unified_browser_window.open_image_viewer", side_effect=_fake_open_image_viewer):
        win._open_image_viewer("/media/logo.png", b"\x89PNG", 456, True, 2_000_000)

    assert captured["file_path"] == "10.20.30.40/ftp_root/media/logo.png"
    assert captured["content"] == b"\x89PNG"
    assert captured["max_pixels"] == 2_000_000
    assert captured["truncated"] is True
    assert callable(captured["on_save_callback"])

    captured["on_save_callback"]()
    win._start_download_thread.assert_called_once_with([("/media/logo.png", 456)])


def test_open_image_viewer_shows_error_when_viewer_raises():
    win = _make_window()

    with patch("gui.components.unified_browser_window.open_image_viewer", side_effect=RuntimeError("bad image")), patch(
        "gui.components.unified_browser_window.messagebox.showerror"
    ) as mock_showerror:
        win._open_image_viewer("/media/corrupt.png", b"bad", 99, False, 1_000)

    win._set_status.assert_called_with("View failed: bad image")
    mock_showerror.assert_called_once()
    args, kwargs = mock_showerror.call_args
    assert args[0] == "View Error"
    assert args[1] == "bad image"
    assert kwargs["parent"] is win.window


def test_on_view_uses_image_limits_and_dispatches_view_thread():
    win = _make_window()
    win._navigator = object()
    win._current_path = "/pub"
    win.config = {"viewer": {"max_view_size_mb": 5, "max_image_size_mb": 15, "max_image_pixels": 123456}}
    win._start_view_thread = MagicMock()
    win.tree = MagicMock()
    win.tree.selection.return_value = ["item-1"]
    win.tree.item.return_value = ("photo.jpg", "file", "1.2 KB", "", "", "1024")

    win._on_view()

    win._start_view_thread.assert_called_once_with(
        remote_path="/pub/photo.jpg",
        display_name="photo.jpg",
        max_bytes=15 * 1024 * 1024,
        is_image=True,
        max_image_pixels=123456,
        size_raw=1024,
    )


def test_populate_treeview_sorts_dirs_then_files_alphabetically():
    win = _make_window()
    win.tree = _CaptureTree()
    list_result = SimpleNamespace(
        entries=[
            SimpleNamespace(name="zeta.txt", is_dir=False, size=1, modified_time=None),
            SimpleNamespace(name="beta", is_dir=True, size=0, modified_time=None),
            SimpleNamespace(name="Alpha.txt", is_dir=False, size=2, modified_time=None),
            SimpleNamespace(name="aardvark", is_dir=True, size=0, modified_time=None),
        ]
    )

    win._populate_treeview(list_result)

    assert [row[0] for row in win.tree.rows] == ["aardvark", "beta", "Alpha.txt", "zeta.txt"]
    assert [row[1] for row in win.tree.rows] == ["dir", "dir", "file", "file"]


# ---------------------------------------------------------------------------
# Tuning hook and persistence tests
# ---------------------------------------------------------------------------

def test_adapt_large_file_tuning_enabled_is_true_for_ftp():
    win = FtpBrowserWindow.__new__(FtpBrowserWindow)
    assert win._adapt_large_file_tuning_enabled() is True


def _make_ftp_with_settings(worker_count=None, large_mb=None):
    """Construct FtpBrowserWindow with a mock settings_manager, no Tk/threads."""
    sm = MagicMock()
    defaults = {}
    if worker_count is not None:
        defaults["file_browser.download_worker_count"] = worker_count
    if large_mb is not None:
        defaults["file_browser.download_large_file_mb"] = large_mb
    sm.get_setting.side_effect = lambda k, d: defaults.get(k, d)
    with patch.multiple(
        "gui.components.unified_browser_window.FtpBrowserWindow",
        _build_window=MagicMock(),
        _navigate_to=MagicMock(),
        _run_probe_background=MagicMock(),
        _apply_probe_snapshot=MagicMock(),
    ), patch("gui.components.unified_browser_window.threading.Thread", _NoopThread), \
       patch("gui.utils.probe_cache_dispatch.load_probe_result_for_host", return_value=None):
        win = FtpBrowserWindow(parent=MagicMock(), ip_address="1.2.3.4", settings_manager=sm)
    return win


def test_init_loads_worker_count_from_settings_manager():
    win = _make_ftp_with_settings(worker_count=3)
    assert win.download_workers == 3


def test_init_clamps_worker_count_to_max_3():
    win = _make_ftp_with_settings(worker_count=99)
    assert win.download_workers == 3


def test_init_clamps_worker_count_to_min_1():
    win = _make_ftp_with_settings(worker_count=0)
    assert win.download_workers == 1


def test_init_loads_large_file_mb_from_settings_manager():
    win = _make_ftp_with_settings(large_mb=50)
    assert win.download_large_mb == 50


def test_init_clamps_large_file_mb_to_min_1():
    win = _make_ftp_with_settings(large_mb=0)
    assert win.download_large_mb == 1


def test_persist_tuning_writes_correct_settings_keys():
    win = FtpBrowserWindow.__new__(FtpBrowserWindow)
    win.workers_var = _IntVar(2)
    win.large_mb_var = _IntVar(30)
    win.download_workers = 2
    win.download_large_mb = 30
    sm = MagicMock()
    win.settings_manager = sm

    win._persist_tuning()

    calls = {c.args[0]: c.args[1] for c in sm.set_setting.call_args_list}
    assert calls["file_browser.download_worker_count"] == 2
    assert calls["file_browser.download_large_file_mb"] == 30
