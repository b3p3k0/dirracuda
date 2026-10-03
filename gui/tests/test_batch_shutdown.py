"""Batch completion must never call Tk from an executor thread."""

from concurrent.futures import Future
from types import SimpleNamespace
import threading
import sqlite3

import pytest

from gui.components.server_list_window.actions import batch


class ThreadCheckedWindow:
    def __init__(self):
        self.owner = threading.get_ident()
        self.pending = {}
        self.destroy_callback = None
        self.alive = True

    def check_thread(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("main thread is not in main loop")

    def winfo_exists(self):
        self.check_thread()
        return self.alive

    def after(self, delay, callback, *args):
        self.check_thread()
        token = str(len(self.pending))
        self.pending[token] = (callback, args)
        return token

    def after_cancel(self, token):
        self.check_thread()
        self.pending.pop(token, None)

    def bind(self, event, callback, add=None):
        self.destroy_callback = callback

    def tick(self):
        pending, self.pending = self.pending, {}
        for callback, args in pending.values():
            callback(*args)

    def destroy(self):
        self.alive = False
        if self.destroy_callback:
            self.destroy_callback(SimpleNamespace(widget=self))


class ControlledExecutor:
    def __init__(self, **kwargs):
        self.future = Future()

    def submit(self, *args):
        return self.future

    def shutdown(self, **kwargs):
        pass


class BatchHarness(batch.ServerListWindowBatchMixin):
    def __init__(self):
        self.window = ThreadCheckedWindow()
        self.active_jobs = {}
        self.delivered = []

    def _set_status(self, *args):
        pass

    def _update_action_buttons_state(self):
        pass

    def _init_batch_status_dialog(self, *args, **kwargs):
        return None

    def _register_batch_running_task(self, job_id):
        pass

    def _on_batch_future_done(self, *args):
        self.window.check_thread()
        self.delivered.append(args)


def test_completion_from_worker_never_calls_tk(monkeypatch, caplog):
    monkeypatch.setattr(batch, "ThreadPoolExecutor", ControlledExecutor)
    owner = BatchHarness()
    owner._start_batch_job("probe", [{"ip_address": "192.0.2.1"}], {})
    job = next(iter(owner.active_jobs.values()))
    worker = threading.Thread(target=job["executor"].future.set_result,
                              args=({"status": "success"},))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert "exception calling callback" not in caplog.text
    owner.window.tick()
    assert len(owner.delivered) == 1


@pytest.mark.parametrize("outcome", ["success", "cancelled", "database", "exception"])
def test_late_completion_after_destroy_preserves_only_real_errors(monkeypatch, caplog, outcome):
    monkeypatch.setattr(batch, "ThreadPoolExecutor", ControlledExecutor)
    owner = BatchHarness()
    owner._start_batch_job("probe", [{"ip_address": "192.0.2.1"}], {})
    future = next(iter(owner.active_jobs.values()))["executor"].future
    owner.window.destroy()

    def complete():
        if outcome == "cancelled":
            future.cancel()
        elif outcome == "exception":
            future.set_exception(ValueError("unexpected worker failure"))
        elif outcome == "database":
            try:
                raise sqlite3.OperationalError("database is locked")
            except sqlite3.Error as exc:
                future.set_result({"status": "failed", "notes": str(exc), "_exception": exc})
        else:
            future.set_result({"status": "success"})

    worker = threading.Thread(target=complete)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert not owner.delivered
    assert not owner.window.pending
    assert not owner._batch_delivery.pending_futures()
    assert "exception calling callback" not in caplog.text
    if outcome in {"success", "cancelled"}:
        assert not caplog.records
    else:
        assert owner._batch_delivery.failed
        assert any(record.exc_info for record in caplog.records)
        assert ("Database issue" if outcome == "database" else "unexpected worker failure") in caplog.text


def test_dispatcher_retains_callback_errors_and_keeps_processing(caplog):
    from gui.utils.ui_dispatcher import UIDispatcher
    window = ThreadCheckedWindow()
    dispatcher = UIDispatcher(window)
    received = []

    def fail():
        raise ValueError("real UI bug")

    dispatcher.schedule(fail)
    dispatcher.schedule(received.append, "next update")
    window.tick()
    assert received == ["next update"]
    assert "real UI bug" in caplog.text
    assert caplog.records[0].exc_info
    dispatcher.stop()


def test_database_write_failure_is_a_failed_batch_result(monkeypatch, caplog):
    from unittest.mock import Mock
    owner = BatchHarness()
    owner.indicator_patterns = None
    owner.all_servers = []
    owner.settings_manager = None
    owner.db_reader = Mock()
    owner.db_reader.upsert_probe_snapshot_for_host.side_effect = sqlite3.OperationalError("disk full")
    owner._batch_delivery = SimpleNamespace(schedule=lambda *a, **kw: None)
    monkeypatch.setattr(batch, "dispatch_probe_run", lambda *a, **kw: {})
    monkeypatch.setattr(batch.probe_patterns, "attach_indicator_analysis", lambda *a: {})
    monkeypatch.setattr(batch, "summarize_probe_snapshot", lambda *a: {"display_entries": []})
    result = owner._run_batch_task("probe-1", "probe", {"host_type": "F", "ip_address": "192.0.2.1"}, {}, threading.Event())
    assert result["status"] == "failed"
    assert isinstance(result["_exception"], sqlite3.Error)


def test_smb_cancellation_after_probe_is_not_reported_as_failure(monkeypatch):
    owner = BatchHarness()
    owner.db_reader = None
    event = threading.Event()

    def probe(*args, **kwargs):
        event.set()
        return {}

    monkeypatch.setattr(batch, "dispatch_probe_run", probe)
    result = owner._run_batch_task("probe-1", "probe", {"host_type": "S", "ip_address": "192.0.2.1"}, {}, event)
    assert result["status"] == "cancelled"


@pytest.mark.gui_smoke
def test_real_tk_worker_finishes_after_mainloop_exits(caplog):
    import os
    import tkinter as tk
    from concurrent.futures import ThreadPoolExecutor
    from gui.utils.batch_delivery import BatchDelivery

    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires a display; run with xvfb-run -a")
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    delivery = BatchDelivery(window, lambda *a: pytest.fail("UI callback after close"))
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        assert release.wait(timeout=5)
        return {"status": "success"}

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            delivery.watch("probe-1", {"ip_address": "192.0.2.1"}, future)
            assert started.wait(timeout=2)
            root.after(0, root.destroy)
            root.mainloop()
            release.set()
    finally:
        release.set()
    assert not delivery.pending_futures()
    assert not caplog.records
