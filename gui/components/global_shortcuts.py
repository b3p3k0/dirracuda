"""Global shortcut registration for the Dirracuda app shell."""

from __future__ import annotations

import tkinter as tk
from typing import Any

from gui.components.help_manual_dialog import open_help_manual_dialog
from gui.utils.keybindings import bind_global_app_shortcuts


def register_global_shortcuts(root: tk.Misc, dashboard: Any, on_quit: Any) -> None:
    """Register app-wide Ctrl/Cmd+Q/H/T shortcuts."""
    bind_global_app_shortcuts(
        root,
        on_quit=on_quit,
        on_help=lambda: open_help_manual_dialog(root, theme=getattr(dashboard, "theme", None)),
        on_theme_toggle=lambda: getattr(dashboard, "_toggle_theme", lambda: None)(),
    )
