"""
C2 import-contract coverage: gui/browsers package scaffold.

Ensures that:
1. All frozen public symbols still import from the legacy canonical path
   (gui.components.unified_browser_window).
2. ubw module-attribute monkeypatch contracts (threading, messagebox, queue,
   tk, ttk) remain intact on unified_browser_window.
3. gui.browsers package is importable as the C2 scaffold.
4. gui.browsers re-exports all frozen symbols as the same objects (not copies).
"""
import gui.browsers
import gui.components.unified_browser_window as ubw
from gui.components.unified_browser_window import (
    open_ftp_http_browser,
    open_smb_browser,
    open_file_viewer,
    open_image_viewer,
    UnifiedBrowserCore,
    FtpBrowserWindow,
    HttpBrowserWindow,
    SmbBrowserWindow,
    _extract_smb_banner,
    _coerce_bool,
    _format_file_size,
)


def test_legacy_ubw_functions_resolve():
    assert callable(open_ftp_http_browser)
    assert callable(open_smb_browser)
    assert callable(open_file_viewer)
    assert callable(open_image_viewer)


def test_legacy_ubw_classes_resolve():
    assert UnifiedBrowserCore is not None
    assert FtpBrowserWindow is not None
    assert HttpBrowserWindow is not None
    assert SmbBrowserWindow is not None


def test_legacy_ubw_private_helpers_resolve():
    assert callable(_extract_smb_banner)
    assert callable(_coerce_bool)
    assert callable(_format_file_size)


def test_ubw_module_attribute_contracts():
    """Module-level names required by monkeypatch contracts §2c/§2d are present."""
    assert hasattr(ubw, "threading")
    assert hasattr(ubw, "messagebox")
    assert hasattr(ubw, "queue")
    assert hasattr(ubw, "tk")
    assert hasattr(ubw, "ttk")


def test_gui_browsers_package_importable():
    import gui.browsers  # noqa: F401


def test_gui_browsers_re_exports_public_symbols():
    assert hasattr(gui.browsers, "open_ftp_http_browser")
    assert hasattr(gui.browsers, "open_smb_browser")
    assert hasattr(gui.browsers, "open_file_viewer")
    assert hasattr(gui.browsers, "open_image_viewer")
    assert hasattr(gui.browsers, "UnifiedBrowserCore")
    assert hasattr(gui.browsers, "FtpBrowserWindow")
    assert hasattr(gui.browsers, "HttpBrowserWindow")
    assert hasattr(gui.browsers, "SmbBrowserWindow")
    assert hasattr(gui.browsers, "_extract_smb_banner")
    assert hasattr(gui.browsers, "_coerce_bool")
    assert hasattr(gui.browsers, "_format_file_size")


def test_gui_browsers_symbols_are_same_objects():
    """Symbols in gui.browsers are the same objects as in ubw (no copies)."""
    assert gui.browsers.open_ftp_http_browser is ubw.open_ftp_http_browser
    assert gui.browsers.open_smb_browser is ubw.open_smb_browser
    assert gui.browsers.open_file_viewer is ubw.open_file_viewer
    assert gui.browsers.open_image_viewer is ubw.open_image_viewer
    assert gui.browsers.UnifiedBrowserCore is ubw.UnifiedBrowserCore
    assert gui.browsers.FtpBrowserWindow is ubw.FtpBrowserWindow
    assert gui.browsers.HttpBrowserWindow is ubw.HttpBrowserWindow
    assert gui.browsers.SmbBrowserWindow is ubw.SmbBrowserWindow
    assert gui.browsers._extract_smb_banner is ubw._extract_smb_banner
