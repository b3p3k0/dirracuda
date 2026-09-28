"""Opt-in real Tk/X11 checks; run on an isolated X display with a WM.

DIRRACUDA_FOCUS_TESTS=1 DISPLAY=:91 ./venv/bin/python -m pytest -q <this file>
Never enable this against the user's desktop: the test deliberately sets focus.
"""

import os
import subprocess
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from gui.components.scan_results_dialog import ScanResultsDialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog
from gui.components.clamav_results_dialog import show_clamav_results_dialog
from gui.components.dashboard_scan_output_dialog import show_scan_output_dialog, reopen_scan_output_dialog
from gui.utils.background_windows import show_background_window
from gui.utils.style import get_theme


pytestmark = [pytest.mark.gui_smoke, pytest.mark.skipif(
    os.environ.get("DIRRACUDA_FOCUS_TESTS") != "1",
    reason="Requires explicitly enabled isolated X11 display with a window manager",
)]


def settle(root):
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)


@pytest.fixture
def desktop():
    wm = subprocess.check_output(
        ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], text=True,
    )
    assert "window id # 0x" in wm, "Start an EWMH window manager on the isolated display"
    root = tk.Tk()
    root.title("Dirracuda focus test")
    root.geometry("400x200+20+20")
    entry = tk.Entry(root)
    entry.pack()
    settle(root)
    entry.focus_force()
    settle(root)
    yield root, entry
    root.destroy()


@pytest.mark.parametrize("kind", ["scan", "probe", "extract", "clamav"])
def test_results_preserve_typing_and_do_not_block(desktop, kind):
    root, entry = desktop
    if kind == "scan":
        result = ScanResultsDialog(root, {"status": "completed", "hosts_scanned": 1})
        result.show()
        window = result.dialog
    elif kind == "clamav":
        window = show_clamav_results_dialog(
            parent=root, theme=None, results=[], on_mute=lambda: None, wait=False, modal=False,
        )
    else:
        window = show_batch_summary_dialog(parent=root, theme=None, results=[], job_type=kind)
    settle(root)
    assert window.winfo_viewable()
    assert root.focus_get() == entry
    assert root.grab_current() is None
    assert window.transient() == ""
    # Enter while still typing in the original window must not dismiss results.
    entry.event_generate("<Return>")
    settle(root)
    assert window.winfo_exists()
    window.destroy()


def test_new_background_window_does_not_activate_over_another_app(desktop):
    root, _entry = desktop
    other = tk.Tk()  # Separate Tcl application, like an unrelated editor.
    other.title("Other application")
    entry = tk.Entry(other)
    entry.pack()
    settle(other)
    entry.focus_force()
    settle(other)
    try:
        window = tk.Toplevel(root)
        window.withdraw()
        tk.Label(window, text="Background work finished").pack()
        show_background_window(window)
        settle(root)
        assert other.focus_get() == entry
        assert window.winfo_viewable()
        window.destroy()
    finally:
        other.destroy()


def test_console_hide_stage_transition_and_explicit_reopen(desktop):
    root, entry = desktop
    dash = SimpleNamespace(
        parent=root, theme=get_theme(), log_bg_color="black", log_fg_color="white",
        _copy_log_output=lambda: None, _scroll_log_to_latest=lambda: None,
        _update_log_autoscroll_state=lambda *_: None, _configure_log_tags=lambda: None,
        _render_log_placeholder=lambda: None,
    )
    show_scan_output_dialog(dash, protocol="SMB", country="US")
    settle(root)
    assert root.focus_get() == entry
    dash.scan_output_dialog.withdraw()
    for provider in ("FTP", "HTTP", "REDDIT", "SEARXNG"):
        show_scan_output_dialog(dash, protocol=provider, country="US")
        settle(root)
        assert dash.scan_output_dialog.state() == "withdrawn"
        assert root.focus_get() == entry
    reopen_scan_output_dialog(dash)
    settle(root)
    assert root.focus_get().winfo_toplevel() == dash.scan_output_dialog
