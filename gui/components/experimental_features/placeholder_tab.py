"""
placeholder tab for the Experimental Features dialog.

Scaffold for future experimental modules. Content is sourced from the
experimental.placeholder module so that adding real functionality only
requires updating that module — no dialog surgery needed.
"""

from __future__ import annotations

import tkinter as tk

from gui.utils.style import get_theme


def build_placeholder_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the placeholder tab frame."""
    try:
        from experimental.placeholder import get_description
        description = get_description()
    except ImportError:
        description = "This tab is a scaffold for future experimental modules."

    theme = get_theme()
    frame = tk.Frame(parent)
    theme.apply_to_widget(frame, "main_window")

    label = tk.Label(
        frame,
        text="Coming soon",
        anchor="w",
        font=(None, 11, "bold"),
    )
    theme.apply_to_widget(label, "label")
    label.pack(anchor="w", padx=16, pady=(16, 6))

    desc_label = tk.Label(
        frame,
        text=description,
        justify="left",
        anchor="w",
        wraplength=480,
    )
    theme.apply_to_widget(desc_label, "label")
    desc_label.pack(anchor="w", padx=16, pady=(0, 12))

    return frame
