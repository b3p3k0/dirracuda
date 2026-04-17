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


class RedditTab:
    """Content widget for the Reddit experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        description = (
            "Reddit ingestion and review tools.\n"
            "Fetches posts from r/opendirectories, extracts SMB/FTP/HTTP targets,\n"
            "and stores them in a sidecar DB at ~/.dirracuda/reddit_od.db"
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

    def _invoke_reddit_grab(self) -> None:
        cb = self._context.get("reddit_grab_callback")
        if cb is not None:
            cb()

    def _invoke_open_reddit_post_db(self) -> None:
        cb = self._context.get("open_reddit_post_db")
        if cb is not None:
            cb()


def build_reddit_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Reddit tab frame."""
    tab = RedditTab(parent, context)
    return tab.frame
