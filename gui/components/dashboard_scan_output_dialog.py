"""
Non-modal live scan output dialog for DashboardWidget.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional


def _dialog_exists(dash) -> bool:
    dialog = getattr(dash, "scan_output_dialog", None)
    return bool(dialog and dialog.winfo_exists())


def ensure_scan_output_dialog(dash) -> None:
    """Create the scan output dialog once and keep it hide/reopen capable."""
    if _dialog_exists(dash):
        return

    dialog = tk.Toplevel(dash.parent)
    dialog.title("Live Scan Output")
    dialog.geometry("900x420")
    dialog.minsize(720, 320)
    dialog.transient(dash.parent)
    dialog.protocol("WM_DELETE_WINDOW", lambda: hide_scan_output_dialog(dash))
    dash.theme.apply_to_widget(dialog, "main_window")

    frame = tk.Frame(dialog, bg=dash.theme.colors["card_bg"])
    frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    header = tk.Frame(frame, bg=dash.theme.colors["card_bg"])
    header.pack(fill=tk.X, pady=(0, 6))

    title_var = tk.StringVar(master=dialog, value="Live Scan Output")
    header_label = tk.Label(
        header,
        textvariable=title_var,
        bg=dash.theme.colors["card_bg"],
        fg=dash.theme.colors["text"],
        font=dash.theme.fonts["heading"],
    )
    header_label.pack(side=tk.LEFT)

    copy_button = tk.Button(header, text="Copy All", command=dash._copy_log_output)
    dash.theme.apply_to_widget(copy_button, "button_secondary")
    copy_button.pack(side=tk.RIGHT, padx=(6, 0))

    jump_button = tk.Button(header, text="Jump to Latest", command=dash._scroll_log_to_latest)
    dash.theme.apply_to_widget(jump_button, "button_secondary")
    jump_button.pack(side=tk.RIGHT)
    jump_button.pack_forget()

    text_frame = tk.Frame(frame, bg=dash.log_bg_color)
    text_frame.pack(fill=tk.BOTH, expand=True)

    scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    log_text_widget = tk.Text(
        text_frame,
        wrap=tk.NONE,
        bg=dash.log_bg_color,
        fg=dash.log_fg_color,
        font=dash.theme.fonts["mono"],
        state=tk.DISABLED,
        relief="solid",
        borderwidth=1,
        highlightthickness=0,
        insertbackground=dash.log_fg_color,
    )
    log_text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_text_widget.configure(yscrollcommand=scrollbar.set)
    scrollbar.configure(command=log_text_widget.yview)

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<ButtonRelease-1>", "<Shift-MouseWheel>"):
        log_text_widget.bind(sequence, dash._update_log_autoscroll_state, add="+")

    dash.scan_output_dialog = dialog
    dash.scan_output_title_var = title_var
    dash.scan_output_header_label = header_label
    dash.log_text_widget = log_text_widget
    dash.log_jump_button = jump_button
    dash.copy_log_button = copy_button
    dash.clear_log_button = None

    dash._configure_log_tags()
    dash._render_log_placeholder()
    dash.theme.apply_theme_to_application(dialog)


def show_scan_output_dialog(dash, *, protocol: str, country: Optional[str]) -> None:
    ensure_scan_output_dialog(dash)
    if not _dialog_exists(dash):
        return

    target = str(country or "").strip() or "Global"
    protocol_label = str(protocol or "").strip().upper() or "SCAN"
    title = f"Live Scan Output - {protocol_label} ({target})"
    if getattr(dash, "scan_output_title_var", None):
        dash.scan_output_title_var.set(title)

    try:
        dash.scan_output_dialog.deiconify()
        dash.scan_output_dialog.lift()
        dash.scan_output_dialog.focus_force()
    except tk.TclError:
        return


def hide_scan_output_dialog(dash) -> None:
    if not _dialog_exists(dash):
        return
    try:
        dash.scan_output_dialog.withdraw()
    except tk.TclError:
        return


def reopen_scan_output_dialog(dash) -> None:
    if not _dialog_exists(dash):
        return
    try:
        dash.scan_output_dialog.deiconify()
        dash.scan_output_dialog.lift()
        dash.scan_output_dialog.focus_force()
    except tk.TclError:
        return


def destroy_scan_output_dialog(dash) -> None:
    if _dialog_exists(dash):
        try:
            dash.scan_output_dialog.destroy()
        except tk.TclError:
            pass
    dash.scan_output_dialog = None
    dash.scan_output_title_var = None
    dash.scan_output_header_label = None
    dash.log_text_widget = None
    dash.log_jump_button = None
    dash.copy_log_button = None
    dash.clear_log_button = None

