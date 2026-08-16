"""C13 registry, hydration, and import-boundary tests."""

from __future__ import annotations

from experimental.analyst.service import AnalystRunSummary
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
    assert task.progress == "4/10 files · 2/3 model-reviewed"
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


def test_dashboard_hydration_reconciles_once_then_refreshes_and_stops(monkeypatch):
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
    assert calls == ["reconcile", "list", "list"]
    dashboard_experimental.stop_analyst_task_hydration(widget)
    assert calls[-1][0] == "after_cancel"
