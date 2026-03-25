"""
Browser window base class and protocol-specific browser windows for FTP, HTTP, and SMB.

UnifiedBrowserCore provides the common UI/controller machinery.  Protocol-specific
behaviour is supplied via four adapter hooks that subclasses must implement:

    _adapt_window_title()         -> str
    _adapt_banner_label()         -> str
    _adapt_banner_placeholder()   -> str
    _adapt_setup_treeview(tree_frame) -> None  (creates self.tree + scrollbar)

FtpBrowserWindow, HttpBrowserWindow, and SmbBrowserWindow are all defined in this
module.  FTP/HTTP are accessed via open_ftp_http_browser(); SMB via open_smb_browser().

Methods kept per-protocol (NOT in UnifiedBrowserCore):

  _on_cancel, _on_close
      FTP disconnects the session (navigator.disconnect()); HTTP/SMB cancel only.

  _list_thread_fn
      FTP lazy-connect/cancel ordering is fragile; kept verbatim per protocol.

  _run_probe_background, _apply_probe_snapshot
      Different probe functions and error payload shapes per protocol.

  _populate_treeview, _on_item_double_click, _on_up, _on_view,
  _on_download, _download_thread_fn
      Column structure or path-resolution semantics differ per protocol.

Heavy protocol imports (shared.ftp_browser, shared.http_browser, shared.smb_browser)
are kept as per-method lazy imports — all three pull impacket unconditionally, so
module-level placement would load impacket at import time.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:
    from utils.dialog_helpers import ensure_dialog_focus  # type: ignore[no-redef]


def open_file_viewer(*args: Any, **kwargs: Any) -> Any:
    """Lazy-load file viewer to avoid import-time coupling in browser/probe paths."""
    try:
        from gui.components.file_viewer_window import open_file_viewer as _open_file_viewer
    except ImportError:
        from file_viewer_window import open_file_viewer as _open_file_viewer  # type: ignore[no-redef]
    return _open_file_viewer(*args, **kwargs)


def open_image_viewer(*args: Any, **kwargs: Any) -> Any:
    """Lazy-load image viewer to avoid import-time failures when Pillow/ImageTk is unavailable."""
    try:
        from gui.components.image_viewer_window import open_image_viewer as _open_image_viewer
    except ImportError:
        from image_viewer_window import open_image_viewer as _open_image_viewer  # type: ignore[no-redef]
    return _open_image_viewer(*args, **kwargs)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# Shared helpers (FTP config loader, HTTP config loader, file-size formatter)
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


def _load_http_browser_config(config_path: Optional[str]) -> Dict:
    defaults: Dict[str, Any] = {
        "max_entries": 5000,
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
        defaults.update(data.get("http_browser", {}))
    except Exception:
        pass
    return defaults


def _load_smb_browser_config(config_path: Optional[str]) -> Dict:
    defaults: Dict[str, Any] = {
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
        "max_batch_files": 50,
        "quarantine_root": "~/.smbseek/quarantine",
        "viewer": {
            "max_view_size_mb": 5,
            "max_image_size_mb": 15,
            "max_image_pixels": 20_000_000,
            "default_encoding": "utf-8",
            "hex_bytes_per_row": 16,
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


def _extract_smb_banner(shodan_data) -> str:
    """Best-effort extract a display string from a Shodan JSON blob.

    Fallback order: SMB/139 service .data → first service .data → org → isp →
    hostnames[0] → "" (caller shows placeholder).
    Never raises; returns "" on any error or missing data.
    """
    if not shodan_data:
        return ""
    try:
        d = json.loads(shodan_data) if isinstance(shodan_data, str) else shodan_data
    except Exception:
        return ""
    if not isinstance(d, dict):
        return ""
    try:
        services = d.get("data") or []
        smb_svc = next((s for s in services if s.get("port") in (445, 139)), None)
        if smb_svc:
            raw = str(smb_svc.get("data") or "").strip()
            if raw:
                return raw[:500]
        if services:
            raw = str(services[0].get("data") or "").strip()
            if raw:
                return raw[:500]
        for key in ("org", "isp"):
            val = str(d.get(key) or "").strip()
            if val:
                return val
        hostnames = d.get("hostnames") or []
        if hostnames:
            return str(hostnames[0]).strip()
    except Exception:
        pass
    return ""


class UnifiedBrowserCore:
    """Shared UI/controller base for FTP and HTTP browser windows.

    Subclasses must implement the four adapter hooks.  All other methods
    in this class are verbatim copies of code that was previously duplicated
    across FtpBrowserWindow and HttpBrowserWindow.
    """

    # ------------------------------------------------------------------
    # Adapter hooks — implement in each protocol subclass
    # ------------------------------------------------------------------

    def _adapt_window_title(self) -> str:
        raise NotImplementedError

    def _adapt_banner_label(self) -> str:
        raise NotImplementedError

    def _adapt_banner_placeholder(self) -> str:
        raise NotImplementedError

    def _adapt_setup_treeview(self, tree_frame: tk.Frame) -> None:
        """Create self.tree (Treeview) and its scrollbar inside tree_frame."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title(self._adapt_window_title())
        self.window.geometry("900x620")
        self.window.minsize(720, 480)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Banner panel (fixed 4 lines + scrollbar) at top of dialog
        banner_frame = tk.Frame(self.window)
        banner_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(banner_frame, text=self._adapt_banner_label()).pack(anchor="w")

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

        banner_value = self._server_banner.strip() or self._adapt_banner_placeholder()
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

        # Treeview (protocol-specific columns via adapter hook)
        tree_frame = tk.Frame(self.window)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))
        self._adapt_setup_treeview(tree_frame)
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

    def _refresh(self) -> None:
        if self.busy:
            return
        self._navigate_to(self._current_path)

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def _start_view_thread(
        self,
        remote_path: str,
        display_name: str,
        max_bytes: int,
        is_image: bool,
        max_image_pixels: int,
        size_raw: int = 0,
    ) -> None:
        """Read remote file in background; dispatch to per-protocol viewer on main thread.

        Calls self._open_viewer or self._open_image_viewer only — never
        open_file_viewer / open_image_viewer directly.  Those imports live in
        the protocol modules to remain valid monkeypatch targets in tests.
        """

        def _read_thread() -> None:
            try:
                self.window.after(0, self._set_status, f"Reading {display_name}...")
                result = self._navigator.read_file(remote_path, max_bytes=max_bytes)
                file_size = size_raw or result.size
                if is_image:
                    self.window.after(
                        0,
                        self._open_image_viewer,
                        remote_path,
                        result.data,
                        file_size,
                        result.truncated,
                        max_image_pixels,
                    )
                else:
                    self.window.after(
                        0,
                        self._open_viewer,
                        remote_path,
                        result.data,
                        file_size,
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

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _start_download_thread(self, file_list) -> None:
        self._cancel_event.clear()
        self.btn_cancel.config(state=tk.NORMAL)
        self.btn_download.config(state=tk.DISABLED)
        self.busy = True
        self._download_thread = threading.Thread(
            target=self._download_thread_fn, args=(file_list,), daemon=True
        )
        self._download_thread.start()

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


# ---------------------------------------------------------------------------
# FtpBrowserWindow
# ---------------------------------------------------------------------------

class FtpBrowserWindow(UnifiedBrowserCore):
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
        from gui.utils.probe_cache_dispatch import load_probe_result_for_host
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
        self._navigator: Optional["FtpNavigator"] = None
        self._nav_thread: Optional[threading.Thread] = None
        self._download_thread: Optional[threading.Thread] = None
        self.busy: bool = False

        self._build_window()

        # Apply cached probe snapshot if available
        self._apply_probe_snapshot(load_probe_result_for_host(ip_address, "F"))

        # Start navigating to root
        self._navigate_to("/")

        # Start background probe in a daemon thread
        t = threading.Thread(target=self._run_probe_background, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Adapter hooks (required by UnifiedBrowserCore)
    # ------------------------------------------------------------------

    def _adapt_window_title(self) -> str:
        return f"FTP Browser \u2014 {self.ip_address}:{self.port}"

    def _adapt_banner_label(self) -> str:
        return "Banner:"

    def _adapt_banner_placeholder(self) -> str:
        return "(No FTP banner available)"

    def _adapt_setup_treeview(self, tree_frame: tk.Frame) -> None:
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

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _list_thread_fn(self, path: str) -> None:
        from shared.ftp_browser import FtpNavigator
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

    def _populate_treeview(self, list_result) -> None:
        self.tree.delete(*self.tree.get_children())
        sorted_entries = sorted(
            list_result.entries,
            key=lambda e: (0 if e.is_dir else 1, e.name.casefold()),
        )
        for entry in sorted_entries:
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

    def _download_thread_fn(self, file_list: List) -> None:
        from shared.ftp_browser import FtpCancelledError, FtpFileTooLargeError
        from shared.quarantine import build_quarantine_path, log_quarantine_event
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
    # Background probe
    # ------------------------------------------------------------------

    def _run_probe_background(self) -> None:
        """Run probe in background; non-fatal if it fails."""
        from gui.utils.ftp_probe_runner import run_ftp_probe
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


# ---------------------------------------------------------------------------
# HttpBrowserWindow
# ---------------------------------------------------------------------------

class HttpBrowserWindow(UnifiedBrowserCore):
    """Tkinter toplevel window for HTTP directory-index navigation and file download."""

    def __init__(
        self,
        parent: tk.Widget,
        ip_address: str,
        port: int = 80,
        scheme: str = "http",
        banner: Optional[str] = None,
        config_path: Optional[str] = None,
        db_reader=None,
        theme=None,
        settings_manager=None,
    ) -> None:
        from shared.http_browser import HttpNavigator
        from gui.utils.probe_cache_dispatch import load_probe_result_for_host
        self.parent = parent
        self.ip_address = ip_address
        self.port = port
        self.scheme = scheme
        self.db_reader = db_reader
        self.theme = theme
        self.settings_manager = settings_manager
        self.config = _load_http_browser_config(config_path)
        self._server_banner = str(banner or "")

        self._current_path: str = "/"
        self._cancel_event = threading.Event()
        self._navigator: HttpNavigator = HttpNavigator(
            ip=ip_address,
            port=port,
            scheme=scheme,
            allow_insecure_tls=True,
            connect_timeout=float(self.config["connect_timeout"]),
            request_timeout=float(self.config["request_timeout"]),
            max_entries=int(self.config["max_entries"]),
            max_file_bytes=int(self.config["max_file_bytes"]),
        )
        # Share the window-level cancel_event with the navigator
        self._navigator._cancel_event = self._cancel_event

        self._nav_thread: Optional[threading.Thread] = None
        self._download_thread: Optional[threading.Thread] = None
        self.busy: bool = False

        # Maps treeview iid -> absolute path for safe routing
        self._path_map: Dict[str, str] = {}

        self._build_window()

        # Apply cached probe snapshot if available
        self._apply_probe_snapshot(load_probe_result_for_host(ip_address, "H", port=port))

        # Start navigating to root
        self._navigate_to("/")

        # Start background probe in a daemon thread
        t = threading.Thread(target=self._run_probe_background, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Adapter hooks (required by UnifiedBrowserCore)
    # ------------------------------------------------------------------

    def _adapt_window_title(self) -> str:
        return f"HTTP Browser \u2014 {self.scheme}://{self.ip_address}:{self.port}"

    def _adapt_banner_label(self) -> str:
        return "Banner/Title:"

    def _adapt_banner_placeholder(self) -> str:
        return "(No HTTP banner available)"

    def _adapt_setup_treeview(self, tree_frame: tk.Frame) -> None:
        # Treeview — name col shows basename; abs_path in hidden path_raw col
        columns = ("name", "type", "size", "modified", "path_raw")
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
        self.tree.heading("path_raw", text="")

        self.tree.column("name", width=320, minwidth=120)
        self.tree.column("type", width=80, minwidth=60)
        self.tree.column("size", width=110, minwidth=60)
        self.tree.column("modified", width=180, minwidth=100)
        self.tree.column("path_raw", width=0, stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _list_thread_fn(self, path: str) -> None:
        try:
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

    def _populate_treeview(self, list_result) -> None:
        """Insert entries into treeview; populate _path_map with iid -> abs_path."""
        self.tree.delete(*self.tree.get_children())
        self._path_map.clear()
        sorted_entries = sorted(
            list_result.entries,
            key=lambda e: (
                0 if e.is_dir else 1,
                (PurePosixPath(e.name.rstrip("/")).name or e.name).casefold(),
            ),
        )
        for entry in sorted_entries:
            abs_path = entry.name  # Entry.name holds the full abs path
            display_name = PurePosixPath(abs_path.rstrip("/")).name or abs_path
            type_label = "dir" if entry.is_dir else "file"
            iid = self.tree.insert(
                "",
                "end",
                values=(display_name, type_label, "\u2014", "\u2014", abs_path),
            )
            self._path_map[iid] = abs_path

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_item_double_click(self, _event) -> None:
        if self.busy:
            return
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        vals = self.tree.item(iid, "values")
        type_label = vals[1]
        abs_path = self._path_map.get(iid, "")
        if not abs_path:
            return
        if type_label == "dir":
            self._navigate_to(abs_path)
        else:
            self._on_view()

    def _on_up(self) -> None:
        if self.busy or self._current_path == "/":
            return
        parent_path = str(PurePosixPath(self._current_path).parent)
        if not parent_path or parent_path == self._current_path:
            parent_path = "/"
        self._navigate_to(parent_path)

    def _on_view(self) -> None:
        """Read selected file and open shared text/image viewer."""
        sel = self.tree.selection()
        if not sel:
            return
        if len(sel) > 1:
            messagebox.showinfo("View", "Select only one file to view.")
            return
        iid = sel[0]
        vals = self.tree.item(iid, "values")
        type_label = vals[1]
        if type_label != "file":
            messagebox.showinfo("View", "Select a file (not a directory) to view.")
            return

        abs_path = self._path_map.get(iid, "")
        if not abs_path:
            messagebox.showinfo("View", "Path not found.")
            return

        display_name = vals[0]
        suffix = Path(display_name).suffix.lower()
        is_image = suffix in IMAGE_EXTS

        viewer_cfg = self.config.get("viewer", {}) or {}
        max_view_mb = int(viewer_cfg.get("max_view_size_mb", 5) or 5)
        max_image_mb = int(viewer_cfg.get("max_image_size_mb", max_view_mb) or max_view_mb)
        max_image_pixels = int(viewer_cfg.get("max_image_pixels", 20_000_000) or 20_000_000)
        max_view_bytes = (max_image_mb if is_image else max_view_mb) * 1024 * 1024

        self._start_view_thread(
            remote_path=abs_path,
            display_name=display_name,
            max_bytes=max_view_bytes,
            is_image=is_image,
            max_image_pixels=max_image_pixels,
        )

    def _open_viewer(self, remote_path: str, content: bytes, file_size: int) -> None:
        display_path = f"{self.scheme}://{self.ip_address}:{self.port}{remote_path}"

        def save_callback() -> None:
            self._start_download_thread([(remote_path, 0)])

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
        """Open shared image viewer for raster image files."""
        display_path = f"{self.scheme}://{self.ip_address}:{self.port}{remote_path}"

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

        file_list = []
        for iid in sel:
            vals = self.tree.item(iid, "values")
            type_label = vals[1]
            if type_label == "dir":
                messagebox.showinfo(
                    "Download",
                    f"'{vals[0]}' is a directory. Folder download is not supported.\n\n"
                    "Select individual files to download.",
                )
                return
            abs_path = self._path_map.get(iid, "")
            if abs_path:
                file_list.append(abs_path)

        if not file_list:
            return

        self._start_download_thread([(p, 0) for p in file_list])

    def _download_thread_fn(self, file_list) -> None:
        from shared.http_browser import HttpCancelledError, HttpFileTooLargeError
        from shared.quarantine import build_quarantine_path, log_quarantine_event
        quarantine_dir = build_quarantine_path(
            ip_address=self.ip_address,
            share_name="http_root",
            base_path=Path(self.config["quarantine_base"]).expanduser(),
            purpose="http",
        )
        success_count = 0
        for remote_path, _ in file_list:
            if self._cancel_event.is_set():
                break
            filename = PurePosixPath(remote_path).name or remote_path
            try:
                self.window.after(0, self._set_status, f"Downloading {filename}...")
            except tk.TclError:
                return
            try:
                result = self._navigator.download_file(
                    remote_path=remote_path,
                    dest_dir=quarantine_dir,
                    progress_callback=lambda done, total: (
                        self.window.after(
                            0, self._set_status, f"Downloading... {done // 1024} KB"
                        )
                    ),
                )
                log_quarantine_event(
                    quarantine_dir,
                    f"Downloaded {remote_path} -> {result.saved_path}",
                )
                success_count += 1
            except HttpCancelledError:
                try:
                    self.window.after(0, self._set_status, "Download cancelled.")
                except tk.TclError:
                    pass
                break
            except HttpFileTooLargeError:
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

    # ------------------------------------------------------------------
    # Cancel / Close
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self._navigator.cancel()
        self.btn_cancel.config(state=tk.DISABLED)
        self._set_status("Cancelling...")

    def _on_close(self) -> None:
        self._cancel_event.set()
        self._navigator.cancel()
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Background probe
    # ------------------------------------------------------------------

    def _run_probe_background(self) -> None:
        """Run HTTP probe in background; non-fatal if it fails."""
        from gui.utils.http_probe_runner import run_http_probe
        try:
            snapshot = run_http_probe(
                ip=self.ip_address,
                port=self.port,
                scheme=self.scheme,
                allow_insecure_tls=True,
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
                first_err = errors[0]
                msg = (
                    first_err.get("message", str(first_err))
                    if isinstance(first_err, dict)
                    else str(first_err)
                )
                self._set_status(f"Probe warning: {msg}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# SmbBrowserWindow
# ---------------------------------------------------------------------------


class SmbBrowserWindow(UnifiedBrowserCore):
    """Tkinter window for SMB navigation + download (unified architecture)."""

    def __init__(
        self,
        parent: tk.Widget,
        ip_address: str,
        shares: List[str],
        auth_method: Optional[str],
        config_path: Optional[str],
        db_reader=None,
        theme=None,
        settings_manager=None,
        share_credentials: Optional[Dict[str, Dict[str, str]]] = None,
        on_extracted=None,
        banner: str = "",
    ) -> None:
        # Lazy-import heavy SMB deps (shared.smb_browser -> impacket)
        from shared.smb_browser import SMBNavigator
        try:
            from gui.components.server_list_window import details as detail_helpers
        except ImportError:
            from server_list_window import details as detail_helpers  # type: ignore[no-redef]

        self.parent = parent
        self.ip_address = ip_address
        self._server_banner = str(banner or "")
        self.shares = shares
        self.auth_method = auth_method or ""
        self.db_reader = db_reader
        self.theme = theme
        self.config_path = config_path
        self.config = _load_smb_browser_config(config_path)
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
                self.download_workers = int(self.settings_manager.get_setting(
                    "file_browser.download_worker_count", self.download_workers
                ))
                self.download_large_mb = int(self.settings_manager.get_setting(
                    "file_browser.download_large_file_mb", self.download_large_mb
                ))
                self.download_workers = max(1, min(3, self.download_workers))
            except Exception:
                pass

        creds = detail_helpers._derive_credentials(self.auth_method)
        self.username, self.password = creds
        self.folder_defaults = self.config.get("folder_download", {})
        self.max_batch_files = int(self.config.get("max_batch_files", 50))
        self.current_share: Optional[str] = None
        self.current_path = "\\"
        self.pending_path: Optional[str] = None
        self.list_thread: Optional[threading.Thread] = None
        self.download_thread: Optional[threading.Thread] = None
        self.busy = False
        self._at_virtual_root: bool = True
        self._entering_share: bool = False

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
        self._go_to_virtual_root()

    # ------------------------------------------------------------------
    # Adapter hooks (satisfy UnifiedBrowserCore interface)
    # ------------------------------------------------------------------

    def _adapt_window_title(self) -> str:
        return f"SMB File Browser - {self.ip_address}"

    def _adapt_banner_label(self) -> str:
        return "Shodan:"

    def _adapt_banner_placeholder(self) -> str:
        return "(No Shodan banner available)"

    def _adapt_setup_treeview(self, tree_frame: tk.Frame) -> None:
        columns = ("name", "type", "size", "modified", "mtime_raw", "size_raw")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.column("name", width=260, anchor="w")
        self.tree.column("type", width=90, anchor="w")
        self.tree.column("size", width=120, anchor="e")
        self.tree.column("modified", width=180, anchor="w")
        self.tree.column("mtime_raw", width=0, stretch=False)
        self.tree.column("size_raw", width=0, stretch=False)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------------
    # UI setup (overrides base — SMB layout differs: share selector + tuning)
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"SMB File Browser - {self.ip_address}")
        self.window.geometry("900x620")
        self.window.minsize(720, 480)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Banner panel (4 lines + scrollbar) — shows Shodan metadata when available
        banner_frame = tk.Frame(self.window)
        banner_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        tk.Label(banner_frame, text="Shodan:").pack(anchor="w")
        banner_text_frame = tk.Frame(banner_frame)
        banner_text_frame.pack(fill=tk.X, pady=(3, 0))
        self.banner_text = tk.Text(
            banner_text_frame, height=4, wrap="word", state="normal"
        )
        if self.theme:
            self.theme.apply_to_widget(self.banner_text, "text_area")
        banner_vsb = ttk.Scrollbar(
            banner_text_frame, orient="vertical", command=self.banner_text.yview
        )
        self.banner_text.configure(yscrollcommand=banner_vsb.set)
        banner_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.banner_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        banner_value = self._server_banner.strip() or "(No Shodan banner available)"
        self.banner_text.insert("1.0", banner_value)
        self.banner_text.configure(state="disabled")

        top_frame = tk.Frame(self.window)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top_frame, text="Path:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="\\")
        self.path_label = tk.Label(top_frame, textvariable=self.path_var, anchor="w")
        self.path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 10))

        button_frame = tk.Frame(self.window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.btn_up = tk.Button(button_frame, text="\u2b06 Up", command=self._on_up)
        self.btn_refresh = tk.Button(button_frame, text="\U0001f504 Refresh", command=self._refresh)
        self.btn_view = tk.Button(button_frame, text="\U0001f441 View", command=self._on_view)
        self.btn_download = tk.Button(
            button_frame, text="\u2b07 Download to Quarantine", command=self._on_download
        )
        self.btn_cancel = tk.Button(
            button_frame, text="Cancel", command=self._on_cancel, state=tk.DISABLED
        )

        for btn in (self.btn_up, self.btn_refresh, self.btn_view, self.btn_download, self.btn_cancel):
            btn.pack(side=tk.LEFT, padx=5)

        # Download tuning controls (workers + large threshold)
        tuning_frame = tk.Frame(self.window)
        tuning_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        tk.Label(tuning_frame, text="Workers").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_var = tk.IntVar(value=self.download_workers)
        workers_spin = tk.Spinbox(
            tuning_frame, from_=1, to=3, width=3, textvariable=self.workers_var,
            command=self._persist_tuning,
        )
        workers_spin.pack(side=tk.LEFT)

        tk.Label(tuning_frame, text="Large file MB").pack(side=tk.LEFT, padx=(10, 4))
        self.large_mb_var = tk.IntVar(value=self.download_large_mb)
        large_spin = tk.Spinbox(
            tuning_frame, from_=1, to=1024, width=5, textvariable=self.large_mb_var,
            command=self._persist_tuning,
        )
        large_spin.pack(side=tk.LEFT)

        # Treeview (protocol-specific columns via adapter hook)
        tree_frame = tk.Frame(self.window)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self._adapt_setup_treeview(tree_frame)
        self.tree.bind("<Double-1>", self._on_item_double_click)

        self.status_var = tk.StringVar(value="Ready.")
        status = tk.Label(self.window, textvariable=self.status_var, anchor="w")
        status.pack(fill=tk.X, padx=10, pady=(0, 10))
        if self.theme:
            self.theme.apply_theme_to_application(self.window)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_to_virtual_root(self, status_override: str = "") -> None:
        self._at_virtual_root = True
        self._entering_share = False
        self._disconnect()
        self.current_share = None
        self.current_path = "\\"
        self.pending_path = None
        self.path_var.set("\\")
        self._populate_virtual_root(status_override=status_override)
        self._update_action_buttons()

    def _populate_virtual_root(self, status_override: str = "") -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        unique_shares = sorted(set(self.shares))
        for share_name in unique_shares:
            self.tree.insert("", "end", values=(share_name, "dir", "", "", "", ""))
        if status_override:
            self._set_status(status_override)
        elif unique_shares:
            self._set_status(f"{len(unique_shares)} accessible share(s).")
        else:
            self._set_status("No accessible shares found for this host.")

    def _enter_share(self, share_name: str) -> None:
        if self.busy:
            return
        if self.share_credentials:
            creds = self.share_credentials.get(share_name)
            if creds:
                self.username = creds.get("username") or self.username
                self.password = creds.get("password") or self.password
        self._disconnect()
        self.current_share = share_name
        self._at_virtual_root = False
        self._entering_share = True
        self._set_path("\\")
        self._update_action_buttons()
        self._refresh()

    def _update_action_buttons(self) -> None:
        if self.busy:
            return  # busy state owns button enables; don't interfere
        at_root = self._at_virtual_root
        for btn in (self.btn_up, self.btn_view, self.btn_download):
            if btn and btn.winfo_exists():
                btn.configure(state=tk.DISABLED if at_root else tk.NORMAL)

    def _on_up(self) -> None:
        if self._at_virtual_root:
            return
        if self.current_path in ("\\", "/", ""):
            self._go_to_virtual_root()
            return
        parts = [p for p in self.current_path.split("\\") if p]
        new_path = "\\" + "\\".join(parts[:-1]) if parts[:-1] else "\\"
        self._navigate_to(new_path)

    def _refresh(self) -> None:
        if self.busy:
            return
        if self._at_virtual_root:
            self._populate_virtual_root()
            return
        if not self.current_share:
            return
        self.pending_path = self.current_path
        self._start_list_thread(self.current_path)

    def _on_item_double_click(self, _event=None) -> None:
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

        if self._at_virtual_root:
            if name:
                self._enter_share(name)
            return

        if type_label == "dir":
            target_path = self._join_path(self.current_path, name)
            self._navigate_to(target_path)
        elif type_label == "file":
            self._on_view()

    def _navigate_to(self, target_path: str) -> None:
        if self.busy or not self.current_share:
            return
        self.pending_path = target_path
        self._start_list_thread(target_path)

    def _set_path(self, path: str) -> None:
        self.current_path = path
        if self._at_virtual_root or not self.current_share:
            self.path_var.set(path)
        else:
            self.path_var.set(f"{self.current_share}{path}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_download(self) -> None:
        if self.busy or not self.current_share:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select one or more files to download.", parent=self.window)
            return

        files: List[Tuple[str, Optional[float], int]] = []
        dirs: List[str] = []
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
        self.navigator.cancel()
        if self.download_cancel_event:
            self.download_cancel_event.set()
        self._set_status("Cancellation requested…")
        self.btn_cancel.configure(state=tk.DISABLED)

    def _on_view(self) -> None:
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

        size_raw = 0
        if len(values) > 5:
            try:
                size_raw = int(values[5])
            except (ValueError, TypeError):
                size_raw = 0
        remote_path = self._join_path(self.current_path, name)

        suffix = Path(name).suffix.lower()
        is_image = suffix in IMAGE_EXTS

        viewer_cfg = self.config.get("viewer", {}) or {}
        max_view_mb = viewer_cfg.get("max_view_size_mb", 5)
        max_image_mb = viewer_cfg.get("max_image_size_mb", max_view_mb)
        max_image_pixels = viewer_cfg.get("max_image_pixels", 20_000_000)
        max_view_bytes = (max_image_mb if is_image else max_view_mb) * 1024 * 1024

        if size_raw > max_view_bytes:
            if is_image:
                if not self._confirm_image_oversize(name, size_raw, max_view_mb if not is_image else max_image_mb):
                    return
            else:
                if not self._show_size_warning_dialog(name, size_raw, max_view_mb):
                    return
                max_view_bytes = 1024 * 1024 * 1024

        self._start_view_thread(remote_path, name, max_view_bytes, is_image=is_image, max_image_pixels=max_image_pixels)

    # ------------------------------------------------------------------
    # Size warning dialogs
    # ------------------------------------------------------------------

    def _show_size_warning_dialog(self, filename: str, file_size: int, max_mb: int) -> bool:
        dialog = tk.Toplevel(self.window)
        dialog.title("File Too Large")
        dialog.geometry("450x180")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")

        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() // 2) - 225
        y = self.window.winfo_y() + (self.window.winfo_height() // 2) - 90
        dialog.geometry(f"+{x}+{y}")

        result = {"proceed": False}

        msg_frame = tk.Frame(dialog)
        self.theme.apply_to_widget(msg_frame, "main_window")
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        warning_label = tk.Label(
            msg_frame,
            text=(
                f'The file "{filename}" ({_format_file_size(file_size)}) exceeds\n'
                f"the maximum view size of {max_mb} MB."
            ),
            justify=tk.LEFT,
        )
        self.theme.apply_to_widget(warning_label, "text")
        warning_label.pack(anchor="w")

        hint_label = tk.Label(msg_frame, text="\nYou can change this limit in:", justify=tk.LEFT)
        self.theme.apply_to_widget(hint_label, "text")
        hint_label.pack(anchor="w")

        path_hint_label = tk.Label(
            msg_frame,
            text="conf/config.json -> file_browser.viewer.max_view_size_mb",
            font=("Courier", 9),
            fg=self.theme.colors["text_secondary"],
        )
        self.theme.apply_to_widget(path_hint_label, "text")
        path_hint_label.configure(fg=self.theme.colors["text_secondary"])
        path_hint_label.pack(anchor="w")

        btn_frame = tk.Frame(dialog)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 15))

        def on_ok():
            result["proceed"] = False
            dialog.destroy()

        def on_ignore():
            result["proceed"] = True
            dialog.destroy()

        ok_button = tk.Button(btn_frame, text="OK", width=12, command=on_ok)
        self.theme.apply_to_widget(ok_button, "button_secondary")
        ok_button.pack(side=tk.LEFT, padx=(0, 10))

        ignore_button = tk.Button(btn_frame, text="Ignore Once", width=12, command=on_ignore)
        self.theme.apply_to_widget(ignore_button, "button_secondary")
        ignore_button.pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", on_ok)
        self.theme.apply_theme_to_application(dialog)
        ensure_dialog_focus(dialog, self.window)
        dialog.wait_window()

        return result["proceed"]

    def _confirm_image_oversize(self, name: str, size_bytes: int, max_mb: int) -> bool:
        dialog = tk.Toplevel(self.window)
        dialog.title("Large image")
        dialog.geometry("440x170")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")
        ensure_dialog_focus(dialog, self.window)

        msg_frame = tk.Frame(dialog)
        self.theme.apply_to_widget(msg_frame, "main_window")
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        msg = (
            f'The file "{name}" is {_format_file_size(size_bytes)}.\n'
            f"Maximum view size is set to {max_mb} MB.\n\nProceed?"
        )
        msg_label = tk.Label(msg_frame, text=msg, justify=tk.LEFT, anchor="w")
        self.theme.apply_to_widget(msg_label, "text")
        msg_label.pack(fill=tk.X, expand=True)

        btn_frame = tk.Frame(dialog)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=20, pady=(5, 10))

        result = {"proceed": False}

        def on_ok():
            result["proceed"] = True
            dialog.destroy()

        def on_cancel():
            result["proceed"] = False
            dialog.destroy()

        ok_button = tk.Button(btn_frame, text="OK", width=10, command=on_ok)
        self.theme.apply_to_widget(ok_button, "button_secondary")
        ok_button.pack(side=tk.LEFT, padx=(0, 10))

        cancel_button = tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel)
        self.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        self.theme.apply_theme_to_application(dialog)
        dialog.wait_window()

        return result["proceed"]

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        self.navigator.cancel()
        self._disconnect()
        self.window.destroy()
        self.window = None

    # ------------------------------------------------------------------
    # Thread wrappers
    # ------------------------------------------------------------------

    def _prompt_extract_options(self, target_count: int) -> Optional[Dict[str, Any]]:
        from gui.components.batch_extract_dialog import BatchExtractSettingsDialog
        config_path = self.config_path
        if self.settings_manager:
            cfg_override = self.settings_manager.get_setting("backend.config_path", None)
            if cfg_override:
                config_path = cfg_override
        dialog_config = BatchExtractSettingsDialog(
            parent=self.window,
            theme=self.theme,
            settings_manager=self.settings_manager,
            config_path=config_path,
            config_editor_callback=None,
            mode="on-demand",
            target_count=target_count,
        ).show()

        if not dialog_config:
            return None

        limits = {
            "max_depth": int(dialog_config.get("max_directory_depth", 0)),
            "max_files": int(dialog_config.get("max_files_per_target", 0)),
            "max_total_mb": int(dialog_config.get("max_total_size_mb", 0)),
            "max_file_mb": int(dialog_config.get("max_file_size_mb", 0)),
        }
        self._persist_folder_limit_defaults(limits)

        limits.update({
            "extension_mode": dialog_config.get("extension_mode", "download_all"),
            "included_extensions": [ext.lower() for ext in dialog_config.get("included_extensions", [])],
            "excluded_extensions": [ext.lower() for ext in dialog_config.get("excluded_extensions", [])],
        })
        return limits

    def _start_list_thread(self, path: str) -> None:
        self._set_busy(True)

        def worker():
            try:
                self._ensure_connected()
                result = self.navigator.list_dir(path)
                self._safe_after(0, lambda: self._populate_entries(result, path))
            except Exception as e:
                self._safe_after(0, lambda err=e, attempted=path: self._handle_list_error(attempted, err))
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.list_thread = threading.Thread(target=worker, daemon=True)
        self.list_thread.start()

    def _start_view_thread(
        self,
        remote_path: str,
        display_name: str,
        max_bytes: int,
        is_image: bool = False,
        max_image_pixels: int = 20_000_000,
    ) -> None:
        def worker():
            try:
                self._set_busy(True)
                self._safe_after(0, lambda: self._set_status(f"Reading {display_name}..."))
                self._ensure_connected()
                result = self.navigator.read_file(remote_path, max_bytes=max_bytes)
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
                self._safe_after(
                    0,
                    lambda err=e: messagebox.showerror("View error", str(err), parent=self.window)
                    if self._window_alive()
                    else None,
                )
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _start_download_thread(
        self,
        files_with_mtime: List[Tuple[str, Optional[float], int]],
        remote_dirs: List[str],
        folder_limits: Optional[Dict[str, Any]],
    ) -> None:
        from shared.smb_browser import SMBNavigator
        from shared.quarantine import build_quarantine_path, log_quarantine_event

        def worker():
            try:
                self._set_busy(True)
                self._ensure_connected()
                worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
                large_threshold_bytes = (
                    max(1, int(self.large_mb_var.get() or self.download_large_mb)) * 1024 * 1024
                )
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
                        for remote_path, mtime, size in files_with_mtime:
                            if cancel_event.is_set():
                                break
                            enqueue_file(remote_path, mtime, size or 0)

                        if remote_dirs and folder_limits:
                            self._safe_after(0, lambda: self._set_status("Enumerating selected folders..."))
                            enumerated = 0
                            stack: List[Tuple[str, int]] = [(d, 0) for d in remote_dirs]
                            max_depth = limits.get("max_depth", 0)
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
                                    if not self._should_include_extension(
                                        name,
                                        limits.get("extension_mode", "download_all"),
                                        [ext.lower() for ext in limits.get("included_extensions", [])],
                                        [ext.lower() for ext in limits.get("excluded_extensions", [])],
                                    ):
                                        continue
                                    if not enqueue_file(rel, entry.modified_time, size):
                                        break
                                    enumerated += 1
                                    if enumerated % 50 == 0:
                                        self._safe_after(
                                            0,
                                            lambda count=enumerated, qs=q_small.qsize() + q_large.qsize(): self._set_status(
                                                f"Enumerating... {count} files queued ({qs} ready)"
                                            ),
                                        )
                    finally:
                        done_enumerating.set()

                def consumer(target_q: queue.Queue):
                    nonlocal completed
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

                    while (
                        not (done_enumerating.is_set() and q_small.empty() and q_large.empty())
                        and not cancel_event.is_set()
                    ):
                        try:
                            item = target_q.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if cancel_event.is_set():
                            target_q.task_done()
                            break
                        remote_path, mtime, _size = item
                        self._safe_after(
                            0,
                            lambda rp=remote_path, c=completed, t=lambda: max(total_enqueued, completed + 1): self._set_status(
                                f"Downloading {rp} ({c+1}/{t()})"
                            ),
                        )
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
                                self._safe_after(
                                    0,
                                    lambda rp=remote_path, c=completed, h=human: self._set_status(
                                        f"Downloading {rp} ({c+1}/{max(total_enqueued, completed+1)}) – {h} (workers {self.workers_var.get()})"
                                    ),
                                )

                            result = worker_nav.download_file(
                                remote_path,
                                dest_dir,
                                preserve_structure=True,
                                mtime=mtime,
                                progress_callback=_progress,
                            )
                            try:
                                host_dir = Path(dest_dir).parent.parent
                                log_quarantine_event(
                                    host_dir,
                                    f"downloaded {self.current_share}{remote_path} -> {result.saved_path}",
                                )
                            except Exception:
                                pass
                            completed += 1
                        except Exception as e:
                            friendly = self._map_download_error(e)
                            errors.append((remote_path, friendly))
                        finally:
                            target_q.task_done()
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
                        self._safe_after(
                            0,
                            lambda: messagebox.showwarning("Download issues", err_text, parent=self.window)
                            if self._window_alive()
                            else None,
                        )
                    else:
                        self._safe_after(
                            0,
                            lambda: messagebox.showinfo("Download complete", summary_msg, parent=self.window)
                            if self._window_alive()
                            else None,
                        )
            except Exception as e:
                self._safe_after(0, lambda err=e: self._set_status(f"Download failed: {err}"))
                self._safe_after(
                    0,
                    lambda err=e: messagebox.showerror("Download failed", str(err), parent=self.window)
                    if self._window_alive()
                    else None,
                )
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.download_thread = threading.Thread(target=worker, daemon=True)
        self.download_thread.start()

    # ------------------------------------------------------------------
    # Viewers
    # ------------------------------------------------------------------

    def _open_viewer(self, remote_path: str, content: bytes, size: int, truncated: bool) -> None:
        if not self._window_alive():
            return
        display_path = f"{self.ip_address}/{self.current_share}{remote_path}"

        def save_callback():
            mtime = None
            self._start_download_thread([(remote_path, mtime, 0)], [], None)

        open_file_viewer(
            parent=self.window,
            file_path=display_path,
            content=content,
            file_size=size,
            theme=self.theme,
            on_save_callback=save_callback,
        )
        self._set_status(f"Viewing {remote_path}")

    def _open_image_viewer(
        self, remote_path: str, content: bytes, size: int, truncated: bool, max_image_pixels: int
    ) -> None:
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

    # ------------------------------------------------------------------
    # SMB helpers
    # ------------------------------------------------------------------

    def _expand_directories(
        self, dirs: List[str], limits: Dict[str, Any]
    ) -> Tuple[List[Tuple[str, Optional[float]]], int, List[Tuple[str, str]]]:
        max_depth = limits.get("max_depth", 0)
        max_files = limits.get("max_files", 0)
        max_total_mb = limits.get("max_total_mb", 0)
        max_file_mb = limits.get("max_file_mb", 0)
        extension_mode = limits.get("extension_mode", "download_all")
        included_ext = [ext.lower() for ext in limits.get("included_extensions", [])]
        excluded_ext = [ext.lower() for ext in limits.get("excluded_extensions", [])]

        expanded: List[Tuple[str, Optional[float]]] = []
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
                    self._safe_after(
                        0,
                        lambda count=enumerated: self._set_status(f"Enumerating... {count} files queued"),
                    )
                if max_files and len(expanded) >= max_files:
                    return expanded, skipped, errors

        return expanded, skipped, errors

    def _should_include_extension(
        self, name: str, mode: str, included: List[str], excluded: List[str]
    ) -> bool:
        from gui.components.batch_extract_dialog import NO_EXTENSION_TOKEN
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
        text = str(exc)
        lowered = text.lower()
        if "protocolid" in lowered or "unpacked data doesn't match" in lowered:
            return "Unexpected SMB response from server (often happens with large or partial transfers). File not saved."
        if "timed out" in lowered or "timeout" in lowered:
            return "Download timed out. Try again or reduce file size."
        if "cancelled" in lowered:
            return "Download cancelled."
        return text

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # UI updates
    # ------------------------------------------------------------------

    def _populate_entries(self, result, path: str) -> None:
        self._entering_share = False
        self._at_virtual_root = False
        for item in self.tree.get_children():
            self.tree.delete(item)

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
                values=(
                    entry.name,
                    "dir" if entry.is_dir else "file",
                    _format_file_size(entry.size),
                    mtime_str,
                    mtime_raw,
                    size_raw,
                ),
            )

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
        if not busy:
            self._update_action_buttons()  # re-apply virtual-root overrides after unbusy

    def _handle_list_error(self, attempted_path: str, err: Exception) -> None:
        if self._entering_share:
            self._entering_share = False
            failed_share = self.current_share or attempted_path
            error_msg = f"Cannot open '{failed_share}': {err}"
            self._go_to_virtual_root(status_override=error_msg)
            return
        if attempted_path != self.current_path:
            self.pending_path = None
            share_prefix = (self.current_share or "") if not self._at_virtual_root else ""
            self.path_var.set(f"{share_prefix}{self.current_path}")
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

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_folder_limit_defaults(self) -> Dict[str, int]:
        defaults = self.folder_defaults or {}
        if self.settings_manager:
            try:
                saved = self.settings_manager.get_setting("file_browser.folder_limits", {}) or {}
                defaults = {**defaults, **saved}
            except Exception:
                pass
        return defaults

    def _persist_folder_limit_defaults(self, limits: Dict[str, int]) -> None:
        if not self.settings_manager:
            return
        try:
            self.settings_manager.set_setting("file_browser.folder_limits", limits)
        except Exception:
            pass

    def _persist_tuning(self) -> None:
        try:
            self.download_workers = max(1, min(3, int(self.workers_var.get())))
            self.download_large_mb = max(1, int(self.large_mb_var.get()))
        except Exception:
            return
        if self.settings_manager:
            try:
                self.settings_manager.set_setting("file_browser.download_worker_count", self.download_workers)
                self.settings_manager.set_setting("file_browser.download_large_file_mb", self.download_large_mb)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _join_path(base: str, name: str) -> str:
        base_norm = base.rstrip("\\/")
        if not base_norm:
            return f"\\{name}"
        return f"{base_norm}\\{name}"


def open_ftp_http_browser(
    host_type: str,
    parent,
    ip_address: str,
    port: int,
    *,
    banner=None,
    scheme=None,
    config_path=None,
    db_reader=None,
    theme=None,
    settings_manager=None,
) -> None:
    """Launch FtpBrowserWindow (host_type='F') or HttpBrowserWindow (host_type='H').

    SMB (host_type='S') is not handled here; callers must route it separately.
    """
    host_type = (host_type or "").upper()
    if host_type == "F":
        FtpBrowserWindow(
            parent=parent,
            ip_address=ip_address,
            port=port,
            banner=banner,
            config_path=config_path,
            db_reader=db_reader,
            theme=theme,
            settings_manager=settings_manager,
        )
    elif host_type == "H":
        HttpBrowserWindow(
            parent=parent,
            ip_address=ip_address,
            port=port,
            scheme=scheme,
            banner=banner,
            config_path=config_path,
            db_reader=db_reader,
            theme=theme,
            settings_manager=settings_manager,
        )
    else:
        raise ValueError(f"open_ftp_http_browser: unsupported host_type {host_type!r}")


def _normalize_share_name(name: str) -> str:
    return name.strip().strip("\\/").strip()


def open_smb_browser(
    parent,
    ip_address: str,
    shares: list,
    auth_method: str = "",
    *,
    config_path=None,
    db_reader=None,
    theme=None,
    settings_manager=None,
    share_credentials=None,
    on_extracted=None,
) -> None:
    """Launch SmbBrowserWindow for SMB (host_type='S') with Shodan banner.

    Resolves banner from db_reader.get_smb_shodan_data() when available;
    falls back to empty string so SmbBrowserWindow shows its placeholder.
    Re-queries accessible shares from DB when available (freshest source of
    truth); falls back to caller-provided shares only on exception.
    """
    if db_reader:
        try:
            rows = db_reader.get_accessible_shares(ip_address)
            shares = [
                n for r in rows
                if (n := _normalize_share_name(r.get("share_name") or ""))
            ]
        except Exception:
            pass  # exception only — keep caller-provided shares as fallback

    # Normalize all sources uniformly — handles DB-exception and no-db_reader paths
    shares = [n for s in shares if (n := _normalize_share_name(s))]

    shodan_raw = None
    if db_reader:
        try:
            shodan_raw = db_reader.get_smb_shodan_data(ip_address)
        except Exception:
            pass
    banner = _extract_smb_banner(shodan_raw)

    SmbBrowserWindow(
        parent=parent,
        ip_address=ip_address,
        shares=shares,
        auth_method=auth_method,
        config_path=config_path,
        db_reader=db_reader,
        theme=theme,
        settings_manager=settings_manager,
        share_credentials=share_credentials or {},
        on_extracted=on_extracted,
        banner=banner,
    )
