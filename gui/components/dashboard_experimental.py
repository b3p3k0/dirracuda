"""
Experimental feature helpers for DashboardWidget (C1 extraction).

Each function takes the dashboard widget instance as first arg.

Intra-class call discipline: calls to other DashboardWidget methods go through
widget.method_name() so instance-level monkeypatches in tests still intercept.

Patch path for show_reddit_browser_window in tests:
  gui.components.dashboard_experimental.show_reddit_browser_window
"""

import sys
import threading
import tkinter as tk
from pathlib import Path

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.db_unification import execute_sidecar_migration_now
from gui.utils.style import apply_theme_to_window
from gui.components.reddit_browser_window import show_reddit_browser_window
from gui.components.se_dork_browser_window import show_se_dork_browser_window
from gui.components.dorkbook_window import show_dorkbook_window
from gui.components.keymaster_window import show_keymaster_window
from gui.utils.sidecar_promotion import (
    promote_sidecar_prefill,
    promote_sidecar_prefills,
)
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


def _mb():
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def set_server_list_getter(widget, getter) -> None:
    """Store a callable that returns the current ServerListWindow or None."""
    widget._server_list_getter = getter


def _resolve_main_db_path(widget) -> Path:
    """Resolve current primary DB path from dashboard state with runtime fallback."""
    db_reader = getattr(widget, "db_reader", None)
    reader_path = getattr(db_reader, "db_path", None)
    if reader_path:
        try:
            return Path(reader_path).expanduser().resolve(strict=False)
        except Exception:
            pass

    try:
        from shared.path_service import (
            get_legacy_paths,
            get_paths,
            resolve_runtime_main_db_path,
        )

        paths = get_paths()
        legacy = get_legacy_paths(paths=paths)
        return resolve_runtime_main_db_path(paths=paths, legacy=legacy)
    except Exception:
        return Path("dirracuda.db").resolve(strict=False)


def _resolve_reddit_sidecar_path() -> Path:
    """Resolve canonical/legacy Reddit sidecar DB path for legacy browsing."""
    try:
        from shared.path_service import get_legacy_paths, get_paths, select_existing_path

        paths = get_paths()
        legacy = get_legacy_paths(paths=paths)
        return select_existing_path(
            paths.reddit_od_db_file,
            [legacy.flat_sidecar_reddit_od_file, legacy.legacy_home_root / "reddit_od.db"],
        )
    except Exception:
        return Path("reddit_od.db").resolve(strict=False)


def _resolve_se_dork_sidecar_path() -> Path:
    """Resolve canonical/legacy SearXNG sidecar DB path for legacy browsing."""
    try:
        from shared.path_service import get_legacy_paths, get_paths, select_existing_path

        paths = get_paths()
        legacy = get_legacy_paths(paths=paths)
        return select_existing_path(
            paths.se_dork_db_file,
            [legacy.flat_sidecar_se_dork_file, legacy.legacy_home_root / "se_dork.db"],
        )
    except Exception:
        return Path("se_dork.db").resolve(strict=False)


def handle_experimental_button_click(widget) -> None:
    """Open the Experimental Features dialog from the dashboard."""
    from gui.components.experimental_features_dialog import show_experimental_features_dialog

    config_path = None
    if hasattr(widget, "_resolve_active_config_path"):
        try:
            resolved = widget._resolve_active_config_path()
            config_path = str(resolved) if resolved is not None else None
        except Exception:
            config_path = None
    if config_path is None:
        config_path = getattr(widget, "config_path", None)

    context = {
        "reddit_grab_callback": widget._handle_reddit_grab_button_click,
        "reddit_grab_status_getter": lambda: bool(
            getattr(widget, "_reddit_grab_running", False)
        ),
        "provider_queue_active_getter": lambda: bool(
            getattr(widget, "_provider_queue_active", False)
        ),
        "open_reddit_post_db": widget._open_reddit_post_db,
        "open_se_dork_results_db": lambda: open_se_dork_results_db(widget),
        "open_app_config": widget._open_config_editor,
        "open_dorkbook": lambda: open_dorkbook(widget),
        "open_keymaster": lambda: open_keymaster(widget),
        "parent": widget.parent,
        "main_db_path": str(_resolve_main_db_path(widget)),
        "running_tasks_registry": getattr(widget, "running_tasks_registry", None),
    }
    if config_path is not None:
        context["webui_config_path"] = config_path
    show_experimental_features_dialog(widget.parent, context, widget.settings_manager)


