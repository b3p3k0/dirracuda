"""
Thread-safe dispatcher for Tkinter UI updates.

Tkinter is NOT thread-safe — all widget mutations MUST occur on the main
thread. This dispatcher provides the required path: worker threads call
schedule(), and updates execute on the main thread via root.after().

Usage:
    dispatcher = UIDispatcher(root)
    # From any thread:
    dispatcher.schedule(my_widget.config, text="Updated")
    # On shutdown (before root.destroy()):
    dispatcher.stop()
"""

from __future__ import annotations

import queue
import tkinter as tk
from typing import Any, Callable


class UIDispatcher:
    """
    Queue-based dispatcher that marshals callbacks to the Tk main thread.

    All UI mutations from worker threads should go through schedule().
    The dispatcher polls a queue and executes callbacks on the main thread
    via root.after(), ensuring thread safety.

    Attributes:
        MAX_ITEMS_PER_POLL: Cap on callbacks processed per tick to avoid
            blocking the main loop during bursts.
        POLL_INTERVAL_MS: Milliseconds between queue checks.
    """

    MAX_ITEMS_PER_POLL = 20
    POLL_INTERVAL_MS = 50

    def __init__(self, root: tk.Tk) -> None:
        """
        Initialize the dispatcher.

        Args:
            root: The Tk root window. Must remain valid until stop() is called.
        """
        self._root = root
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._after_id: str | None = None
        self._start_polling()

    def schedule(self, callback: Callable, *args: Any, **kwargs: Any) -> None:
        """
        Queue a callback to run on the main thread.

        Safe to call from any thread. Does nothing if dispatcher is stopped
        or root is destroyed.

        Args:
            callback: Function to call on the main thread.
            *args: Positional arguments for callback.
            **kwargs: Keyword arguments for callback.
        """
        if not self._running:
            return
        try:
            if not self._root.winfo_exists():
                return
        except (tk.TclError, RuntimeError):
            return
        self._queue.put((callback, args, kwargs))

    def _start_polling(self) -> None:
        """Begin the polling loop."""
        self._poll()

    def _poll(self) -> None:
        """Process queued callbacks on main thread."""
        if not self._running:
            return

        # Guard against root destruction
        try:
            if not self._root.winfo_exists():
                self._running = False
                return
        except (tk.TclError, RuntimeError):
            self._running = False
            return

        # Process up to MAX_ITEMS_PER_POLL to avoid blocking
        processed = 0
        while processed < self.MAX_ITEMS_PER_POLL:
            try:
                callback, args, kwargs = self._queue.get_nowait()
                try:
                    callback(*args, **kwargs)
                except tk.TclError:
                    # Widget may have been destroyed; ignore
                    pass
                except Exception:
                    # Don't let one bad callback kill the dispatcher
                    pass
                processed += 1
            except queue.Empty:
                break

        # Schedule next poll
        if self._running:
            try:
                self._after_id = self._root.after(self.POLL_INTERVAL_MS, self._poll)
            except tk.TclError:
                self._running = False

    def stop(self) -> None:
        """
        Stop the dispatcher. Call before destroying root.

        Cancels pending after() and drains queue to prevent TclError on shutdown.
        """
        self._running = False

        # Cancel pending after callback
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

        # Drain queue (discard remaining items)
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
