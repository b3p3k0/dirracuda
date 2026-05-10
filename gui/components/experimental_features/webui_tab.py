"""
Web UI tab for the Experimental Features dialog.

Provides inline controls for status / start / stop / open browser / copy URL.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser

from gui.utils import safe_messagebox
from gui.utils.style import get_theme


class WebUITab:
    """Content widget for the Web UI experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
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
