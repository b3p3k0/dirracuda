"""
Server List Batch Operations Mixin

Handles probe, extract, browse, pry, delete, and batch job lifecycle logic.
Extracted from batch.py to shrink file size while preserving behavior.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
import threading
import csv
import os
from typing import Dict, List, Any, Optional

from gui.components.server_list_window import export, details, filters, table
from gui.components.batch_extract_dialog import BatchExtractSettingsDialog
from gui.components.pry_dialog import PryDialog
from gui.utils import probe_cache, probe_patterns, probe_runner, extract_runner, pry_runner
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir

_logger = get_logger("server_list_window")


class ServerListWindowBatchOperationsMixin:
    """
    Batch action handlers shared by the server list window.
    """
    def _on_copy_ip(self) -> None:
        """Copy selected host IP address(es) to clipboard."""
        self._hide_context_menu()
        if not self.tree:
            return
        selected = self.tree.selection()
        if not selected:
            return

        ips = []
        for item in selected:
            values = self.tree.item(item)["values"]
            if len(values) >= 7:
                ips.append(str(values[6]))  # IP at index 6 (after fav/avoid/probe/rce/extracted/Type)

        if ips:
            try:
                self.window.clipboard_clear()
                self.window.clipboard_append("\n".join(ips))
            except tk.TclError:
                pass

    def _on_probe_selected(self) -> None:
        self._hide_context_menu()
        targets = self._build_selected_targets()
        self._launch_probe_workflow(targets)

    def _on_extract_selected(self) -> None:
        self._hide_context_menu()
        targets = self._build_selected_targets()
        self._launch_extract_workflow(targets)

    def _on_pry_selected(self) -> None:
        self._hide_context_menu()
        targets = self._build_selected_targets()
        ftp_targets = [t for t in targets if t.get("host_type") == "F"]
        if ftp_targets:
            messagebox.showwarning(
                "Pry Not Supported",
                "Pry is an SMB-only action and cannot run on FTP server rows.",
                parent=self.window,
            )
            return
        if len(targets) != 1:
            messagebox.showwarning("Select one server", "Choose exactly one server to run Pry.", parent=self.window)
            return

        target = targets[0]
        ip_addr = target.get("ip_address") or ""

        config_path = None
        if self.settings_manager:
            config_path = self.settings_manager.get_setting('backend.config_path', None)
            if not config_path and hasattr(self.settings_manager, "get_smbseek_config_path"):
                config_path = self.settings_manager.get_smbseek_config_path()

        # Build share choices from share_access data
        shares = []
        try:
            shares = self.db_reader.get_denied_shares(ip_addr, limit=100)
            # Also include accessible shares for completeness
            shares += self.db_reader.get_accessible_shares(ip_addr)
            # Mark accessible flag for combobox badge
            for s in shares:
                s.setdefault("accessible", bool(s.get("permissions") or False))
        except Exception:
            shares = []

        dialog = PryDialog(
            parent=self.window,
            theme=self.theme,
            settings_manager=self.settings_manager,
            config_path=config_path,
            target_label=ip_addr,
            shares=shares
        )
        dialog_result = dialog.show()
        if not dialog_result:
            return

        options = dialog_result.get("options", {})
        options.update({
            "username": dialog_result.get("username", ""),
            "share_name": dialog_result.get("share_name", ""),
            "wordlist_path": dialog_result.get("wordlist_path", ""),
            "worker_count": 1
        })
        self._start_batch_job("pry", [target], options)

    def _on_file_browser_selected(self) -> None:
        self._hide_context_menu()
        targets = self._build_selected_targets()
        if not targets:
            messagebox.showwarning("No Selection", "Please select a server to browse.", parent=self.window)
            return
        if len(targets) != 1:
            messagebox.showwarning("Select one server", "Choose exactly one server to browse.", parent=self.window)
            return

        self._launch_browse_workflow(targets[0])

    def _on_mark_favorite_selected(self) -> None:
        """Toggle favorite flag for selected protocol rows."""
        self._hide_context_menu()
        self._toggle_selected_user_flag("favorite")

    def _on_mark_avoid_selected(self) -> None:
        """Toggle avoid flag for selected protocol rows."""
        self._hide_context_menu()
        self._toggle_selected_user_flag("avoid")

    def _on_mark_compromised_selected(self) -> None:
        """Toggle compromised status for selected protocol rows."""
        self._hide_context_menu()
        self._toggle_selected_compromised()

    def _toggle_selected_user_flag(self, field: str) -> None:
        """
        Toggle favorite/avoid for selected rows.

        Row-scoped behavior:
        - Each selected row flips its own current value.
        - Same-IP sibling rows in other protocols are not touched.
        """
        if field not in ("favorite", "avoid"):
            return
        targets = self._build_selected_targets()
        if not targets:
            return

        selected_row_keys = self._get_selected_row_keys()
        for target in targets:
            row_key = target.get("row_key")
            if not row_key:
                continue
            server_data = target.get("data") or {}
            current_value = 1 if bool(server_data.get(field, 0)) else 0
            new_value = 0 if current_value else 1
            self._apply_flag_toggle(row_key, field, new_value)

        self._apply_filters()
        self._restore_selection(selected_row_keys)

    def _toggle_selected_compromised(self) -> None:
        """
        Toggle compromised state for selected rows using probe cache status.

        Compromised ON:
        - probe_status = "issue"
        - indicator_matches >= 1

        Compromised OFF:
        - probe_status = "clean"
        - indicator_matches = 0
        """
        targets = self._build_selected_targets()
        if not targets:
            return

        selected_row_keys = self._get_selected_row_keys()
        changed = False

        for target in targets:
            server_data = target.get("data") or {}
            ip_address = target.get("ip_address")
            row_key = target.get("row_key")
            host_type = str(target.get("host_type") or "S").upper()
            if not ip_address or host_type not in ("S", "F", "H"):
                continue

            try:
                indicator_matches = int(server_data.get("indicator_matches", 0) or 0)
            except Exception:
                indicator_matches = 0
            probe_status = str(server_data.get("probe_status") or "").lower()
            is_compromised = probe_status == "issue" or indicator_matches > 0

            if is_compromised:
                new_status = "clean"
                new_indicator_matches = 0
            else:
                new_status = "issue"
                new_indicator_matches = indicator_matches if indicator_matches > 0 else 1

            try:
                if self.db_reader:
                    self.db_reader.upsert_probe_cache_for_host(
                        ip_address,
                        host_type,
                        status=new_status,
                        indicator_matches=new_indicator_matches,
                        snapshot_path=None,
                        accessible_dirs_count=None,
                        accessible_dirs_list=None,
                        accessible_files_count=None,
                        protocol_server_id=server_data.get("protocol_server_id"),
                        port=server_data.get("port"),
                    )
            except Exception as exc:
                _logger.warning(
                    "Compromised toggle DB write failed for %s (%s): %s",
                    row_key or ip_address,
                    host_type,
                    exc,
                )
                continue

            # Keep in-memory state in sync for immediate UI/filter behavior.
            target_server = next((s for s in self.all_servers if s.get("row_key") == row_key), None)
            if target_server is None:
                target_server = next(
                    (
                        s for s in self.all_servers
                        if s.get("ip_address") == ip_address
                        and str(s.get("host_type") or "S").upper() == host_type
                    ),
                    None,
                )
            if target_server is not None:
                target_server["probe_status"] = new_status
                target_server["probe_status_emoji"] = self._probe_status_to_emoji(new_status)
                target_server["indicator_matches"] = new_indicator_matches
                changed = True

        if changed:
            self._apply_filters()
            self._restore_selection(selected_row_keys)

    def _on_delete_selected(self) -> None:
        """Handle delete selected rows action."""
        self._hide_context_menu()

        # Validate selection exists
        targets = self._build_selected_targets()
        if not targets:
            messagebox.showwarning("No Selection", "Please select rows to delete.", parent=self.window)
            return

        # Check if delete already in progress
        if getattr(self, '_delete_in_progress', False):
            messagebox.showinfo("Delete In Progress", "A delete operation is already running.", parent=self.window)
            return

        # Check if batch jobs are active
        if self._is_batch_active():
            messagebox.showinfo(
                "Batch Active",
                "Cannot delete rows while a batch operation is running. "
                "Please wait for the batch to complete or stop it first.",
                parent=self.window
            )
            return

        # Dedup by row_key; fall back to synthetic key when row_key absent
        def _row_key_or_synthetic(t):
            rk = t.get("row_key")
            if rk:
                return rk
            synthetic = f"{t.get('host_type', 'S')}:{t.get('ip_address', '')}"
            return synthetic

        targets_by_key = {_row_key_or_synthetic(t): t for t in targets}

        # Build row_specs — skip malformed entries
        row_specs = []
        for t in targets_by_key.values():
            ht = t.get("host_type") or "S"
            ip = t.get("ip_address", "").strip()
            if ht not in ("S", "F", "H") or not ip:
                continue
            if ht == "H":
                port_value = (t.get("data") or {}).get("port")
                try:
                    port = int(port_value) if port_value not in (None, "") else None
                except (TypeError, ValueError):
                    port = None
                row_specs.append((ht, ip, port))
            else:
                row_specs.append((ht, ip))

        if not row_specs:
            messagebox.showwarning("No Valid Targets", "No valid server rows found to delete.", parent=self.window)
            return

        # Build label list from validated specs only
        validated_targets = [
            t for t in targets_by_key.values()
            if (t.get("host_type") or "S") in ("S", "F", "H") and t.get("ip_address", "").strip()
        ]
        row_labels = [f"{t.get('host_type', 'S')} {t.get('ip_address')}" for t in validated_targets]
        favorite_labels = [
            lbl for t, lbl in zip(validated_targets, row_labels)
            if t.get("data", {}).get("favorite")
        ]
        count = len(row_specs)

        # Show confirmation dialog
        if favorite_labels:
            favorite_list = "\n".join(f"• {lbl}" for lbl in favorite_labels)
            message = (
                f"You are about to delete {count} {'row' if count == 1 else 'rows'} including "
                f"{len(favorite_labels)} favorite(s):\n\n{favorite_list}\n\n"
                f"This action cannot be undone. Continue?"
            )
            title = "Delete Favorite Rows?"
        else:
            message = f"Delete {count} selected {'row' if count == 1 else 'rows'}? This action cannot be undone."
            title = "Delete Rows?"

        confirmed = messagebox.askyesno(title, message, parent=self.window)
        if not confirmed:
            return

        # Start background delete operation
        self._delete_in_progress = True
        self._set_status(f"Deleting {count} {'row' if count == 1 else 'rows'}...")
        self._update_action_buttons_state()

        # Create executor and submit delete task
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="delete-servers")
        future = executor.submit(self._run_delete_operation, row_specs)
        future.add_done_callback(lambda f: self.window.after(0, self._on_delete_complete, f))

    def _run_delete_operation(self, row_specs: List[tuple]) -> Dict[str, Any]:
        """Background thread worker for delete operation."""
        try:
            results = self.db_reader.bulk_delete_rows(row_specs)

            # Clear file-based probe cache only for SMB-deleted IPs.
            # Probe cache is IP-keyed; clearing on FTP-only delete would
            # also wipe the SMB probe cache for the same IP.
            for ip in results.get("deleted_smb_ips", []):
                try:
                    probe_cache.clear_probe_result(ip)
                except Exception:
                    pass

            return results

        except Exception as e:
            return {
                "deleted_count": 0,
                "deleted_ips": [],
                "deleted_smb_ips": [],
                "error": str(e)
            }

    def _on_delete_complete(self, future) -> None:
        """Handle delete completion on UI thread."""
        try:
            results = future.result()

            deleted_count = results.get("deleted_count", 0)
            error = results.get("error")

            # Show results messagebox with partial success handling
            if deleted_count > 0 and error is None:
                # Full success
                messagebox.showinfo(
                    "Delete Complete",
                    f"Deleted {deleted_count} row{'s' if deleted_count != 1 else ''} successfully.",
                    parent=self.window
                )
            elif deleted_count > 0 and error is not None:
                # Partial success
                messagebox.showwarning(
                    "Partial Delete",
                    f"Deleted {deleted_count} row{'s' if deleted_count != 1 else ''}, but errors occurred:\n\n{error}",
                    parent=self.window
                )
            elif deleted_count == 0 and error is not None:
                # Full failure
                messagebox.showerror(
                    "Delete Failed",
                    f"Failed to delete selected rows:\n\n{error}",
                    parent=self.window
                )
            else:
                # No-op (shouldn't happen)
                messagebox.showinfo(
                    "Delete Complete",
                    "No rows were deleted.",
                    parent=self.window
                )

            # If any servers were deleted, refresh table
            if deleted_count > 0:
                self.db_reader.clear_cache()
                self._load_data()
                self._apply_filters(force=True)

            # Clear selection BEFORE re-enabling buttons
            if self.tree:
                self.tree.selection_remove(self.tree.selection())

            # Re-enable UI
            self._delete_in_progress = False
            self._update_action_buttons_state()
            self._set_status("Idle")

        except Exception as e:
            # Handle worker thread exceptions
            messagebox.showerror(
                "Delete Error",
                f"An error occurred during delete:\n\n{str(e)}",
                parent=self.window
            )
            self._delete_in_progress = False
            self._update_action_buttons_state()
            self._set_status("Idle")

    def _prompt_probe_batch_settings(self, target_count: int) -> Optional[Dict[str, Any]]:
        config = details._load_probe_config(self.settings_manager)
        default_workers = 3
        enable_rce_default = False
        if self.settings_manager:
            default_workers = int(self.settings_manager.get_setting('probe.batch_max_workers', default_workers))
            rce_pref = self.settings_manager.get_setting('probe_dialog.rce_enabled', None)
            enable_rce_default = bool(rce_pref) if rce_pref is not None else bool(self.settings_manager.get_setting('scan_dialog.rce_enabled', False))

        default_workers = max(1, min(8, default_workers))

        dialog = tk.Toplevel(self.window)
        dialog.title("Batch Probe Settings")
        dialog.transient(self.window)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")

        tk.Label(dialog, text=f"Targets selected: {target_count}").grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")

        worker_var = tk.IntVar(value=default_workers)
        rce_var = tk.BooleanVar(value=enable_rce_default)
        max_dirs_var = tk.IntVar(value=config["max_directories"])
        max_files_var = tk.IntVar(value=config["max_files"])
        timeout_var = tk.IntVar(value=config["timeout_seconds"])

        def add_labeled_entry(row: int, label: str, var: tk.Variable):
            tk.Label(dialog, text=label).grid(row=row, column=0, padx=10, pady=5, sticky="w")
            tk.Entry(dialog, textvariable=var, width=10).grid(row=row, column=1, padx=10, pady=5, sticky="w")

        tk.Label(dialog, text="Worker threads (max 8):").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        tk.Entry(dialog, textvariable=worker_var, width=10).grid(row=1, column=1, padx=10, pady=5, sticky="w")

        add_labeled_entry(2, "Max directories/share:", max_dirs_var)
        add_labeled_entry(3, "Max files/directory:", max_files_var)
        add_labeled_entry(4, "Timeout per share (s):", timeout_var)

        tk.Checkbutton(dialog, text="Enable RCE analysis", variable=rce_var).grid(row=5, column=0, columnspan=2, padx=10, pady=(5, 10), sticky="w")

        result: Dict[str, Any] = {}

        def on_start():
            try:
                workers = max(1, min(8, int(worker_var.get())))
                max_dirs = max(1, int(max_dirs_var.get()))
                max_files = max(1, int(max_files_var.get()))
                timeout_val = max(1, int(timeout_var.get()))
            except (ValueError, tk.TclError):
                messagebox.showerror("Invalid Input", "Please enter numeric values for probe limits.", parent=dialog)
                return

            if self.settings_manager:
                self.settings_manager.set_setting('probe.batch_max_workers', workers)
                self.settings_manager.set_setting('probe.max_directories_per_share', max_dirs)
                self.settings_manager.set_setting('probe.max_files_per_directory', max_files)
                self.settings_manager.set_setting('probe.share_timeout_seconds', timeout_val)
                self.settings_manager.set_setting('probe_dialog.rce_enabled', bool(rce_var.get()))

            result.update({
                "worker_count": workers,
                "enable_rce": bool(rce_var.get()),
                "limits": {
                    "max_directories": max_dirs,
                    "max_files": max_files,
                    "timeout_seconds": timeout_val
                }
            })
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=6, column=0, columnspan=2, pady=(0, 10))
        tk.Button(button_frame, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=5)
        tk.Button(button_frame, text="Start", command=on_start).pack(side=tk.RIGHT)

        self.theme.apply_theme_to_application(dialog)
        dialog.wait_window()
        return result or None

    # Shared workflow launchers (used by main window + detail popup)

    def _launch_probe_workflow(self, targets: List[Dict[str, Any]]) -> None:
        if not targets:
            messagebox.showwarning("No Selection", "Please select at least one server to probe.", parent=self.window)
            return

        dialog_config = self._prompt_probe_batch_settings(len(targets))
        if not dialog_config:
            return

        self._start_batch_job("probe", targets, dialog_config)

    def _launch_extract_workflow(self, targets: List[Dict[str, Any]]) -> None:
        if not targets:
            messagebox.showwarning("No Selection", "Please select at least one server to extract from.", parent=self.window)
            return

        config_path = self._get_config_path()

        dialog_config = BatchExtractSettingsDialog(
            parent=self.window,
            theme=self.theme,
            settings_manager=self.settings_manager,
            config_path=config_path,
            config_editor_callback=self._open_config_editor,
            mode="on-demand",
            target_count=len(targets)
        ).show()

        if not dialog_config:
            return

        self._start_batch_job("extract", targets, dialog_config)

    def _launch_browse_workflow(self, target: Dict[str, Any]) -> None:
        ip_addr = target.get("ip_address")
        if not ip_addr:
            messagebox.showerror("Missing IP", "Unable to determine IP for selected server.", parent=self.window)
            return

        config_path = self._get_config_path()
        host_type = target.get("host_type", "S")

        if host_type == "F":
            port_raw = target.get("data", {}).get("port")
            try:
                port = int(port_raw) if port_raw not in (None, "") else 21
            except (TypeError, ValueError):
                port = 21
            banner = target.get("data", {}).get("banner")
            from gui.components.unified_browser_window import open_ftp_http_browser
            open_ftp_http_browser(
                "F",
                parent=self.window,
                ip_address=ip_addr,
                port=port,
                banner=banner,
                config_path=config_path,
                db_reader=self.db_reader,
                theme=self.theme,
                settings_manager=self.settings_manager,
            )
            return

        elif host_type == "H":
            row_data = target.get("data", {}) or {}
            row_psid = row_data.get("protocol_server_id")
            row_port = row_data.get("port")
            detail = (
                self.db_reader.get_http_server_detail(
                    ip_addr,
                    protocol_server_id=row_psid,
                    port=row_port,
                )
                if self.db_reader else None
            )
            try:
                port = int((detail or {}).get("port") or row_port or 80)
            except (TypeError, ValueError):
                port = 80
            scheme = (detail or {}).get("scheme") or ("https" if port == 443 else "http")
            banner = target.get("data", {}).get("banner")
            from gui.components.unified_browser_window import open_ftp_http_browser
            open_ftp_http_browser(
                "H",
                parent=self.window,
                ip_address=ip_addr,
                port=port,
                scheme=scheme,
                banner=banner,
                config_path=config_path,
                db_reader=self.db_reader,
                theme=self.theme,
                settings_manager=self.settings_manager,
            )
            return

        # SMB path
        shares = self.db_reader.get_accessible_shares(ip_addr) if self.db_reader else []

        def _clean_share_name(name: str) -> str:
            return name.strip().strip("\\/").strip()

        seen = set()
        share_names = []
        for s in shares:
            raw = s.get("share_name")
            cleaned = _clean_share_name(raw) if raw else ""
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            share_names.append(cleaned)
        if not share_names:
            messagebox.showinfo("No shares", "No accessible shares found for this host.")
            return

        share_creds = {}
        try:
            if self.db_reader:
                creds_rows = self.db_reader.get_share_credentials(ip_addr)
                for row in creds_rows:
                    raw_name = row.get("share_name")
                    cleaned_name = _clean_share_name(raw_name) if raw_name else ""
                    if cleaned_name:
                        share_creds[cleaned_name] = {
                            "username": row.get("username") or "",
                            "password": row.get("password") or "",
                            "source": row.get("source") or "",
                            "last_verified_at": row.get("last_verified_at")
                        }
        except Exception:
            share_creds = {}

        from gui.components.unified_browser_window import open_smb_browser
        open_smb_browser(
            parent=self.window,
            ip_address=ip_addr,
            shares=share_names,
            auth_method=target.get("auth_method", ""),
            config_path=config_path,
            db_reader=self.db_reader,
            theme=self.theme,
            settings_manager=self.settings_manager,
            share_credentials=share_creds,
            on_extracted=self._handle_extracted_update,
        )

    def _launch_probe_from_detail(self, server_data: Dict[str, Any]) -> None:
        target = self._server_data_to_target(server_data)
        if target:
            self._launch_probe_workflow([target])

    def _launch_extract_from_detail(self, server_data: Dict[str, Any]) -> None:
        target = self._server_data_to_target(server_data)
        if target:
            self._launch_extract_workflow([target])

    def _launch_browse_from_detail(self, server_data: Dict[str, Any]) -> None:
        target = self._server_data_to_target(server_data)
        if target:
            self._launch_browse_workflow(target)

    def _open_config_editor(self, config_path: str) -> None:
        """Open configuration editor window."""
        try:
            from gui.components.config_editor_window import open_config_editor_window
        except ImportError:
            try:
                from components.config_editor_window import open_config_editor_window
            except Exception as exc:
                messagebox.showerror("Configuration Editor Error", f"Unable to load config editor: {exc}", parent=self.window)
                return
        try:
            open_config_editor_window(self.window, config_path)
        except Exception as exc:
            messagebox.showerror("Configuration Editor Error", f"Failed to open configuration editor:\n{exc}", parent=self.window)

    # _prompt_extract_batch_settings removed - replaced by BatchExtractSettingsDialog

    def _build_selected_targets(self) -> List[Dict[str, Any]]:
        selected_servers = table.get_selected_server_data(self.tree, self.filtered_servers)
        descriptors: List[Dict[str, Any]] = []
        for server in selected_servers:
            target = self._server_data_to_target(server)
            if target:
                descriptors.append(target)
        return descriptors

    def _server_data_to_target(self, server_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ip_address = server_data.get("ip_address")
        if not ip_address:
            return None
        return {
            "ip_address": ip_address,
            "auth_method": server_data.get("auth_method", ""),
            "shares": self._parse_accessible_shares(server_data.get("accessible_shares_list")),
            "row_key": server_data.get("row_key"),
            "host_type": server_data.get("host_type", "S"),
            "protocol_server_id": server_data.get("protocol_server_id"),
            "port": server_data.get("port"),
            "data": server_data
        }
