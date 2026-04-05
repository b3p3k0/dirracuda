"""
Unit tests for HTTP browser viewer integration (no Tk display required).

Mirrors FTP browser test structure; factory function and mock shapes are
the same but assertions use HTTP-specific URL format and 5-column treeview layout.
"""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.unified_browser_window import HttpBrowserWindow


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


def _make_window() -> HttpBrowserWindow:
    """Create a minimal HttpBrowserWindow instance without building Tk widgets."""
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.port = 80
    win.scheme = "http"
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

    assert captured["file_path"] == "http://10.20.30.40:80/pub/readme.txt"
    assert captured["content"] == b"hello"
    assert captured["file_size"] == 123
    assert callable(captured["on_save_callback"])

    captured["on_save_callback"]()
    win._start_download_thread.assert_called_once_with([("/pub/readme.txt", 0)])


def test_open_image_viewer_uses_shared_image_viewer_and_save_callback_downloads():
    win = _make_window()
    captured = {}

    def _fake_open_image_viewer(**kwargs):
        captured.update(kwargs)

    with patch("gui.components.unified_browser_window.open_image_viewer", side_effect=_fake_open_image_viewer):
        win._open_image_viewer("/media/logo.png", b"\x89PNG", 456, True, 2_000_000)

    assert captured["file_path"] == "http://10.20.30.40:80/media/logo.png"
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
    win.config = {"viewer": {"max_view_size_mb": 5, "max_image_size_mb": 15, "max_image_pixels": 123456}}
    win._start_view_thread = MagicMock()
    win.tree = MagicMock()
    win.tree.selection.return_value = ["item-1"]
    win._path_map = {"item-1": "/pub/photo.jpg"}
    win.tree.item.return_value = ("photo.jpg", "file", "\u2014", "\u2014", "/pub/photo.jpg")

    win._on_view()

    win._start_view_thread.assert_called_once()
    kw = win._start_view_thread.call_args.kwargs
    assert kw["remote_path"] == "/pub/photo.jpg"
    assert kw["display_name"] == "photo.jpg"
    assert kw["max_bytes"] == 15 * 1024 * 1024
    assert kw["is_image"] is True
    assert kw["max_image_pixels"] == 123456


def test_on_view_uses_text_limits_and_dispatches_view_thread():
    win = _make_window()
    win._navigator = object()
    win.config = {"viewer": {"max_view_size_mb": 5, "max_image_size_mb": 15, "max_image_pixels": 20_000_000}}
    win._start_view_thread = MagicMock()
    win.tree = MagicMock()
    win.tree.selection.return_value = ["item-2"]
    win._path_map = {"item-2": "/pub/readme.txt"}
    win.tree.item.return_value = ("readme.txt", "file", "\u2014", "\u2014", "/pub/readme.txt")

    win._on_view()

    win._start_view_thread.assert_called_once()
    kw = win._start_view_thread.call_args.kwargs
    assert kw["remote_path"] == "/pub/readme.txt"
    assert kw["display_name"] == "readme.txt"
    assert kw["max_bytes"] == 5 * 1024 * 1024
    assert kw["is_image"] is False
    # max_image_pixels passed through but ignored when is_image=False
    assert kw["max_image_pixels"] == 20_000_000


def test_populate_treeview_sorts_dirs_then_files_alphabetically():
    win = _make_window()
    win.tree = _CaptureTree()
    win._path_map = {}
    list_result = SimpleNamespace(
        entries=[
            SimpleNamespace(name="/zeta.txt", is_dir=False, size=0, modified_time=None),
            SimpleNamespace(name="/Beta/", is_dir=True, size=0, modified_time=None),
            SimpleNamespace(name="/alpha.txt", is_dir=False, size=0, modified_time=None),
            SimpleNamespace(name="/aardvark/", is_dir=True, size=0, modified_time=None),
        ]
    )

    win._populate_treeview(list_result)

    assert [row[0] for row in win.tree.rows] == ["aardvark", "Beta", "alpha.txt", "zeta.txt"]
    assert [row[1] for row in win.tree.rows] == ["dir", "dir", "file", "file"]


# ---------------------------------------------------------------------------
# Tuning hook and persistence tests
# ---------------------------------------------------------------------------

def test_adapt_large_file_tuning_enabled_is_false_for_http():
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    assert win._adapt_large_file_tuning_enabled() is False


def _make_http_with_settings(worker_count=None, large_mb=None):
    """Construct HttpBrowserWindow with a mock settings_manager, no Tk/threads."""
    sm = MagicMock()
    defaults = {}
    if worker_count is not None:
        defaults["file_browser.download_worker_count"] = worker_count
    if large_mb is not None:
        defaults["file_browser.download_large_file_mb"] = large_mb
    sm.get_setting.side_effect = lambda k, d: defaults.get(k, d)
    with patch.multiple(
        "gui.components.unified_browser_window.HttpBrowserWindow",
        _build_window=MagicMock(),
        _navigate_to=MagicMock(),
        _run_probe_background=MagicMock(),
        _apply_probe_snapshot=MagicMock(),
    ), patch("shared.http_browser.HttpNavigator"), \
       patch("gui.components.unified_browser_window.threading.Thread", _NoopThread), \
       patch("gui.utils.probe_cache_dispatch.load_probe_result_for_host", return_value=None):
        win = HttpBrowserWindow(
            parent=MagicMock(), ip_address="1.2.3.4", settings_manager=sm
        )
    return win


def test_init_loads_worker_count_from_settings_manager():
    win = _make_http_with_settings(worker_count=3)
    assert win.download_workers == 3


def test_init_clamps_worker_count_to_max_3():
    win = _make_http_with_settings(worker_count=99)
    assert win.download_workers == 3


def test_init_clamps_worker_count_to_min_1():
    win = _make_http_with_settings(worker_count=0)
    assert win.download_workers == 1


def test_init_loads_large_file_mb_from_settings_manager():
    win = _make_http_with_settings(large_mb=50)
    assert win.download_large_mb == 50


def test_init_clamps_large_file_mb_to_min_1():
    win = _make_http_with_settings(large_mb=0)
    assert win.download_large_mb == 1


def test_persist_tuning_writes_correct_settings_keys():
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
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


def test_build_window_renders_large_spinbox_disabled_with_note():
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    win.download_workers = 2
    win.download_large_mb = 25
    win.settings_manager = None
    win.theme = None
    win.parent = MagicMock()
    win._server_banner = ""
    win._cancel_event = threading.Event()
    win.ip_address = "1.2.3.4"
    win.port = 80
    win.scheme = "http"

    captured_spinboxes = []
    captured_label_texts = []

    def _fake_spinbox(*_args, **kwargs):
        captured_spinboxes.append(kwargs)
        return MagicMock()

    def _fake_label(*_args, **kwargs):
        captured_label_texts.append(kwargs.get("text", ""))
        return MagicMock()

    with patch("gui.components.unified_browser_window.tk.Toplevel", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Frame", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Text", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Button", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.StringVar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.IntVar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.ttk.Scrollbar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.ttk.Treeview", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Spinbox", side_effect=_fake_spinbox), \
         patch("gui.components.unified_browser_window.tk.Label", side_effect=_fake_label):
        win._build_window()

    # Large spinbox: from_=1, to=1024 — must be DISABLED
    large_spins = [c for c in captured_spinboxes if c.get("to") == 1024]
    assert len(large_spins) == 1
    assert large_spins[0].get("state") == "disabled"

    # Explanatory note label must be present
    assert any("not active" in t for t in captured_label_texts)
