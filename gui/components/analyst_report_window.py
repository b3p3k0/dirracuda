"""Coverage-first, lazy desktop browser for completed Analyst reports."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

from gui.utils import safe_messagebox
from gui.utils.keybindings import bind_close_shortcuts, bind_tree_enter_shortcut
from gui.utils.style import get_theme


_PAGE_ROWS = 100


class AnalystReportWindow:
    """Modeless browser that never reads an entire report into memory."""

    def __init__(self, parent: tk.Widget, *, db_path: Path | None = None) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self.window: tk.Toplevel | None = None
        self._runs: list[tuple[str, str, str]] = []
        self._handle = None
        self._inventory_cursor = -1
        self._detector_cursor = 0
        self._model_cursor = 0
        self._busy = False
        self._inventory_busy = False
        self._findings_busy = False
        self._view_generation = 0
        self._select_all_model = False
        self._selected_model_ids: set[int] = set()
        self._build()
        self._load_runs()

    def _build(self) -> None:
        window = tk.Toplevel(self.parent)
        self.window = window
        window.title("Analyst Reports")
        window.geometry("1040x660")
        window.minsize(820, 520)
        window.transient(self.parent)
        window.protocol("WM_DELETE_WINDOW", self.destroy)
        self.theme.apply_to_widget(window, "main_window")

        outer = tk.Frame(window)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        top = tk.Frame(outer)
        self.theme.apply_to_widget(top, "main_window")
        top.pack(fill=tk.X)
        label = tk.Label(top, text="Completed report:")
        self.theme.apply_to_widget(label, "label")
        label.pack(side=tk.LEFT)
        self._run_var = tk.StringVar(value="Loading…")
        self._run_box = ttk.Combobox(
            top, textvariable=self._run_var, state="readonly", width=58,
        )
        self._run_box.pack(side=tk.LEFT, padx=(8, 8), fill=tk.X, expand=True)
        self._run_box.bind("<<ComboboxSelected>>", self._on_run_selected, add="+")
        refresh = tk.Button(top, text="Refresh", command=self._load_runs)
        self.theme.apply_to_widget(refresh, "button_secondary")
        refresh.pack(side=tk.LEFT, padx=(0, 8))
        self._html_btn = tk.Button(
            top, text="Open verified HTML", state="disabled",
            command=self._open_html,
        )
        self.theme.apply_to_widget(self._html_btn, "button_secondary")
        self._html_btn.pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="")
        status = tk.Label(
            outer, textvariable=self._status_var, anchor="w", justify="left",
        )
        self.theme.apply_to_widget(status, "label")
        status.pack(fill=tk.X, pady=(8, 4))

        coverage = tk.Frame(outer)
        self.theme.apply_to_widget(coverage, "card")
        coverage.pack(fill=tk.X, pady=(0, 8))
        heading = tk.Label(coverage, text="Coverage", anchor="w")
        self.theme.apply_to_widget(heading, "label")
        heading.pack(anchor="w", padx=10, pady=(8, 2))
        self._coverage_var = tk.StringVar(value="Select a completed report.")
        coverage_text = tk.Label(
            coverage, textvariable=self._coverage_var, anchor="w", justify="left",
            wraplength=980,
        )
        self.theme.apply_to_widget(coverage_text, "label")
        coverage_text.pack(fill=tk.X, padx=10, pady=(0, 8))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)
        findings_frame = tk.Frame(notebook)
        inventory_frame = tk.Frame(notebook)
        self.theme.apply_to_widget(findings_frame, "main_window")
        self.theme.apply_to_widget(inventory_frame, "main_window")
        notebook.add(findings_frame, text="Findings")
        notebook.add(inventory_frame, text="Inventory")

        self._findings = ttk.Treeview(
            findings_frame,
            columns=("export", "kind", "file", "type", "evidence", "state"),
            show="headings",
        )
        for key, text, width in (
            ("export", "Export", 60),
            ("kind", "Evidence", 100),
            ("file", "File", 260),
            ("type", "Category / detector", 150),
            ("evidence", "Grounded value / quote", 360),
            ("state", "Review", 110),
        ):
            self._findings.heading(key, text=text)
            self._findings.column(key, width=width, anchor="w")
        self._findings.pack(fill=tk.BOTH, expand=True)
        self._findings.bind("<Double-1>", self._toggle_export, add="+")
        finding_buttons = tk.Frame(findings_frame)
        self.theme.apply_to_widget(finding_buttons, "main_window")
        finding_buttons.pack(fill=tk.X, pady=(5, 0))
        self._finding_more = tk.Button(
            finding_buttons, text="Load more", state="disabled",
            command=self._load_more_findings,
        )
        self.theme.apply_to_widget(self._finding_more, "button_secondary")
        self._finding_more.pack(side=tk.RIGHT)
        for text, command in (
            ("Select all", self._select_all),
            ("Select none", self._select_none),
            ("Accept", lambda: self._review_selected("accepted")),
            ("Reject", lambda: self._review_selected("rejected")),
            ("Export…", self._export_selected),
        ):
            button = tk.Button(finding_buttons, text=text, command=command)
            self.theme.apply_to_widget(button, "button_secondary")
            button.pack(side=tk.LEFT, padx=(0, 6))

        self._inventory = ttk.Treeview(
            inventory_frame,
            columns=("ordinal", "file", "format", "terminal", "coverage"),
            show="headings",
        )
        for key, text, width in (
            ("ordinal", "#", 55),
            ("file", "File", 330),
            ("format", "Format", 95),
            ("terminal", "Outcome", 210),
            ("coverage", "Hits / chunks / findings", 210),
        ):
            self._inventory.heading(key, text=text)
            self._inventory.column(key, width=width, anchor="w")
        self._inventory.pack(fill=tk.BOTH, expand=True)
        inventory_buttons = tk.Frame(inventory_frame)
        self.theme.apply_to_widget(inventory_buttons, "main_window")
        inventory_buttons.pack(fill=tk.X, pady=(5, 0))
        self._inventory_more = tk.Button(
            inventory_buttons, text="Load more", state="disabled",
            command=self._load_more_inventory,
        )
        self.theme.apply_to_widget(self._inventory_more, "button_secondary")
        self._inventory_more.pack(side=tk.RIGHT)

        bind_close_shortcuts(window, self.destroy)
        bind_tree_enter_shortcut(self._findings, lambda _event=None: None)
        window.lift()
        window.focus_force()

    def destroy(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None

    def _schedule(self, callback) -> None:
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.after(0, callback)
        except Exception:
            pass

    def _load_runs(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._status_var.set("Loading completed reports…")

        def work() -> None:
            try:
                from experimental.analyst.report_browser import list_completed_reports

                result = list_completed_reports(path=self.db_path)
            except Exception:
                self._schedule(lambda: self._finish_runs(None))
                return
            self._schedule(lambda: self._finish_runs(result))

        threading.Thread(target=work, daemon=True).start()

    def _finish_runs(self, rows) -> None:
        self._busy = False
        if rows is None:
            self._status_var.set("Completed reports are unavailable.")
            return
        self._runs = list(rows)
        labels = [f"{label} · {finished} · {run_id[:12]}" for run_id, label, finished in rows]
        self._run_box.configure(values=labels)
        if not labels:
            self._run_var.set("No completed reports")
            self._status_var.set("No completed Analyst report is available yet.")
            return
        self._run_box.current(0)
        self._open_selected(0)

    def _on_run_selected(self, _event=None) -> None:
        index = self._run_box.current()
        if index >= 0:
            self._open_selected(index)

    def _open_selected(self, index: int) -> None:
        if self._busy or not 0 <= index < len(self._runs):
            return
        run_id = self._runs[index][0]
        self._view_generation += 1
        generation = self._view_generation
        self._inventory_busy = False
        self._findings_busy = False
        self._busy = True
        self._status_var.set("Verifying report artifacts…")

        def work() -> None:
            try:
                from experimental.analyst.report_browser import open_completed_report

                handle = open_completed_report(run_id, path=self.db_path)
            except Exception:
                self._schedule(lambda: self._finish_open(None, generation))
                return
            self._schedule(lambda: self._finish_open(handle, generation))

        threading.Thread(target=work, daemon=True).start()

    def _finish_open(self, handle, generation: int) -> None:
        if generation != self._view_generation:
            return
        self._busy = False
        if handle is None:
            self._handle = None
            self._html_btn.configure(state="disabled")
            self._status_var.set("Report verification failed closed.")
            return
        self._handle = handle
        self._inventory_cursor = -1
        self._detector_cursor = 0
        self._model_cursor = 0
        self._select_all_model = False
        self._selected_model_ids.clear()
        self._inventory.delete(*self._inventory.get_children(""))
        self._findings.delete(*self._findings.get_children(""))
        self._html_btn.configure(state="normal")
        self._status_var.set(
            "Verified manifest · model suggestions remain unreviewed until adjudicated."
        )
        self._coverage_var.set(
            f"Discovered {handle.discovered_files} · Excluded {handle.excluded_paths} · "
            f"Detector-scanned {handle.detector_scanned_files} · Selected "
            f"{handle.selected_files} · Model-reviewed {handle.model_reviewed_files} · "
            f"Deterministic hits {handle.detector_hits} · Suggested model findings "
            f"{handle.model_findings}"
        )
        self._load_more_inventory()
        self._load_more_findings()

    def _load_more_inventory(self) -> None:
        handle = self._handle
        if handle is None or self._inventory_busy:
            return
        self._inventory_busy = True
        cursor = self._inventory_cursor
        generation = self._view_generation

        def work() -> None:
            try:
                from experimental.analyst.report_browser import load_completed_inventory_page

                rows = load_completed_inventory_page(
                    handle, after_ordinal=cursor, limit=_PAGE_ROWS, path=self.db_path,
                )
            except Exception:
                self._schedule(lambda: self._finish_inventory(None, generation))
                return
            self._schedule(lambda: self._finish_inventory(rows, generation))

        threading.Thread(target=work, daemon=True).start()

    def _finish_inventory(self, rows, generation: int) -> None:
        if generation != self._view_generation:
            return
        self._inventory_busy = False
        if rows is None:
            self._inventory_more.configure(state="disabled")
            self._status_var.set("Inventory page failed closed.")
            return
        for row in rows:
            self._inventory.insert("", "end", values=(
                row.ordinal,
                row.relative_path,
                row.format_name or "unidentified",
                row.terminal.value,
                f"{row.detector_hit_count} / {row.chunk_count} / "
                f"{row.retained_model_finding_count}",
            ))
            self._inventory_cursor = row.ordinal
        self._inventory_more.configure(
            state="normal" if len(rows) == _PAGE_ROWS else "disabled",
        )

    def _load_more_findings(self) -> None:
        handle = self._handle
        if handle is None or self._findings_busy:
            return
        self._findings_busy = True
        detector_cursor = self._detector_cursor
        model_cursor = self._model_cursor
        generation = self._view_generation

        def work() -> None:
            try:
                from experimental.analyst.report_browser import (
                    load_completed_detector_page,
                    load_completed_model_page,
                )

                detector = load_completed_detector_page(
                    handle, after_id=detector_cursor, limit=_PAGE_ROWS, path=self.db_path,
                )
                model = load_completed_model_page(
                    handle, after_id=model_cursor, limit=_PAGE_ROWS, path=self.db_path,
                )
            except Exception:
                self._schedule(
                    lambda: self._finish_findings(None, None, generation)
                )
                return
            self._schedule(
                lambda: self._finish_findings(detector, model, generation)
            )

        threading.Thread(target=work, daemon=True).start()

    def _finish_findings(self, detector, model, generation: int) -> None:
        if generation != self._view_generation:
            return
        self._findings_busy = False
        if detector is None or model is None:
            self._finding_more.configure(state="disabled")
            self._status_var.set("Finding page failed closed.")
            return
        for cursor, row in detector:
            self._findings.insert("", "end", iid=f"detector:{cursor}", values=(
                "—", "deterministic", row.relative_path, row.detector_kind,
                row.detector_value, "verified",
            ))
            self._detector_cursor = cursor
        for cursor, row in model:
            self._findings.insert("", "end", iid=f"model:{cursor}", values=(
                "[x]" if self._is_selected(cursor) else "[ ]",
                "model", row.relative_path, row.category, row.quote,
                row.review_state,
            ))
            self._model_cursor = cursor
        self._finding_more.configure(
            state=(
                "normal"
                if len(detector) == _PAGE_ROWS or len(model) == _PAGE_ROWS
                else "disabled"
            ),
        )

    def _is_selected(self, finding_id: int) -> bool:
        return (
            finding_id not in self._selected_model_ids
            if self._select_all_model else finding_id in self._selected_model_ids
        )

    def _toggle_export(self, event=None) -> None:
        row_id = self._findings.identify_row(event.y) if event is not None else ""
        if not row_id.startswith("model:"):
            return
        finding_id = int(row_id.split(":", 1)[1])
        if self._select_all_model:
            if finding_id in self._selected_model_ids:
                self._selected_model_ids.remove(finding_id)
            else:
                self._selected_model_ids.add(finding_id)
        elif finding_id in self._selected_model_ids:
            self._selected_model_ids.remove(finding_id)
        else:
            self._selected_model_ids.add(finding_id)
        self._refresh_loaded_selection()

    def _select_all(self) -> None:
        self._select_all_model = True
        self._selected_model_ids.clear()
        self._refresh_loaded_selection()

    def _select_none(self) -> None:
        self._select_all_model = False
        self._selected_model_ids.clear()
        self._refresh_loaded_selection()

    def _refresh_loaded_selection(self) -> None:
        for row_id in self._findings.get_children(""):
            if not row_id.startswith("model:"):
                continue
            finding_id = int(row_id.split(":", 1)[1])
            values = list(self._findings.item(row_id, "values"))
            values[0] = "[x]" if self._is_selected(finding_id) else "[ ]"
            self._findings.item(row_id, values=values)

    def _review_selected(self, decision: str) -> None:
        selected = self._findings.selection()
        handle = self._handle
        if handle is None or len(selected) != 1 or not selected[0].startswith("model:"):
            safe_messagebox.showinfo(
                "Analyst Reports", "Select one model suggestion to review.",
                parent=self.window,
            )
            return
        row_id = selected[0]
        finding_id = int(row_id.split(":", 1)[1])

        def work() -> None:
            try:
                from experimental.analyst.report_browser import (
                    ReviewDecision,
                    review_model_finding,
                )

                review_model_finding(
                    handle, finding_id, ReviewDecision(decision), path=self.db_path,
                )
            except Exception:
                self._schedule(lambda: self._finish_review(None, None))
                return
            self._schedule(lambda: self._finish_review(row_id, decision))

        threading.Thread(target=work, daemon=True).start()

    def _finish_review(self, row_id, decision) -> None:
        if row_id is None:
            safe_messagebox.showerror(
                "Analyst Reports", "The review decision was not saved.",
                parent=self.window,
            )
            return
        if self._findings.exists(row_id):
            values = list(self._findings.item(row_id, "values"))
            values[5] = decision
            self._findings.item(row_id, values=values)

    def _export_selected(self) -> None:
        handle = self._handle
        if handle is None:
            return
        if not self._select_all_model and not self._selected_model_ids:
            safe_messagebox.showinfo(
                "Analyst Reports", "Select at least one model suggestion to export.",
                parent=self.window,
            )
            return
        selected = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export selected Analyst findings",
            defaultextension=".jsonl",
            filetypes=(("JSON Lines", "*.jsonl"), ("CSV", "*.csv")),
        )
        if not selected:
            return
        destination = Path(selected)
        output_name = "csv" if destination.suffix.casefold() == ".csv" else "jsonl"
        all_findings = self._select_all_model
        ids = tuple(sorted(self._selected_model_ids))

        def work() -> None:
            try:
                from experimental.analyst.report_browser import (
                    ExportFormat,
                    FindingExportSelection,
                    export_model_findings,
                )

                count = export_model_findings(
                    handle,
                    FindingExportSelection(all_findings, ids),
                    destination,
                    ExportFormat(output_name),
                    path=self.db_path,
                )
            except Exception:
                self._schedule(lambda: self._finish_export(None))
                return
            self._schedule(lambda: self._finish_export(count))

        threading.Thread(target=work, daemon=True).start()

    def _finish_export(self, count) -> None:
        if count is None:
            safe_messagebox.showerror(
                "Analyst Reports", "The selected findings were not exported.",
                parent=self.window,
            )
            return
        safe_messagebox.showinfo(
            "Analyst Reports", f"Exported {count} selected model finding(s).",
            parent=self.window,
        )

    def _open_html(self) -> None:
        handle = self._handle
        if handle is None or self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                from experimental.analyst.service import completed_report_html

                report = completed_report_html(handle.run_id, path=self.db_path)
            except Exception:
                self._schedule(lambda: self._finish_html(None))
                return
            self._schedule(lambda: self._finish_html(report))

        threading.Thread(target=work, daemon=True).start()

    def _finish_html(self, report) -> None:
        self._busy = False
        if report is None:
            safe_messagebox.showerror(
                "Analyst Reports", "The report failed verification and was not opened.",
                parent=self.window,
            )
            return
        webbrowser.open(Path(report).as_uri())


def show_analyst_report_window(
    parent: tk.Widget, *, db_path: Path | None = None,
) -> AnalystReportWindow:
    """Build and return one modeless verified report browser."""
    return AnalystReportWindow(parent, db_path=db_path)


__all__ = ["AnalystReportWindow", "show_analyst_report_window"]
