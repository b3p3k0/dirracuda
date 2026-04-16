"""
Browser window base class and protocol-specific browser windows for FTP, HTTP, and SMB.

UnifiedBrowserCore provides the common UI/controller machinery.  Protocol-specific
behaviour is supplied via four adapter hooks that subclasses must implement:

    _adapt_window_title()         -> str
    _adapt_banner_label()         -> str
    _adapt_banner_placeholder()   -> str
    _adapt_setup_treeview(tree_frame) -> None  (creates self.tree + scrollbar)

FtpBrowserWindow, HttpBrowserWindow, and SmbBrowserWindow are all defined in this
module.  FTP/HTTP are accessed via open_ftp_http_browser(); SMB via open_smb_browser().

Methods kept per-protocol (NOT in UnifiedBrowserCore):

  _on_cancel, _on_close
      FTP disconnects the session (navigator.disconnect()); HTTP/SMB cancel only.

  _list_thread_fn
      FTP lazy-connect/cancel ordering is fragile; kept verbatim per protocol.

  _run_probe_background, _apply_probe_snapshot
      Different probe functions and error payload shapes per protocol.

  _populate_treeview, _on_item_double_click, _on_up, _on_view,
  _on_download, _download_thread_fn
      Column structure or path-resolution semantics differ per protocol.

Heavy protocol imports (shared.ftp_browser, shared.http_browser, shared.smb_browser)
are kept as per-method lazy imports — all three pull impacket unconditionally, so
module-level placement would load impacket at import time.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from gui.utils import safe_messagebox as messagebox
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:
    from utils.dialog_helpers import ensure_dialog_focus  # type: ignore[no-redef]

from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size
# UnifiedBrowserCore is extracted to gui.browsers.core (Card C3).
# Re-exported here to preserve the legacy name in this module's namespace
# and maintain backward compatibility for all callers and monkeypatch targets.
from gui.browsers.core import UnifiedBrowserCore


def open_file_viewer(*args: Any, **kwargs: Any) -> Any:
    """Lazy-load file viewer to avoid import-time coupling in browser/probe paths."""
    try:
        from gui.components.file_viewer_window import open_file_viewer as _open_file_viewer
    except ImportError:
        from file_viewer_window import open_file_viewer as _open_file_viewer  # type: ignore[no-redef]
    return _open_file_viewer(*args, **kwargs)


def open_image_viewer(*args: Any, **kwargs: Any) -> Any:
    """Lazy-load image viewer to avoid import-time failures when Pillow/ImageTk is unavailable."""
    try:
        from gui.components.image_viewer_window import open_image_viewer as _open_image_viewer
    except ImportError:
        from image_viewer_window import open_image_viewer as _open_image_viewer  # type: ignore[no-redef]
    return _open_image_viewer(*args, **kwargs)


# ---------------------------------------------------------------------------
# FtpBrowserWindow — extracted to gui.browsers.ftp_browser (Card C4)
# ---------------------------------------------------------------------------
from gui.browsers.ftp_browser import FtpBrowserWindow, _load_ftp_browser_config


# ---------------------------------------------------------------------------
# HttpBrowserWindow — extracted to gui.browsers.http_browser (Card C4)
# ---------------------------------------------------------------------------
from gui.browsers.http_browser import HttpBrowserWindow, _load_http_browser_config

# ---------------------------------------------------------------------------
# SmbBrowserWindow — extracted to gui.browsers.smb_browser (Card C5)
# ---------------------------------------------------------------------------
from gui.browsers.smb_browser import (
    SmbBrowserWindow,
    _load_smb_browser_config,
    _extract_smb_banner,
)

# ---------------------------------------------------------------------------
# Factory functions — extracted to gui.browsers.factory (Card C5)
# ---------------------------------------------------------------------------
from gui.browsers.factory import (
    open_ftp_http_browser,
    open_smb_browser,
    _normalize_share_name,
)
