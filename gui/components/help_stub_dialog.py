"""Compatibility wrapper for the in-app User Manual dialog entrypoint."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from gui.components.help_manual_dialog import open_help_manual_dialog


def open_help_stub_dialog(parent: tk.Misc, *, theme: Any = None) -> Optional[tk.Toplevel]:
    """Backward-compatible alias retained for existing call sites/tests."""
    return open_help_manual_dialog(parent, theme=theme)
