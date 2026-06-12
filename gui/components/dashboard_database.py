"""DB surface routing dialog — satellite of DashboardWidget."""
import sys
import tkinter as tk

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import apply_theme_to_window
from gui.components import dashboard_experimental


def _mb():
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def open_db_surface(widget) -> None:
    dialog = tk.Toplevel(widget.parent)
    dialog.title("Database")
    dialog.transient(widget.parent)
    dialog.resizable(False, False)
    if widget.theme:
        apply_theme_to_window(dialog)

    def _pick(choice: str) -> None:
        dialog.destroy()
        if choice == "servers":
            widget._open_drill_down("server_list")
        elif choice == "tools":
            db_reader = getattr(widget, "db_reader", None)
            if db_reader is None:
                _mb().showerror("No Database", "No database is currently loaded.",
                                parent=widget.parent)
                return
            from gui.components.db_tools_dialog import show_db_tools_dialog
            show_db_tools_dialog(
                parent=widget.parent,
                db_path=str(db_reader.db_path),
                on_database_changed=widget.refresh_after_database_change,
            )
        elif choice == "sidecar":
            dashboard_experimental.open_sidecar_legacy_db(widget)

    for label, key in [
        ("\U0001F4CB View Servers", "servers"),
        ("\U0001F5C4 DB Tools", "tools"),
        ("\U0001F4E6 [Legacy] Sidecar Data", "sidecar"),
    ]:
        btn = tk.Button(dialog, text=label, command=lambda k=key: _pick(k))
        widget.theme.apply_to_widget(btn, "button_secondary")
        btn.pack(fill="x", padx=12, pady=4)
    ensure_dialog_focus(dialog, widget.parent)
