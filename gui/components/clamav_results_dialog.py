"""
ClamAV results dialog shown after bulk extract operations.

Public API:
  should_show_clamav_dialog(job_type, results, clamav_cfg) -> bool
  show_clamav_results_dialog(*, parent, theme, results, on_mute, wait, modal) -> Optional[tk.Toplevel]
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

from gui.utils import session_flags

_BOOL_TRUE = frozenset(("true", "yes", "1"))


def _coerce_bool_like(v: Any, default: bool = True) -> bool:
    """Coerce a config value to bool. Passes bools through; treats string 'false'/'0'/'no' as False."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in _BOOL_TRUE


def should_show_clamav_dialog(
    job_type: str,
    results: List[Dict[str, Any]],
    clamav_cfg: Dict[str, Any],
) -> bool:
    """Return True when the ClamAV results dialog should be shown.

    Conditions (all must hold):
    - job_type is "extract"
    - session mute is not active
    - clamav_cfg["show_results"] is truthy (default True when absent)
    - at least one result has result["clamav"]["enabled"] == True
    """
    if job_type != "extract":
        return False
    if session_flags.get_flag(session_flags.CLAMAV_MUTE_KEY):
        return False
    show_results_raw = clamav_cfg.get("show_results", True) if isinstance(clamav_cfg, dict) else True
    if not _coerce_bool_like(show_results_raw, default=True):
        return False
    return any(
        isinstance(r.get("clamav"), dict) and r["clamav"].get("enabled")
        for r in results
    )


