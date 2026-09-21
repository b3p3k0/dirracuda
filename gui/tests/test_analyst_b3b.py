from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    targets = []

    class Thread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            targets.append(target)

        def start(self):
            pass

    monkeypatch.setattr(module.threading, "Thread", Thread)
    root = tk.Tk()
    root.withdraw()
    widget = module.AnalystTab(root, {})
    widget._schedule = lambda callback: callback()
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    widget._source_var.set(str(source))
    widget._output_var.set(str(output))
    try:
        yield module, widget, targets
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_directory_launch_shows_progress_and_hides_cancel_on_finish(
    tab, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _module, widget, targets = tab
    observed = {}

    def launch(_request, **kwargs):
        observed["cancel_check"] = kwargs["cancel_check"]
        observed["progress_callback"] = kwargs["progress_callback"]
        kwargs["progress_callback"](512)
        observed["progress"] = widget._status_var.get()
        return SimpleNamespace(run_id="a" * 32)

    monkeypatch.setattr(
        "experimental.analyst.service.create_and_launch", launch,
    )

    assert widget._cancel_launch_btn.winfo_manager() == ""
    widget._start_analysis()

    assert widget._cancel_launch_btn.winfo_manager() == "pack"
    assert len(targets) == 1
    targets.pop()()

    assert callable(observed["cancel_check"])
    assert callable(observed["progress_callback"])
    assert observed["progress"] == "Inventorying… 512 files"
    assert widget._cancel_launch_btn.winfo_manager() == ""


@pytest.mark.gui_smoke
def test_directory_launch_cancel_sets_event_and_reports_cancelled(
    tab, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, widget, targets = tab
    shown = MagicMock()
    monkeypatch.setattr(module.safe_messagebox, "showerror", shown)

    def launch(_request, **kwargs):
        assert kwargs["cancel_check"]() is False
        widget._cancel_launch_btn.invoke()
        assert kwargs["cancel_check"]() is True
        assert widget._status_var.get() == "Cancelling inventory…"
        raise RuntimeError("inventory stopped")

    monkeypatch.setattr(
        "experimental.analyst.service.create_and_launch", launch,
    )

    widget._start_analysis()
    targets.pop()()

    assert widget._launch_cancel_event.is_set()
    assert widget._status_var.get() == "Inventory cancelled."
    assert widget._cancel_launch_btn.winfo_manager() == ""
    shown.assert_called_once_with(
        "Analyst", "Inventory cancelled.", parent=widget.frame.winfo_toplevel(),
    )
