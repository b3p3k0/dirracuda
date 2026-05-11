"""
Web UI tab for the Experimental Features dialog.

Provides inline controls for status / start / stop / open browser / copy URL.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme


class WebUITab:
    """Content widget for the Web UI experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self._cred_dialog = None
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)
        self._refresh_status()

    def _build(self, frame: tk.Frame) -> None:
        description = (
            "Browser-based UI for scan control and results review.\n"
            "Runs a local web server accessible at http://127.0.0.1:5480."
        )
        desc_label = tk.Label(
            frame,
            text=description,
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._theme.apply_to_widget(desc_label, "label")
        desc_label.pack(anchor="w", padx=16, pady=(16, 12))

        status_frame = tk.Frame(frame)
        self._theme.apply_to_widget(status_frame, "main_window")
        status_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

        status_label = tk.Label(status_frame, text="Status:")
        self._theme.apply_to_widget(status_label, "label")
        status_label.pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="Checking…")
        status_value = tk.Label(status_frame, textvariable=self._status_var)
        self._theme.apply_to_widget(status_value, "label")
        status_value.pack(side=tk.LEFT, padx=(8, 0))

        url_frame = tk.Frame(frame)
        self._theme.apply_to_widget(url_frame, "main_window")
        url_frame.pack(fill=tk.X, padx=16, pady=(0, 12))

        url_label = tk.Label(url_frame, text="URL:")
        self._theme.apply_to_widget(url_label, "label")
        url_label.pack(side=tk.LEFT)

        self._url_var = tk.StringVar(value="")
        url_value = tk.Label(url_frame, textvariable=self._url_var)
        self._theme.apply_to_widget(url_value, "label")
        url_value.pack(side=tk.LEFT, padx=(8, 0))

        cred_btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(cred_btn_frame, "main_window")
        cred_btn_frame.pack(anchor="w", padx=16, pady=(0, 12))

        self._manage_creds_btn = tk.Button(
            cred_btn_frame,
            text="Manage Credentials",
            command=self._open_credentials_dialog,
        )
        self._theme.apply_to_widget(self._manage_creds_btn, "button_secondary")
        self._manage_creds_btn.pack(side=tk.LEFT)

        btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))

        self._start_btn = tk.Button(btn_frame, text="Start", command=self._on_start)
        self._theme.apply_to_widget(self._start_btn, "button_primary")
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._stop_btn = tk.Button(btn_frame, text="Stop", command=self._on_stop)
        self._theme.apply_to_widget(self._stop_btn, "button_danger")
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._browser_btn = tk.Button(
            btn_frame,
            text="Open in Browser",
            command=self._on_open_browser,
        )
        self._theme.apply_to_widget(self._browser_btn, "button_secondary")
        self._browser_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._copy_btn = tk.Button(btn_frame, text="Copy URL", command=self._on_copy_url)
        self._theme.apply_to_widget(self._copy_btn, "button_secondary")
        self._copy_btn.pack(side=tk.LEFT)

    def _get_webui_cfg(self):
        try:
            from experimental.webui.config import load_config

            return load_config()
        except Exception:
            from types import SimpleNamespace

            return SimpleNamespace(bind_address="127.0.0.1", port=5480)

    def _schedule_ui(self, callback) -> None:
        try:
            if self.frame.winfo_exists():
                self.frame.after(0, callback)
        except Exception:
            pass

    def _dialog_exists(self) -> bool:
        dialog = getattr(self, "_cred_dialog", None)
        if dialog is None:
            return False
        try:
            return bool(dialog.winfo_exists())
        except Exception:
            return False

    def _schedule_dialog_ui(self, callback) -> None:
        if not self._dialog_exists():
            return
        try:
            self._cred_dialog.after(0, callback)
        except Exception:
            pass

    def _refresh_status(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._url_var.set(f"http://{host}:{port}")

        def _check() -> None:
            from experimental.webui.service_control import is_running

            running = is_running(host, port)
            self._schedule_ui(lambda: self._apply_status(running))

        threading.Thread(target=_check, daemon=True).start()

    def _apply_status(self, running: bool) -> None:
        if not self.frame.winfo_exists():
            return
        if running:
            self._status_var.set("Running")
            self._start_btn.configure(state=tk.DISABLED)
            self._stop_btn.configure(state=tk.NORMAL)
            self._browser_btn.configure(state=tk.NORMAL)
        else:
            self._status_var.set("Stopped")
            self._start_btn.configure(state=tk.NORMAL)
            self._stop_btn.configure(state=tk.DISABLED)
            self._browser_btn.configure(state=tk.DISABLED)

    def _apply_failed_status(self, reason: str) -> None:
        if not self.frame.winfo_exists():
            return
        suffix = "startup failed"
        if reason:
            suffix = reason
        self._status_var.set(f"Failed: {suffix}")
        self._start_btn.configure(state=tk.NORMAL)
        self._stop_btn.configure(state=tk.DISABLED)
        self._browser_btn.configure(state=tk.DISABLED)

    def _open_credentials_dialog(self) -> None:
        if self._dialog_exists():
            self._cred_dialog.lift()
            self._cred_dialog.focus_force()
            return

        parent = self.frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Web UI Credentials")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()
        self._theme.apply_to_widget(dialog, "main_window")

        outer = tk.Frame(dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        user_row = tk.Frame(outer)
        self._theme.apply_to_widget(user_row, "main_window")
        user_row.pack(fill=tk.X, pady=(0, 6))

        user_label = tk.Label(user_row, text="Username:")
        self._theme.apply_to_widget(user_label, "label")
        user_label.pack(side=tk.LEFT)

        self._cred_username_var = tk.StringVar(value="")
        user_entry = tk.Entry(user_row, textvariable=self._cred_username_var, width=32)
        self._theme.apply_to_widget(user_entry, "entry")
        user_entry.pack(side=tk.LEFT, padx=(8, 0))

        pass_row = tk.Frame(outer)
        self._theme.apply_to_widget(pass_row, "main_window")
        pass_row.pack(fill=tk.X, pady=(0, 8))

        pass_label = tk.Label(pass_row, text="Password:")
        self._theme.apply_to_widget(pass_label, "label")
        pass_label.pack(side=tk.LEFT)

        self._cred_password_var = tk.StringVar(value="")
        pass_entry = tk.Entry(pass_row, textvariable=self._cred_password_var, show="*", width=32)
        self._theme.apply_to_widget(pass_entry, "entry")
        pass_entry.pack(side=tk.LEFT, padx=(8, 0))

        status_row = tk.Frame(outer)
        self._theme.apply_to_widget(status_row, "main_window")
        status_row.pack(fill=tk.X, pady=(0, 10))

        status_label = tk.Label(status_row, text="Status:")
        self._theme.apply_to_widget(status_label, "label")
        status_label.pack(side=tk.LEFT)

        self._cred_status_var = tk.StringVar(value="Ready")
        status_value = tk.Label(status_row, textvariable=self._cred_status_var)
        self._theme.apply_to_widget(status_value, "label")
        status_value.pack(side=tk.LEFT, padx=(8, 0))

        btn_row = tk.Frame(outer)
        self._theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        self._save_creds_btn = tk.Button(
            btn_row,
            text="Save Credentials",
            command=self._on_save_credentials_dialog,
        )
        self._theme.apply_to_widget(self._save_creds_btn, "button_primary")
        self._save_creds_btn.pack(side=tk.LEFT, padx=(0, 8))

        close_btn = tk.Button(btn_row, text="Close", command=self._close_credentials_dialog)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", self._close_credentials_dialog)
        self._cred_dialog = dialog
        ensure_dialog_focus(dialog, parent)
        user_entry.focus_set()

    def _close_credentials_dialog(self) -> None:
        if not self._dialog_exists():
            self._cred_dialog = None
            return
        try:
            self._cred_dialog.grab_release()
        except Exception:
            pass
        try:
            self._cred_dialog.destroy()
        except Exception:
            pass
        self._cred_dialog = None

    def _finish_credential_save_dialog(self, success: bool, detail: str) -> None:
        if not self._dialog_exists():
            return
        if success:
            self._cred_status_var.set(f"Saved credentials for '{detail}'")
            self._cred_password_var.set("")
        else:
            reason = detail.strip() if detail else "credential save failed"
            self._cred_status_var.set(f"Failed: {reason}")
        self._save_creds_btn.configure(state=tk.NORMAL)

    def _on_save_credentials_dialog(self) -> None:
        username = self._cred_username_var.get()
        password = self._cred_password_var.get()

        if not username.strip():
            self._cred_status_var.set("Failed: username is required")
            return
        if not password:
            self._cred_status_var.set("Failed: password is required")
            return

        self._save_creds_btn.configure(state=tk.DISABLED)
        self._cred_status_var.set("Saving...")

        def _do() -> None:
            from experimental.webui.auth import set_password

            try:
                set_password(username, password)
            except ValueError as exc:
                self._schedule_dialog_ui(
                    lambda: self._finish_credential_save_dialog(False, str(exc))
                )
                return
            except Exception as exc:
                self._schedule_dialog_ui(
                    lambda: safe_messagebox.showerror(
                        "Credential Save Failed", str(exc), parent=self._cred_dialog
                    )
                )
                self._schedule_dialog_ui(
                    lambda: self._finish_credential_save_dialog(False, str(exc))
                )
                return
            self._schedule_dialog_ui(
                lambda: self._finish_credential_save_dialog(True, username)
            )

        threading.Thread(target=_do, daemon=True).start()

    def _on_start(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._start_btn.configure(state=tk.DISABLED)
        self._status_var.set("Starting…")

        def _do() -> None:
            from experimental.webui.service_control import start

            try:
                result = start(host, port)
            except Exception as exc:
                self._schedule_ui(
                    lambda: safe_messagebox.showerror(
                        "Start Failed", str(exc), parent=self.frame
                    )
                )
                self._schedule_ui(lambda: self._apply_failed_status(str(exc)))
                return

            if result.state in ("running", "already_running"):
                self._schedule_ui(lambda: self._apply_status(True))
                return
            self._schedule_ui(lambda: self._apply_failed_status(result.reason))

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._stop_btn.configure(state=tk.DISABLED)
        self._status_var.set("Stopping…")

        def _do() -> None:
            from experimental.webui.service_control import is_running, stop

            try:
                stop()
            except Exception as exc:
                self._schedule_ui(
                    lambda: safe_messagebox.showerror(
                        "Stop Failed", str(exc), parent=self.frame
                    )
                )
            running = is_running(host, port)

            def _finish() -> None:
                self._apply_status(running)
                if running:
                    safe_messagebox.showwarning(
                        "Stop Did Not Complete",
                        "Service is still running. Ownership could not be confirmed"
                        " — use system tools to stop the process.",
                        parent=self.frame,
                    )

            self._schedule_ui(_finish)

        threading.Thread(target=_do, daemon=True).start()

    def _on_open_browser(self) -> None:
        cfg = self._get_webui_cfg()
        webbrowser.open(f"http://{cfg.bind_address}:{cfg.port}")

    def _on_copy_url(self) -> None:
        cfg = self._get_webui_cfg()
        url = f"http://{cfg.bind_address}:{cfg.port}"
        self.frame.clipboard_clear()
        self.frame.clipboard_append(url)
        safe_messagebox.showinfo("Copied", f"URL copied: {url}", parent=self.frame)


def build_webui_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Web UI tab frame."""
    tab = WebUITab(parent, context)
    return tab.frame
