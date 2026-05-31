"""
Scan orchestration helpers for DashboardWidget (C7 extraction).

Each function takes the dashboard instance (dash) as first arg and mirrors
the original method behavior from dashboard.py. No UI text or behavior changes.

Intra-class call discipline: calls to other DashboardWidget methods go through
dash.method_name() so instance-level monkeypatches in tests still intercept.
Messagebox calls go through _mb() so module-level patches on
gui.components.dashboard.messagebox still intercept.
"""

import json
import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


def _mb():
    """Return messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox. Calling through this
    helper means the patched object is used at call-time, preserving all
    frozen patch paths (e.g. test_dashboard_api_key_gate).
    Falls back to the real safe_messagebox if dashboard is not yet loaded.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _to_int(value: Any) -> int:
    """Best-effort integer coercion for scan metric aggregation."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _call_dashboard_hook(dash, method_name: str, *args, **kwargs) -> None:
    """Invoke optional DashboardWidget hook when present (test-safe)."""
    method = getattr(dash, method_name, None)
    if not callable(method):
        return
    try:
        method(*args, **kwargs)
    except Exception:
        return


def _parse_iso(ts: Any) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _resolve_main_db_path(dash) -> Path:
    """
    Resolve active primary DB path for dashboard-owned runs.

    Preference:
      1) live dashboard db_reader.db_path
      2) canonical runtime main DB path from path_service
    """
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


