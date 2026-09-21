from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState


def _summary() -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id="a" * 32,
        state=RunState.INTERRUPTED,
        report_label="Recovered run",
        mode="fast",
        created_at_utc="2026-09-20T12:00:00Z",
        updated_at_utc="2026-09-20T12:01:00Z",
        discovered_files=1,
        terminal_files=0,
        selected_files=0,
        model_reviewed_files=0,
        detector_hits=0,
        model_findings=0,
        schedule_state="available",
        resource_not_before_utc=None,
    )


@pytest.fixture
def tab(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    refresh_runs = module.AnalystTab._refresh_runs
    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    widget = module.AnalystTab(root, {})
    widget._schedule = lambda callback: callback()
    widget._schedule_auto_refresh = lambda: None

    class Thread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(module.threading, "Thread", Thread)
    try:
        yield widget, refresh_runs
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_refresh_reconciles_before_applying_summaries(tab, monkeypatch) -> None:
    widget, refresh_runs = tab
    summary = _summary()
    calls = []
    monkeypatch.setattr(
        "experimental.analyst.service.reconcile_for_hydration",
        lambda: calls.append("reconcile"),
    )
    monkeypatch.setattr(
        "experimental.analyst.service.list_run_summaries",
        lambda: calls.append("list") or (summary,),
    )

    refresh_runs(widget)

    assert calls == ["reconcile", "list"]
    assert widget._summaries == [summary]
    assert widget._runs.item(summary.run_id, "values")[2] == "interrupted"


@pytest.mark.gui_smoke
def test_refresh_still_applies_summaries_when_reconcile_fails(
    tab, monkeypatch,
) -> None:
    widget, refresh_runs = tab
    summary = _summary()
    reconcile = MagicMock(side_effect=RuntimeError("reconcile failed"))
    list_summaries = MagicMock(return_value=(summary,))
    monkeypatch.setattr(
        "experimental.analyst.service.reconcile_for_hydration", reconcile,
    )
    monkeypatch.setattr(
        "experimental.analyst.service.list_run_summaries", list_summaries,
    )

    refresh_runs(widget)

    reconcile.assert_called_once_with()
    list_summaries.assert_called_once_with()
    assert widget._summaries == [summary]
    assert widget._status_var.get().startswith("Paused · Recovered run ·")
