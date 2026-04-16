"""
gui/browsers — Browser package.

Phase C2: package skeleton.
Phase C3: UnifiedBrowserCore extracted to gui.browsers.core.
Phase C4: FtpBrowserWindow extracted to gui.browsers.ftp_browser;
          HttpBrowserWindow extracted to gui.browsers.http_browser.
Phase C5: SmbBrowserWindow extracted to gui.browsers.smb_browser;
          factory functions extracted to gui.browsers.factory.

After C5, SMB and factory symbols are imported directly (no more lazy-load
circular for those paths). open_file_viewer and open_image_viewer still live
in gui.components.unified_browser_window and remain lazy-loaded via __getattr__
to avoid the circular that arises when UBW imports gui.browsers.* during
__init__.py initialisation.

INVARIANT: do not add module-scope imports from gui.components.unified_browser_window
here — it would re-introduce the circular import.
"""
from gui.browsers.core import UnifiedBrowserCore
from gui.browsers.ftp_browser import FtpBrowserWindow
from gui.browsers.http_browser import HttpBrowserWindow
from gui.browsers.smb_browser import SmbBrowserWindow, _extract_smb_banner
from gui.browsers.factory import open_ftp_http_browser, open_smb_browser
from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size

# open_file_viewer and open_image_viewer are defined in unified_browser_window
# (as lazy-load wrappers and monkeypatch targets). They cannot be imported at
# module scope here without re-introducing the circular, so they remain lazy.
_LAZY_SYMBOLS = frozenset({"open_file_viewer", "open_image_viewer"})


def __getattr__(name: str):
    if name in _LAZY_SYMBOLS:
        from gui.components.unified_browser_window import (
            open_file_viewer,
            open_image_viewer,
        )
        _loaded = {
            "open_file_viewer": open_file_viewer,
            "open_image_viewer": open_image_viewer,
        }
        globals().update(_loaded)
        return _loaded[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "UnifiedBrowserCore",
    "FtpBrowserWindow",
    "HttpBrowserWindow",
    "SmbBrowserWindow",
    "open_ftp_http_browser",
    "open_smb_browser",
    "open_file_viewer",
    "open_image_viewer",
    "_extract_smb_banner",
    "_coerce_bool",
    "_format_file_size",
]