def _merge_queued_scan_results(results_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a combined multi-protocol scan summary payload.

    Aggregation policy:
      - hosts_scanned / accessible_hosts / shares_found: summed across protocols
      - start_time / end_time: earliest start and latest end
      - duration_seconds: wall-clock elapsed between earliest start and latest end
      - protocol: "multi"
      - protocols: ordered protocol list in completion order (deduped)
    """
    merged: Dict[str, Any] = {
        "status": "completed",
        "success": True,
        "protocol": "multi",
        "protocols": [],
        "hosts_scanned": 0,
        "accessible_hosts": 0,
        "shares_found": 0,
    }

    starts: List[datetime] = []
    ends: List[datetime] = []
    seen_protocols = set()
    ordered_protocols: List[str] = []

    for row in results_list:
        protocol = str(row.get("protocol") or "").strip().lower()
        if protocol and protocol not in seen_protocols:
            seen_protocols.add(protocol)
            ordered_protocols.append(protocol)

        merged["hosts_scanned"] += _to_int(row.get("hosts_scanned"))
        merged["accessible_hosts"] += _to_int(row.get("accessible_hosts"))
        merged["shares_found"] += _to_int(row.get("shares_found"))

        start_dt = _parse_iso(row.get("start_time"))
        end_dt = _parse_iso(row.get("end_time"))
        if start_dt is not None:
            starts.append(start_dt)
        if end_dt is not None:
            ends.append(end_dt)

    merged["protocols"] = ordered_protocols

    if starts:
        first_start = min(starts)
        merged["start_time"] = first_start.isoformat()
    if ends:
        last_end = max(ends)
        merged["end_time"] = last_end.isoformat()
    if starts and ends:
        merged["duration_seconds"] = max(0.0, (max(ends) - min(starts)).total_seconds())
    else:
        merged["duration_seconds"] = float(
            sum(float(row.get("duration_seconds") or 0.0) for row in results_list)
        )

    protocol_display = ", ".join(p.upper() for p in ordered_protocols) or "selected protocols"
    merged["summary_message"] = (
        f"Queued scan completed across {protocol_display}: "
        f"{merged['accessible_hosts']}/{merged['hosts_scanned']} hosts accessible."
    )
    return merged


# ── Queue / multi-protocol lifecycle ─────────────────────────────────────────

def clear_queued_scan_state(dash) -> None:
    """Reset in-memory state for queued multi-protocol scan runs."""
    dash._queued_scan_active = False
    dash._queued_scan_protocols = []
    dash._queued_scan_common_options = None
    dash._queued_scan_current_protocol = None
    dash._queued_scan_failures = []
    dash._queued_scan_results = []
    dash._queued_scan_batch_rows = {"probe": [], "extract": []}
    dash._queued_scan_total = 0


def start_unified_scan(dash, scan_request: dict) -> None:
    """
    Start scans from unified dialog request.

    If multiple protocols are selected, scans execute sequentially.
    SearXNG runs independently of the Shodan protocol queue.
    """
    providers = [
        str(p).strip().lower()
        for p in (scan_request.get("providers") or ["shodan"])
    ]

    # SearXNG dispatch — independent background run, does not block Shodan queue
    if "searxng" in providers:
        start_searxng_scan(dash, scan_request)

    # Reddit dispatch — independent background run, does not block Shodan queue
    if "reddit" in providers:
        start_reddit_scan(dash, scan_request)

    # Shodan protocol queue — existing sequential queue logic
    if "shodan" not in providers:
        return

    protocols = [
        str(p).strip().lower()
        for p in (scan_request.get("protocols") or [])
        if str(p).strip().lower() in {"smb", "ftp", "http"}
    ]
    if not protocols:
        _mb().showerror(
            "Scan Error",
            "No protocols selected. Please select at least one protocol."
        )
        return

    # Single protocol: run directly (no queue wrapper).
    if len(protocols) == 1:
        dash._clear_queued_scan_state()
        protocol = protocols[0]
        options = dash._build_protocol_scan_options(protocol, scan_request)
        dash._start_protocol_scan(protocol, options)
        return

    # Multi-protocol queue.
    dash._queued_scan_active = True
    dash._queued_scan_protocols = list(protocols)
    dash._queued_scan_common_options = dict(scan_request)
    dash._queued_scan_current_protocol = None
    dash._queued_scan_total = len(protocols)
    dash._queued_scan_failures = []
    dash._queued_scan_results = []
    dash._queued_scan_batch_rows = {"probe": [], "extract": []}
    _call_dashboard_hook(dash, "_set_scan_task_queued", protocols, scan_request.get("country"))
    dash._launch_next_queued_scan()


def build_protocol_scan_options(protocol: str, common_options: Dict[str, Any]) -> Dict[str, Any]:
    """Convert unified dialog options into protocol-specific scan options.

    Pure function — no dash state required.
    """
    country = common_options.get("country")
    verbose = bool(common_options.get("verbose", False))
    bulk_probe = bool(common_options.get("bulk_probe_enabled", False))
    bulk_extract = bool(common_options.get("bulk_extract_enabled", False))
    skip_indicator_extract = bool(common_options.get("bulk_extract_skip_indicators", True))

    def _coerce_budget(value: Any, default: int = 1) -> int:
        try:
            budget = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, budget)

    def _coerce_cap(value: Any, default: int = 100) -> int:
        try:
            cap = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, cap)

    def _budget_for_cap(cap: int) -> int:
        return max(1, (cap + 99) // 100)

    shared_cap = common_options.get("max_shodan_results")
    shared_cap_default = _coerce_cap(shared_cap, 0) if shared_cap is not None else 0

    smb_cap = _coerce_cap(
        common_options.get("smb_max_shodan_results_per_scan"),
        shared_cap_default or _coerce_budget(common_options.get("smb_max_query_credits_per_scan"), 1) * 100,
    )
    ftp_cap = _coerce_cap(
        common_options.get("ftp_max_shodan_results_per_scan"),
        shared_cap_default or _coerce_budget(common_options.get("ftp_max_query_credits_per_scan"), 1) * 100,
    )
    http_cap = _coerce_cap(
        common_options.get("http_max_shodan_results_per_scan"),
        shared_cap_default or _coerce_budget(common_options.get("http_max_query_credits_per_scan"), 1) * 100,
    )
    smb_budget = _budget_for_cap(smb_cap)
    ftp_budget = _budget_for_cap(ftp_cap)
    http_budget = _budget_for_cap(http_cap)

    try:
        shared_concurrency = int(common_options.get("shared_concurrency", 10))
    except (TypeError, ValueError):
        shared_concurrency = 10
    try:
        shared_timeout = int(common_options.get("shared_timeout_seconds", 10))
    except (TypeError, ValueError):
        shared_timeout = 10

    shared_concurrency = max(1, min(256, shared_concurrency))
    shared_timeout = max(1, min(300, shared_timeout))

    if protocol == "smb":
        security_mode = str(common_options.get("security_mode", "cautious")).strip().lower()
        if security_mode not in {"cautious", "legacy"}:
            security_mode = "cautious"
        return {
            "country": country,
            "max_shodan_results": smb_cap,
            "discovery_max_concurrent_hosts": shared_concurrency,
            "access_max_concurrent_hosts": shared_concurrency,
            "connection_timeout": shared_timeout,
            "security_mode": security_mode,
            "verbose": verbose,
            "bulk_probe_enabled": bulk_probe,
            "bulk_extract_enabled": bulk_extract,
            "bulk_extract_skip_indicators": skip_indicator_extract,
            "smb_max_shodan_results_per_scan": smb_cap,
            "ftp_max_shodan_results_per_scan": ftp_cap,
            "http_max_shodan_results_per_scan": http_cap,
            "smb_max_query_credits_per_scan": smb_budget,
            "ftp_max_query_credits_per_scan": ftp_budget,
            "http_max_query_credits_per_scan": http_budget,
        }

    if protocol == "ftp":
        return {
            "country": country,
            "max_shodan_results": ftp_cap,
            "discovery_max_concurrent_hosts": shared_concurrency,
            "access_max_concurrent_hosts": shared_concurrency,
            "connect_timeout": shared_timeout,
            "auth_timeout": shared_timeout,
            "listing_timeout": shared_timeout,
            "verbose": verbose,
            "bulk_probe_enabled": bulk_probe,
            "bulk_extract_enabled": bulk_extract,
            "bulk_extract_skip_indicators": skip_indicator_extract,
            "smb_max_shodan_results_per_scan": smb_cap,
            "ftp_max_shodan_results_per_scan": ftp_cap,
            "http_max_shodan_results_per_scan": http_cap,
            "smb_max_query_credits_per_scan": smb_budget,
            "ftp_max_query_credits_per_scan": ftp_budget,
            "http_max_query_credits_per_scan": http_budget,
        }

    # HTTP
    allow_insecure_tls = bool(common_options.get("allow_insecure_tls", True))
    return {
        "country": country,
        "max_shodan_results": http_cap,
        "discovery_max_concurrent_hosts": shared_concurrency,
        "access_max_concurrent_hosts": shared_concurrency,
        "connect_timeout": shared_timeout,
        "request_timeout": shared_timeout,
        "subdir_timeout": shared_timeout,
        "verify_http": True,
        "verify_https": True,
        "allow_insecure_tls": allow_insecure_tls,
        "verbose": verbose,
        "bulk_probe_enabled": bulk_probe,
        "bulk_extract_enabled": bulk_extract,
        "bulk_extract_skip_indicators": skip_indicator_extract,
        "smb_max_shodan_results_per_scan": smb_cap,
        "ftp_max_shodan_results_per_scan": ftp_cap,
        "http_max_shodan_results_per_scan": http_cap,
        "smb_max_query_credits_per_scan": smb_budget,
        "ftp_max_query_credits_per_scan": ftp_budget,
        "http_max_query_credits_per_scan": http_budget,
    }


def start_protocol_scan(dash, protocol: str, scan_options: Dict[str, Any]) -> bool:
    """Dispatch launch to the existing protocol-specific start handlers."""
    if protocol == "smb":
        return bool(dash._start_new_scan(scan_options))
    if protocol == "ftp":
        return bool(dash._start_ftp_scan(scan_options))
    if protocol == "http":
        return bool(dash._start_http_scan(scan_options))
    return False


def abort_queued_scan_on_failure(
    dash,
    protocol: str,
    reason: str,
    *,
    title: str = "Protocol Scan Failed",
) -> None:
    """Abort remaining queued protocol scans after a failure."""
    remaining = [p.upper() for p in dash._queued_scan_protocols if p]
    skipped_text = ", ".join(remaining) if remaining else "None"

    dash._queued_scan_failures.append({"protocol": protocol, "reason": reason})
    dash._clear_queued_scan_state()
    _call_dashboard_hook(dash, "_clear_scan_task")
    _mb().showwarning(
        title,
        f"{protocol.upper()} scan failed. Remaining queued scans were not started.\n\n"
        f"Reason: {reason}\n"
        f"Skipped protocols: {skipped_text}",
    )


def launch_next_queued_scan(dash) -> None:
    """Start the next protocol in queue, if any remain."""
    if not dash._queued_scan_active:
        return

    if not dash._queued_scan_protocols:
        if dash._queued_scan_failures:
            lines = [
                f"- {item['protocol'].upper()}: {item['reason']}"
                for item in dash._queued_scan_failures
            ]
            _mb().showwarning(
                "Queued Scans Completed With Failures",
                "One or more protocol scans failed:\n\n" + "\n".join(lines),
            )
        dash._clear_queued_scan_state()
        _call_dashboard_hook(dash, "_clear_scan_task")
        return

    protocol = dash._queued_scan_protocols.pop(0)
    dash._queued_scan_current_protocol = protocol
    common = dash._queued_scan_common_options or {}
    scan_options = dash._build_protocol_scan_options(protocol, common)

    started = dash._start_protocol_scan(protocol, scan_options)
    if not started:
        dash._abort_queued_scan_on_failure(
            protocol,
            "failed to start",
            title="Protocol Start Failed",
        )
        return


def handle_queued_scan_completion(dash, results: Dict[str, Any]) -> None:
    """Handle queue continuation after each protocol scan completes."""
    if not dash._queued_scan_active:
        return

    protocol = (dash._queued_scan_current_protocol or results.get("protocol") or "smb").lower()
    status = str(results.get("status", "")).lower()
    success = bool(results.get("success", False))
    error = str(results.get("error", "") or "").strip()

    # User cancellation stops the queue.
    if status == "cancelled":
        dash._clear_queued_scan_state()
        _call_dashboard_hook(dash, "_clear_scan_task")
        _mb().showinfo(
            "Queued Scans Cancelled",
            "Scan queue cancelled by user. Remaining protocols were not started.",
        )
        return

    failed = status in {"failed", "error"} or (not success and bool(error))
    if failed:
        reason = error or status or "unknown error"
        dash._abort_queued_scan_on_failure(protocol, reason)
        _call_dashboard_hook(dash, "_clear_scan_task")
        return

    # Success path: record per-protocol results for final aggregate dialog.
    recorded = dict(results)
    recorded["protocol"] = protocol
    if not isinstance(getattr(dash, "_queued_scan_results", None), list):
        dash._queued_scan_results = []
    dash._queued_scan_results.append(recorded)

    payload = results.get("_batch_summary_payload") or {}
    if isinstance(payload, dict):
        if not isinstance(getattr(dash, "_queued_scan_batch_rows", None), dict):
            dash._queued_scan_batch_rows = {"probe": [], "extract": []}
        for job_type in ("probe", "extract"):
            rows = payload.get(job_type) or []
            if rows:
                dash._queued_scan_batch_rows.setdefault(job_type, [])
                dash._queued_scan_batch_rows[job_type].extend([dict(r) for r in rows])

    if dash._queued_scan_protocols:
        _call_dashboard_hook(dash, "_set_scan_task_waiting_next")
        try:
            dash.parent.after(150, dash._launch_next_queued_scan)
        except tk.TclError:
            pass
    else:
        # Queue complete: show combined summaries + combined scan results.
        combined_probe = list((dash._queued_scan_batch_rows or {}).get("probe", []))
        combined_extract = list((dash._queued_scan_batch_rows or {}).get("extract", []))
        if combined_probe:
            dash._show_batch_summary(combined_probe, job_type="probe")
        if combined_extract:
            dash._show_batch_summary(combined_extract, job_type="extract")

        combined_results = _merge_queued_scan_results(getattr(dash, "_queued_scan_results", []))
        dash._show_scan_results(combined_results)
        try:
            dash.parent.after(5000, dash._reset_scan_status)
        except Exception:
            pass

        dash._clear_queued_scan_state()
        _call_dashboard_hook(dash, "_clear_scan_task")


# ── Pre-scan checks ───────────────────────────────────────────────────────────

def ensure_shodan_api_key_for_scan(dash, scan_options: Dict[str, Any]) -> bool:
    """
    Ensure scans have a persisted Shodan API key before launch.

    If config key is missing:
    - Use api_key_override when provided (persist and continue), or
    - Prompt user for key (persist; abort when cancelled/failed).
    """
    if bool(getattr(dash.backend_interface, "mock_mode", False)):
        return True

    configured_key = dash._read_shodan_api_key_from_config()
    if configured_key:
        return True

    override_key = str(scan_options.get("api_key_override") or "").strip()
    if not override_key:
        override_key = str(dash._prompt_for_shodan_api_key() or "").strip()
        if not override_key:
            _mb().showinfo(
                "Scan Cancelled",
                "Scan start was cancelled because no Shodan API key was provided.",
                parent=dash.parent,
            )
            return False

    if not dash._persist_shodan_api_key_to_config(override_key):
        _mb().showerror(
            "Configuration Error",
            "Failed to save Shodan API key to config file.\n\n"
            "Please check config file permissions and try again.",
            parent=dash.parent,
        )
        return False

    # Ensure immediate run uses the newly provided key even before any
    # backend config reload.
    scan_options["api_key_override"] = override_key
    return True


def check_external_scans(dash) -> None:
    """Check for external scans using lock file system."""
    try:
        if dash.scan_manager.is_scan_active():
            # Get lock file info
            lock_file_path = os.path.join(os.path.dirname(__file__), '..', '..', '.scan_lock')
            if os.path.exists(lock_file_path):
                with open(lock_file_path, 'r') as f:
                    lock_data = json.load(f)

                # Check if it's our own scan or external
                lock_pid = lock_data.get('process_id')
                current_pid = os.getpid()

                if lock_pid != current_pid:
                    # External scan detected
                    if dash._validate_external_process(lock_pid):
                        dash.external_scan_pid = lock_pid
                        dash._update_scan_button_state("disabled_external")
                        return
                    else:
                        # Stale lock file - clean it up
                        dash.scan_manager._cleanup_stale_locks()
                else:
                    # Our own scan is running
                    if dash.scan_manager.is_scanning:
                        dash._update_scan_button_state("scanning")
                    else:
                        # Scan completed, update state
                        dash._update_scan_button_state("idle")
                    return

        # No active scans detected
        dash._update_scan_button_state("idle")

    except Exception as e:
        _logger.warning("Error checking external scans: %s", e)
        # Fallback to idle state
        dash._update_scan_button_state("idle")


# ── SearXNG launch handlers ───────────────────────────────────────────────────

def start_searxng_scan(dash, scan_request: dict) -> bool:
    """Launch a SearXNG dork search in a background thread.

    Returns True if the thread was started, False if validation failed.
    Errors during the run are reported via _on_searxng_scan_done on the UI thread.
    """
    if getattr(dash, "_searxng_scan_running", False):
        _mb().showwarning(
            "SearXNG Busy",
            "A SearXNG search is already running. Please wait for it to complete."
        )
        return False

    instance_url = str(scan_request.get("searxng_instance_url") or "").strip()
    if not instance_url:
        _mb().showerror("SearXNG Error", "SearXNG instance URL is required.")
        return False
    query = str(scan_request.get("searxng_query") or "").strip()
    if not query:
        _mb().showerror("SearXNG Error", "SearXNG search query is required.")
        return False

    from experimental.se_dork.main_db_sync import sync_run_to_main_db
    from experimental.se_dork.models import RunOptions, RunResult, RUN_STATUS_ERROR
    from experimental.se_dork.service import run_dork_search

    # Resolve probe config and worker count from settings (mirrors se_dork_tab.py)
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

    options = RunOptions(
        instance_url=instance_url,
        query=query,
        max_results=max(1, min(500, _to_int(scan_request.get("searxng_max_results", 50)))),
        bulk_probe_enabled=bool(scan_request.get("bulk_probe_enabled", False)),
        probe_config_path=probe_config_path,
        probe_worker_count=probe_worker_count,
    )
    db_path = _resolve_main_db_path(dash)
    country = scan_request.get("country")
    _started_at = datetime.now()

    try:
        dash._searxng_scan_running = True
    except Exception:
        pass

    _call_dashboard_hook(dash, "_show_scan_output_dialog", "SearXNG", country)
    _call_dashboard_hook(dash, "_reset_log_output", country)
    _call_dashboard_hook(dash, "_set_searxng_task_running", country)
    _call_dashboard_hook(dash, "_log_status_event",
        f'SearXNG search started: {instance_url} | query: "{query}"')

    _providers = [str(p).strip().lower() for p in (scan_request.get("providers") or [])]
    _searxng_only = set(_providers) == {"searxng"}

    def _ui_log(msg: str) -> None:
        """Marshal progress from worker thread to log + (if SearXNG-only) summary bar.

        Skips _update_progress_summary when Shodan is also running to avoid
        overwriting Shodan's own progress display.
        """
        def _dispatch(m=msg):
            _call_dashboard_hook(dash, "_log_status_event", m)
            if _searxng_only:
                _call_dashboard_hook(dash, "_update_progress_summary", "SearXNG", m)
        try:
            dash.parent.after(0, _dispatch)
        except Exception:
            pass

    def _worker():
        sync_summary = None
        try:
            result = run_dork_search(options, db_path=db_path, progress_cb=_ui_log)
            if result.status != RUN_STATUS_ERROR and not result.error:
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
            ))
        except Exception:
            # Tk scheduling failed — minimal cleanup, no UI calls
            try:
                dash._searxng_scan_running = False
            except Exception:
                pass
            _call_dashboard_hook(dash, "_clear_searxng_task")

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
        _call_dashboard_hook(dash, "_clear_searxng_task")
        _mb().showerror("SearXNG Error", f"Failed to start SearXNG scan: {exc}")
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
) -> None:
    """Handle SearXNG scan completion on the UI thread.

    In mixed SearXNG+Shodan runs (searxng_only=False) the completion dialog is
    intentionally suppressed — the Shodan queue may still be active and a modal
    would interrupt it. Completion is signalled via live status lines only.
    """
    from experimental.se_dork.models import RUN_STATUS_ERROR

    try:
        dash._searxng_scan_running = False
    except Exception:
        pass
    _call_dashboard_hook(dash, "_clear_searxng_task")

    if result.status == RUN_STATUS_ERROR or result.error:
        _call_dashboard_hook(dash, "_log_status_event",
            f"SearXNG search failed: {result.error or 'unknown error'}")
        _mb().showerror(
            "SearXNG Scan Error",
            f"SearXNG search failed: {result.error or 'unknown error'}",
        )
        return

    _call_dashboard_hook(dash, "_log_status_event",
        f"SearXNG search complete. Fetched: {result.fetched_count}, stored: {result.deduped_count}")
    if result.probe_enabled:
        _call_dashboard_hook(dash, "_log_status_event",
            f"Probe: {result.probe_total} attempted"
            f" — {result.probe_clean} clean"
            f", {result.probe_issue} flagged"
            f", {result.probe_unprobed} unprobed")
    if isinstance(sync_summary, dict):
        _call_dashboard_hook(
            dash,
            "_log_status_event",
            "Primary DB sync: "
            f"processed {int(sync_summary.get('processed', 0) or 0)}"
            f" (inserted {int(sync_summary.get('inserted', 0) or 0)},"
            f" updated {int(sync_summary.get('updated', 0) or 0)},"
            f" skipped {int(sync_summary.get('skipped', 0) or 0)},"
            f" failed {int(sync_summary.get('failed', 0) or 0)},"
            f" cancelled {int(sync_summary.get('cancelled', 0) or 0)}).",
        )

    if searxng_only:
        end_dt = datetime.now()
        duration_seconds = (end_dt - started_at).total_seconds() if started_at else 0.0
        summary_lines = [
            f"SearXNG dork search complete. {result.fetched_count} URLs fetched, "
            f"{result.deduped_count} retained as open-index results.",
        ]
        if query:
            summary_lines += ["", f"Query: {query}"]
        summary_lines += [
            "",
            "Retained results were written to the primary Dirracuda database during this run.",
        ]
        if isinstance(sync_summary, dict):
            summary_lines += [
                "",
                "Primary DB sync: "
                f"{int(sync_summary.get('processed', 0) or 0)} processed "
                f"({int(sync_summary.get('inserted', 0) or 0)} inserted, "
                f"{int(sync_summary.get('updated', 0) or 0)} updated, "
                f"{int(sync_summary.get('skipped', 0) or 0)} skipped, "
                f"{int(sync_summary.get('failed', 0) or 0)} failed).",
            ]
        if result.probe_enabled:
            summary_lines += [
                "",
                f"Probe: {result.probe_total} attempted — {result.probe_clean} clean, "
                f"{result.probe_issue} flagged, {result.probe_unprobed} unprobed.",
            ]
        scan_results = {
            "protocol": "searxng",
            "status": "completed",
            "hosts_scanned": result.fetched_count,
            "accessible_hosts": result.deduped_count,
            "shares_found": result.deduped_count,
            "country": instance_url or "SearXNG instance",
            "summary_message": "\n".join(summary_lines),
            "end_time": end_dt.isoformat(),
            "duration_seconds": duration_seconds,
        }
        _call_dashboard_hook(dash, "_show_scan_results", scan_results)
    _call_dashboard_hook(dash, "_refresh_dashboard_data")


def start_reddit_scan(dash, scan_request: dict) -> bool:
    """Launch a background Reddit ingest from a unified-scan request dict.

    Cross-guards against both _reddit_scan_running (this path) and
    _reddit_grab_running (legacy accessory path) to prevent DB lock contention.
    Returns True if the worker thread started, False otherwise.
    """
    if getattr(dash, "_reddit_scan_running", False) or getattr(dash, "_reddit_grab_running", False):
        _mb().showwarning("Reddit Busy",
            "A Reddit ingest is already running. Please wait for it to complete.")
        return False

    from experimental.redseek.service import IngestOptions, run_ingest
    from shared.path_service import get_paths

    mode = str(scan_request.get("reddit_mode") or "feed").strip()
    sort = str(scan_request.get("reddit_sort") or "new").strip()
    top_window = str(scan_request.get("reddit_top_window") or "week").strip()
    max_posts = max(1, min(200, _to_int(scan_request.get("reddit_max_posts", 50))))
    query = str(scan_request.get("reddit_query") or "").strip()
    username = str(scan_request.get("reddit_username") or "").strip()
    parse_body = bool(scan_request.get("reddit_parse_body", True))
    include_nsfw = bool(scan_request.get("reddit_include_nsfw", False))

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

    options = IngestOptions(
        sort=sort,
        max_posts=max_posts,
        parse_body=parse_body,
        include_nsfw=include_nsfw,
        replace_cache=False,
        max_pages=3,
        subreddit="opendirectories",
        top_window=top_window,
        mode=mode,
        query=query,
        username=username,
        bulk_probe_enabled=bool(scan_request.get("bulk_probe_enabled", False)),
        probe_config_path=probe_config_path,
        probe_worker_count=probe_worker_count,
    )
    db_path = get_paths().reddit_od_db_file
    _started_at = datetime.now()

    try:
        dash._reddit_scan_running = True
    except Exception:
        pass

    _providers = [str(p).strip().lower() for p in (scan_request.get("providers") or [])]
    _reddit_only = set(_providers) == {"reddit"}

    _call_dashboard_hook(dash, "_show_scan_output_dialog", "Reddit", None)
    _call_dashboard_hook(dash, "_reset_log_output", None)
    _call_dashboard_hook(dash, "_set_reddit_task_running", None)
    _call_dashboard_hook(dash, "_log_status_event",
        f'Reddit {mode} ingest started (sort: {sort}, max_posts: {max_posts})')

    def _ui_log(msg: str) -> None:
        def _dispatch(m=msg):
            _call_dashboard_hook(dash, "_log_status_event", m)
            if _reddit_only:
                _call_dashboard_hook(dash, "_update_progress_summary", "Reddit", m)
        try:
            dash.parent.after(0, _dispatch)
        except Exception:
            pass

    def _worker():
        try:
            _ui_log("Fetching Reddit posts...")
            result = run_ingest(options, db_path=db_path)
        except Exception as exc:
            from experimental.redseek.service import IngestResult
            result = IngestResult(
                sort=sort, subreddit="opendirectories",
                pages_fetched=0, posts_stored=0, posts_skipped=0,
                targets_stored=0, targets_deduped=0, parse_errors=0,
                stopped_by_cursor=False, stopped_by_max_posts=False,
                replace_cache_done=False, rate_limited=False,
                error=str(exc),
                probe_enabled=False, probe_total=0, probe_clean=0,
                probe_issue=0, probe_unprobed=0, probe_skipped=0,
            )
        try:
            dash.parent.after(0, lambda: _on_reddit_scan_done(
                dash, result,
                mode=mode,
                reddit_only=_reddit_only,
                started_at=_started_at,
            ))
        except Exception:
            try:
                dash._reddit_scan_running = False
            except Exception:
                pass
            _call_dashboard_hook(dash, "_clear_reddit_task")

    try:
        threading.Thread(
            target=_worker,
            name="dashboard-reddit-scan",
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        try:
            dash._reddit_scan_running = False
        except Exception:
            pass
        _call_dashboard_hook(dash, "_clear_reddit_task")
        _mb().showerror("Reddit Error", f"Failed to start Reddit ingest: {exc}")
        return False


def _on_reddit_scan_done(
    dash,
    result,
    *,
    mode: str = "feed",
    reddit_only: bool = True,
    started_at: Optional[datetime] = None,
) -> None:
    """Handle Reddit ingest completion on the UI thread."""
    try:
        dash._reddit_scan_running = False
    except Exception:
        pass
    _call_dashboard_hook(dash, "_clear_reddit_task")

    if result.error:
        _call_dashboard_hook(dash, "_log_status_event",
            f"Reddit ingest failed: {result.error}")
        _mb().showerror("Reddit Ingest Error", f"Reddit ingest failed: {result.error}")
        return

    _call_dashboard_hook(dash, "_log_status_event",
        f"Reddit ingest complete. Posts stored: {result.posts_stored}, targets: {result.targets_stored}")
    if result.probe_enabled:
        _call_dashboard_hook(dash, "_log_status_event",
            f"Probe: {result.probe_total} attempted"
            f" — {result.probe_clean} clean"
            f", {result.probe_issue} flagged"
            f", {result.probe_unprobed} unprobed")

    if reddit_only:
        end_dt = datetime.now()
        duration_seconds = (end_dt - started_at).total_seconds() if started_at else 0.0
        summary_lines = [
            f"Reddit {mode} ingest complete. {result.posts_stored} posts stored, "
            f"{result.targets_stored} targets retained.",
            "",
            "Results are stored in the Reddit sidecar database (reddit_od.db) and can be "
            "promoted to the main database via the Reddit browser.",
        ]
        if result.probe_enabled:
            summary_lines += [
                "",
                f"Probe: {result.probe_total} attempted — {result.probe_clean} clean, "
                f"{result.probe_issue} flagged, {result.probe_unprobed} unprobed.",
            ]
        scan_results = {
            "protocol": "reddit",
            "status": "completed",
            "hosts_scanned": result.posts_stored,
            "accessible_hosts": result.targets_stored,
            "shares_found": result.targets_stored,
            "country": "Reddit /r/opendirectories",
            "summary_message": "\n".join(summary_lines),
            "end_time": end_dt.isoformat(),
            "duration_seconds": duration_seconds,
        }
        _call_dashboard_hook(dash, "_show_scan_results", scan_results)
    _call_dashboard_hook(dash, "_refresh_dashboard_data")


# ── Protocol launch handlers ──────────────────────────────────────────────────

def start_new_scan(dash, scan_options: dict) -> bool:
    """Start new scan with specified options."""
    try:
        # Final check for external scans before starting
        dash._check_external_scans()
        if dash.scan_button_state != "idle":
            return False  # External scan detected, don't proceed

        if not dash._ensure_shodan_api_key_for_scan(scan_options):
            return False

        # Store scan options for post-scan batch operations
        dash.current_scan_options = scan_options

        # Get backend path for external SMBSeek installation
        backend_path = getattr(dash.backend_interface, "backend_path", ".")
        backend_path = str(backend_path)

        # Start scan via scan manager with new options
        success = dash.scan_manager.start_scan(
            scan_options=scan_options,
            backend_path=backend_path,
            progress_callback=dash._handle_scan_progress,
            log_callback=dash._handle_scan_log_line,
            config_path=dash.config_path,
        )

        if success:
            _call_dashboard_hook(dash, "_show_scan_output_dialog", "SMB", scan_options.get("country"))

            # Reset viewer and note which scan is running
            dash._reset_log_output(scan_options.get('country'))
            _call_dashboard_hook(dash, "_set_scan_task_running", "SMB", scan_options.get("country"))

            # Update button state to scanning
            dash._update_scan_button_state("scanning")

            # Show progress display
            country = scan_options.get('country')
            dash._show_scan_progress(country)

            # Start monitoring scan completion
            dash._monitor_scan_completion()
            return True
        else:
            # Get more specific error information
            error_details = []

            # Check if backend path exists
            if not os.path.exists(backend_path):
                error_details.append(f"• Backend path not found: {backend_path}")

            # Check if SMBSeek executable exists
            smbseek_cli = os.path.join(backend_path, "cli", "smbseek.py")
            if not os.path.exists(smbseek_cli):
                error_details.append(f"• Dirracuda CLI not found: {smbseek_cli}")

            # Check scan manager state
            if dash.scan_manager.is_scanning:
                error_details.append("• Scan manager reports scan already in progress")

            # Check for lock file
            lock_file_path = os.path.join(os.path.dirname(__file__), '..', '..', '.scan_lock')
            if os.path.exists(lock_file_path):
                error_details.append("• Lock file exists, indicating another scan may be running")

            if error_details:
                detailed_msg = "Failed to start scan. Issues detected:\n\n" + "\n".join(error_details)
                detailed_msg += "\n\nPlease ensure Dirracuda is properly installed and configured."
            else:
                detailed_msg = "Failed to start scan. Another scan may already be running."

            _mb().showerror("Scan Error", detailed_msg)
            return False
    except Exception as e:
        error_msg = str(e)

        # Provide specific guidance based on error type
        if "backend" in error_msg.lower() or "not found" in error_msg.lower():
            detailed_msg = (
                f"Backend interface error: {error_msg}\n\n"
                "This usually indicates:\n"
                "• Dirracuda backend is not installed or not in expected location\n"
                "• Backend CLI is not executable\n"
                "• Configuration file is missing\n\n"
                "Please ensure the backend is properly installed and configured."
            )
        elif "lock" in error_msg.lower():
            detailed_msg = (
                f"Scan coordination error: {error_msg}\n\n"
                "Another scan may already be running. Please wait for it to complete\n"
                "or restart the application if the scan appears to be stuck."
            )
        else:
            detailed_msg = (
                f"Scan initialization failed: {error_msg}\n\n"
                "Please try again or check the configuration settings."
            )

        _mb().showerror("Scan Error", detailed_msg)
        return False


def start_ftp_scan(dash, scan_options: dict) -> bool:
    """Start FTP scan with options from dialog. Mirrors start_new_scan()."""
    # Final race-condition check before acquiring scan lock.
    dash._check_external_scans()
    if dash.scan_button_state != "idle":
        return False

    if not dash._ensure_shodan_api_key_for_scan(scan_options):
        return False

    # BackendInterface expects a directory path; "." mirrors BackendInterface defaults.
    backend_path_obj = getattr(dash.backend_interface, "backend_path", None)
    backend_path = str(backend_path_obj) if backend_path_obj else "."

    started = dash.scan_manager.start_ftp_scan(
        scan_options=scan_options,
        backend_path=backend_path,
        progress_callback=dash._handle_scan_progress,
        log_callback=dash._handle_scan_log_line,
        config_path=dash.config_path,
    )

    if started:
        dash.current_scan_options = scan_options
        _call_dashboard_hook(dash, "_show_scan_output_dialog", "FTP", scan_options.get("country"))
        dash._reset_log_output(scan_options.get("country"))
        _call_dashboard_hook(dash, "_set_scan_task_running", "FTP", scan_options.get("country"))
        dash._update_scan_button_state("scanning")
        dash._show_scan_progress(scan_options.get("country"))
        dash._monitor_scan_completion()
        return True
    else:
        _mb().showerror(
            "FTP Scan Error",
            "Could not start FTP scan.\n"
            "A scan may already be running.",
            parent=dash.parent,
        )
        return False


def start_http_scan(dash, scan_options: dict) -> bool:
    """Start HTTP scan with options from dialog. Mirrors start_ftp_scan()."""
    # Final race-condition check before acquiring scan lock.
    dash._check_external_scans()
    if dash.scan_button_state != "idle":
        return False

    if not dash._ensure_shodan_api_key_for_scan(scan_options):
        return False

    # BackendInterface expects a directory path; "." mirrors BackendInterface defaults.
    backend_path_obj = getattr(dash.backend_interface, "backend_path", None)
    backend_path = str(backend_path_obj) if backend_path_obj else "."

    started = dash.scan_manager.start_http_scan(
        scan_options=scan_options,
        backend_path=backend_path,
        progress_callback=dash._handle_scan_progress,
        log_callback=dash._handle_scan_log_line,
        config_path=dash.config_path,
    )

    if started:
        dash.current_scan_options = scan_options
        _call_dashboard_hook(dash, "_show_scan_output_dialog", "HTTP", scan_options.get("country"))
        dash._reset_log_output(scan_options.get("country"))
        _call_dashboard_hook(dash, "_set_scan_task_running", "HTTP", scan_options.get("country"))
        dash._update_scan_button_state("scanning")
        dash._show_scan_progress(scan_options.get("country"))
        dash._monitor_scan_completion()
        return True
    else:
        _mb().showerror(
            "HTTP Scan Error",
            "Could not start HTTP scan.\n"
            "A scan may already be running.",
            parent=dash.parent,
        )
        return False


# ── Progress handling ─────────────────────────────────────────────────────────

def handle_scan_progress(dash, percentage: float, status: str, phase: str) -> None:
    """Handle progress updates from scan manager."""
    try:
        # Update status text with phase/percentage info
        detail_text = status if status else None
        if phase:
            phase_display = phase.replace("_", " ").title()
            if percentage is not None:
                progress_text = f"{phase_display}: {percentage:.0f}%"
            else:
                progress_text = phase_display
        else:
            if percentage is not None:
                progress_text = f"{percentage:.0f}% complete"
            else:
                progress_text = None

        if not progress_text:
            progress_text = detail_text if detail_text else "Processing..."
            detail_text = None

        dash._update_progress_summary(progress_text, detail_text)

        # Note: No explicit update() needed here. When using UIDispatcher,
        # this callback runs on the main thread via after(), so Tk's event
        # loop handles UI refreshes automatically. Calling update() from a
        # dispatched callback would be unnecessary and risk reentrancy.

    except Exception as e:
        # Log error but don't interrupt scan
        _logger.warning("Progress update error: %s", e)


def show_scan_progress(dash, country: Optional[str]) -> None:
    """Transition progress display to active scanning state."""
    scan_target = country if country else "global"
    summary = f"Initializing {scan_target} scan"
    dash._update_progress_summary(summary, "Setting up scan parameters...")
    dash._log_status_event(summary)


def monitor_scan_completion(dash) -> None:
    """Monitor scan for completion and show results."""
    STOP_TIMEOUT_SECONDS = 10

    def check_completion():
        try:
            # Check for stop timeout while in "stopping" state
            if dash.scan_button_state == "stopping" and dash.stopping_started_time:
                elapsed = time.time() - dash.stopping_started_time
                if elapsed > STOP_TIMEOUT_SECONDS and dash.scan_manager.is_scanning:
                    # Stop is taking too long - offer retry
                    dash._update_scan_button_state("retry")
                    dash._log_status_event(
                        f"Stop taking longer than {STOP_TIMEOUT_SECONDS}s. "
                        "Click 'Stop (retry)' to try again."
                    )
                    # Continue monitoring
                    try:
                        dash.parent.after(1000, check_completion)
                    except tk.TclError:
                        pass
                    return

            if not dash.scan_manager.is_scanning:
                # Get results first to check status
                results = dash.scan_manager.get_scan_results()
                is_queued_run = bool(dash._queued_scan_active)

                # Reset button state to idle
                dash._update_scan_button_state("idle")

                # Handle cancelled scans differently
                if results and results.get("status") == "cancelled":
                    # Show lightweight info message for cancelled scan
                    try:
                        _mb().showinfo(
                            "Scan Cancelled",
                            "Scan was cancelled by user request."
                        )
                    except Exception:
                        # Fallback - log message
                        _logger.info("Scan cancelled by user")
                    dash._log_status_event("Scan cancelled by user request")
                    dash._reset_scan_status()
                elif results:
                    status = results.get("status", "")
                    success = results.get("success", False)
                    error = results.get("error")
                    # Be tolerant of different result field names
                    hosts_scanned = (
                        results.get("hosts_scanned", 0)
                        or results.get("hosts_tested", 0)
                        or results.get("hosts_discovered", 0)
                        or results.get("accessible_hosts", 0)
                        or results.get("shares_found", 0)
                        or 0
                    )

                    # Run bulk ops if scan finished and wasn't cancelled
                    # Permissive: check success flag OR status in completed/success/failed
                    is_finished = status not in {"cancelled"} and (
                        success or status in {"completed", "success", "failed"}
                    )
                    has_new_hosts = hosts_scanned > 0

                    bulk_probe_enabled = dash.current_scan_options.get('bulk_probe_enabled', False) if dash.current_scan_options else False
                    bulk_extract_enabled = dash.current_scan_options.get('bulk_extract_enabled', False) if dash.current_scan_options else False
                    has_bulk_ops = dash.current_scan_options and is_finished and has_new_hosts and (bulk_probe_enabled or bulk_extract_enabled)

                    # Debug output for bulk ops decision
                    if os.getenv("XSMBSEEK_DEBUG_PARSING") or os.getenv("DIRRACUDA_DEBUG_PARSING"):
                        _logger.debug("Bulk ops decision: status=%s, success=%s, is_finished=%s",
                                    status, success, is_finished)
                        _logger.debug("hosts_scanned=%d, has_new_hosts=%s",
                                    hosts_scanned, has_new_hosts)
                        _logger.debug("bulk_probe_enabled=%s, bulk_extract_enabled=%s",
                                    bulk_probe_enabled, bulk_extract_enabled)
                        _logger.debug("has_bulk_ops=%s", has_bulk_ops)

                    if has_bulk_ops:
                        dash._pending_scan_results = results
                        batch_payload = dash._run_post_scan_batch_operations(
                            dash.current_scan_options,
                            results,
                            schedule_reset=not is_queued_run,
                            show_dialogs=not is_queued_run,
                        )
                        if is_queued_run and isinstance(results, dict):
                            results["_batch_summary_payload"] = batch_payload or {}
                    else:
                        # For queued multi-protocol runs, suppress per-protocol
                        # summaries and show one aggregate summary at queue end.
                        if not is_queued_run:
                            dash._show_scan_results(results)
                        if not is_queued_run:
                            try:
                                dash.parent.after(5000, dash._reset_scan_status)
                            except tk.TclError:
                                pass
                else:
                    dash._reset_scan_status()
                # If no results, scan may have been cancelled before any results were recorded

                # Refresh dashboard data with cache invalidation
                try:
                    dash._refresh_after_scan_completion()
                except Exception as e:
                    _logger.warning("Dashboard refresh error after scan: %s", e)
                    # Continue anyway

                if is_queued_run and results:
                    dash._handle_queued_scan_completion(results)
                elif is_queued_run and not results:
                    dash._clear_queued_scan_state()
                    _call_dashboard_hook(dash, "_clear_scan_task")
                else:
                    _call_dashboard_hook(dash, "_clear_scan_task")
            else:
                # Check again in 1 second
                try:
                    dash.parent.after(1000, check_completion)
                except tk.TclError:
                    # UI destroyed, stop monitoring
                    pass

        except Exception as e:
            # Critical error in monitoring, show error and stop
            try:
                _mb().showerror(
                    "Scan Monitoring Error",
                    f"Error monitoring scan progress: {str(e)}\n\n"
                    "The scan may still be running in the background.\n"
                    "Please check the scan results manually."
                )
            except Exception:
                # Even error dialog failed, just stop monitoring
                pass

            # Try to clean up
            try:
                dash._reset_scan_status()
            except Exception:
                pass

    # Start monitoring with error protection
    try:
        dash.parent.after(1000, check_completion)
    except tk.TclError:
        # UI not available
        pass


# ── Stop / error handlers ─────────────────────────────────────────────────────

def stop_scan_immediate(dash) -> None:
    """Stop scan immediately."""
    dash._update_scan_button_state("stopping")

    try:
        success = dash.scan_manager.interrupt_scan()

        if success:
            # Stop signal sent - stay in "stopping" state
            # Monitor loop will detect when scan actually terminates
            # and transition to "idle" or "retry" as appropriate
            dash._log_status_event("Stop command sent, waiting for scan to terminate...")
        else:
            # Stop failed immediately
            dash._handle_stop_error("Failed to interrupt scan - scan may not be active")

    except Exception as e:
        dash._handle_stop_error(f"Error stopping scan: {str(e)}")


def stop_scan_after_host(dash) -> None:
    """Stop scan after current host completes."""
    # For now, implement as immediate stop with different message
    # Future enhancement: could add graceful stopping to scan manager
    dash._update_scan_button_state("stopping")

    try:
        success = dash.scan_manager.interrupt_scan()

        if success:
            # Stop signal sent - stay in "stopping" state
            # Monitor loop will handle the transition
            dash._log_status_event("Stop command sent, scan will finish current host...")
        else:
            dash._handle_stop_error("Failed to schedule graceful stop")

    except Exception as e:
        dash._handle_stop_error(f"Error scheduling graceful stop: {str(e)}")


def handle_stop_error(dash, error_message: str) -> None:
    """Handle scan stop error."""
    # Double-check actual scan state
    if not dash.scan_manager.is_scanning:
        # Scan actually stopped despite error
        dash._update_scan_button_state("idle")
        _mb().showinfo(
            "Scan Stopped",
            "Scan has stopped (despite error in communication)."
        )
    else:
        # Scan still running, show error state
        dash._update_scan_button_state("error")
        _mb().showerror(
            "Stop Failed",
            f"Failed to stop scan: {error_message}\n\n"
            "Click 'Stop Scan' again to retry."
        )


# ── Public progress API ───────────────────────────────────────────────────────

def start_scan_progress(dash, scan_type: str, countries) -> None:
    """Start displaying scan progress."""
    countries_text = ", ".join(countries) if countries else "global"
    summary = f"Starting {scan_type} scan"
    detail = f"Countries: {countries_text}"
    dash._update_progress_summary(summary, detail)
    dash._log_status_event(f"{summary} for {countries_text}")


def update_scan_progress(dash, percentage, message: str) -> None:
    """Update scan progress display."""
    if percentage is not None:
        summary = f"{percentage:.0f}% complete"
        detail = message if message else None
    else:
        summary = message if message else "Processing..."
        detail = None

    dash._update_progress_summary(summary, detail)

    # Force UI update without triggering window auto-resize
    # Using update() instead of update_idletasks() to prevent geometry recalculation
    try:
        dash.parent.update()
        # Enforce window size after UI update to prevent auto-resizing
        if dash.size_enforcement_callback:
            dash.size_enforcement_callback()
    except tk.TclError:
        # UI may be destroyed, ignore
        pass


def finish_scan_progress(dash, success: bool, results: Dict[str, Any]) -> None:
    """Finish scan progress display."""
    if success:
        successful = results.get("successful_auth", 0)
        total = results.get("hosts_tested", 0)
        summary = f"Scan complete: {successful}/{total} servers accessible"
        dash._update_progress_summary(summary, "Refreshing dashboard...")
        dash._log_status_event(summary)

        # Refresh dashboard with new data (clear cache for fresh Recent Discoveries count)
        dash._schedule_post_scan_refresh(delay_ms=2000)
    else:
        summary = "Scan failed - check backend connection"
        dash._update_progress_summary(summary, None)
        dash._log_status_event(summary)
        dash._schedule_post_scan_refresh(delay_ms=2000)

    # Return to ready state after giving the user time to read the summary
    dash.parent.after(5000, dash._reset_scan_status)
