"""
gui/browsers/core.py — UnifiedBrowserCore base class (extracted in Card C3).

Contains the shared UI/controller machinery for FTP, HTTP, and SMB browser
windows.  Imported by gui.browsers and re-exported from
gui.components.unified_browser_window for backward compatibility.

Heavy protocol imports (shared.ftp_browser, shared.http_browser,
shared.smb_browser) remain as per-method lazy imports in the protocol
subclasses — all three pull impacket unconditionally, so module-level
placement would load impacket at import time.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional

from gui.utils import safe_messagebox as messagebox
from gui.utils.coercion import _coerce_bool


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

    def _adapt_large_file_tuning_enabled(self) -> bool:
        """Return True if large-file threshold spinbox should be active. HTTP overrides to False."""
        return True

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

        # Download tuning strip (FTP/HTTP; SMB overrides _build_window entirely)
        tuning_frame = tk.Frame(self.window)
        tuning_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        tk.Label(tuning_frame, text="Workers").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_var = tk.IntVar(value=self.download_workers)
        workers_spin = tk.Spinbox(
            tuning_frame, from_=1, to=3, width=3, textvariable=self.workers_var,
            command=self._persist_tuning,
        )
        workers_spin.pack(side=tk.LEFT)
        workers_spin.bind("<FocusOut>", lambda _e: self._persist_tuning())
        workers_spin.bind("<Return>",   lambda _e: self._persist_tuning())

        tk.Label(tuning_frame, text="Large files limit (MB)").pack(side=tk.LEFT, padx=(10, 4))
        self.large_mb_var = tk.IntVar(value=self.download_large_mb)
        _large_enabled = self._adapt_large_file_tuning_enabled()
        large_spin = tk.Spinbox(
            tuning_frame, from_=1, to=1024, width=5, textvariable=self.large_mb_var,
            command=self._persist_tuning,
            state=tk.NORMAL if _large_enabled else tk.DISABLED,
        )
        large_spin.pack(side=tk.LEFT)
        large_spin.bind("<FocusOut>", lambda _e: self._persist_tuning())
        large_spin.bind("<Return>",   lambda _e: self._persist_tuning())

        self.show_download_success_var = tk.IntVar(
            value=1
            if _coerce_bool(
                getattr(self, "show_download_success_dialog", True),
                True,
            )
            else 0
        )
        show_success_check = tk.Checkbutton(
            tuning_frame,
            text="Show download completion popup",
            variable=self.show_download_success_var,
            command=self._persist_tuning,
        )
        if self.theme:
            self.theme.apply_to_widget(show_success_check, "checkbox")
        show_success_check.pack(side=tk.LEFT, padx=(10, 0))

        if not _large_enabled:
            # No fg= override; apply_theme_to_application() handles consistent styling
            tk.Label(
                tuning_frame,
                text="(HTTP large-file split not active in this version)",
            ).pack(side=tk.LEFT, padx=(6, 0))

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
                            "View Error",
                            f"Could not read file:\n{exc}",
                            parent=self.window,
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

    def _on_download_done(
        self,
        success: int,
        total: int,
        quarantine_path: str,
        clamav_accum: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.busy = False
        self._set_buttons_busy(False)
        self.btn_cancel.config(state=tk.DISABLED)
        if clamav_accum and clamav_accum.get("enabled"):
            promoted = int(clamav_accum.get("promoted", 0) or 0)
            infected = int(clamav_accum.get("infected", 0) or 0)
            status_parts = [f"Downloaded {success}/{total} file(s)"]
            if promoted:
                status_parts.append(f"{promoted} clean -> extracted")
            if infected:
                status_parts.append(f"{infected} infected -> known_bad")
            status_text = ", ".join(status_parts)
            self._set_status(status_text)
            shown = self._maybe_show_clamav_dialog(clamav_accum)
            if (
                not shown
                and success > 0
                and self._show_download_success_popup_enabled()
            ):
                messagebox.showinfo("Download complete", status_text, parent=self.window)
            return

        self._set_status(f"Downloaded {success}/{total} file(s) \u2192 {quarantine_path}")
        if success > 0 and self._show_download_success_popup_enabled():
            messagebox.showinfo(
                "Download complete",
                f"Downloaded {success}/{total} file(s) to quarantine:\n{quarantine_path}",
                parent=self.window,
            )

    def _maybe_show_clamav_dialog(self, clamav_accum: Optional[Dict[str, Any]]) -> bool:
        """Show ClamAV results dialog and return True iff shown."""
        try:
            if not clamav_accum:
                return False
            if int(clamav_accum.get("files_scanned", 0) or 0) <= 0:
                return False
            from gui.components.clamav_results_dialog import (
                should_show_clamav_dialog,
                show_clamav_results_dialog,
            )
            from gui.utils import session_flags
            clamav_cfg = self.config.get("clamav", {}) if isinstance(self.config, dict) else {}
            results = [{"clamav": clamav_accum, "ip_address": self.ip_address}]
            if not should_show_clamav_dialog("extract", results, clamav_cfg):
                return False
            show_clamav_results_dialog(
                parent=self.window,
                theme=self.theme,
                results=results,
                on_mute=lambda: session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY),
            )
            return True
        except Exception:
            return False

    def _on_smb_download_done(
        self,
        summary_msg: str,
        clamav_accum: Optional[Dict[str, Any]],
    ) -> None:
        """Main-thread SMB completion handler with one-popup policy."""
        shown = (
            self._maybe_show_clamav_dialog(clamav_accum)
            if (clamav_accum and clamav_accum.get("enabled"))
            else False
        )
        if (
            not shown
            and self._window_alive()
            and self._show_download_success_popup_enabled()
        ):
            messagebox.showinfo("Download complete", summary_msg, parent=self.window)

    def _show_download_success_popup_enabled(self) -> bool:
        return _coerce_bool(
            getattr(self, "show_download_success_dialog", True),
            True,
        )

    # ------------------------------------------------------------------
    # Status and button helpers
    # ------------------------------------------------------------------

    def _persist_tuning(self) -> None:
        try:
            self.download_workers = max(1, min(3, int(self.workers_var.get())))
            self.download_large_mb = max(1, int(self.large_mb_var.get()))
            popup_var = getattr(self, "show_download_success_var", None)
            if popup_var is not None:
                self.show_download_success_dialog = _coerce_bool(
                    popup_var.get(),
                    True,
                )
            else:
                self.show_download_success_dialog = _coerce_bool(
                    getattr(self, "show_download_success_dialog", True),
                    True,
                )
        except Exception:
            return
        if self.settings_manager:
            try:
                self.settings_manager.set_setting("file_browser.download_worker_count", self.download_workers)
                self.settings_manager.set_setting("file_browser.download_large_file_mb", self.download_large_mb)
                self.settings_manager.set_setting(
                    "file_browser.show_download_success_dialog",
                    self.show_download_success_dialog,
                )
            except Exception:
                pass

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
