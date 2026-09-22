"""S-B Analyst stability GUI tests."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystRunSummary
from experimental.analyst.state import RunState
from gui.utils.analyst_tasks import apply_analyst_task_hydration
from gui.utils.running_tasks import RunningTaskRegistry


def _summary(
    run_id: str,
    state: RunState,
    *,
    schedule_state: str = "available",
) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id=run_id,
        state=state,
        report_label=f"Run {run_id[0]}",
        mode="fast",
        created_at_utc="2026-09-19T12:00:00Z",
        updated_at_utc="2026-09-19T12:01:00Z",
        discovered_files=4,
        terminal_files=1,
        selected_files=2,
        model_reviewed_files=1,
        detector_hits=0,
        model_findings=0,
        schedule_state=schedule_state,
        resource_not_before_utc=None,
    )


class _Settings:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.writes = []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))
        return True


def _find_button(widget: tk.Misc, text: str) -> tk.Button:
    for child in widget.winfo_children():
        if child.winfo_class() == "Button" and child.cget("text") == text:
            return child
        try:
            return _find_button(child, text)
        except LookupError:
            pass
    raise LookupError(text)


@pytest.fixture(autouse=True)
def _isolated_profiles(tmp_path, monkeypatch):
    """N1: keep the profile selector off the real user database."""
    from experimental.analyst import profiles as profile_store

    path = tmp_path / "profiles.db"
    real_ensure = profile_store.ensure_default_profile
    real_list = profile_store.list_profiles
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **kwargs: real_ensure(path=path),
    )
    monkeypatch.setattr(
        profile_store, "list_profiles", lambda **kwargs: real_list(path=path),
    )


@pytest.fixture
def gui(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    try:
        yield module, root
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_selection_actions_and_abandon_runs_off_tk_thread(gui, monkeypatch) -> None:
    module, root = gui
    registry = RunningTaskRegistry()
    tab = module.AnalystTab(
        root,
        {"settings_manager": _Settings(), "running_tasks_registry": registry},
    )
    resumable = _summary("a" * 32, RunState.READY)
    active = _summary("b" * 32, RunState.FINALIZING)
    tab._summaries = [resumable, active]
    for item in tab._summaries:
        tab._runs.insert("", "end", iid=item.run_id, values=(item.report_label,))
    tab._hydrate_registry()

    tab._runs.selection_set(resumable.run_id)
    tab._on_selection()
    assert tab._resume_btn.cget("state") == "normal"
    assert tab._abandon_btn.cget("state") == "normal"
    assert tab._cancel_btn.cget("state") == "disabled"

    callbacks = queue.Queue()
    completed = threading.Event()
    worker_threads = []
    abandoned = MagicMock()

    def abandon_run(run_id):
        worker_threads.append(threading.get_ident())
        abandoned(run_id)
        completed.set()

    monkeypatch.setattr("experimental.analyst.service.abandon_run", abandon_run)
    tab._schedule = callbacks.put
    main_thread = threading.get_ident()
    tab._abandon_btn.invoke()
    assert completed.wait(2.0)
    callbacks.get(timeout=2.0)()

    abandoned.assert_called_once_with(resumable.run_id)
    assert worker_threads[0] != main_thread
    assert registry.get_task(resumable.task_id) is None

    tab._runs.selection_set(active.run_id)
    tab._on_selection()
    assert tab._resume_btn.cget("state") == "disabled"
    assert tab._abandon_btn.cget("state") == "disabled"
    assert tab._cancel_btn.cget("state") == "normal"


def test_hydration_uses_cancel_for_active_and_abandon_for_resumable() -> None:
    registry = RunningTaskRegistry()
    active = _summary(
        "c" * 32, RunState.INTERRUPTED, schedule_state="paused_resource",
    )
    resumable = _summary("d" * 32, RunState.CANCELLED_PENDING_RESUME)
    cancelled = []
    abandoned = []

    apply_analyst_task_hydration(
        registry,
        (active, resumable),
        reopen=lambda _run_id: lambda: None,
        cancel=lambda run_id: lambda: cancelled.append(run_id),
        abandon=lambda run_id: lambda: abandoned.append(run_id),
    )
    registry.get_task(active.task_id).cancel_callback()
    registry.get_task(resumable.task_id).cancel_callback()

    assert cancelled == [active.run_id]
    assert abandoned == [resumable.run_id]


def test_cancel_all_abandons_and_removes_hydrated_resumable_task() -> None:
    registry = RunningTaskRegistry()
    resumable = _summary("e" * 32, RunState.INTERRUPTED)
    abandoned = []

    def abandon(run_id):
        def callback():
            abandoned.append(run_id)
            registry.remove_task(f"analyst:{run_id}")

        return callback

    apply_analyst_task_hydration(
        registry,
        (resumable,),
        reopen=lambda _run_id: lambda: None,
        cancel=lambda _run_id: lambda: None,
        abandon=abandon,
    )
    dashboard = SimpleNamespace(
        request_cancel_active_or_queued_work=registry.cancel_all,
        has_active_or_queued_work=registry.has_tasks,
    )

    dashboard.request_cancel_active_or_queued_work()

    assert abandoned == [resumable.run_id]
    assert dashboard.has_active_or_queued_work() is False


def test_close_reentrancy_guard_returns_before_second_dialog(monkeypatch) -> None:
    from gui.utils.dirracuda_loader import load_dirracuda_module

    module = load_dirracuda_module()
    app = module.XSMBSeekGUI.__new__(module.XSMBSeekGUI)
    app._closing = True
    dialog = MagicMock()
    monkeypatch.setattr(module.messagebox, "askyesno", dialog)

    app._on_closing()

    dialog.assert_not_called()


@pytest.mark.gui_smoke
def test_output_folder_defaults_saves_loads_and_analyze_persists(
    gui, monkeypatch, tmp_path: Path,
) -> None:
    module, root = gui
    default = tmp_path / "reports"
    default.mkdir()
    monkeypatch.setattr(
        module, "get_paths", lambda: SimpleNamespace(analyst_reports_dir=default),
    )
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models", lambda: (),
    )
    settings = _Settings()
    tab = module.AnalystTab(root, {"settings_manager": settings})
    assert tab._output_var.get() == str(default)

    saved = tmp_path / "saved"
    saved.mkdir()
    tab._output_var.set(str(saved))
    tab._open_advanced()
    _find_button(tab._advanced_dialog, "Save").invoke()
    assert settings.values["analyst.output_folder"] == str(saved)

    fresh = module.AnalystTab(root, {"settings_manager": settings})
    assert fresh._output_var.get() == str(saved)

    analyzed = tmp_path / "analyzed"
    source = tmp_path / "source"
    analyzed.mkdir()
    source.mkdir()
    fresh._source_var.set(str(source))
    fresh._output_var.set(str(analyzed))
    monkeypatch.setattr(
        "experimental.analyst.service.create_and_launch",
        lambda *_args, **_kwargs: SimpleNamespace(run_id="f" * 32),
    )
    monkeypatch.setattr(
        module.threading,
        "Thread",
        lambda target, daemon=True: SimpleNamespace(start=target),
    )
    fresh._schedule = lambda callback: callback()

    fresh._start_analysis()

    assert settings.values["analyst.output_folder"] == str(analyzed)