def start_analyst_task_hydration(widget) -> None:
    """Reconcile once and restore durable Analyst tasks after GUI startup."""
    if getattr(widget, "_analyst_hydration_started", False):
        return
    registry = getattr(widget, "running_tasks_registry", None)
    parent = getattr(widget, "parent", None)
    if registry is None or parent is None:
        return
    widget._analyst_hydration_started = True
    widget._analyst_hydration_stopped = False
    widget._analyst_hydration_after_id = None
    widget._analyst_hydration_reconciled = False
    widget._analyst_hydration_busy = False

    def _schedule(callback) -> None:
        try:
            if not getattr(widget, "_analyst_hydration_stopped", True):
                parent.after(0, callback)
        except Exception:
            pass

    def _cancel_factory(run_id: str):
        def _cancel() -> None:
            def _work() -> None:
                try:
                    from experimental.analyst.service import cancel_run

                    cancel_run(run_id)
                except Exception:
                    return
            threading.Thread(target=_work, daemon=True).start()
        return _cancel

    def _reopen_factory(_run_id: str):
        return lambda: handle_experimental_button_click(widget)

    def _apply(summaries) -> None:
        try:
            if summaries is not None:
                from gui.utils.analyst_tasks import apply_analyst_task_hydration

                apply_analyst_task_hydration(
                    registry,
                    summaries,
                    reopen=_reopen_factory,
                    cancel=_cancel_factory,
                )
        except Exception:
            pass
        finally:
            widget._analyst_hydration_busy = False
            try:
                if not getattr(widget, "_analyst_hydration_stopped", True):
                    widget._analyst_hydration_after_id = parent.after(5000, _launch)
            except Exception:
                pass

    def _work() -> None:
        try:
            from experimental.analyst.service import (
                list_run_summaries,
                reconcile_for_hydration,
            )

            if not getattr(widget, "_analyst_hydration_reconciled", False):
                reconcile_for_hydration()
                widget._analyst_hydration_reconciled = True
            summaries = list_run_summaries()
        except Exception:
            summaries = None
        _schedule(lambda: _apply(summaries))

    def _launch() -> None:
        if (
            getattr(widget, "_analyst_hydration_stopped", True)
            or getattr(widget, "_analyst_hydration_busy", False)
        ):
            return
        widget._analyst_hydration_busy = True
        threading.Thread(target=_work, daemon=True).start()

    _launch()


def stop_analyst_task_hydration(widget) -> None:
    """Stop the dashboard-owned durable Analyst refresh loop."""
    widget._analyst_hydration_stopped = True
    after_id = getattr(widget, "_analyst_hydration_after_id", None)
    parent = getattr(widget, "parent", None)
    if after_id is not None and parent is not None:
        try:
            parent.after_cancel(after_id)
        except Exception:
            pass
    widget._analyst_hydration_after_id = None


def open_reddit_post_db(widget) -> None:
    """Open the Reddit Post DB browser in primary-DB mode (promotion disabled)."""
    show_reddit_browser_window(
        parent=widget.parent,
        db_path=_resolve_main_db_path(widget),
        add_record_callback=None,
        promote_record_callback=None,
        promote_records_callback=None,
        allow_promotion=False,
        settings_manager=getattr(widget, "settings_manager", None),
    )


def open_se_dork_results_db(widget) -> None:
    """Open the SE Dork results browser in primary-DB mode (promotion disabled)."""
    show_se_dork_browser_window(
        parent=widget.parent,
        db_path=_resolve_main_db_path(widget),
        add_record_callback=None,
        promote_record_callback=None,
        promote_records_callback=None,
        allow_promotion=False,
        settings_manager=getattr(widget, "settings_manager", None),
    )


def open_sidecar_legacy_db(widget) -> None:
    dialog = tk.Toplevel(widget.parent)
    dialog.title("[Legacy] Sidecar Data")
    dialog.transient(widget.parent)
    dialog.resizable(False, False)
    theme = getattr(widget, "theme", None)
    if theme:
        apply_theme_to_window(dialog)

    def _pick(choice: str) -> None:
        dialog.destroy()
        if choice == "se_dork":
            show_se_dork_browser_window(
                parent=widget.parent,
                db_path=_resolve_se_dork_sidecar_path(),
                add_record_callback=None,
                promote_record_callback=_make_sidecar_promote_callback(widget),
                promote_records_callback=_make_sidecar_bulk_promote_callback(widget),
                allow_promotion=True,
                settings_manager=getattr(widget, "settings_manager", None),
            )
        elif choice == "reddit":
            show_reddit_browser_window(
                parent=widget.parent,
                db_path=_resolve_reddit_sidecar_path(),
                add_record_callback=None,
                promote_record_callback=_make_sidecar_promote_callback(widget),
                promote_records_callback=_make_sidecar_bulk_promote_callback(widget),
                allow_promotion=True,
                settings_manager=getattr(widget, "settings_manager", None),
            )
        elif choice == "migrate":
            db_reader = getattr(widget, "db_reader", None)
            if db_reader is None:
                _mb().showerror("No Database", "No database is currently loaded.",
                                parent=widget.parent)
                return
            def _worker():
                try:
                    execute_sidecar_migration_now(db_reader)
                except Exception:
                    pass
                try:
                    widget.parent.after(0, widget.refresh_after_database_change)
                except Exception:
                    pass
            threading.Thread(target=_worker, daemon=True).start()

    for label, key in [
        ("SearXNG Dork Results", "se_dork"),
        ("Reddit Open Directory Posts", "reddit"),
        ("Migrate All to Main DB", "migrate"),
    ]:
        btn = tk.Button(dialog, text=label, command=lambda k=key: _pick(k))
        if theme:
            theme.apply_to_widget(btn, "button_secondary")
        btn.pack(fill="x", padx=12, pady=4)
    ensure_dialog_focus(dialog, widget.parent)


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


def _make_sidecar_bulk_promote_callback(widget):
    """Return a direct bulk sidecar promotion callback, or None when DB is absent."""
    db_reader = getattr(widget, "db_reader", None)
    if db_reader is None:
        return None

    def _promote_many(prefills, *, cancel_event=None, progress_callback=None):
        summary = promote_sidecar_prefills(
            db_reader,
            prefills,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        promoted = int(summary.get("inserted", 0)) + int(summary.get("updated", 0))
        if promoted > 0:
            _notify_dashboard_database_changed(widget)
        return summary

    return _promote_many


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
