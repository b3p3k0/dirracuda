"""Read-first desktop view for completed Analyst reports."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from experimental.analyst.report_json import UNVERIFIED_NOTICE, dumps_report
from experimental.analyst.report_render import render, render_markdown
from experimental.analyst.service import AnalystServiceError, ServiceFailure
from experimental.analyst.store import AnalystStoreBusy
from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.keybindings import bind_close_shortcuts
from gui.utils.style import get_theme


_FACT_CATEGORIES = ("All", "PII", "Financial", "Contact", "Demographic")
_LEGACY_MESSAGE = "Legacy run - re-run to view a read."


class AnalystReportWindow:
    """Modeless, read-only browser for the sealed read-first report artifact."""

    def __init__(self, parent: tk.Widget, *, db_path: Path | None = None) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self.window: tk.Toplevel | None = None
        self._runs: list[tuple[str, str, str]] = []
        self._report: dict | None = None
        self._build()
        self._load_runs()

    def _build(self) -> None:
        window = tk.Toplevel(self.parent)
        self.window = window
        window.title("Analyst Reports")
        window.geometry("1040x720")
        window.minsize(820, 600)
        window.transient(self.parent)
        window.protocol("WM_DELETE_WINDOW", self.destroy)
        self.theme.apply_to_widget(window, "main_window")

        outer = tk.Frame(window)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        self._build_run_picker(outer)
        self._build_read_block(outer)
        self._build_facts_block(outer)

        bind_close_shortcuts(window, self.destroy)
        ensure_dialog_focus(window, self.parent)

    def _build_run_picker(self, parent: tk.Widget) -> None:
        row = tk.Frame(parent)
        self.theme.apply_to_widget(row, "main_window")
        row.pack(fill=tk.X, pady=(0, 8))

        label = tk.Label(row, text="Completed report:")
        self.theme.apply_to_widget(label, "label")
        label.pack(side=tk.LEFT)

        self._run_var = tk.StringVar(value="Loading…")
        self._run_box = ttk.Combobox(
            row,
            textvariable=self._run_var,
            state="readonly",
            width=58,
        )
        self._run_box.pack(side=tk.LEFT, padx=(8, 8), fill=tk.X, expand=True)
        self._run_box.bind("<<ComboboxSelected>>", self._on_run_selected, add="+")

        refresh = tk.Button(row, text="Refresh", command=self._load_runs)
        self.theme.apply_to_widget(refresh, "button_secondary")
        refresh.pack(side=tk.LEFT)

        self._retry_btn = tk.Button(row, text="Retry", command=self._load_runs)
        self.theme.apply_to_widget(self._retry_btn, "button_secondary")

        self._status_var = tk.StringVar(value="")
        status = tk.Label(parent, textvariable=self._status_var, anchor="w", justify="left")
        self.theme.apply_to_widget(status, "label")
        status.pack(fill=tk.X, pady=(0, 6))

        self._changed_var = tk.StringVar(value="")
        changed = tk.Label(
            parent,
            textvariable=self._changed_var,
            anchor="w",
            justify="left",
        )
        self.theme.apply_to_widget(changed, "label")
        changed.pack(fill=tk.X, pady=(0, 6))

    def _build_read_block(self, parent: tk.Widget) -> None:
        read_card = tk.Frame(parent)
        self.theme.apply_to_widget(read_card, "card")
        read_card.pack(fill=tk.X, pady=(0, 8))

        heading_row = tk.Frame(read_card)
        self.theme.apply_to_widget(heading_row, "card")
        heading_row.pack(fill=tk.X, padx=10, pady=(8, 4))

        heading = tk.Label(heading_row, text="WHAT THIS IS", anchor="w")
        self.theme.apply_to_widget(heading, "label")
        heading.pack(side=tk.LEFT)

        self._risk_var = tk.StringVar(value="Risk: —")
        risk = tk.Label(heading_row, textvariable=self._risk_var, anchor="e")
        self.theme.apply_to_widget(risk, "label")
        risk.pack(side=tk.RIGHT)

        self._host_summary_var = tk.StringVar(value="Select a completed report.")
        summary = tk.Label(
            read_card,
            textvariable=self._host_summary_var,
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self.theme.apply_to_widget(summary, "label")
        summary.pack(fill=tk.X, padx=10, pady=(0, 8))

        self._owner_var = tk.StringVar(value="Likely owner : —")
        owner = tk.Label(read_card, textvariable=self._owner_var, anchor="w")
        self.theme.apply_to_widget(owner, "label")
        owner.pack(fill=tk.X, padx=10)

        self._contacts_var = tk.StringVar(value="Contacts     : —")
        contacts = tk.Label(
            read_card,
            textvariable=self._contacts_var,
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self.theme.apply_to_widget(contacts, "label")
        contacts.pack(fill=tk.X, padx=10)

        self._counts_var = tk.StringVar(value="Files read   : —      Flagged files: —")
        counts = tk.Label(read_card, textvariable=self._counts_var, anchor="w")
        self.theme.apply_to_widget(counts, "label")
        counts.pack(fill=tk.X, padx=10, pady=(0, 8))

        exposures_heading = tk.Label(read_card, text="TOP EXPOSURES", anchor="w")
        self.theme.apply_to_widget(exposures_heading, "label")
        exposures_heading.pack(fill=tk.X, padx=10)

        self._exposures_var = tk.StringVar(value="(none)")
        exposures = tk.Label(
            read_card,
            textvariable=self._exposures_var,
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self.theme.apply_to_widget(exposures, "label")
        exposures.pack(fill=tk.X, padx=18, pady=(2, 8))

        self._notice_var = tk.StringVar(value=UNVERIFIED_NOTICE)
        notice = tk.Label(
            read_card,
            textvariable=self._notice_var,
            anchor="w",
            justify="left",
        )
        self.theme.apply_to_widget(notice, "label")
        notice.pack(fill=tk.X, padx=10, pady=(0, 8))

    def _build_facts_block(self, parent: tk.Widget) -> None:
        controls = tk.Frame(parent)
        self.theme.apply_to_widget(controls, "main_window")
        controls.pack(fill=tk.X, pady=(0, 5))

        heading = tk.Label(controls, text="FACTS")
        self.theme.apply_to_widget(heading, "label")
        heading.pack(side=tk.LEFT)

        filter_label = tk.Label(controls, text="Category:")
        self.theme.apply_to_widget(filter_label, "label")
        filter_label.pack(side=tk.LEFT, padx=(16, 5))

        self._category_var = tk.StringVar(value="All")
        self._category_box = ttk.Combobox(
            controls,
            textvariable=self._category_var,
            values=_FACT_CATEGORIES,
            state="readonly",
            width=14,
        )
        self._category_box.current(0)
        self._category_box.pack(side=tk.LEFT)
        self._category_box.bind("<<ComboboxSelected>>", self._apply_fact_filter, add="+")

        self._copy_btn = tk.Button(
            controls,
            text="Copy",
            state="disabled",
            command=self._copy_report,
        )
        self.theme.apply_to_widget(self._copy_btn, "button_secondary")
        self._copy_btn.pack(side=tk.RIGHT)

        self._export_btn = tk.Button(
            controls,
            text="Export…",
            state="disabled",
            command=self._export_report,
        )
        self.theme.apply_to_widget(self._export_btn, "button_secondary")
        self._export_btn.pack(side=tk.RIGHT, padx=(0, 6))

        tree_frame = tk.Frame(parent)
        self.theme.apply_to_widget(tree_frame, "main_window")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self._facts = ttk.Treeview(
            tree_frame,
            columns=("kind", "value", "file", "rank"),
            show="headings",
        )
        for key, text, width in (
            ("kind", "Kind", 145),
            ("value", "Value", 360),
            ("file", "File", 330),
            ("rank", "Rank", 80),
        ):
            self._facts.heading(key, text=text)
            self._facts.column(key, width=width, anchor="w")

        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._facts.yview)
        self._facts.configure(yscrollcommand=scroll.set)
        self._facts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def destroy(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None

    def _load_runs(self) -> None:
        self._hide_retry()
        self._status_var.set("Loading completed reports…")
        self._set_report_actions(False)
        try:
            from experimental.analyst.report_browser import list_completed_reports

            rows = list_completed_reports(path=self.db_path)
        except AnalystStoreBusy:
            message = "Reports are busy — the analysis is still writing. Click Retry."
            self._run_var.set(message)
            self._status_var.set(message)
            self._show_retry()
            return
        except Exception:
            self._runs = []
            self._run_box.configure(values=())
            self._run_var.set("Completed reports unavailable")
            self._status_var.set("Completed reports are unavailable.")
            return

        self._runs = list(rows)
        self._hide_retry()
        labels = [
            f"{label} · {finished} · {run_id[:12]}"
            for run_id, label, finished in self._runs
        ]
        self._run_box.configure(values=labels)
        if not labels:
            self._run_var.set("No completed reports")
            self._status_var.set("No completed Analyst report is available yet.")
            self._clear_report()
            return
        self._run_box.current(0)
        self._open_selected(0)

    def _on_run_selected(self, _event=None) -> None:
        index = self._run_box.current()
        if index >= 0:
            self._open_selected(index)

    def _open_selected(self, index: int) -> None:
        if not 0 <= index < len(self._runs):
            return
        run_id = self._runs[index][0]
        self._status_var.set("Opening report…")
        self._set_report_actions(False)
        try:
            from experimental.analyst.service import read_report_json

            report, changed = read_report_json(run_id, path=self.db_path)
        except AnalystServiceError as exc:
            if exc.code is ServiceFailure.BUSY:
                self._status_var.set(
                    "Report is busy — the analysis is still writing. Click Retry."
                )
                self._show_retry()
                return
            self._show_legacy_run()
            return
        except Exception:
            self._show_legacy_run()
            return
        self._show_report(report, changed=changed)

    def _show_report(self, report: dict, *, changed: bool) -> None:
        self._hide_retry()
        self._report = report
        run = report["run"]
        read = report["read"]

        if self.window is not None:
            self.window.title(f"Report - {run['report_label']}")
        self._status_var.set("")
        self._changed_var.set("changed since saved" if changed else "")
        self._risk_var.set(f"Risk: ● {read['risk_level']}")
        self._host_summary_var.set(str(read["host_summary"]))
        owner = read["likely_owner"]
        self._owner_var.set(
            f"Likely owner : {owner if owner is not None else '(not identified)'}"
        )
        contacts = read["contacts"]
        self._contacts_var.set(
            f"Contacts     : {', '.join(contacts) if contacts else '(none)'}"
        )
        self._counts_var.set(
            f"Files read   : {run['files_read']}      "
            f"Flagged files: {run['flagged_files']}"
        )
        exposures = [
            item for item in read["top_exposures"]
            if item["severity"] != "LOW"
        ]
        self._exposures_var.set(
            "\n".join(
                f"{item['rank']}. {item['severity']}  {item['text']}"
                for item in exposures
            )
            if exposures
            else "(none)"
        )
        self._notice_var.set(str(read["unverified_notice"]))
        self._category_var.set("All")
        self._apply_fact_filter()
        self._set_report_actions(True)

    def _clear_report(self) -> None:
        self._report = None
        self._changed_var.set("")
        self._risk_var.set("Risk: —")
        self._host_summary_var.set("Select a completed report.")
        self._owner_var.set("Likely owner : —")
        self._contacts_var.set("Contacts     : —")
        self._counts_var.set("Files read   : —      Flagged files: —")
        self._exposures_var.set("(none)")
        self._notice_var.set(UNVERIFIED_NOTICE)
        self._facts.delete(*self._facts.get_children(""))
        self._set_report_actions(False)

    def _show_legacy_run(self) -> None:
        self._clear_report()
        self._host_summary_var.set(_LEGACY_MESSAGE)
        self._status_var.set(_LEGACY_MESSAGE)

    def _show_retry(self) -> None:
        self._retry_btn.pack(side=tk.LEFT, padx=(6, 0))

    def _hide_retry(self) -> None:
        self._retry_btn.pack_forget()

    def _apply_fact_filter(self, _event=None) -> None:
        self._facts.delete(*self._facts.get_children(""))
        if self._report is None:
            return
        selected = self._category_var.get().casefold()
        for fact in self._report["facts"]:
            if selected != "all" and fact["category"].casefold() != selected:
                continue
            self._facts.insert(
                "",
                "end",
                values=(fact["kind"], fact["quote"], fact["file"], fact["rank"]),
            )

    def _set_report_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._export_btn.configure(state=state)
        self._copy_btn.configure(state=state)

    def _export_report(self) -> None:
        report = self._report
        if report is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export Analyst report",
            defaultextension=".md",
            filetypes=(
                ("Markdown", "*.md"),
                ("JSON", "*.json"),
                ("Plain text", "*.txt"),
                ("CSV facts", "*.csv"),
            ),
        )
        if not destination:
            return
        target = Path(destination)
        fmt = target.suffix.casefold().lstrip(".") or "md"
        try:
            content = dumps_report(report) if fmt == "json" else render(report, fmt)
            self._write_owner_only(target, content)
        except Exception:
            safe_messagebox.showerror(
                "Analyst Reports",
                "The report could not be exported.",
                parent=self.window,
            )
            return
        self._status_var.set(f"Exported report to {target}.")

    @staticmethod
    def _write_owner_only(target: Path, content: str) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as sink:
                descriptor = -1
                sink.write(content)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def clipboard_clear(self) -> None:
        if self.window is not None:
            self.window.clipboard_clear()

    def clipboard_append(self, text: str) -> None:
        if self.window is not None:
            self.window.clipboard_append(text)

    def _copy_report(self) -> None:
        report = self._report
        if report is None:
            return
        try:
            markdown = render_markdown(report)
            self.clipboard_clear()
            self.clipboard_append(markdown)
        except Exception:
            safe_messagebox.showerror(
                "Analyst Reports",
                "The report could not be copied.",
                parent=self.window,
            )
            return
        self._status_var.set("Copied report as Markdown.")


def show_analyst_report_window(
    parent: tk.Widget, *, db_path: Path | None = None,
) -> AnalystReportWindow:
    """Build and return one modeless read-first report browser."""
    return AnalystReportWindow(parent, db_path=db_path)


__all__ = ["AnalystReportWindow", "show_analyst_report_window"]
