"""
Safe messagebox wrapper for consistent parenting and focus behavior.

This module centralizes all Tk messagebox calls so modal dialogs are properly
parented and raised above their owner windows.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as _tk_messagebox
from typing import Any, Optional


_CONSTANTS = (
    "ABORT",
    "ABORTRETRYIGNORE",
    "CANCEL",
    "ERROR",
    "IGNORE",
    "INFO",
    "NO",
    "OK",
    "OKCANCEL",
    "QUESTION",
    "RETRY",
    "RETRYCANCEL",
    "WARNING",
    "YES",
    "YESNO",
    "YESNOCANCEL",
)

for _name in _CONSTANTS:
    if hasattr(_tk_messagebox, _name):
        globals()[_name] = getattr(_tk_messagebox, _name)


def _widget_exists(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        return bool(int(widget.winfo_exists()) == 1)
    except Exception:
        return False


def _as_toplevel(widget: Any) -> Optional[tk.Widget]:
    if not _widget_exists(widget):
        return None
    try:
        top = widget.winfo_toplevel()
        if _widget_exists(top):
            return top
    except Exception:
        pass
    return widget if _widget_exists(widget) else None


def _resolve_parent(explicit_parent: Any = None) -> Optional[tk.Widget]:
    explicit = _as_toplevel(explicit_parent)
    if explicit is not None:
        return explicit

    root = getattr(tk, "_default_root", None)
    if not _widget_exists(root):
        return None

    try:
        grab = root.grab_current()
        grabbed = _as_toplevel(grab)
        if grabbed is not None:
            return grabbed
    except Exception:
        pass

    try:
        focused = _as_toplevel(root.focus_get())
        if focused is not None:
            return focused
    except Exception:
        pass

    return _as_toplevel(root)


def _pulse_topmost(window: tk.Widget) -> None:
    try:
        window.attributes("-topmost", True)
        window.attributes("-topmost", False)
    except Exception:
        pass


def _prepare_parent(parent: tk.Widget) -> None:
    if not _widget_exists(parent):
        return
    try:
        parent.update_idletasks()
    except Exception:
        pass
    try:
        parent.lift()
    except Exception:
        pass
    try:
        parent.focus_force()
    except Exception:
        pass
    _pulse_topmost(parent)


def _restore_parent(parent: tk.Widget) -> None:
    if not _widget_exists(parent):
        return
    try:
        parent.update_idletasks()
    except Exception:
        pass
    try:
        parent.lift()
    except Exception:
        pass
    try:
        parent.focus_force()
    except Exception:
        pass


def _call(kind: str, *args: Any, **kwargs: Any) -> Any:
    parent = _resolve_parent(kwargs.get("parent"))
    if parent is not None:
        kwargs["parent"] = parent
        _prepare_parent(parent)

    fn = getattr(_tk_messagebox, kind)
    try:
        return fn(*args, **kwargs)
    finally:
        if parent is not None:
            _restore_parent(parent)


def showinfo(*args: Any, **kwargs: Any) -> Any:
    return _call("showinfo", *args, **kwargs)


def showwarning(*args: Any, **kwargs: Any) -> Any:
    return _call("showwarning", *args, **kwargs)


def showerror(*args: Any, **kwargs: Any) -> Any:
    return _call("showerror", *args, **kwargs)


def askquestion(*args: Any, **kwargs: Any) -> Any:
    return _call("askquestion", *args, **kwargs)


def askokcancel(*args: Any, **kwargs: Any) -> Any:
    return _call("askokcancel", *args, **kwargs)


def askyesno(*args: Any, **kwargs: Any) -> Any:
    return _call("askyesno", *args, **kwargs)


def askyesnocancel(*args: Any, **kwargs: Any) -> Any:
    return _call("askyesnocancel", *args, **kwargs)


def askretrycancel(*args: Any, **kwargs: Any) -> Any:
    return _call("askretrycancel", *args, **kwargs)


__all__ = [
    "showinfo",
    "showwarning",
    "showerror",
    "askquestion",
    "askokcancel",
    "askyesno",
    "askyesnocancel",
    "askretrycancel",
] + [name for name in _CONSTANTS if name in globals()]
