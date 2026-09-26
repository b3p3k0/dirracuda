from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState


def _summary(run_id: str, state: RunState) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id=run_id,
        state=state,
        report_label=f"Run {run_id[0]}",
        mode="fast",
        created_at_utc="2026-09-21T12:00:00Z",
        updated_at_utc="2026-09-21T12:01:00Z",
        discovered_files=1,
        terminal_files=1,
        selected_files=1,
        model_reviewed_files=1,
        detector_hits=0,
        model_findings=0,
        schedule_state="available",
        resource_not_before_utc=None,
    )


@pytest.fixture
def tab(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    widget = module.AnalystTab(root, {})
    try:
        yield widget
    finally:
        root.destroy()


def _select(tab, summaries, run_id: str) -> None:
    tab._summaries = list(summaries)
    for item in summaries:
        tab._runs.insert("", "end", iid=item.run_id, values=(item.report_label,))
    tab._runs.selection_set(run_id)
    tab._on_selection()


@pytest.mark.gui_smoke
def test_open_reports_opens_selected_complete_run(tab, monkeypatch) -> None:
    newest = _summary("a" * 32, RunState.COMPLETE)
    selected = _summary("b" * 32, RunState.COMPLETE)
    _select(tab, (newest, selected), selected.run_id)
    opened = MagicMock(return_value=SimpleNamespace(window=None))
    monkeypatch.setattr(
        "gui.components.analyst_report_window.show_analyst_report_window", opened,
    )

    tab._open_reports()

    assert opened.call_args.args[1] == selected.run_id
    assert opened.call_args.args[1] != newest.run_id


@pytest.mark.gui_smoke
def test_open_report_is_disabled_when_selected_run_is_not_complete(tab) -> None:
    running = _summary("c" * 32, RunState.RUNNING)

    _select(tab, (running,), running.run_id)

    assert tab._reports_btn.cget("state") == "disabled"


@pytest.mark.gui_smoke
def test_open_reports_reuses_existing_window_for_selected_run(tab) -> None:
    selected = _summary("d" * 32, RunState.COMPLETE)
    _select(tab, (selected,), selected.run_id)
    existing = SimpleNamespace(
        window=SimpleNamespace(
            winfo_exists=MagicMock(return_value=True),
            lift=MagicMock(),
            focus_force=MagicMock(),
        ),
        open_run=MagicMock(),
    )
    tab._report_window = existing

    tab._open_reports()

    existing.open_run.assert_called_once_with(selected.run_id)
    existing.window.lift.assert_called_once_with()
    existing.window.focus_force.assert_called_once_with()
