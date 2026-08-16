"""Bridge durable Analyst summaries into the shared Running Tasks registry."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState
from gui.utils.running_tasks import RunningTaskRegistry


_ACTIVE_STATES = frozenset(set(RunState) - {RunState.COMPLETE, RunState.ABANDONED})


def apply_analyst_task_hydration(
    registry: RunningTaskRegistry,
    summaries: Sequence[AnalystRunSummary],
    *,
    reopen: Callable[[str], Callable[[], None]],
    cancel: Callable[[str], Callable[[], None]],
) -> None:
    """Idempotently replace only the Analyst-owned registry projection."""
    if not isinstance(registry, RunningTaskRegistry):
        raise TypeError("Analyst hydration requires a RunningTaskRegistry")
    if not callable(reopen) or not callable(cancel):
        raise TypeError("Analyst task callbacks must be factories")
    active = {
        item.task_id: item
        for item in summaries
        if type(item) is AnalystRunSummary and item.state in _ACTIVE_STATES
    }
    for snapshot in registry.list_tasks():
        if snapshot.task_id.startswith("analyst:") and snapshot.task_id not in active:
            registry.remove_task(snapshot.task_id)
    for task_id, item in active.items():
        state = _task_state(item)
        can_cancel = (
            item.state in {RunState.RUNNING, RunState.CANCEL_REQUESTED}
            or (
                item.state is RunState.INTERRUPTED
                and item.schedule_state == "paused_resource"
            )
        )
        registry.upsert_task(
            task_id,
            task_type="analyst",
            name=f"Analyst · {item.report_label}",
            state=state,
            progress=item.progress,
            started_at=item.created_at_utc,
            reopen_callback=reopen(item.run_id),
            cancel_callback=cancel(item.run_id) if can_cancel else None,
        )


def _task_state(summary: AnalystRunSummary) -> str:
    if summary.schedule_state == "paused_resource":
        return "paused"
    if summary.state is RunState.CANCEL_REQUESTED:
        return "cancelling"
    if summary.state in {RunState.RUNNING, RunState.FINALIZING}:
        return "running"
    return "queued"


__all__ = ["apply_analyst_task_hydration"]
