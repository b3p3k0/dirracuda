"""
SearXNG scan orchestration satellite (C11A extraction).

Extracted from dashboard_scan.py to stay within the 1,700-line limit while
adding cancellation support. No imports from dashboard_scan.py to avoid
circular dependencies; shared helpers are defined locally following the
pattern established in dashboard_provider_queue.py.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from gui.components.dashboard_scan_rollup import (
    format_searxng_cancelled_rollup,
    format_searxng_popup_summary,
    format_searxng_rollup,
)


# ---------------------------------------------------------------------------
# Private helpers (mirror dashboard_scan.py conventions)
# ---------------------------------------------------------------------------


def _hook(dash, method_name: str, *args, **kwargs) -> object:
    """Invoke an optional DashboardWidget hook and return its result."""
    method = getattr(dash, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args, **kwargs)
    except Exception:
        return None


def _mb():
    """Return messagebox from gui.components.dashboard's namespace at call-time."""
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    from gui.utils import safe_messagebox
    return safe_messagebox


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_main_db_path(dash) -> Path:
    reader = getattr(dash, "db_reader", None)
    reader_path = getattr(reader, "db_path", None)
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


def _emit_live_rollup(dash, rollup: str) -> None:
    _hook(dash, "_handle_scan_log_line", rollup)


def _clear_cancel_event(dash) -> None:
    """Safely clear the active SearXNG cancel event on any terminal path."""
    try:
        dash._searxng_cancel_event = None
    except Exception:
        pass


def _build_cancel_callback(dash, *, queue_managed: bool) -> Optional[Callable[[], None]]:
    """Return the cancel_callback appropriate for the run mode.

    Queue-managed: route through cancel_provider_queue (which also signals
    the event). Standalone: signal the event directly.
    """
    if queue_managed:
        from gui.components.dashboard_provider_queue import cancel_provider_queue
        return lambda: cancel_provider_queue(dash, notify=True)
    evt = getattr(dash, "_searxng_cancel_event", None)
    if evt is not None:
        return evt.set
    return None


# ---------------------------------------------------------------------------
# SearXNG scan lifecycle
# ---------------------------------------------------------------------------


