"""
gui/browsers/ftp_browser.py — FtpBrowserWindow (extracted in Card C4).

Contains the FTP-specific browser window and its config loader.
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
from datetime import datetime
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


def _load_ftp_browser_config(config_path: Optional[str]) -> Dict:
    defaults: Dict[str, Any] = {
        "max_entries": 5000,
        "max_depth": 12,
        "max_path_length": 1024,
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
        defaults.update(data.get("ftp_browser", {}))
        defaults["clamav"] = data.get("clamav", {})
    except Exception:
        pass
    return defaults


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
            messagebox.showinfo("View", "Select only one file to view.", parent=self.window)
            return
        vals = self.tree.item(sel[0], "values")
        name, type_label = vals[0], vals[1]
        if type_label != "file":
            messagebox.showinfo("View", "Select a file (not a directory) to view.", parent=self.window)
            return
        if self._navigator is None:
            messagebox.showinfo("View", "Not connected.", parent=self.window)
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
                parent=self.window,
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
        # Lazy import preserves gui.components.unified_browser_window.open_file_viewer
        # as a valid monkeypatch target (BASELINE_CONTRACTS §2c).
        from gui.components.unified_browser_window import open_file_viewer
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
        # Lazy import preserves gui.components.unified_browser_window.open_image_viewer
        # as a valid monkeypatch target (BASELINE_CONTRACTS §2c).
        from gui.components.unified_browser_window import open_image_viewer
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
            messagebox.showinfo("Download", "Select one or more files to download.", parent=self.window)
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
                    parent=self.window,
                )
                return
            if size_raw and size_raw > limit:
                size_mb = size_raw / (1024 * 1024)
                messagebox.showerror(
                    "File too large",
                    f"{name} is {size_mb:.1f} MB, exceeding the "
                    f"{limit // (1024 * 1024)} MB limit.\n\n"
                    f"Adjust ftp_browser.max_file_bytes in config to change this limit.",
                    parent=self.window,
                )
                return
            remote_path = str(PurePosixPath(self._current_path) / name)
            file_list.append((remote_path, size_raw))

        if not file_list:
            return

        self._start_download_thread(file_list)

    def _download_thread_fn(self, file_list: List) -> None:
        from gui.utils.extract_runner import (
            build_browser_download_clamav_setup,
            update_browser_clamav_accum,
        )
        from shared.ftp_browser import FtpCancelledError, FtpFileTooLargeError, FtpNavigator
        from shared.quarantine_postprocess import PostProcessInput
        from shared.quarantine import build_quarantine_path, log_quarantine_event
        quarantine_dir = build_quarantine_path(
            ip_address=self.ip_address,
            share_name="ftp_root",
            base_path=Path(self.config["quarantine_base"]).expanduser(),
            purpose="ftp",
        )
        _pp, clamav_accum, _init_err = build_browser_download_clamav_setup(
            self.config.get("clamav", {}),
            self.ip_address,
            quarantine_dir,
            "ftp_root",
        )
        if _init_err:
            try:
                self.window.after(0, self._set_status, _init_err)
            except tk.TclError:
                pass

        try:
            worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
            large_threshold_bytes = max(1, int(self.large_mb_var.get() or self.download_large_mb)) * 1024 * 1024
        except Exception:
            worker_count = max(1, min(3, self.download_workers))
            large_threshold_bytes = max(1, self.download_large_mb) * 1024 * 1024

        q_small: queue.Queue = queue.Queue()
        q_large: queue.Queue = queue.Queue()
        for remote_path, file_size in file_list:
            if file_size and file_size > large_threshold_bytes:
                q_large.put((remote_path, file_size))
            else:
                q_small.put((remote_path, file_size))

        success_count_ref = [0]
        clamav_lock = threading.Lock()

        def consumer(target_q: queue.Queue) -> None:
            if self._cancel_event.is_set():
                return
            try:
                item = target_q.get_nowait()
            except queue.Empty:
                return

            nav = FtpNavigator(
                connect_timeout=float(self.config["connect_timeout"]),
                request_timeout=float(self.config["request_timeout"]),
                max_entries=int(self.config["max_entries"]),
                max_depth=int(self.config["max_depth"]),
                max_path_length=int(self.config["max_path_length"]),
                max_file_bytes=int(self.config["max_file_bytes"]),
            )
            nav._cancel_event = self._cancel_event
            try:
                nav.connect(self.ip_address, self.port)
            except Exception as exc:
                try:
                    self.window.after(
                        0, self._set_status, f"FTP connect failed: {exc}"
                    )
                except tk.TclError:
                    pass
                return

            try:
                while True:
                    remote_path, file_size = item
                    if self._cancel_event.is_set():
                        break
                    filename = PurePosixPath(remote_path).name
                    try:
                        self.window.after(0, self._set_status, f"Downloading {filename}...")
                    except tk.TclError:
                        return
                    try:
                        result = nav.download_file(
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
                        if _pp is not None and clamav_accum is not None:
                            try:
                                _pp_result = _pp(PostProcessInput(
                                    file_path=Path(result.saved_path),
                                    ip_address=self.ip_address,
                                    share="ftp_root",
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
                    except FtpCancelledError:
                        try:
                            self.window.after(0, self._set_status, "Download cancelled.")
                        except tk.TclError:
                            pass
                        return
                    except FtpFileTooLargeError:
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
                        item = target_q.get_nowait()
                    except queue.Empty:
                        break
            finally:
                try:
                    nav.disconnect()
                except Exception:
                    pass

        consumer_threads = []
        for _ in range(worker_count):
            consumer_threads.append(threading.Thread(target=consumer, args=(q_small,), daemon=True))
        consumer_threads.append(threading.Thread(target=consumer, args=(q_large,), daemon=True))
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
