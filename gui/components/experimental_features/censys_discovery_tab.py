"""
Censys Discovery tab for the Experimental Features dialog.

Credit UX wired (C9): config-derived tier estimate + live balance fetch on open.
Run UX wired (C10): selected protocols run sequentially (FTP -> HTTP -> SMB)
and stop on first failure.
Open Results is enabled when an open_censys_results_db callback is present in context.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple

from gui.utils.style import get_theme

_PROTOCOLS = ("FTP", "HTTP", "SMB")
_PLACEHOLDER_STATUS = "Provider: Censys Platform v3 (not configured)"
_PLACEHOLDER_CREDIT = "Credit estimate: — (PAT not configured)"
_PLACEHOLDER_BALANCE = "Live balance: — (not available)"


def _credit_estimate_text(cfg) -> str:
    if cfg is None:
        return _PLACEHOLDER_CREDIT
    try:
        return f"Credit profile: {cfg.get_censys_credit_profile()}"
    except Exception:
        return _PLACEHOLDER_CREDIT


def _status_text(cfg) -> str:
    if cfg is None:
        return _PLACEHOLDER_STATUS
    try:
        cfg.get_censys_pat()  # raises ValueError if empty
        return "Provider: Censys Platform v3 (configured)"
    except ValueError:
        return _PLACEHOLDER_STATUS
    except Exception:
        return _PLACEHOLDER_STATUS


class CensysDiscoveryTab:
    """Content widget for the Censys Discovery experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")

        self._cfg = self._load_config_from_settings()
        self._protocol_vars: Dict[str, tk.BooleanVar] = {}
        self._protocol_checkbuttons: List[tk.Checkbutton] = []

        self._build(self.frame)
        self._refresh_balance()

    def _load_config_from_settings(self):
        sm = self._context.get("settings_manager")
        if sm is None:
            return None
        try:
            from shared.config import load_config

            return load_config(sm.get_smbseek_config_path())
        except Exception:
            return None

    def _build(self, frame: tk.Frame) -> None:
        desc_label = tk.Label(
            frame,
            text=(
                "Censys Discovery — Censys Platform v3 sidecar.\n"
                "Run selected protocols sequentially. Open Results is available when prior\n"
                "scan results exist."
            ),
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._theme.apply_to_widget(desc_label, "label")
        desc_label.pack(anchor="w", padx=16, pady=(16, 12))

        status_label = tk.Label(frame, text=_status_text(self._cfg), anchor="w")
        self._theme.apply_to_widget(status_label, "label")
        status_label.pack(anchor="w", padx=16, pady=(0, 6))

        proto_row = tk.Frame(frame)
        self._theme.apply_to_widget(proto_row, "main_window")
        proto_row.pack(anchor="w", padx=16, pady=(0, 6), fill=tk.X)

        proto_label = tk.Label(proto_row, text="Protocols:", anchor="w", width=14)
        self._theme.apply_to_widget(proto_label, "label")
        proto_label.pack(side=tk.LEFT)

        for proto in _PROTOCOLS:
            var = tk.BooleanVar(value=True)
            cb = tk.Checkbutton(proto_row, text=proto, variable=var)
            self._theme.apply_to_widget(cb, "checkbox")
            cb.pack(side=tk.LEFT, padx=(6, 0))
            self._protocol_vars[proto] = var
            self._protocol_checkbuttons.append(cb)

        query_row = tk.Frame(frame)
        self._theme.apply_to_widget(query_row, "main_window")
        query_row.pack(anchor="w", padx=16, pady=(0, 6), fill=tk.X)

        query_label = tk.Label(query_row, text="Query:", anchor="w", width=14)
        self._theme.apply_to_widget(query_label, "label")
        query_label.pack(side=tk.LEFT)

        query_text = tk.Label(
            query_row, text="Protocol baseline + config defaults", anchor="w"
        )
        self._theme.apply_to_widget(query_text, "label")
        query_text.pack(side=tk.LEFT, padx=(6, 0))

        credit_label = tk.Label(frame, text=_credit_estimate_text(self._cfg), anchor="w")
        self._theme.apply_to_widget(credit_label, "label")
        credit_label.pack(anchor="w", padx=16, pady=(0, 4))

        self._balance_label = tk.Label(frame, text=_PLACEHOLDER_BALANCE, anchor="w")
        self._theme.apply_to_widget(self._balance_label, "label")
        self._balance_label.pack(anchor="w", padx=16, pady=(0, 8))

        btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))

        self._run_btn = tk.Button(btn_frame, text="Run", command=self._invoke_run)
        self._theme.apply_to_widget(self._run_btn, "button_primary")
        self._run_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._results_btn = tk.Button(
            btn_frame, text="Open Results", command=self._invoke_open_results
        )
        self._theme.apply_to_widget(self._results_btn, "button_secondary")
        self._results_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._config_btn = tk.Button(
            btn_frame, text="Config", command=self._invoke_open_config
        )
        self._theme.apply_to_widget(self._config_btn, "button_secondary")
        self._config_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(frame, text="", anchor="w")
        self._theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(anchor="w", padx=16, pady=(4, 0))

        self._set_controls_running(False)

    def _refresh_balance(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        try:
            pat = cfg.get_censys_pat()
        except ValueError:
            self._balance_label.configure(text="Live balance: PAT not configured")
            return
        try:
            org_id = cfg.get_censys_org_id()
        except ValueError:
            self._balance_label.configure(
                text="Live balance: invalid organization_id in config"
            )
            return
        t = threading.Thread(
            target=self._fetch_balance_worker, args=(pat, org_id), daemon=True
        )
        t.start()

    def _fetch_balance_worker(self, pat: str, org_id) -> None:
        from experimental.censys_discovery.client import CensysClient

        try:
            client = CensysClient(pat=pat)
            result = (
                client.get_org_credits(org_id) if org_id else client.get_user_credits()
            )
            if result.ok:
                bal = result.data
                text = f"Live balance: {bal.balance} credits"
                if getattr(bal, "resets_at", None):
                    text += f" (resets {bal.resets_at})"
            else:
                text = "Live balance: unavailable — check API key"
        except Exception:
            text = "Live balance: unavailable — check API key"
        try:
            self.frame.after(0, lambda: self._balance_label.configure(text=text))
        except Exception:
            pass  # widget destroyed before worker completed

    def _resolve_selected_protocols(self) -> List[str]:
        selected: List[str] = []
        for proto in _PROTOCOLS:
            var = self._protocol_vars.get(proto)
            if var is not None and bool(var.get()):
                selected.append(proto)
        return selected

    def _set_controls_running(self, running: bool) -> None:
        run_state = "disabled" if running else "normal"
        self._run_btn.configure(state=run_state)
        for cb in self._protocol_checkbuttons:
            cb.configure(state=run_state)

        results_state = "disabled" if running else (
            "normal" if self._context.get("open_censys_results_db") else "disabled"
        )
        self._results_btn.configure(state=results_state)

        config_state = "disabled" if running else (
            "normal" if self._context.get("open_app_config") else "disabled"
        )
        self._config_btn.configure(state=config_state)

    @staticmethod
    def _error_message(result: Any) -> str:
        msg = str(getattr(result, "error", "") or "").strip()
        return msg or "unknown error"

    def _invoke_run(self) -> None:
        selected = self._resolve_selected_protocols()
        if not selected:
            self._status_label.configure(text="Select at least one protocol first.")
            return

        cfg = self._load_config_from_settings()
        self._cfg = cfg
        if cfg is None:
            self._status_label.configure(text="Run failed: configuration unavailable.")
            return

        try:
            pat = cfg.get_censys_pat()
        except ValueError:
            self._status_label.configure(text="Run failed: Censys PAT is not configured.")
            return
        except Exception:
            self._status_label.configure(text="Run failed: unable to read Censys PAT.")
            return

        try:
            org_id = cfg.get_censys_org_id()
        except ValueError:
            self._status_label.configure(
                text="Run failed: invalid censys.organization_id in config."
            )
            return

        defaults = {}
        try:
            defaults = cfg.get_censys_defaults() or {}
        except Exception:
            defaults = {}

        query_hours = int(defaults.get("query_hours", 24))
        max_pages = int(defaults.get("max_pages", 5))
        page_size = int(defaults.get("page_size", 100))

        self._set_controls_running(True)
        self._status_label.configure(
            text=f"Running Censys discovery: {', '.join(selected)}..."
        )

        t = threading.Thread(
            target=self._run_stack_worker,
            args=(selected, pat, org_id, query_hours, max_pages, page_size),
            daemon=True,
        )
        t.start()

    def _run_stack_worker(
        self,
        selected: List[str],
        pat: str,
        org_id: Optional[str],
        query_hours: int,
        max_pages: int,
        page_size: int,
    ) -> None:
        from experimental.censys_discovery.models import CensysRunOptions
        from experimental.censys_discovery.service import (
            run_ftp_discovery,
            run_http_discovery,
            run_smb_discovery,
        )

        runner_by_protocol = {
            "FTP": run_ftp_discovery,
            "HTTP": run_http_discovery,
            "SMB": run_smb_discovery,
        }

        summaries: List[Tuple[str, Any]] = []
        failed_protocol: Optional[str] = None
        failed_message: Optional[str] = None

        for proto in selected:
            runner = runner_by_protocol[proto]
            try:
                options = CensysRunOptions(
                    pat=pat,
                    protocol=proto,
                    query_hours=query_hours,
                    max_pages=max_pages,
                    page_size=page_size,
                    org_id=org_id,
                )
                result = runner(options)
            except Exception as exc:
                failed_protocol = proto
                failed_message = str(exc) or "unknown error"
                break

            summaries.append((proto, result))
            if not result.ok:
                failed_protocol = proto
                failed_message = self._error_message(result)
                break

        try:
            self.frame.after(
                0,
                lambda: self._on_run_stack_done(
                    summaries, failed_protocol, failed_message
                ),
            )
        except Exception:
            pass

    def _on_run_stack_done(
        self,
        summaries: List[Tuple[str, Any]],
        failed_protocol: Optional[str],
        failed_message: Optional[str],
    ) -> None:
        self._set_controls_running(False)

        if failed_protocol is not None:
            completed = [
                f"{proto}: fetched {result.fetched_count}, stored {result.deduped_count}"
                for proto, result in summaries
                if getattr(result, "ok", False)
            ]
            text = f"Run failed at {failed_protocol}: {failed_message or 'unknown error'}"
            if completed:
                text += f"\nCompleted: {'; '.join(completed)}"
        else:
            parts = [
                f"{proto}: fetched {result.fetched_count}, stored {result.deduped_count}"
                for proto, result in summaries
            ]
            text = f"Done — {'; '.join(parts)}" if parts else "Done — no protocols ran."

        self._status_label.configure(text=text)
        self._cfg = self._load_config_from_settings()
        self._refresh_balance()

    def _invoke_open_results(self) -> None:
        cb = self._context.get("open_censys_results_db")
        if cb is not None:
            cb()

    def _invoke_open_config(self) -> None:
        cb = self._context.get("open_app_config")
        if cb is not None:
            cb()


def build_censys_discovery_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Censys Discovery tab frame."""
    tab = CensysDiscoveryTab(parent, context)
    return tab.frame
