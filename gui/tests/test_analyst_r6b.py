"""R6b Analyst batch-export dialog tests."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from experimental.analyst.report_export import ExportResult
from experimental.analyst.report_json import (
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
)
from experimental.analyst.state import RunState


def _report(label: str, run_id: str) -> dict:
    return build_report_json(
        RunMeta(
            run_id=run_id,
            report_label=label,
            read_mode="quick",
            model_tag="qwen-test",
            model_digest="0" * 64,
            created_at_utc="2026-09-18T12:00:00Z",
            files_read=1,
            files_total=1,
            flagged_files=1,
        ),
        HostRead(
            host_summary=f"Read for {label}",
            likely_owner=None,
            contacts=(),
            risk_level="HIGH",
            top_exposures=(TopExposure(1, "HIGH", "Exposure"),),
        ),
        (
            GroundedFact(
                kind="ssn", category="pii", quote="123-45-6789",
                file="one.txt", provenance="line 1", rank="HIGH",
                source="detector",
            ),
        ),
        Coverage(1, 1, 0, 0, 0),
    )


def _widget_texts(widget: tk.Misc) -> set[str]:
    texts: set[str] = set()
    for child in widget.winfo_children():
        try:
            text = child.cget("text") if "text" in child.keys() else ""
        except tk.TclError:
            text = ""
        if text:
            texts.add(str(text))
        texts.update(_widget_texts(child))
    return texts


def _find_button(widget: tk.Misc, text: str) -> tk.Button:
    for child in widget.winfo_children():
        if child.winfo_class() == "Button" and child.cget("text") == text:
            return child
        try:
            return _find_button(child, text)
        except LookupError:
            pass
    raise LookupError(text)


@pytest.fixture
def tab_and_root(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    tab = module.AnalystTab(root, {"settings_manager": MagicMock()})
    try:
        yield module, tab, root
    finally:
        root.destroy()


def _select(tab, *run_ids: str) -> None:
    for run_id in run_ids:
        tab._runs.insert(
            "", "end", iid=run_id, values=(run_id[:6], "Quick", "complete", "HIGH"),
        )
    tab._runs.selection_set(*run_ids)
    tab._on_selection()


def _summary(run_id: str, label: str):
    return SimpleNamespace(
        run_id=run_id,
        report_label=label,
        state=RunState.COMPLETE,
        schedule_state="available",
    )


@pytest.mark.gui_smoke
def test_export_button_enables_when_a_run_is_selected(tab_and_root) -> None:
    _module, tab, _root = tab_and_root
    assert tab._export_btn.cget("state") == "disabled"

    _select(tab, "a" * 32)

    assert tab._export_btn.cget("state") == "normal"


@pytest.mark.gui_smoke
def test_dialog_builds_contract_controls_with_grab_and_focus(
    tab_and_root, monkeypatch,
) -> None:
    module, tab, root = tab_and_root
    focus = MagicMock()
    monkeypatch.setattr(module, "ensure_dialog_focus", focus)
    _select(tab, "a" * 32, "b" * 32)

    tab._open_export_dialog()

    dialog = tab._export_dialog
    assert dialog is not None
    assert {
        "Selected: 2 reports", "Format", "Markdown", "JSON", "Plain text",
        "CSV (facts)", "Layout", "One file per report", "One combined file",
        "Include", "The read", "The facts", "Folder", "Browse", "Cancel", "Export",
    } <= _widget_texts(dialog)
    assert tab._export_format_vars["md"].get() is True
    assert tab._export_format_vars["json"].get() is True
    assert tab._export_format_vars["txt"].get() is False
    assert tab._export_format_vars["csv"].get() is False
    assert tab._export_layout_var.get() == "per_report"
    assert tab._export_include_vars["read"].get() is True
    assert tab._export_include_vars["facts"].get() is True
    assert dialog.grab_current() == dialog
    focus.assert_called_once_with(dialog, root)


@pytest.mark.gui_smoke
def test_legacy_run_is_skipped_and_reported(
    tab_and_root, tmp_path: Path, monkeypatch,
) -> None:
    module, tab, _root = tab_and_root
    run_one, run_two = "a" * 32, "b" * 32
    report = _report("host-one", run_one)
    tab._summaries = [_summary(run_one, "host-one"), _summary(run_two, "legacy")]
    _select(tab, run_one, run_two)
    read = MagicMock(side_effect=[(report, False), RuntimeError("legacy")])
    monkeypatch.setattr("experimental.analyst.service.read_report_json", read)
    exported = MagicMock(return_value=ExportResult(1, 1, (tmp_path / "one.md",)))
    monkeypatch.setattr("experimental.analyst.report_export.export_reports", exported)
    info = MagicMock()
    monkeypatch.setattr(module.safe_messagebox, "showinfo", info)
    monkeypatch.setattr(
        module.threading,
        "Thread",
        lambda target, daemon=True: SimpleNamespace(start=target),
    )
    tab._schedule = lambda callback: callback()
    tab._open_export_dialog()
    tab._export_folder_var.set(str(tmp_path))

    _find_button(tab._export_dialog, "Export").invoke()

    assert read.call_count == 2
    assert "Exported 1 reports" in info.call_args.args[1]
    assert "Skipped 1 legacy runs" in info.call_args.args[1]


@pytest.mark.gui_smoke
def test_export_passes_the_chosen_options(
    tab_and_root, tmp_path: Path, monkeypatch,
) -> None:
    module, tab, _root = tab_and_root
    run_id = "a" * 32
    report = _report("host-one", run_id)
    tab._summaries = [_summary(run_id, "host-one")]
    _select(tab, run_id)
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json",
        MagicMock(return_value=(report, False)),
    )
    exported = MagicMock(return_value=ExportResult(1, 2, ()))
    monkeypatch.setattr("experimental.analyst.report_export.export_reports", exported)
    monkeypatch.setattr(module.safe_messagebox, "showinfo", MagicMock())
    monkeypatch.setattr(
        module.threading,
        "Thread",
        lambda target, daemon=True: SimpleNamespace(start=target),
    )
    tab._schedule = lambda callback: callback()
    tab._open_export_dialog()
    tab._export_format_vars["md"].set(False)
    tab._export_format_vars["json"].set(True)
    tab._export_format_vars["txt"].set(True)
    tab._export_format_vars["csv"].set(True)
    tab._export_layout_var.set("combined")
    tab._export_include_vars["read"].set(False)
    tab._export_include_vars["facts"].set(True)
    tab._export_folder_var.set(str(tmp_path))

    _find_button(tab._export_dialog, "Export").invoke()

    exported.assert_called_once_with(
        [("host-one", report)],
        formats=frozenset({"json", "txt", "csv"}),
        layout="combined",
        include=frozenset({"facts"}),
        dest_dir=tmp_path,
    )
