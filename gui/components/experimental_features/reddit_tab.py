"""
Reddit tab for the Experimental Features dialog.

Provides two actions:
  - Open Reddit Grab   — launches the Reddit ingestion dialog
  - Open Reddit Post DB — opens the Reddit browser window

Both actions are wired to live callbacks supplied via *context*.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from gui.utils.style import get_theme
from shared.path_service import get_paths

_PATHS = get_paths()
_RUNNING_STATUS_TEXT = "Reddit Grab is running..."
_STATUS_POLL_MS = 500


class RedditTab:
    """Content widget for the Reddit experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self._status_visible = False
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        description = (
            "Reddit ingestion and review tools.\n"
            "Fetches posts from r/opendirectories, extracts SMB/FTP/HTTP targets,\n"
            f"and stores them in a sidecar DB at {_PATHS.reddit_od_db_file}"
        )
        desc_label = tk.Label(
            frame,
            text=description,
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._theme.apply_to_widget(desc_label, "label")
        desc_label.pack(anchor="w", padx=16, pady=(16, 12))

        btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))

        self._grab_btn = tk.Button(
            btn_frame,
            text="Open Reddit Grab",
            command=self._invoke_reddit_grab,
        )
        self._theme.apply_to_widget(self._grab_btn, "button_primary")
        self._grab_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._post_db_btn = tk.Button(
            btn_frame,
            text="Open Reddit Post DB",
            command=self._invoke_open_reddit_post_db,
        )
        self._theme.apply_to_widget(self._post_db_btn, "button_secondary")
        self._post_db_btn.pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="")
        self._status_label = tk.Label(
            frame,
            textvariable=self._status_var,
            anchor="w",
            justify="left",
        )
        self._theme.apply_to_widget(self._status_label, "label")
        self._update_running_status()

    def _invoke_reddit_grab(self) -> None:
        cb = self._context.get("reddit_grab_callback")
        if cb is not None:
            cb()
        self._update_running_status()

    def _invoke_open_reddit_post_db(self) -> None:
        cb = self._context.get("open_reddit_post_db")
        if cb is not None:
            cb()

    def _is_reddit_grab_running(self) -> bool:
        getter = self._context.get("reddit_grab_status_getter")
        if getter is None:
            return False
        try:
            return bool(getter())
        except Exception:
            return False

    def _update_running_status(self) -> None:
        running = self._is_reddit_grab_running()
        status_var = getattr(self, "_status_var", None)
        if status_var is not None:
            status_var.set(_RUNNING_STATUS_TEXT if running else "")

        grab_btn = getattr(self, "_grab_btn", None)
        if grab_btn is not None:
            grab_btn.configure(state=tk.DISABLED if running else tk.NORMAL)

        status_label = getattr(self, "_status_label", None)
        if status_label is not None and running and not self._status_visible:
            status_label.pack(anchor="w", padx=16, pady=(0, 8))
            self._status_visible = True
        elif status_label is not None and not running and self._status_visible:
            status_label.pack_forget()
            self._status_visible = False

        self._schedule_status_poll()

    def _schedule_status_poll(self) -> None:
        try:
            if self.frame.winfo_exists():
                self.frame.after(_STATUS_POLL_MS, self._update_running_status)
        except Exception:
            pass


def build_reddit_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Reddit tab frame."""
    tab = RedditTab(parent, context)
    return tab.frame
