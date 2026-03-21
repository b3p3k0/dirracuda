"""
Read-only SMB file browser window for xsmbseek.

Capabilities:
- Browse directories (list only) on a chosen share.
- Download a single file to quarantine.
- No previews, execution, or writes to SMB.
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import time
import queue

from shared.smb_browser import SMBNavigator, ListResult, Entry, ReadResult
try:
    from gui.components.file_viewer_window import open_file_viewer, is_binary_content
except ImportError:
    from file_viewer_window import open_file_viewer, is_binary_content
try:
    from gui.components.image_viewer_window import open_image_viewer
except ImportError:
    from image_viewer_window import open_image_viewer
from shared.quarantine import build_quarantine_path, log_quarantine_event
try:
    from gui.utils.database_access import DatabaseReader
except ImportError:
    from utils.database_access import DatabaseReader
try:
    from gui.components.server_list_window import details as detail_helpers  # for credential derivation
except ImportError:
    from server_list_window import details as detail_helpers

try:
    from gui.components.batch_extract_dialog import BatchExtractSettingsDialog, NO_EXTENSION_TOKEN
except ImportError:
    from batch_extract_dialog import BatchExtractSettingsDialog, NO_EXTENSION_TOKEN


def _format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format (e.g., '1.6 MB')."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[unit_index]}"


def _load_file_browser_config(config_path: Optional[str]) -> Dict:
    defaults = {
        "allow_smb1": True,
        "connect_timeout_seconds": 8,
        "request_timeout_seconds": 10,
        "max_entries_per_dir": 5000,
        "max_depth": 12,
        "max_path_length": 240,
        "download_chunk_mb": 4,
        "download_worker_count": 2,
        "download_large_file_mb": 25,
        "max_download_size_mb": 25,
        "quarantine_root": "~/.smbseek/quarantine",
        "viewer": {
            "max_view_size_mb": 5,
            "max_image_size_mb": 15,
            "max_image_pixels": 20000000,
            "default_encoding": "utf-8",
            "hex_bytes_per_row": 16
        },
    }
    if not config_path:
        return defaults
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        defaults.update(data.get("file_browser", {}))
    except Exception:
        pass
    return defaults


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


class FileBrowserWindow:
    """Tkinter window for SMB navigation + download."""

    def __init__(
        self,
        parent: tk.Widget,
        ip_address: str,
        shares: List[str],
        auth_method: Optional[str],
        config_path: Optional[str],
        db_reader: Optional[DatabaseReader] = None,
        theme=None,
        settings_manager=None,
        share_credentials: Optional[Dict[str, Dict[str, str]]] = None,
        on_extracted=None,
    ) -> None:
        self.parent = parent
        self.ip_address = ip_address
        self.shares = shares
        self.auth_method = auth_method or ""
        self.db_reader = db_reader
        self.theme = theme
        self.config_path = config_path
        self.config = _load_file_browser_config(config_path)
        self.download_cancel_event: Optional[threading.Event] = None
        self.settings_manager = settings_manager
        self.share_credentials = share_credentials or {}
        self.on_extracted = on_extracted

        # Download tuning
        self.download_workers = int(self.config.get("download_worker_count", 2) or 2)
        self.download_workers = max(1, min(3, self.download_workers))
        self.download_large_mb = int(self.config.get("download_large_file_mb", 25) or 25)
        if self.settings_manager:
            try:
                self.download_workers = int(self.settings_manager.get_setting('file_browser.download_worker_count', self.download_workers))
                self.download_large_mb = int(self.settings_manager.get_setting('file_browser.download_large_file_mb', self.download_large_mb))
                self.download_workers = max(1, min(3, self.download_workers))
            except Exception:
                pass

        creds = detail_helpers._derive_credentials(self.auth_method)
        self.username, self.password = creds
        self.folder_defaults = self.config.get("folder_download", {})
        self.max_batch_files = int(self.config.get("max_batch_files", 50))
        self.current_share: Optional[str] = None
        self.current_path = "\\"
        self.pending_path: Optional[str] = None  # in-flight navigation target
        self.list_thread: Optional[threading.Thread] = None
        self.download_thread: Optional[threading.Thread] = None
        self.busy = False

        self.navigator = SMBNavigator(
            allow_smb1=bool(self.config.get("allow_smb1", True)),
            connect_timeout=float(self.config.get("connect_timeout_seconds", 8)),
            request_timeout=float(self.config.get("request_timeout_seconds", 10)),
            max_entries=int(self.config.get("max_entries_per_dir", 5000)),
            max_depth=int(self.config.get("max_depth", 12)),
            max_path_length=int(self.config.get("max_path_length", 240)),
            download_chunk_mb=int(self.config.get("download_chunk_mb", 4)),
        )
        self.max_batch_files = int(self.config.get("max_batch_files", 50))

        self._build_window()
        if self.shares:
            self.share_var.set(self.shares[0])
            self._on_share_changed()
        else:
            self._set_status("No accessible shares found for this host.")

    # --- UI setup ------------------------------------------------------

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"SMB File Browser - {self.ip_address}")
        self.window.geometry("900x620")
        self.window.minsize(720, 480)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        top_frame = tk.Frame(self.window)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top_frame, text="Share:").pack(side=tk.LEFT)
        self.share_var = tk.StringVar()
        self.share_select = ttk.Combobox(top_frame, textvariable=self.share_var, state="readonly", values=self.shares)
        self.share_select.pack(side=tk.LEFT, padx=(5, 10))
        self.share_select.bind("<<ComboboxSelected>>", lambda *_: self._on_share_changed())

        tk.Label(top_frame, text="Path:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="\\")
        self.path_label = tk.Label(top_frame, textvariable=self.path_var, anchor="w")
        self.path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 10))

        button_frame = tk.Frame(self.window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.btn_up = tk.Button(button_frame, text="⬆ Up", command=self._on_up)
        self.btn_refresh = tk.Button(button_frame, text="🔄 Refresh", command=self._refresh)
        self.btn_view = tk.Button(button_frame, text="👁 View", command=self._on_view)
        self.btn_download = tk.Button(button_frame, text="⬇ Download to Quarantine", command=self._on_download)
        self.btn_cancel = tk.Button(button_frame, text="Cancel", command=self._on_cancel, state=tk.DISABLED)

        for btn in (self.btn_up, self.btn_refresh, self.btn_view, self.btn_download, self.btn_cancel):
            btn.pack(side=tk.LEFT, padx=5)

        # Download tuning controls (workers + large threshold)
        tuning_frame = tk.Frame(self.window)
        tuning_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        tk.Label(tuning_frame, text="Workers").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_var = tk.IntVar(value=self.download_workers)
        workers_spin = tk.Spinbox(
            tuning_frame, from_=1, to=3, width=3, textvariable=self.workers_var,
            command=self._persist_tuning
        )
        workers_spin.pack(side=tk.LEFT)

        tk.Label(tuning_frame, text="Large file MB").pack(side=tk.LEFT, padx=(10, 4))
        self.large_mb_var = tk.IntVar(value=self.download_large_mb)
        large_spin = tk.Spinbox(
            tuning_frame, from_=1, to=1024, width=5, textvariable=self.large_mb_var,
            command=self._persist_tuning
        )
        large_spin.pack(side=tk.LEFT)

        # Treeview for entries with always-visible scrollbar
        tree_frame = tk.Frame(self.window)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        columns = ("name", "type", "size", "modified", "mtime_raw", "size_raw")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("name", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.column("name", width=260, anchor="w")
        self.tree.column("type", width=90, anchor="w")
        self.tree.column("size", width=120, anchor="e")
        self.tree.column("modified", width=180, anchor="w")
        self.tree.column("mtime_raw", width=0, stretch=False)  # Hidden column for raw epoch
        self.tree.column("size_raw", width=0, stretch=False)  # Hidden column for raw bytes

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._on_item_double_click)

        self.status_var = tk.StringVar(value="Select a share to begin.")
        status = tk.Label(self.window, textvariable=self.status_var, anchor="w")
        status.pack(fill=tk.X, padx=10, pady=(0, 10))
        if self.theme:
            self.theme.apply_theme_to_application(self.window)

    # --- Navigation helpers -------------------------------------------

    def _on_share_changed(self) -> None:
        share = self.share_var.get()
        if not share:
            return
        # Apply stored credentials if available for this share
        if self.share_credentials:
            creds = self.share_credentials.get(share)
            if creds:
                self.username = creds.get("username") or self.username
                self.password = creds.get("password") or self.password
        self._disconnect()
        self.current_share = share
        self._set_path("\\")
        self._refresh()

    def _on_up(self) -> None:
        if self.current_path in ("\\", "/", ""):
            return
        parts = [p for p in self.current_path.split("\\") if p]
        new_path = "\\" + "\\".join(parts[:-1]) if parts[:-1] else "\\"
        self._navigate_to(new_path)

    def _refresh(self) -> None:
        if self.busy or not self.current_share:
            return
        # Refresh uses committed path, not pending navigation.
        self.pending_path = self.current_path
        self._start_list_thread(self.current_path)

    def _on_item_double_click(self, _event=None) -> None:
        # Ignore double-clicks while a directory listing is in-flight to prevent
        # accidental path appends (e.g., foo\\bar -> foo\\bar\\bar).
        if self.busy:
            return
        selection = self.tree.selection()
        if not selection:
            return
        item_id = selection[0]
        item = self.tree.item(item_id)
        values = item.get("values", [])
        name = values[0] if values else None
        type_label = values[1] if len(values) > 1 else None
        if type_label == "dir":
            target_path = self._join_path(self.current_path, name)
            self._navigate_to(target_path)
        elif type_label == "file":
            # Double-click on file opens viewer
            self._on_view()

    def _navigate_to(self, target_path: str) -> None:
        """Navigate to target path, only committing on successful list."""
        if self.busy or not self.current_share:
            return
        self.pending_path = target_path
        self._start_list_thread(target_path)

    def _set_path(self, path: str) -> None:
        self.current_path = path
        self.path_var.set(path)

    def _on_download(self) -> None:
        if self.busy or not self.current_share:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select one or more files to download.", parent=self.window)
            return

        files = []  # List of (path, mtime, size) tuples
        dirs = []
        skipped_dirs = 0
        for item_id in selection:
            item = self.tree.item(item_id)
            values = item.get("values", [])
            if len(values) < 2:
                continue
            name = values[0]
            type_label = values[1]
            mtime_raw = values[4] if len(values) > 4 and values[4] != "" else None
            if isinstance(mtime_raw, str):
                try:
                    mtime_raw = float(mtime_raw)
                except (ValueError, TypeError):
                    mtime_raw = None
            size_raw = 0
            if len(values) > 5:
                try:
                    size_raw = int(values[5])
                except (ValueError, TypeError):
                    size_raw = 0
            if type_label == "file":
                remote_path = self._join_path(self.current_path, name)
                files.append((remote_path, mtime_raw, size_raw))
            else:
                dir_path = self._join_path(self.current_path, name)
                dirs.append(dir_path)

        if not files and not dirs:
            messagebox.showinfo("No files", "No files or folders selected.", parent=self.window)
            return

        # Pre-flight size check for files
        max_dl_mb = float(self.config.get("max_download_size_mb", 25) or 0)
        if max_dl_mb > 0:
            over_limit = []
            for item_id in selection:
                item = self.tree.item(item_id)
                values = item.get("values", [])
                if len(values) > 5 and values[1] == "file":
                    try:
                        size_raw = int(values[5])
                        if size_raw > max_dl_mb * 1024 * 1024:
                            over_limit.append((values[0], size_raw))
                    except Exception:
                        continue
            if over_limit:
                names = ", ".join(n for n, _ in over_limit[:3])
                if len(over_limit) > 3:
                    names += f" … +{len(over_limit)-3} more"
                proceed = messagebox.askyesno(
                    "Large download",
                    f"The selected file(s) exceed the download limit of {max_dl_mb:.0f} MB.\n"
                    f"{names}\n\nDownload anyway?",
                    icon="warning",
                    parent=self.window,
                )
                if not proceed:
                    return

        if len(files) > self.max_batch_files:
            proceed = messagebox.askyesno(
                "Large selection",
                f"You selected {len(files)} files (limit {self.max_batch_files}). Download anyway?",
                icon="warning",
                parent=self.window,
            )
            if not proceed:
                return

        extract_opts = None
        if dirs:
            extract_opts = self._prompt_extract_options(len(dirs))
            if not extract_opts:
                return
        self._start_download_thread(files, dirs, extract_opts if dirs else None)

    def _on_cancel(self) -> None:
        """Cancel in-flight and queued downloads and return UI to idle state."""
        self.navigator.cancel()
        if self.download_cancel_event:
            self.download_cancel_event.set()
        self._set_status("Cancellation requested…")
        self.btn_cancel.configure(state=tk.DISABLED)

    def _on_view(self) -> None:
        """View selected file contents."""
        if self.busy or not self.current_share:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select a file to view.", parent=self.window)
            return
        if len(selection) > 1:
            messagebox.showinfo("Single file only", "Select only one file to view.", parent=self.window)
            return

        item_id = selection[0]
        item = self.tree.item(item_id)
        values = item.get("values", [])
        if len(values) < 2:
            return
        name = values[0]
        type_label = values[1]

        if type_label != "file":
            messagebox.showinfo("Not a file", "Select a file to view, not a directory.", parent=self.window)
            return

        # Get file size from treeview (size_raw is index 5)
        size_str = values[2] if len(values) > 2 else "0 B"
        size_raw = 0
        if len(values) > 5:
            try:
                size_raw = int(values[5])
            except (ValueError, TypeError):
                size_raw = 0
        remote_path = self._join_path(self.current_path, name)

        suffix = Path(name).suffix.lower()
        is_image = suffix in IMAGE_EXTS

        # Check size limit from config
        viewer_cfg = self.config.get("viewer", {}) or {}
        max_view_mb = viewer_cfg.get("max_view_size_mb", 5)
        max_image_mb = viewer_cfg.get("max_image_size_mb", max_view_mb)
        max_image_pixels = viewer_cfg.get("max_image_pixels", 20_000_000)
        max_view_bytes = (max_image_mb if is_image else max_view_mb) * 1024 * 1024

        # Pre-check: warn if file exceeds configured limit (image path uses image limit)
        if size_raw > max_view_bytes:
            if is_image:
                if not self._confirm_image_oversize(name, size_raw, max_view_mb if not is_image else max_image_mb):
                    return  # user cancelled
            else:
                if not self._show_size_warning_dialog(name, size_raw, max_view_mb):
                    return  # User cancelled
                # User clicked "Ignore Once" - proceed with 1GB hard cap
                max_view_bytes = 1024 * 1024 * 1024

        self._start_view_thread(remote_path, name, max_view_bytes, is_image=is_image, max_image_pixels=max_image_pixels)

    def _show_size_warning_dialog(self, filename: str, file_size: int, max_mb: int) -> bool:
        """
        Show dialog when file exceeds size limit.

        Returns:
            True if user wants to proceed anyway (Ignore Once)
            False if user wants to cancel (OK)
        """
        dialog = tk.Toplevel(self.window)
        dialog.title("File Too Large")
        dialog.geometry("450x180")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        # Center on parent
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() // 2) - 225
        y = self.window.winfo_y() + (self.window.winfo_height() // 2) - 90
        dialog.geometry(f"+{x}+{y}")

        result = {"proceed": False}

        # Message
        msg_frame = tk.Frame(dialog)
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        tk.Label(
            msg_frame,
            text=f'The file "{filename}" ({_format_file_size(file_size)}) exceeds\nthe maximum view size of {max_mb} MB.',
            justify=tk.LEFT
        ).pack(anchor="w")

        tk.Label(
            msg_frame,
            text="\nYou can change this limit in:",
            justify=tk.LEFT
        ).pack(anchor="w")

        tk.Label(
            msg_frame,
            text="conf/config.json -> file_browser.viewer.max_view_size_mb",
            font=("Courier", 9),
            fg="#666666"
        ).pack(anchor="w")

        # Buttons
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 15))

        def on_ok():
            result["proceed"] = False
            dialog.destroy()

        def on_ignore():
            result["proceed"] = True
            dialog.destroy()

        tk.Button(btn_frame, text="OK", width=12, command=on_ok).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame, text="Ignore Once", width=12, command=on_ignore).pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", on_ok)
        dialog.wait_window()

        return result["proceed"]

    def _start_view_thread(self, remote_path: str, display_name: str, max_bytes: int,
                           is_image: bool = False, max_image_pixels: int = 20000000) -> None:
        """Start background thread to read file for viewing."""
        def worker():
            try:
                self._set_busy(True)
                self._safe_after(0, lambda: self._set_status(f"Reading {display_name}..."))
                self._ensure_connected()
                result = self.navigator.read_file(remote_path, max_bytes=max_bytes)
                # Open viewer on main thread
                if is_image:
                    self._safe_after(0, lambda r=result: self._open_image_viewer(
                        remote_path, r.data, r.size, r.truncated, max_image_pixels
                    ))
                else:
                    self._safe_after(0, lambda r=result: self._open_viewer(
                        remote_path, r.data, r.size, r.truncated
                    ))
            except Exception as e:
                self._safe_after(0, lambda err=e: self._set_status(f"View failed: {err}"))
                self._safe_after(0, lambda err=e: messagebox.showerror(
                    "View error", str(err), parent=self.window
                ) if self._window_alive() else None)
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        view_thread = threading.Thread(target=worker, daemon=True)
        view_thread.start()

    def _open_viewer(self, remote_path: str, content: bytes, size: int, truncated: bool) -> None:
        """Open the file viewer window."""
        if not self._window_alive():
            return

        display_path = f"{self.ip_address}/{self.current_share}{remote_path}"
        file_size = size if not truncated else size  # actual bytes read

        def save_callback():
            # Download the file to quarantine when Save is clicked
            mtime = None  # We don't have mtime in viewer context
            self._start_download_thread([(remote_path, mtime)], [], None)

        open_file_viewer(
            parent=self.window,
            file_path=display_path,
            content=content,
            file_size=file_size,
            theme=self.theme,
            on_save_callback=save_callback,
        )
        self._set_status(f"Viewing {remote_path}")

    def _open_image_viewer(self, remote_path: str, content: bytes, size: int, truncated: bool, max_image_pixels: int) -> None:
        """Open image viewer with safety guards."""
        if not self._window_alive():
            return

        display_path = f"{self.ip_address}/{self.current_share}{remote_path}"

        def save_callback():
            mtime = None
            self._start_download_thread([(remote_path, mtime, size)], [], None)

        try:
            open_image_viewer(
                parent=self.window,
                file_path=display_path,
                content=content,
                max_pixels=max_image_pixels,
                theme=self.theme,
                on_save_callback=save_callback,
                truncated=truncated,
            )
            self._set_status(f"Viewing {remote_path}")
        except Exception as e:
            self._set_status(f"View failed: {e}")
            if self._window_alive():
                messagebox.showerror("View error", str(e), parent=self.window)

    def _confirm_image_oversize(self, name: str, size_bytes: int, max_mb: int) -> bool:
        """
        Prompt user to proceed with an oversized image, similar tone to extract warnings.

        Returns True to proceed, False to cancel.
        """
        dialog = tk.Toplevel(self.window)
        dialog.title("Large image")
        dialog.geometry("440x170")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        ensure_dialog_focus(dialog, self.window)

        msg_frame = tk.Frame(dialog)
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        msg = (
            f'The file "{name}" is {_format_file_size(size_bytes)}.\n'
            f"Maximum view size is set to {max_mb} MB.\n\nProceed?"
        )
        tk.Label(msg_frame, text=msg, justify=tk.LEFT, anchor="w").pack(fill=tk.X, expand=True)

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=(5, 10))

        result = {"proceed": False}

        def on_ok():
            result["proceed"] = True
            dialog.destroy()

        def on_cancel():
            result["proceed"] = False
            dialog.destroy()

        tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        dialog.wait_window()

        return result["proceed"]

    def _on_close(self) -> None:
        self.navigator.cancel()
        self._disconnect()
        self.window.destroy()
        self.window = None

    # --- Thread wrappers ----------------------------------------------

    def _prompt_extract_options(self, target_count: int) -> Optional[Dict[str, Any]]:
        """Use the shared batch extract dialog for folder downloads."""
        config_path = self.config_path
        if self.settings_manager:
            # Prefer user-set backend config path if available
            cfg_override = self.settings_manager.get_setting('backend.config_path', None)
            if cfg_override:
                config_path = cfg_override
        dialog_config = BatchExtractSettingsDialog(
            parent=self.window,
            theme=self.theme,
            settings_manager=self.settings_manager,
            config_path=config_path,
            config_editor_callback=None,
            mode="on-demand",
            target_count=target_count
        ).show()

        if not dialog_config:
            return None

        # Persist legacy folder limits for continuity
        limits = {
            "max_depth": int(dialog_config.get("max_directory_depth", 0)),
            "max_files": int(dialog_config.get("max_files_per_target", 0)),
            "max_total_mb": int(dialog_config.get("max_total_size_mb", 0)),
            "max_file_mb": int(dialog_config.get("max_file_size_mb", 0)),
        }
        self._persist_folder_limit_defaults(limits)

        # Add extension filter settings for directory expansion
        limits.update({
            "extension_mode": dialog_config.get("extension_mode", "download_all"),
            "included_extensions": [ext.lower() for ext in dialog_config.get("included_extensions", [])],
            "excluded_extensions": [ext.lower() for ext in dialog_config.get("excluded_extensions", [])],
        })
        return limits

    def _start_list_thread(self, path: str) -> None:
        # Mark busy before the worker thread starts to block re-entrant navigation.
        self._set_busy(True)

        def worker():
            try:
                self._ensure_connected()
                result = self.navigator.list_dir(path)
                self._safe_after(0, lambda: self._populate_entries(result, path))
            except Exception as e:
                # On navigation failure, revert to last committed path and surface context.
                self._safe_after(0, lambda err=e, attempted=path: self._handle_list_error(attempted, err))
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.list_thread = threading.Thread(target=worker, daemon=True)
        self.list_thread.start()

    def _start_download_thread(self, files_with_mtime: List[Tuple[str, Optional[float], int]], remote_dirs: List[str], folder_limits: Optional[Dict[str, Any]]) -> None:
        """
        Stream directory expansion into a bounded queue and start downloads immediately.
        """
        def worker():
            try:
                self._set_busy(True)
                self._ensure_connected()
                worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
                large_threshold_bytes = max(1, int(self.large_mb_var.get() or self.download_large_mb)) * 1024 * 1024
                dest_dir = build_quarantine_path(
                    self.ip_address,
                    self.current_share,
                    base_path=self.config.get("quarantine_root"),
                )

                q_small: queue.Queue = queue.Queue(maxsize=200)
                q_large: queue.Queue = queue.Queue(maxsize=200)
                expand_errors: List[Tuple[str, str]] = []
                errors: List[Tuple[str, str]] = []
                completed = 0
                total_enqueued = 0
                done_enumerating = threading.Event()
                cancel_event = threading.Event()
                self.download_cancel_event = cancel_event

                limits = folder_limits or {}
                max_files = limits.get("max_files", 0)
                max_total_mb = limits.get("max_total_mb", 0)
                max_file_mb = limits.get("max_file_mb", 0)
                max_total_bytes = max_total_mb * 1024 * 1024 if max_total_mb else 0
                max_file_bytes = max_file_mb * 1024 * 1024 if max_file_mb else 0
                bytes_enqueued = 0

                def enqueue_file(path: str, mtime: Optional[float], size: int) -> bool:
                    nonlocal total_enqueued, bytes_enqueued
                    if cancel_event.is_set():
                        return False
                    if max_file_bytes and size > max_file_bytes:
                        expand_errors.append((path, "skipped: exceeds per-file limit"))
                        return True
                    if max_total_bytes and (bytes_enqueued + size) > max_total_bytes:
                        expand_errors.append((path, "total size limit reached"))
                        return False
                    if max_files and total_enqueued >= max_files:
                        expand_errors.append((path, "file limit reached"))
                        return False
                    target_q = q_large if (size and size > large_threshold_bytes) else q_small
                    while not cancel_event.is_set():
                        try:
                            target_q.put((path, mtime, size), timeout=0.5)
                            break
                        except queue.Full:
                            continue
                    total_enqueued += 1
                    bytes_enqueued += size
                    return True

                def producer():
                    try:
                        # Seed initial explicit files
                        for remote_path, mtime, size in files_with_mtime:
                            if cancel_event.is_set():
                                break
                            enqueue_file(remote_path, mtime, size or 0)

                        if remote_dirs and folder_limits:
                            self._safe_after(0, lambda: self._set_status("Enumerating selected folders..."))
                            enumerated = 0
                            stack: List[Tuple[str, int]] = [(d, 0) for d in remote_dirs]
                            max_depth = limits.get("max_depth", 0)
                            extension_mode = limits.get("extension_mode", "download_all")
                            included_ext = [ext.lower() for ext in limits.get("included_extensions", [])]
                            excluded_ext = [ext.lower() for ext in limits.get("excluded_extensions", [])]
                            while stack and not cancel_event.is_set():
                                current_path, depth = stack.pop()
                                if max_depth and depth > max_depth:
                                    continue
                                try:
                                    entries = self.navigator.list_dir(current_path)
                                except Exception as exc:
                                    expand_errors.append((current_path, str(exc)))
                                    continue
                                for entry in entries.entries:
                                    if cancel_event.is_set():
                                        break
                                    name = entry.name
                                    rel = self._join_path(current_path, name)
                                    if entry.is_dir:
                                        stack.append((rel, depth + 1))
                                        continue
                                    size = entry.size or 0
                                    if not self._should_include_extension(name, limits.get("extension_mode", "download_all"),
                                                                          [ext.lower() for ext in limits.get("included_extensions", [])],
                                                                          [ext.lower() for ext in limits.get("excluded_extensions", [])]):
                                        continue
                                    if not enqueue_file(rel, entry.modified_time, size):
                                        break
                                    enumerated += 1
                                    if enumerated % 50 == 0:
                                        self._safe_after(0, lambda count=enumerated, qs=q_small.qsize()+q_large.qsize(): self._set_status(f"Enumerating... {count} files queued ({qs} ready)"))
                    finally:
                        done_enumerating.set()

                def consumer(target_q: queue.Queue):
                    nonlocal completed
                    last_status = {"ts": 0}
                    # Per-worker navigator to avoid sharing SMBConnection across threads
                    worker_nav = SMBNavigator(
                        allow_smb1=bool(self.config.get("allow_smb1", True)),
                        connect_timeout=float(self.config.get("connect_timeout_seconds", 8)),
                        request_timeout=float(self.config.get("request_timeout_seconds", 10)),
                        max_entries=int(self.config.get("max_entries_per_dir", 5000)),
                        max_depth=int(self.config.get("max_depth", 12)),
                        max_path_length=int(self.config.get("max_path_length", 240)),
                        download_chunk_mb=int(self.config.get("download_chunk_mb", 4)),
                    )
                    try:
                        worker_nav.connect(
                            host=self.ip_address,
                            share=self.current_share,
                            username=self.username,
                            password=self.password,
                        )
                    except Exception as exc:
                        errors.append(("", f"Worker connect failed: {exc}"))
                        return

                    while not (done_enumerating.is_set() and q_small.empty() and q_large.empty()) and not cancel_event.is_set():
                        try:
                            item = target_q.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if cancel_event.is_set():
                            target_q.task_done()
                            break
                        remote_path, mtime, _size = item
                        self._safe_after(0, lambda rp=remote_path, c=completed, t=lambda: max(total_enqueued, completed + 1): self._set_status(f"Downloading {rp} ({c+1}/{t()})"))
                        try:
                            last_update = {"ts": 0}

                            def _progress(bytes_written: int, _total_unused: Optional[int]) -> None:
                                now = time.time()
                                if cancel_event.is_set():
                                    worker_nav.cancel()
                                    return
                                if now - last_update["ts"] < 0.2:
                                    return
                                last_update["ts"] = now
                                human = _format_file_size(bytes_written)
                                self._safe_after(0, lambda rp=remote_path, c=completed, h=human: self._set_status(
                                    f"Downloading {rp} ({c+1}/{max(total_enqueued, completed+1)}) – {h} (workers {self.workers_var.get()})"))

                            result = worker_nav.download_file(
                                remote_path,
                                dest_dir,
                                preserve_structure=True,
                                mtime=mtime,
                                progress_callback=_progress
                            )
                            try:
                                host_dir = Path(dest_dir).parent.parent  # host/date/share
                                log_quarantine_event(host_dir, f"downloaded {self.current_share}{remote_path} -> {result.saved_path}")
                            except Exception:
                                pass
                            completed += 1
                        except Exception as e:
                            friendly = self._map_download_error(e)
                            errors.append((remote_path, friendly))
                        finally:
                            target_q.task_done()
                    # If cancel requested mid-transfer, ensure connection is closed promptly
                    if cancel_event.is_set():
                        try:
                            worker_nav.cancel()
                        except Exception:
                            pass
                    try:
                        worker_nav.disconnect()
                    except Exception:
                        pass

                producer_thread = threading.Thread(target=producer, daemon=True)
                consumer_threads = []
                for _ in range(worker_count):
                    consumer_threads.append(threading.Thread(target=consumer, args=(q_small,), daemon=True))
                # Single worker for large files
                consumer_threads.append(threading.Thread(target=consumer, args=(q_large,), daemon=True))

                producer_thread.start()
                for t in consumer_threads:
                    t.start()

                producer_thread.join()
                for t in consumer_threads:
                    t.join()

                if cancel_event.is_set():
                    self._safe_after(0, lambda: self._set_status("Download cancelled."))
                else:
                    summary_msg = f"Downloaded {completed}/{max(total_enqueued, completed)} file(s)"
                    total_errors = len(errors) + len(expand_errors)
                    if total_errors:
                        summary_msg += f" ({total_errors} failed)"
                    self._safe_after(0, lambda: self._set_status(summary_msg))
                    if completed > 0:
                        self._safe_after(0, self._handle_extracted_success)
                    if total_errors:
                        combined = errors + expand_errors
                        err_text = "\n".join(f"{p}: {err}" for p, err in combined[:5])
                        self._safe_after(0, lambda: messagebox.showwarning("Download issues", err_text, parent=self.window) if self._window_alive() else None)
                    else:
                        self._safe_after(0, lambda: messagebox.showinfo("Download complete", summary_msg, parent=self.window) if self._window_alive() else None)
            except Exception as e:
                self._safe_after(0, lambda err=e: self._set_status(f"Download failed: {err}"))
                self._safe_after(0, lambda err=e: messagebox.showerror("Download failed", str(err), parent=self.window) if self._window_alive() else None)
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.download_thread = threading.Thread(target=worker, daemon=True)
        self.download_thread.start()

    # --- SMB helpers ---------------------------------------------------

    def _expand_directories(self, dirs: List[str], limits: Dict[str, Any]) -> Tuple[List[Tuple[str, Optional[float]]], int, List[Tuple[str, str]]]:
        max_depth = limits.get("max_depth", 0)
        max_files = limits.get("max_files", 0)
        max_total_mb = limits.get("max_total_mb", 0)
        max_file_mb = limits.get("max_file_mb", 0)
        extension_mode = limits.get("extension_mode", "download_all")
        included_ext = [ext.lower() for ext in limits.get("included_extensions", [])]
        excluded_ext = [ext.lower() for ext in limits.get("excluded_extensions", [])]

        expanded: List[Tuple[str, Optional[float]]] = []  # (path, mtime) tuples
        errors: List[Tuple[str, str]] = []
        skipped = 0
        total_bytes = 0
        enumerated = 0

        stack: List[Tuple[str, int]] = [(d, 0) for d in dirs]

        while stack:
            current_path, depth = stack.pop()
            if max_depth and depth > max_depth:
                continue
            try:
                entries = self.navigator.list_dir(current_path)
            except Exception as exc:
                errors.append((current_path, str(exc)))
                continue
            for entry in entries.entries:
                name = entry.name
                rel = self._join_path(current_path, name)
                if entry.is_dir:
                    stack.append((rel, depth + 1))
                    continue

                size = entry.size or 0
                if max_file_mb and size > max_file_mb * 1024 * 1024:
                    skipped += 1
                    continue
                if max_total_mb:
                    if (total_bytes + size) > max_total_mb * 1024 * 1024:
                        errors.append((rel, "total size limit reached"))
                        return expanded, skipped, errors

                if not self._should_include_extension(name, extension_mode, included_ext, excluded_ext):
                    skipped += 1
                    continue

                expanded.append((rel, entry.modified_time))
                total_bytes += size
                enumerated += 1
                if enumerated % 50 == 0:
                    self._safe_after(0, lambda count=enumerated: self._set_status(f"Enumerating... {count} files queued"))
                if max_files and len(expanded) >= max_files:
                    return expanded, skipped, errors

        return expanded, skipped, errors

    def _should_include_extension(self, name: str, mode: str, included: List[str], excluded: List[str]) -> bool:
        """Determine if a file should be included based on extension filters."""
        if mode == "download_all":
            return True
        ext = Path(name).suffix.lower()
        token = ext if ext else NO_EXTENSION_TOKEN.lower()
        if mode == "allow_only":
            return token in included
        if mode == "deny_only":
            return token not in excluded
        return True

    def _handle_extracted_success(self) -> None:
        """Invoke callback/DB flag when a download succeeds."""
        if callable(self.on_extracted):
            try:
                self.on_extracted(self.ip_address)
            except Exception:
                pass
            return
        if self.db_reader:
            try:
                self.db_reader.upsert_extracted_flag(self.ip_address, True)
            except Exception:
                pass

    @staticmethod
    def _map_download_error(exc: Exception) -> str:
        """Translate low-level download errors into user-friendly messages."""
        text = str(exc)
        lowered = text.lower()
        if "protocolid" in lowered or "unpacked data doesn't match" in lowered:
            return "Unexpected SMB response from server (often happens with large or partial transfers). File not saved."
        if "timed out" in lowered or "timeout" in lowered:
            return "Download timed out. Try again or reduce file size."
        if "cancelled" in lowered:
            return "Download cancelled."
        return text

    def _ensure_connected(self) -> None:
        if self.navigator and self.current_share and self.navigator.share_name == self.current_share:
            return
        if not self.current_share:
            raise RuntimeError("No share selected.")
        self.navigator.cancel()
        self.navigator.disconnect()
        self._set_status(f"Connecting to {self.ip_address}/{self.current_share}…")
        self.navigator.connect(
            host=self.ip_address,
            share=self.current_share,
            username=self.username,
            password=self.password,
        )

    def _disconnect(self) -> None:
        try:
            self.navigator.disconnect()
        except Exception:
            pass

    # --- UI updates ----------------------------------------------------

    def _populate_entries(self, result: ListResult, path: str) -> None:
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Sort directories first, then files
        sorted_entries = sorted(
            result.entries,
            key=lambda e: (0 if e.is_dir else 1, e.name.lower()),
        )

        for entry in sorted_entries:
            mtime_str = ""
            mtime_raw = entry.modified_time or ""
            if entry.modified_time:
                mtime_str = datetime.fromtimestamp(entry.modified_time).strftime("%Y-%m-%d %H:%M:%S")
            size_raw = entry.size or 0
            self.tree.insert(
                "",
                "end",
                values=(entry.name, "dir" if entry.is_dir else "file", _format_file_size(entry.size), mtime_str, mtime_raw, size_raw),
            )

        # Commit navigation only after successful list
        self._set_path(path)

        status_parts = [f"Path {path} ({len(result.entries)} items)"]
        if result.truncated:
            status_parts.append(f"truncated at {self.config.get('max_entries_per_dir')}")
        if result.warning:
            status_parts.append(result.warning)
        self._set_status(" | ".join(status_parts))
        self.btn_cancel.configure(state=tk.NORMAL if self.busy else tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self.btn_up, self.btn_refresh, self.btn_view, self.btn_download):
            if btn and btn.winfo_exists():
                btn.configure(state=state)
        if self.btn_cancel and self.btn_cancel.winfo_exists():
            self.btn_cancel.configure(state=tk.NORMAL if busy else tk.DISABLED)

    def _handle_list_error(self, attempted_path: str, err: Exception) -> None:
        """Handle directory listing errors and restore previous path."""
        # If the attempted path differs from the committed path, roll back path label.
        if attempted_path != self.current_path:
            # Revert any pending path; keep current_path as-is.
            self.pending_path = None
            self.path_var.set(self.current_path)
        self._set_status(f"Error listing {attempted_path}: {err}")
        if self._window_alive():
            messagebox.showerror("Browse error", f"{attempted_path}\n\n{err}", parent=self.window)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _window_alive(self) -> bool:
        return bool(self.window and self.window.winfo_exists())

    def _safe_after(self, delay_ms: int, callback) -> None:
        if not self._window_alive():
            return
        try:
            self.window.after(delay_ms, callback)
        except Exception:
            pass

    def _load_folder_limit_defaults(self) -> Dict[str, int]:
        """
        Load folder download limits, preferring user settings over config defaults.
        """
        defaults = self.folder_defaults or {}
        if self.settings_manager:
            try:
                saved = self.settings_manager.get_setting('file_browser.folder_limits', {}) or {}
                # Merge saved over defaults
                defaults = {**defaults, **saved}
            except Exception:
                pass
        return defaults

    def _persist_folder_limit_defaults(self, limits: Dict[str, int]) -> None:
        """Persist folder download limits to settings."""
        if not self.settings_manager:
            return
        try:
            self.settings_manager.set_setting('file_browser.folder_limits', limits)
        except Exception:
            pass

    def _persist_tuning(self) -> None:
        """Persist worker/threshold tuning from UI controls."""
        try:
            self.download_workers = max(1, min(3, int(self.workers_var.get())))
            self.download_large_mb = max(1, int(self.large_mb_var.get()))
        except Exception:
            return
        if self.settings_manager:
            try:
                self.settings_manager.set_setting('file_browser.download_worker_count', self.download_workers)
                self.settings_manager.set_setting('file_browser.download_large_file_mb', self.download_large_mb)
            except Exception:
                pass

    # --- Path helpers --------------------------------------------------

    @staticmethod
    def _join_path(base: str, name: str) -> str:
        base_norm = base.rstrip("\\/")
        if not base_norm:
            return f"\\{name}"
        return f"{base_norm}\\{name}"
