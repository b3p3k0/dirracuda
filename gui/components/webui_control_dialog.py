"""
Web UI control dialog.

Status / start / stop / open browser / copy URL for the embedded web UI service.
State is checked via pidfile + ownership + health endpoint — not in-memory — so the
dialog works correctly after desktop app close and reopen.
"""

from __future__ import annotations

import threading
import tkinter as tk

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme


class WebUIControlDialog:
    """Modal dialog for controlling the embedded web UI service."""

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        self._theme = get_theme()
        self._dialog = tk.Toplevel(parent)
        self._build()
        self._dialog.transient(parent)
        self._dialog.grab_set()
        ensure_dialog_focus(self._dialog, parent)
        self._refresh_status()

    def _get_webui_cfg(self):
        try:
            from webui.config import load_config
            return load_config()
        except Exception:
            from types import SimpleNamespace
            return SimpleNamespace(bind_address="127.0.0.1", port=5480)

    def _build(self) -> None:
        dialog = self._dialog
        dialog.title("Web UI Control")
        dialog.resizable(False, False)

        outer = tk.Frame(dialog, padx=16, pady=16)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        status_frame = tk.Frame(outer)
        self._theme.apply_to_widget(status_frame, "main_window")
        status_frame.pack(fill=tk.X, pady=(0, 8))

        lbl = tk.Label(status_frame, text="Status:")
        self._theme.apply_to_widget(lbl, "label")
        lbl.pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="Checking…")
        status_val = tk.Label(status_frame, textvariable=self._status_var)
        self._theme.apply_to_widget(status_val, "label")
        status_val.pack(side=tk.LEFT, padx=(8, 0))

        url_frame = tk.Frame(outer)
        self._theme.apply_to_widget(url_frame, "main_window")
        url_frame.pack(fill=tk.X, pady=(0, 16))

        url_lbl = tk.Label(url_frame, text="URL:")
        self._theme.apply_to_widget(url_lbl, "label")
        url_lbl.pack(side=tk.LEFT)

        self._url_var = tk.StringVar(value="")
        url_val = tk.Label(url_frame, textvariable=self._url_var)
        self._theme.apply_to_widget(url_val, "label")
        url_val.pack(side=tk.LEFT, padx=(8, 0))

        btn_frame = tk.Frame(outer)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X)

        self._start_btn = tk.Button(btn_frame, text="Start", command=self._on_start)
        self._theme.apply_to_widget(self._start_btn, "button_primary")
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._stop_btn = tk.Button(btn_frame, text="Stop", command=self._on_stop)
        self._theme.apply_to_widget(self._stop_btn, "button_danger")
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._browser_btn = tk.Button(
            btn_frame, text="Open in Browser", command=self._on_open_browser
        )
        self._theme.apply_to_widget(self._browser_btn, "button_secondary")
        self._browser_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._copy_btn = tk.Button(
            btn_frame, text="Copy URL", command=self._on_copy_url
        )
        self._theme.apply_to_widget(self._copy_btn, "button_secondary")
        self._copy_btn.pack(side=tk.LEFT, padx=(0, 16))

        close_btn = tk.Button(btn_frame, text="Close", command=self._dialog.destroy)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.RIGHT)

    def _refresh_status(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._url_var.set(f"http://{host}:{port}")

        def _check() -> None:
            from webui.service_control import is_running
            running = is_running(host, port)
            if self._dialog.winfo_exists():
                self._dialog.after(0, lambda: self._apply_status(running))

        threading.Thread(target=_check, daemon=True).start()

    def _apply_status(self, running: bool) -> None:
        if not self._dialog.winfo_exists():
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

    def _on_start(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._start_btn.configure(state=tk.DISABLED)
        self._status_var.set("Starting…")

        def _do() -> None:
            import time
            from webui.service_control import is_running, start
            try:
                start(host, port)
                time.sleep(1.5)
            except Exception as exc:
                if self._dialog.winfo_exists():
                    self._dialog.after(
                        0,
                        lambda: safe_messagebox.showerror(
                            "Start Failed", str(exc), parent=self._dialog
                        ),
                    )
            running = is_running(host, port)
            if self._dialog.winfo_exists():
                self._dialog.after(0, lambda: self._apply_status(running))

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop(self) -> None:
        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        self._stop_btn.configure(state=tk.DISABLED)
        self._status_var.set("Stopping…")

        def _do() -> None:
            from webui.service_control import is_running, stop
            try:
                stop()
            except Exception as exc:
                if self._dialog.winfo_exists():
                    self._dialog.after(
                        0,
                        lambda: safe_messagebox.showerror(
                            "Stop Failed", str(exc), parent=self._dialog
                        ),
                    )
            running = is_running(host, port)
            if self._dialog.winfo_exists():
                self._dialog.after(0, lambda: self._apply_status(running))
                if running:
                    self._dialog.after(
                        0,
                        lambda: safe_messagebox.showwarning(
                            "Stop Did Not Complete",
                            "Service is still running. Ownership could not be confirmed"
                            " — use system tools to stop the process.",
                            parent=self._dialog,
                        ),
                    )

        threading.Thread(target=_do, daemon=True).start()

    def _on_open_browser(self) -> None:
        import webbrowser
        cfg = self._get_webui_cfg()
        webbrowser.open(f"http://{cfg.bind_address}:{cfg.port}")

    def _on_copy_url(self) -> None:
        cfg = self._get_webui_cfg()
        url = f"http://{cfg.bind_address}:{cfg.port}"
        self._dialog.clipboard_clear()
        self._dialog.clipboard_append(url)
        safe_messagebox.showinfo("Copied", f"URL copied: {url}", parent=self._dialog)


def show_webui_control_dialog(parent: tk.Widget) -> None:
    """Open the Web UI control dialog."""
    WebUIControlDialog(parent)
