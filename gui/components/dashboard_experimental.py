"""
Experimental feature helpers for DashboardWidget (C1 extraction).

Each function takes the dashboard widget instance as first arg.

Intra-class call discipline: calls to other DashboardWidget methods go through
widget.method_name() so instance-level monkeypatches in tests still intercept.

Patch path for show_reddit_browser_window in tests:
  gui.components.dashboard_experimental.show_reddit_browser_window
"""

from gui.components.reddit_browser_window import show_reddit_browser_window
from gui.components.se_dork_browser_window import show_se_dork_browser_window
from gui.components.dorkbook_window import show_dorkbook_window
from gui.components.keymaster_window import show_keymaster_window
from gui.utils.sidecar_promotion import promote_sidecar_prefill
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
        "reddit_grab_status_getter": lambda: bool(
            getattr(widget, "_reddit_grab_running", False)
        ),
        "open_reddit_post_db": widget._open_reddit_post_db,
        "open_se_dork_results_db": lambda: open_se_dork_results_db(widget),
        "open_dorkbook": lambda: open_dorkbook(widget),
        "open_keymaster": lambda: open_keymaster(widget),
        "parent": widget.parent,
    }
    show_experimental_features_dialog(widget.parent, context, widget.settings_manager)


def open_reddit_post_db(widget) -> None:
    """Open the Reddit Post DB browser with direct main-DB promotion."""
    show_reddit_browser_window(
        parent=widget.parent,
        add_record_callback=None,
        promote_record_callback=_make_sidecar_promote_callback(widget),
        settings_manager=getattr(widget, "settings_manager", None),
    )


def open_se_dork_results_db(widget) -> None:
    """Open the SE Dork results browser with direct main-DB promotion."""
    show_se_dork_browser_window(
        parent=widget.parent,
        add_record_callback=None,
        promote_record_callback=_make_sidecar_promote_callback(widget),
        settings_manager=getattr(widget, "settings_manager", None),
    )


def open_dorkbook(widget) -> None:
    """Open singleton Dorkbook window."""
    show_dorkbook_window(
        parent=widget.parent,
        settings_manager=getattr(widget, "settings_manager", None),
    )


def open_keymaster(widget) -> None:
    """Open singleton Keymaster window.

    Patch path for tests:
      gui.components.dashboard_experimental.show_keymaster_window
    """
    config_path = None
    if hasattr(widget, "_resolve_active_config_path"):
        try:
            resolved = widget._resolve_active_config_path()
            config_path = str(resolved) if resolved is not None else None
        except Exception:
            pass
    if config_path is None:
        config_path = getattr(widget, "config_path", None)

    show_keymaster_window(
        parent=widget.parent,
        settings_manager=getattr(widget, "settings_manager", None),
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_sidecar_promote_callback(widget):
    """Return a direct sidecar promotion callback, or None when DB is absent."""
    db_reader = getattr(widget, "db_reader", None)
    if db_reader is None:
        return None

    def _promote(prefill):
        promotion = promote_sidecar_prefill(db_reader, prefill)
        _notify_dashboard_database_changed(widget)
        return promotion

    return _promote


def _notify_dashboard_database_changed(widget) -> None:
    """Refresh dashboard DB summary after a successful sidecar promotion."""
    refresher = getattr(widget, "refresh_after_database_change", None)
    if not callable(refresher):
        return
    try:
        refresher(refresh_runtime_status=False)
    except Exception as exc:
        _logger.warning("Dashboard refresh after sidecar promotion failed: %s", exc)

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
