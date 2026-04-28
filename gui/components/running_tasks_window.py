"""
Non-modal Running Tasks window for monitorable dashboard jobs.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from gui.utils.running_tasks import RunningTaskRegistry, RunningTaskSnapshot


class RunningTasksWindow:
    """Window that displays active/queued tasks and reopens task dialogs."""

    def __init__(self, parent: tk.Widget, theme, registry: RunningTaskRegistry):
        self.parent = parent
        self.theme = theme
        self.registry = registry

        self.window: Optional[tk.Toplevel] = None
        self.tree: Optional[ttk.Treeview] = None
        self.empty_label: Optional[tk.Label] = None
        self._subscribed = False

    def show(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return
        self._build()

    def destroy(self) -> None:
        if self._subscribed:
            self.registry.unsubscribe(self._on_tasks_changed)
            self._subscribed = False
        if self.window and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        self.tree = None
        self.empty_label = None

    def _build(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title("Running Tasks")
        self.window.geometry("760x320")
        self.window.minsize(640, 240)
        self.window.transient(self.parent)
        self.window.protocol("WM_DELETE_WINDOW", self.destroy)

        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        frame = tk.Frame(self.window)
        if self.theme:
            self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("type", "name", "state", "progress", "started")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        self.tree.heading("type", text="Type")
        self.tree.heading("name", text="Name")
        self.tree.heading("state", text="State")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("started", text="Started")
        self.tree.column("type", width=90, anchor="w", stretch=False)
        self.tree.column("name", width=280, anchor="w")
        self.tree.column("state", width=100, anchor="center", stretch=False)
        self.tree.column("progress", width=170, anchor="w")
        self.tree.column("started", width=90, anchor="center", stretch=False)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self._on_row_double_click, add="+")

        self.empty_label = tk.Label(
            frame,
            text="No active or queued tasks.",
            anchor="w",
            justify="left",
        )
        if self.theme:
            self.theme.apply_to_widget(self.empty_label, "label")
        self.empty_label.pack(fill=tk.X, pady=(8, 0))

        if self.theme:
            self.theme.apply_theme_to_application(self.window)

        self.registry.subscribe(self._on_tasks_changed)
        self._subscribed = True
        self.window.lift()
        self.window.focus_force()

    def _on_tasks_changed(self, tasks: List[RunningTaskSnapshot]) -> None:
        if not (self.window and self.window.winfo_exists() and self.tree):
            return

        existing_ids = set(self.tree.get_children(""))
        incoming_ids = {task.task_id for task in tasks}

        for stale_id in existing_ids - incoming_ids:
            self.tree.delete(stale_id)

        for task in tasks:
            values = (
                str(task.task_type or "").upper(),
                task.name,
                task.state,
                task.progress,
                task.started_at,
            )
            if task.task_id in existing_ids:
                self.tree.item(task.task_id, values=values)
            else:
                self.tree.insert("", "end", iid=task.task_id, values=values)

        if self.empty_label:
            if tasks:
                self.empty_label.configure(text="Double-click a task to reopen its monitor dialog.")
            else:
                self.empty_label.configure(text="No active or queued tasks.")

    def _on_row_double_click(self, _event=None) -> None:
        if not self.tree:
            return
        selected = self.tree.selection()
        if not selected:
            return
        task_id = selected[0]
        task = self.registry.get_task(task_id)
        if not task or not task.reopen_callback:
            return
        try:
            task.reopen_callback()
        except Exception:
            return

