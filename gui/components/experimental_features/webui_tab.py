"""
Web UI tab for the Experimental Features dialog.

Provides a single action: Open Web UI Control — opens the service control dialog.
"""

from __future__ import annotations

import tkinter as tk

from gui.utils.style import get_theme


class WebUITab:
    """Content widget for the Web UI experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

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

        self._open_btn = tk.Button(
            frame,
            text="Open Web UI Control",
            command=self._invoke_open_control,
        )
        self._theme.apply_to_widget(self._open_btn, "button_primary")
        self._open_btn.pack(anchor="w", padx=16, pady=(0, 8))

    def _invoke_open_control(self) -> None:
        cb = self._context.get("open_webui_control")
        if cb is not None:
            cb()


def build_webui_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Web UI tab frame."""
    tab = WebUITab(parent, context)
    return tab.frame
