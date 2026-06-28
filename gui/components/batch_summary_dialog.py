"""
Shared batch-operation summary dialog used by dashboard and server list flows.
"""

from __future__ import annotations

import csv
import tkinter as tk
from tkinter import filedialog, ttk
from gui.utils import safe_messagebox as messagebox
from typing import Any, Dict, List, Optional
from gui.utils.keybindings import add_shortcut_hint, bind_close_shortcuts, bind_save_shortcuts, bind_submit_shortcuts
from gui.utils.sherlock_risk_display import resolve_sherlock_risk, sherlock_row_tag


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
    show_risk: bool = False,
    sherlock_settings: Any = None,
) -> tk.Toplevel:
    """Create and display a batch-operation summary dialog.

    When ``show_risk`` is True an alert-only Sherlock Risk column is added before
    Notes and finding rows are tinted via ``sherlock_settings.tint_for`` (C12).
    With ``show_risk`` False the dialog renders exactly as before.
    """
    dialog = tk.Toplevel(parent)
    title = f"{(job_type or 'batch').title()} {title_suffix}"
    dialog.title(title)
    dialog.geometry(geometry)
    dialog.transient(parent)
    if modal:
        dialog.grab_set()

    if theme:
        theme.apply_to_widget(dialog, "main_window")

    columns, headings, widths = _resolve_summary_columns(show_protocol=show_protocol, show_risk=show_risk)
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
    configured_risk_tags: set = set()
    for entry in results:
        status = str(entry.get("status", "unknown") or "unknown")
        if status.lower() == "success":
            success_count += 1
        elif status.lower() in {"failed", "error"}:
            failed_count += 1

        # Alert-only Risk tint: only fresh, non-zero findings carry a tag (and
        # only when settings are available); blank rows stay untinted.
        row_tags: tuple = ()
        if show_risk and sherlock_settings is not None:
            resolved = resolve_sherlock_risk(entry.get("sherlock_risk"))
            if resolved is not None:
                severity, _count, color_tag = resolved
                tag_name = sherlock_row_tag(severity, color_tag)
                if tag_name not in configured_risk_tags:
                    tree.tag_configure(
                        tag_name,
                        background=sherlock_settings.tint_for(severity, color_tag),
                    )
                    configured_risk_tags.add(tag_name)
                row_tags = (tag_name,)

        tree.insert(
            "",
            "end",
            values=_build_summary_row(
                entry, job_type=job_type, status=status, show_protocol=show_protocol, show_risk=show_risk
            ),
            tags=row_tags,
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
            command=lambda: _export_batch_summary(results, job_type, dialog, show_protocol=show_protocol, show_risk=show_risk),
        )
        if theme:
            theme.apply_to_widget(save_button, "button_secondary")
        save_button.pack(side=tk.RIGHT, padx=(0, 5))

    close_button = tk.Button(button_frame, text="Close", command=dialog.destroy)
    if theme:
        theme.apply_to_widget(close_button, "button_secondary")
    close_button.pack(side=tk.RIGHT)

    add_shortcut_hint(
        button_frame,
        theme,
        "Enter close  •  Esc/Ctrl+W/Cmd+W close  •  Ctrl/Cmd+S save CSV",
    )

    if theme:
        theme.apply_theme_to_application(dialog)

    bind_submit_shortcuts(dialog, dialog.destroy, allow_text_submit_with_enter=True)
    bind_close_shortcuts(dialog, dialog.destroy)
    if show_export:
        bind_save_shortcuts(
            dialog,
            lambda: _export_batch_summary(results, job_type, dialog, show_protocol=show_protocol, show_risk=show_risk),
        )

    if wait:
        parent.wait_window(dialog)

    return dialog


def _resolve_summary_columns(
    *, show_protocol: bool, show_risk: bool = False
) -> tuple[tuple[str, ...], Dict[str, str], Dict[str, int]]:
    """Resolve treeview columns, headings, and widths for summary dialog.

    When ``show_risk`` is True a "Risk" column is inserted before Notes (and
    Notes is trimmed to make room); otherwise the layout is unchanged.
    """
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
    else:
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

    if show_risk:
        idx = columns.index("notes")
        columns = columns[:idx] + ("risk",) + columns[idx:]
        headings = {**headings, "risk": "Risk"}
        widths = {**widths, "risk": 80, "notes": max(200, widths["notes"] - 80)}

    return columns, headings, widths


def _risk_cell_text(entry: Dict[str, Any]) -> str:
    """Alert-only Risk cell text (HIGH/MED/LOW n) for a row, else blank."""
    resolved = resolve_sherlock_risk(entry.get("sherlock_risk"))
    if resolved is None:
        return ""
    severity, count, _color_tag = resolved
    return severity.display_text(count)


def _build_summary_row(
    entry: Dict[str, Any],
    *,
    job_type: str,
    status: str,
    show_protocol: bool,
    show_risk: bool = False,
) -> tuple[Any, ...]:
    """Build treeview row values for batch summary (Risk before Notes when shown)."""
    values: List[Any] = [entry.get("ip_address", "-")]
    if show_protocol:
        values.append(entry.get("protocol", ""))
    values.append(str(entry.get("action", job_type or "batch")).title())
    values.append(status.title())
    if show_risk:
        values.append(_risk_cell_text(entry))
    values.append(entry.get("notes", ""))
    return tuple(values)


def _export_batch_summary(
    results: List[Dict[str, Any]],
    job_type: str,
    parent: tk.Toplevel,
    *,
    show_protocol: bool = False,
    show_risk: bool = False,
) -> None:
    """Persist batch summary rows to CSV (Risk column only when visible)."""
    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Save Batch Summary",
        defaultextension=".csv",
        filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
    )
    if not path:
        return

    header = ["ip_address"]
    if show_protocol:
        header.append("protocol")
    header += ["action", "status"]
    if show_risk:
        header.append("risk")
    header.append("notes")

    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for entry in results:
            row: List[Any] = [entry.get("ip_address", "")]
            if show_protocol:
                row.append(entry.get("protocol", ""))
            row += [entry.get("action", job_type), entry.get("status", "")]
            if show_risk:
                row.append(_risk_cell_text(entry))
            row.append(entry.get("notes", ""))
            writer.writerow(row)

    messagebox.showinfo("Summary Saved", f"Saved batch summary to {path}", parent=parent)
