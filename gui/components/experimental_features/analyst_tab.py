"""Accessories tab for launching and monitoring durable Analyst runs."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from gui.utils import safe_messagebox
from gui.utils.analyst_tasks import apply_analyst_task_hydration
from gui.utils.running_tasks import get_running_task_registry
from gui.utils.style import get_theme


class AnalystTab:
    """Low-input launcher; all durable and blocking work stays off the Tk thread."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self._busy = False
        self._summaries = []
        self._report_window = None
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build()
        self._refresh_runs()

    def _build(self) -> None:
        frame = self.frame
        description = tk.Label(
            frame,
            text=(
                "Analyze a directory with deterministic detectors and the fixed local "
                "model. Ollama version, tag, and digest are verified after launch; "
                "opening this tab makes no model-server request."
            ),
            justify="left",
            anchor="w",
            wraplength=590,
        )
        self._theme.apply_to_widget(description, "label")
        description.pack(fill=tk.X, padx=16, pady=(14, 10))

        form = tk.Frame(frame)
        self._theme.apply_to_widget(form, "main_window")
        form.pack(fill=tk.X, padx=16)
        form.columnconfigure(1, weight=1)

        self._source_var = tk.StringVar(value="")
        self._output_var = tk.StringVar(value="")
        self._label_var = tk.StringVar(value="")
        self._mode_var = tk.StringVar(value="fast")
        self._add_path_row(form, 0, "Source directory", self._source_var, self._browse_source)
        self._add_path_row(form, 1, "Output base", self._output_var, self._browse_output)

        label = tk.Label(form, text="Report label")
        self._theme.apply_to_widget(label, "label")
        label.grid(row=2, column=0, sticky="w", pady=3)
        entry = tk.Entry(form, textvariable=self._label_var)
        self._theme.apply_to_widget(entry, "entry")
        entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=3)

        mode_label = tk.Label(form, text="Depth")
        self._theme.apply_to_widget(mode_label, "label")
        mode_label.grid(row=3, column=0, sticky="w", pady=3)
        modes = tk.Frame(form)
        self._theme.apply_to_widget(modes, "main_window")
        modes.grid(row=3, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=3)
        for value, text in (
            ("fast", "Fast — model-review deterministic hits"),
            ("deep", "Deep — model-review every nonempty supported file"),
        ):
            button = tk.Radiobutton(
                modes, text=text, variable=self._mode_var, value=value,
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.pack(anchor="w")

        model = tk.Label(
            form,
            text="Model: qwen3.6:27b · fixed digest · local loopback · strict sandbox",
            anchor="w",
        )
        self._theme.apply_to_widget(model, "label")
        model.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 3))

        controls = tk.Frame(frame)
        self._theme.apply_to_widget(controls, "main_window")
        controls.pack(fill=tk.X, padx=16, pady=(9, 7))
        self._analyze_btn = tk.Button(
            controls, text="Analyze", command=self._start_analysis,
        )
        self._theme.apply_to_widget(self._analyze_btn, "button_primary")
        self._analyze_btn.pack(side=tk.LEFT, padx=(0, 7))
        refresh = tk.Button(controls, text="Refresh", command=self._refresh_runs)
        self._theme.apply_to_widget(refresh, "button_secondary")
        refresh.pack(side=tk.LEFT, padx=(0, 7))
        self._resume_btn = tk.Button(
            controls, text="Resume", state="disabled", command=self._resume_selected,
        )
        self._theme.apply_to_widget(self._resume_btn, "button_secondary")
        self._resume_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._cancel_btn = tk.Button(
            controls, text="Cancel", state="disabled", command=self._cancel_selected,
        )
        self._theme.apply_to_widget(self._cancel_btn, "button_danger")
        self._cancel_btn.pack(side=tk.LEFT, padx=(0, 7))
        reports = tk.Button(controls, text="Reports", command=self._open_reports)
        self._theme.apply_to_widget(reports, "button_secondary")
        reports.pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="Ready.")
        status = tk.Label(frame, textvariable=self._status_var, anchor="w")
        self._theme.apply_to_widget(status, "label")
        status.pack(fill=tk.X, padx=16, pady=(0, 5))

        self._runs = ttk.Treeview(
            frame,
            columns=("label", "mode", "state", "progress"),
            show="headings",
            height=6,
        )
        for key, text, width in (
            ("label", "Report", 190),
            ("mode", "Depth", 65),
            ("state", "State", 145),
            ("progress", "Coverage", 250),
        ):
            self._runs.heading(key, text=text)
            self._runs.column(key, width=width, anchor="w")
        self._runs.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        self._runs.bind("<<TreeviewSelect>>", self._on_selection, add="+")

    def _add_path_row(self, parent, row, text, variable, command) -> None:
        label = tk.Label(parent, text=text)
        self._theme.apply_to_widget(label, "label")
        label.grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(parent, textvariable=variable)
        self._theme.apply_to_widget(entry, "entry")
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 7), pady=3)
        button = tk.Button(parent, text="Browse…", command=command)
        self._theme.apply_to_widget(button, "button_secondary")
        button.grid(row=row, column=2, pady=3)

    def _browse_source(self) -> None:
        selected = filedialog.askdirectory(parent=self.frame.winfo_toplevel())
        if selected:
            self._source_var.set(selected)
            if not self._output_var.get().strip():
                self._output_var.set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.frame.winfo_toplevel())
        if selected:
            self._output_var.set(selected)

    def _schedule(self, callback) -> None:
        try:
            if self.frame.winfo_exists():
                self.frame.after(0, callback)
        except Exception:
            pass

    def _start_analysis(self) -> None:
        if self._busy:
            return
        try:
            from experimental.analyst.service import DirectoryRunRequest

            request = DirectoryRunRequest(
                Path(self._source_var.get()),
                Path(self._output_var.get()),
                self._label_var.get(),
                self._mode_var.get(),
            )
        except Exception:
            safe_messagebox.showerror(
                "Analyst", "Choose absolute source/output directories and enter a report label.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self._set_busy(True, "Inventorying and creating the durable run…")

        def work() -> None:
            try:
                from experimental.analyst.service import create_and_launch

                launch = create_and_launch(request)
            except Exception:
                self._schedule(lambda: self._finish_action(False, "Run creation or launch failed."))
                return
            self._schedule(
                lambda: self._finish_action(
                    True, f"Worker launched for run {launch.run_id[:12]}.",
                )
            )

        threading.Thread(target=work, daemon=True).start()

    def _refresh_runs(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "Loading durable Analyst runs…")

        def work() -> None:
            try:
                from experimental.analyst.service import list_run_summaries

                summaries = list_run_summaries()
            except Exception:
                self._schedule(lambda: self._finish_refresh(None))
                return
            self._schedule(lambda: self._finish_refresh(summaries))

        threading.Thread(target=work, daemon=True).start()

    def _finish_refresh(self, summaries) -> None:
        self._set_busy(False, "Ready." if summaries is not None else "No Analyst state yet.")
        if summaries is None:
            return
        self._summaries = list(summaries)
        self._runs.delete(*self._runs.get_children(""))
        for item in self._summaries:
            state = "paused_resource" if item.schedule_state == "paused_resource" else item.state.value
            self._runs.insert(
                "", "end", iid=item.run_id,
                values=(item.report_label, item.mode, state, item.progress),
            )
        self._hydrate_registry()
        self._on_selection()

    def _hydrate_registry(self) -> None:
        registry = self._context.get("running_tasks_registry")
        if registry is None:
            registry = get_running_task_registry()

        def reopen(_run_id: str):
            return self._reopen

        def cancel(run_id: str):
            return lambda: self._cancel_run_id(run_id)

        apply_analyst_task_hydration(
            registry, self._summaries, reopen=reopen, cancel=cancel,
        )

    def _reopen(self) -> None:
        try:
            top = self.frame.winfo_toplevel()
            top.deiconify()
            top.lift()
            top.focus_force()
        except Exception:
            pass

    def _selected_summary(self):
        selected = self._runs.selection()
        if not selected:
            return None
        return next((item for item in self._summaries if item.run_id == selected[0]), None)

    def _on_selection(self, _event=None) -> None:
        item = self._selected_summary()
        if item is None:
            self._resume_btn.configure(state="disabled")
            self._cancel_btn.configure(state="disabled")
            return
        from experimental.analyst.state import RunState

        resumable = item.state in {
            RunState.READY, RunState.INTERRUPTED, RunState.CANCELLED_PENDING_RESUME,
        }
        cancellable = (
            item.state in {RunState.RUNNING, RunState.CANCEL_REQUESTED}
            or (
                item.state is RunState.INTERRUPTED
                and item.schedule_state == "paused_resource"
            )
        )
        self._resume_btn.configure(state="normal" if resumable else "disabled")
        self._cancel_btn.configure(state="normal" if cancellable else "disabled")

    def _resume_selected(self) -> None:
        item = self._selected_summary()
        if item is not None:
            self._run_service_action(item.run_id, "resume")

    def _cancel_selected(self) -> None:
        item = self._selected_summary()
        if item is not None:
            self._cancel_run_id(item.run_id)

    def _cancel_run_id(self, run_id: str) -> None:
        self._run_service_action(run_id, "cancel")

    def _run_service_action(self, run_id: str, action: str) -> None:
        if self._busy:
            return
        self._set_busy(True, "Requesting " + action + "…")

        def work() -> None:
            try:
                if action == "resume":
                    from experimental.analyst.service import resume_run

                    resume_run(run_id)
                else:
                    from experimental.analyst.service import cancel_run

                    cancel_run(run_id)
            except Exception:
                self._schedule(lambda: self._finish_action(False, action.capitalize() + " failed."))
                return
            self._schedule(lambda: self._finish_action(True, action.capitalize() + " requested."))

        threading.Thread(target=work, daemon=True).start()

    def _finish_action(self, success: bool, message: str) -> None:
        self._set_busy(False, message)
        if not success:
            safe_messagebox.showerror(
                "Analyst", message, parent=self.frame.winfo_toplevel(),
            )
        self._refresh_runs()

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._status_var.set(status)
        self._analyze_btn.configure(state="disabled" if busy else "normal")

    def _open_reports(self) -> None:
        from gui.components.analyst_report_window import show_analyst_report_window

        existing = self._report_window
        if existing is not None and existing.window is not None:
            try:
                if existing.window.winfo_exists():
                    existing.window.lift()
                    existing.window.focus_force()
                    return
            except Exception:
                pass
        self._report_window = show_analyst_report_window(self.frame.winfo_toplevel())


def build_analyst_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Analyst Accessories tab frame."""
    return AnalystTab(parent, context).frame


__all__ = ["AnalystTab", "build_analyst_tab"]
