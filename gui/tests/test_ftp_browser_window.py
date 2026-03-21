"""
Unit tests for FTP browser viewer integration (no Tk display required).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.ftp_browser_window import FtpBrowserWindow


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

    with patch("gui.components.ftp_browser_window.open_file_viewer", side_effect=_fake_open_file_viewer):
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

    with patch("gui.components.ftp_browser_window.open_image_viewer", side_effect=_fake_open_image_viewer):
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

    with patch("gui.components.ftp_browser_window.open_image_viewer", side_effect=RuntimeError("bad image")), patch(
        "gui.components.ftp_browser_window.messagebox.showerror"
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
