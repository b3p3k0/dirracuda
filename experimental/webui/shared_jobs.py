"""Shared jobs facade for WebUI run/probe queue visibility.

This module keeps existing /api/scans behavior intact while exposing a shared
queue model that can include scan runs and experimental run/probe jobs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import enum
import logging
import secrets
import threading
import time
from typing import Callable, Optional

from experimental.webui.tasks import CancelResult, ScanQueue, TaskStatus

logger = logging.getLogger(__name__)


class SharedJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SharedCancelResult(str, enum.Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    TERMINAL = "terminal"


RunnerFn = Callable[["ExternalJob"], None]
CancelFn = Callable[[], None]


@dataclass
class ExternalJob:
    job_id: str
    source: str
    kind: str
    label: str
    runner: RunnerFn
    cancel_callback: Optional[CancelFn] = None
    status: SharedJobStatus = SharedJobStatus.QUEUED
    progress_pct: float = 0.0
    progress_message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_progress(self, message: str, progress_pct: Optional[float] = None) -> None:
        with self._lock:
            self.progress_message = str(message or "")
            if progress_pct is not None:
                try:
                    pct = float(progress_pct)
                except (TypeError, ValueError):
                    pct = self.progress_pct
                self.progress_pct = min(100.0, max(0.0, pct))

    def set_metadata(self, **kwargs) -> None:
        if not kwargs:
            return
        with self._lock:
            self.metadata.update(kwargs)

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def mark_done(self) -> None:
        with self._lock:
            self.status = SharedJobStatus.DONE
            self.progress_pct = 100.0
            self.finished_at = time.time()

    def mark_failed(self, error: str) -> None:
        with self._lock:
            self.status = SharedJobStatus.FAILED
            self.error = str(error or "job failed")
            self.finished_at = time.time()

    def mark_cancelled(self) -> None:
        with self._lock:
            self.status = SharedJobStatus.CANCELLED
            self.finished_at = time.time()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status.value,
                "source": self.source,
                "kind": self.kind,
                "label": self.label,
                "progress_pct": self.progress_pct,
                "progress_message": self.progress_message,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "metadata": dict(self.metadata),
            }


class SharedJobQueue:
    """Aggregate scan queue tasks and external background jobs."""

    def __init__(self, scan_queue: ScanQueue) -> None:
        self._scan_queue = scan_queue
        self._jobs: dict[str, ExternalJob] = {}
        self._queue: deque[str] = deque()
        self._active_job_id: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="webui-shared-job-monitor",
            daemon=True,
        )
        self._monitor.start()

    def submit_external(
        self,
        *,
        source: str,
        kind: str,
        label: str,
        runner: RunnerFn,
        metadata: Optional[dict] = None,
        cancel_callback: Optional[CancelFn] = None,
    ) -> ExternalJob:
        job = ExternalJob(
            job_id=f"xjob-{secrets.token_hex(8)}",
            source=str(source or "external"),
            kind=str(kind or "run"),
            label=str(label or "background job"),
            runner=runner,
            cancel_callback=cancel_callback,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
        self._maybe_start_next()
        return job

    def queue_status(self) -> dict:
        self._maybe_start_next()
        scan_snapshot = self._scan_queue.queue_status()

        with self._lock:
            external_active = self._jobs.get(self._active_job_id) if self._active_job_id else None
            external_queued = [
                self._jobs[job_id]
                for job_id in list(self._queue)
                if job_id in self._jobs
            ]

        active_job: Optional[dict] = None
        queued_jobs: list[dict] = []

        scan_active = scan_snapshot.get("active") if isinstance(scan_snapshot, dict) else None
        scan_queued = scan_snapshot.get("queued") if isinstance(scan_snapshot, dict) else []

        if isinstance(scan_active, dict) and scan_active.get("task_id"):
            active_job = self._scan_task_to_job(scan_active)
            queued_jobs.extend(self._scan_tasks_to_jobs(scan_queued))
            if external_active is not None:
                queued_jobs.append(external_active.to_dict())
        elif external_active is not None:
            active_job = external_active.to_dict()
            queued_jobs.extend(self._scan_tasks_to_jobs(scan_queued))
        else:
            queued_jobs.extend(self._scan_tasks_to_jobs(scan_queued))

        queued_jobs.extend(job.to_dict() for job in external_queued)
        return {"active": active_job, "queued": queued_jobs}

    def get_job(self, job_id: str) -> Optional[dict]:
        scan_task = self._scan_queue.get_task(job_id)
        if scan_task is not None:
            return self._scan_task_to_job(scan_task.to_dict())

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.to_dict()

    def cancel(self, job_id: str) -> SharedCancelResult:
        scan_task = self._scan_queue.get_task(job_id)
        if scan_task is not None:
            mapped = self._scan_queue.cancel(job_id)
            if mapped == CancelResult.OK:
                return SharedCancelResult.OK
            if mapped == CancelResult.TERMINAL:
                return SharedCancelResult.TERMINAL
            return SharedCancelResult.NOT_FOUND

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return SharedCancelResult.NOT_FOUND

            if job.status in {
                SharedJobStatus.DONE,
                SharedJobStatus.FAILED,
                SharedJobStatus.CANCELLED,
            }:
                return SharedCancelResult.TERMINAL

            if job.status == SharedJobStatus.QUEUED:
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job.mark_cancelled()
                return SharedCancelResult.OK

            job._cancel_event.set()
            callback = job.cancel_callback

        if callable(callback):
            try:
                callback()
            except Exception:
                logger.exception("shared external cancel callback failed: job_id=%s", job_id)
        return SharedCancelResult.OK

    def shutdown(self) -> None:
        self._stop_event.set()

    def _scan_task_to_job(self, task: dict) -> dict:
        protocol = str(task.get("protocol") or "").lower()
        label = f"{protocol.upper()} scan" if protocol else "Shodan scan"
        return {
            "job_id": task.get("task_id"),
            "status": task.get("status"),
            "source": "shodan",
            "kind": "run",
            "label": label,
            "progress_pct": task.get("progress_pct", 0.0),
            "progress_message": task.get("progress_message", ""),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "error": None,
            "metadata": {
                "protocol": protocol,
                "countries": task.get("countries") or [],
            },
        }

    def _scan_tasks_to_jobs(self, tasks: object) -> list[dict]:
        rows: list[dict] = []
        if not isinstance(tasks, list):
            return rows
        for task in tasks:
            if isinstance(task, dict) and task.get("task_id"):
                rows.append(self._scan_task_to_job(task))
        return rows

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(0.5):
            self._maybe_start_next()

    def _maybe_start_next(self) -> None:
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status == SharedJobStatus.RUNNING:
                    return
                self._active_job_id = None

            if not self._queue:
                return

            next_id = self._queue[0]
            job = self._jobs.get(next_id)
            if job is None:
                self._queue.popleft()
                return

            scan_active = self._scan_queue.queue_status().get("active")
            if scan_active is not None:
                return

            self._queue.popleft()
            self._active_job_id = next_id
            with job._lock:
                job.status = SharedJobStatus.RUNNING
                job.started_at = time.time()

        threading.Thread(
            target=self._run_external_job,
            args=(job,),
            name=f"webui-{job.source}-{job.kind}-{job.job_id[:8]}",
            daemon=True,
        ).start()

    def _run_external_job(self, job: ExternalJob) -> None:
        try:
            if job.is_cancel_requested():
                job.mark_cancelled()
                return
            job.runner(job)
            if job.is_cancel_requested():
                job.mark_cancelled()
            elif job.status == SharedJobStatus.RUNNING:
                job.mark_done()
        except Exception as exc:
            logger.exception("shared external job failed: job_id=%s", job.job_id)
            if job.is_cancel_requested():
                job.mark_cancelled()
            else:
                job.mark_failed(str(exc))
        finally:
            with self._lock:
                if self._active_job_id == job.job_id:
                    self._active_job_id = None
            self._maybe_start_next()
