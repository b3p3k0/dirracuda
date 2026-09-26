"""The About dialog for DashboardWidget.

A satellite of ``gui.dashboard.widget``. Each function takes the dashboard
instance as ``dash``; module-level names resolve through ``_d()`` so the
frozen ``gui.components.dashboard.*`` patch paths stay valid.
"""

import sys
import tkinter as tk
import webbrowser
from typing import Any, Optional

from gui.components.help_manual_dialog import open_help_manual_dialog
from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.keybindings import (
    add_shortcut_hint,
    bind_close_shortcuts,
    bind_submit_shortcuts,
)
from gui.utils.style import apply_theme_to_window


def _w(name: str):
    """Resolve a name from gui.dashboard.widget at call-time.

    Some tests patch gui.dashboard.widget.<name> rather than the shim. The
    widget imports this module, so the lookup goes through sys.modules rather
    than an import, which would be a cycle.
    """
    mod = sys.modules.get("gui.dashboard.widget")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return globals()[name]


def _mb():
    """Return messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox. Calling through this
    helper means the patched object is used at call-time, preserving all
    frozen patch paths.
    Falls back to the real safe_messagebox if dashboard is not yet loaded.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str) -> Any:
    """Resolve a name from gui.components.dashboard at call-time.

    Tests patch gui.components.dashboard.<name>. Using this helper ensures
    the patched binding is used rather than a cached import-time reference.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )


def open_about_dialog(dash) -> None:
    dialog = tk.Toplevel(dash.parent)
    dialog.title("About Dirracuda")
    dialog.transient(dash.parent)
    dialog.grab_set()
    if dash.theme:
        apply_theme_to_window(dialog)

    body = tk.Frame(dialog)
    dash.theme.apply_to_widget(body, "main_window")
    body.pack(padx=18, pady=16, fill=tk.BOTH, expand=True)

    title = tk.Label(
        body,
        text="Dirracuda",
        font=(None, 14, "bold"),
        bg=dash.theme.colors["primary_bg"],
        fg=dash.theme.colors["text"],
    )
    title.pack(anchor="w")

    blurb = (
        "Dirracuda helps defensive analysts find exposed servers (SMB, FTP, HTTP)\n"
        "with weak authentication and demonstrate impact via safe, guided workflows.\n"
        "No warranty expressed or implied; use at your own risk."
    )
    tk.Label(
        body,
        text=blurb,
        justify="left",
        anchor="w",
        bg=dash.theme.colors["primary_bg"],
        fg=dash.theme.colors["text"],
    ).pack(anchor="w", pady=(6, 10))

    link = tk.Label(
        body,
        text="GitHub: https://github.com/b3p3k0/dirracuda",
        fg=dash.theme.colors["accent"],
        bg=dash.theme.colors["primary_bg"],
        cursor="hand2",
    )
    link.pack(anchor="w")
    link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/b3p3k0/dirracuda"))

    btn_frame = tk.Frame(body)
    dash.theme.apply_to_widget(btn_frame, "main_window")
    btn_frame.pack(fill=tk.X, pady=(12, 0))

    manual_button = tk.Button(
        btn_frame,
        text="User Manual",
        command=lambda: dash._open_user_manual_from_about(dialog),
    )
    dash.theme.apply_to_widget(manual_button, "button_secondary")
    manual_button.pack(side=tk.RIGHT, padx=(0, 6))

    close_button = tk.Button(btn_frame, text="Close", command=dialog.destroy)
    dash.theme.apply_to_widget(close_button, "button_secondary")
    close_button.pack(side=tk.RIGHT)
    add_shortcut_hint(
        btn_frame,
        dash.theme,
        "Enter close  •  Esc/Ctrl+W/Cmd+W close",
    )
    bind_submit_shortcuts(dialog, dialog.destroy, allow_text_submit_with_enter=True)
    bind_close_shortcuts(dialog, dialog.destroy)

    dialog.update_idletasks()
    dialog.lift()
    dialog.focus_set()

def open_user_manual_from_about(dash, about_dialog: Optional[tk.Misc] = None) -> None:
    """Close About dialog (if present) then open/focus the shared User Manual window."""
    try:
        if about_dialog is not None:
            about_dialog.destroy()
    except Exception:
        pass
    _w("open_help_manual_dialog")(dash.parent, theme=dash.theme)
