"""Stay-open quick-filter popover using the window's existing variables."""

import tkinter as tk
from typing import Any, Dict, Mapping, Tuple


QUICK_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("favorites_only", "Favorites only"),
    ("exclude_avoid", "Exclude avoid"),
    ("probed_only", "Probed only"),
    ("exclude_compromised", "Exclude compromised"),
    ("shares_filter", "Show Only Shares >0"),
    ("has_notes_only", "Has notes"),
)


def count_active(filter_vars: Mapping[str, Any]) -> int:
    return sum(bool(filter_vars[key].get()) for key, _ in QUICK_FILTERS if key in filter_vars)


def format_button_label(n) -> str:
    return "Filters ▾" if n == 0 else f"Filters ({n}) ▾"


class FilterDropdown:
    def __init__(self, parent, theme, filter_vars, on_changed):
        self.theme = theme
        self.filter_vars = filter_vars
        self.on_changed = on_changed
        self.popover = None
        self.option_widgets: Dict[str, tk.Checkbutton] = {}
        self._option_states: Dict[str, bool] = {}
        self._outside_window = None
        self._outside_bind_id = None
        self._traces = []
        self.button = tk.Button(
            parent, text=format_button_label(count_active(filter_vars)), command=self.toggle
        )
        theme.apply_to_widget(self.button, "button_secondary")
        for key, _ in QUICK_FILTERS:
            if key in filter_vars:
                var = filter_vars[key]
                self._traces.append((var, var.trace_add("write", self._refresh_label)))
        self.button.bind("<Destroy>", self._on_destroy, add="+")

    def _refresh_label(self, *_args):
        self.button.configure(text=format_button_label(count_active(self.filter_vars)))

    def _on_toggle(self):
        self.on_changed()

    def is_open(self) -> bool:
        try:
            return self.popover is not None and bool(self.popover.winfo_exists())
        except tk.TclError:
            return False

    def open(self):
        if self.is_open():
            return
        self.close()
        self.popover = tk.Toplevel(self.button)
        self.popover.overrideredirect(True)
        self.theme.apply_to_widget(self.popover, "card")
        self.popover.geometry(
            f"+{self.button.winfo_rootx()}+{self.button.winfo_rooty() + self.button.winfo_height()}"
        )
        for key, label in QUICK_FILTERS:
            if key not in self.filter_vars:
                continue
            option = tk.Checkbutton(
                self.popover, text=label, variable=self.filter_vars[key],
                command=self._on_toggle, anchor="w",
                state=tk.NORMAL if self._option_states.get(key, True) else tk.DISABLED,
            )
            self.theme.apply_to_widget(option, "checkbox")
            option.pack(anchor="w", fill=tk.X)
            self.option_widgets[key] = option
        self.popover.bind("<Escape>", lambda _event: self.close())
        self._outside_window = self.button.winfo_toplevel()
        self._outside_bind_id = self._outside_window.bind(
            "<Button-1>", self._on_outside_click, add="+"
        )
        self.popover.focus_set()

    def _on_outside_click(self, event):
        # Popover children have their own toplevel bind tag, so ticks stay open.
        if event.widget is not self.button:
            self.close()

    def close(self):
        if self._outside_bind_id is not None:
            try:
                self._outside_window.unbind("<Button-1>", self._outside_bind_id)
            except tk.TclError:
                pass
        self._outside_bind_id = None
        self._outside_window = None
        if self.popover is not None:
            try:
                self.popover.destroy()
            except tk.TclError:
                pass
        self.popover = None
        self.option_widgets.clear()

    def toggle(self):
        if self.is_open():
            self.close()
        else:
            self.open()

    def set_option_state(self, key, enabled: bool):
        self._option_states[key] = enabled
        if self.is_open() and key in self.option_widgets:
            self.option_widgets[key].configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _on_destroy(self, event):
        if event.widget is self.button:
            self.close()
            for var, trace_id in self._traces:
                try:
                    var.trace_remove("write", trace_id)
                except tk.TclError:
                    pass
            self._traces.clear()
