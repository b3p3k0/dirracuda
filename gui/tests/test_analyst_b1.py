from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import AnalystServiceError, ServiceFailure
from experimental.analyst.store import AnalystStoreBusy
from gui.components import analyst_report_window as report_window


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr(
        "experimental.analyst.report_browser.list_completed_reports", lambda **_kwargs: (),
    )
    root = tk.Tk()
    root.withdraw()
    window = report_window.AnalystReportWindow(root)
    window.window.withdraw()
    try:
        yield window
    finally:
        window.destroy()
        root.destroy()


@pytest.mark.gui_smoke
def test_busy_run_list_preserves_report_and_retry_reloads(view, monkeypatch):
    prior = {"prior": True}
    view._report = prior
    view._host_summary_var.set("Previously loaded report")

    list_reports = MagicMock(side_effect=AnalystStoreBusy("busy"))
    monkeypatch.setattr(
        "experimental.analyst.report_browser.list_completed_reports", list_reports,
    )
    view._load_runs()

    message = "Reports are busy — the analysis is still writing. Click Retry."
    assert view._report is prior
    assert view._host_summary_var.get() == "Previously loaded report"
    assert view._run_var.get() == message
    assert view._status_var.get() == message
    assert "Legacy run" not in view._status_var.get()
    assert view._retry_btn.winfo_manager() == "pack"

    list_reports.reset_mock(side_effect=True)
    list_reports.return_value = ()
    view._retry_btn.invoke()

    list_reports.assert_called_once_with(path=None)
    assert view._status_var.get() == "No completed Analyst report is available yet."
    assert view._retry_btn.winfo_manager() == ""


@pytest.mark.gui_smoke
def test_busy_report_preserves_report_without_legacy_state(view, monkeypatch):
    prior = {"prior": True}
    view._report = prior
    view._host_summary_var.set("Previously loaded report")
    view._runs = [("a" * 32, "Report", "2026-09-20T12:00:00Z")]
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json",
        MagicMock(side_effect=AnalystServiceError(ServiceFailure.BUSY)),
    )

    view._open_selected(0)

    assert view._report is prior
    assert view._host_summary_var.get() == "Previously loaded report"
    assert view._status_var.get() == (
        "Report is busy — the analysis is still writing. Click Retry."
    )
    assert "Legacy run" not in view._status_var.get()
    assert view._retry_btn.winfo_manager() == "pack"


@pytest.mark.gui_smoke
def test_genuine_list_and_report_failures_keep_existing_states(view, monkeypatch):
    monkeypatch.setattr(
        "experimental.analyst.report_browser.list_completed_reports",
        MagicMock(side_effect=RuntimeError("unavailable")),
    )
    view._load_runs()

    assert view._run_var.get() == "Completed reports unavailable"
    assert view._status_var.get() == "Completed reports are unavailable."
    assert view._retry_btn.winfo_manager() == ""

    view._runs = [("b" * 32, "Legacy", "2026-09-20T12:00:00Z")]
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json",
        MagicMock(side_effect=AnalystServiceError(ServiceFailure.REPORT)),
    )
    view._open_selected(0)

    assert view._status_var.get() == "Legacy run - re-run to view a read."
    assert view._host_summary_var.get() == "Legacy run - re-run to view a read."
