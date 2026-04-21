"""
Batch operation helpers for DashboardWidget (C8 extraction).

Each function takes the dashboard instance (dash) as first arg and mirrors
the original method behavior from dashboard.py. No UI text or behavior changes.

Intra-class call discipline: calls to other DashboardWidget methods go through
dash._method_name() so instance-level monkeypatches in tests still intercept.

Messagebox calls go through _mb() so module-level patches on
gui.components.dashboard.messagebox still intercept.

All other patch-sensitive symbols (tk, ttk, threading, dispatch_probe_run,
probe_patterns, get_probe_snapshot_path_for_host, create_quarantine_dir,
extract_runner) are resolved at call-time from the dashboard module namespace
via _d(name), so test patches at gui.components.dashboard.* still intercept.

Import discipline:
- threading / tkinter: imported at module level for annotations and
  tk.TclError only. Never instantiate Thread/Event or construct widgets
  directly from these module-level names — always go through _d().
- dispatch_probe_run, probe_patterns, get_probe_snapshot_path_for_host,
  create_quarantine_dir, extract_runner: NOT imported at module level.
  Always resolved via _d() at call-time.
"""

import json
import sys
import threading  # annotations only — use _d("threading") for Thread/Event instantiation
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk  # annotations + tk.TclError only — use _d("tk")/_d("ttk") for widget construction

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.logging_config import get_logger
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog

_logger = get_logger("dashboard")


# ── Patch-safe helpers ────────────────────────────────────────────────────────

def _mb():
    """Return messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox. Calling through this
    helper means the patched object is used at call-time, preserving all
    frozen patch paths.
    Falls back to the real safe_messagebox if dashboard is not yet loaded.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str) -> Any:
    """Resolve a name from gui.components.dashboard at call-time.

    Tests patch gui.components.dashboard.<name>. Using this helper ensures
    the patched binding is used rather than a cached import-time reference.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )


# ── Dialog lifecycle helpers ─────────────────────────────────────────────────

def _safe_destroy_dialog(dialog: Any) -> None:
    """Best-effort dialog teardown with grab release and existence checks."""
    if dialog is None:
        return

    try:
        dialog.grab_release()
    except Exception:
        pass

    try:
        exists = bool(dialog.winfo_exists()) if hasattr(dialog, "winfo_exists") else True
    except Exception:
        exists = True

    if not exists:
        return

    try:
        dialog.destroy()
    except Exception:
        pass


# ── Pure helpers ──────────────────────────────────────────────────────────────

def protocol_label_from_host_type(host_type: Optional[str]) -> str:
    """Map protocol host_type code to user-facing protocol label."""
    code = str(host_type or "").strip().upper()
    return {
        "S": "SMB",
        "F": "FTP",
        "H": "HTTP",
    }.get(code, "Unknown")


# ── Batch orchestration ───────────────────────────────────────────────────────

