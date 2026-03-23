"""
DashboardWidget bulk-operations mixin.

Extracted from dashboard.py to keep that module's line count manageable.
Provides post-scan probe/extract orchestration as a private mixin class consumed
only by DashboardWidget.  Do not import or instantiate directly.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import json
from typing import Dict, List, Any, Optional, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gui.utils import (
    probe_cache,
    probe_patterns,
    probe_runner,
    extract_runner,
    ftp_probe_runner,
    ftp_probe_cache,
    http_probe_runner,
    http_probe_cache,
)
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir

_logger = get_logger("dashboard")


class _DashboardBulkOpsMixin:
    """
    Private mixin providing post-scan bulk probe/extract methods for DashboardWidget.

    Relies on the following attributes being set by DashboardWidget.__init__:
        self.parent               - tk root / parent widget
        self.db_reader            - DatabaseReader instance (may be None)
        self.theme                - theme object with apply_to_widget()
        self.settings_manager     - SettingsManager instance
        self.current_scan_options - dict of active scan options (may be None)
        self.config_path          - path to SMBSeek config.json (may be None)
        self.ransomware_indicators - list, populated by _load_indicator_patterns
        self.indicator_patterns    - list, populated by _load_indicator_patterns
    """

    # ------------------------------------------------------------------ #
    # Post-scan bulk operations                                            #
    # ------------------------------------------------------------------ #

    def _run_post_scan_batch_operations(
        self,
        scan_options: Dict[str, Any],
        scan_results: Dict[str, Any],
        *,
        schedule_reset: bool = True,
        show_dialogs: bool = True,
    ) -> None:
        """Run bulk probe/extract operations after scan completion.

        Called when:
        - Scan completes (not cancelled)
        - Scan has success=True OR status in ('completed', 'success', 'failed')
        - current_scan_options is set
        - At least one bulk operation (probe/extract) is enabled

        Will show info dialog if no accessible servers are found.
        """
        try:
            # Check if any bulk operations are enabled
            bulk_probe_enabled = scan_options.get('bulk_probe_enabled', False)
            bulk_extract_enabled = scan_options.get('bulk_extract_enabled', False)

            if not (bulk_probe_enabled or bulk_extract_enabled):
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return  # No bulk operations requested

            # Skip bulk if scan failed or produced no hosts (use tolerant metrics)
            host_metric = max(
                scan_results.get("hosts_scanned", 0) or scan_results.get("hosts_tested", 0) or scan_results.get("hosts_discovered", 0) or 0,
                scan_results.get("accessible_hosts", 0) or 0,
                scan_results.get("shares_found", 0) or 0,
            )
            if scan_results.get("error") or host_metric == 0:
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return

            # Query database for eligible servers in the active protocol (keep UI responsive)
            scan_protocol = str(scan_results.get("protocol") or "").strip().lower()
            host_type_filter = {
                "ftp": "F",
                "http": "H",
            }.get(scan_protocol, "S")

            def _fetch_servers():
                return self._get_servers_for_bulk_ops(
                    skip_indicator_extract=scan_options.get("bulk_extract_skip_indicators", True),
                    host_type_filter=host_type_filter,
                )

            servers_for_ops, fetch_error = self._run_background_fetch(
                title="Preparing Bulk Operations",
                message="Gathering eligible servers for bulk operations...",
                fetch_fn=_fetch_servers
            )

            if fetch_error:
                if show_dialogs:
                    messagebox.showerror(
                        "Bulk Operations Error",
                        f"Failed to gather servers for bulk operations:\n{fetch_error}"
                    )
                    self._show_scan_results(scan_results)
                else:
                    _logger.warning("Bulk operations fetch error (suppressed dialog): %s", fetch_error)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return

            probe_targets = servers_for_ops.get("probe") if isinstance(servers_for_ops, dict) else []
            extract_targets = servers_for_ops.get("extract") if isinstance(servers_for_ops, dict) else []

            if not probe_targets and not extract_targets and (bulk_probe_enabled or bulk_extract_enabled):
                # Show info message only if bulk operations were enabled.
                if show_dialogs:
                    messagebox.showinfo(
                        "Bulk Operations Skipped",
                        "No eligible servers found for bulk operations.\n\n"
                        "Bulk probe/extract operations require at least one accessible server."
                    )
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return

            # Run batch operations (record summaries per op, show in LIFO order)
            summary_stack: List[Tuple[str, List[Dict[str, Any]]]] = []

            if bulk_probe_enabled:
                probe_results = self._execute_batch_probe(probe_targets)
                summary_stack.append(("probe", probe_results))

            if bulk_extract_enabled:
                if not extract_targets:
                    summary_stack.append(("extract", [{
                        "ip_address": "",
                        "action": "extract",
                        "status": "skipped",
                        "notes": "All accessible hosts were flagged with indicators; extract skipped."
                    }]))
                else:
                    extract_results = self._execute_batch_extract(extract_targets)
                    summary_stack.append(("extract", extract_results))

            # Present summaries in LIFO order
            while summary_stack:
                job_type, results = summary_stack.pop()
                if show_dialogs and results:
                    self._show_batch_summary(results, job_type=job_type)

            # After bulk operations, show the deferred scan summary
            if show_dialogs:
                self._show_scan_results(scan_results)
            if schedule_reset:
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass

        except Exception as e:
            if show_dialogs:
                messagebox.showerror(
                    "Batch Operations Error",
                    f"Error running post-scan batch operations: {str(e)}\n\n"
                    f"The scan completed successfully but bulk operations encountered an error."
                )
            else:
                _logger.exception("Post-scan batch operations error (suppressed dialog): %s", e)

            # Even on error, fall back to showing the scan summary when dialogs are enabled.
            try:
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    self.parent.after(5000, self._reset_scan_status)
            except Exception:
                pass

    def _get_servers_for_bulk_ops(
        self,
        skip_indicator_extract: bool = True,
        host_type_filter: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gather servers eligible for bulk probe and extract.

        Probe: any recent active row in the selected protocol
        Extract: accessible_shares > 0 AND (no indicators) unless toggle disabled
        """
        result = {"probe": [], "extract": []}
        try:
            if not self.db_reader:
                return result

            if hasattr(self.db_reader, "get_protocol_server_list"):
                servers, _ = self.db_reader.get_protocol_server_list(
                    limit=5000,
                    offset=0,
                    country_filter=None,
                    recent_scan_only=True,
                )
            else:
                servers, _ = self.db_reader.get_server_list(
                    limit=5000,
                    offset=0,
                    country_filter=None,
                    recent_scan_only=True,
                )

            for server in servers:
                server_host_type = (server.get("host_type") or "S").upper()
                if host_type_filter and server_host_type != host_type_filter:
                    continue

                # Probe eligibility is protocol-row based (recent + active), not
                # share-count based. HTTP/FTP rows may legitimately have zero
                # accessible_shares prior to first probe-cache sync.
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

    def _load_indicator_patterns(self) -> None:
        """Load ransomware indicator patterns from SMBSeek config."""
        config_path = self.config_path
        if not config_path and self.settings_manager:
            config_path = self.settings_manager.get_setting('backend.config_path', None)
            if not config_path:
                try:
                    config_path = self.settings_manager.get_smbseek_config_path()
                except Exception:
                    config_path = None
        self.ransomware_indicators = probe_patterns.load_ransomware_indicators(config_path)
        self.indicator_patterns = probe_patterns.compile_indicator_patterns(self.ransomware_indicators)

    def _run_background_fetch(self, title: str, message: str, fetch_fn: Callable[[], Any]) -> tuple[Any, Optional[str]]:
        """
        Run a blocking fetch function off the UI thread while showing a small modal.

        Returns:
            (result, error_message_or_None)
        """
        result_container = {"result": None, "error": None, "done": False}

        dialog = tk.Toplevel(self.parent)
        dialog.title(title)
        dialog.geometry("380x140")
        dialog.transient(self.parent)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")

        label = tk.Label(dialog, text=message)
        label.pack(pady=(20, 10))

        progress = ttk.Progressbar(dialog, mode="indeterminate", length=260)
        progress.pack(pady=(0, 10))
        progress.start(10)

        dialog.update_idletasks()

        def worker():
            try:
                result_container["result"] = fetch_fn()
            except Exception as exc:  # pragma: no cover - best-effort guard
                result_container["error"] = str(exc)
            finally:
                result_container["done"] = True
                try:
                    dialog.after(0, dialog.destroy)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self.parent.wait_window(dialog)
        return result_container["result"], result_container["error"]

    def _execute_batch_probe(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute bulk probe operation on servers."""
        # Load probe settings
        worker_count = int(self.settings_manager.get_setting('probe.batch_max_workers', 3))
        worker_count = max(1, min(8, worker_count))
        max_dirs = int(self.settings_manager.get_setting('probe.max_directories_per_share', 3))
        max_files = int(self.settings_manager.get_setting('probe.max_files_per_directory', 5))
        timeout_seconds = int(self.settings_manager.get_setting('probe.share_timeout_seconds', 10))
        enable_rce = bool(
            (self.current_scan_options or {}).get(
                "rce_enabled",
                self.settings_manager.get_setting('scan_dialog.rce_enabled', False)
            )
        )

        results: List[Dict[str, Any]] = []
        cancel_event = threading.Event()

        # Create progress dialog quickly, then hand work to background thread
        progress_dialog = tk.Toplevel(self.parent)
        progress_dialog.title("Bulk Probe Progress")
        progress_dialog.geometry("420x170")
        progress_dialog.transient(self.parent)
        progress_dialog.grab_set()
        self.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = tk.Label(progress_dialog, text=f"Probing 0/{len(servers)} servers...")
        progress_label.pack(pady=(18, 8))

        progress_bar = ttk.Progressbar(progress_dialog, length=320, mode='determinate', maximum=len(servers))
        progress_bar.pack(pady=(0, 10))

        cancel_button = tk.Button(progress_dialog, text="Cancel", command=lambda: cancel_event.set())
        cancel_button.pack(pady=(0, 10))

        # Ensure initial paint before heavy work
        progress_dialog.update_idletasks()

        # Shared state for UI updates from worker
        state = {
            "completed": 0,
            "total": len(servers),
            "results": results,
            "done": False,
            "error": None
        }

        def ui_tick():
            """Periodic UI refresher to keep dialog responsive."""
            try:
                progress_label.config(text=f"Probing {state['completed']}/{state['total']} servers...")
                progress_bar['value'] = state['completed']
                progress_dialog.update_idletasks()
            except tk.TclError:
                return  # Dialog closed

            if not state["done"]:
                progress_dialog.after(150, ui_tick)

        def worker():
            """Run probes off the UI thread and report completion order."""
            try:
                with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="probe-batch") as executor:
                    future_to_server = {
                        executor.submit(
                            self._probe_single_server,
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
                try:
                    progress_dialog.after(0, progress_dialog.destroy)
                except Exception:
                    pass

        # Start background worker and UI tick
        threading.Thread(target=worker, daemon=True).start()
        progress_dialog.after(150, ui_tick)

        # Block until dialog destroyed (worker sets done then destroys dialog)
        self.parent.wait_window(progress_dialog)
        return results

    def _probe_single_server(self, server: Dict[str, Any], max_dirs: int, max_files: int,
                              timeout_seconds: int, enable_rce: bool, cancel_event: threading.Event) -> Dict[str, Any]:
        """Probe a single server (SMB or FTP)."""
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "action": "probe",
                "status": "cancelled",
                "notes": "Cancelled"
            }

        ip_address = server.get("ip_address")
        host_type = (server.get("host_type") or "S").upper()

        # FTP probe path
        if host_type == "F":
            try:
                port = int(server.get("port") or 21)
            except Exception:
                port = 21

            max_entries = max(1, int(max_dirs) * int(max_files))
            try:
                snapshot = ftp_probe_runner.run_ftp_probe(
                    ip_address,
                    port=port,
                    max_entries=max_entries,
                    max_directories=int(max_dirs),
                    max_files=int(max_files),
                    connect_timeout=int(timeout_seconds),
                    request_timeout=int(timeout_seconds),
                    cancel_event=cancel_event,
                )
                analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
                issue_detected = bool(analysis.get("is_suspicious"))
                status = "issue" if issue_detected else "clean"

                shares = snapshot.get("shares", [])
                first_share = shares[0] if shares else {}
                dir_names = [
                    d.get("name")
                    for d in first_share.get("directories", [])
                    if isinstance(d, dict) and d.get("name")
                ]
                accessible_dirs_count = len(dir_names)
                accessible_dirs_list = ",".join(dir_names)
                snapshot_path = str(ftp_probe_cache.get_ftp_cache_path(ip_address))

                try:
                    if self.db_reader:
                        self.db_reader.upsert_probe_cache_for_host(
                            ip_address,
                            "F",
                            status=status,
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
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
                    "action": "probe",
                    "status": "success",
                    "notes": ", ".join(notes),
                }
            except Exception as e:
                status = "cancelled" if "cancel" in str(e).lower() else "failed"
                return {
                    "ip_address": ip_address,
                    "action": "probe",
                    "status": status,
                    "notes": str(e)
                }

        # HTTP probe path
        elif host_type == "H":
            try:
                detail = self.db_reader.get_http_server_detail(ip_address) if self.db_reader else None
                port = int((detail or {}).get("port") or 80)
                scheme = (detail or {}).get("scheme") or "http"
                max_entries = max(1, int(max_dirs) * int(max_files))
                snapshot = http_probe_runner.run_http_probe(
                    ip_address,
                    port=port,
                    scheme=scheme,
                    allow_insecure_tls=True,
                    max_entries=max_entries,
                    max_directories=int(max_dirs),
                    max_files=int(max_files),
                    connect_timeout=int(timeout_seconds),
                    request_timeout=int(timeout_seconds),
                    cancel_event=cancel_event,
                )
                analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
                issue_detected = bool(analysis.get("is_suspicious"))
                status = "issue" if issue_detected else "clean"

                shares = snapshot.get("shares", [])
                first_share = shares[0] if shares else {}
                dir_names = [
                    d.get("name")
                    for d in first_share.get("directories", [])
                    if isinstance(d, dict) and d.get("name")
                ]
                root_files = first_share.get("root_files", [])
                total_files = len(root_files) + sum(
                    len(d.get("files", [])) for d in first_share.get("directories", [])
                    if isinstance(d, dict)
                )
                total = len(dir_names) + total_files
                accessible_dirs_count = len(dir_names)
                accessible_dirs_list = ",".join(dir_names)
                snapshot_path = str(http_probe_cache.get_http_cache_path(ip_address))

                try:
                    if self.db_reader:
                        self.db_reader.upsert_probe_cache_for_host(
                            ip_address,
                            "H",
                            status=status,
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
                            accessible_dirs_count=accessible_dirs_count,
                            accessible_dirs_list=accessible_dirs_list,
                            accessible_files_count=total_files,
                        )
                except Exception:
                    pass

                notes_h: List[str] = [f"{total} entries"]
                if issue_detected:
                    notes_h.append("Indicators detected")

                return {
                    "ip_address": ip_address,
                    "action": "probe",
                    "status": "success",
                    "notes": ", ".join(notes_h),
                }
            except Exception as e:
                status = "cancelled" if "cancel" in str(e).lower() else "failed"
                return {
                    "ip_address": ip_address,
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
            result = probe_runner.run_probe(
                ip_address,
                shares,
                max_directories=max_dirs,
                max_files=max_files,
                timeout_seconds=timeout_seconds,
                username=username,
                password=password,
                enable_rce_analysis=enable_rce,
                cancel_event=cancel_event,
                allow_empty=True,
                db_accessor=self.db_reader,
            )
            # Persist probe snapshot to disk and DB (align with server list workflow)
            probe_cache.save_probe_result(ip_address, result)
            snapshot_path = None
            try:
                if hasattr(probe_cache, "get_probe_result_path"):
                    snapshot_path = probe_cache.get_probe_result_path(ip_address)
            except Exception:
                snapshot_path = None

            # Attach ransomware indicator analysis (mirror server list behavior)
            analysis = probe_patterns.attach_indicator_analysis(result, self.indicator_patterns)
            issue_detected = bool(analysis.get("is_suspicious"))

            try:
                if self.db_reader:
                    self.db_reader.upsert_probe_cache_for_host(
                        ip_address,
                        "S",
                        status="issue" if issue_detected else "clean",
                        indicator_matches=len(analysis.get("matches", [])),
                        snapshot_path=snapshot_path
                    )
            except Exception:
                pass

            return {
                "ip_address": ip_address,
                "action": "probe",
                "status": "success",
                "notes": self._build_probe_notes(len(shares), enable_rce, issue_detected, analysis, result)
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "probe",
                "status": status,
                "notes": str(e)
            }

    def _build_probe_notes(self, share_count: int, enable_rce: bool, issue_detected: bool, analysis: Dict[str, Any], result: Dict[str, Any]) -> str:
        notes: List[str] = []
        if share_count:
            notes.append(f"{share_count} share(s)")
        else:
            notes.append("No accessible shares")

        if enable_rce and result.get("rce_analysis"):
            rce_status = result["rce_analysis"].get("rce_status", "not_run")
            notes.append(f"RCE: {rce_status}")
            try:
                self._handle_rce_status_update(result.get("ip_address") or "", rce_status)
            except Exception:
                pass

        if issue_detected:
            match_count = len(analysis.get("matches", [])) if isinstance(analysis, dict) else 0
            notes.append(f"Indicators detected ({match_count})" if match_count else "Indicators detected")

        return ", ".join(notes) if notes else "Probed"

    def _execute_batch_extract(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute bulk extract operation on servers."""
        # Load extract settings
        worker_count = int(self.settings_manager.get_setting('extract.batch_max_workers', 2))
        worker_count = max(1, min(8, worker_count))
        max_file_mb = int(self.settings_manager.get_setting('extract.max_file_size_mb', 50))
        max_total_mb = int(self.settings_manager.get_setting('extract.max_total_size_mb', 200))
        max_time = int(self.settings_manager.get_setting('extract.max_time_seconds', 300))
        max_files = int(self.settings_manager.get_setting('extract.max_files_per_target', 10))
        extension_mode = str(self.settings_manager.get_setting('extract.extension_mode', 'allow_only')).lower()

        # Load extension filters from config if available
        included_extensions: List[str] = []
        excluded_extensions: List[str] = []
        quarantine_base_path: Optional[Path] = None
        config_path = self.settings_manager.get_setting('backend.config_path', None) if self.settings_manager else None
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
            except Exception:
                pass

        results = []
        cancel_event = threading.Event()

        # Create progress dialog
        progress_dialog = tk.Toplevel(self.parent)
        progress_dialog.title("Bulk Extract Progress")
        progress_dialog.geometry("400x150")
        progress_dialog.transient(self.parent)
        progress_dialog.grab_set()
        self.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = tk.Label(progress_dialog, text=f"Extracting from 0/{len(servers)} servers...")
        progress_label.pack(pady=20)

        progress_bar = ttk.Progressbar(progress_dialog, length=300, mode='determinate', maximum=len(servers))
        progress_bar.pack(pady=10)

        cancel_button = tk.Button(progress_dialog, text="Cancel", command=lambda: cancel_event.set())
        cancel_button.pack(pady=10)

        def update_progress(completed_count):
            progress_label.config(text=f"Extracting from {completed_count}/{len(servers)} servers...")
            progress_bar['value'] = completed_count
            progress_dialog.update()

        # Run extract operations with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="extract-batch") as executor:
            futures = []
            for server in servers:
                future = executor.submit(
                    self._extract_single_server,
                    server,
                    max_file_mb,
                    max_total_mb,
                    max_time,
                    max_files,
                    extension_mode,
                    included_extensions,
                    excluded_extensions,
                    quarantine_base_path,
                    cancel_event
                )
                futures.append((server, future))

            for server, future in futures:
                if cancel_event.is_set():
                    break
                try:
                    result = future.result(timeout=max_time + 30)
                    results.append(result)
                except Exception as e:
                    results.append({
                        "ip_address": server.get("ip_address"),
                        "action": "extract",
                        "status": "failed",
                        "notes": str(e)
                    })
                update_progress(len(results))

        progress_dialog.destroy()
        return results

    def _extract_single_server(self, server: Dict[str, Any], max_file_mb: int, max_total_mb: int,
                                 max_time: int, max_files: int, extension_mode: str,
                                 included_extensions: List[str], excluded_extensions: List[str],
                                 quarantine_base_path: Optional[Path],
                                 cancel_event: threading.Event) -> Dict[str, Any]:
        """Extract files from a single server."""
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "action": "extract",
                "status": "cancelled",
                "notes": "Cancelled"
            }

        ip_address = server.get("ip_address")
        raw_shares = server.get("accessible_shares_list") or server.get("accessible_shares") or ""
        shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]

        if not shares:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "skipped",
                "notes": "No accessible shares"
            }

        # Create quarantine directory
        try:
            quarantine_dir = create_quarantine_dir(
                ip_address,
                purpose="post-scan-extract",
                base_path=quarantine_base_path,
            )
        except Exception as e:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "failed",
                "notes": f"Quarantine error: {e}"
            }

        # Derive credentials
        auth_method = server.get("auth_method", "")
        username = "" if "anonymous" in auth_method.lower() else "guest"
        password = ""

        try:
            summary = extract_runner.run_extract(
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
                cancel_event=cancel_event
            )

            files = summary["totals"].get("files_downloaded", 0)
            bytes_downloaded = summary["totals"].get("bytes_downloaded", 0)
            size_mb = bytes_downloaded / (1024 * 1024) if bytes_downloaded else 0

            # Mark host as extracted (one-way flag)
            try:
                if self.db_reader:
                    self.db_reader.upsert_extracted_flag(ip_address, True)
            except Exception:
                pass

            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "success",
                "notes": f"{files} file(s), {size_mb:.1f} MB"
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": status,
                "notes": str(e)
            }

    def _show_batch_summary(self, results: List[Dict[str, Any]], job_type: Optional[str] = None) -> None:
        """Show summary dialog for batch operations."""
        dialog = tk.Toplevel(self.parent)
        title = f"{(job_type or 'Batch').title()} Operations Summary"
        dialog.title(title)
        dialog.geometry("700x515")
        dialog.transient(self.parent)
        self.theme.apply_to_widget(dialog, "main_window")
        dialog.grab_set()

        tk.Label(dialog, text=f"{title} Complete", font=("TkDefaultFont", 12, "bold")).pack(pady=10)

        # Create treeview for results
        tree_frame = tk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tree = ttk.Treeview(tree_frame, columns=("IP", "Action", "Status", "Notes"), show="headings", height=15)
        tree.heading("IP", text="IP Address")
        tree.heading("Action", text="Operation")
        tree.heading("Status", text="Status")
        tree.heading("Notes", text="Notes")

        tree.column("IP", width=120)
        tree.column("Action", width=80)
        tree.column("Status", width=80)
        tree.column("Notes", width=380)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Populate results
        success_count = 0
        failed_count = 0
        for result in results:
            status = result.get("status", "unknown")
            if status == "success":
                success_count += 1
            elif status in ("failed", "error"):
                failed_count += 1

            tree.insert("", tk.END, values=(
                result.get("ip_address", ""),
                result.get("action", ""),
                status,
                result.get("notes", "")
            ))

        # Summary stats
        stats_label = tk.Label(
            dialog,
            text=f"Total: {len(results)} | Success: {success_count} | Failed: {failed_count}",
            font=("TkDefaultFont", 10)
        )
        stats_label.pack(pady=5)

        # Close button
        close_button = tk.Button(dialog, text="Close", command=dialog.destroy)
        close_button.pack(pady=10)

        # Block until closed to preserve display sequencing
        self.parent.wait_window(dialog)
