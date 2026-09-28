"""Remember per-window positions without changing size, focus, or monitor layout."""

import logging
import re
import tkinter as tk

from gui.utils.settings_manager import get_settings_manager

_POSITION = re.compile(r"[+-]-?\d{1,9}[+-]-?\d{1,9}", re.ASCII)
_GEOMETRY = re.compile(r"\d+x\d+([+-]-?\d{1,9}[+-]-?\d{1,9})", re.ASCII)
_logger = logging.getLogger(__name__)


def remember_window_position(window, name, settings_manager=None):
    """Restore after default placement, before display; save settled moves.

    Position is Tk's geometry suffix, retaining its left/right and top/bottom
    anchoring, including signed offsets. Never clamp it to the screen rectangle:
    that rectangle can contain gaps between physical monitors.
    """
    controller = _WindowPosition(window, name, settings_manager)
    # Retain the controller for the lifetime of this window and its callbacks.
    window._position_memory = controller
    return controller


class _WindowPosition:
    def __init__(self, window, name, settings_manager):
        self.window = window
        self.name = name
        self.settings = settings_manager if settings_manager is not None else get_settings_manager()
        self.timer = None
        self.pending = None
        self.saved = None
        value = self.settings.get_setting(f"windows.{name}.position", None)
        if isinstance(value, str) and _POSITION.fullmatch(value):
            try:
                window.geometry(value)
                self.saved = value
            except tk.TclError:
                _logger.warning("Ignoring invalid saved position for %s", name)
        window.bind("<Configure>", self._moved, add="+")
        window.bind("<Unmap>", self._flush_event, add="+")
        window.bind("<Destroy>", self._flush_event, add="+")

    def _moved(self, event):
        if event.widget is not self.window:
            return
        try:
            if self.window.state() != "normal":
                return  # Do not replace the user's position with minimized/zoomed geometry.
            try:
                if self.window.attributes("-zoomed"):
                    return  # X11 reports maximized windows as state "normal".
            except tk.TclError:
                pass  # Other Tk backends use state "zoomed" instead.
            geometry = self.window.geometry()
            if geometry.startswith("1x1"):
                return  # Ignore Tk's provisional, not-yet-laid-out window.
            match = _GEOMETRY.fullmatch(geometry)
            if not match:
                return
            position = match.group(1)
            if position == self.pending or (self.pending is None and position == self.saved):
                return
            self.pending = position
            self._cancel_timer()
            self.timer = self.window.after(400, self.flush)
        except tk.TclError:
            pass  # The window may be tearing down.

    def _cancel_timer(self):
        if self.timer is not None:
            try:
                self.window.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None

    def _flush_event(self, event):
        if event.widget is self.window:
            self.flush()

    def flush(self):
        """Use the last normal Configure snapshot; never query destroyed widgets."""
        self._cancel_timer()
        if self.pending is None or self.pending == self.saved:
            return
        try:
            if self.settings.set_setting(f"windows.{self.name}.position", self.pending) is not False:
                self.saved = self.pending
                self.pending = None
        except Exception:
            _logger.warning("Could not save window position for %s", self.name, exc_info=True)
