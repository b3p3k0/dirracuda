"""
Web UI tab for the Experimental Features dialog.

Provides inline controls for status / start / stop / open browser / copy URL,
plus modal dialogs for credentials and Web UI configuration.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme


class WebUITab:
    """Content widget for the Web UI experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self._cred_dialog = None
        self._cfg_dialog = None
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
        cred_btn_frame.pack(anchor="w", padx=16, pady=(0, 24))

        self._manage_creds_btn = tk.Button(
            cred_btn_frame,
            text="Manage Credentials",
            command=self._open_credentials_dialog,
        )
        self._theme.apply_to_widget(self._manage_creds_btn, "button_secondary")
        self._manage_creds_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._webui_cfg_btn = tk.Button(
            cred_btn_frame,
            text="WebUI Config",
            command=self._open_config_dialog,
        )
        self._theme.apply_to_widget(self._webui_cfg_btn, "button_secondary")
        self._webui_cfg_btn.pack(side=tk.LEFT)

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

            return load_config(self._get_webui_config_path())
        except Exception:
            from types import SimpleNamespace

            return SimpleNamespace(bind_address="127.0.0.1", port=5480)

    def _get_webui_config_path(self):
        cfg_path = self._context.get("webui_config_path")
        if cfg_path is None:
            return None
        path_text = str(cfg_path).strip()
        if not path_text:
            return None
        name = Path(path_text).name.lower()
        if name == "webui.json" or name.endswith("-webui.json") or name.endswith("_webui.json"):
            return path_text
        return None

    def _normalize_cfg_error(self, detail: object, *, max_len: int = 180) -> str:
        message = " ".join(str(detail).strip().split())
        if not message:
            return "unknown error"
        if "Unknown config keys:" in message:
            return "Selected file is not a Web UI config (expected webui.json)."
        if len(message) <= max_len:
            return message
        return f"{message[: max_len - 3]}..."

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

    def _cfg_dialog_exists(self) -> bool:
        dialog = getattr(self, "_cfg_dialog", None)
        if dialog is None:
            return False
        try:
            return bool(dialog.winfo_exists())
        except Exception:
            return False

    def _schedule_cfg_dialog_ui(self, callback) -> None:
        if not self._cfg_dialog_exists():
            return
        try:
            self._cfg_dialog.after(0, callback)
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
        self._cred_dialog = dialog

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

    def _open_config_dialog(self) -> None:
        if self._cfg_dialog_exists():
            self._cfg_dialog.lift()
            self._cfg_dialog.focus_force()
            return

        load_error = ""
        try:
            from experimental.webui.config import WebUIConfig, load_config

            cfg = load_config(self._get_webui_config_path())
        except Exception as exc:
            cfg = WebUIConfig()
            load_error = self._normalize_cfg_error(exc)

        self._cfg_enabled_value = bool(getattr(cfg, "enabled", False))

        parent = self.frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Web UI Config")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()
        self._theme.apply_to_widget(dialog, "main_window")
        self._cfg_dialog = dialog

        outer = tk.Frame(dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        def _row(label_text: str, var, *, width=44, show=None):
            row = tk.Frame(outer)
            self._theme.apply_to_widget(row, "main_window")
            row.pack(fill=tk.X, pady=(0, 6))
            label = tk.Label(row, text=label_text)
            self._theme.apply_to_widget(label, "label")
            label.pack(side=tk.LEFT)
            entry = tk.Entry(row, textvariable=var, width=width, show=show)
            self._theme.apply_to_widget(entry, "entry")
            entry.pack(side=tk.LEFT, padx=(8, 0))
            return entry

        bind_port_row = tk.Frame(outer)
        self._theme.apply_to_widget(bind_port_row, "main_window")
        bind_port_row.pack(fill=tk.X, pady=(0, 6))

        bind_label = tk.Label(bind_port_row, text="Bind Address:")
        self._theme.apply_to_widget(bind_label, "label")
        bind_label.pack(side=tk.LEFT)
        self._cfg_bind_var = tk.StringVar(value=cfg.bind_address)
        bind_entry = tk.Entry(bind_port_row, textvariable=self._cfg_bind_var, width=24)
        self._theme.apply_to_widget(bind_entry, "entry")
        bind_entry.pack(side=tk.LEFT, padx=(8, 12))

        port_label = tk.Label(bind_port_row, text="Port:")
        self._theme.apply_to_widget(port_label, "label")
        port_label.pack(side=tk.LEFT)
        self._cfg_port_var = tk.StringVar(value=str(cfg.port))
        port_entry = tk.Entry(bind_port_row, textvariable=self._cfg_port_var, width=8)
        self._theme.apply_to_widget(port_entry, "entry")
        port_entry.pack(side=tk.LEFT, padx=(8, 0))

        self._cfg_remote_var = tk.BooleanVar(value=cfg.remote_enabled)
        remote_row = tk.Frame(outer)
        self._theme.apply_to_widget(remote_row, "main_window")
        remote_row.pack(fill=tk.X, pady=(0, 4))
        remote_cb = tk.Checkbutton(
            remote_row,
            text="Remote Access Enabled",
            variable=self._cfg_remote_var,
            command=self._update_cfg_remote_warning,
        )
        self._theme.apply_to_widget(remote_cb, "checkbox")
        remote_cb.pack(anchor="w")

        self._cfg_remote_warn_label = tk.Label(
            outer,
            text=(
                "Warning: remote access exposes this service on non-loopback interfaces. "
                "Ensure TLS and allowlist are configured before enabling."
            ),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self._theme.apply_to_widget(self._cfg_remote_warn_label, "label")
        self._update_cfg_remote_warning()

        self._cfg_tls_enabled_var = tk.BooleanVar(value=cfg.tls.enabled)
        tls_enabled_row = tk.Frame(outer)
        self._theme.apply_to_widget(tls_enabled_row, "main_window")
        tls_enabled_row.pack(fill=tk.X, pady=(0, 2))
        tls_enabled_cb = tk.Checkbutton(
            tls_enabled_row, text="TLS Enabled", variable=self._cfg_tls_enabled_var
        )
        self._theme.apply_to_widget(tls_enabled_cb, "checkbox")
        tls_enabled_cb.pack(anchor="w")

        self._cfg_tls_insecure_var = tk.BooleanVar(value=cfg.tls.allow_insecure_remote)
        tls_insecure_row = tk.Frame(outer)
        self._theme.apply_to_widget(tls_insecure_row, "main_window")
        tls_insecure_row.pack(fill=tk.X, pady=(0, 6))
        tls_insecure_cb = tk.Checkbutton(
            tls_insecure_row,
            text="Allow Insecure Remote Override",
            variable=self._cfg_tls_insecure_var,
        )
        self._theme.apply_to_widget(tls_insecure_cb, "checkbox")
        tls_insecure_cb.pack(anchor="w")

        self._cfg_tls_cert_var = tk.StringVar(value=cfg.tls.cert_file)
        _row("TLS Cert Path:", self._cfg_tls_cert_var, width=50)

        self._cfg_tls_key_var = tk.StringVar(value=cfg.tls.key_file)
        _row("TLS Key Path:", self._cfg_tls_key_var, width=50)

        self._cfg_allowlist_var = tk.StringVar(value=", ".join(cfg.allowed_cidrs))
        _row("Allowlist (CIDRs):", self._cfg_allowlist_var, width=50)

        timeout_row = tk.Frame(outer)
        self._theme.apply_to_widget(timeout_row, "main_window")
        timeout_row.pack(fill=tk.X, pady=(0, 8))

        idle_label = tk.Label(timeout_row, text="Idle Timeout (minutes):")
        self._theme.apply_to_widget(idle_label, "label")
        idle_label.pack(side=tk.LEFT)
        self._cfg_idle_var = tk.StringVar(value=str(cfg.session_timeout_idle // 60))
        idle_entry = tk.Entry(timeout_row, textvariable=self._cfg_idle_var, width=6)
        self._theme.apply_to_widget(idle_entry, "entry")
        idle_entry.pack(side=tk.LEFT, padx=(8, 12))

        abs_label = tk.Label(timeout_row, text="Absolute Timeout (hours):")
        self._theme.apply_to_widget(abs_label, "label")
        abs_label.pack(side=tk.LEFT)
        self._cfg_abs_var = tk.StringVar(value=str(cfg.session_timeout_absolute // 3600))
        abs_entry = tk.Entry(timeout_row, textvariable=self._cfg_abs_var, width=6)
        self._theme.apply_to_widget(abs_entry, "entry")
        abs_entry.pack(side=tk.LEFT, padx=(8, 0))

        auth_frame = tk.LabelFrame(outer, text="Auth Rate Limiting", padx=8, pady=6)
        self._theme.apply_to_widget(auth_frame, "main_window")
        auth_frame.pack(fill=tk.X, pady=(0, 8))

        def _auth_row(label_text, var, width=8):
            row = tk.Frame(auth_frame)
            self._theme.apply_to_widget(row, "main_window")
            row.pack(fill=tk.X, pady=(0, 4))
            lbl = tk.Label(row, text=label_text)
            self._theme.apply_to_widget(lbl, "label")
            lbl.pack(side=tk.LEFT)
            ent = tk.Entry(row, textvariable=var, width=width)
            self._theme.apply_to_widget(ent, "entry")
            ent.pack(side=tk.LEFT, padx=(8, 0))

        self._cfg_auth_threshold_var = tk.StringVar(
            value=str(cfg.auth.lockout_threshold)
        )
        self._cfg_auth_window_var = tk.StringVar(
            value=str(cfg.auth.lockout_window_sec)
        )
        self._cfg_auth_base_var = tk.StringVar(
            value=str(cfg.auth.lockout_base_duration_sec)
        )
        self._cfg_auth_max_var = tk.StringVar(
            value=str(cfg.auth.lockout_max_duration_sec)
        )
        _auth_row("Lockout Threshold (attempts):", self._cfg_auth_threshold_var)
        _auth_row("Observation Window (sec):", self._cfg_auth_window_var)
        _auth_row("Base Lockout Duration (sec):", self._cfg_auth_base_var)
        _auth_row("Max Lockout Duration (sec):", self._cfg_auth_max_var)

        status_row = tk.Frame(outer)
        self._theme.apply_to_widget(status_row, "main_window")
        status_row.pack(fill=tk.X, pady=(0, 2))

        status_label = tk.Label(status_row, text="Status:")
        self._theme.apply_to_widget(status_label, "label")
        status_label.pack(anchor="w")

        self._cfg_status_var = tk.StringVar(value="Ready")
        status_value = tk.Label(
            status_row,
            textvariable=self._cfg_status_var,
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self._theme.apply_to_widget(status_value, "label")
        status_value.pack(fill=tk.X, pady=(2, 0))

        note = tk.Label(outer, text="Changes take effect on restart.", anchor="w", justify="left")
        self._theme.apply_to_widget(note, "label")
        note.pack(fill=tk.X, pady=(0, 8))

        btn_row = tk.Frame(outer)
        self._theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        self._cfg_save_btn = tk.Button(btn_row, text="Save", command=self._on_save_config_dialog)
        self._theme.apply_to_widget(self._cfg_save_btn, "button_primary")
        self._cfg_save_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._cfg_save_restart_btn = tk.Button(
            btn_row,
            text="Save & Restart",
            command=self._on_save_restart_config_dialog,
        )
        self._theme.apply_to_widget(self._cfg_save_restart_btn, "button_secondary")
        self._cfg_save_restart_btn.pack(side=tk.LEFT, padx=(0, 8))

        close_btn = tk.Button(btn_row, text="Close", command=self._close_config_dialog)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", self._close_config_dialog)
        ensure_dialog_focus(dialog, parent)
        bind_entry.focus_set()

        if load_error:
            self._cfg_status_var.set(f"Failed: {load_error}")

    def _update_cfg_remote_warning(self) -> None:
        if not self._cfg_dialog_exists():
            return
        if self._cfg_remote_var.get():
            if not self._cfg_remote_warn_label.winfo_ismapped():
                self._cfg_remote_warn_label.pack(fill=tk.X, pady=(0, 8))
        else:
            if self._cfg_remote_warn_label.winfo_ismapped():
                self._cfg_remote_warn_label.pack_forget()

    def _close_config_dialog(self) -> None:
        if not self._cfg_dialog_exists():
            self._cfg_dialog = None
            return
        try:
            self._cfg_dialog.grab_release()
        except Exception:
            pass
        try:
            self._cfg_dialog.destroy()
        except Exception:
            pass
        self._cfg_dialog = None

    def _parse_cidrs(self, raw: str) -> list[str]:
        if not raw or not raw.strip():
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    def _build_config_from_dialog(self):
        from experimental.webui.config import AuthConfig, TLSConfig, WebUIConfig

        bind = self._cfg_bind_var.get().strip()
        cert = self._cfg_tls_cert_var.get().strip()
        key = self._cfg_tls_key_var.get().strip()
        allowlist = self._parse_cidrs(self._cfg_allowlist_var.get())
        try:
            port = int(self._cfg_port_var.get().strip())
            idle_min = int(self._cfg_idle_var.get().strip())
            absolute_hr = int(self._cfg_abs_var.get().strip())
            auth_threshold = int(self._cfg_auth_threshold_var.get().strip())
            auth_window = int(self._cfg_auth_window_var.get().strip())
            auth_base = int(self._cfg_auth_base_var.get().strip())
            auth_max = int(self._cfg_auth_max_var.get().strip())
        except ValueError as exc:
            raise ValueError("port, timeout, and auth fields must be valid integers") from exc

        return WebUIConfig(
            enabled=bool(getattr(self, "_cfg_enabled_value", False)),
            bind_address=bind,
            port=port,
            remote_enabled=bool(self._cfg_remote_var.get()),
            allowed_cidrs=allowlist,
            session_timeout_idle=idle_min * 60,
            session_timeout_absolute=absolute_hr * 3600,
            tls=TLSConfig(
                enabled=bool(self._cfg_tls_enabled_var.get()),
                cert_file=cert,
                key_file=key,
                allow_insecure_remote=bool(self._cfg_tls_insecure_var.get()),
            ),
            auth=AuthConfig(
                lockout_threshold=auth_threshold,
                lockout_window_sec=auth_window,
                lockout_base_duration_sec=auth_base,
                lockout_max_duration_sec=auth_max,
            ),
        )

    def _set_config_save_controls(self, disabled: bool) -> None:
        if not self._cfg_dialog_exists():
            return
        state = tk.DISABLED if disabled else tk.NORMAL
        self._cfg_save_btn.configure(state=state)
        self._cfg_save_restart_btn.configure(state=state)

    def _finish_config_dialog(self, success: bool, message: str) -> None:
        if not self._cfg_dialog_exists():
            return
        self._cfg_status_var.set(message)
        self._set_config_save_controls(False)
        if success:
            self._refresh_status()

    def _save_config_from_dialog(self):
        from experimental.webui.config import save_config, validate

        cfg = self._build_config_from_dialog()
        validate(cfg)
        save_config(cfg, path=self._get_webui_config_path())
        return cfg

    def _on_save_config_dialog(self) -> None:
        if not self._cfg_dialog_exists():
            return
        self._set_config_save_controls(True)
        self._cfg_status_var.set("Saving...")

        def _do() -> None:
            try:
                self._save_config_from_dialog()
            except Exception as exc:
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(
                        False, f"Failed: {self._normalize_cfg_error(exc)}"
                    )
                )
                return
            self._schedule_cfg_dialog_ui(
                lambda: self._finish_config_dialog(
                    True, "Saved. Changes take effect on restart."
                )
            )

        threading.Thread(target=_do, daemon=True).start()

    def _on_save_restart_config_dialog(self) -> None:
        if not self._cfg_dialog_exists():
            return
        self._set_config_save_controls(True)
        self._cfg_status_var.set("Saving and restarting...")

        current_cfg = self._get_webui_cfg()
        old_host, old_port = current_cfg.bind_address, current_cfg.port

        def _do() -> None:
            from experimental.webui.service_control import is_running, start, stop

            try:
                new_cfg = self._save_config_from_dialog()
            except Exception as exc:
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(
                        False, f"Failed: {self._normalize_cfg_error(exc)}"
                    )
                )
                return

            try:
                was_running = is_running(old_host, old_port)
            except Exception as exc:
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(
                        False, f"Failed: {self._normalize_cfg_error(exc)}"
                    )
                )
                return

            if was_running:
                try:
                    stopped = stop()
                except Exception as exc:
                    self._schedule_cfg_dialog_ui(
                        lambda: self._finish_config_dialog(
                            False, f"Failed: {self._normalize_cfg_error(exc)}"
                        )
                    )
                    return
                if not stopped:
                    self._schedule_cfg_dialog_ui(
                        lambda: self._finish_config_dialog(
                            False,
                            "Failed: stop did not complete; service may still be running",
                        )
                    )
                    return

            try:
                start_result = start(new_cfg.bind_address, new_cfg.port)
            except Exception as exc:
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(
                        False, f"Failed: {self._normalize_cfg_error(exc)}"
                    )
                )
                return

            if start_result.state not in ("running", "already_running"):
                reason = start_result.reason or "startup failed"
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(False, f"Failed: {reason}")
                )
                return

            self._schedule_cfg_dialog_ui(
                lambda: self._finish_config_dialog(
                    True, "Saved and service started."
                )
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
