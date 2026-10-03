"""Focused GUI tests for structured Web UI daemon control."""

from __future__ import annotations

import types

import tkinter as tk

from gui.components.experimental_features.webui_tab import WebUITab


class _Frame:
    def winfo_exists(self):
        return True


class _Var:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class _Button:
    def __init__(self):
        self.state = None

    def configure(self, **kwargs):
        self.state = kwargs.get("state")


def _tab():
    tab = WebUITab.__new__(WebUITab)
    tab.frame = _Frame()
    tab._status_var = _Var()
    tab._backend_var = _Var()
    tab._start_btn = _Button()
    tab._stop_btn = _Button()
    tab._browser_btn = _Button()
    return tab


def test_structured_status_shows_systemd_backend():
    tab = _tab()
    status = types.SimpleNamespace(
        running=True,
        state="running",
        backend="systemd",
        reason="",
    )

    tab._apply_service_status(status)

    assert tab._backend_var.value == "systemd"
    assert tab._status_var.value == "Running"
    assert tab._start_btn.state == tk.DISABLED
    assert tab._stop_btn.state == tk.NORMAL


def test_structured_ambiguous_status_stays_actionable():
    tab = _tab()
    status = types.SimpleNamespace(
        running=False,
        state="ambiguous",
        backend="direct",
        reason="process ownership could not be verified",
    )

    tab._apply_service_status(status)

    assert tab._backend_var.value == "direct"
    assert "ownership" in tab._status_var.value
    assert tab._start_btn.state == tk.NORMAL


def test_apply_status_pushes_running_state_to_dashboard():
    tab = _tab()
    pushed = []
    tab._context = {"on_webui_status_changed": pushed.append}

    tab._apply_status(True)
    tab._apply_status(False)

    assert pushed == [True, False]


def test_failed_status_pushes_idle_to_dashboard():
    tab = _tab()
    pushed = []
    tab._context = {"on_webui_status_changed": pushed.append}

    tab._apply_failed_status("port in use")

    assert pushed == [False]
    assert tab._status_var.value == "Failed: port in use"


def test_dashboard_callback_error_does_not_break_tab():
    tab = _tab()

    def _boom(_running):
        raise RuntimeError("dashboard gone")

    tab._context = {"on_webui_status_changed": _boom}

    tab._apply_status(True)

    assert tab._status_var.value == "Running"
    assert tab._stop_btn.state == tk.NORMAL
