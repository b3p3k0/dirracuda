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
import time
import tkinter as tk
from datetime import datetime
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
    """
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

    # Hidden RCE controls are session-gated; force runtime off when locked.
    if not bool(getattr(dash, "_rce_unlocked", True)):
        scan_request = dict(scan_request or {})
        scan_request["rce_enabled"] = False

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
    rce_enabled = bool(common_options.get("rce_enabled", False))

    def _coerce_budget(value: Any, default: int = 1) -> int:
        try:
            budget = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, budget)

    smb_budget = _coerce_budget(common_options.get("smb_max_query_credits_per_scan"), 1)
    ftp_budget = _coerce_budget(common_options.get("ftp_max_query_credits_per_scan"), 1)
    http_budget = _coerce_budget(common_options.get("http_max_query_credits_per_scan"), 1)

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
            "max_shodan_results": smb_budget * 100,
            "discovery_max_concurrent_hosts": shared_concurrency,
            "access_max_concurrent_hosts": shared_concurrency,
            "connection_timeout": shared_timeout,
            "security_mode": security_mode,
            "verbose": verbose,
            "rce_enabled": rce_enabled,
            "bulk_probe_enabled": bulk_probe,
            "bulk_extract_enabled": bulk_extract,
            "bulk_extract_skip_indicators": skip_indicator_extract,
            "smb_max_query_credits_per_scan": smb_budget,
            "ftp_max_query_credits_per_scan": ftp_budget,
            "http_max_query_credits_per_scan": http_budget,
        }

    if protocol == "ftp":
        return {
            "country": country,
            "max_shodan_results": ftp_budget * 100,
            "discovery_max_concurrent_hosts": shared_concurrency,
            "access_max_concurrent_hosts": shared_concurrency,
            "connect_timeout": shared_timeout,
            "auth_timeout": shared_timeout,
            "listing_timeout": shared_timeout,
            "verbose": verbose,
            "rce_enabled": rce_enabled,
            "bulk_probe_enabled": bulk_probe,
            "bulk_extract_enabled": bulk_extract,
            "bulk_extract_skip_indicators": skip_indicator_extract,
            "smb_max_query_credits_per_scan": smb_budget,
            "ftp_max_query_credits_per_scan": ftp_budget,
            "http_max_query_credits_per_scan": http_budget,
        }

    # HTTP
    allow_insecure_tls = bool(common_options.get("allow_insecure_tls", True))
    return {
        "country": country,
        "max_shodan_results": http_budget * 100,
        "discovery_max_concurrent_hosts": shared_concurrency,
        "access_max_concurrent_hosts": shared_concurrency,
        "connect_timeout": shared_timeout,
        "request_timeout": shared_timeout,
        "subdir_timeout": shared_timeout,
        "verify_http": True,
        "verify_https": True,
        "allow_insecure_tls": allow_insecure_tls,
        "verbose": verbose,
        "rce_enabled": rce_enabled,
        "bulk_probe_enabled": bulk_probe,
        "bulk_extract_enabled": bulk_extract,
        "bulk_extract_skip_indicators": skip_indicator_extract,
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