def show_clamav_results_dialog(
    *,
    parent: tk.Widget,
    theme: Any,
    results: List[Dict[str, Any]],
    on_mute: Callable[[], None],
    wait: bool = False,
    modal: bool = False,
) -> Optional[tk.Toplevel]:
    """Build and display the ClamAV results dialog.

    Returns the Toplevel on success, None if rendering fails (fail-safe).
    """
    try:
        return _build_dialog(
            parent=parent,
            theme=theme,
            results=results,
            on_mute=on_mute,
            wait=wait,
            modal=modal,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_dialog(
    *,
    parent: tk.Widget,
    theme: Any,
    results: List[Dict[str, Any]],
    on_mute: Callable[[], None],
    wait: bool,
    modal: bool,
) -> tk.Toplevel:
    # Aggregate totals across all enabled hosts
    total_scanned = 0
    total_clean = 0
    total_infected = 0
    total_errors = 0
    infected_rows: List[tuple] = []
    error_rows: List[tuple] = []

    for r in results:
        av = r.get("clamav") or {}
        if not (isinstance(av, dict) and av.get("enabled")):
            continue
        total_scanned += int(av.get("files_scanned", 0))
        total_clean += int(av.get("clean", 0))
        total_infected += int(av.get("infected", 0))
        total_errors += int(av.get("errors", 0))
        ip = r.get("ip_address", "-")
        for item in av.get("infected_items", []):
            infected_rows.append((
                ip,
                item.get("path", "-"),
                item.get("signature") or "-",
                item.get("moved_to", "-"),
            ))
        for item in av.get("error_items", []):
            error_rows.append((ip, item.get("path", "-"), item.get("error", "-")))

    dialog = tk.Toplevel(parent)
    dialog.title("ClamAV Scan Results")
    dialog.geometry("760x440")
    dialog.transient(parent)
    if modal:
        dialog.grab_set()
    if theme:
        theme.apply_to_widget(dialog, "main_window")

    # Summary strip: larger, centered, and color-coded for fast triage.
    text_color = "#1f2937"
    clean_color = "#16a34a"
    infected_color = "#dc2626"
    other_color = "#d97706"
    if theme and hasattr(theme, "colors") and isinstance(theme.colors, dict):
        text_color = theme.colors.get("text", text_color)
        clean_color = theme.colors.get("success", clean_color)
        infected_color = theme.colors.get("error", infected_color)
        other_color = theme.colors.get("warning", other_color)

    summary_frame = tk.Frame(dialog)
    if theme:
        theme.apply_to_widget(summary_frame, "main_window")
    summary_frame.pack(fill=tk.X, padx=10, pady=(12, 6))

    summary_title = tk.Label(summary_frame, text="ClamAV Summary", font=("TkDefaultFont", 12, "bold"))
    if theme:
        theme.apply_to_widget(summary_title, "label")
    summary_title.configure(fg=text_color)
    summary_title.pack(anchor="center", pady=(0, 6))

    stats_row = tk.Frame(summary_frame)
    if theme:
        theme.apply_to_widget(stats_row, "main_window")
    stats_row.pack(anchor="center")

    def _add_stat(label: str, value: int, color: str) -> None:
        box = tk.Frame(stats_row, padx=12, pady=6)
        if theme:
            theme.apply_to_widget(box, "card")
        box.pack(side=tk.LEFT, padx=6)

        value_lbl = tk.Label(box, text=str(value), font=("TkDefaultFont", 17, "bold"))
        if theme:
            theme.apply_to_widget(value_lbl, "label")
        value_lbl.configure(fg=color)
        value_lbl.pack(anchor="center")

        label_lbl = tk.Label(box, text=label, font=("TkDefaultFont", 10, "bold"))
        if theme:
            theme.apply_to_widget(label_lbl, "label")
        label_lbl.configure(fg=text_color)
        label_lbl.pack(anchor="center")

    _add_stat("Scanned", total_scanned, text_color)
    _add_stat("Clean", total_clean, clean_color)
    _add_stat("Infected", total_infected, infected_color)
    _add_stat("Other", total_errors, other_color)

    # Notebook with infected / errors tabs
    nb = ttk.Notebook(dialog)
    nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

    # Infected tab
    inf_frame = tk.Frame(nb)
    if theme:
        theme.apply_to_widget(inf_frame, "main_window")
    nb.add(inf_frame, text=f"Infected ({len(infected_rows)})")

    inf_cols = ("ip", "path", "signature", "moved_to")
    inf_tree = ttk.Treeview(inf_frame, columns=inf_cols, show="headings", height=5)
    inf_tree.heading("ip", text="IP")
    inf_tree.heading("path", text="Path")
    inf_tree.heading("signature", text="Signature")
    inf_tree.heading("moved_to", text="Moved to")
    inf_tree.column("ip", width=120, anchor="w")
    inf_tree.column("path", width=200, anchor="w")
    inf_tree.column("signature", width=160, anchor="w")
    inf_tree.column("moved_to", width=220, anchor="w")
    for row in infected_rows:
        inf_tree.insert("", "end", values=row)
    inf_scroll = ttk.Scrollbar(inf_frame, orient="vertical", command=inf_tree.yview)
    inf_tree.configure(yscrollcommand=inf_scroll.set)
    inf_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    inf_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # Errors tab
    err_frame = tk.Frame(nb)
    if theme:
        theme.apply_to_widget(err_frame, "main_window")
    nb.add(err_frame, text=f"Errors ({len(error_rows)})")

    err_cols = ("ip", "path", "error")
    err_tree = ttk.Treeview(err_frame, columns=err_cols, show="headings", height=5)
    err_tree.heading("ip", text="IP")
    err_tree.heading("path", text="Path")
    err_tree.heading("error", text="Error")
    err_tree.column("ip", width=120, anchor="w")
    err_tree.column("path", width=220, anchor="w")
    err_tree.column("error", width=360, anchor="w")
    for row in error_rows:
        err_tree.insert("", "end", values=row)
    err_scroll = ttk.Scrollbar(err_frame, orient="vertical", command=err_tree.yview)
    err_tree.configure(yscrollcommand=err_scroll.set)
    err_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    err_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # Button row
    btn_frame = tk.Frame(dialog)
    if theme:
        theme.apply_to_widget(btn_frame, "main_window")
    btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

    def _on_mute():
        on_mute()
        dialog.destroy()

    mute_btn = tk.Button(btn_frame, text="Mute until restart", command=_on_mute)
    if theme:
        theme.apply_to_widget(mute_btn, "button_secondary")
    mute_btn.pack(side=tk.LEFT)

    close_btn = tk.Button(btn_frame, text="Close", command=dialog.destroy)
    if theme:
        theme.apply_to_widget(close_btn, "button_secondary")
    close_btn.pack(side=tk.RIGHT)

    if theme:
        theme.apply_theme_to_application(dialog)

    if wait:
        parent.wait_window(dialog)

    return dialog
