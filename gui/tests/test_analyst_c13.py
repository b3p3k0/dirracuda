"""C13 registry, hydration, and import-boundary tests."""

from __future__ import annotations

from experimental.analyst.service import (
    AnalystRunSummary,
    AnalystServiceError,
    ServiceFailure,
)
from experimental.analyst.state import RunState
from gui.utils.analyst_tasks import apply_analyst_task_hydration
from gui.utils.running_tasks import RunningTaskRegistry


def _summary(
    run_id: str = "a" * 32,
    *,
    state: RunState = RunState.RUNNING,
    schedule: str = "available",
) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id=run_id,
        state=state,
        report_label="Public Report",
        mode="fast",
        created_at_utc="2026-08-16T18:00:00Z",
        updated_at_utc="2026-08-16T18:01:00Z",
        discovered_files=10,
        terminal_files=4,
        selected_files=3,
        model_reviewed_files=2,
        detector_hits=5,
        model_findings=1,
        schedule_state=schedule,
        resource_not_before_utc=(
            "2026-08-16T18:10:00Z" if schedule != "available" else None
        ),
    )


def test_hydration_uses_stable_ids_without_duplicates_and_preserves_other_tasks():
    registry = RunningTaskRegistry()
    other = registry.create_task(task_type="scan", name="Other")
    reopened = []
    cancelled = []

    def reopen(run_id):
        return lambda: reopened.append(run_id)

    def cancel(run_id):
        return lambda: cancelled.append(run_id)

    item = _summary()
    apply_analyst_task_hydration(
        registry, (item,), reopen=reopen, cancel=cancel,
    )
    apply_analyst_task_hydration(
        registry, (item,), reopen=reopen, cancel=cancel,
    )
    assert registry.count() == 2
    assert registry.get_task(other) is not None
    task = registry.get_task(item.task_id)
    assert task is not None
    assert task.state == "running"
    assert task.progress == "4/10 finalized · 2/3 model-reviewed"
    task.reopen_callback()
    task.cancel_callback()
    assert reopened == [item.run_id]
    assert cancelled == [item.run_id]


def test_hydration_removes_terminal_or_missing_analyst_tasks_only():
    registry = RunningTaskRegistry()
    registry.upsert_task(
        "analyst:" + "b" * 32, task_type="analyst", name="Old",
    )
    scan = registry.create_task(task_type="scan", name="Scan")
    complete = _summary("c" * 32, state=RunState.COMPLETE)
    apply_analyst_task_hydration(
        registry, (complete,), reopen=lambda _run: lambda: None,
        cancel=lambda _run: lambda: None,
    )
    assert registry.get_task("analyst:" + "b" * 32) is None
    assert registry.get_task(complete.task_id) is None
    assert registry.get_task(scan) is not None


def test_paused_resource_hydrates_as_paused_and_cancellable():
    registry = RunningTaskRegistry()
    paused = _summary(
        state=RunState.INTERRUPTED, schedule="paused_resource",
    )
    apply_analyst_task_hydration(
        registry, (paused,), reopen=lambda _run: lambda: None,
        cancel=lambda _run: lambda: None,
    )
    task = registry.get_task(paused.task_id)
    assert task is not None
    assert task.state == "paused"
    assert callable(task.cancel_callback)


def test_ready_task_has_no_cancel_callback():
    registry = RunningTaskRegistry()
    ready = _summary(state=RunState.READY)
    apply_analyst_task_hydration(
        registry, (ready,), reopen=lambda _run: lambda: None,
        cancel=lambda _run: lambda: None,
    )
    task = registry.get_task(ready.task_id)
    assert task is not None
    assert task.state == "queued"
    assert task.cancel_callback is None


def test_registry_and_config_expose_analyst_once():
    from gui.components.experimental_features.registry import _get_features
    from shared.config_store import EXPERIMENTAL_MODULES

    assert [item.feature_id for item in _get_features()].count("analyst") == 1
    assert EXPERIMENTAL_MODULES.count("analyst") == 1


def test_analyst_ui_modules_import_without_service_actions(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "experimental.analyst.service.create_and_launch",
        lambda *_args, **_kwargs: calls.append("launch"),
    )
    import gui.components.analyst_report_window as report_window
    import gui.components.experimental_features.analyst_tab as analyst_tab

    assert report_window.AnalystReportWindow is not None
    assert analyst_tab.AnalystTab is not None
    assert calls == []


def test_creation_failures_are_closed_and_actionable():
    from gui.components.experimental_features.analyst_tab import (
        _creation_failure_message,
    )

    expected = {
        ServiceFailure.INVENTORY: "inventory failed",
        ServiceFailure.STORAGE: "save the durable run",
        ServiceFailure.LAUNCH: "saved, but its worker",
    }
    marker = "PRIVATE_EXCEPTION_MARKER"
    for code, fragment in expected.items():
        error = AnalystServiceError(code)
        error.__cause__ = RuntimeError(marker)
        message = _creation_failure_message(error)
        assert fragment in message
        assert marker not in message


