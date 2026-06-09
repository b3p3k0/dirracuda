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
