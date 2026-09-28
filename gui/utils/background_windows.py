"""Map passive monitor/result windows without requesting keyboard activation.

Call withdraw() immediately after constructing the Toplevel, then this helper
after building it. Explicit user opens/reopens should use their normal focus path.
"""

import ctypes
from ctypes.util import find_library
from functools import lru_cache
import logging
import tkinter as tk


@lru_cache(maxsize=1)
def _x11():
    """Load Tk's existing X11 system dependency; no additional Python package."""
    name = find_library("X11")
    if not name:
        raise OSError("X11 library unavailable")
    lib = ctypes.CDLL(name)
    ulong = ctypes.c_ulong
    pointer = ctypes.c_void_p
    signatures = {
        "XOpenDisplay": ([ctypes.c_char_p], pointer),
        "XCloseDisplay": ([pointer], ctypes.c_int),
        "XInternAtom": ([pointer, ctypes.c_char_p, ctypes.c_int], ulong),
        "XQueryTree": ([pointer, ulong, ctypes.POINTER(ulong), ctypes.POINTER(ulong),
                        ctypes.POINTER(ctypes.POINTER(ulong)), ctypes.POINTER(ctypes.c_uint)], ctypes.c_int),
        "XChangeProperty": ([pointer, ulong, ulong, ulong, ctypes.c_int, ctypes.c_int,
                             pointer, ctypes.c_int], ctypes.c_int),
        "XFree": ([pointer], ctypes.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes = args
        function.restype = result
    return lib


def _set_x11_no_activation(window):
    """Set EWMH user time to zero on Tk's WM wrapper before its first map."""
    lib = _x11()
    display = lib.XOpenDisplay(window.winfo_screen().encode())
    if not display:
        raise OSError("Cannot open window's X display")
    try:
        root, wrapper = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        # Tk's widget ID is inside a wrapper; the WM reads wrapper properties.
        found = lib.XQueryTree(display, window.winfo_id(), ctypes.byref(root),
                              ctypes.byref(wrapper), ctypes.byref(children), ctypes.byref(count))
        if children:
            lib.XFree(children)
        if not found or not wrapper.value or wrapper.value == root.value:
            raise OSError("Tk window-manager wrapper unavailable")
        atom = lib.XInternAtom(display, b"_NET_WM_USER_TIME", 0)
        cardinal = lib.XInternAtom(display, b"CARDINAL", 0)
        timestamp = ctypes.c_ulong(0)
        lib.XChangeProperty(display, wrapper.value, atom, cardinal, 32, 0,
                            ctypes.byref(timestamp), 1)
    finally:
        # Closing flushes the hint before Tk sends the subsequent map request.
        lib.XCloseDisplay(display)


def show_background_window(window):
    """Initially show a passive window without raising, grabbing, or focusing it.

    EWMH-compliant X11 desktops honor the no-activation hint. Other Tk backends
    retain their normal window-manager policy; never steal focus back afterward.
    Do not call this on updates: a user-hidden window must stay hidden.
    """
    # Passive windows must not stay stacked above a parent the user is editing.
    # Tk ownership still destroys them when their parent is destroyed.
    window.transient("")
    window.update_idletasks()
    if window.tk.call("tk", "windowingsystem") == "x11":
        try:
            _set_x11_no_activation(window)
        except (OSError, AttributeError, tk.TclError):
            logging.getLogger(__name__).warning(
                "Could not set background window activation hint", exc_info=True,
            )
    window.deiconify()