def run_post_scan_batch_operations(
    dash,
    scan_options: Dict[str, Any],
    scan_results: Dict[str, Any],
    *,
    schedule_reset: bool = True,
    show_dialogs: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run bulk probe/extract operations after scan completion.

    Called when:
    - Scan completes (not cancelled)
    - Scan has success=True OR status in ('completed', 'success', 'failed')
    - current_scan_options is set
    - At least one bulk operation (probe/extract) is enabled

    Will show info dialog if no accessible servers are found.
    """
    summary_payload: Dict[str, List[Dict[str, Any]]] = {
        "probe": [],
        "extract": [],
    }
    try:
        # Check if any bulk operations are enabled
        bulk_probe_enabled = scan_options.get('bulk_probe_enabled', False)
        bulk_extract_enabled = scan_options.get('bulk_extract_enabled', False)

        if not (bulk_probe_enabled or bulk_extract_enabled):
            if show_dialogs:
                dash._show_scan_results(scan_results)
            if schedule_reset:
                try:
                    dash.parent.after(5000, dash._reset_scan_status)
                except tk.TclError:
                    pass
            return summary_payload  # No bulk operations requested

        # Skip bulk if scan failed or produced no hosts (use tolerant metrics)
        host_metric = max(
            scan_results.get("hosts_scanned", 0) or scan_results.get("hosts_tested", 0) or scan_results.get("hosts_discovered", 0) or 0,
            scan_results.get("accessible_hosts", 0) or 0,
            scan_results.get("shares_found", 0) or 0,
        )
        if scan_results.get("error") or host_metric == 0:
            if show_dialogs:
                dash._show_scan_results(scan_results)
            if schedule_reset:
                try:
                    dash.parent.after(5000, dash._reset_scan_status)
                except tk.TclError:
                    pass
            return summary_payload

        # Query database for eligible servers in the active protocol (keep UI responsive)
        scan_protocol = str(scan_results.get("protocol") or "").strip().lower()
        host_type_filter = {
            "ftp": "F",
            "http": "H",
        }.get(scan_protocol, "S")

        def _fetch_servers():
            return dash._get_servers_for_bulk_ops(
                skip_indicator_extract=scan_options.get("bulk_extract_skip_indicators", True),
                host_type_filter=host_type_filter,
                scan_start_time=scan_results.get("start_time"),
                scan_end_time=scan_results.get("end_time"),
            )

        servers_for_ops, fetch_error = dash._run_background_fetch(
            title="Preparing Bulk Operations",
            message="Gathering eligible servers for bulk operations...",
            fetch_fn=_fetch_servers
        )

        if fetch_error:
            if show_dialogs:
                _mb().showerror(
                    "Bulk Operations Error",
                    f"Failed to gather servers for bulk operations:\n{fetch_error}"
                )
                dash._show_scan_results(scan_results)
            else:
                _logger.warning("Bulk operations fetch error (suppressed dialog): %s", fetch_error)
            if schedule_reset:
                try:
                    dash.parent.after(5000, dash._reset_scan_status)
                except tk.TclError:
                    pass
            return summary_payload

        probe_targets = servers_for_ops.get("probe") if isinstance(servers_for_ops, dict) else []
        extract_targets = servers_for_ops.get("extract") if isinstance(servers_for_ops, dict) else []

        if not probe_targets and not extract_targets and (bulk_probe_enabled or bulk_extract_enabled):
            # Show info message only if bulk operations were enabled.
            if show_dialogs:
                _mb().showinfo(
                    "Bulk Operations Skipped",
                    "No eligible servers found for bulk operations.\n\n"
                    "Bulk probe/extract operations require at least one accessible server."
                )
                dash._show_scan_results(scan_results)
            if schedule_reset:
                try:
                    dash.parent.after(5000, dash._reset_scan_status)
                except tk.TclError:
                    pass
            return summary_payload

        # Run batch operations (record summaries per op, show in LIFO order)
        summary_stack: List[Tuple[str, List[Dict[str, Any]]]] = []
        extract_results: List[Dict[str, Any]] = []

        if bulk_probe_enabled:
            probe_results = dash._execute_batch_probe(probe_targets)
            summary_payload["probe"] = list(probe_results)
            summary_stack.append(("probe", probe_results))

        if bulk_extract_enabled:
            if not extract_targets:
                skipped_extract = [{
                    "ip_address": "",
                    "protocol": dash._protocol_label_from_host_type(host_type_filter),
                    "action": "extract",
                    "status": "skipped",
                    "notes": "All accessible hosts were flagged with indicators; extract skipped."
                }]
                summary_payload["extract"] = list(skipped_extract)
                summary_stack.append(("extract", skipped_extract))
            else:
                extract_results = dash._execute_batch_extract(extract_targets)
                summary_payload["extract"] = list(extract_results)
                summary_stack.append(("extract", extract_results))

        # Present summaries in LIFO order
        while summary_stack:
            job_type, results = summary_stack.pop()
            if show_dialogs and results:
                dash._show_batch_summary(results, job_type=job_type)

        if show_dialogs and extract_results:
            _clamav_cfg = dash._load_clamav_config()
            dash._maybe_show_clamav_dialog(extract_results, _clamav_cfg, wait=True, modal=True)

        # After bulk operations, show the deferred scan summary
        if show_dialogs:
            dash._show_scan_results(scan_results)
        if schedule_reset:
            try:
                dash.parent.after(5000, dash._reset_scan_status)
            except tk.TclError:
                pass
        return summary_payload

    except Exception as e:
        if show_dialogs:
            _mb().showerror(
                "Batch Operations Error",
                f"Error running post-scan batch operations: {str(e)}\n\n"
                f"The scan completed successfully but bulk operations encountered an error."
            )
        else:
            _logger.exception("Post-scan batch operations error (suppressed dialog): %s", e)

        # Even on error, fall back to showing the scan summary when dialogs are enabled.
        try:
            if show_dialogs:
                dash._show_scan_results(scan_results)
            if schedule_reset:
                dash.parent.after(5000, dash._reset_scan_status)
        except Exception:
            pass
        return summary_payload


def get_servers_for_bulk_ops(
    dash,
    skip_indicator_extract: bool = True,
    host_type_filter: Optional[str] = None,
    scan_start_time: Optional[str] = None,
    scan_end_time: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Gather servers eligible for bulk probe and extract.

    Probe:
      - Post-scan path: accessible rows from the immediate prior scan only
        (when scan_start_time + scan_end_time are provided)
      - Other callers: recent active rows in selected protocol
    Extract: accessible_shares > 0 AND (no indicators) unless toggle disabled
    """
    result = {"probe": [], "extract": []}
    try:
        if not dash.db_reader:
            return result

        if hasattr(dash.db_reader, "get_protocol_server_list"):
            servers, _ = dash.db_reader.get_protocol_server_list(
                limit=5000,
                offset=0,
                country_filter=None,
                recent_scan_only=True,
            )
        else:
            servers, _ = dash.db_reader.get_server_list(
                limit=5000,
                offset=0,
                country_filter=None,
                recent_scan_only=True,
            )

        scan_cohort_ids: Optional[set] = None
        if (
            host_type_filter in {"S", "F", "H"}
            and scan_start_time
            and scan_end_time
            and hasattr(dash.db_reader, "get_protocol_scan_cohort_server_ids")
        ):
            try:
                scan_cohort_ids = set(
                    dash.db_reader.get_protocol_scan_cohort_server_ids(
                        host_type_filter,
                        scan_start_time,
                        scan_end_time,
                    )
                )
            except Exception as exc:
                _logger.warning(
                    "Scan cohort filter unavailable for protocol %s: %s",
                    host_type_filter,
                    exc,
                )
                # Avoid widening post-scan probe scope on errors.
                scan_cohort_ids = set()

        for server in servers:
            server_host_type = (server.get("host_type") or "S").upper()
            if host_type_filter and server_host_type != host_type_filter:
                continue

            if scan_cohort_ids is not None:
                try:
                    server_id = int(server.get("protocol_server_id"))
                except (TypeError, ValueError):
                    continue
                if server_id not in scan_cohort_ids:
                    continue

            # Probe eligibility remains row-based (not share-count based).
            # Optional scan-cohort filtering above narrows post-scan runs to
            # immediate prior scan results only.
            probe_eligible = True
            if server_host_type == "F":
                anon_accessible = server.get("anon_accessible")
                if isinstance(anon_accessible, str):
                    probe_eligible = anon_accessible.strip().lower() in {
                        "1", "true", "yes", "y", "on"
                    }
                else:
                    probe_eligible = bool(anon_accessible)

            if probe_eligible:
                result["probe"].append(server)

            accessible = (server.get("accessible_shares") or 0) > 0
            if not accessible:
                continue

            indicator_matches = int(server.get("indicator_matches", 0) or 0)
            probe_status = (server.get("probe_status") or "").lower()
            is_issue = probe_status == "issue" or indicator_matches > 0

            if skip_indicator_extract and is_issue:
                continue

            result["extract"].append(server)

    except Exception as e:
        _logger.error("Error querying servers for bulk ops: %s", e)

    return result


def run_background_fetch(
    dash,
    title: str,
    message: str,
    fetch_fn: Callable[[], Any],
) -> tuple:
    """
    Run a blocking fetch function off the UI thread while showing a small modal.

    Returns:
        (result, error_message_or_None)
    """
    result_container = {"result": None, "error": None, "done": False}
    dialog = None
    progress = None

    try:
        dialog = _d("tk").Toplevel(dash.parent)
        dialog.title(title)
        dialog.geometry("380x140")
        dialog.transient(dash.parent)
        dialog.grab_set()
        dash.theme.apply_to_widget(dialog, "main_window")

        label = _d("tk").Label(dialog, text=message)
        label.pack(pady=(20, 10))

        progress = _d("ttk").Progressbar(dialog, mode="indeterminate", length=260)
        progress.pack(pady=(0, 10))
        progress.start(10)

        dialog.update_idletasks()

        # This dialog has no cancel path; keep it modal until fetch completes.
        try:
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        except Exception:
            pass

        def close_dialog():
            try:
                if progress is not None:
                    progress.stop()
            except Exception:
                pass
            _safe_destroy_dialog(dialog)

        def poll_done():
            if result_container["done"]:
                close_dialog()
                return
            try:
                dialog.after(80, poll_done)
            except Exception:
                close_dialog()

        def worker():
            try:
                result_container["result"] = fetch_fn()
            except Exception as exc:  # pragma: no cover - best-effort guard
                result_container["error"] = str(exc)
            finally:
                result_container["done"] = True

        _d("threading").Thread(target=worker, daemon=True).start()
        dialog.after(80, poll_done)
        dash.parent.wait_window(dialog)
    finally:
        _safe_destroy_dialog(dialog)

    return result_container["result"], result_container["error"]


# ── Probe ─────────────────────────────────────────────────────────────────────

def execute_batch_probe(dash, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Execute bulk probe operation on servers."""
    # Load probe settings
    worker_count = int(dash.settings_manager.get_setting('probe.batch_max_workers', 3))
    worker_count = max(1, min(8, worker_count))
    max_dirs = int(dash.settings_manager.get_setting('probe.max_directories_per_share', 3))
    max_files = int(dash.settings_manager.get_setting('probe.max_files_per_directory', 5))
    timeout_seconds = int(dash.settings_manager.get_setting('probe.share_timeout_seconds', 10))
    enable_rce = bool(
        (dash.current_scan_options or {}).get(
            "rce_enabled",
            dash.settings_manager.get_setting('scan_dialog.rce_enabled', False)
        )
    )

    results: List[Dict[str, Any]] = []
    cancel_event = _d("threading").Event()
    done_event = _d("threading").Event()
    progress_dialog = None
    progress_label = None
    progress_bar = None
    task_id: Optional[str] = None

    # Shared state for UI updates from worker
    state = {
        "completed": 0,
        "total": len(servers),
        "results": results,
        "done": False,
        "error": None,
    }

    def cleanup_progress_dialog():
        nonlocal progress_dialog
        dialog = progress_dialog
        if dialog is None:
            return
        _safe_destroy_dialog(dialog)
        if getattr(dash, "_bulk_probe_progress_dialog", None) is dialog:
            setattr(dash, "_bulk_probe_progress_dialog", None)
        progress_dialog = None

    def reopen_monitor_dialog() -> None:
        if progress_dialog is None:
            return
        try:
            progress_dialog.deiconify()
            progress_dialog.lift()
            progress_dialog.focus_force()
        except Exception:
            return

    def request_cancel() -> None:
        cancel_event.set()

    def ui_tick():
        """Periodic UI refresher to keep dialog responsive."""
        if progress_dialog is None:
            return

        if state["done"]:
            if task_id and hasattr(dash, "_remove_running_task"):
                dash._remove_running_task(task_id)
            cleanup_progress_dialog()
            return

        try:
            progress_label.config(text=f"Probing {state['completed']}/{state['total']} servers...")
            progress_bar['value'] = state['completed']
            progress_dialog.update_idletasks()
            if task_id and hasattr(dash, "_update_running_task"):
                dash._update_running_task(
                    task_id,
                    state="running",
                    progress=f"{state['completed']}/{state['total']} targets",
                )
        except tk.TclError:
            if task_id and hasattr(dash, "_remove_running_task"):
                dash._remove_running_task(task_id)
            cleanup_progress_dialog()
            return

        progress_dialog.after(150, ui_tick)

    def worker():
        """Run probes off the UI thread and report completion order.

        Tk calls are intentionally forbidden here; UI teardown runs via ui_tick().
        """
        try:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="probe-batch") as executor:
                future_to_server = {
                    executor.submit(
                        dash._probe_single_server,
                        server,
                        max_dirs,
                        max_files,
                        timeout_seconds,
                        enable_rce,
                        cancel_event
                    ): server for server in servers
                }

                # Consume futures as they complete to avoid head-of-line blocking
                for future in as_completed(future_to_server):
                    server = future_to_server[future]
                    if cancel_event.is_set():
                        break
                    try:
                        result = future.result(timeout=timeout_seconds + 10)
                    except Exception as e:
                        result = {
                            "ip_address": server.get("ip_address"),
                            "protocol": dash._protocol_label_from_host_type(server.get("host_type")),
                            "action": "probe",
                            "status": "failed",
                            "notes": str(e)
                        }
                    results.append(result)
                    state["completed"] = len(results)
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            state["done"] = True
            done_event.set()

    # If a stale dialog survived a prior run, clean it before opening a new one.
    stale_dialog = getattr(dash, "_bulk_probe_progress_dialog", None)
    if stale_dialog is not None:
        _safe_destroy_dialog(stale_dialog)
        if getattr(dash, "_bulk_probe_progress_dialog", None) is stale_dialog:
            setattr(dash, "_bulk_probe_progress_dialog", None)

    try:
        # Create progress dialog quickly, then hand work to background thread.
        progress_dialog = _d("tk").Toplevel(dash.parent)
        setattr(dash, "_bulk_probe_progress_dialog", progress_dialog)

        progress_dialog.title("Bulk Probe Progress")
        progress_dialog.geometry("420x170")
        progress_dialog.transient(dash.parent)
        dash.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = _d("tk").Label(progress_dialog, text=f"Probing 0/{len(servers)} servers...")
        dash.theme.apply_to_widget(progress_label, "label")
        progress_label.pack(pady=(18, 8))

        progress_bar = _d("ttk").Progressbar(
            progress_dialog,
            length=320,
            mode='determinate',
            maximum=len(servers),
            style="SMBSeek.Horizontal.TProgressbar",
        )
        progress_bar.pack(pady=(0, 10))

        cancel_button = _d("tk").Button(progress_dialog, text="Cancel", command=request_cancel)
        dash.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(pady=(0, 10))

        # Window close hides monitor; task continues and can be reopened.
        try:
            progress_dialog.protocol("WM_DELETE_WINDOW", lambda dialog=progress_dialog: dialog.withdraw())
        except Exception:
            pass

        if hasattr(dash, "_register_running_task"):
            task_id = dash._register_running_task(
                task_type="probe",
                name="Post-scan Probe Batch",
                state="running",
                progress=f"0/{len(servers)} targets",
                reopen_callback=reopen_monitor_dialog,
                cancel_callback=request_cancel,
            )

        dash.theme.apply_theme_to_application(progress_dialog)

        # Ensure initial paint before heavy work.
        progress_dialog.update_idletasks()

        # Start background worker and UI tick.
        _d("threading").Thread(target=worker, daemon=True).start()
        progress_dialog.after(150, ui_tick)

        # Block until dialog is closed by ui_tick() completion path.
        dash.parent.wait_window(progress_dialog)
        if not state["done"]:
            done_event.wait(timeout=5.0)
    finally:
        if not state["done"]:
            cancel_event.set()
        if task_id and hasattr(dash, "_remove_running_task"):
            dash._remove_running_task(task_id)
        cleanup_progress_dialog()

    return results


def probe_single_server(
    dash,
    server: Dict[str, Any],
    max_dirs: int,
    max_files: int,
    timeout_seconds: int,
    enable_rce: bool,
    cancel_event: threading.Event,
) -> Dict[str, Any]:
    """Probe a single server (SMB, FTP, or HTTP)."""
    protocol_label = dash._protocol_label_from_host_type(server.get("host_type"))
    if cancel_event.is_set():
        return {
            "ip_address": server.get("ip_address"),
            "protocol": protocol_label,
            "action": "probe",
            "status": "cancelled",
            "notes": "Cancelled"
        }

    ip_address = server.get("ip_address")
    host_type = (server.get("host_type") or "S").upper()
    protocol_label = dash._protocol_label_from_host_type(host_type)

    # FTP probe path
    if host_type == "F":
        try:
            port = int(server.get("port") or 21)
        except Exception:
            port = 21

        try:
            snapshot = _d("dispatch_probe_run")(
                ip_address, host_type,
                max_directories=int(max_dirs),
                max_files=int(max_files),
                timeout_seconds=int(timeout_seconds),
                cancel_event=cancel_event,
                port=port,
            )
            analysis = _d("probe_patterns").attach_indicator_analysis(snapshot, dash.indicator_patterns)
            issue_detected = bool(analysis.get("is_suspicious"))
            status = "issue" if issue_detected else "clean"

            probe_summary = summarize_probe_snapshot(snapshot)
            display_entries = probe_summary["display_entries"]
            accessible_dirs_count = len(display_entries)
            accessible_dirs_list = ",".join(display_entries)
            try:
                if dash.db_reader:
                    snapshot_id = dash.db_reader.upsert_probe_snapshot_for_host(
                        ip_address,
                        "F",
                        snapshot,
                        port=port,
                    )
                    dash.db_reader.upsert_probe_cache_for_host(
                        ip_address,
                        "F",
                        status=status,
                        indicator_matches=len(analysis.get("matches", [])),
                        snapshot_path=None,
                        latest_snapshot_id=snapshot_id,
                        accessible_dirs_count=accessible_dirs_count,
                        accessible_dirs_list=accessible_dirs_list,
                    )
            except Exception:
                pass

            notes: List[str] = [f"{accessible_dirs_count} directorie(s)"]
            if issue_detected:
                notes.append("Indicators detected")

            return {
                "ip_address": ip_address,
                "protocol": protocol_label,
                "action": "probe",
                "status": "success",
                "notes": ", ".join(notes),
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "protocol": protocol_label,
                "action": "probe",
                "status": status,
                "notes": str(e)
            }

    # HTTP probe path
    elif host_type == "H":
        try:
            protocol_server_id = server.get("protocol_server_id")
            try:
                http_port = int(server.get("port")) if server.get("port") is not None else None
            except (TypeError, ValueError):
                http_port = None
            http_scheme = server.get("scheme")
            if dash.db_reader and (http_scheme is None or http_port is None):
                detail = dash.db_reader.get_http_server_detail(
                    ip_address,
                    protocol_server_id=protocol_server_id,
                    port=http_port,
                )
                if http_port is None:
                    try:
                        http_port = int((detail or {}).get("port") or 80)
                    except (TypeError, ValueError):
                        http_port = 80
                if http_scheme is None:
                    http_scheme = (detail or {}).get("scheme") or ("https" if http_port == 443 else "http")
            if http_port is None:
                http_port = 80
            if http_scheme is None:
                http_scheme = "https" if http_port == 443 else "http"
            snapshot = _d("dispatch_probe_run")(
                ip_address, host_type,
                max_directories=int(max_dirs),
                max_files=int(max_files),
                timeout_seconds=int(timeout_seconds),
                cancel_event=cancel_event,
                port=http_port,
                scheme=http_scheme,
                protocol_server_id=protocol_server_id,
                db_reader=dash.db_reader,
            )
            analysis = _d("probe_patterns").attach_indicator_analysis(snapshot, dash.indicator_patterns)
            issue_detected = bool(analysis.get("is_suspicious"))
            status = "issue" if issue_detected else "clean"

            probe_summary = summarize_probe_snapshot(snapshot)
            dir_names = probe_summary["directory_names"]
            display_entries = probe_summary["display_entries"]
            total_files = int(probe_summary["total_file_count"])
            total = len(dir_names) + total_files
            accessible_dirs_count = len(dir_names)
            accessible_dirs_list = ",".join(display_entries)
            try:
                if dash.db_reader:
                    snapshot_id = dash.db_reader.upsert_probe_snapshot_for_host(
                        ip_address,
                        "H",
                        snapshot,
                        protocol_server_id=protocol_server_id,
                        port=http_port,
                    )
                    dash.db_reader.upsert_probe_cache_for_host(
                        ip_address,
                        "H",
                        status=status,
                        indicator_matches=len(analysis.get("matches", [])),
                        snapshot_path=None,
                        latest_snapshot_id=snapshot_id,
                        accessible_dirs_count=accessible_dirs_count,
                        accessible_dirs_list=accessible_dirs_list,
                        accessible_files_count=total_files,
                        protocol_server_id=protocol_server_id,
                        port=http_port,
                    )
            except Exception:
                pass

            notes_h: List[str] = [f"{total} entries"]
            if issue_detected:
                notes_h.append("Indicators detected")

            return {
                "ip_address": ip_address,
                "protocol": protocol_label,
                "action": "probe",
                "status": "success",
                "notes": ", ".join(notes_h),
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "protocol": protocol_label,
                "action": "probe",
                "status": status,
                "notes": str(e)
            }

    # SMB probe path
    raw_shares = server.get("accessible_shares_list") or server.get("accessible_shares") or ""
    shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]

    # Derive credentials from auth method
    auth_method = server.get("auth_method", "")
    username = "" if "anonymous" in auth_method.lower() else "guest"
    password = ""

    try:
        result = _d("dispatch_probe_run")(
            ip_address, host_type,
            max_directories=max_dirs,
            max_files=max_files,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            shares=shares,
            username=username,
            password=password,
            enable_rce=enable_rce,
            allow_empty=True,
            db_reader=dash.db_reader,
        )
        # Attach ransomware indicator analysis (mirror server list behavior)
        analysis = _d("probe_patterns").attach_indicator_analysis(result, dash.indicator_patterns)
        issue_detected = bool(analysis.get("is_suspicious"))

        try:
            if dash.db_reader:
                snapshot_id = dash.db_reader.upsert_probe_snapshot_for_host(
                    ip_address,
                    "S",
                    result,
                )
                dash.db_reader.upsert_probe_cache_for_host(
                    ip_address,
                    "S",
                    status="issue" if issue_detected else "clean",
                    indicator_matches=len(analysis.get("matches", [])),
                    snapshot_path=None,
                    latest_snapshot_id=snapshot_id,
                )
        except Exception:
            pass

        return {
            "ip_address": ip_address,
            "protocol": protocol_label,
            "action": "probe",
            "status": "success",
            "notes": dash._build_probe_notes(len(shares), enable_rce, issue_detected, analysis, result)
        }
    except Exception as e:
        status = "cancelled" if "cancel" in str(e).lower() else "failed"
        return {
            "ip_address": ip_address,
            "protocol": protocol_label,
            "action": "probe",
            "status": status,
            "notes": str(e)
        }


def protocol_label_for_result(dash, result: Dict[str, Any]) -> str:
    """Resolve protocol label from result payload for summary display."""
    explicit = str(result.get("protocol") or "").strip().upper()
    if explicit:
        return explicit
    return dash._protocol_label_from_host_type(result.get("host_type"))


def build_probe_notes(
    dash,
    share_count: int,
    enable_rce: bool,
    issue_detected: bool,
    analysis: Dict[str, Any],
    result: Dict[str, Any],
) -> str:
    notes: List[str] = []
    if share_count:
        notes.append(f"{share_count} share(s)")
    else:
        notes.append("No accessible shares")

    if enable_rce and result.get("rce_analysis"):
        rce_status = result["rce_analysis"].get("rce_status", "not_run")
        notes.append(f"RCE: {rce_status}")
        try:
            dash._handle_rce_status_update(result.get("ip_address") or "", rce_status)
        except Exception:
            pass

    if issue_detected:
        match_count = len(analysis.get("matches", [])) if isinstance(analysis, dict) else 0
        notes.append(f"Indicators detected ({match_count})" if match_count else "Indicators detected")

    return ", ".join(notes) if notes else "Probed"


# ── Extract ───────────────────────────────────────────────────────────────────

def execute_batch_extract(dash, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Execute bulk extract operation on servers."""
    # Load extract settings
    worker_count = int(dash.settings_manager.get_setting('extract.batch_max_workers', 2))
    worker_count = max(1, min(8, worker_count))
    max_file_mb = int(dash.settings_manager.get_setting('extract.max_file_size_mb', 50))
    max_total_mb = int(dash.settings_manager.get_setting('extract.max_total_size_mb', 200))
    max_time = int(dash.settings_manager.get_setting('extract.max_time_seconds', 300))
    max_files = int(dash.settings_manager.get_setting('extract.max_files_per_target', 10))
    extension_mode = str(dash.settings_manager.get_setting('extract.extension_mode', 'allow_only')).lower()

    # Load extension filters from config if available
    included_extensions: List[str] = []
    excluded_extensions: List[str] = []
    quarantine_base_path: Optional[Path] = None
    clamav_cfg: Dict[str, Any] = {}
    http_allow_insecure_tls = True
    config_path = dash.settings_manager.get_setting('backend.config_path', None) if dash.settings_manager else None
    if config_path and Path(config_path).exists():
        try:
            config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            file_cfg = config_data.get("file_collection", {})
            included_extensions = file_cfg.get("included_extensions", []) or []
            excluded_extensions = file_cfg.get("excluded_extensions", []) or []
            quarantine_candidate = (
                config_data.get("file_browser", {}).get("quarantine_root")
                or config_data.get("ftp_browser", {}).get("quarantine_base")
                or config_data.get("http_browser", {}).get("quarantine_base")
                or config_data.get("file_collection", {}).get("quarantine_base")
            )
            if quarantine_candidate:
                quarantine_base_path = Path(str(quarantine_candidate)).expanduser()
            clamav_cfg = config_data.get("clamav", {})
            http_allow_insecure_tls = bool(
                config_data.get("http", {})
                .get("verification", {})
                .get("allow_insecure_tls", True)
            )
        except Exception:
            pass

    results: List[Dict[str, Any]] = []
    cancel_event = _d("threading").Event()
    done_event = _d("threading").Event()
    progress_dialog = None
    progress_label = None
    progress_bar = None
    task_id: Optional[str] = None

    state = {
        "completed": 0,
        "total": len(servers),
        "done": False,
        "error": None,
    }

    def cleanup_progress_dialog():
        nonlocal progress_dialog
        dialog = progress_dialog
        if dialog is None:
            return
        _safe_destroy_dialog(dialog)
        progress_dialog = None

    def reopen_monitor_dialog() -> None:
        if progress_dialog is None:
            return
        try:
            progress_dialog.deiconify()
            progress_dialog.lift()
            progress_dialog.focus_force()
        except Exception:
            return

    def request_cancel() -> None:
        cancel_event.set()

    def ui_tick():
        if progress_dialog is None:
            return
        if state["done"]:
            if task_id and hasattr(dash, "_remove_running_task"):
                dash._remove_running_task(task_id)
            cleanup_progress_dialog()
            return
        try:
            progress_label.config(text=f"Extracting from {state['completed']}/{state['total']} servers...")
            progress_bar['value'] = state['completed']
            progress_dialog.update_idletasks()
            if task_id and hasattr(dash, "_update_running_task"):
                dash._update_running_task(
                    task_id,
                    state="running",
                    progress=f"{state['completed']}/{state['total']} targets",
                )
        except tk.TclError:
            if task_id and hasattr(dash, "_remove_running_task"):
                dash._remove_running_task(task_id)
            cleanup_progress_dialog()
            return
        progress_dialog.after(150, ui_tick)

    def worker():
        try:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="extract-batch") as executor:
                future_to_server = {}
                for server in servers:
                    server_payload = dict(server)
                    server_payload["_http_allow_insecure_tls"] = bool(http_allow_insecure_tls)
                    future = executor.submit(
                        dash._extract_single_server,
                        server_payload,
                        max_file_mb,
                        max_total_mb,
                        max_time,
                        max_files,
                        extension_mode,
                        included_extensions,
                        excluded_extensions,
                        quarantine_base_path,
                        cancel_event,
                        clamav_cfg,
                    )
                    future_to_server[future] = server

                for future in as_completed(future_to_server):
                    server = future_to_server[future]
                    if cancel_event.is_set():
                        break
                    try:
                        result = future.result(timeout=max_time + 30)
                    except Exception as exc:
                        result = {
                            "ip_address": server.get("ip_address"),
                            "protocol": dash._protocol_label_from_host_type(server.get("host_type")),
                            "action": "extract",
                            "status": "failed",
                            "notes": str(exc),
                        }
                    results.append(result)
                    state["completed"] = len(results)
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            state["done"] = True
            done_event.set()

    try:
        progress_dialog = _d("tk").Toplevel(dash.parent)
        progress_dialog.title("Bulk Extract Progress")
        progress_dialog.geometry("420x170")
        progress_dialog.transient(dash.parent)
        dash.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = _d("tk").Label(progress_dialog, text=f"Extracting from 0/{len(servers)} servers...")
        dash.theme.apply_to_widget(progress_label, "label")
        progress_label.pack(pady=(18, 8))

        progress_bar = _d("ttk").Progressbar(
            progress_dialog,
            length=320,
            mode='determinate',
            maximum=len(servers),
            style="SMBSeek.Horizontal.TProgressbar",
        )
        progress_bar.pack(pady=(0, 10))

        cancel_button = _d("tk").Button(progress_dialog, text="Cancel", command=request_cancel)
        dash.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(pady=(0, 10))

        try:
            progress_dialog.protocol("WM_DELETE_WINDOW", lambda dialog=progress_dialog: dialog.withdraw())
        except Exception:
            pass

        if hasattr(dash, "_register_running_task"):
            task_id = dash._register_running_task(
                task_type="extract",
                name="Post-scan Extract Batch",
                state="running",
                progress=f"0/{len(servers)} targets",
                reopen_callback=reopen_monitor_dialog,
                cancel_callback=request_cancel,
            )

        dash.theme.apply_theme_to_application(progress_dialog)
        progress_dialog.update_idletasks()

        _d("threading").Thread(target=worker, daemon=True).start()
        progress_dialog.after(150, ui_tick)
        dash.parent.wait_window(progress_dialog)
        if not state["done"]:
            done_event.wait(timeout=5.0)
    finally:
        if not state["done"]:
            cancel_event.set()
        if task_id and hasattr(dash, "_remove_running_task"):
            dash._remove_running_task(task_id)
        cleanup_progress_dialog()

    return results


def extract_single_server(
    dash,
    server: Dict[str, Any],
    max_file_mb: int,
    max_total_mb: int,
    max_time: int,
    max_files: int,
    extension_mode: str,
    included_extensions: List[str],
    excluded_extensions: List[str],
    quarantine_base_path: Optional[Path],
    cancel_event: threading.Event,
    clamav_config: Optional[Dict[str, Any]] = None,
    http_allow_insecure_tls: bool = True,
) -> Dict[str, Any]:
    """Extract files from a single server."""
    host_type = str(server.get("host_type") or "S").upper()
    protocol_label = dash._protocol_label_from_host_type(host_type)
    allow_insecure_tls = bool(
        server.get("_http_allow_insecure_tls", http_allow_insecure_tls)
    )
    if cancel_event.is_set():
        return {
            "ip_address": server.get("ip_address"),
            "protocol": protocol_label,
            "action": "extract",
            "status": "cancelled",
            "notes": "Cancelled"
        }

    ip_address = server.get("ip_address")

    # Create quarantine directory
    try:
        quarantine_dir = _d("create_quarantine_dir")(
            ip_address,
            purpose="post-scan-extract",
            base_path=quarantine_base_path,
        )
    except Exception as e:
        return {
            "ip_address": ip_address,
            "protocol": protocol_label,
            "action": "extract",
            "status": "failed",
            "notes": f"Quarantine error: {e}"
        }

    ftp_port: Optional[int] = None
    http_port: Optional[int] = None
    protocol_server_id = server.get("protocol_server_id")

    try:
        if host_type == "F":
            try:
                ftp_port = int(server.get("port")) if server.get("port") is not None else 21
            except (TypeError, ValueError):
                ftp_port = 21
            summary = _d("protocol_extract_runner").run_ftp_extract(
                ip_address,
                port=ftp_port,
                download_dir=quarantine_dir,
                max_total_bytes=max_total_mb * 1024 * 1024,
                max_file_bytes=max_file_mb * 1024 * 1024,
                max_file_count=max_files,
                max_seconds=max_time,
                max_depth=3,
                allowed_extensions=included_extensions,
                denied_extensions=excluded_extensions,
                delay_seconds=0,
                connection_timeout=30,
                extension_mode=extension_mode,
                progress_callback=None,
                cancel_event=cancel_event,
                clamav_config=clamav_config,
            )
        elif host_type == "H":
            try:
                http_port = int(server.get("port")) if server.get("port") is not None else None
            except (TypeError, ValueError):
                http_port = None

            http_scheme = server.get("scheme")
            request_host = server.get("probe_host")
            start_path = server.get("probe_path")

            if dash.db_reader and (
                http_scheme is None
                or http_port is None
                or request_host is None
                or start_path is None
            ):
                detail = dash.db_reader.get_http_server_detail(
                    ip_address,
                    protocol_server_id=protocol_server_id,
                    port=http_port,
                )
                if detail:
                    if http_port is None:
                        try:
                            http_port = int(detail.get("port") or 80)
                        except (TypeError, ValueError):
                            http_port = 80
                    if http_scheme is None:
                        http_scheme = detail.get("scheme")
                    if request_host is None:
                        request_host = detail.get("probe_host")
                    if start_path is None:
                        start_path = detail.get("probe_path")

            if http_port is None:
                http_port = 80
            if not isinstance(http_scheme, str) or http_scheme.strip().lower() not in {"http", "https"}:
                http_scheme = "https" if http_port == 443 else "http"
            else:
                http_scheme = http_scheme.strip().lower()

            request_host_norm = str(request_host or "").strip() or None
            start_path_norm = str(start_path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
            if not start_path_norm.startswith("/"):
                start_path_norm = "/" + start_path_norm.lstrip("/")

            summary = _d("protocol_extract_runner").run_http_extract(
                ip_address,
                port=http_port,
                scheme=http_scheme,
                request_host=request_host_norm,
                start_path=start_path_norm,
                allow_insecure_tls=allow_insecure_tls,
                download_dir=quarantine_dir,
                max_total_bytes=max_total_mb * 1024 * 1024,
                max_file_bytes=max_file_mb * 1024 * 1024,
                max_file_count=max_files,
                max_seconds=max_time,
                max_depth=3,
                allowed_extensions=included_extensions,
                denied_extensions=excluded_extensions,
                delay_seconds=0,
                connection_timeout=30,
                extension_mode=extension_mode,
                progress_callback=None,
                cancel_event=cancel_event,
                clamav_config=clamav_config,
            )
        else:
            raw_shares = server.get("accessible_shares_list") or server.get("accessible_shares") or ""
            shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]
            if not shares:
                return {
                    "ip_address": ip_address,
                    "protocol": protocol_label,
                    "action": "extract",
                    "status": "skipped",
                    "notes": "No accessible shares"
                }

            # Derive credentials
            auth_method = server.get("auth_method", "")
            username = "" if "anonymous" in auth_method.lower() else "guest"
            password = ""

            summary = _d("extract_runner").run_extract(
                ip_address,
                shares,
                download_dir=quarantine_dir,
                username=username,
                password=password,
                max_total_bytes=max_total_mb * 1024 * 1024,
                max_file_bytes=max_file_mb * 1024 * 1024,
                max_file_count=max_files,
                max_seconds=max_time,
                max_depth=3,
                allowed_extensions=included_extensions,
                denied_extensions=excluded_extensions,
                delay_seconds=0,
                connection_timeout=30,
                extension_mode=extension_mode,
                progress_callback=None,
                cancel_event=cancel_event,
                clamav_config=clamav_config,
            )

        files = summary["totals"].get("files_downloaded", 0)
        bytes_downloaded = summary["totals"].get("bytes_downloaded", 0)
        size_mb = bytes_downloaded / (1024 * 1024) if bytes_downloaded else 0

        # Mark host as extracted (one-way flag)
        try:
            if dash.db_reader:
                if hasattr(dash.db_reader, "upsert_extracted_flag_for_host"):
                    kwargs: Dict[str, Any] = {}
                    if protocol_server_id is not None:
                        kwargs["protocol_server_id"] = protocol_server_id
                    if host_type == "F" and ftp_port is not None:
                        kwargs["port"] = ftp_port
                    if host_type == "H" and http_port is not None:
                        kwargs["port"] = http_port
                    dash.db_reader.upsert_extracted_flag_for_host(
                        ip_address,
                        host_type,
                        True,
                        **kwargs,
                    )
                else:
                    dash.db_reader.upsert_extracted_flag(ip_address, True)
        except Exception:
            pass

        return {
            "ip_address": ip_address,
            "protocol": protocol_label,
            "action": "extract",
            "status": "success",
            "notes": f"{files} file(s), {size_mb:.1f} MB",
            "clamav": summary.get("clamav", {"enabled": False}),
        }
    except Exception as e:
        status = "cancelled" if "cancel" in str(e).lower() else "failed"
        return {
            "ip_address": ip_address,
            "protocol": protocol_label,
            "action": "extract",
            "status": status,
            "notes": str(e)
        }


# ── Summary / ClamAV / Results ────────────────────────────────────────────────

def show_batch_summary(
    dash,
    results: List[Dict[str, Any]],
    job_type: Optional[str] = None,
) -> None:
    """Show summary dialog for batch operations."""
    normalized_results: List[Dict[str, Any]] = []
    for row in results:
        normalized = dict(row)
        normalized["protocol"] = dash._protocol_label_for_result(normalized)
        normalized_results.append(normalized)

    show_batch_summary_dialog(
        parent=dash.parent,
        theme=dash.theme,
        job_type=job_type or "batch",
        results=normalized_results,
        title_suffix="Batch Summary",
        geometry="780x400",
        show_export=True,
        show_protocol=True,
        show_stats=False,
        wait=True,
        modal=True,
    )


def load_clamav_config(dash) -> Dict[str, Any]:
    """Read the clamav section from conf/config.json. Returns {} on any error."""
    config_path = dash.settings_manager.get_setting('backend.config_path', None) if dash.settings_manager else None
    if not config_path:
        return {}
    try:
        config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return config_data.get("clamav", {})
    except Exception:
        return {}


def maybe_show_clamav_dialog(
    dash,
    results: List[Dict[str, Any]],
    clamav_cfg: Dict[str, Any],
    *,
    wait: bool = False,
    modal: bool = False,
) -> None:
    """Show ClamAV results dialog if conditions are met. Fail-safe."""
    try:
        from gui.components.clamav_results_dialog import (
            should_show_clamav_dialog,
            show_clamav_results_dialog,
        )
        from gui.utils import session_flags
        if should_show_clamav_dialog("extract", results, clamav_cfg):
            def _mute() -> None:
                session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)
            show_clamav_results_dialog(
                parent=dash.parent,
                theme=dash.theme,
                results=results,
                on_mute=_mute,
                wait=wait,
                modal=modal,
            )
    except Exception:
        pass


def show_scan_results(dash, results: Dict[str, Any]) -> None:
    """Show scan results dialog."""
    try:
        show_scan_results_dialog(
            parent=dash.parent,
            scan_results=results
        )

    except Exception as e:
        # Fallback to simple message box if results dialog fails
        status = results.get("status", "unknown")
        hosts_scanned = results.get("hosts_scanned", 0)
        accessible_hosts = results.get("accessible_hosts", 0)

        fallback_message = (
            f"Scan completed with status: {status}\n\n"
            f"Results:\n"
            f"• Hosts scanned: {hosts_scanned}\n"
            f"• Accessible hosts: {accessible_hosts}\n\n"
            f"Note: Full results dialog could not be displayed due to error:\n{str(e)}"
        )

        _mb().showinfo("Scan Results", fallback_message)
