"""
Server List Actions Mixin

Contains batch operations, status updates, filter templates, and other actions
extracted from the monolithic server_list_window to reduce file size while
preserving behavior.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import datetime
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, Future
import threading
import platform
import csv
import os
import sys

from gui.utils.database_access import DatabaseReader
from gui.utils.style import get_theme
from gui.utils.data_export_engine import get_export_engine
from gui.utils.scan_manager import get_scan_manager
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.template_store import TemplateStore
from gui.components.pry_dialog import PryDialog
from gui.components.pry_status_dialog import BatchStatusDialog
from shared.db_migrations import run_migrations
from gui.components.server_list_window import export, details, filters, table
from gui.components.batch_extract_dialog import BatchExtractSettingsDialog
from .batch_status import ServerListWindowBatchStatusMixin
from gui.utils import (
    probe_cache,
    probe_patterns,
    probe_runner,
    extract_runner,
    pry_runner,
)
from gui.utils.probe_cache_dispatch import get_probe_snapshot_path_for_host, dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from shared.quarantine import create_quarantine_dir

from .batch_operations import ServerListWindowBatchOperationsMixin

class ServerListWindowBatchMixin(ServerListWindowBatchOperationsMixin, ServerListWindowBatchStatusMixin):

    def _start_batch_job(self, job_type: str, targets: List[Dict[str, Any]], options: Dict[str, Any]) -> None:
        if not targets:
            return

        # Enforce max concurrent jobs
        if len(self.active_jobs) >= 3:
            messagebox.showinfo("Too many tasks", "Please wait for an existing task to finish before starting another.")
            return

        # Enforce per-host exclusivity (row_key-based; falls back to ip_address)
        active_row_keys = {
            t.get("row_key") or t.get("ip_address")
            for job in self.active_jobs.values()
            for t in job.get("targets", [])
        }
        for t in targets:
            key = t.get("row_key") or t.get("ip_address")
            if key and key in active_row_keys:
                ip = t.get("ip_address", key)
                messagebox.showinfo("Task already running", f"A task is already running for host {ip}. Please wait or stop it first.")
                return

        worker_count = max(1, min(8, int(options.get("worker_count", 1))))
        cancel_event = threading.Event()
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"{job_type}-batch")
        options = {**options, "worker_count": worker_count}

        job_id = f"{job_type}-{len(self.active_jobs)+1}-{int(threading.get_ident())}"

        # Unit tracking: all job types count targets (1 unit per target).
        # Share counts appear in per-target notes, not in progress math.
        unit_label = "targets"
        total_units = len(targets)

        job_record = {
            "id": job_id,
            "type": job_type,
            "targets": targets,
            "options": options,
            "executor": executor,
            "cancel_event": cancel_event,
            "results": [],
            "completed": 0,
            "total": total_units,
            "unit_label": unit_label,
            "futures": [],
            "dialog": None,
        }
        self.active_jobs[job_id] = job_record

        self._set_status(f"Running {job_type} batch (0/{total_units} {unit_label})…")
        self._update_action_buttons_state()

        if job_type == "pry":
            host_label = targets[0].get("ip_address") or "-"
            dialog = self._init_batch_status_dialog(
                "pry",
                {
                    "Host": host_label,
                    "Username": (options.get("username") or "").strip(),
                    "Share": (options.get("share_name") or "").strip(),
                    "Wordlist": Path(options.get("wordlist_path", "")).name if options.get("wordlist_path") else "-",
                },
                cancel_event,
                total=len(targets),
            )
            job_record["dialog"] = dialog
        elif job_type == "probe":
            est_shares = sum(len(t.get("shares", []) or []) for t in targets)
            dialog = self._init_batch_status_dialog(
                "probe",
                {
                    "Targets": str(total_units),
                    "Shares (est)": str(est_shares),
                    "Workers": str(worker_count),
                    "Max dirs/share": str(options.get("limits", {}).get("max_directories", "")),
                    "Max files/dir": str(options.get("limits", {}).get("max_files", "")),
                },
                cancel_event,
                total=total_units,
            )
            job_record["dialog"] = dialog
        elif job_type == "extract":
            dialog = self._init_batch_status_dialog(
                "extract",
                {
                    "Targets": str(len(targets)),
                    "Workers": str(worker_count),
                    "Max files/host": str(options.get("max_files_per_target", "")),
                    "Max size MB": str(options.get("max_total_size_mb", "")),
                },
                cancel_event,
                total=len(targets),
            )
            job_record["dialog"] = dialog

        for target in targets:
            future = executor.submit(self._run_batch_task, job_id, job_type, target, options, cancel_event)
            job_record["futures"].append((target, future))
            future.add_done_callback(lambda fut, target=target, jid=job_id: self.window.after(0, self._on_batch_future_done, jid, target, fut))

        if self._is_table_lock_required(job_type):
            self._set_table_interaction_enabled(False)

    def _run_batch_task(self, job_id: str, job_type: str, target: Dict[str, Any], options: Dict[str, Any], cancel_event: threading.Event) -> Dict[str, Any]:
        if cancel_event.is_set():
            return {
                "ip_address": target.get("ip_address"),
                "action": job_type,
                "status": "cancelled",
                "notes": "Cancelled"
            }

        try:
            if job_type == "probe":
                return self._execute_probe_target(job_id, target, options, cancel_event)
            if job_type == "extract":
                return self._execute_extract_target(job_id, target, options, cancel_event)
            if job_type == "pry":
                return self._execute_pry_target(job_id, target, options, cancel_event)
            raise RuntimeError(f"Unknown batch job type: {job_type}")
        except Exception as exc:
            return {
                "ip_address": target.get("ip_address"),
                "action": job_type,
                "status": "failed",
                "notes": str(exc)
            }

    def _execute_probe_target(self, job_id: str, target: Dict[str, Any], options: Dict[str, Any], cancel_event: threading.Event) -> Dict[str, Any]:
        ip_address = target.get("ip_address")
        host_type = str(target.get("host_type") or "S").strip().upper()
        row_key = target.get("row_key")

        if host_type == "F":
            port = 21
            try:
                port = int((target.get("data") or {}).get("port") or 21)
            except Exception:
                port = 21
            limits = options.get("limits", {}) or {}
            snapshot = dispatch_probe_run(
                ip_address, host_type,
                max_directories=int(limits.get("max_directories", 3)),
                max_files=int(limits.get("max_files", 5)),
                timeout_seconds=int(limits.get("timeout_seconds", 10)),
                cancel_event=cancel_event,
                port=port,
            )
            analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
            issue_detected = bool(analysis.get("is_suspicious"))
            status = "issue" if issue_detected else "clean"
            probe_summary = summarize_probe_snapshot(snapshot)
            display_entries = probe_summary["display_entries"]
            accessible_dirs_count = len(display_entries)
            accessible_dirs_list = ",".join(display_entries)
            try:
                snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=port)
            except TypeError:
                snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)

            for server in self.all_servers:
                if server.get("row_key") == row_key:
                    server["total_shares"] = accessible_dirs_count
                    server["accessible_shares"] = accessible_dirs_count
                    server["accessible_shares_list"] = accessible_dirs_list
                    break

            self._handle_probe_status_update(ip_address, status, row_key=row_key)
            try:
                self.db_reader.upsert_probe_cache_for_host(
                    ip_address,
                    host_type,
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
                "units": 1,
            }

        if host_type == "H":
            limits = options.get("limits", {}) or {}
            protocol_server_id = (
                target.get("protocol_server_id")
                if target.get("protocol_server_id") is not None
                else (target.get("data") or {}).get("protocol_server_id")
            )
            try:
                http_port = int(
                    target.get("port")
                    if target.get("port") is not None
                    else (target.get("data") or {}).get("port")
                )
            except (TypeError, ValueError):
                http_port = None
            http_scheme = (target.get("data") or {}).get("scheme")
            if self.db_reader and (http_scheme is None or http_port is None):
                detail = self.db_reader.get_http_server_detail(
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
            snapshot = dispatch_probe_run(
                ip_address, host_type,
                max_directories=int(limits.get("max_directories", 3)),
                max_files=int(limits.get("max_files", 5)),
                timeout_seconds=int(limits.get("timeout_seconds", 10)),
                cancel_event=cancel_event,
                port=http_port,
                scheme=http_scheme,
                protocol_server_id=protocol_server_id,
                db_reader=self.db_reader,
            )
            analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
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
                snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=http_port)
            except TypeError:
                snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)

            for server in self.all_servers:
                if server.get("row_key") == row_key:
                    server["total_shares"] = total
                    server["accessible_shares"] = total
                    server["accessible_shares_list"] = accessible_dirs_list
                    break

            self._handle_probe_status_update(ip_address, status, row_key=row_key)
            try:
                self.db_reader.upsert_probe_cache_for_host(
                    ip_address,
                    host_type,
                    status=status,
                    indicator_matches=len(analysis.get("matches", [])),
                    snapshot_path=snapshot_path,
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
                "action": "probe",
                "status": "success",
                "notes": ", ".join(notes_h),
                "units": 1,
            }

        # SMB probe path
        shares = target.get("shares", [])
        limits = options.get("limits", {})
        max_dirs = max(1, int(limits.get("max_directories", 3)))
        max_files = max(1, int(limits.get("max_files", 5)))
        timeout_seconds = max(1, int(limits.get("timeout_seconds", 10)))
        enable_rce = bool(options.get("enable_rce", False))

        username, password = details._derive_credentials(target.get("auth_method", ""))

        try:
            result = dispatch_probe_run(
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
                db_reader=self.db_reader,
            )
        except probe_runner.ProbeError as exc:
            status = "cancelled" if "cancel" in str(exc).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "probe",
                "status": status,
                "notes": str(exc),
                "units": 1,
            }

        if cancel_event.is_set():
            raise probe_runner.ProbeError("Probe cancelled")

        probe_cache.save_probe_result(ip_address, result)
        analysis = probe_patterns.attach_indicator_analysis(result, self.indicator_patterns)
        issue_detected = bool(analysis.get("is_suspicious"))
        self._handle_probe_status_update(ip_address, 'issue' if issue_detected else 'clean', row_key=row_key)
        try:
            self.db_reader.upsert_probe_cache_for_host(
                ip_address,
                host_type,
                status='issue' if issue_detected else 'clean',
                indicator_matches=len(analysis.get("matches", [])),
                snapshot_path=probe_cache.get_probe_result_path(ip_address) if hasattr(probe_cache, "get_probe_result_path") else None
            )
        except Exception:
            pass

        share_count = len(result.get("shares", []))
        notes: List[str] = []
        if share_count:
            notes.append(f"{share_count} share(s)")
        else:
            notes.append("No accessible shares")

        if enable_rce and result.get("rce_analysis"):
            rce_status = result["rce_analysis"].get("rce_status", "not_run")
            notes.append(f"RCE: {rce_status}")
            try:
                self._handle_rce_status_update(ip_address, rce_status, row_key=row_key)
            except Exception:
                pass

        if issue_detected:
            notes.append("Indicators detected")

        return {
            "ip_address": ip_address,
            "action": "probe",
            "status": "success",
            "notes": ", ".join(notes),
            "units": 1,
        }

    def _execute_extract_target(self, job_id: str, target: Dict[str, Any], options: Dict[str, Any], cancel_event: threading.Event) -> Dict[str, Any]:
        ip_address = target.get("ip_address")
        host_type = target.get("host_type", "S")
        row_key = target.get("row_key")

        if host_type in ("F", "H"):
            protocol_name = {"F": "FTP", "H": "HTTP"}.get(host_type, host_type)
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "skipped",
                "notes": f"{protocol_name} extract not yet supported",
            }

        shares = target.get("shares", [])
        if not shares:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "skipped",
                "notes": "No accessible shares"
            }

        base_path = Path(options.get("download_path", str(Path.home() / ".dirracuda" / "quarantine"))).expanduser()
        try:
            quarantine_dir = create_quarantine_dir(ip_address, purpose="extract", base_path=base_path)
        except Exception as exc:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "failed",
                "notes": f"Quarantine error: {exc}"
            }

        username, password = details._derive_credentials(target.get("auth_method", ""))

        dialog = self.active_jobs.get(job_id, {}).get("dialog")

        try:
            self.window.after(0, self._update_batch_status_dialog, dialog, 0, self.active_jobs.get(job_id, {}).get("total"), f"Extracting {ip_address}")

            def progress_cb(rel_path: str, index: int, limit: Optional[int]) -> None:
                try:
                    self.window.after(0, self._update_batch_status_dialog, dialog, 0, None, f"{ip_address}: {index}/{limit or '?'} {rel_path}")
                except Exception:
                    pass

            summary = extract_runner.run_extract(
                ip_address,
                shares,
                download_dir=quarantine_dir,
                username=username,
                password=password,
                max_total_bytes=options["max_total_size_mb"] * 1024 * 1024,
                max_file_bytes=options["max_file_size_mb"] * 1024 * 1024,
                max_file_count=options["max_files_per_target"],
                max_seconds=options["max_time_seconds"],
                max_depth=options["max_directory_depth"],
                allowed_extensions=options["included_extensions"],
                denied_extensions=options["excluded_extensions"],
                delay_seconds=options["download_delay_seconds"],
                connection_timeout=options["connection_timeout"],
                extension_mode=options.get("extension_mode"),
                progress_callback=progress_cb,
                cancel_event=cancel_event
            )
            log_path = extract_runner.write_extract_log(summary)
        except extract_runner.ExtractError as exc:
            status = "cancelled" if "cancel" in str(exc).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": status,
                "notes": str(exc)
            }

        files = summary["totals"].get("files_downloaded", 0)
        bytes_downloaded = summary["totals"].get("bytes_downloaded", 0)
        size_mb = bytes_downloaded / (1024 * 1024) if bytes_downloaded else 0
        note_parts = [f"{files} file(s)", f"{size_mb:.1f} MB"]
        if summary.get("timed_out"):
            note_parts.append("timed out")
        if summary.get("stop_reason"):
            note_parts.append(summary["stop_reason"].replace("_", " "))
        note_parts.append(f"log: {log_path}")

        # Mark host as extracted (successful run, even if zero files)
        self._handle_extracted_update(ip_address, row_key=row_key, host_type=host_type)

        # Update dialog progress (per target)
        self.window.after(0, self._update_batch_status_dialog, dialog, 1, self.active_jobs.get(job_id, {}).get("total"), f"Extracted {ip_address}")

        return {
            "ip_address": ip_address,
            "action": "extract",
            "status": "success",
            "notes": ", ".join(note_parts)
        }

    def _execute_pry_target(self, job_id: str, target: Dict[str, Any], options: Dict[str, Any], cancel_event: threading.Event) -> Dict[str, Any]:
        ip_address = target.get("ip_address")
        username = (options.get("username") or "").strip()
        wordlist_path = (options.get("wordlist_path") or "").strip()

        if not ip_address:
            return {
                "ip_address": ip_address,
                "action": "pry",
                "status": "failed",
                "notes": "Missing IP address"
            }
        if not username:
            return {
                "ip_address": ip_address,
                "action": "pry",
                "status": "failed",
                "notes": "Username is required"
            }
        if not wordlist_path:
            return {
                "ip_address": ip_address,
                "action": "pry",
                "status": "failed",
                "notes": "Password wordlist is required"
            }

        attempt_delay = float(options.get("attempt_delay", 1.0))
        max_attempts = int(options.get("max_attempts", 0))
        user_as_pass = bool(options.get("user_as_pass", True))
        stop_on_lockout = bool(options.get("stop_on_lockout", True))
        verbose = bool(options.get("verbose", False))
        self._last_password_tried = getattr(self, "_last_password_tried", {})
        self._last_password_tried[job_id] = None

        def progress_cb(done: int, total: Optional[int]) -> None:
            total_display = total if total is not None and total > 0 else "?"
            try:
                self.window.after(0, self._set_status, f"Pry {ip_address}: tried {done}/{total_display} passwords…")
                dialog = self.active_jobs.get(job_id, {}).get("dialog")
                # Show the actual password tried in last event instead of repeating counts
                last_pwd = self._last_password_tried.get(job_id)
                last_event_msg = f"Tried {last_pwd}" if last_pwd else f"Tried {done}/{total_display}"
                self.window.after(0, self._update_batch_status_dialog, dialog, done, total, last_event_msg)
            except Exception:
                pass

        try:
            result = pry_runner.run_pry(
                ip_address=ip_address,
                username=username,
                wordlist_path=wordlist_path,
                share_name=options.get("share_name", ""),
                user_as_pass=user_as_pass,
                stop_on_lockout=stop_on_lockout,
                verbose=verbose,
                attempt_delay=attempt_delay,
                max_attempts=max_attempts,
                cancel_event=cancel_event,
                progress_callback=progress_cb,
            )
        except pry_runner.PryError as exc:
            status = "cancelled" if "cancel" in str(exc).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "pry",
                "status": status,
                "notes": str(exc)
            }
        except Exception as exc:
            return {
                "ip_address": ip_address,
                "action": "pry",
                "status": "failed",
                "notes": str(exc)
            }

        if result.status == "success" and result.found_password:
            try:
                self._persist_pry_success(target, options.get("share_name", ""), username, result.found_password)
            except Exception:
                pass
            try:
                self.db_reader.upsert_probe_cache(
                    ip_address,
                    status="issue",
                    indicator_matches=0,
                    snapshot_path=None
                )
            except Exception:
                pass

        notes_text = result.notes
        if result.status == "cancelled" and result.notes.lower() == "cancelled":
            notes_text = f"Cancelled after {result.attempts} attempts"

        return {
            "ip_address": ip_address,
            "action": "pry",
            "status": result.status,
            "notes": notes_text
        }





























    # Probe status helpers



    # (status helpers moved to batch_status.py)
