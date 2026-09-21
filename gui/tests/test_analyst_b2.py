from __future__ import annotations

import queue
import threading
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState
from gui.utils.running_tasks import RunningTaskRegistry


def _summary(run_id: str, state: RunState) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id=run_id,
        state=state,
        report_label=f"Run {run_id[0]}",
        mode="fast",
        created_at_utc="2026-09-20T12:00:00Z",
        updated_at_utc="2026-09-20T12:01:00Z",
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
    registry = RunningTaskRegistry()
    widget = module.AnalystTab(root, {"running_tasks_registry": registry})
    try:
        yield module, widget, registry
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_delete_enabled_only_when_every_selected_run_is_terminal(tab) -> None:
    _module, widget, _registry = tab
    complete = _summary("a" * 32, RunState.COMPLETE)
    abandoned = _summary("b" * 32, RunState.ABANDONED)
    active = _summary("c" * 32, RunState.RUNNING)
    resumable = _summary("d" * 32, RunState.INTERRUPTED)
    widget._summaries = [complete, abandoned, active, resumable]
    for item in widget._summaries:
        widget._runs.insert("", "end", iid=item.run_id, values=(item.report_label,))

    widget._runs.selection_set(complete.run_id, abandoned.run_id)
    widget._on_selection()
    assert widget._delete_btn.cget("state") == "normal"

    widget._runs.selection_set(complete.run_id, active.run_id)
    widget._on_selection()
    assert widget._delete_btn.cget("state") == "disabled"

    widget._runs.selection_set(abandoned.run_id, resumable.run_id)
    widget._on_selection()
    assert widget._delete_btn.cget("state") == "disabled"

    widget._runs.selection_remove(*widget._runs.selection())
    widget._on_selection()
    assert widget._delete_btn.cget("state") == "disabled"


@pytest.mark.gui_smoke
def test_confirmed_delete_runs_each_service_call_and_refreshes(
    tab, monkeypatch,
) -> None:
    module, widget, registry = tab
    first = _summary("e" * 32, RunState.COMPLETE)
    second = _summary("f" * 32, RunState.ABANDONED)
    widget._summaries = [first, second]
    for item in widget._summaries:
        widget._runs.insert("", "end", iid=item.run_id, values=(item.report_label,))
        registry.upsert_task(
            item.task_id, task_type="Analyst", name=item.report_label,
        )
    widget._runs.selection_set(first.run_id, second.run_id)
    widget._on_selection()
    monkeypatch.setattr(module.safe_messagebox, "askyesno", lambda *_args, **_kwargs: True)
    deleted = MagicMock()
    monkeypatch.setattr("experimental.analyst.service.delete_run", deleted)
    callbacks = queue.Queue()
    completed = threading.Event()
    widget._schedule = lambda callback: (callbacks.put(callback), completed.set())
    refresh = MagicMock()
    widget._refresh_runs = refresh

    widget._delete_btn.invoke()
    assert completed.wait(2.0)
    callbacks.get(timeout=2.0)()

    assert deleted.call_args_list == [
        ((first.run_id,), {}),
        ((second.run_id,), {}),
    ]
    assert registry.get_task(first.task_id) is None
    assert registry.get_task(second.task_id) is None
    refresh.assert_called_once_with()
    assert widget._status_var.get() == "Deleted 2 report(s)."


@pytest.mark.gui_smoke
def test_declined_delete_does_nothing(tab, monkeypatch) -> None:
    module, widget, _registry = tab
    complete = _summary("1" * 32, RunState.COMPLETE)
    widget._summaries = [complete]
    widget._runs.insert("", "end", iid=complete.run_id, values=(complete.report_label,))
    widget._runs.selection_set(complete.run_id)
    widget._on_selection()
    monkeypatch.setattr(module.safe_messagebox, "askyesno", lambda *_args, **_kwargs: False)
    deleted = MagicMock()
    monkeypatch.setattr("experimental.analyst.service.delete_run", deleted)

    widget._delete_btn.invoke()

    deleted.assert_not_called()
    assert widget._busy is False
