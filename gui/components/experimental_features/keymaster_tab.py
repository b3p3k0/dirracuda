"""
Keymaster tab for the Experimental Features dialog.

Provides one launch action:
  - Open Keymaster
"""

from __future__ import annotations

import tkinter as tk

from gui.utils.style import get_theme


class KeymasterTab:
    """Content widget for the Keymaster experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        description = (
            "Keymaster stores reusable API keys for rapid testing key rotation.\n"
            "Select and apply a key to update the active Shodan API key instantly."
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
            text="Open Keymaster",
            command=self._invoke_open_keymaster,
        )
        self._theme.apply_to_widget(self._open_btn, "button_primary")
        self._open_btn.pack(side=tk.LEFT)

    def _invoke_open_keymaster(self) -> None:
        cb = self._context.get("open_keymaster")
        if cb is not None:
            cb()


def build_keymaster_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Keymaster tab frame."""
    tab = KeymasterTab(parent, context)
    return tab.frame
