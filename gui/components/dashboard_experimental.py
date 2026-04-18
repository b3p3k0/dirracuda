"""
Experimental feature helpers for DashboardWidget (C1 extraction).

Each function takes the dashboard widget instance as first arg. No UI text
or behavior changes beyond what is explicitly described here.

Intra-class call discipline: calls to other DashboardWidget methods go through
widget.method_name() so instance-level monkeypatches in tests still intercept.

Patch path for show_reddit_browser_window in tests:
  gui.components.dashboard_experimental.show_reddit_browser_window
"""

from gui.components.reddit_browser_window import show_reddit_browser_window
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


def set_server_list_getter(widget, getter) -> None:
    """Store a callable that returns the current ServerListWindow or None."""
    widget._server_list_getter = getter


def handle_experimental_button_click(widget) -> None:
    """Open the Experimental Features dialog from the dashboard."""
    from gui.components.experimental_features_dialog import show_experimental_features_dialog

    context = {
        "reddit_grab_callback": widget._handle_reddit_grab_button_click,
        "open_reddit_post_db": widget._open_reddit_post_db,
        "parent": widget.parent,
    }
    show_experimental_features_dialog(widget.parent, context, widget.settings_manager)


def open_reddit_post_db(widget) -> None:
    """Open the Reddit Post DB browser, wiring add_record_callback when possible.

    Resolution order (single pass; no side-effect window opens):
    1. Call _server_list_getter() if set; accept result if window is live.
    2. If None/dead/error: open browser without add_record_callback (fallback).
       The browser's own "Not available" message handles the UX.

    Parent window:
    - Live server_window: parent=server_window.window  (matches prior behavior)
    - Fallback:           parent=widget.parent
    """
    server_window = _resolve_server_window(widget)

    if server_window is not None:
        show_reddit_browser_window(
            parent=server_window.window,
            add_record_callback=server_window.open_add_record_dialog,
        )
    else:
        show_reddit_browser_window(
            parent=widget.parent,
            add_record_callback=None,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_server_window(widget):
    """Return a live ServerListWindow instance or None (single getter pass)."""
    getter = getattr(widget, "_server_list_getter", None)

    server_window = _safe_get_server_window(getter)
    if server_window is not None and not _window_is_live(server_window):
        server_window = None

    return server_window


def _safe_get_server_window(getter):
    """Call server-list getter safely; return None on failure."""
    if getter is None:
        return None
    try:
        return getter()
    except Exception as exc:
        _logger.warning(
            "Experimental Reddit Post DB fallback: server list getter failed: %s",
            exc,
        )
        return None


def _window_is_live(server_window) -> bool:
    """Return True if server_window's underlying Tk widget is still valid."""
    try:
        return bool(server_window.window.winfo_exists())
    except Exception:
        return False
