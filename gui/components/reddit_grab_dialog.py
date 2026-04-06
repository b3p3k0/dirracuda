"""
Reddit Grab Dialog

Modal dialog for configuring and launching a Reddit ingestion run.
Collects options and passes an IngestOptions instance to grab_start_callback
when the user presses Run Grab.

Options:
  sort          "new" | "top"
  max_posts     integer 1–200
  parse_body    bool
  include_nsfw  bool
  replace_cache bool
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from gui.utils.style import get_theme
from experimental.redseek.service import IngestOptions


class RedditGrabDialog:
    """
    Modal dialog for configuring a Reddit ingestion run.

    Validates options locally, then calls grab_start_callback(IngestOptions)
    before destroying itself. On cancel or invalid input the dialog stays open
    (or is destroyed with no callback).
    """

    def __init__(
        self,
        parent: tk.Widget,
        grab_start_callback: Callable[[IngestOptions], None],
    ) -> None:
        self.parent = parent
        self.grab_start_callback = grab_start_callback
        self.theme = get_theme()

        self.sort_var = tk.StringVar(value="new")
        self.max_posts_var = tk.StringVar(value="50")
        self.parse_body_var = tk.BooleanVar(value=True)
        self.include_nsfw_var = tk.BooleanVar(value=False)
        self.replace_cache_var = tk.BooleanVar(value=False)

        self.dialog = tk.Toplevel(parent)
        self._build_dialog()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_dialog(self) -> None:
        self.dialog.title("Reddit Grab")
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        self.theme.apply_to_widget(self.dialog, "dialog")

        outer = tk.Frame(self.dialog, padx=16, pady=14)
        self.theme.apply_to_widget(outer, "dialog")
        outer.pack(fill=tk.BOTH, expand=True)

        # Options grid
        grid = tk.Frame(outer)
        self.theme.apply_to_widget(grid, "dialog")
        grid.pack(fill=tk.X)

        def _label(row: int, text: str) -> None:
            lbl = tk.Label(grid, text=text, anchor=tk.W)
            self.theme.apply_to_widget(lbl, "label")
            lbl.grid(row=row, column=0, sticky=tk.W, pady=4, padx=(0, 12))

        # Sort
        _label(0, "Sort:")
        sort_menu = ttk.Combobox(
            grid,
            textvariable=self.sort_var,
            values=["new", "top"],
            state="readonly",
            width=8,
        )
        sort_menu.grid(row=0, column=1, sticky=tk.W, pady=4)

        # Max posts
        _label(1, "Max posts:")
        max_entry = tk.Entry(grid, textvariable=self.max_posts_var, width=6)
        self.theme.apply_to_widget(max_entry, "entry")
        max_entry.grid(row=1, column=1, sticky=tk.W, pady=4)

        # Checkboxes
        def _check(row: int, text: str, var: tk.BooleanVar) -> None:
            cb = tk.Checkbutton(grid, text=text, variable=var, anchor=tk.W)
            self.theme.apply_to_widget(cb, "checkbutton")
            cb.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        _check(2, "Parse body", self.parse_body_var)
        _check(3, "Include NSFW", self.include_nsfw_var)
        _check(4, "Replace cache", self.replace_cache_var)

        # Buttons
        btn_frame = tk.Frame(outer)
        self.theme.apply_to_widget(btn_frame, "dialog")
        btn_frame.pack(fill=tk.X, pady=(14, 0))

        run_btn = tk.Button(btn_frame, text="Run Grab", command=self._on_run)
        self.theme.apply_to_widget(run_btn, "button_primary")
        run_btn.pack(side=tk.LEFT, padx=(0, 8))

        cancel_btn = tk.Button(btn_frame, text="Cancel", command=self._on_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.LEFT)

        # Centre over parent
        self.dialog.update_idletasks()
        try:
            px = self.parent.winfo_rootx() + self.parent.winfo_width() // 2
            py = self.parent.winfo_rooty() + self.parent.winfo_height() // 2
            w = self.dialog.winfo_reqwidth()
            h = self.dialog.winfo_reqheight()
            self.dialog.geometry(f"+{px - w // 2}+{py - h // 2}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> IngestOptions | None:
        sort = self.sort_var.get().strip()
        if sort not in {"new", "top"}:
            messagebox.showerror(
                "Invalid input",
                "Sort must be 'new' or 'top'.",
                parent=self.dialog,
            )
            return None

        raw = self.max_posts_var.get().strip()
        try:
            max_posts = int(raw)
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Max posts must be a whole number between 1 and 200.",
                parent=self.dialog,
            )
            return None

        if not (1 <= max_posts <= 200):
            messagebox.showerror(
                "Invalid input",
                f"Max posts must be between 1 and 200 (got {max_posts}).",
                parent=self.dialog,
            )
            return None

        return IngestOptions(
            sort=sort,
            max_posts=max_posts,
            parse_body=self.parse_body_var.get(),
            include_nsfw=self.include_nsfw_var.get(),
            replace_cache=self.replace_cache_var.get(),
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        options = self._validate()
        if options is None:
            return
        self.grab_start_callback(options)
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.dialog.destroy()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show(self) -> None:
        self.dialog.wait_window()


def show_reddit_grab_dialog(
    parent: tk.Widget,
    grab_start_callback: Callable[[IngestOptions], None],
) -> None:
    """
    Show the Reddit Grab configuration dialog modally.

    Calls grab_start_callback(IngestOptions) when user confirms.
    Returns when the dialog is closed (run or cancel).
    """
    dialog = RedditGrabDialog(parent, grab_start_callback)
    dialog.show()
