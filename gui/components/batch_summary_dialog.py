"""
Shared batch-operation summary dialog used by dashboard and server list flows.
"""

from __future__ import annotations

import csv
import tkinter as tk
from tkinter import filedialog, ttk
from gui.utils import safe_messagebox as messagebox
from typing import Any, Dict, List, Optional


def show_batch_summary_dialog(
    *,
    parent: tk.Widget,
    theme,
    job_type: str,
    results: List[Dict[str, Any]],
    title_suffix: str = "Batch Summary",
    geometry: str = "700x400",
    show_export: bool = True,
    show_protocol: bool = False,
    show_stats: bool = False,
    wait: bool = False,
    modal: bool = False,
) -> tk.Toplevel:
    """Create and display a batch-operation summary dialog."""
    dialog = tk.Toplevel(parent)
    title = f"{(job_type or 'batch').title()} {title_suffix}"
    dialog.title(title)
    dialog.geometry(geometry)
    dialog.transient(parent)
    if modal:
        dialog.grab_set()

    if theme:
        theme.apply_to_widget(dialog, "main_window")

    columns, headings, widths = _resolve_summary_columns(show_protocol=show_protocol)
    tree_frame = tk.Frame(dialog)
    if theme:
        theme.apply_to_widget(tree_frame, "main_window")
    tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    scrollbar_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
    tree = ttk.Treeview(
        tree_frame,
        columns=columns,
        show="headings",
        height=15,
        yscrollcommand=scrollbar_y.set,
    )
    scrollbar_y.config(command=tree.yview)
    for col in columns:
        tree.heading(col, text=headings[col])
        tree.column(col, width=widths[col], anchor="w")

    success_count = 0
    failed_count = 0
    for entry in results:
        status = str(entry.get("status", "unknown") or "unknown")
        if status.lower() == "success":
            success_count += 1
        elif status.lower() in {"failed", "error"}:
            failed_count += 1
        tree.insert(
            "",
            "end",
            values=_build_summary_row(entry, job_type=job_type, status=status, show_protocol=show_protocol),
        )

    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)

    if show_stats:
        stats_label = tk.Label(
            dialog,
            text=f"Total: {len(results)} | Success: {success_count} | Failed: {failed_count}",
            font=("TkDefaultFont", 10),
        )
        if theme:
            theme.apply_to_widget(stats_label, "label")
        stats_label.pack(pady=(0, 6))

    button_frame = tk.Frame(dialog)
    if theme:
        theme.apply_to_widget(button_frame, "main_window")
    button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

    if show_export:
        save_button = tk.Button(
            button_frame,
            text="Save CSV",
            command=lambda: _export_batch_summary(results, job_type, dialog, show_protocol=show_protocol),
        )
        if theme:
            theme.apply_to_widget(save_button, "button_secondary")
        save_button.pack(side=tk.RIGHT, padx=(0, 5))

    close_button = tk.Button(button_frame, text="Close", command=dialog.destroy)
    if theme:
        theme.apply_to_widget(close_button, "button_secondary")
    close_button.pack(side=tk.RIGHT)

    if theme:
        theme.apply_theme_to_application(dialog)

    if wait:
        parent.wait_window(dialog)

    return dialog


def _resolve_summary_columns(*, show_protocol: bool) -> tuple[tuple[str, ...], Dict[str, str], Dict[str, int]]:
    """Resolve treeview columns, headings, and widths for summary dialog."""
    if show_protocol:
        columns = ("ip", "protocol", "action", "status", "notes")
        headings = {
            "ip": "IP Address",
            "protocol": "Protocol",
            "action": "Action",
            "status": "Result",
            "notes": "Notes",
        }
        widths = {
            "ip": 130,
            "protocol": 90,
            "action": 90,
            "status": 90,
            "notes": 340,
        }
        return columns, headings, widths

    columns = ("ip", "action", "status", "notes")
    headings = {
        "ip": "IP Address",
        "action": "Action",
        "status": "Result",
        "notes": "Notes",
    }
    widths = {
        "ip": 130,
        "action": 130,
        "status": 130,
        "notes": 360,
    }
    return columns, headings, widths


def _build_summary_row(
    entry: Dict[str, Any],
    *,
    job_type: str,
    status: str,
    show_protocol: bool,
) -> tuple[Any, ...]:
    """Build treeview row values for batch summary."""
    base = (
        entry.get("ip_address", "-"),
        str(entry.get("action", job_type or "batch")).title(),
        status.title(),
        entry.get("notes", ""),
    )
    if not show_protocol:
        return base
    return (
        entry.get("ip_address", "-"),
        entry.get("protocol", ""),
        str(entry.get("action", job_type or "batch")).title(),
        status.title(),
        entry.get("notes", ""),
    )


def _export_batch_summary(
    results: List[Dict[str, Any]],
    job_type: str,
    parent: tk.Toplevel,
    *,
    show_protocol: bool = False,
) -> None:
    """Persist batch summary rows to CSV."""
    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Save Batch Summary",
        defaultextension=".csv",
        filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
    )
    if not path:
        return

    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        if show_protocol:
            writer.writerow(["ip_address", "protocol", "action", "status", "notes"])
        else:
            writer.writerow(["ip_address", "action", "status", "notes"])
        for entry in results:
            if show_protocol:
                writer.writerow(
                    [
                        entry.get("ip_address", ""),
                        entry.get("protocol", ""),
                        entry.get("action", job_type),
                        entry.get("status", ""),
                        entry.get("notes", ""),
                    ]
                )
            else:
                writer.writerow(
                    [
                        entry.get("ip_address", ""),
                        entry.get("action", job_type),
                        entry.get("status", ""),
                        entry.get("notes", ""),
                    ]
                )

    messagebox.showinfo("Summary Saved", f"Saved batch summary to {path}", parent=parent)
