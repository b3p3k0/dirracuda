"""Deliver batch results on the UI thread, retaining errors after window close."""

from concurrent.futures import CancelledError
from functools import partial
import sqlite3
import threading

from gui.utils.logging_config import get_logger
from gui.utils.ui_dispatcher import UIDispatcher

_logger = get_logger("batch")


class BatchDelivery:
    def __init__(self, window, on_done):
        self.dispatcher = UIDispatcher(window)
        self.on_done = on_done
        self.failed = False
        self._pending = set()
        self._lock = threading.Lock()
        window.bind("<Destroy>", self._on_destroy, add="+")
        self.window = window

    def _on_destroy(self, event):
        if event.widget is self.window:
            self.dispatcher.stop()

    def schedule(self, callback, *args, **kwargs):
        self.dispatcher.schedule(callback, *args, **kwargs)

    def watch(self, job_id, target, future):
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(partial(self.completed, job_id, target))

    def pending_futures(self):
        with self._lock:
            return list(self._pending)

    def completed(self, job_id, target, future):
        """Runs on executor threads, including after Tk has been destroyed."""
        try:
            result = future.result()
        except CancelledError:
            pass
        except Exception:
            self.failed = True
            _logger.exception("Batch task failed for %s; details follow", target.get("ip_address"))
        else:
            if result.get("status") == "failed":
                self.failed = True
                exc = result.get("_exception")
                kind = ("Database issue" if isinstance(exc, sqlite3.Error) else
                        "Network issue" if isinstance(exc, (ConnectionError, TimeoutError)) else
                        "Batch task failed")
                _logger.error(
                    "%s for %s: %s", kind, target.get("ip_address"),
                    result.get("notes") or "Unknown error",
                    exc_info=(type(exc), exc, exc.__traceback__) if exc else None,
                )
        self.schedule(self.on_done, job_id, target, future)
        with self._lock:
            self._pending.discard(future)
