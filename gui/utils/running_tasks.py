"""
Reusable running-task registry for monitorable long-running work.

This module is UI-framework agnostic. Consumers register tasks with optional
reopen/cancel callbacks and subscribe to snapshot updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import itertools
import threading
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class RunningTaskSnapshot:
    """Immutable snapshot used by UI listeners."""

    task_id: str
    task_type: str
    name: str
    state: str
    progress: str
    started_at: str
    reopen_callback: Optional[Callable[[], None]] = None
    cancel_callback: Optional[Callable[[], None]] = None


class RunningTaskRegistry:
    """
    In-memory registry for active/queued monitorable tasks.

    Tasks should be removed when they finish/cancel/fail to maintain the
    "active + queued only" policy.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._tasks: Dict[str, RunningTaskSnapshot] = {}
        self._listeners: List[Callable[[List[RunningTaskSnapshot]], None]] = []

    def create_task(
        self,
        *,
        task_type: str,
        name: str,
        state: str = "running",
        progress: str = "",
        reopen_callback: Optional[Callable[[], None]] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
    ) -> str:
        with self._lock:
            task_id = f"task-{next(self._ids)}"
            self._tasks[task_id] = RunningTaskSnapshot(
                task_id=task_id,
                task_type=str(task_type or "").strip() or "task",
                name=str(name or "").strip() or "Unnamed Task",
                state=str(state or "").strip() or "running",
                progress=str(progress or "").strip(),
                started_at=datetime.now().strftime("%H:%M:%S"),
                reopen_callback=reopen_callback,
                cancel_callback=cancel_callback,
            )
        self._notify_listeners()
        return task_id

    def update_task(
        self,
        task_id: str,
        *,
        name: Optional[str] = None,
        state: Optional[str] = None,
        progress: Optional[str] = None,
        reopen_callback: Optional[Callable[[], None]] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return

            new_reopen = current.reopen_callback if reopen_callback is None else reopen_callback
            new_cancel = current.cancel_callback if cancel_callback is None else cancel_callback
            self._tasks[task_id] = RunningTaskSnapshot(
                task_id=current.task_id,
                task_type=current.task_type,
                name=current.name if name is None else (str(name or "").strip() or current.name),
                state=current.state if state is None else (str(state or "").strip() or current.state),
                progress=current.progress if progress is None else str(progress or "").strip(),
                started_at=current.started_at,
                reopen_callback=new_reopen,
                cancel_callback=new_cancel,
            )
        self._notify_listeners()

    def remove_task(self, task_id: str) -> None:
        removed = False
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                removed = True
        if removed:
            self._notify_listeners()

    def get_task(self, task_id: str) -> Optional[RunningTaskSnapshot]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> List[RunningTaskSnapshot]:
        with self._lock:
            return list(self._tasks.values())

    def count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def has_tasks(self) -> bool:
        return self.count() > 0

    def cancel_all(self) -> None:
        callbacks: List[Callable[[], None]] = []
        with self._lock:
            for snapshot in self._tasks.values():
                if snapshot.cancel_callback:
                    callbacks.append(snapshot.cancel_callback)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue

    def subscribe(self, listener: Callable[[List[RunningTaskSnapshot]], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                return
            self._listeners.append(listener)
        self._notify_single_listener(listener)

    def unsubscribe(self, listener: Callable[[List[RunningTaskSnapshot]], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_single_listener(self, listener: Callable[[List[RunningTaskSnapshot]], None]) -> None:
        snapshot = self.list_tasks()
        try:
            listener(snapshot)
        except Exception:
            return

    def _notify_listeners(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        snapshot = self.list_tasks()
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                continue


_registry_lock = threading.RLock()
_shared_registry: Optional[RunningTaskRegistry] = None


def get_running_task_registry() -> RunningTaskRegistry:
    """Return the process-wide running-task registry singleton."""
    global _shared_registry
    with _registry_lock:
        if _shared_registry is None:
            _shared_registry = RunningTaskRegistry()
        return _shared_registry


def _reset_running_task_registry_for_tests() -> None:
    """Reset shared task registry for deterministic test isolation."""
    global _shared_registry
    with _registry_lock:
        _shared_registry = RunningTaskRegistry()
