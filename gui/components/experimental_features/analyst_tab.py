"""Accessories tab for launching and monitoring durable Analyst runs."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from gui.utils import safe_messagebox
from gui.utils.analyst_tasks import apply_analyst_task_hydration
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.running_tasks import get_running_task_registry
from gui.utils.style import get_theme
from shared.path_service import get_paths


_CREATE_FAILURE_MESSAGES = {
    "contract": "The source or output directory is not supported by Analyst.",
    "output_invalid": (
        "Output folder must be an existing local directory (not a symlink)."
    ),
    "output_unsafe_fs": (
        "Output folder can't keep reports private (e.g. a network/CIFS mount). "
        "Choose a local folder."
    ),
    "abandon": "Could not abandon the run.",
    "inventory": (
        "Source inventory failed. Check that the directory is readable and stable."
    ),
    "storage": "Analyst could not save the durable run. No worker was launched.",
    "launch": (
        "The run was saved, but its worker could not be launched. Refresh to resume it."
    ),
    "state": "Analyst durable state prevented this run from starting.",
}
_ACTIVE_REFRESH_MS = 2_000
_IDLE_REFRESH_MS = 60_000
_ACTIVE_RUN_STATES = frozenset({"running", "cancel_requested", "finalizing"})
_RESUMABLE_RUN_STATES = frozenset({
    "ready", "interrupted", "cancelled_pending_resume",
})


def _creation_failure_message(error: BaseException) -> str:
    code = getattr(getattr(error, "code", None), "value", None)
    if type(code) is str:
        return _CREATE_FAILURE_MESSAGES.get(
            code, "Run creation or launch failed.",
        )
    return "Run creation or launch failed."


def _run_browser_status(summaries) -> str:
    active = next(
        (
            item for item in summaries
            if item.state.value in _ACTIVE_RUN_STATES
            or item.schedule_state == "paused_resource"
        ),
        None,
    )
    if active is not None:
        state = (
            "Paused for shared GPU resources"
            if active.schedule_state == "paused_resource"
            else active.state.value.replace("_", " ").title()
        )
        return f"{state} · {active.report_label} · {active.progress}."
    resumable = next(
        (item for item in summaries if item.state.value in _RESUMABLE_RUN_STATES),
        None,
    )
    if resumable is not None:
        label = "Queued" if resumable.state.value == "ready" else "Paused"
        return f"{label} · {resumable.report_label} · {resumable.progress}."
    if any(item.state.value == "complete" for item in summaries):
        return "No active analyses. Completed reports are available below."
    if summaries:
        return "No active analyses."
    return "No Analyst runs yet."


def _refresh_interval_ms(summaries) -> int:
    if any(
        item.state.value in _ACTIVE_RUN_STATES or item.state.value == "ready"
        for item in summaries
    ):
        return _ACTIVE_REFRESH_MS
    return _IDLE_REFRESH_MS


class AnalystTab:
    """Low-input launcher; all durable and blocking work stays off the Tk thread."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self._busy = False
        self._refreshing = False
        self._refresh_after_id = None
        self._refresh_interval = _IDLE_REFRESH_MS
        self._summaries = []
        self._manifest_choices = []
        self._manifest_index = -1
        self._model_choices = []
        self._model_combo = None
        self._model_connect_btn = None
        self._model_status_var = None
        self._report_window = None
        self._advanced_dialog = None
        self._export_dialog = None
        self._auto_label = ""
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build()
        self._refresh_runs()

    def _build(self) -> None:
        frame = self.frame
        description = tk.Label(
            frame,
            text=(
                "Point Analyst at a folder of a host's extracted files. It reads "
                "them and tells you what the host is and what is worth your attention."
            ),
            justify="left",
            anchor="w",
            wraplength=590,
        )
        self._theme.apply_to_widget(description, "label")
        description.pack(fill=tk.X, padx=16, pady=(14, 10))

        self._main_form = tk.Frame(frame)
        self._theme.apply_to_widget(self._main_form, "main_window")
        self._main_form.pack(fill=tk.X, padx=16)
        self._main_form.columnconfigure(1, weight=1)

        self._source_var = tk.StringVar(value="")
        output_folder = str(get_paths().analyst_reports_dir)
        settings_manager = self._context.get("settings_manager")
        if settings_manager is not None:
            try:
                persisted_output = settings_manager.get_setting(
                    "analyst.output_folder", None,
                )
                if type(persisted_output) is str and persisted_output.strip():
                    output_folder = persisted_output
            except Exception:
                pass
        self._output_var = tk.StringVar(value=output_folder)
        self._label_var = tk.StringVar(value="")
        self._mode_var = tk.StringVar(value="fast")
        self._source_kind_var = tk.StringVar(value="directory")
        self._manifest_var = tk.StringVar(value="No persisted extraction selected")
        self._server_kind_var = tk.StringVar(value="local")
        self._server_host_var = tk.StringVar(value="127.0.0.1")
        self._server_port_var = tk.StringVar(value="11434")
        self._model_var = tk.StringVar(value="")
        self._manifest_combo = None
        self._manifest_refresh_btn = None

        self._add_path_row(
            self._main_form, 0, "Input Dir", self._source_var, self._browse_source,
        )
        self._add_path_row(
            self._main_form, 1, "Output Dir", self._output_var, self._browse_output,
        )
        self._source_var.trace_add("write", self._source_changed)

        label = tk.Label(self._main_form, text="Name")
        self._theme.apply_to_widget(label, "label")
        label.grid(row=2, column=0, sticky="w", pady=3)
        entry = tk.Entry(self._main_form, textvariable=self._label_var)
        self._theme.apply_to_widget(entry, "entry")
        entry.grid(row=2, column=1, sticky="ew", padx=(8, 7), pady=3)
        optional = tk.Label(self._main_form, text="optional")
        self._theme.apply_to_widget(optional, "label")
        optional.grid(row=2, column=2, sticky="w", pady=3)

        mode_label = tk.Label(self._main_form, text="Read")
        self._theme.apply_to_widget(mode_label, "label")
        mode_label.grid(row=3, column=0, sticky="w", pady=(8, 3))
        modes = tk.Frame(self._main_form)
        self._theme.apply_to_widget(modes, "main_window")
        modes.grid(
            row=3, column=1, columnspan=2, sticky="w",
            padx=(8, 0), pady=(8, 3),
        )
        for value, text in (
            ("fast", "Quick look"),
            ("deep", "Full read"),
        ):
            button = tk.Radiobutton(
                modes, text=text, variable=self._mode_var, value=value,
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.pack(side=tk.LEFT, padx=(0, 14))

        try:
            offer_enabled = settings_manager is not None and (
                settings_manager.get_setting("analyst.offer_after_extract", False) is True
            )
        except Exception:
            offer_enabled = False
        self._offer_var = tk.BooleanVar(value=offer_enabled)
        self._selected_model_tag, self._selected_model_digest = (
            self._load_selected_model(settings_manager)
        )

        controls = tk.Frame(frame)
        self._theme.apply_to_widget(controls, "main_window")
        controls.pack(fill=tk.X, padx=16, pady=(9, 7))
        self._analyze_btn = tk.Button(
            controls, text="Analyze", command=self._start_analysis,
        )
        self._theme.apply_to_widget(self._analyze_btn, "button_primary")
        self._analyze_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._advanced_btn = tk.Button(
            controls, text="Advanced...", command=self._open_advanced,
        )
        self._theme.apply_to_widget(self._advanced_btn, "button_secondary")
        self._advanced_btn.pack(side=tk.RIGHT)

        runs_header = tk.Frame(frame)
        self._theme.apply_to_widget(runs_header, "main_window")
        runs_header.pack(fill=tk.X, padx=16, pady=(8, 4))
        runs_label = tk.Label(runs_header, text="Runs", anchor="w")
        self._theme.apply_to_widget(runs_label, "label")
        runs_label.pack(side=tk.LEFT)
        self._select_all_btn = tk.Button(
            runs_header, text="Select all", command=self._select_all_runs,
        )
        self._theme.apply_to_widget(self._select_all_btn, "button_secondary")
        self._select_all_btn.pack(side=tk.RIGHT)

        self._runs = ttk.Treeview(
            frame,
            columns=("label", "mode", "state", "progress"),
            show="headings",
            height=6,
            selectmode="extended",
        )
        for key, text, width in (
            ("label", "Analysis", 190),
            ("mode", "Read", 65),
            ("state", "State", 145),
            ("progress", "Result", 250),
        ):
            self._runs.heading(key, text=text)
            self._runs.column(key, width=width, anchor="w")
        self._runs.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 7))
        self._runs.bind("<<TreeviewSelect>>", self._on_selection, add="+")

        run_controls = tk.Frame(frame)
        self._theme.apply_to_widget(run_controls, "main_window")
        run_controls.pack(fill=tk.X, padx=16, pady=(0, 7))
        self._reports_btn = tk.Button(
            run_controls, text="Open report", state="disabled",
            command=self._open_reports,
        )
        self._theme.apply_to_widget(self._reports_btn, "button_secondary")
        self._reports_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._export_btn = tk.Button(
            run_controls, text="Export selected...", state="disabled",
            command=self._open_export_dialog,
        )
        self._theme.apply_to_widget(self._export_btn, "button_secondary")
        self._export_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._resume_btn = tk.Button(
            run_controls, text="Resume", state="disabled", command=self._resume_selected,
        )
        self._theme.apply_to_widget(self._resume_btn, "button_secondary")
        self._resume_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._cancel_btn = tk.Button(
            run_controls, text="Cancel", state="disabled", command=self._cancel_selected,
        )
        self._theme.apply_to_widget(self._cancel_btn, "button_danger")
        self._cancel_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._abandon_btn = tk.Button(
            run_controls, text="Abandon", state="disabled",
            command=self._abandon_selected,
        )
        self._theme.apply_to_widget(self._abandon_btn, "button_danger")
        self._abandon_btn.pack(side=tk.LEFT, padx=(0, 7))
        self._delete_btn = tk.Button(
            run_controls, text="Delete", state="disabled",
            command=self._delete_selected,
        )
        self._theme.apply_to_widget(self._delete_btn, "button_danger")
        self._delete_btn.pack(side=tk.LEFT, padx=(0, 7))

        self._status_var = tk.StringVar(value="Ready.")
        status = tk.Label(frame, textvariable=self._status_var, anchor="w")
        self._theme.apply_to_widget(status, "label")
        status.pack(fill=tk.X, padx=16, pady=(0, 12))

    def _add_path_row(
        self, parent, row, text, variable, command, *, button_text="Browse",
    ) -> None:
        label = tk.Label(parent, text=text)
        self._theme.apply_to_widget(label, "label")
        label.grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(parent, textvariable=variable)
        self._theme.apply_to_widget(entry, "entry")
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 7), pady=3)
        button = tk.Button(parent, text=button_text, command=command)
        self._theme.apply_to_widget(button, "button_secondary")
        button.grid(row=row, column=2, pady=3)

    def _source_changed(self, *_args) -> None:
        source = self._source_var.get().strip()
        basename = Path(source).name if source else ""
        current_label = self._label_var.get().strip()
        if not current_label or current_label == self._auto_label:
            self._label_var.set(basename)
            self._auto_label = basename

    def _open_advanced(self) -> None:
        existing = self._advanced_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass

        parent = self.frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Analyst - Advanced")
        dialog.transient(parent)
        dialog.resizable(True, False)
        self._theme.apply_to_widget(dialog, "main_window")
        self._advanced_dialog = dialog
        snapshot = {
            "source_kind": self._source_kind_var.get(),
            "manifest": self._manifest_var.get(),
            "manifest_index": self._manifest_index,
            "offer": self._offer_var.get(),
            "model_tag": self._selected_model_tag,
            "model_digest": self._selected_model_digest,
        }

        outer = tk.Frame(dialog)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        outer.columnconfigure(1, weight=1)

        source_heading = tk.Label(outer, text="Source")
        self._theme.apply_to_widget(source_heading, "label")
        source_heading.grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 3),
        )
        source_modes = tk.Frame(outer)
        self._theme.apply_to_widget(source_modes, "main_window")
        source_modes.grid(row=1, column=0, columnspan=3, sticky="w")
        for value, text in (
            ("directory", "A folder"),
            ("manifest", "From a saved scan"),
        ):
            button = tk.Radiobutton(
                source_modes,
                text=text,
                variable=self._source_kind_var,
                value=value,
                command=self._update_source_controls,
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.pack(side=tk.LEFT, padx=(0, 14))

        manifest_label = tk.Label(outer, text="Saved scan")
        self._theme.apply_to_widget(manifest_label, "label")
        manifest_label.grid(row=2, column=0, sticky="w", pady=3)
        self._manifest_combo = ttk.Combobox(
            outer, textvariable=self._manifest_var, state="disabled",
        )
        self._manifest_combo.grid(
            row=2, column=1, sticky="ew", padx=(8, 7), pady=3,
        )
        self._manifest_combo.bind(
            "<<ComboboxSelected>>", self._manifest_selected, add="+",
        )
        self._manifest_refresh_btn = tk.Button(
            outer, text="Reload", command=self._refresh_manifest_choices,
        )
        self._theme.apply_to_widget(
            self._manifest_refresh_btn, "button_secondary",
        )
        self._manifest_refresh_btn.grid(row=2, column=2, pady=3)

        server_heading = tk.Label(outer, text="Model server")
        self._theme.apply_to_widget(server_heading, "label")
        server_heading.grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(12, 3),
        )
        server_modes = tk.Frame(outer)
        self._theme.apply_to_widget(server_modes, "main_window")
        server_modes.grid(row=4, column=0, columnspan=3, sticky="w")
        local = tk.Radiobutton(
            server_modes, text="Local", variable=self._server_kind_var,
            value="local",
        )
        self._theme.apply_to_widget(local, "checkbox")
        local.pack(side=tk.LEFT, padx=(0, 14))
        remote = tk.Radiobutton(
            server_modes, text="Remote AI box", variable=self._server_kind_var,
            value="remote", state="disabled",
        )
        self._theme.apply_to_widget(remote, "checkbox")
        remote.pack(side=tk.LEFT, padx=(0, 7))
        later = tk.Label(server_modes, text="later card")
        self._theme.apply_to_widget(later, "label")
        later.pack(side=tk.LEFT)

        connection = tk.Frame(outer)
        self._theme.apply_to_widget(connection, "main_window")
        connection.grid(row=5, column=0, columnspan=3, sticky="w", pady=3)
        host_label = tk.Label(connection, text="Host")
        self._theme.apply_to_widget(host_label, "label")
        host_label.pack(side=tk.LEFT)
        host = tk.Entry(
            connection, textvariable=self._server_host_var,
            state="disabled", width=18,
        )
        self._theme.apply_to_widget(host, "entry")
        host.pack(side=tk.LEFT, padx=(7, 12))
        port_label = tk.Label(connection, text="Port")
        self._theme.apply_to_widget(port_label, "label")
        port_label.pack(side=tk.LEFT)
        port = tk.Entry(
            connection, textvariable=self._server_port_var,
            state="disabled", width=8,
        )
        self._theme.apply_to_widget(port, "entry")
        port.pack(side=tk.LEFT, padx=(7, 12))
        test = tk.Button(connection, text="Test", state="disabled")
        self._theme.apply_to_widget(test, "button_secondary")
        test.pack(side=tk.LEFT)

        model_heading = tk.Label(outer, text="Model")
        self._theme.apply_to_widget(model_heading, "label")
        model_heading.grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(12, 3),
        )
        self._model_combo = ttk.Combobox(
            outer, textvariable=self._model_var, state="readonly",
        )
        self._model_combo.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=3,
        )
        self._model_connect_btn = tk.Button(
            outer, text="Connect / Refresh", command=self._discover_models,
        )
        self._theme.apply_to_widget(
            self._model_connect_btn, "button_secondary",
        )
        self._model_connect_btn.grid(row=7, column=2, padx=(7, 0), pady=3)

        helper = tk.Label(
            outer,
            text=(
                "Connect once to pull the model list from the server, then pick "
                "one. Refresh if you change your backend."
            ),
            justify="left",
            anchor="w",
            wraplength=500,
        )
        self._theme.apply_to_widget(helper, "label")
        helper.grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(1, 0),
        )
        self._model_status_var = tk.StringVar(value="")
        model_status = tk.Label(
            outer, textvariable=self._model_status_var, anchor="w",
        )
        self._theme.apply_to_widget(model_status, "label")
        model_status.grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(1, 3),
        )

        try:
            from experimental.analyst.service import list_discovered_models

            model_choices = list_discovered_models()
        except Exception:
            model_choices = ()
        self._populate_model_choices(model_choices)

        offer = tk.Checkbutton(
            outer,
            text="Offer a quick review after an extraction",
            variable=self._offer_var,
        )
        self._theme.apply_to_widget(offer, "checkbox")
        offer.grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(12, 3),
        )

        actions = tk.Frame(outer)
        self._theme.apply_to_widget(actions, "main_window")
        actions.grid(row=11, column=0, columnspan=3, sticky="e", pady=(14, 0))

        def close(*, save: bool) -> None:
            if save:
                self._persist_offer_setting()
                self._persist_model_selection()
                self._persist_output_folder()
            else:
                self._source_kind_var.set(snapshot["source_kind"])
                self._manifest_var.set(snapshot["manifest"])
                self._manifest_index = snapshot["manifest_index"]
                self._offer_var.set(snapshot["offer"])
                self._selected_model_tag = snapshot["model_tag"]
                self._selected_model_digest = snapshot["model_digest"]
                self._model_var.set(snapshot["model_tag"] or "")
            self._advanced_dialog = None
            self._manifest_combo = None
            self._manifest_refresh_btn = None
            self._model_combo = None
            self._model_connect_btn = None
            self._model_status_var = None
            dialog.grab_release()
            dialog.destroy()

        cancel = tk.Button(
            actions, text="Cancel", command=lambda: close(save=False),
        )
        self._theme.apply_to_widget(cancel, "button_secondary")
        cancel.pack(side=tk.LEFT, padx=(0, 7))
        save = tk.Button(
            actions, text="Save", command=lambda: close(save=True),
        )
        self._theme.apply_to_widget(save, "button_primary")
        save.pack(side=tk.LEFT)
        dialog.protocol("WM_DELETE_WINDOW", lambda: close(save=False))
        dialog.grab_set()
        self._refresh_manifest_choices()
        ensure_dialog_focus(dialog, parent)

    @staticmethod
    def _load_selected_model(settings_manager) -> tuple[str | None, str | None]:
        if settings_manager is None:
            return None, None
        try:
            tag = settings_manager.get_setting(
                "analyst.selected_model_tag", None,
            )
            digest = settings_manager.get_setting(
                "analyst.selected_model_digest", None,
            )
        except Exception:
            return None, None
        if type(tag) is not str or not tag or type(digest) is not str or not digest:
            return None, None
        return tag, digest

    def _populate_model_choices(self, choices, *, preferred_tag=None) -> None:
        self._model_choices = list(choices)
        combo = self._model_combo
        status = self._model_status_var
        if combo is None or status is None:
            return
        if self._model_choices:
            tags = [choice.model_tag for choice in self._model_choices]
            combo.configure(values=tags)
            wanted = preferred_tag or self._selected_model_tag
            index = tags.index(wanted) if wanted in tags else 0
            combo.current(index)
            count = len(tags)
            status.set(
                f"Found {count} model{'s' if count != 1 else ''} on this server."
            )
            return

        if self._selected_model_tag is not None:
            shown_tag = self._selected_model_tag
            message = "Saved model shown. Connect to refresh the model list."
        else:
            from experimental.analyst.models import ANALYST_DEFAULTS

            shown_tag = ANALYST_DEFAULTS.model_tag
            message = "Pinned default shown. Connect to load the model list."
        combo.configure(values=(shown_tag,))
        combo.current(0)
        status.set(message)

    def _discover_models(self) -> None:
        dialog = self._advanced_dialog
        button = self._model_connect_btn
        if dialog is None or button is None:
            return
        button.configure(state="disabled")
        if self._model_status_var is not None:
            self._model_status_var.set("Connecting to the local model server…")
        preferred_tag = self._model_var.get()

        def work() -> None:
            try:
                from experimental.analyst.service import discover_models

                choices = discover_models()
            except Exception:
                self._schedule(
                    lambda: self._finish_model_discovery(
                        dialog, None, preferred_tag,
                    )
                )
                return
            self._schedule(
                lambda: self._finish_model_discovery(
                    dialog, choices, preferred_tag,
                )
            )

        threading.Thread(target=work, daemon=True).start()

    def _finish_model_discovery(self, dialog, choices, preferred_tag) -> None:
        if self._advanced_dialog is not dialog:
            return
        try:
            if not dialog.winfo_exists():
                return
        except Exception:
            return
        if self._model_connect_btn is not None:
            self._model_connect_btn.configure(state="normal")
        if choices is None:
            if self._model_status_var is not None:
                self._model_status_var.set("Model server is unavailable.")
            safe_messagebox.showerror(
                "Analyst",
                "Could not reach the model server on loopback.",
                parent=dialog,
            )
            return
        self._populate_model_choices(choices, preferred_tag=preferred_tag)

    def _persist_model_selection(self) -> None:
        selected_tag = None
        selected_digest = None
        combo = self._model_combo
        if combo is not None:
            index = combo.current()
            if 0 <= index < len(self._model_choices):
                choice = self._model_choices[index]
                selected_tag = choice.model_tag
                selected_digest = choice.model_digest
            elif (
                not self._model_choices
                and self._selected_model_tag is not None
                and self._model_var.get() == self._selected_model_tag
            ):
                selected_tag = self._selected_model_tag
                selected_digest = self._selected_model_digest
        self._selected_model_tag = selected_tag
        self._selected_model_digest = selected_digest

        settings_manager = self._context.get("settings_manager")
        if settings_manager is None:
            return
        try:
            settings_manager.set_setting(
                "analyst.selected_model_tag", selected_tag,
            )
            settings_manager.set_setting(
                "analyst.selected_model_digest", selected_digest,
            )
        except Exception:
            pass

    def _select_all_runs(self) -> None:
        children = self._runs.get_children("")
        if children:
            self._runs.selection_set(*children)
            self._on_selection()

    def _browse_source(self) -> None:
        selected = filedialog.askdirectory(parent=self.frame.winfo_toplevel())
        if selected:
            self._source_var.set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.frame.winfo_toplevel())
        if selected:
            self._output_var.set(selected)

    def _open_export_dialog(self) -> None:
        selected = tuple(self._runs.selection())
        if not selected:
            return
        existing = self._export_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass

        parent = self.frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Export reports")
        dialog.transient(parent)
        dialog.resizable(False, False)
        self._theme.apply_to_widget(dialog, "main_window")
        self._export_dialog = dialog

        outer = tk.Frame(dialog)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        outer.columnconfigure(1, weight=1)

        selected_label = tk.Label(
            outer, text=f"Selected: {len(selected)} reports", anchor="w",
        )
        self._theme.apply_to_widget(selected_label, "label")
        selected_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self._export_format_vars = {
            "md": tk.BooleanVar(value=True),
            "json": tk.BooleanVar(value=True),
            "txt": tk.BooleanVar(value=False),
            "csv": tk.BooleanVar(value=False),
        }
        self._export_layout_var = tk.StringVar(value="per_report")
        self._export_include_vars = {
            "read": tk.BooleanVar(value=True),
            "facts": tk.BooleanVar(value=True),
        }
        default_folder = self._output_var.get().strip()
        if not default_folder or not Path(default_folder).expanduser().is_dir():
            default_folder = str(Path.home())
        self._export_folder_var = tk.StringVar(value=default_folder)

        format_label = tk.Label(outer, text="Format")
        self._theme.apply_to_widget(format_label, "label")
        format_label.grid(row=1, column=0, sticky="nw", pady=3)
        format_controls = tk.Frame(outer)
        self._theme.apply_to_widget(format_controls, "main_window")
        format_controls.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0))
        for index, (value, text) in enumerate((
            ("md", "Markdown"), ("json", "JSON"),
            ("txt", "Plain text"), ("csv", "CSV (facts)"),
        )):
            button = tk.Checkbutton(
                format_controls, text=text, variable=self._export_format_vars[value],
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 14))

        layout_label = tk.Label(outer, text="Layout")
        self._theme.apply_to_widget(layout_label, "label")
        layout_label.grid(row=2, column=0, sticky="nw", pady=(10, 3))
        layout_controls = tk.Frame(outer)
        self._theme.apply_to_widget(layout_controls, "main_window")
        layout_controls.grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 3),
        )
        for value, text in (
            ("per_report", "One file per report"),
            ("combined", "One combined file"),
        ):
            button = tk.Radiobutton(
                layout_controls, text=text, variable=self._export_layout_var,
                value=value,
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.pack(anchor="w")

        include_label = tk.Label(outer, text="Include")
        self._theme.apply_to_widget(include_label, "label")
        include_label.grid(row=3, column=0, sticky="w", pady=(10, 3))
        include_controls = tk.Frame(outer)
        self._theme.apply_to_widget(include_controls, "main_window")
        include_controls.grid(
            row=3, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 3),
        )
        for value, text in (("read", "The read"), ("facts", "The facts")):
            button = tk.Checkbutton(
                include_controls, text=text, variable=self._export_include_vars[value],
            )
            self._theme.apply_to_widget(button, "checkbox")
            button.pack(side=tk.LEFT, padx=(0, 14))

        folder_label = tk.Label(outer, text="Folder")
        self._theme.apply_to_widget(folder_label, "label")
        folder_label.grid(row=4, column=0, sticky="w", pady=(10, 3))
        folder_entry = tk.Entry(outer, textvariable=self._export_folder_var, width=42)
        self._theme.apply_to_widget(folder_entry, "entry")
        folder_entry.grid(row=4, column=1, sticky="ew", padx=(8, 7), pady=(10, 3))

        def browse() -> None:
            chosen = filedialog.askdirectory(parent=dialog)
            if chosen:
                self._export_folder_var.set(chosen)

        browse_button = tk.Button(outer, text="Browse", command=browse)
        self._theme.apply_to_widget(browse_button, "button_secondary")
        browse_button.grid(row=4, column=2, pady=(10, 3))

        actions = tk.Frame(outer)
        self._theme.apply_to_widget(actions, "main_window")
        actions.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
        cancel = tk.Button(
            actions, text="Cancel", command=lambda: self._close_export_dialog(dialog),
        )
        self._theme.apply_to_widget(cancel, "button_secondary")
        cancel.pack(side=tk.LEFT, padx=(0, 7))
        export = tk.Button(
            actions,
            text="Export",
            command=lambda: self._start_export(dialog, selected, cancel, export),
        )
        self._theme.apply_to_widget(export, "button_primary")
        export.pack(side=tk.LEFT)
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._close_export_dialog(dialog))
        dialog.grab_set()
        ensure_dialog_focus(dialog, parent)

    def _close_export_dialog(self, dialog) -> None:
        if self._export_dialog is dialog:
            self._export_dialog = None
        try:
            dialog.grab_release()
            dialog.destroy()
        except tk.TclError:
            pass

    def _start_export(self, dialog, run_ids, cancel_button, export_button) -> None:
        formats = frozenset(
            value for value, variable in self._export_format_vars.items()
            if variable.get()
        )
        include = frozenset(
            value for value, variable in self._export_include_vars.items()
            if variable.get()
        )
        layout = self._export_layout_var.get()
        destination = Path(self._export_folder_var.get()).expanduser()
        if not formats:
            safe_messagebox.showwarning(
                "Analyst export", "Select at least one format.", parent=dialog,
            )
            return
        if not include:
            safe_messagebox.showwarning(
                "Analyst export", "Include the read, the facts, or both.", parent=dialog,
            )
            return
        if not destination.is_dir():
            safe_messagebox.showwarning(
                "Analyst export", "Choose an existing export folder.", parent=dialog,
            )
            return

        labels = {
            item.run_id: item.report_label for item in self._summaries
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
                self._schedule(
                    lambda: self._finish_export(
                        dialog, None, skipped, destination,
                    )
                )
                return
            self._schedule(
                lambda: self._finish_export(dialog, result, skipped, destination)
            )

        threading.Thread(target=work, daemon=True).start()

    def _finish_export(self, dialog, result, skipped: int, destination: Path) -> None:
        self._close_export_dialog(dialog)
        parent = self.frame.winfo_toplevel()
        if result is None:
            safe_messagebox.showerror(
                "Analyst export", "The selected reports could not be exported.",
                parent=parent,
            )
            return
        message = f"Exported {result.report_count} reports to {destination}."
        if skipped:
            message += f" Skipped {skipped} legacy runs."
        safe_messagebox.showinfo("Analyst export", message, parent=parent)

    def _update_source_controls(self) -> None:
        if self._manifest_combo is None:
            return
        manifest = self._source_kind_var.get() == "manifest"
        self._manifest_combo.configure(
            state="readonly" if manifest and self._manifest_choices else "disabled",
        )

    def _main_db_path(self) -> Path | None:
        raw = self._context.get("main_db_path")
        if type(raw) is not str or not raw:
            return None
        candidate = Path(raw).expanduser().absolute()
        return candidate if candidate.is_absolute() else None

    def _refresh_manifest_choices(self) -> None:
        db_path = self._main_db_path()
        if db_path is None:
            self._finish_manifest_refresh(())
            return

        def work() -> None:
            try:
                from experimental.analyst.manifest import list_extraction_manifests

                choices = list_extraction_manifests(db_path)
            except Exception:
                choices = ()
            self._schedule(lambda: self._finish_manifest_refresh(choices))

        threading.Thread(target=work, daemon=True).start()

    def _finish_manifest_refresh(self, choices) -> None:
        self._manifest_choices = list(choices)
        labels = [choice.display_label for choice in self._manifest_choices]
        if self._manifest_combo is None:
            return
        self._manifest_combo.configure(values=labels)
        if labels:
            self._manifest_combo.current(0)
            self._manifest_index = 0
            self._manifest_var.set(labels[0])
            self._manifest_selected()
        else:
            self._manifest_index = -1
            self._manifest_var.set("No persisted extraction available")
        self._update_source_controls()

    def _manifest_selected(self, _event=None) -> None:
        if self._manifest_combo is None:
            return
        index = self._manifest_combo.current()
        self._manifest_index = index
        if 0 <= index < len(self._manifest_choices) and not self._label_var.get().strip():
            self._label_var.set(self._manifest_choices[index].ip_address)

    def _persist_offer_setting(self) -> None:
        settings_manager = self._context.get("settings_manager")
        if settings_manager is None:
            self._offer_var.set(False)
            return
        try:
            settings_manager.set_setting(
                "analyst.offer_after_extract", bool(self._offer_var.get()),
            )
        except Exception:
            self._offer_var.set(False)

    def _persist_output_folder(self) -> None:
        settings_manager = self._context.get("settings_manager")
        if settings_manager is None:
            return
        try:
            settings_manager.set_setting(
                "analyst.output_folder", self._output_var.get().strip(),
            )
        except Exception:
            pass

    def _schedule(self, callback) -> None:
        try:
            if self.frame.winfo_exists():
                self.frame.after(0, callback)
        except Exception:
            pass

    def _start_analysis(self) -> None:
        if self._busy:
            return
        self._persist_output_folder()
        source_kind = self._source_kind_var.get()
        request = None
        choice = None
        manifest_output = None
        report_label = self._label_var.get().strip()
        mode = self._mode_var.get()
        model_tag = self._selected_model_tag
        model_digest = self._selected_model_digest
        try:
            if source_kind == "directory":
                from experimental.analyst.service import DirectoryRunRequest

                if not report_label:
                    report_label = Path(self._source_var.get()).name
                    self._label_var.set(report_label)
                request = DirectoryRunRequest(
                    Path(self._source_var.get()),
                    Path(self._output_var.get()),
                    report_label,
                    mode,
                )
            elif source_kind == "manifest":
                index = self._manifest_index
                choice = self._manifest_choices[index]
                output_text = self._output_var.get().strip()
                manifest_output = Path(output_text) if output_text else None
                if manifest_output is not None and not manifest_output.is_absolute():
                    raise ValueError("output base must be absolute")
                if not report_label.strip():
                    raise ValueError("report label is required")
            else:
                raise ValueError("unknown source kind")
        except Exception:
            safe_messagebox.showerror(
                "Analyst", "Choose a valid source and enter a report label.",
                parent=self.frame.winfo_toplevel(),
            )
            return
        self._set_busy(True, "Inventorying and creating the durable run…")

        def work() -> None:
            try:
                if source_kind == "directory":
                    from experimental.analyst.service import create_and_launch

                    launch = create_and_launch(
                        request,
                        model_tag=model_tag,
                        model_digest=model_digest,
                    )
                else:
                    from experimental.analyst.service import create_manifest_and_launch

                    launch = create_manifest_and_launch(
                        choice.reference,
                        main_db_path=self._main_db_path(),
                        output_base=manifest_output,
                        report_label=report_label,
                        mode=mode,
                        model_tag=model_tag,
                        model_digest=model_digest,
                    )
            except Exception as exc:
                message = _creation_failure_message(exc)
                self._schedule(lambda: self._finish_action(False, message))
                return
            self._schedule(
                lambda: self._finish_action(
                    True, f"Worker launched for run {launch.run_id[:12]}.",
                )
            )

        threading.Thread(target=work, daemon=True).start()

    def _refresh_runs(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        if not self._busy:
            self._status_var.set("Refreshing durable Analyst progress…")

        def work() -> None:
            try:
                from experimental.analyst.service import (
                    list_run_summaries,
                    reconcile_for_hydration,
                )

                try:
                    reconcile_for_hydration()
                except Exception:
                    pass
                summaries = list_run_summaries()
            except Exception:
                self._schedule(lambda: self._finish_refresh(None))
                return
            self._schedule(lambda: self._finish_refresh(summaries))

        threading.Thread(target=work, daemon=True).start()

    def _finish_refresh(self, summaries) -> None:
        self._refreshing = False
        if summaries is None:
            if not self._busy:
                self._status_var.set("Analyst progress is temporarily unavailable.")
            self._schedule_auto_refresh()
            return
        selected = self._runs.selection()
        self._summaries = list(summaries)
        self._refresh_interval = _refresh_interval_ms(self._summaries)
        self._runs.delete(*self._runs.get_children(""))
        for item in self._summaries:
            state = "paused_resource" if item.schedule_state == "paused_resource" else item.state.value
            self._runs.insert(
                "", "end", iid=item.run_id,
                values=(
                    item.report_label,
                    "Quick" if item.mode == "fast" else "Full",
                    state,
                    item.result_label,
                ),
            )
        run_ids = {item.run_id for item in self._summaries}
        retained = tuple(run_id for run_id in selected if run_id in run_ids)
        if not retained and self._summaries:
            retained = (self._summaries[0].run_id,)
        if retained:
            self._runs.selection_set(*retained)
            self._runs.focus(retained[0])
        self._reports_btn.configure(
            state=(
                "normal"
                if any(item.state.value == "complete" for item in self._summaries)
                else "disabled"
            )
        )
        if not self._busy:
            self._status_var.set(_run_browser_status(self._summaries))
        self._hydrate_registry()
        self._on_selection()
        self._schedule_auto_refresh()

    def _schedule_auto_refresh(self) -> None:
        if self._refresh_after_id is not None:
            return

        def refresh() -> None:
            self._refresh_after_id = None
            try:
                if not self.frame.winfo_exists():
                    return
            except Exception:
                return
            if self._busy or self._refreshing:
                self._schedule_auto_refresh()
                return
            self._refresh_runs()

        try:
            if self.frame.winfo_exists():
                self._refresh_after_id = self.frame.after(
                    self._refresh_interval, refresh,
                )
        except Exception:
            self._refresh_after_id = None

    def _hydrate_registry(self) -> None:
        registry = self._context.get("running_tasks_registry")
        if registry is None:
            registry = get_running_task_registry()

        def reopen(_run_id: str):
            return self._reopen

        def cancel(run_id: str):
            return lambda: self._cancel_run_id(run_id)

        def abandon(run_id: str):
            return lambda: self._abandon_run_id(run_id)

        apply_analyst_task_hydration(
            registry, self._summaries, reopen=reopen, cancel=cancel,
            abandon=abandon,
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

    def _selected_summaries(self):
        selected = set(self._runs.selection())
        return [item for item in self._summaries if item.run_id in selected]

    def _on_selection(self, _event=None) -> None:
        self._export_btn.configure(
            state="normal" if self._runs.selection() else "disabled",
        )
        item = self._selected_summary()
        from experimental.analyst.state import RunState, TERMINAL_RUN_STATES

        chosen = self._selected_summaries()
        deletable = bool(chosen) and all(
            summary.state in TERMINAL_RUN_STATES for summary in chosen
        )
        self._delete_btn.configure(state="normal" if deletable else "disabled")
        if item is None:
            self._resume_btn.configure(state="disabled")
            self._cancel_btn.configure(state="disabled")
            self._abandon_btn.configure(state="disabled")
            return

        resumable = (
            item.state in {
                RunState.READY, RunState.INTERRUPTED,
                RunState.CANCELLED_PENDING_RESUME,
            }
            and item.schedule_state != "paused_resource"
        )
        cancellable = (
            item.state in {
                RunState.RUNNING, RunState.CANCEL_REQUESTED, RunState.FINALIZING,
            }
            or (
                item.state is RunState.INTERRUPTED
                and item.schedule_state == "paused_resource"
            )
        )
        self._resume_btn.configure(state="normal" if resumable else "disabled")
        self._abandon_btn.configure(state="normal" if resumable else "disabled")
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

    def _abandon_selected(self) -> None:
        item = self._selected_summary()
        if item is not None:
            self._abandon_run_id(item.run_id)

    def _abandon_run_id(self, run_id: str) -> None:
        self._run_service_action(run_id, "abandon")

    def _delete_selected(self) -> None:
        if self._busy:
            return
        from experimental.analyst.state import TERMINAL_RUN_STATES

        chosen = [
            summary for summary in self._selected_summaries()
            if summary.state in TERMINAL_RUN_STATES
        ]
        if not chosen:
            return
        count = len(chosen)
        if not safe_messagebox.askyesno(
            "Analyst",
            f"Delete {count} report(s)? This removes the report files and cannot be undone.",
            parent=self.frame.winfo_toplevel(),
        ):
            return
        run_ids = [summary.run_id for summary in chosen]
        self._set_busy(True, "Deleting…")

        def work() -> None:
            failed = 0
            for run_id in run_ids:
                try:
                    from experimental.analyst.service import delete_run

                    delete_run(run_id)
                except Exception:
                    failed += 1
            self._schedule(lambda: self._finish_delete(run_ids, failed))

        threading.Thread(target=work, daemon=True).start()

    def _finish_delete(self, run_ids, failed) -> None:
        registry = (
            self._context.get("running_tasks_registry")
            or get_running_task_registry()
        )
        for run_id in run_ids:
            registry.remove_task(f"analyst:{run_id}")
        total = len(run_ids)
        deleted = total - failed
        if failed:
            message = f"Deleted {deleted} of {total}; {failed} failed."
        else:
            message = f"Deleted {deleted} report(s)."
        self._finish_action(failed == 0, message)

    def _run_service_action(self, run_id: str, action: str) -> None:
        if self._busy:
            return
        self._set_busy(True, "Requesting " + action + "…")

        def work() -> None:
            try:
                if action == "resume":
                    from experimental.analyst.service import resume_run

                    resume_run(run_id)
                elif action == "cancel":
                    from experimental.analyst.service import cancel_run

                    cancel_run(run_id)
                else:
                    from experimental.analyst.service import abandon_run

                    abandon_run(run_id)
            except Exception as exc:
                message = (
                    _creation_failure_message(exc)
                    if action == "abandon"
                    else action.capitalize() + " failed."
                )
                self._schedule(lambda: self._finish_action(False, message))
                return
            self._schedule(
                lambda: self._finish_service_action(run_id, action),
            )

        threading.Thread(target=work, daemon=True).start()

    def _finish_service_action(self, run_id: str, action: str) -> None:
        if action == "abandon":
            registry = self._context.get("running_tasks_registry")
            if registry is None:
                registry = get_running_task_registry()
            registry.remove_task(f"analyst:{run_id}")
            message = "Run abandoned."
        else:
            message = action.capitalize() + " requested."
        self._finish_action(True, message)

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
