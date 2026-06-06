"""Tests for C11A Cancel button in RunningTasksWindow."""
from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from gui.components.running_tasks_window import RunningTasksWindow
from gui.utils.running_tasks import RunningTaskRegistry, _reset_running_task_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_registry():
    _reset_running_task_registry_for_tests()


@pytest.fixture()
def root(request):
    try:
        r = tk.Tk()
        r.withdraw()
        yield r
        r.destroy()
    except tk.TclError:
        pytest.skip("No display available")


@pytest.fixture()
def registry():
    return RunningTaskRegistry()


def _build_window(root, registry):
    w = RunningTasksWindow(root, theme=None, registry=registry)
    w.show()
    root.update_idletasks()
    return w


class TestCancelButtonState:
    def test_cancel_btn_disabled_no_selection(self, root, registry):
        w = _build_window(root, registry)
        assert w._cancel_btn is not None
        assert str(w._cancel_btn.cget("state")) == "disabled"

    def test_cancel_btn_disabled_when_task_has_no_callback(self, root, registry):
        registry.create_task(task_type="scan", name="No Cancel",
                             reopen_callback=lambda: None, cancel_callback=None)
        w = _build_window(root, registry)
        root.update_idletasks()
        items = w.tree.get_children("")
        if items:
            w.tree.selection_set(items[0])
            w._on_selection_changed()
        assert str(w._cancel_btn.cget("state")) == "disabled"

    def test_cancel_btn_enabled_when_task_has_callback(self, root, registry):
        registry.create_task(task_type="scan", name="Has Cancel",
                             cancel_callback=lambda: None)
        w = _build_window(root, registry)
        root.update_idletasks()
        items = w.tree.get_children("")
        if items:
            w.tree.selection_set(items[0])
            w._on_selection_changed()
        assert str(w._cancel_btn.cget("state")) == "normal"

    def test_cancel_btn_invokes_callback(self, root, registry):
        called = []
        registry.create_task(task_type="scan", name="Cancellable",
                             cancel_callback=lambda: called.append(True))
        w = _build_window(root, registry)
        root.update_idletasks()
        items = w.tree.get_children("")
        if items:
            w.tree.selection_set(items[0])
            w._on_selection_changed()
            w._cancel_selected_task()
        assert called == [True]

    def test_selection_change_updates_btn_state(self, root, registry):
        t1 = registry.create_task(task_type="scan", name="With Cancel",
                                   cancel_callback=lambda: None)
        t2 = registry.create_task(task_type="scan", name="No Cancel",
                                   cancel_callback=None)
        w = _build_window(root, registry)
        root.update_idletasks()

        # Select the task with callback
        w.tree.selection_set(t1)
        w._on_selection_changed()
        assert str(w._cancel_btn.cget("state")) == "normal"

        # Switch to task without callback
        w.tree.selection_set(t2)
        w._on_selection_changed()
        assert str(w._cancel_btn.cget("state")) == "disabled"
