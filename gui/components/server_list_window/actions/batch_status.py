"""
Batch status and UI helpers for ServerListWindow.
"""

import sqlite3
import tkinter as tk
from tkinter import messagebox
import time
import platform
import json
import threading
from concurrent.futures import Future
from typing import Dict, Any, Optional, List

from gui.components.server_list_window import table, filters
from gui.components.batch_summary_dialog import show_batch_summary_dialog
from gui.utils import probe_cache
from gui.utils.logging_config import get_logger
from gui.components.pry_status_dialog import BatchStatusDialog
from shared.config import get_standard_timestamp
from shared.db_migrations import run_migrations

_logger = get_logger("server_list_window.batch_status")


class ServerListWindowBatchStatusMixin:
        def _widget_exists(self, widget) -> bool:
            """Return True if a Tk widget is still alive."""
            try:
                return bool(widget) and widget.winfo_exists()
            except Exception:
                return False

        def _parse_accessible_shares(self, raw_value: Optional[Any]) -> List[str]:
            if not raw_value:
                return []
            if isinstance(raw_value, list):
                return [share.strip() for share in raw_value if isinstance(share, str) and share.strip()]
            return [share.strip() for share in str(raw_value).split(',') if share.strip()]

        def _is_table_lock_required(self, job_type: str) -> bool:
            """Return True if the server table should be locked for this batch type."""
            # Concurrency-friendly: do not lock the table for any job
            return False

        def _normalize_share_name(self, name: str) -> str:
            """
            Strip badges or slashes from share labels (e.g., 'share (denied)' -> 'share').
            """
            if not name:
                return ""
            cleaned = name.split(" (")[0]
            return cleaned.strip().strip("\\/").strip()

        def _get_config_path(self) -> Optional[str]:
            config_path = None
            if self.settings_manager:
                config_path = self.settings_manager.get_setting('backend.config_path', None)
                if not config_path and hasattr(self.settings_manager, "get_smbseek_config_path"):
                    config_path = self.settings_manager.get_smbseek_config_path()
            return config_path

        def _on_batch_future_done(self, job_id: str, target: Dict[str, Any], future: Future) -> None:
            # If the window is already destroyed, ignore late callbacks
            if not self._widget_exists(getattr(self, "window", None)):
                return

            job = self.active_jobs.get(job_id)
            if not job:
                return
            dialog = job.get("dialog")

            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "ip_address": target.get("ip_address"),
                    "action": job.get("type", "batch"),
                    "status": "failed",
                    "notes": str(exc)
                }

            job["results"].append(result)
            units = 1
            try:
                units = int(result.get("units", 1))
            except Exception:
                units = 1
            job["completed"] += max(units, 0)
            completed = job["completed"]
            total = job["total"]
            job_type = job["type"].title()
            unit_label = job.get("unit_label", "tasks")
            self._set_status(f"{job_type} batch {completed}/{total} {unit_label} complete")

            # Refresh dialog progress with latest counts
            dialog = job.get("dialog")
            message = None
            try:
                ip = result.get("ip_address") or ""
                message = f"{ip} ({units} {unit_label})" if ip else None
            except Exception:
                message = None
            self._update_batch_status_dialog(dialog, completed, total, message)

            if completed >= total:
                self._finalize_batch_job(job_id, dialog)

        def _finalize_batch_job(self, job_id: str, dialog: Optional[BatchStatusDialog] = None, *, show_summary: bool = True) -> None:
            job = self.active_jobs.pop(job_id, None)
            if not job:
                return

            executor = job.get("executor")
            if executor:
                executor.shutdown(wait=False, cancel_futures=True)

            results = list(job.get("results", []))
            job_type = job.get("type", "batch")
            dlg = dialog or job.get("dialog")
            if dlg:
                primary_result = results[0] if results else {"status": "unknown", "notes": ""}
                self._finish_batch_status_dialog(dlg, primary_result.get("status", "unknown"), primary_result.get("notes", ""))

            self._update_action_buttons_state()
            self._set_status(f"{job_type.title()} batch finished")
            self._flush_pending_refresh()
            self._set_table_interaction_enabled(True)
            if results and show_summary:
                self._show_batch_summary(job_type, results)
            # Close the status pop-out once the summary is shown
            if dlg:
                try:
                    dlg.destroy()
                except Exception:
                    pass
                if self.batch_status_dialog is dlg:
                    self.batch_status_dialog = None
            self._update_stop_button_style(self._is_batch_active())

        def _stop_active_batch(self) -> None:
            """
            Stop the most recently started active job. Individual dialogs also have Cancel.
            """
            if not self.active_jobs:
                return
            # Stop the last inserted job (most recent)
            job_id = list(self.active_jobs.keys())[-1]
            job = self.active_jobs.get(job_id)
            if not job:
                return
            cancel_event = job.get("cancel_event")
            if cancel_event:
                cancel_event.set()
            executor = job.get("executor")
            if executor:
                executor.shutdown(wait=False, cancel_futures=True)

            pending = []
            futures = job.get("futures", [])
            for target, future in futures:
                if not future.done():
                    future.cancel()
                    pending.append(target)

            for target in pending:
                job["results"].append({
                    "ip_address": target.get("ip_address"),
                    "action": job.get("type", "batch"),
                    "status": "cancelled",
                    "notes": "Stopped by user"
                })

            job["completed"] = job["total"]
            self._set_status("Batch stopped")
            self._finalize_batch_job(job_id)

        def _stop_all_jobs(self, *, show_summary: bool = True) -> None:
            """Stop all active jobs (used on window close)."""
            for job_id in list(self.active_jobs.keys()):
                job = self.active_jobs.get(job_id)
                if not job:
                    continue
                cancel_event = job.get("cancel_event")
                if cancel_event:
                    cancel_event.set()
                executor = job.get("executor")
                if executor:
                    executor.shutdown(wait=False, cancel_futures=True)
                job["completed"] = job["total"]
                self._finalize_batch_job(job_id, show_summary=show_summary)

        def _is_batch_active(self) -> bool:
            return any(job.get("completed", 0) < job.get("total", 0) for job in self.active_jobs.values())

        def _current_batch_type(self) -> Optional[str]:
            # Return most recently started job type if any
            if not self.active_jobs:
                return None
            latest = list(self.active_jobs.values())[-1]
            return latest.get("type")

        def _is_pry_batch_active(self) -> bool:
            return any(job.get("type") == "pry" and job.get("completed", 0) < job.get("total", 0) for job in self.active_jobs.values())

        def _is_probe_batch_active(self) -> bool:
            return any(job.get("type") == "probe" and job.get("completed", 0) < job.get("total", 0) for job in self.active_jobs.values())

        def _is_extract_batch_active(self) -> bool:
            return any(job.get("type") == "extract" and job.get("completed", 0) < job.get("total", 0) for job in self.active_jobs.values())

        def _set_status(self, message: str) -> None:
            if self._widget_exists(getattr(self, "status_label", None)):
                try:
                    self.status_label.configure(text=message)
                except Exception:
                    pass

        def _set_pry_status_button_visible(self, visible: bool) -> None:
            if not self.pry_status_button:
                return
            if visible:
                try:
                    self.pry_status_button.pack(anchor="w", pady=(4, 0))
                except Exception:
                    pass
            else:
                try:
                    self.pry_status_button.pack_forget()
                except Exception:
                    pass

        def _show_pry_status_dialog(self) -> None:
            if self.batch_status_dialog:
                self.batch_status_dialog.show()
            elif self.active_jobs:
                # Show the most recent job dialog if available
                for job in reversed(list(self.active_jobs.values())):
                    dlg = job.get("dialog")
                    if dlg:
                        dlg.show()
                        self.batch_status_dialog = dlg
                        break

        def _init_batch_status_dialog(self, job_type: str, fields: Dict[str, str], cancel_event: threading.Event, total: Optional[int] = None) -> BatchStatusDialog:
            """Create a fresh batch status dialog for the active run."""
            dialog = BatchStatusDialog(
                parent=self.window,
                theme=self.theme,
                title=f"{job_type.title()} Status",
                fields=fields,
                on_cancel=lambda: cancel_event.set(),
                total=total
            )
            self.batch_status_dialog = dialog  # keep latest for quick reopen
            self._set_pry_status_button_visible(True)
            return dialog

        def _update_batch_status_dialog(self, dialog: BatchStatusDialog, done: int, total: Optional[int], message: Optional[str]) -> None:
            if not self._widget_exists(dialog):
                return
            try:
                dialog.update_progress(done, total, message)
            except Exception:
                pass

        def _finish_batch_status_dialog(self, dialog: BatchStatusDialog, status: str, notes: str) -> None:
            if not self._widget_exists(dialog):
                return
            try:
                dialog.mark_finished(status, notes)
                dialog.show()
            except Exception:
                pass
            self._set_pry_status_button_visible(True)

        def _destroy_batch_status_dialog(self) -> None:
            if self.batch_status_dialog:
                try:
                    self.batch_status_dialog.destroy()
                except Exception:
                    pass
            self.batch_status_dialog = None
            self._set_pry_status_button_visible(False)

        def _persist_pry_success(self, target: Dict[str, Any], share_label: str, username: str, password: str) -> None:
            """
            Persist Pry success to DB: mark share accessible and store credentials.
            """
            if not self.settings_manager:
                return
            db_path = None
            try:
                db_path = self.settings_manager.get_database_path()
            except Exception:
                return
            if not db_path:
                return

            ip_address = target.get("ip_address")
            auth_method = target.get("auth_method", "")
            share_name = self._normalize_share_name(share_label)
            if not ip_address or not share_name:
                return

            try:
                run_migrations(db_path)
            except Exception:
                # Continue even if migration logging fails
                pass

            now_ts = get_standard_timestamp()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                # Ensure server row
                cur.execute("SELECT id, auth_method FROM smb_servers WHERE ip_address = ?", (ip_address,))
                row = cur.fetchone()
                if row:
                    server_id = row["id"]
                    cur.execute("UPDATE smb_servers SET last_seen = ? WHERE id = ?", (now_ts, server_id))
                else:
                    cur.execute(
                        """
                        INSERT INTO smb_servers (ip_address, auth_method, first_seen, last_seen, scan_count)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (ip_address, auth_method, now_ts, now_ts),
                    )
                    server_id = cur.lastrowid

                # Create a minimal pry session
                cur.execute(
                    """
                    INSERT INTO scan_sessions (tool_name, scan_type, status, started_at, completed_at, total_targets, successful_targets, failed_targets, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("xsmbseek", "pry", "completed", now_ts, now_ts, 1, 1, 0, f"Pry credential stored for {ip_address}"),
                )
                session_id = cur.lastrowid

                # Upsert share_access
                cur.execute(
                    "SELECT id FROM share_access WHERE server_id = ? AND share_name = ?",
                    (server_id, share_name),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE share_access
                        SET accessible = 1,
                            auth_status = ?,
                            error_message = NULL,
                            test_timestamp = ?,
                            session_id = ?
                        WHERE id = ?
                        """,
                        ("pry", now_ts, session_id, row["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO share_access (server_id, session_id, share_name, accessible, auth_status, test_timestamp)
                        VALUES (?, ?, ?, 1, ?, ?)
                        """,
                        (server_id, session_id, share_name, "pry", now_ts),
                    )

                # Upsert share_credentials
                cur.execute(
                    """
                    UPDATE share_credentials
                    SET username = ?, password = ?, last_verified_at = ?, updated_at = ?
                    WHERE server_id = ? AND share_name = ? AND source = 'pry'
                    """,
                    (username, password, now_ts, now_ts, server_id, share_name),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        """
                        INSERT INTO share_credentials (server_id, share_name, username, password, source, session_id, last_verified_at, updated_at)
                        VALUES (?, ?, ?, ?, 'pry', ?, ?, ?)
                        """,
                        (server_id, share_name, username, password, session_id, now_ts, now_ts),
                    )

                conn.commit()
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # Log warning; avoid breaking UI
                _logger.warning("Failed to persist Pry credential for %s/%s: %s", ip_address, share_name, exc)
            finally:
                conn.close()

        def _update_action_buttons_state(self) -> None:
            has_selection = bool(self.tree and self.tree.selection())
            batch_active = self._is_batch_active()
            pry_batch_active = self._is_pry_batch_active()
            probe_batch_active = self._is_probe_batch_active()
            extract_batch_active = self._is_extract_batch_active()

            # Allow browsing/details/export during running batches (read-only), but block starting new batches
            start_state = tk.NORMAL if has_selection and len(self.active_jobs) < 3 else tk.DISABLED
            for button in (self.probe_button, self.extract_button, self.pry_button):
                if button:
                    button.configure(state=start_state)
            # Browse stays enabled during batch
            if self.browser_button:
                self.browser_button.configure(state=tk.NORMAL if has_selection else tk.DISABLED)

            # Delete - disabled if no selection, batch active, or delete in progress
            if self.delete_button:
                delete_allowed = has_selection and not batch_active and not getattr(self, '_delete_in_progress', False)
                delete_state = tk.NORMAL if delete_allowed else tk.DISABLED
                self.delete_button.configure(state=delete_state)

            if self.stop_button:
                self.stop_button.configure(state=tk.NORMAL if batch_active else tk.DISABLED)
                self._update_stop_button_style(batch_active)

            detail_state = tk.NORMAL if has_selection else tk.DISABLED
            if self.details_button:
                self.details_button.configure(state=detail_state)

            self._set_pry_status_button_visible(bool(batch_active or self.batch_status_dialog))

            self._update_context_menu_state()

        def _update_stop_button_style(self, batch_active: bool) -> None:
            if not self.stop_button:
                return

            if not hasattr(self, "_stop_button_original_style"):
                self._stop_button_original_style = {
                    "bg": self.stop_button.cget("bg"),
                    "fg": self.stop_button.cget("fg"),
                    "activebackground": self.stop_button.cget("activebackground"),
                    "activeforeground": self.stop_button.cget("activeforeground"),
                    "text": self.stop_button.cget("text")
                }

            if batch_active:
                self.stop_button.configure(
                    bg="#b00020",
                    fg="#ffffff",
                    activebackground="#d32f2f",
                    activeforeground="#ffffff",
                    text="⏹ Stop Batch (running)"
                )
            else:
                original = getattr(self, "_stop_button_original_style", None)
                if original:
                    self.stop_button.configure(
                        bg=original.get("bg"),
                        fg=original.get("fg"),
                        activebackground=original.get("activebackground"),
                        activeforeground=original.get("activeforeground"),
                        text=original.get("text", "⏹ Stop Batch")
                    )

        def _update_context_menu_state(self) -> None:
            if not self.context_menu:
                return
            has_selection = bool(self.tree and self.tree.selection())
            batch_active = self._is_batch_active()
            # Context menu items enabled when selection exists (batch ops don't block)
            selection_state = tk.NORMAL if has_selection else tk.DISABLED
            selection_indices = getattr(self, "_selection_menu_indices", [])
            if not selection_indices:
                # Backward-compatible fallback for legacy menu layout.
                selection_indices = [0, 1, 2, 3, 4]
            for idx in selection_indices:
                try:
                    self.context_menu.entryconfig(idx, state=selection_state)
                except Exception:
                    pass
            # Delete (use stored index, not hardcoded)
            if self._delete_menu_index is not None:
                delete_allowed = has_selection and not batch_active and not getattr(self, '_delete_in_progress', False)
                delete_state = tk.NORMAL if delete_allowed else tk.DISABLED
                self.context_menu.entryconfig(self._delete_menu_index, state=delete_state)

        def _show_context_menu(self, event) -> str:
            if not self.tree or not self.context_menu:
                return "break"
            if self._context_menu_visible:
                self._hide_context_menu()
            row = self.tree.identify_row(event.y)
            if not row:
                return "break"
            selected = set(self.tree.selection())
            if row not in selected:
                self.tree.selection_set(row)
            self._update_action_buttons_state()
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()
            self._context_menu_visible = True
            self._install_context_dismiss_handlers()
            return "break"

        def _install_context_dismiss_handlers(self) -> None:
            self._remove_context_dismiss_handlers()
            for sequence in ("<Button-1>", "<Button-3>"):
                bind_id = self.tree.bind(sequence, self._handle_context_dismiss_click, add="+")
                if bind_id:
                    self._context_menu_bindings.append((sequence, bind_id))

        def _remove_context_dismiss_handlers(self) -> None:
            if not self._context_menu_bindings:
                return
            for sequence, bind_id in self._context_menu_bindings:
                try:
                    self.tree.unbind(sequence, bind_id)
                except Exception:
                    pass
            self._context_menu_bindings = []

        def _handle_context_dismiss_click(self, event=None):
            self._hide_context_menu()

        def _hide_context_menu(self) -> None:
            if not self._context_menu_visible or not self.context_menu:
                return
            try:
                self.context_menu.unpost()
            except Exception:
                pass
            self._context_menu_visible = False
            self._remove_context_dismiss_handlers()

        def _show_batch_summary(self, job_type: str, results: List[Dict[str, Any]]) -> None:
            show_batch_summary_dialog(
                parent=self.window,
                theme=self.theme,
                job_type=job_type,
                results=results,
                title_suffix="Batch Summary",
                geometry="700x400",
                show_export=True,
                show_stats=False,
                wait=False,
                modal=False,
            )

        def _flush_pending_refresh(self) -> None:
            if not self._pending_table_refresh:
                return
            self._apply_filters(force=True)
            if self._pending_selection:
                self._restore_selection(self._pending_selection)
            self._pending_table_refresh = False
            self._pending_selection = []

        def _attach_probe_status(self, servers: List[Dict[str, Any]]) -> None:
            if not self.settings_manager:
                for server in servers:
                    server["probe_status"] = 'unprobed'
                    server["probe_status_emoji"] = self._probe_status_to_emoji('unprobed')
                    server["rce_status"] = 'not_run'
                    server["rce_status_emoji"] = self._rce_status_to_emoji('not_run')
                    server["extracted"] = server.get("extracted", 0) or 0
                    server["extract_status_emoji"] = self._extract_status_to_emoji(server.get("extracted", 0))
                return

            for server in servers:
                ip = server.get("ip_address")
                host_type = server.get("host_type", "S")
                # FTP and HTTP rows: use DB-supplied probe_status only — never call
                # _determine_probe_status which reads the SMB file-based cache.
                # The UNION ALL query already populates probe_status from probe_cache tables.
                if host_type in ("F", "H"):
                    status = server.get("probe_status") or "unprobed"
                else:
                    status = server.get("probe_status") or self._determine_probe_status(ip)
                server["probe_status"] = status
                server["probe_status_emoji"] = self._probe_status_to_emoji(status)
                # Attach RCE status from database (protocol-aware)
                rce_status = server.get("rce_status") or self._determine_rce_status(ip, host_type)
                server["rce_status"] = rce_status
                server["rce_status_emoji"] = self._rce_status_to_emoji(rce_status)
                extracted_flag = server.get("extracted", 0)
                server["extract_status_emoji"] = self._extract_status_to_emoji(extracted_flag)

        def _determine_rce_status(self, ip_address: Optional[str], host_type: str = "S") -> str:
            """Determine RCE status from database probe cache."""
            if not ip_address or not self.db_reader:
                return 'not_run'

            try:
                result = self.db_reader.get_rce_status_for_host(ip_address, host_type)
                return result or 'not_run'
            except Exception:
                return 'not_run'

        def _handle_rce_status_update(self, ip_address: str, status: str, row_key: Optional[str] = None) -> None:
            """Handle RCE status update from probe/analysis."""
            if not ip_address:
                return

            for server in self.all_servers:
                if row_key is not None:
                    if server.get("row_key") == row_key:
                        server["rce_status"] = status
                        server["rce_status_emoji"] = self._rce_status_to_emoji(status)
                else:
                    # Legacy fallback: IP match, SMB rows only
                    if server.get("ip_address") == ip_address and server.get("host_type", "S") == "S":
                        server["rce_status"] = status
                        server["rce_status_emoji"] = self._rce_status_to_emoji(status)

            if self._is_batch_active():
                if not self._pending_table_refresh:
                    self._pending_selection = self._get_selected_row_keys()
                self._pending_table_refresh = True
            else:
                selected_ips = self._get_selected_row_keys()
                self._apply_filters()
                self._restore_selection(selected_ips)

        def _determine_probe_status(self, ip_address: Optional[str]) -> str:
            if not ip_address:
                return 'unprobed'

            cached_result = probe_cache.load_probe_result(ip_address)
            derived_status = 'unprobed'
            if cached_result:
                if self.indicator_patterns:
                    analysis = probe_patterns.attach_indicator_analysis(cached_result, self.indicator_patterns)
                else:
                    analysis = {"is_suspicious": False}
                if analysis.get('is_suspicious'):
                    derived_status = 'issue'
                else:
                    derived_status = 'clean'

            stored_status = self.settings_manager.get_probe_status(ip_address)
            status = derived_status if derived_status != 'unprobed' else stored_status

            if status != stored_status:
                self.settings_manager.set_probe_status(ip_address, status)

            self.probe_status_map[ip_address] = status
            return status

        def _probe_status_to_emoji(self, status: str) -> str:
            mapping = {
                'clean': '✔',
                'issue': '✖',
                'unprobed': '○'
            }
            return mapping.get(status, '⚪')

        def _rce_status_to_emoji(self, status: str) -> str:
            """
            Map rce_status to display icon.
            IMPORTANT: INSUFFICIENT_DATA renders as ⭘ (not run), not ✓ (clean)
            """
            mapping = {
                'not_run': '⭘',
                'clean': '✓',
                'flagged': '✖',
                'unknown': '?',
                'error': '⚠'
            }
            return mapping.get(status, '⭘')

        def _extract_status_to_emoji(self, extracted: Any) -> str:
            try:
                return '✔' if int(extracted) else '○'
            except Exception:
                return '○'

        def _handle_probe_status_update(self, ip_address: str, status: str, row_key: Optional[str] = None) -> None:
            if not ip_address:
                return
            if self.settings_manager:
                self.settings_manager.set_probe_status(ip_address, status)
            self.probe_status_map[ip_address] = status

            for server in self.all_servers:
                if row_key is not None:
                    if server.get("row_key") == row_key:
                        server["probe_status"] = status
                        server["probe_status_emoji"] = self._probe_status_to_emoji(status)
                else:
                    # Legacy fallback: IP match, SMB rows only
                    if server.get("ip_address") == ip_address and server.get("host_type", "S") == "S":
                        server["probe_status"] = status
                        server["probe_status_emoji"] = self._probe_status_to_emoji(status)

            if self._is_batch_active():
                if not self._pending_table_refresh:
                    self._pending_selection = self._get_selected_row_keys()
                self._pending_table_refresh = True
            else:
                selected_ips = self._get_selected_row_keys()
                self._apply_filters()
                self._restore_selection(selected_ips)

        def _handle_extracted_update(self, ip_address: str, row_key: Optional[str] = None, host_type: str = "S") -> None:
            """Mark host as extracted in-memory and persist to DB."""
            if not ip_address:
                return
            target_server = None
            for server in self.all_servers:
                if row_key is not None:
                    if server.get("row_key") == row_key:
                        target_server = server
                        break
                elif server.get("ip_address") == ip_address and server.get("host_type", "S") == host_type:
                    target_server = server
                    break
            if self.db_reader:
                try:
                    kw = {}
                    psid = (target_server or {}).get("protocol_server_id")
                    port = (target_server or {}).get("port")
                    if psid is not None:
                        kw["protocol_server_id"] = psid
                    if port is not None:
                        kw["port"] = port
                    self.db_reader.upsert_extracted_flag_for_host(
                        ip_address,
                        host_type,
                        True,
                        **kw,
                    )
                except Exception:
                    pass

            for server in self.all_servers:
                if row_key is not None:
                    if server.get("row_key") == row_key:
                        server["extracted"] = 1
                        server["extract_status_emoji"] = self._extract_status_to_emoji(1)
                else:
                    if server.get("ip_address") == ip_address and server.get("host_type", "S") == host_type:
                        server["extracted"] = 1
                        server["extract_status_emoji"] = self._extract_status_to_emoji(1)

            if self._is_batch_active():
                if not self._pending_table_refresh:
                    self._pending_selection = self._get_selected_row_keys()
                self._pending_table_refresh = True
            else:
                selected_ips = self._get_selected_row_keys()
                self._apply_filters()
                self._restore_selection(selected_ips)

        def _get_selected_row_keys(self) -> List[str]:
            """Return row_keys (iids) of currently selected tree rows."""
            return list(self.tree.selection())  # item IDs == iids == row_keys

        def _restore_selection(self, row_keys: List[str]) -> None:
            """Restore selection by row_key iids after a table refresh."""
            if not row_keys:
                return
            for rk in row_keys:
                if self.tree.exists(rk):
                    self.tree.selection_add(rk)

        def _toggle_mode(self) -> None:
            """Toggle between simple and advanced mode."""
            self.is_advanced_mode = not self.is_advanced_mode
            self._update_mode_display()

        def _set_mode(self, advanced: bool) -> None:
            """Set simple/advanced mode explicitly."""
            self.is_advanced_mode = bool(advanced)
            self._update_mode_display()

        def _update_mode_display(self) -> None:
            """Update display based on current mode using filters module."""
            if self.is_advanced_mode:
                self.mode_button.configure(text="📊 Simple")
                self.filter_frame.pack(fill=tk.X, padx=10, pady=(0, 5), before=self.table_frame)
                if self.filter_widgets and 'advanced_filters_frame' in self.filter_widgets:
                    filters.update_mode_display(self.filter_widgets['advanced_filters_frame'], True)
            else:
                self.mode_button.configure(text="🔧 Advanced")
                self.filter_frame.pack(fill=tk.X, padx=10, pady=(0, 5), before=self.table_frame)
                if self.filter_widgets and 'advanced_filters_frame' in self.filter_widgets:
                    filters.update_mode_display(self.filter_widgets['advanced_filters_frame'], False)

        def _clear_search(self) -> None:
            """Clear search text."""
            self.search_text.set("")
            self._apply_filters()

        def _populate_country_filter(self) -> None:
            """
            Populate country filter listbox with distinct country codes from database.

            Note: Sorting is done by country code BEFORE adding display text "(count)",
            ensuring alphabetical ordering by code, not by the full display string.
            """
            if not self.country_listbox:
                return

            # Save current selections before repopulating (for data refresh scenario)
            saved_selections = self._get_selected_country_codes() if hasattr(self, 'country_code_list') else []

            # Clear existing items
            self.country_listbox.delete(0, tk.END)
            self.country_code_list = []  # Reset mapping

            # Derive country breakdown from already-loaded all_servers (unified S+F rows).
            # Using db_reader.get_country_breakdown() would query smb_servers only and miss
            # FTP-only countries.
            from collections import Counter
            country_breakdown = Counter(
                s["country_code"] for s in self.all_servers if s.get("country_code")
            )

            if not country_breakdown:
                return  # Empty database

            # Sort alphabetically by country code (BEFORE adding display text)
            sorted_countries = sorted(country_breakdown.items())

            # Store full data for text filtering
            self.country_full_data = sorted_countries

            # Populate listbox with "CODE (count)" format
            for code, count in sorted_countries:
                display_text = f"{code} ({count})"
                self.country_listbox.insert(tk.END, display_text)
                self.country_code_list.append(code)  # Store code separately for filter logic

            # Restore selections after repopulation (handles data refresh)
            if saved_selections:
                for i, code in enumerate(self.country_code_list):
                    if code in saved_selections:
                        self.country_listbox.selection_set(i)
