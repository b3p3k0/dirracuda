"""Placeholder Help/Manual dialog used by global keyboard shortcuts."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from gui.utils.keybindings import bind_close_shortcuts


def open_help_stub_dialog(parent: tk.Misc, *, theme: Any = None) -> Optional[tk.Toplevel]:
    """Open (or focus) lightweight Help placeholder dialog."""
    if parent is None:
        return None

    existing = getattr(parent, "_help_stub_dialog", None)
    try:
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return existing
    except Exception:
        pass

    dialog = tk.Toplevel(parent)
    setattr(parent, "_help_stub_dialog", dialog)
    dialog.title("Help / Manual")
    dialog.transient(parent)
    dialog.resizable(False, False)

    frame = tk.Frame(dialog, padx=14, pady=12)
    frame.pack(fill=tk.BOTH, expand=True)

    heading = tk.Label(frame, text="Help is coming soon.", anchor="w", justify="left")
    heading.pack(anchor="w")
    body = tk.Label(
        frame,
        text=(
            "A full in-app manual will be added in a later task.\n"
            "For now, see README.md and docs/TECHNICAL_REFERENCE.md."
        ),
        anchor="w",
        justify="left",
    )
    body.pack(anchor="w", pady=(6, 10))

    button_row = tk.Frame(frame)
    button_row.pack(fill=tk.X)
    close_btn = tk.Button(button_row, text="Close", command=dialog.destroy)
    close_btn.pack(side=tk.RIGHT)

    bind_close_shortcuts(dialog, dialog.destroy)

    if theme is not None:
        try:
            theme.apply_theme_to_application(dialog)
        except Exception:
            pass

    def _on_destroy(_event=None) -> None:
        if getattr(parent, "_help_stub_dialog", None) is dialog:
            setattr(parent, "_help_stub_dialog", None)

    dialog.bind("<Destroy>", _on_destroy, add="+")
    close_btn.focus_set()
    return dialog

