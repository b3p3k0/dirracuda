"""
Reddit Grab Dialog

Modal dialog for configuring and launching a Reddit ingestion run.
Collects options and passes an IngestOptions instance to grab_start_callback
when the user presses Run Grab.

Options:
  mode          "feed" | "search" | "user"
  sort          "new" | "top"
  top_window    "hour" | "day" | "week" | "month" | "year" | "all"
  query         str (search mode only)
  username      str (user mode only)
  max_posts     integer 1–200
  parse_body    bool
  include_nsfw  bool
  replace_cache bool
"""

import logging
import tkinter as tk
from tkinter import ttk
from gui.utils import safe_messagebox as messagebox
from typing import Callable

from gui.utils.style import get_theme
from experimental.redseek.service import IngestOptions

_log = logging.getLogger("dirracuda_gui.reddit_grab_dialog")


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
        settings_manager=None,
    ) -> None:
        self.parent = parent
        self.grab_start_callback = grab_start_callback
        self.theme = get_theme()
        self.settings = settings_manager

        self.mode_var = tk.StringVar(value="feed")
        self.query_var = tk.StringVar(value="")
        self.username_var = tk.StringVar(value="")
        self.sort_var = tk.StringVar(value="new")
        self.top_window_var = tk.StringVar(value="week")
        self.max_posts_var = tk.StringVar(value="50")
        self.parse_body_var = tk.BooleanVar(value=True)
        self.include_nsfw_var = tk.BooleanVar(value=False)
        self.replace_cache_var = tk.BooleanVar(value=False)

        self._load_settings()

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
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)

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

        # Mode selector (row 0)
        _label(0, "Mode:")
        mode_menu = ttk.Combobox(
            grid,
            textvariable=self.mode_var,
            values=["feed", "search", "user"],
            state="readonly",
            width=8,
        )
        mode_menu.grid(row=0, column=1, sticky=tk.W, pady=4)

        # Sort + Top Window (row 1)
        _label(1, "Sort:")
        sort_menu = ttk.Combobox(
            grid,
            textvariable=self.sort_var,
            values=["new", "top"],
            state="readonly",
            width=8,
        )
        sort_menu.grid(row=1, column=1, sticky=tk.W, pady=4)

        top_win_lbl = tk.Label(grid, text="Top Window:", anchor=tk.W)
        self.theme.apply_to_widget(top_win_lbl, "label")
        top_win_lbl.grid(row=1, column=2, sticky=tk.W, pady=4, padx=(12, 4))
        self._top_window_cb = ttk.Combobox(
            grid,
            textvariable=self.top_window_var,
            values=["hour", "day", "week", "month", "year", "all"],
            state="disabled",
            width=8,
        )
        self._top_window_cb.grid(row=1, column=3, sticky=tk.W, pady=4)

        self.sort_var.trace_add("write", self._on_sort_changed)
        self._on_sort_changed()  # set initial combobox state

        # Search query field (row 2 — hidden until mode=search)
        self._query_lbl = tk.Label(grid, text="Search Query:", anchor=tk.W)
        self.theme.apply_to_widget(self._query_lbl, "label")
        self._query_lbl.grid(row=2, column=0, sticky=tk.W, pady=4, padx=(0, 12))
        self._query_entry = tk.Entry(grid, textvariable=self.query_var, width=30)
        self.theme.apply_to_widget(self._query_entry, "entry")
        self._query_entry.grid(row=2, column=1, columnspan=3, sticky=tk.W, pady=4)
        self._query_lbl.grid_remove()
        self._query_entry.grid_remove()

        # Username field (row 3 — hidden until mode=user)
        self._username_lbl = tk.Label(grid, text="Username:", anchor=tk.W)
        self.theme.apply_to_widget(self._username_lbl, "label")
        self._username_lbl.grid(row=3, column=0, sticky=tk.W, pady=4, padx=(0, 12))
        self._username_entry = tk.Entry(grid, textvariable=self.username_var, width=30)
        self.theme.apply_to_widget(self._username_entry, "entry")
        self._username_entry.grid(row=3, column=1, columnspan=3, sticky=tk.W, pady=4)
        self._username_lbl.grid_remove()
        self._username_entry.grid_remove()

        self.mode_var.trace_add("write", self._on_mode_changed)
        self._on_mode_changed()  # set initial field visibility

        # Max posts (row 4)
        _label(4, "Max posts:")
        max_entry = tk.Entry(grid, textvariable=self.max_posts_var, width=6)
        self.theme.apply_to_widget(max_entry, "entry")
        max_entry.grid(row=4, column=1, sticky=tk.W, pady=4)

        # Checkboxes (rows 5-7)
        def _check(row: int, text: str, var: tk.BooleanVar) -> None:
            cb = tk.Checkbutton(grid, text=text, variable=var, anchor=tk.W)
            self.theme.apply_to_widget(cb, "checkbutton")
            cb.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        _check(5, "Parse body", self.parse_body_var)
        _check(6, "Include NSFW", self.include_nsfw_var)
        _check(7, "Replace cache", self.replace_cache_var)

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
    # Mode-change handler
    # ------------------------------------------------------------------

    def _on_mode_changed(self, *_) -> None:
        mode = self.mode_var.get()
        if mode == "search":
            self._query_lbl.grid()
            self._query_entry.grid()
            self._username_lbl.grid_remove()
            self._username_entry.grid_remove()
        elif mode == "user":
            self._query_lbl.grid_remove()
            self._query_entry.grid_remove()
            self._username_lbl.grid()
            self._username_entry.grid()
        else:  # feed
            self._query_lbl.grid_remove()
            self._query_entry.grid_remove()
            self._username_lbl.grid_remove()
            self._username_entry.grid_remove()

    # ------------------------------------------------------------------
    # Sort-change handler
    # ------------------------------------------------------------------

    def _on_sort_changed(self, *_) -> None:
        if self.sort_var.get() == "top":
            self._top_window_cb.configure(state="readonly")
        else:
            self._top_window_cb.configure(state="disabled")
            self.top_window_var.set("week")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> IngestOptions | None:
        mode = self.mode_var.get().strip()
        query = ""
        username = ""
        if mode == "search":
            query = self.query_var.get().strip()
            if not query:
                messagebox.showerror(
                    "Invalid input",
                    "Search query cannot be empty.",
                    parent=self.dialog,
                )
                return None
        elif mode == "user":
            username = self.username_var.get().strip()
            if not username:
                messagebox.showerror(
                    "Invalid input",
                    "Username cannot be empty.",
                    parent=self.dialog,
                )
                return None
            if " " in username:
                messagebox.showerror(
                    "Invalid input",
                    "Username cannot contain spaces.",
                    parent=self.dialog,
                )
                return None

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
            top_window=self.top_window_var.get(),
            mode=mode,
            query=query,
            username=username,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        options = self._validate()
        if options is None:
            return
        self._save_settings()
        self.grab_start_callback(options)
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self._save_settings()
        self.dialog.destroy()

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        if self.settings is None:
            return

        def _coerce_bool(value, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off", ""}:
                    return False
            return default

        try:
            mode = str(self.settings.get_setting('reddit_grab.mode', 'feed'))
            if mode not in {'feed', 'search', 'user'}:
                mode = 'feed'
            self.mode_var.set(mode)

            sort = str(self.settings.get_setting('reddit_grab.sort', 'new'))
            if sort not in {'new', 'top'}:
                sort = 'new'
            self.sort_var.set(sort)

            top_window = str(self.settings.get_setting('reddit_grab.top_window', 'week'))
            if top_window not in {'hour', 'day', 'week', 'month', 'year', 'all'}:
                top_window = 'week'
            self.top_window_var.set(top_window)

            self.query_var.set(str(self.settings.get_setting('reddit_grab.query', '')))
            self.username_var.set(str(self.settings.get_setting('reddit_grab.username', '')))

            raw_max = self.settings.get_setting('reddit_grab.max_posts', 50)
            try:
                max_posts = max(1, min(200, int(raw_max)))
            except (ValueError, TypeError):
                max_posts = 50
            self.max_posts_var.set(str(max_posts))

            self.parse_body_var.set(
                _coerce_bool(self.settings.get_setting('reddit_grab.parse_body', True), True)
            )
            self.include_nsfw_var.set(
                _coerce_bool(self.settings.get_setting('reddit_grab.include_nsfw', False), False)
            )
            self.replace_cache_var.set(
                _coerce_bool(self.settings.get_setting('reddit_grab.replace_cache', False), False)
            )
        except Exception as e:
            _log.warning("reddit_grab_dialog: failed to load settings: %s", e)

    def _save_settings(self) -> None:
        if self.settings is None:
            return
        try:
            self.settings.set_setting('reddit_grab.mode', self.mode_var.get())
            self.settings.set_setting('reddit_grab.sort', self.sort_var.get())
            self.settings.set_setting('reddit_grab.top_window', self.top_window_var.get())
            self.settings.set_setting('reddit_grab.query', self.query_var.get())
            self.settings.set_setting('reddit_grab.username', self.username_var.get())
            raw = self.max_posts_var.get().strip()
            try:
                max_posts = max(1, min(200, int(raw)))
            except (ValueError, TypeError):
                max_posts = 50
            self.settings.set_setting('reddit_grab.max_posts', max_posts)
            self.settings.set_setting('reddit_grab.parse_body', bool(self.parse_body_var.get()))
            self.settings.set_setting('reddit_grab.include_nsfw', bool(self.include_nsfw_var.get()))
            self.settings.set_setting('reddit_grab.replace_cache', bool(self.replace_cache_var.get()))
        except Exception as e:
            _log.warning("reddit_grab_dialog: failed to save settings: %s", e)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show(self) -> None:
        self.dialog.wait_window()


def show_reddit_grab_dialog(
    parent: tk.Widget,
    grab_start_callback: Callable[[IngestOptions], None],
    settings_manager=None,
) -> None:
    """
    Show the Reddit Grab configuration dialog modally.

    Calls grab_start_callback(IngestOptions) when user confirms.
    Returns when the dialog is closed (run or cancel).
    """
    dialog = RedditGrabDialog(parent, grab_start_callback, settings_manager=settings_manager)
    dialog.show()
