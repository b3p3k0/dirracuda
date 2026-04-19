"""
Dorkbook tab for the Experimental Features dialog.

Provides one launch action:
  - Open Dorkbook
"""

from __future__ import annotations

import tkinter as tk

from gui.utils.style import get_theme


class DorkbookTab:
    """Content widget for the Dorkbook experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        description = (
            "Dorkbook stores reusable dork recipes in a sidecar DB.\n"
            "Built-ins stay read-only, and custom recipes can be managed per protocol."
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

        btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))

        self._open_btn = tk.Button(
            btn_frame,
            text="Open Dorkbook",
            command=self._invoke_open_dorkbook,
        )
        self._theme.apply_to_widget(self._open_btn, "button_primary")
        self._open_btn.pack(side=tk.LEFT)

    def _invoke_open_dorkbook(self) -> None:
        cb = self._context.get("open_dorkbook")
        if cb is not None:
            cb()


def build_dorkbook_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Dorkbook tab frame."""
    tab = DorkbookTab(parent, context)
    return tab.frame

