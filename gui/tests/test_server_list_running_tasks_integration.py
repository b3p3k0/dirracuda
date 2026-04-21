"""Server-list batch integration tests for shared Running Tasks registry."""

from __future__ import annotations

from concurrent.futures import Future
import threading
import tkinter as tk

from gui.components.server_list_window.actions.batch_status import ServerListWindowBatchStatusMixin
from gui.utils.running_tasks import (
    _reset_running_task_registry_for_tests,
    get_running_task_registry,
)


def setup_function():
    _reset_running_task_registry_for_tests()


class _FakeExecutor:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, wait=False, cancel_futures=True):
        self.shutdown_calls.append((wait, cancel_futures))


class _FakeDialog:
    def __init__(self):
        self.hidden = False
        self.destroy_calls = 0
        self.finished_calls = []

    def winfo_exists(self):
        return True

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def mark_finished(self, status, notes):
        self.finished_calls.append((status, notes))

    def destroy(self):
        self.destroy_calls += 1


class _WrappedDialogWindow:
    def winfo_exists(self):
        return True


class _WrappedDialog:
    def __init__(self):
        self.window = _WrappedDialogWindow()
        self.show_calls = 0

    def show(self):
        self.show_calls += 1


class _FakeButton:
    def __init__(self):
        self._state = tk.DISABLED
        self._text = "Running Tasks (0)"

    def winfo_exists(self):
        return True

    def configure(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs["state"]
        if "text" in kwargs:
            self._text = kwargs["text"]


class _BatchStatusStub(ServerListWindowBatchStatusMixin):
    def __init__(self):
        self.active_jobs = {}
        self.batch_status_dialog = None
        self.window = type("_Win", (), {"winfo_exists": lambda _self: True})()

    def _update_action_buttons_state(self):
        return None

    def _set_status(self, _message: str):
        return None

    def _flush_pending_refresh(self):
        return None

    def _set_table_interaction_enabled(self, _enabled: bool):
        return None

    def _show_batch_summary(self, _job_type, _results):
        return None

    def _maybe_show_clamav_dialog(self, _results, _clamav_cfg, **_kwargs):
        return None

    def _update_stop_button_style(self, _batch_active: bool):
        return None


def test_server_list_batch_task_survives_hide_and_removes_on_finalize():
    stub = _BatchStatusStub()
    dialog = _FakeDialog()
    job_id = "probe-1"
    stub.active_jobs[job_id] = {
        "id": job_id,
        "type": "probe",
        "targets": [{"ip_address": "198.51.100.10"}],
        "options": {},
        "executor": _FakeExecutor(),
        "cancel_event": threading.Event(),
        "results": [{"status": "success", "notes": "ok"}],
        "completed": 0,
        "total": 1,
        "unit_label": "targets",
        "futures": [],
        "dialog": dialog,
    }

    stub._register_batch_running_task(job_id)
    registry = get_running_task_registry()
    assert registry.count() == 1

    dialog.hide()
    assert dialog.hidden is True
    assert registry.count() == 1

    stub.active_jobs[job_id]["completed"] = 1
    stub._finalize_batch_job(job_id, dialog, show_summary=False)
    assert registry.count() == 0


def test_server_list_batch_task_progress_updates_from_future_completion():
    stub = _BatchStatusStub()
    dialog = _FakeDialog()
    job_id = "extract-1"
    stub.active_jobs[job_id] = {
        "id": job_id,
        "type": "extract",
        "targets": [{"ip_address": "203.0.113.11"}],
        "options": {},
        "executor": _FakeExecutor(),
        "cancel_event": threading.Event(),
        "results": [],
        "completed": 0,
        "total": 3,
        "unit_label": "targets",
        "futures": [],
        "dialog": dialog,
    }
    stub._register_batch_running_task(job_id)
    task_id = stub.active_jobs[job_id]["task_id"]

    future = Future()
    future.set_result(
        {
            "ip_address": "203.0.113.11",
            "action": "extract",
            "status": "success",
            "notes": "ok",
            "units": 1,
        }
    )
    stub._on_batch_future_done(job_id, {"ip_address": "203.0.113.11"}, future)

    snapshot = get_running_task_registry().get_task(task_id)
    assert snapshot is not None
    assert snapshot.progress == "1/3 targets"


def test_server_list_batch_task_reopen_supports_window_wrapped_dialogs():
    stub = _BatchStatusStub()
    dialog = _WrappedDialog()
    job_id = "probe-2"
    stub.active_jobs[job_id] = {
        "id": job_id,
        "type": "probe",
        "targets": [{"ip_address": "203.0.113.55"}],
        "options": {},
        "executor": _FakeExecutor(),
        "cancel_event": threading.Event(),
        "results": [],
        "completed": 0,
        "total": 1,
        "unit_label": "targets",
        "futures": [],
        "dialog": dialog,
    }
    stub._register_batch_running_task(job_id)
    task_id = stub.active_jobs[job_id]["task_id"]
    task = get_running_task_registry().get_task(task_id)
    assert task is not None
    assert task.reopen_callback is not None

    task.reopen_callback()

    assert dialog.show_calls == 1
    assert stub.batch_status_dialog is dialog


def test_server_list_running_tasks_button_updates_from_shared_registry():
    stub = _BatchStatusStub()
    stub.running_tasks_button = _FakeButton()
    stub._initialize_running_tasks_button()

    registry = get_running_task_registry()
    assert stub.running_tasks_button._text == "Running Tasks (0)"
    assert stub.running_tasks_button._state == tk.DISABLED

    task_id = registry.create_task(task_type="probe", name="Probe")
    assert stub.running_tasks_button._text == "Running Tasks (1)"
    assert stub.running_tasks_button._state == tk.NORMAL

    registry.remove_task(task_id)
    assert stub.running_tasks_button._text == "Running Tasks (0)"
    assert stub.running_tasks_button._state == tk.DISABLED
