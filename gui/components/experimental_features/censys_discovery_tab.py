"""
Censys Discovery tab for the Experimental Features dialog.

Credit UX wired (C9): config-derived tier estimate + live balance fetch on open.
Run is disabled until discovery run wiring is added in a future card.
Open Results is enabled when an open_censys_results_db callback is present in context.
"""

from __future__ import annotations

import threading
import tkinter as tk

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

        sm = context.get("settings_manager")
        self._cfg = None
        if sm is not None:
            try:
                from shared.config import load_config
                self._cfg = load_config(sm.get_smbseek_config_path())
            except Exception:
                pass

        self._build(self.frame)
        self._refresh_balance()

    def _build(self, frame: tk.Frame) -> None:
        desc_label = tk.Label(
            frame,
            text=(
                "Censys Discovery — Censys Platform v3 sidecar.\n"
                "Run will be enabled in a future card. Open Results is available when prior\n"
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

        proto_label = tk.Label(proto_row, text="Protocol:", anchor="w", width=14)
        self._theme.apply_to_widget(proto_label, "label")
        proto_label.pack(side=tk.LEFT)

        self._protocol_var = tk.StringVar(value=_PROTOCOLS[0])
        for proto in _PROTOCOLS:
            rb = tk.Radiobutton(
                proto_row,
                text=proto,
                value=proto,
                variable=self._protocol_var,
            )
            self._theme.apply_to_widget(rb, "checkbox")
            rb.pack(side=tk.LEFT, padx=(6, 0))

        query_row = tk.Frame(frame)
        self._theme.apply_to_widget(query_row, "main_window")
        query_row.pack(anchor="w", padx=16, pady=(0, 6), fill=tk.X)

        query_label = tk.Label(query_row, text="Query:", anchor="w", width=14)
        self._theme.apply_to_widget(query_label, "label")
        query_label.pack(side=tk.LEFT)

        query_placeholder = tk.Label(
            query_row, text="— (run wiring pending)", anchor="w"
        )
        self._theme.apply_to_widget(query_placeholder, "label")
        query_placeholder.pack(side=tk.LEFT, padx=(6, 0))

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
        self._run_btn.configure(state="disabled")
        self._run_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._results_btn = tk.Button(
            btn_frame, text="Open Results", command=self._invoke_open_results
        )
        self._theme.apply_to_widget(self._results_btn, "button_secondary")
        open_results_state = (
            "normal" if self._context.get("open_censys_results_db") else "disabled"
        )
        self._results_btn.configure(state=open_results_state)
        self._results_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(frame, text="", anchor="w")
        self._theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(anchor="w", padx=16, pady=(4, 0))

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
            result = client.get_org_credits(org_id) if org_id else client.get_user_credits()
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

    def _invoke_run(self) -> None:
        pass  # intentional no-op; run wiring is pending

    def _invoke_open_results(self) -> None:
        cb = self._context.get("open_censys_results_db")
        if cb is not None:
            cb()


def build_censys_discovery_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Censys Discovery tab frame."""
    tab = CensysDiscoveryTab(parent, context)
    return tab.frame
