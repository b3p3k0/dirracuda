"""
gui/browsers/http_browser.py — HttpBrowserWindow (extracted in Card C4).

Contains the HTTP-specific browser window and its config loader.
Re-exported by gui.components.unified_browser_window for backward compatibility.

open_file_viewer and open_image_viewer are accessed via lazy import from
gui.components.unified_browser_window inside _open_viewer / _open_image_viewer
to preserve the monkeypatch targets used in tests (§2c of BASELINE_CONTRACTS).
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from gui.utils import safe_messagebox as messagebox
from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size
from gui.browsers.core import UnifiedBrowserCore
from shared.path_service import get_paths, get_legacy_paths, select_existing_path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)
_DEFAULT_QUARANTINE_ROOT = select_existing_path(
    _PATHS.quarantine_dir,
    [
        _LEGACY.flat_quarantine_dir,
        _LEGACY.legacy_home_root / "quarantine",
    ],
)


def _load_http_browser_config(config_path: Optional[str]) -> Dict:
    defaults: Dict[str, Any] = {
        "max_entries": 5000,
        "max_file_bytes": 26_214_400,   # 25 MB
        "connect_timeout": 10,
        "request_timeout": 15,
        "quarantine_base": str(_DEFAULT_QUARANTINE_ROOT),
        "viewer": {
            "max_view_size_mb": 5,
            "max_image_size_mb": 15,
            "max_image_pixels": 20_000_000,
        },
        "clamav": {},
    }
    if not config_path:
        return defaults
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        defaults.update(data.get("http_browser", {}))
        defaults["clamav"] = data.get("clamav", {})
    except Exception:
        pass
    return defaults


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
        initial_path: Optional[str] = None,
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
        self._initial_path = self._normalize_initial_path(initial_path)
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

        # Download tuning
        self.download_workers = 2
        self.download_large_mb = 25
        self.show_download_success_dialog = True
        if self.settings_manager:
            try:
                self.download_workers = max(1, min(3, int(self.settings_manager.get_setting(
                    "file_browser.download_worker_count", self.download_workers
                ))))
                self.download_large_mb = max(1, int(self.settings_manager.get_setting(
                    "file_browser.download_large_file_mb", self.download_large_mb
                )))
                self.show_download_success_dialog = _coerce_bool(
                    self.settings_manager.get_setting(
                        "file_browser.show_download_success_dialog",
                        self.show_download_success_dialog,
                    ),
                    self.show_download_success_dialog,
                )
            except Exception:
                pass

        self._build_window()

        # Apply cached probe snapshot if available
        self._apply_probe_snapshot(load_probe_result_for_host(ip_address, "H", port=port))

        # Start navigating to configured initial path (defaults to root).
        self._navigate_to(self._initial_path)

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

    def _adapt_large_file_tuning_enabled(self) -> bool:
        return False

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

    @staticmethod
    def _normalize_initial_path(path: Optional[str]) -> str:
        """Normalize configured startup path for first HTTP navigation."""
        if not path:
            return "/"
        cleaned = str(path).strip()
        if not cleaned:
            return "/"
        return f"/{cleaned.lstrip('/')}"

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
            messagebox.showinfo("View", "Select only one file to view.", parent=self.window)
            return
        iid = sel[0]
        vals = self.tree.item(iid, "values")
        type_label = vals[1]
        if type_label != "file":
            messagebox.showinfo("View", "Select a file (not a directory) to view.", parent=self.window)
            return

        abs_path = self._path_map.get(iid, "")
        if not abs_path:
            messagebox.showinfo("View", "Path not found.", parent=self.window)
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
        # Lazy import preserves gui.components.unified_browser_window.open_file_viewer
        # as a valid monkeypatch target (BASELINE_CONTRACTS §2c).
        from gui.components.unified_browser_window import open_file_viewer
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
        # Lazy import preserves gui.components.unified_browser_window.open_image_viewer
        # as a valid monkeypatch target (BASELINE_CONTRACTS §2c).
        from gui.components.unified_browser_window import open_image_viewer
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
            messagebox.showinfo("Download", "Select one or more files to download.", parent=self.window)
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
                    parent=self.window,
                )
                return
            abs_path = self._path_map.get(iid, "")
            if abs_path:
                file_list.append(abs_path)

        if not file_list:
            return

        self._start_download_thread([(p, 0) for p in file_list])

    def _download_thread_fn(self, file_list) -> None:
        from gui.utils.extract_runner import (
            build_browser_download_clamav_setup,
            update_browser_clamav_accum,
        )
        from shared.http_browser import HttpCancelledError, HttpFileTooLargeError
        from shared.quarantine_postprocess import PostProcessInput
        from shared.quarantine import build_quarantine_path, log_quarantine_event
        quarantine_dir = build_quarantine_path(
            ip_address=self.ip_address,
            share_name="http_root",
            base_path=Path(self.config["quarantine_base"]).expanduser(),
            purpose="http",
        )
        _pp, clamav_accum, _init_err = build_browser_download_clamav_setup(
            self.config.get("clamav", {}),
            self.ip_address,
            quarantine_dir,
            "http_root",
        )
        if _init_err:
            try:
                self.window.after(0, self._set_status, _init_err)
            except tk.TclError:
                pass

        try:
            worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
        except Exception:
            worker_count = max(1, min(3, self.download_workers))

        q: queue.Queue = queue.Queue()
        for item in file_list:
            q.put(item)

        success_count_ref = [0]
        clamav_lock = threading.Lock()

        def consumer() -> None:
            if self._cancel_event.is_set():
                return
            try:
                item = q.get_nowait()
            except queue.Empty:
                return

            while True:
                remote_path, file_size = item
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
                    if _pp is not None and clamav_accum is not None:
                        try:
                            _pp_result = _pp(PostProcessInput(
                                file_path=Path(result.saved_path),
                                ip_address=self.ip_address,
                                share="http_root",
                                rel_display=remote_path,
                                file_size=int(file_size or 0),
                            ))
                            with clamav_lock:
                                update_browser_clamav_accum(clamav_accum, _pp_result, remote_path)
                        except Exception as exc:
                            with clamav_lock:
                                clamav_accum["errors"] += 1
                                clamav_accum["error_items"].append({
                                    "path": remote_path,
                                    "error": str(exc),
                                })
                            try:
                                log_quarantine_event(
                                    quarantine_dir,
                                    f"clamav post-process error for {remote_path}: {exc}",
                                )
                            except Exception:
                                pass
                    with clamav_lock:
                        success_count_ref[0] += 1
                except HttpCancelledError:
                    try:
                        self.window.after(0, self._set_status, "Download cancelled.")
                    except tk.TclError:
                        pass
                    return
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
                # Claim next item only after finishing current one
                if self._cancel_event.is_set():
                    break
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    break

        consumer_threads = [
            threading.Thread(target=consumer, daemon=True)
            for _ in range(worker_count)
        ]
        for t in consumer_threads:
            t.start()
        for t in consumer_threads:
            t.join()

        try:
            self.window.after(
                0,
                self._on_download_done,
                success_count_ref[0],
                len(file_list),
                str(quarantine_dir),
                clamav_accum,
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
