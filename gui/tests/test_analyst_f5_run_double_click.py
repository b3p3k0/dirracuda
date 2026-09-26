"""F5: double-clicking a completed run opens its report.

The rows looked clickable and were not. Every other list in the app opens on
a double-click, so a dead action here read as a bug.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState


@pytest.fixture(autouse=True)
def _isolated_profiles(tmp_path, monkeypatch):
    from experimental.analyst import profiles as profile_store

    path = tmp_path / "profiles.db"
    real_ensure = profile_store.ensure_default_profile
    real_list = profile_store.list_profiles
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **kwargs: real_ensure(path=path),
    )
    monkeypatch.setattr(
        profile_store, "list_profiles", lambda **kwargs: real_list(path=path),
    )


def _summary(run_id: str, state: RunState) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id=run_id,
        state=state,
        report_label=f"host-{run_id[:4]}",
        mode="fast",
        created_at_utc="2026-09-26T12:00:00Z",
        updated_at_utc="2026-09-26T12:01:00Z",
        discovered_files=10,
        terminal_files=10,
        selected_files=3,
        model_reviewed_files=3,
        detector_hits=1,
        model_findings=1,
        schedule_state="available",
        resource_not_before_utc=None,
    )


@pytest.fixture
def tab(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    instance = module.AnalystTab(root, {})
    instance._finish_refresh((
        _summary("a" * 32, RunState.COMPLETE),
        _summary("b" * 32, RunState.RUNNING),
    ))
    instance._open_reports = MagicMock()
    try:
        yield instance
    finally:
        root.destroy()


class _Event:
    def __init__(self, x: int = 40, y: int = 10) -> None:
        self.x = x
        self.y = y


def _click_row(tab, run_id: str, *, region: str = "cell") -> None:
    tab._runs.identify_region = lambda _x, _y: region
    tab._runs.identify_row = lambda _y: run_id
    tab._on_run_activated(_Event())


@pytest.mark.gui_smoke
def test_double_clicking_a_completed_run_opens_its_report(tab) -> None:
    _click_row(tab, "a" * 32)
    tab._open_reports.assert_called_once_with()


@pytest.mark.gui_smoke
def test_the_double_clicked_row_becomes_the_selected_one(tab) -> None:
    """Not selection()[0]: several rows can be selected at once."""
    tab._runs.selection_set("a" * 32, "b" * 32)
    _click_row(tab, "b" * 32)
    assert tab._runs.selection() == ("b" * 32,)


@pytest.mark.gui_smoke
def test_a_running_run_opens_nothing(tab) -> None:
    """Consistent with the Open Report button, which is disabled for it."""
    from gui.components.experimental_features import analyst_tab as module

    tab._open_reports = lambda: module.AnalystTab._open_reports(tab)
    tab._report_window = None
    _click_row(tab, "b" * 32)
    assert tab._runs.selection() == ("b" * 32,)
    assert tab._report_window is None


@pytest.mark.gui_smoke
def test_a_double_click_on_empty_space_does_nothing(tab) -> None:
    _click_row(tab, "")
    tab._open_reports.assert_not_called()


@pytest.mark.gui_smoke
def test_a_separator_double_click_is_left_to_tk(tab) -> None:
    """Tk auto-fits a column on that gesture; it must keep working."""
    _click_row(tab, "a" * 32, region="separator")
    tab._open_reports.assert_not_called()


@pytest.mark.gui_smoke
def test_a_heading_double_click_does_not_open_a_report(tab) -> None:
    _click_row(tab, "a" * 32, region="heading")
    tab._open_reports.assert_not_called()


@pytest.mark.gui_smoke
def test_the_binding_is_actually_wired(tab) -> None:
    """A handler nothing calls is the bug this card is fixing."""
    assert "_on_run_activated" in tab._runs.bind("<Double-Button-1>")
