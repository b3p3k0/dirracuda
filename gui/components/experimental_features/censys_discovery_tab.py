"""
Censys Discovery tab for the Experimental Features dialog.

C2 scaffold — no live API calls. Run and Open Results are disabled until
the config (C3) and client (C4) layers are wired.
"""

from __future__ import annotations

import tkinter as tk

from gui.utils.style import get_theme

_PROTOCOLS = ("FTP", "HTTP", "SMB")
_PLACEHOLDER_STATUS = "Provider: Censys Platform v3 (not configured)"
_PLACEHOLDER_CREDIT = "Credit estimate: — (available after C3/C4)"
_PLACEHOLDER_BALANCE = "Live balance: — (not available)"


class CensysDiscoveryTab:
    """Content widget for the Censys Discovery experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        desc_label = tk.Label(
            frame,
            text=(
                "Censys Discovery — experimental Censys Platform v3 sidecar.\n"
                "This scaffold is read-only. Run and Open Results are disabled\n"
                "until the config and client layers are wired (C3/C4)."
            ),
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._theme.apply_to_widget(desc_label, "label")
        desc_label.pack(anchor="w", padx=16, pady=(16, 12))

        status_label = tk.Label(frame, text=_PLACEHOLDER_STATUS, anchor="w")
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
            query_row, text="— (available after C3/C4)", anchor="w"
        )
        self._theme.apply_to_widget(query_placeholder, "label")
        query_placeholder.pack(side=tk.LEFT, padx=(6, 0))

        credit_label = tk.Label(frame, text=_PLACEHOLDER_CREDIT, anchor="w")
        self._theme.apply_to_widget(credit_label, "label")
        credit_label.pack(anchor="w", padx=16, pady=(0, 4))

        balance_label = tk.Label(frame, text=_PLACEHOLDER_BALANCE, anchor="w")
        self._theme.apply_to_widget(balance_label, "label")
        balance_label.pack(anchor="w", padx=16, pady=(0, 8))

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
        self._results_btn.configure(state="disabled")
        self._results_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(frame, text="", anchor="w")
        self._theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(anchor="w", padx=16, pady=(4, 0))

    def _invoke_run(self) -> None:
        pass  # intentional no-op; button is disabled in this scaffold

    def _invoke_open_results(self) -> None:
        cb = self._context.get("open_censys_results_db")
        if cb is not None:
            cb()


def build_censys_discovery_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Censys Discovery tab frame."""
    tab = CensysDiscoveryTab(parent, context)
    return tab.frame
