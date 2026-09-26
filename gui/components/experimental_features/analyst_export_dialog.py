"""Analyst batch report export (satellite of ``analyst_tab``).

A satellite so the tab does not carry a whole export dialog inline. One-way
import: ``analyst_tab`` imports this module, never the reverse. Messageboxes
route through ``_mb()`` so test monkeypatches on the ``analyst_tab`` namespace
still intercept, following the dispatch discipline in ``CLAUDE.md``.

Every function takes the tab as ``tab``, mirroring the dashboard satellites'
``dash`` convention.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

def _tab_module():
    from gui.components.experimental_features import analyst_tab

    return analyst_tab


def _mb():
    """Resolve safe_messagebox through the analyst_tab namespace at call time."""
    return _tab_module().safe_messagebox


def _focus(dialog, parent) -> None:
    """Resolve ensure_dialog_focus the same way, for the same reason.

    Tests patch it on the analyst_tab namespace; a module-level import here
    would hold a reference the patch never reaches.
    """
    _tab_module().ensure_dialog_focus(dialog, parent)


def open_export_dialog(tab) -> None:
    selected = tuple(tab._runs.selection())
    if not selected:
        return
    existing = tab._export_dialog
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

    parent = tab.frame.winfo_toplevel()
    dialog = tk.Toplevel(parent)
    dialog.title("Export reports")
    dialog.transient(parent)
    dialog.resizable(False, False)
    tab._theme.apply_to_widget(dialog, "main_window")
    tab._export_dialog = dialog

    outer = tk.Frame(dialog)
    tab._theme.apply_to_widget(outer, "main_window")
    outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
    outer.columnconfigure(1, weight=1)

    selected_label = tk.Label(
        outer, text=f"Selected: {len(selected)} reports", anchor="w",
    )
    tab._theme.apply_to_widget(selected_label, "label")
    selected_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

    tab._export_format_vars = {
        "md": tk.BooleanVar(value=True),
        "json": tk.BooleanVar(value=True),
        "txt": tk.BooleanVar(value=False),
        "csv": tk.BooleanVar(value=False),
    }
    tab._export_layout_var = tk.StringVar(value="per_report")
    tab._export_include_vars = {
        "read": tk.BooleanVar(value=True),
        "facts": tk.BooleanVar(value=True),
    }
    default_folder = tab._output_var.get().strip()
    if not default_folder or not Path(default_folder).expanduser().is_dir():
        default_folder = str(Path.home())
    tab._export_folder_var = tk.StringVar(value=default_folder)

    format_label = tk.Label(outer, text="Format")
    tab._theme.apply_to_widget(format_label, "label")
    format_label.grid(row=1, column=0, sticky="nw", pady=3)
    format_controls = tk.Frame(outer)
    tab._theme.apply_to_widget(format_controls, "main_window")
    format_controls.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0))
    for index, (value, text) in enumerate((
        ("md", "Markdown"), ("json", "JSON"),
        ("txt", "Plain text"), ("csv", "CSV (facts)"),
    )):
        button = tk.Checkbutton(
            format_controls, text=text, variable=tab._export_format_vars[value],
        )
        tab._theme.apply_to_widget(button, "checkbox")
        button.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 14))

    layout_label = tk.Label(outer, text="Layout")
    tab._theme.apply_to_widget(layout_label, "label")
    layout_label.grid(row=2, column=0, sticky="nw", pady=(10, 3))
    layout_controls = tk.Frame(outer)
    tab._theme.apply_to_widget(layout_controls, "main_window")
    layout_controls.grid(
        row=2, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 3),
    )
    for value, text in (
        ("per_report", "One file per report"),
        ("combined", "One combined file"),
    ):
        button = tk.Radiobutton(
            layout_controls, text=text, variable=tab._export_layout_var,
            value=value,
        )
        tab._theme.apply_to_widget(button, "checkbox")
        button.pack(anchor="w")

    include_label = tk.Label(outer, text="Include")
    tab._theme.apply_to_widget(include_label, "label")
    include_label.grid(row=3, column=0, sticky="w", pady=(10, 3))
    include_controls = tk.Frame(outer)
    tab._theme.apply_to_widget(include_controls, "main_window")
    include_controls.grid(
        row=3, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 3),
    )
    for value, text in (("read", "The read"), ("facts", "The facts")):
        button = tk.Checkbutton(
            include_controls, text=text, variable=tab._export_include_vars[value],
        )
        tab._theme.apply_to_widget(button, "checkbox")
        button.pack(side=tk.LEFT, padx=(0, 14))

    folder_label = tk.Label(outer, text="Folder")
    tab._theme.apply_to_widget(folder_label, "label")
    folder_label.grid(row=4, column=0, sticky="w", pady=(10, 3))
    folder_entry = tk.Entry(outer, textvariable=tab._export_folder_var, width=42)
    tab._theme.apply_to_widget(folder_entry, "entry")
    folder_entry.grid(row=4, column=1, sticky="ew", padx=(8, 7), pady=(10, 3))

    def browse() -> None:
        chosen = filedialog.askdirectory(parent=dialog)
        if chosen:
            tab._export_folder_var.set(chosen)

    browse_button = tk.Button(outer, text="Browse", command=browse)
    tab._theme.apply_to_widget(browse_button, "button_secondary")
    browse_button.grid(row=4, column=2, pady=(10, 3))

    actions = tk.Frame(outer)
    tab._theme.apply_to_widget(actions, "main_window")
    actions.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
    cancel = tk.Button(
        actions, text="Cancel", command=lambda: close_export_dialog(tab, dialog),
    )
    tab._theme.apply_to_widget(cancel, "button_secondary")
    cancel.pack(side=tk.LEFT, padx=(0, 7))
    export = tk.Button(
        actions,
        text="Export",
        command=lambda: start_export(
            tab, dialog, selected, cancel, export,
        ),
    )
    tab._theme.apply_to_widget(export, "button_primary")
    export.pack(side=tk.LEFT)
    dialog.protocol(
        "WM_DELETE_WINDOW", lambda: close_export_dialog(tab, dialog),
    )
    dialog.grab_set()
    _focus(dialog, parent)

def close_export_dialog(tab, dialog) -> None:
    if tab._export_dialog is dialog:
        tab._export_dialog = None
    try:
        dialog.grab_release()
        dialog.destroy()
    except tk.TclError:
        pass

def start_export(tab, dialog, run_ids, cancel_button, export_button) -> None:
    formats = frozenset(
        value for value, variable in tab._export_format_vars.items()
        if variable.get()
    )
    include = frozenset(
        value for value, variable in tab._export_include_vars.items()
        if variable.get()
    )
    layout = tab._export_layout_var.get()
    destination = Path(tab._export_folder_var.get()).expanduser()
    if not formats:
        _mb().showwarning(
            "Analyst export", "Select at least one format.", parent=dialog,
        )
        return
    if not include:
        _mb().showwarning(
            "Analyst export", "Include the read, the facts, or both.", parent=dialog,
        )
        return
    if not destination.is_dir():
        _mb().showwarning(
            "Analyst export", "Choose an existing export folder.", parent=dialog,
        )
        return

    labels = {
        item.run_id: item.report_label for item in tab._summaries
        if item.run_id in run_ids
    }
    cancel_button.configure(state="disabled")
    export_button.configure(state="disabled")

    def work() -> None:
        reports = []
        skipped = 0
        try:
            from experimental.analyst.report_export import export_reports
            from experimental.analyst.service import read_report_json

            for run_id in run_ids:
                try:
                    report, _changed = read_report_json(run_id)
                except Exception:
                    skipped += 1
                    continue
                label = labels.get(run_id, report["run"]["report_label"])
                reports.append((label, report))
            result = export_reports(
                reports,
                formats=formats,
                layout=layout,
                include=include,
                dest_dir=destination,
            )
        except Exception:
            tab._schedule(
                lambda: finish_export(tab, dialog, None, skipped, destination)
            )
            return
        tab._schedule(
            lambda: finish_export(
                tab, dialog, result, skipped, destination,
            )
        )

    threading.Thread(target=work, daemon=True).start()


def finish_export(tab, dialog, result, skipped: int, destination: Path) -> None:
    close_export_dialog(tab, dialog)
    parent = tab.frame.winfo_toplevel()
    if result is None:
        _mb().showerror(
            "Analyst export", "The selected reports could not be exported.",
            parent=parent,
        )
        return
    message = f"Exported {result.report_count} reports to {destination}."
    if skipped:
        message += f" Skipped {skipped} legacy runs."
    _mb().showinfo("Analyst export", message, parent=parent)
