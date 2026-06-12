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
            "Runs a separate web service using the bind and access controls below."
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

        backend_frame = tk.Frame(frame)
        self._theme.apply_to_widget(backend_frame, "main_window")
        backend_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

        backend_label = tk.Label(backend_frame, text="Backend:")
        self._theme.apply_to_widget(backend_label, "label")
        backend_label.pack(side=tk.LEFT)

        self._backend_var = tk.StringVar(value="Checking...")
        backend_value = tk.Label(backend_frame, textvariable=self._backend_var)
        self._theme.apply_to_widget(backend_value, "label")
        backend_value.pack(side=tk.LEFT, padx=(8, 0))

        url_frame = tk.Frame(frame)
        self._theme.apply_to_widget(url_frame, "main_window")
        url_frame.pack(fill=tk.X, padx=16, pady=(0, 12))

        url_label = tk.Label(url_frame, text="Listening:")
        self._theme.apply_to_widget(url_label, "label")
        url_label.pack(side=tk.LEFT)

        self._listen_url_var = tk.StringVar(value="")
        url_value = tk.Label(url_frame, textvariable=self._listen_url_var)
        self._theme.apply_to_widget(url_value, "label")
        url_value.pack(side=tk.LEFT, padx=(8, 0))

        local_url_frame = tk.Frame(frame)
        self._theme.apply_to_widget(local_url_frame, "main_window")
        local_url_frame.pack(fill=tk.X, padx=16, pady=(0, 12))

        local_url_label = tk.Label(local_url_frame, text="Local URL:")
        self._theme.apply_to_widget(local_url_label, "label")
        local_url_label.pack(side=tk.LEFT)

        self._url_var = tk.StringVar(value="")
        local_url_value = tk.Label(local_url_frame, textvariable=self._url_var)
        self._theme.apply_to_widget(local_url_value, "label")
        local_url_value.pack(side=tk.LEFT, padx=(8, 0))

        security_frame = tk.Frame(frame)
        self._theme.apply_to_widget(security_frame, "main_window")
        security_frame.pack(fill=tk.X, padx=16, pady=(0, 12))

        security_label = tk.Label(security_frame, text="Security:")
        self._theme.apply_to_widget(security_label, "label")
        security_label.pack(side=tk.LEFT)

        self._security_var = tk.StringVar(value="Checking...")
        security_value = tk.Label(
            security_frame,
            textvariable=self._security_var,
            justify="left",
            anchor="w",
        )
        self._theme.apply_to_widget(security_value, "label")
        security_value.pack(side=tk.LEFT, padx=(8, 0))

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

            return SimpleNamespace(bind_address="127.0.0.1", port=2600)

    @staticmethod
    def _tls_active(cfg) -> bool:
        tls = getattr(cfg, "tls", None)
        return bool(tls and tls.enabled and tls.cert_file and tls.key_file)

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
        from experimental.webui.service_control import get_listen_url, get_url

        cfg = self._get_webui_cfg()
        host, port = cfg.bind_address, cfg.port
        tls = self._tls_active(cfg)
        self._listen_url_var.set(get_listen_url(host, port, tls=tls))
        self._url_var.set(get_url(host, port, tls=tls))
        security_var = getattr(self, "_security_var", None)
        if security_var is not None:
            remote = bool(getattr(cfg, "remote_enabled", False))
            if remote and not tls:
                security_var.set("Remote HTTP (plaintext)")
            elif remote:
                security_var.set("Remote HTTPS")
            elif tls:
                security_var.set("Localhost HTTPS")
            else:
                security_var.set("Localhost HTTP")

        def _check() -> None:
            from experimental.webui.service_control import get_status

            status = get_status(host, port)
            self._schedule_ui(lambda: self._apply_service_status(status))

        threading.Thread(target=_check, daemon=True).start()

    def _apply_service_status(self, status) -> None:
        backend_var = getattr(self, "_backend_var", None)
        if backend_var is not None:
            backend_var.set(status.backend)
        if status.running:
            self._apply_status(True)
            return
        if status.state == "stopped":
            self._apply_status(False)
            return
        self._apply_failed_status(status.reason or status.state)

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

        from experimental.webui.auth import (
            CredentialError, credential_exists, get_credential_usernames,
        )

        try:
            self._creds_existed_at_open = credential_exists()
            stored = get_credential_usernames()
        except CredentialError as exc:
            safe_messagebox.showerror(
                "Credential Store Error",
                f"Cannot open credentials: {exc}\n\nRepair file permissions before continuing.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self._multi_cred_error = len(stored) > 1
        self._stored_username_at_open = stored[0] if len(stored) == 1 else None
        self._cred_confirm_password_var = None

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

        first_focusable = None

        if self._multi_cred_error:
            err_label = tk.Label(
                outer,
                text=(
                    "Multiple credentials found — manage via CLI.\n"
                    "Expected exactly one account."
                ),
                justify="left",
                anchor="w",
                wraplength=320,
            )
            self._theme.apply_to_widget(err_label, "label")
            err_label.pack(fill=tk.X, pady=(0, 10))
        elif self._creds_existed_at_open:
            # Trusted desktop reset: workstation access authorizes replacement.
            user_row = tk.Frame(outer)
            self._theme.apply_to_widget(user_row, "main_window")
            user_row.pack(fill=tk.X, pady=(0, 6))
            user_label = tk.Label(user_row, text="Username:")
            self._theme.apply_to_widget(user_label, "label")
            user_label.pack(side=tk.LEFT)
            stored_name_label = tk.Label(
                user_row, text=self._stored_username_at_open or ""
            )
            self._theme.apply_to_widget(stored_name_label, "label")
            stored_name_label.pack(side=tk.LEFT, padx=(8, 0))
            self._cred_username_var = tk.StringVar(value=self._stored_username_at_open or "")

            notice = tk.Label(
                outer,
                text=(
                    "This trusted desktop reset does not require the current "
                    "password. Access to this workstation authorizes the change."
                ),
                justify="left",
                anchor="w",
                wraplength=420,
            )
            self._theme.apply_to_widget(notice, "label")
            notice.pack(fill=tk.X, pady=(0, 8))

            pass_row = tk.Frame(outer)
            self._theme.apply_to_widget(pass_row, "main_window")
            pass_row.pack(fill=tk.X, pady=(0, 6))
            pass_label = tk.Label(pass_row, text="New Password:")
            self._theme.apply_to_widget(pass_label, "label")
            pass_label.pack(side=tk.LEFT)
            self._cred_password_var = tk.StringVar(value="")
            pass_entry = tk.Entry(
                pass_row, textvariable=self._cred_password_var, show="*", width=32
            )
            self._theme.apply_to_widget(pass_entry, "entry")
            pass_entry.pack(side=tk.LEFT, padx=(8, 0))
            first_focusable = pass_entry

            confirm_row = tk.Frame(outer)
            self._theme.apply_to_widget(confirm_row, "main_window")
            confirm_row.pack(fill=tk.X, pady=(0, 8))
            confirm_label = tk.Label(confirm_row, text="Confirm New Password:")
            self._theme.apply_to_widget(confirm_label, "label")
            confirm_label.pack(side=tk.LEFT)
            self._cred_confirm_password_var = tk.StringVar(value="")
            confirm_entry = tk.Entry(
                confirm_row,
                textvariable=self._cred_confirm_password_var,
                show="*",
                width=32,
            )
            self._theme.apply_to_widget(confirm_entry, "entry")
            confirm_entry.pack(side=tk.LEFT, padx=(8, 0))
        else:
            # Bootstrap: no existing credential; username and password are editable.
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
            first_focusable = user_entry

            pass_row = tk.Frame(outer)
            self._theme.apply_to_widget(pass_row, "main_window")
            pass_row.pack(fill=tk.X, pady=(0, 8))
            pass_label = tk.Label(pass_row, text="Password:")
            self._theme.apply_to_widget(pass_label, "label")
            pass_label.pack(side=tk.LEFT)
            self._cred_password_var = tk.StringVar(value="")
            pass_entry = tk.Entry(
                pass_row, textvariable=self._cred_password_var, show="*", width=32
            )
            self._theme.apply_to_widget(pass_entry, "entry")
            pass_entry.pack(side=tk.LEFT, padx=(8, 0))

            confirm_row = tk.Frame(outer)
            self._theme.apply_to_widget(confirm_row, "main_window")
            confirm_row.pack(fill=tk.X, pady=(0, 8))
            confirm_label = tk.Label(confirm_row, text="Confirm Password:")
            self._theme.apply_to_widget(confirm_label, "label")
            confirm_label.pack(side=tk.LEFT)
            self._cred_confirm_password_var = tk.StringVar(value="")
            confirm_entry = tk.Entry(
                confirm_row,
                textvariable=self._cred_confirm_password_var,
                show="*",
                width=32,
            )
            self._theme.apply_to_widget(confirm_entry, "entry")
            confirm_entry.pack(side=tk.LEFT, padx=(8, 0))

        status_row = tk.Frame(outer)
        self._theme.apply_to_widget(status_row, "main_window")
        status_row.pack(fill=tk.X, pady=(0, 10))

        status_label = tk.Label(status_row, text="Status:")
        self._theme.apply_to_widget(status_label, "label")
        status_label.pack(side=tk.LEFT)

        self._cred_status_var = tk.StringVar(value="Ready")
        status_value = tk.Label(
            status_row,
            textvariable=self._cred_status_var,
            justify="left",
            anchor="w",
            wraplength=420,
        )
        self._theme.apply_to_widget(status_value, "label")
        status_value.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        btn_row = tk.Frame(outer)
        self._theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        self._save_creds_btn = tk.Button(
            btn_row,
            text="Save Credentials",
            command=self._on_save_credentials_dialog,
        )
        self._theme.apply_to_widget(self._save_creds_btn, "button_primary")
        if self._multi_cred_error:
            self._save_creds_btn.configure(state=tk.DISABLED)
        self._save_creds_btn.pack(side=tk.LEFT, padx=(0, 8))

        close_btn = tk.Button(btn_row, text="Close", command=self._close_credentials_dialog)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.LEFT)

        dialog.protocol("WM_DELETE_WINDOW", self._close_credentials_dialog)
        self._cred_dialog = dialog
        ensure_dialog_focus(dialog, parent)
        if first_focusable is not None:
            first_focusable.focus_set()

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
            self._cred_status_var.set(detail)
            self._cred_password_var.set("")
            confirm_var = getattr(self, "_cred_confirm_password_var", None)
            if confirm_var is not None:
                confirm_var.set("")
        else:
            reason = detail.strip() if detail else "credential save failed"
            self._cred_status_var.set(f"Failed: {reason}")
        self._save_creds_btn.configure(state=tk.NORMAL)

    def _on_save_credentials_dialog(self) -> None:
        if getattr(self, "_multi_cred_error", False):
            return

        self._save_creds_btn.configure(state=tk.DISABLED)
        self._cred_status_var.set("Saving...")

        username = (
            self._stored_username_at_open
            if self._creds_existed_at_open
            else self._cred_username_var.get()
        )
        password = self._cred_password_var.get()
        confirm_password = self._cred_confirm_password_var.get()

        if not str(username or "").strip():
            self._cred_status_var.set("Failed: username is required")
            self._save_creds_btn.configure(state=tk.NORMAL)
            return
        if not password:
            label = "new password" if self._creds_existed_at_open else "password"
            self._cred_status_var.set(f"Failed: {label} is required")
            self._save_creds_btn.configure(state=tk.NORMAL)
            return
        if not confirm_password:
            self._cred_status_var.set("Failed: password confirmation is required")
            self._save_creds_btn.configure(state=tk.NORMAL)
            return
        if password != confirm_password:
            self._cred_status_var.set("Failed: passwords do not match")
            self._save_creds_btn.configure(state=tk.NORMAL)
            return

        def _do_save() -> None:
            from experimental.webui.auth import (
                BlocklistUnavailableError,
                CredentialError,
                check_credential_store,
                set_password,
            )
            from experimental.webui.rate_limiter import clear_account_lockouts
            from experimental.webui.service_control import get_status, restart

            try:
                check_credential_store()
                set_password(username, password)
            except BlocklistUnavailableError:
                self._schedule_dialog_ui(
                    lambda: self._finish_credential_save_dialog(
                        False, "Configuration error: password blocklist unavailable"
                    )
                )
                return
            except (CredentialError, ValueError) as exc:
                message = str(exc)
                self._schedule_dialog_ui(
                    lambda: self._finish_credential_save_dialog(False, message)
                )
                return
            except Exception as exc:
                message = str(exc)
                self._schedule_dialog_ui(
                    lambda: safe_messagebox.showerror(
                        "Credential Save Failed", message, parent=self._cred_dialog
                    )
                )
                self._schedule_dialog_ui(
                    lambda: self._finish_credential_save_dialog(False, message)
                )
                return

            warnings = []
            try:
                clear_account_lockouts(username)
            except Exception as exc:
                warnings.append(
                    f"account lockouts could not be cleared: "
                    f"{self._normalize_cfg_error(exc)}"
                )

            lifecycle_note = ""
            try:
                cfg = self._get_webui_cfg()
                status = get_status(cfg.bind_address, cfg.port)
                should_restart = status.state == "running" or (
                    status.state == "unhealthy" and status.managed
                )
                if should_restart:
                    result = restart(cfg.bind_address, cfg.port)
                    if result.state in {"running", "already_running"}:
                        lifecycle_note = (
                            " Service restarted; existing browser sessions "
                            "were signed out."
                        )
                    else:
                        warnings.append(
                            "service restart failed: "
                            + (result.reason or result.state or "unknown error")
                        )
                elif status.state in {"stopped", "stale"}:
                    lifecycle_note = " Service remains stopped."
                elif status.state in {"unmanaged", "ambiguous"}:
                    warnings.append(
                        "service was not restarted because ownership could not "
                        "be confirmed"
                    )
                else:
                    warnings.append(
                        f"service was not restarted from state {status.state!r}"
                    )
            except Exception as exc:
                warnings.append(
                    f"service state handling failed: {self._normalize_cfg_error(exc)}"
                )

            message = f"Saved credentials for '{username}'.{lifecycle_note}"
            if warnings:
                message += " Warning: " + "; ".join(warnings)
            self._schedule_dialog_ui(
                lambda: self._finish_credential_save_dialog(True, message)
            )
            self._schedule_ui(self._refresh_status)

        threading.Thread(target=_do_save, daemon=True).start()

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
        self._cfg_initial_insecure_remote = bool(
            cfg.remote_enabled
            and not cfg.tls.enabled
            and cfg.tls.allow_insecure_remote
        )

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

        self._cfg_tls_enabled_var = tk.BooleanVar(value=cfg.tls.enabled)
        tls_enabled_row = tk.Frame(outer)
        self._theme.apply_to_widget(tls_enabled_row, "main_window")
        tls_enabled_row.pack(fill=tk.X, pady=(0, 2))
        tls_enabled_cb = tk.Checkbutton(
            tls_enabled_row,
            text="TLS Enabled",
            variable=self._cfg_tls_enabled_var,
            command=self._update_cfg_remote_warning,
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
            command=self._update_cfg_remote_warning,
        )
        self._theme.apply_to_widget(tls_insecure_cb, "checkbox")
        tls_insecure_cb.pack(anchor="w")
        self._update_cfg_remote_warning()

        self._cfg_tls_cert_var = tk.StringVar(value=cfg.tls.cert_file)
        _row("TLS Cert Path:", self._cfg_tls_cert_var, width=50)

        self._cfg_tls_key_var = tk.StringVar(value=cfg.tls.key_file)
        _row("TLS Key Path:", self._cfg_tls_key_var, width=50)

        self._cfg_allowlist_var = tk.StringVar(value=", ".join(cfg.allowed_cidrs))
        _row("Allowlist (CIDRs):", self._cfg_allowlist_var, width=50)

        self._cfg_trusted_hosts_var = tk.StringVar(
            value=", ".join(cfg.trusted_hosts)
        )
        _row("Trusted DNS Hosts:", self._cfg_trusted_hosts_var, width=50)

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
            from experimental.webui.config import normalize_remote_bind_address

            current_bind = self._cfg_bind_var.get().strip()
            effective_bind = normalize_remote_bind_address(current_bind, True)
            if effective_bind != current_bind:
                self._cfg_bind_var.set(effective_bind)
            if effective_bind == "0.0.0.0":
                exposure = "all IPv4 interfaces (0.0.0.0)"
            elif effective_bind == "::":
                exposure = "all IPv6 interfaces (::)"
            else:
                exposure = effective_bind
            self._cfg_remote_warn_label.configure(
                text=(
                    f"Warning: remote access listens on {exposure}. "
                    "Only clients matching the allowlist may proceed."
                    + (
                        " Remote HTTP (plaintext): credentials, cookies, and "
                        "data are not encrypted."
                        if (
                            not self._cfg_tls_enabled_var.get()
                            and self._cfg_tls_insecure_var.get()
                        )
                        else ""
                    )
                )
            )
            if not self._cfg_remote_warn_label.winfo_ismapped():
                self._cfg_remote_warn_label.pack(fill=tk.X, pady=(0, 8))
        else:
            current_bind = self._cfg_bind_var.get().strip()
            if current_bind == "0.0.0.0":
                self._cfg_bind_var.set("127.0.0.1")
            elif current_bind == "::":
                self._cfg_bind_var.set("::1")
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
        trusted_hosts_var = getattr(self, "_cfg_trusted_hosts_var", None)
        trusted_hosts = self._parse_cidrs(
            trusted_hosts_var.get() if trusted_hosts_var is not None else ""
        )
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
            trusted_hosts=trusted_hosts,
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

    def _confirm_insecure_remote_transition(self) -> bool:
        entering = (
            bool(self._cfg_remote_var.get())
            and not bool(self._cfg_tls_enabled_var.get())
            and bool(self._cfg_tls_insecure_var.get())
            and not bool(getattr(self, "_cfg_initial_insecure_remote", False))
        )
        if not entering:
            return True
        return safe_messagebox.askyesno(
            "Confirm Remote Plaintext HTTP",
            "Remote HTTP sends credentials, session cookies, and Dirracuda "
            "data without encryption.\n\nSave this configuration anyway?",
            parent=self._cfg_dialog,
        )

    def _finish_config_dialog(self, success: bool, message: str) -> None:
        if not self._cfg_dialog_exists():
            return
        self._cfg_status_var.set(message)
        self._set_config_save_controls(False)
        if success:
            self._refresh_status()

    def _save_config_from_dialog(self):
        from experimental.webui.config import normalize_config, save_config, validate

        cfg = normalize_config(self._build_config_from_dialog())
        validate(cfg)
        effective_cfg = save_config(cfg, path=self._get_webui_config_path())
        return effective_cfg if effective_cfg is not None else cfg

    def _on_save_config_dialog(self) -> None:
        if not self._cfg_dialog_exists():
            return
        if not self._confirm_insecure_remote_transition():
            self._cfg_status_var.set(
                "Save cancelled. Remote plaintext mode was not enabled."
            )
            return
        self._set_config_save_controls(True)
        self._cfg_status_var.set("Saving...")

        def _do() -> None:
            try:
                saved_cfg = self._save_config_from_dialog()
                self._cfg_initial_insecure_remote = bool(
                    saved_cfg.remote_enabled
                    and not saved_cfg.tls.enabled
                    and saved_cfg.tls.allow_insecure_remote
                )
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
        if not self._confirm_insecure_remote_transition():
            self._cfg_status_var.set(
                "Save cancelled. Remote plaintext mode was not enabled."
            )
            return
        self._set_config_save_controls(True)
        self._cfg_status_var.set("Saving and restarting...")
        previous_cfg = self._get_webui_cfg()

        def _do() -> None:
            from experimental.webui.service_control import restart

            try:
                new_cfg = self._save_config_from_dialog()
                self._cfg_initial_insecure_remote = bool(
                    new_cfg.remote_enabled
                    and not new_cfg.tls.enabled
                    and new_cfg.tls.allow_insecure_remote
                )
            except Exception as exc:
                self._schedule_cfg_dialog_ui(
                    lambda: self._finish_config_dialog(
                        False, f"Failed: {self._normalize_cfg_error(exc)}"
                    )
                )
                return

            try:
                start_result = restart(
                    new_cfg.bind_address,
                    new_cfg.port,
                    previous_host=previous_cfg.bind_address,
                    previous_port=previous_cfg.port,
                )
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
                backend_var = getattr(self, "_backend_var", None)
                if backend_var is not None:
                    backend_var.set(getattr(result, "backend", "direct"))
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
        from experimental.webui.service_control import get_url

        cfg = self._get_webui_cfg()
        tls = self._tls_active(cfg)
        webbrowser.open(get_url(cfg.bind_address, cfg.port, tls=tls))

    def _on_copy_url(self) -> None:
        from experimental.webui.service_control import get_url

        cfg = self._get_webui_cfg()
        tls = self._tls_active(cfg)
        url = get_url(cfg.bind_address, cfg.port, tls=tls)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(url)
        safe_messagebox.showinfo("Copied", f"URL copied: {url}", parent=self.frame)


def build_webui_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Web UI tab frame."""
    tab = WebUITab(parent, context)
    return tab.frame
