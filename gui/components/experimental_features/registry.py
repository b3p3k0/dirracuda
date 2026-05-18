"""
Feature registry for the Experimental Features dialog.

Each entry in FEATURES describes one tab. Adding or removing a feature only
requires editing FEATURES — the dialog shell requires no surgery.

Contract per entry:
    feature_id : str   — unique identifier (used for settings keys etc.)
    label      : str   — tab display name
    build_tab  : callable(parent: tk.Widget, context: dict) -> tk.Widget
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

import tkinter as tk
from tkinter import ttk


@dataclass
class ExperimentalFeature:
    feature_id: str
    label: str
    build_tab: Callable[[tk.Widget, dict], tk.Widget]


def _get_features() -> List[ExperimentalFeature]:
    """Return the ordered list of registered experimental features."""
    from gui.components.experimental_features.se_dork_tab import build_se_dork_tab
    from gui.components.experimental_features.reddit_tab import build_reddit_tab
    from gui.components.experimental_features.dorkbook_tab import build_dorkbook_tab
    from gui.components.experimental_features.keymaster_tab import build_keymaster_tab

    return [
        ExperimentalFeature(
            feature_id="se_dork",
            label="SearXNG",
            build_tab=build_se_dork_tab,
        ),
        ExperimentalFeature(
            feature_id="reddit",
            label="Reddit",
            build_tab=build_reddit_tab,
        ),
        ExperimentalFeature(
            feature_id="dorkbook",
            label="Dorkbook",
            build_tab=build_dorkbook_tab,
        ),
        ExperimentalFeature(
            feature_id="keymaster",
            label="Keymaster",
            build_tab=build_keymaster_tab,
        ),
    ]


def build_all_tabs(notebook: ttk.Notebook, context: dict) -> None:
    """Add one tab per registered feature to *notebook*."""
    for feature in _get_features():
        tab_widget = feature.build_tab(notebook, context)
        notebook.add(tab_widget, text=feature.label)