def start_searxng_scan(dash, scan_request: dict) -> bool:
    """Launch a SearXNG dork search in a background thread.

    Returns True if the thread was started, False if validation failed.
    Errors during the run are reported via _on_searxng_scan_done on the UI thread.
    """
    from gui.components.dashboard_provider_queue import (
        is_provider_queue_active,
        report_launch_error,
    )

    queue_managed = bool(scan_request.get("_provider_queue_managed", False))
    provider_generation = _to_int(scan_request.get("_provider_queue_generation"))
    if not queue_managed:
        if is_provider_queue_active(dash):
            _mb().showwarning(
                "Provider Queue Busy",
                "A unified provider queue is running. Please wait for it to complete.",
            )
            return False
    if getattr(dash, "_searxng_scan_running", False):
        if not queue_managed:
            _mb().showwarning(
                "SearXNG Busy",
                "A SearXNG search is already running. Please wait for it to complete.",
            )
        return False

    instance_url = str(scan_request.get("searxng_instance_url") or "").strip()
    if not instance_url:
        report_launch_error(
            dash,
            queue_managed=queue_managed,
            title="SearXNG Error",
            message="SearXNG instance URL is required.",
        )
        return False
    query = str(scan_request.get("searxng_query") or "").strip()
    if not query:
        report_launch_error(
            dash,
            queue_managed=queue_managed,
            title="SearXNG Error",
            message="SearXNG search query is required.",
        )
        return False

    from experimental.se_dork.main_db_sync import sync_run_to_main_db
    from experimental.se_dork.models import (
        MAX_RESULTS,
        RunOptions,
        RunResult,
        RUN_STATUS_CANCELLED,
        RUN_STATUS_DONE,
        RUN_STATUS_ERROR,
    )
    from experimental.se_dork.service import run_dork_search

    sm = getattr(dash, "settings_manager", None)
    probe_config_path = None
    probe_worker_count = 3
    if sm is not None:
        if hasattr(sm, "get_smbseek_config_path"):
            try:
                probe_config_path = sm.get_smbseek_config_path()
            except Exception:
                pass
        try:
            probe_worker_count = max(1, min(8, int(
                sm.get_setting("probe.batch_max_workers", probe_worker_count)
            )))
        except Exception:
            pass

    from gui.components.scan_provider_options import (
        coerce_searxng_tuning,
        SEARXNG_TIMEOUT_DEFAULT, SEARXNG_TIMEOUT_MIN, SEARXNG_TIMEOUT_MAX,
        SEARXNG_SHORT_RETRY_DEFAULT, SEARXNG_SHORT_RETRY_MIN, SEARXNG_SHORT_RETRY_MAX,
        SEARXNG_LONG_RETRY_DEFAULT, SEARXNG_LONG_RETRY_MIN, SEARXNG_LONG_RETRY_MAX,
    )
    options = RunOptions(
        instance_url=instance_url,
        query=query,
        max_results=max(1, min(MAX_RESULTS, _to_int(scan_request.get("searxng_max_results", 500)))),
        bulk_probe_enabled=bool(scan_request.get("bulk_probe_enabled", False)),
        probe_config_path=probe_config_path,
        probe_worker_count=probe_worker_count,
        request_timeout=coerce_searxng_tuning(
            scan_request.get("searxng_request_timeout", SEARXNG_TIMEOUT_DEFAULT),
            default=SEARXNG_TIMEOUT_DEFAULT, lo=SEARXNG_TIMEOUT_MIN,
            hi=SEARXNG_TIMEOUT_MAX, step=1,
        ),
        short_retry_delay=coerce_searxng_tuning(
            scan_request.get("searxng_short_retry_delay", SEARXNG_SHORT_RETRY_DEFAULT),
            default=SEARXNG_SHORT_RETRY_DEFAULT, lo=SEARXNG_SHORT_RETRY_MIN,
            hi=SEARXNG_SHORT_RETRY_MAX, step=5,
        ),
        long_retry_delay=coerce_searxng_tuning(
            scan_request.get("searxng_long_retry_delay", SEARXNG_LONG_RETRY_DEFAULT),
            default=SEARXNG_LONG_RETRY_DEFAULT, lo=SEARXNG_LONG_RETRY_MIN,
            hi=SEARXNG_LONG_RETRY_MAX, step=30,
        ),
    )
    db_path = _resolve_main_db_path(dash)
    country = scan_request.get("country")
    _started_at = datetime.now()

    try:
        dash._searxng_scan_running = True
    except Exception:
        pass

    # Create cancellation event and wire callbacks before launching thread.
    cancel_event = threading.Event()
    _clear_cancel_event(dash)
    try:
        dash._searxng_cancel_event = cancel_event
    except Exception:
        pass

    _hook(dash, "_show_scan_output_dialog", "SearXNG", country)
    if not queue_managed:
        _hook(dash, "_reset_log_output", country)
    _hook(dash, "_set_searxng_task_running", country,
          cancel_callback=_build_cancel_callback(dash, queue_managed=queue_managed))
    _hook(dash, "_log_status_event",
          f'SearXNG search started: {instance_url} | query: "{query}"')

    _providers = [str(p).strip().lower() for p in (scan_request.get("providers") or [])]
    _searxng_only = set(_providers) == {"searxng"}

    def _ui_log(msg: str) -> None:
        def _dispatch(m=msg):
            _hook(dash, "_log_status_event", m)
            if _searxng_only:
                _hook(dash, "_update_progress_summary", "SearXNG", m)
        try:
            dash.parent.after(0, _dispatch)
        except Exception:
            pass

    def _worker():
        sync_summary = None
        try:
            result = run_dork_search(
                options, db_path=db_path, progress_cb=_ui_log,
                cancel_event=cancel_event,
            )
            if result.run_id is not None and result.status in (RUN_STATUS_DONE, RUN_STATUS_CANCELLED):
                sync_summary = sync_run_to_main_db(result.run_id, db_path=db_path)
        except Exception as exc:
            result = RunResult(
                run_id=None,
                fetched_count=0,
                deduped_count=0,
                status=RUN_STATUS_ERROR,
                error=str(exc),
            )
        try:
            dash.parent.after(0, lambda: _on_searxng_scan_done(
                dash, result,
                instance_url=options.instance_url,
                query=options.query,
                searxng_only=_searxng_only,
                started_at=_started_at,
                sync_summary=sync_summary,
                db_path=db_path,
                queue_managed=queue_managed,
                provider_generation=provider_generation,
            ))
        except Exception:
            try:
                dash._searxng_scan_running = False
            except Exception:
                pass
            _hook(dash, "_clear_searxng_task")
            _clear_cancel_event(dash)

    try:
        threading.Thread(
            target=_worker,
            name="dashboard-searxng-scan",
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        try:
            dash._searxng_scan_running = False
        except Exception:
            pass
        _hook(dash, "_clear_searxng_task")
        _clear_cancel_event(dash)
        report_launch_error(
            dash,
            queue_managed=queue_managed,
            title="SearXNG Error",
            message=f"Failed to start SearXNG scan: {exc}",
        )
        return False


def _on_searxng_scan_done(
    dash,
    result,
    *,
    instance_url: str = "",
    query: str = "",
    searxng_only: bool = True,
    started_at: Optional[datetime] = None,
    sync_summary: Optional[dict] = None,
    db_path: Optional[Path | str] = None,
    queue_managed: bool = False,
    provider_generation: int = 0,
) -> None:
    """Handle SearXNG scan completion on the UI thread.

    In multi-provider runs (searxng_only=False), the completion dialog is
    suppressed while the serial provider queue continues. Live output retains
    the durable completion rollup.
    """
    from experimental.se_dork.models import RUN_STATUS_CANCELLED, RUN_STATUS_ERROR

    try:
        dash._searxng_scan_running = False
    except Exception:
        pass
    _hook(dash, "_clear_searxng_task")

    if result.status == RUN_STATUS_CANCELLED:
        resolved_db_path = db_path or _resolve_main_db_path(dash)
        _emit_live_rollup(
            dash,
            format_searxng_cancelled_rollup(
                result, query=query, db_path=resolved_db_path, sync_summary=sync_summary,
            ),
        )
        _hook(dash, "_log_status_event", "SearXNG search cancelled.")
        _hook(dash, "_refresh_dashboard_data")
        _clear_cancel_event(dash)
        return

    if result.status == RUN_STATUS_ERROR or result.error:
        error = result.error or "unknown error"
        _hook(dash, "_log_status_event", f"SearXNG search failed: {error}")
        _clear_cancel_event(dash)
        if queue_managed:
            from gui.components.dashboard_provider_queue import complete_provider
            complete_provider(
                dash, "searxng", provider_generation, success=False, error=error,
            )
        else:
            _mb().showerror("SearXNG Scan Error", f"SearXNG search failed: {error}")
        return

    resolved_db_path = db_path or _resolve_main_db_path(dash)
    _emit_live_rollup(
        dash,
        format_searxng_rollup(
            result,
            query=query,
            db_path=resolved_db_path,
            sync_summary=sync_summary,
        ),
    )

    _clear_cancel_event(dash)

    if searxng_only:
        end_dt = datetime.now()
        duration_seconds = (end_dt - started_at).total_seconds() if started_at else 0.0
        scan_results = {
            "protocol": "searxng",
            "status": "completed",
            "hosts_scanned": result.fetched_count,
            "accessible_hosts": result.deduped_count,
            "shares_found": result.deduped_count,
            "country": instance_url or "SearXNG instance",
            "summary_message": format_searxng_popup_summary(
                result,
                query=query,
                sync_summary=sync_summary,
            ),
            "end_time": end_dt.isoformat(),
            "duration_seconds": duration_seconds,
        }
        _hook(dash, "_show_scan_results", scan_results)
    _hook(dash, "_refresh_dashboard_data")
    if queue_managed:
        from gui.components.dashboard_provider_queue import complete_provider
        complete_provider(dash, "searxng", provider_generation, success=True)