def test_run_browser_status_distinguishes_live_queued_and_completed_runs():
    from gui.components.experimental_features.analyst_tab import (
        _ACTIVE_REFRESH_MS,
        _IDLE_REFRESH_MS,
        _refresh_interval_ms,
        _run_browser_status,
    )

    assert _run_browser_status((_summary(),)) == (
        "Running · Public Report · 4/10 finalized · 2/3 model-reviewed."
    )
    assert _run_browser_status((_summary(state=RunState.READY),)).startswith(
        "Queued · Public Report ·"
    )
    assert _run_browser_status((_summary(state=RunState.COMPLETE),)) == (
        "No active analyses. Completed reports are available below."
    )
    assert _refresh_interval_ms((_summary(),)) == _ACTIVE_REFRESH_MS
    assert _refresh_interval_ms((_summary(state=RunState.READY),)) == (
        _ACTIVE_REFRESH_MS
    )
    assert _refresh_interval_ms((_summary(state=RunState.COMPLETE),)) == (
        _IDLE_REFRESH_MS
    )


def test_tab_auto_refresh_repeats_only_while_live():
    from types import SimpleNamespace

    from gui.components.experimental_features.analyst_tab import AnalystTab

    callbacks = []
    calls = []

    class Frame:
        @staticmethod
        def winfo_exists():
            return True

        @staticmethod
        def after(delay, callback):
            callbacks.append((delay, callback))
            return "refresh-1"

    tab = AnalystTab.__new__(AnalystTab)
    tab.frame = Frame()
    tab._refresh_after_id = None
    tab._busy = False
    tab._refreshing = False
    tab._refresh_interval = 60_000
    tab._refresh_runs = lambda: calls.append("refresh")
    tab._schedule_auto_refresh()
    assert len(callbacks) == 1
    assert callbacks[0][0] == 60_000
    callbacks.pop()[1]()
    assert calls == ["refresh"]
    assert tab._refresh_after_id is None

    tab.frame = SimpleNamespace(winfo_exists=lambda: False)
    tab._schedule_auto_refresh()
    assert tab._refresh_after_id is None


def test_live_refresh_preserves_selection_and_disables_empty_report_browser():
    from gui.components.experimental_features.analyst_tab import AnalystTab

    summary = _summary()
    inserted = []
    selected = [summary.run_id]
    button_states = []
    statuses = []

    class Tree:
        @staticmethod
        def selection():
            return tuple(selected)

        @staticmethod
        def get_children(_parent):
            return (summary.run_id,)

        @staticmethod
        def delete(*_items):
            return None

        @staticmethod
        def insert(*args, **kwargs):
            inserted.append((args, kwargs))

        @staticmethod
        def selection_set(run_id):
            selected[:] = [run_id]

        @staticmethod
        def focus(_run_id):
            return None

    tab = AnalystTab.__new__(AnalystTab)
    tab._busy = False
    tab._refreshing = True
    tab._refresh_interval = 60_000
    tab._summaries = []
    tab._runs = Tree()
    tab._reports_btn = type(
        "Button", (), {"configure": lambda _self, **kw: button_states.append(kw)}
    )()
    tab._status_var = type(
        "Var", (), {"set": lambda _self, value: statuses.append(value)}
    )()
    tab._hydrate_registry = lambda: None
    tab._on_selection = lambda: None
    tab._schedule_auto_refresh = lambda: None

    tab._finish_refresh((summary,))

    assert selected == [summary.run_id]
    assert inserted[0][1]["values"][2:] == (
        "running", "4/10 finalized · 2/3 model-reviewed",
    )
    assert button_states == [{"state": "disabled"}]
    assert statuses[-1].startswith("Running · Public Report ·")
    assert tab._refresh_interval == 2_000


def test_dashboard_hydration_reconciles_each_refresh_and_stops(monkeypatch):
    from types import SimpleNamespace

    from gui.components import dashboard_experimental

    calls = []
    delayed = []

    class Parent:
        def after(self, delay, callback):
            if delay == 0:
                callback()
            else:
                delayed.append(callback)
            return f"after-{delay}-{len(delayed)}"

        def after_cancel(self, after_id):
            calls.append(("after_cancel", after_id))

    class Thread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            self.target()

    summary = _summary()
    monkeypatch.setattr(dashboard_experimental.threading, "Thread", Thread)
    monkeypatch.setattr(
        "experimental.analyst.service.reconcile_for_hydration",
        lambda: calls.append("reconcile") or "no_lease",
    )
    monkeypatch.setattr(
        "experimental.analyst.service.list_run_summaries",
        lambda: calls.append("list") or (summary,),
    )
    widget = SimpleNamespace(
        parent=Parent(), running_tasks_registry=RunningTaskRegistry(),
        settings_manager=None,
    )
    monkeypatch.setattr(
        dashboard_experimental,
        "handle_experimental_button_click",
        lambda _widget: calls.append("reopen"),
    )

    dashboard_experimental.start_analyst_task_hydration(widget)
    assert calls == ["reconcile", "list"]
    assert widget.running_tasks_registry.get_task(summary.task_id) is not None
    assert len(delayed) == 1
    delayed.pop()()
    assert calls == ["reconcile", "list", "reconcile", "list"]
    dashboard_experimental.stop_analyst_task_hydration(widget)
    assert calls[-1][0] == "after_cancel"
