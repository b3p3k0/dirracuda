"""
Experimental Features dialog.

Opens a modeless dialog with a tab-per-feature layout sourced from the
feature registry. Shows a one-time informational warning on first open;
the dismiss preference is persisted via settings_manager immediately on
checkbox toggle (not deferred to close).

Usage:
    show_experimental_features_dialog(parent, context, settings_manager)

context keys (all optional — missing keys disable the relevant action):
    reddit_grab_callback    : callable() — launch Reddit Grab dialog
    open_reddit_post_db     : callable() — launch Reddit Post DB browser
    parent                  : tk.Widget  — dashboard parent (informational)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Optional

from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus

_DISMISSED_KEY = "experimental.warning_dismissed"
_WARNING_TEXT = (
    "These features are experimental and may be unstable or incomplete.\n"
    "Use them with care in production environments."
)


class ExperimentalFeaturesDialog:
    """Modeless dialog hosting experimental feature tabs."""

    def __init__(
        self,
        parent: tk.Widget,
        context: dict,
        settings_manager: Any,
    ) -> None:
        self.parent = parent
        self._context = context
        self._settings_manager = settings_manager
        self._theme = get_theme()

        # Exposed for tests
        self._warning_frame_built: bool = False
        self.dismiss_var: Optional[tk.BooleanVar] = None

        self._build(parent, context, settings_manager)

    def _build(self, parent: tk.Widget, context: dict, settings_manager: Any) -> None:
        dialog = tk.Toplevel(parent)
        dialog.title("Experimental Features")
        dialog.geometry("580x420")
        dialog.resizable(True, True)
        self._theme.apply_to_widget(dialog, "main_window")
        dialog.transient(parent)

        outer = tk.Frame(dialog)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        self._build_warning_section(outer, settings_manager)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        from gui.components.experimental_features.registry import build_all_tabs
        tab_context = {**context, "settings_manager": settings_manager}
        build_all_tabs(notebook, tab_context)

        btn_frame = tk.Frame(outer)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, pady=(8, 0))

        close_btn = tk.Button(btn_frame, text="Close", command=dialog.destroy)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

        ensure_dialog_focus(dialog, parent)

    def _build_warning_section(self, parent: tk.Widget, settings_manager: Any) -> None:
        """Build the dismissible warning banner if not yet dismissed."""
        dismissed = False
        if settings_manager is not None:
            try:
                dismissed = bool(
                    settings_manager.get_setting(_DISMISSED_KEY, False)
                )
            except Exception:
                dismissed = False

        self._warning_frame_built = not dismissed

        if dismissed:
            return

        warn_frame = tk.Frame(parent, relief="flat", bd=1)
        self._theme.apply_to_widget(warn_frame, "card")
        warn_frame.pack(fill=tk.X, pady=(0, 6))

        warn_label = tk.Label(
            warn_frame,
            text=_WARNING_TEXT,
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self._theme.apply_to_widget(warn_label, "label")
        warn_label.pack(anchor="w", padx=10, pady=(8, 4))

        self.dismiss_var = tk.BooleanVar(value=False)

        def _on_dismiss_toggled(*_args) -> None:
            if self.dismiss_var.get() and settings_manager is not None:
                try:
                    settings_manager.set_setting(_DISMISSED_KEY, True)
                except Exception:
                    pass

        self.dismiss_var.trace_add("write", _on_dismiss_toggled)

        cb = tk.Checkbutton(
            warn_frame,
            text="Don't show this notice again",
            variable=self.dismiss_var,
        )
        self._theme.apply_to_widget(cb, "checkbox")
        cb.pack(anchor="w", padx=10, pady=(0, 8))


def show_experimental_features_dialog(
    parent: tk.Widget,
    context: dict,
    settings_manager: Any,
) -> None:
    """Open the Experimental Features dialog."""
    ExperimentalFeaturesDialog(parent, context, settings_manager)
