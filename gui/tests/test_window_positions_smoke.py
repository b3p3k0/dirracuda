"""Opt-in persistence tests on an isolated X11 display with a window manager."""

import os
import subprocess
import time
import tkinter as tk

import pytest

from gui.utils.settings_manager import SettingsManager
from gui.utils.window_positions import remember_window_position

pytestmark = [pytest.mark.gui_smoke, pytest.mark.skipif(
    os.environ.get("DIRRACUDA_FOCUS_TESTS") != "1",
    reason="Requires explicitly enabled isolated display with a window manager",
)]


def settle(root):
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)


def test_drag_close_and_restart_keeps_position_without_decoration_drift(tmp_path):
    assert "window id # 0x" in subprocess.check_output(
        ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], text=True,
    )
    for cycle in range(3):
        # New Tk interpreter and settings instance model a fresh application run.
        settings = SettingsManager(str(tmp_path))
        root = tk.Tk()
        root.geometry("400x220+10+10")
        remember_window_position(root, "main_window", settings)
        settle(root)
        if cycle == 0:
            root.geometry("+340+240")
            settle(root)
        try:
            assert root.geometry() == "400x220+340+240"
        finally:
            root.destroy()  # Must flush even before the debounce timer expires.
        assert settings.get_setting("windows.main_window.position") == "+340+240"


def test_hide_persists_but_never_shows_or_focuses_window(tmp_path):
    root = tk.Tk()
    root.geometry("500x250+20+20")
    settings = SettingsManager(str(tmp_path))
    entry = tk.Entry(root)
    entry.pack()
    window = tk.Toplevel(root)
    window.geometry("300x200+500+300")
    remember_window_position(window, "scan_output", settings)
    settle(root)
    entry.focus_force()
    settle(root)
    try:
        window.withdraw()
        settle(root)
        assert window.state() == "withdrawn"
        assert root.focus_get() == entry
        assert settings.get_setting("windows.scan_output.position") == "+500+300"
        next_window = tk.Toplevel(root)
        next_window.withdraw()
        next_window.geometry("600x350")
        remember_window_position(next_window, "scan_output", SettingsManager(str(tmp_path)))
        next_window.update_idletasks()
        assert next_window.geometry().endswith("+500+300")
        assert next_window.state() == "withdrawn"
        assert root.focus_get() == entry
    finally:
        root.destroy()
