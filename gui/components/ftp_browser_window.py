"""
Read-only FTP file browser window for xsmbseek.

Capabilities:
- Browse directories (list only) on an anonymous FTP server.
- Download a single file to quarantine.
- No writes to the remote FTP server.
- Background probe snapshot on window open.
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional
from datetime import datetime

from shared.ftp_browser import FtpNavigator, FtpCancelledError, FtpFileTooLargeError
from shared.quarantine import build_quarantine_path, log_quarantine_event
from gui.utils.ftp_probe_cache import load_ftp_probe_result
from gui.utils.ftp_probe_runner import run_ftp_probe
try:
    from gui.components.file_viewer_window import open_file_viewer
except ImportError:
    from file_viewer_window import open_file_viewer
try:
    from gui.components.image_viewer_window import open_image_viewer
except ImportError:
    from image_viewer_window import open_image_viewer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _load_ftp_browser_config(config_path: Optional[str]) -> Dict:
    defaults: Dict[str, Any] = {
        "max_entries": 5000,
        "max_depth": 12,
        "max_path_length": 1024,
        "max_file_bytes": 26_214_400,   # 25 MB
        "connect_timeout": 10,
        "request_timeout": 15,
        "quarantine_base": "~/.smbseek/quarantine",
        "viewer": {
            "max_view_size_mb": 5,
            "max_image_size_mb": 15,
            "max_image_pixels": 20_000_000,
        },
    }
    if not config_path:
        return defaults
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        defaults.update(data.get("ftp_browser", {}))
    except Exception:
        pass
    return defaults


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# FtpBrowserWindow
# ---------------------------------------------------------------------------

class FtpBrowserWindow:
    """Tkinter toplevel window for anonymous FTP navigation and file download."""

    def __init__(
        self,
        parent: tk.Widget,
        ip_address: str,
        port: int = 21,
        banner: Optional[str] = None,
        config_path: Optional[str] = None,
        db_reader=None,
        theme=None,
        settings_manager=None,
    ) -> None:
        self.parent = parent
        self.ip_address = ip_address
        self.port = port
        self.db_reader = db_reader
        self.theme = theme
        self.settings_manager = settings_manager
        self.config = _load_ftp_browser_config(config_path)
        self._server_banner = str(banner or "")

        self._current_path: str = "/"
        self._cancel_event = threading.Event()
        self._navigator: Optional[FtpNavigator] = None
        self._nav_thread: Optional[threading.Thread] = None
        self._download_thread: Optional[threading.Thread] = None
        self.busy: bool = False

        self._build_window()

        # Apply cached probe snapshot if available
        self._apply_probe_snapshot(load_ftp_probe_result(ip_address))

        # Start navigating to root
        self._navigate_to("/")

        # Start background probe in a daemon thread
        t = threading.Thread(target=self._run_probe_background, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"FTP Browser \u2014 {self.ip_address}:{self.port}")
        self.window.geometry("900x620")
        self.window.minsize(720, 480)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Banner panel (fixed 4 lines + scrollbar) at top of dialog
        banner_frame = tk.Frame(self.window)
        banner_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(banner_frame, text="Banner:").pack(anchor="w")

        banner_text_frame = tk.Frame(banner_frame)
        banner_text_frame.pack(fill=tk.X, pady=(3, 0))

        self.banner_text = tk.Text(
            banner_text_frame,
            height=4,
            wrap="word",
            state="normal",
        )
        if self.theme:
            self.theme.apply_to_widget(self.banner_text, "text_area")
        banner_vsb = ttk.Scrollbar(
            banner_text_frame, orient="vertical", command=self.banner_text.yview
        )
        self.banner_text.configure(yscrollcommand=banner_vsb.set)
        banner_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.banner_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        banner_value = self._server_banner.strip() or "(No FTP banner available)"
        self.banner_text.insert("1.0", banner_value)
        self.banner_text.configure(state="disabled")

        # Top frame — current path display
        top_frame = tk.Frame(self.window)
        top_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        tk.Label(top_frame, text="Path:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="/")
        path_label = tk.Label(
            top_frame, textvariable=self.path_var, anchor="w"
        )
        path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 10))

        # Button bar
        button_frame = tk.Frame(self.window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.btn_up = tk.Button(button_frame, text="\u2b06 Up", command=self._on_up)
        self.btn_refresh = tk.Button(
            button_frame, text="\U0001f504 Refresh", command=self._refresh
        )
        self.btn_view = tk.Button(
            button_frame, text="\U0001f441 View", command=self._on_view
        )
        self.btn_download = tk.Button(
            button_frame,
            text="\u2b07 Download to Quarantine",
            command=self._on_download,
        )
        self.btn_cancel = tk.Button(
            button_frame, text="Cancel", command=self._on_cancel, state=tk.DISABLED
        )
        for btn in (
            self.btn_up,
            self.btn_refresh,
            self.btn_view,
            self.btn_download,
            self.btn_cancel,
        ):
            btn.pack(side=tk.LEFT, padx=5)

        # Treeview
        tree_frame = tk.Frame(self.window)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        columns = ("name", "type", "size", "modified", "mtime_raw", "size_raw")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.heading("mtime_raw", text="")
        self.tree.heading("size_raw", text="")

        self.tree.column("name", width=280, minwidth=120)
        self.tree.column("type", width=80, minwidth=60)
        self.tree.column("size", width=110, minwidth=60)
        self.tree.column("modified", width=180, minwidth=100)
        self.tree.column("mtime_raw", width=0, stretch=False)
        self.tree.column("size_raw", width=0, stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.bind("<Double-1>", self._on_item_double_click)

        # Status bar
        self.status_var = tk.StringVar(value="Connecting...")
        status_label = tk.Label(
            self.window, textvariable=self.status_var, anchor="w"
        )
        status_label.pack(fill=tk.X, padx=10, pady=(0, 5))
        if self.theme:
            self.theme.apply_theme_to_application(self.window)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to(self, path: str) -> None:
        if self.busy:
            return
        # Clear any residual cancel from a previous operation before starting
        # a new intentional navigation.
        self._cancel_event.clear()
        self.busy = True
        self._set_buttons_busy(True)
        self._set_status("Loading...")
        self._nav_thread = threading.Thread(
            target=self._list_thread_fn, args=(path,), daemon=True
        )
        self._nav_thread.start()

    def _list_thread_fn(self, path: str) -> None:
        try:
            if self._navigator is None:
                nav = FtpNavigator(
                    connect_timeout=float(self.config["connect_timeout"]),
                    request_timeout=float(self.config["request_timeout"]),
                    max_entries=int(self.config["max_entries"]),
                    max_depth=int(self.config["max_depth"]),
                    max_path_length=int(self.config["max_path_length"]),
                    max_file_bytes=int(self.config["max_file_bytes"]),
                )
                nav.connect(self.ip_address, self.port)
                # Assign cancel_event AFTER connect() so connect() does not
                # inadvertently clear a user-initiated cancel signal.
                nav._cancel_event = self._cancel_event
                self._navigator = nav
            result = self._navigator.list_dir(path)
            try:
                self.window.after(0, self._on_list_done, path, result)
            except tk.TclError:
                pass
        except Exception as exc:
            try:
                self.window.after(0, self._on_list_error, str(exc))
            except tk.TclError:
                pass

    def _on_list_done(self, path: str, list_result) -> None:
        self._current_path = path
        self.path_var.set(path)
        self._populate_treeview(list_result)
        if list_result.warning:
            self._set_status(list_result.warning)
        else:
            count = len(list_result.entries)
            self._set_status(f"{count} item{'s' if count != 1 else ''}")
        self.busy = False
        self._set_buttons_busy(False)

    def _on_list_error(self, msg: str) -> None:
        self.busy = False
        self._set_buttons_busy(False)
        self._set_status(f"Error: {msg}")

    def _populate_treeview(self, list_result) -> None:
        self.tree.delete(*self.tree.get_children())
        for entry in list_result.entries:
            type_label = "dir" if entry.is_dir else "file"
            size_str = "" if entry.is_dir else _format_file_size(entry.size)
            modified_str = ""
            if entry.modified_time:
                try:
                    modified_str = datetime.utcfromtimestamp(
                        entry.modified_time
                    ).strftime("%Y-%m-%d %H:%M")
                except (OSError, OverflowError, ValueError):
                    pass
            self.tree.insert(
                "",
                "end",
                values=(
                    entry.name,
                    type_label,
                    size_str,
                    modified_str,
                    entry.modified_time or "",
                    entry.size or 0,
                ),
            )

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_item_double_click(self, _event) -> None:
        if self.busy:
            return
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        name, type_label = vals[0], vals[1]
        if type_label == "dir":
            target = str(PurePosixPath(self._current_path) / name)
            self._navigate_to(target)
        else:
            self._on_view()

    def _on_up(self) -> None:
        if self.busy or self._current_path == "/":
            return
        parent_path = str(PurePosixPath(self._current_path).parent)
        self._navigate_to(parent_path)

    def _refresh(self) -> None:
        if self.busy:
            return
        self._navigate_to(self._current_path)

    def _on_view(self) -> None:
        """Read selected file and open shared text/hex/image viewers."""
        sel = self.tree.selection()
        if not sel:
            return
        if len(sel) > 1:
            messagebox.showinfo("View", "Select only one file to view.")
            return
        vals = self.tree.item(sel[0], "values")
        name, type_label = vals[0], vals[1]
        if type_label != "file":
            messagebox.showinfo("View", "Select a file (not a directory) to view.")
            return
        if self._navigator is None:
            messagebox.showinfo("View", "Not connected.")
            return

        remote_path = str(PurePosixPath(self._current_path) / name)
        try:
            size_raw = int(vals[5]) if len(vals) > 5 and vals[5] else 0
        except (ValueError, IndexError):
            size_raw = 0

        suffix = Path(name).suffix.lower()
        is_image = suffix in IMAGE_EXTS

        viewer_cfg = self.config.get("viewer", {}) or {}
        max_view_mb = int(viewer_cfg.get("max_view_size_mb", 5) or 5)
        max_image_mb = int(viewer_cfg.get("max_image_size_mb", max_view_mb) or max_view_mb)
        max_image_pixels = int(viewer_cfg.get("max_image_pixels", 20_000_000) or 20_000_000)
        max_view_bytes = (max_image_mb if is_image else max_view_mb) * 1024 * 1024

        # Pre-flight file size guard when size is known from listing.
        if size_raw and size_raw > max_view_bytes:
            limit_mb = max_image_mb if is_image else max_view_mb
            messagebox.showerror(
                "View Error",
                f"{name} is {_format_file_size(size_raw)}, exceeding the {limit_mb} MB view limit.",
            )
            return

        self._start_view_thread(
            remote_path=remote_path,
            display_name=name,
            max_bytes=max_view_bytes,
            is_image=is_image,
            max_image_pixels=max_image_pixels,
            size_raw=size_raw,
        )

    def _start_view_thread(
        self,
        remote_path: str,
        display_name: str,
        max_bytes: int,
        is_image: bool,
        max_image_pixels: int,
        size_raw: int,
    ) -> None:
        """Read remote file in background and open shared viewer on main thread."""

        def _read_thread() -> None:
            try:
                self.window.after(0, self._set_status, f"Reading {display_name}...")
                result = self._navigator.read_file(remote_path, max_bytes=max_bytes)
                if is_image:
                    self.window.after(
                        0,
                        self._open_image_viewer,
                        remote_path,
                        result.data,
                        size_raw or result.size,
                        result.truncated,
                        max_image_pixels,
                    )
                else:
                    self.window.after(
                        0,
                        self._open_viewer,
                        remote_path,
                        result.data,
                        size_raw or result.size,
                    )
            except Exception as exc:
                try:
                    self.window.after(
                        0,
                        lambda exc=exc: messagebox.showerror(
                            "View Error", f"Could not read file:\n{exc}"
                        ),
                    )
                except tk.TclError:
                    pass

        threading.Thread(target=_read_thread, daemon=True).start()

    def _open_viewer(self, remote_path: str, content: bytes, file_size: int) -> None:
        """Open shared text/hex file viewer used by SMB browser."""
        display_path = f"{self.ip_address}/ftp_root{remote_path}"

        def save_callback() -> None:
            self._start_download_thread([(remote_path, file_size)])

        open_file_viewer(
            parent=self.window,
            file_path=display_path,
            content=content,
            file_size=file_size,
            theme=self.theme,
            on_save_callback=save_callback,
        )
        self._set_status(f"Viewing {remote_path}")

    def _open_image_viewer(
        self,
        remote_path: str,
        content: bytes,
        file_size: int,
        truncated: bool,
        max_image_pixels: int,
    ) -> None:
        """Open shared image viewer used by SMB browser."""
        display_path = f"{self.ip_address}/ftp_root{remote_path}"

        def save_callback() -> None:
            self._start_download_thread([(remote_path, file_size)])

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
        except Exception as exc:
            self._set_status(f"View failed: {exc}")
            try:
                messagebox.showerror("View Error", str(exc), parent=self.window)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download(self) -> None:
        if self.busy:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Download", "Select one or more files to download.")
            return

        file_list: List = []
        limit = int(self.config["max_file_bytes"])
        for item_id in sel:
            vals = self.tree.item(item_id, "values")
            name, type_label = vals[0], vals[1]
            try:
                size_raw = int(vals[5]) if vals[5] else 0
            except (ValueError, IndexError):
                size_raw = 0
            if type_label == "dir":
                messagebox.showinfo(
                    "Download",
                    f"'{name}' is a directory. Folder download is not supported in this version.\n\n"
                    "Select individual files to download.",
                )
                return
            if size_raw and size_raw > limit:
                size_mb = size_raw / (1024 * 1024)
                messagebox.showerror(
                    "File too large",
                    f"{name} is {size_mb:.1f} MB, exceeding the "
                    f"{limit // (1024 * 1024)} MB limit.\n\n"
                    f"Adjust ftp_browser.max_file_bytes in config to change this limit.",
                )
                return
            remote_path = str(PurePosixPath(self._current_path) / name)
            file_list.append((remote_path, size_raw))

        if not file_list:
            return

        self._start_download_thread(file_list)

    def _start_download_thread(self, file_list: List) -> None:
        self._cancel_event.clear()
        self.btn_cancel.config(state=tk.NORMAL)
        self.btn_download.config(state=tk.DISABLED)
        self.busy = True
        self._download_thread = threading.Thread(
            target=self._download_thread_fn, args=(file_list,), daemon=True
        )
        self._download_thread.start()

    def _download_thread_fn(self, file_list: List) -> None:
        quarantine_dir = build_quarantine_path(
            ip_address=self.ip_address,
            share_name="ftp_root",
            base_path=Path(self.config["quarantine_base"]).expanduser(),
            purpose="ftp",
        )
        success_count = 0
        for remote_path, _ in file_list:
            if self._cancel_event.is_set():
                break
            filename = PurePosixPath(remote_path).name
            try:
                self.window.after(0, self._set_status, f"Downloading {filename}...")
            except tk.TclError:
                return
            try:
                result = self._navigator.download_file(  # type: ignore[union-attr]
                    remote_path=remote_path,
                    dest_dir=quarantine_dir,
                    progress_callback=lambda done, total: (
                        self.window.after(
                            0,
                            self._set_status,
                            f"Downloading... {done // 1024} KB",
                        )
                    ),
                )
                log_quarantine_event(
                    quarantine_dir,
                    f"Downloaded {remote_path} -> {result.saved_path}",
                )
                success_count += 1
            except FtpCancelledError:
                try:
                    self.window.after(0, self._set_status, "Download cancelled.")
                except tk.TclError:
                    pass
                break
            except FtpFileTooLargeError as exc:
                # Safety-net: pre-flight should catch this first
                try:
                    self.window.after(
                        0, self._set_status, f"Skipped (too large): {filename}"
                    )
                except tk.TclError:
                    pass
            except FileExistsError:
                try:
                    self.window.after(
                        0, self._set_status, f"Skipped (already exists): {filename}"
                    )
                except tk.TclError:
                    pass
            except Exception as exc:
                try:
                    self.window.after(
                        0, self._set_status, f"Error downloading {filename}: {exc}"
                    )
                except tk.TclError:
                    pass

        try:
            self.window.after(
                0,
                self._on_download_done,
                success_count,
                len(file_list),
                str(quarantine_dir),
            )
        except tk.TclError:
            pass

    def _on_download_done(self, success: int, total: int, quarantine_path: str) -> None:
        self.busy = False
        self._set_buttons_busy(False)
        self.btn_cancel.config(state=tk.DISABLED)
        self._set_status(f"Downloaded {success}/{total} file(s) \u2192 {quarantine_path}")
        if success > 0:
            messagebox.showinfo(
                "Download complete",
                f"Downloaded {success}/{total} file(s) to quarantine:\n{quarantine_path}",
            )

    # ------------------------------------------------------------------
    # Cancel / Close
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        if self._navigator is not None:
            self._navigator.cancel()
        self.btn_cancel.config(state=tk.DISABLED)
        self._set_status("Cancelling...")

    def _on_close(self) -> None:
        self._cancel_event.set()
        if self._navigator is not None:
            try:
                self._navigator.disconnect()
            except Exception:
                pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Status and button helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        """Set status bar text. Must be called on the main thread."""
        try:
            self.status_var.set(msg)
        except tk.TclError:
            pass

    def _set_buttons_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self.btn_up, self.btn_refresh, self.btn_view, self.btn_download):
            try:
                btn.config(state=state)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Background probe
    # ------------------------------------------------------------------

    def _run_probe_background(self) -> None:
        """Run probe in background; non-fatal if it fails."""
        try:
            snapshot = run_ftp_probe(
                ip=self.ip_address,
                port=self.port,
                max_entries=int(self.config["max_entries"]),
                connect_timeout=int(self.config["connect_timeout"]),
                request_timeout=int(self.config["request_timeout"]),
                cancel_event=self._cancel_event,
                progress_callback=lambda msg: (
                    self.window.after(0, self._set_status, msg)
                ),
            )
            try:
                self.window.after(0, self._apply_probe_snapshot, snapshot)
            except tk.TclError:
                pass
        except Exception:
            pass

    def _apply_probe_snapshot(self, snapshot: Optional[dict]) -> None:
        """Apply snapshot data to status bar (non-critical)."""
        if snapshot is None:
            return
        errors = snapshot.get("errors", [])
        if errors:
            try:
                self._set_status(f"Probe warning: {errors[0]}")
            except Exception:
                pass
