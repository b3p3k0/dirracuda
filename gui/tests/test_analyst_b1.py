from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from experimental.analyst.report_json import UNVERIFIED_NOTICE
from experimental.analyst.service import AnalystServiceError, ServiceFailure
from gui.components import analyst_report_window as report_window


@pytest.fixture
def view():
    root = tk.Tk()
    root.withdraw()
    window = report_window.AnalystReportWindow(root)
    window.window.withdraw()
    try:
        yield window
    finally:
        window.destroy()
        root.destroy()


def _report(run_id: str):
    return {
        "run": {
            "run_id": run_id,
            "report_label": "Report",
            "files_read": 1,
            "flagged_files": 0,
        },
        "read": {
            "risk_level": "LOW",
            "host_summary": "Loaded report",
            "likely_owner": None,
            "contacts": [],
            "top_exposures": [],
            "unverified_notice": UNVERIFIED_NOTICE,
        },
        "facts": [],
    }


@pytest.mark.gui_smoke
def test_busy_report_preserves_report_and_retry_reloads(view, monkeypatch):
    run_id = "a" * 32
    prior = {"prior": True}
    view._report = prior
    view._host_summary_var.set("Previously loaded report")
    view._run_id = run_id

    read_report = MagicMock(
        side_effect=[
            AnalystServiceError(ServiceFailure.BUSY),
            (_report(run_id), False),
        ],
    )
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json", read_report,
    )
    view._open_run(run_id)

    assert view._report is prior
    assert view._host_summary_var.get() == "Previously loaded report"
    assert view._status_var.get() == (
        "Report is busy — the analysis is still writing. Click Retry."
    )
    assert "Legacy run" not in view._status_var.get()
    assert view._retry_btn.winfo_manager() == "pack"

    view._retry_btn.invoke()

    assert read_report.call_args_list == [
        ((run_id,), {"path": None}),
        ((run_id,), {"path": None}),
    ]
    assert view._report == _report(run_id)
    assert view._host_summary_var.get() == "Loaded report"
    assert view._retry_btn.winfo_manager() == ""


@pytest.mark.gui_smoke
def test_busy_report_preserves_report_without_legacy_state(view, monkeypatch):
    run_id = "a" * 32
    prior = {"prior": True}
    view._report = prior
    view._host_summary_var.set("Previously loaded report")
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json",
        MagicMock(side_effect=AnalystServiceError(ServiceFailure.BUSY)),
    )

    view._open_run(run_id)

    assert view._report is prior
    assert view._host_summary_var.get() == "Previously loaded report"
    assert view._status_var.get() == (
        "Report is busy — the analysis is still writing. Click Retry."
    )
    assert "Legacy run" not in view._status_var.get()
    assert view._retry_btn.winfo_manager() == "pack"


@pytest.mark.gui_smoke
def test_genuine_report_failure_shows_legacy_state(view, monkeypatch):
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json",
        MagicMock(side_effect=AnalystServiceError(ServiceFailure.REPORT)),
    )
    view._open_run("b" * 32)

    assert view._status_var.get() == "Legacy run - re-run to view a read."
    assert view._host_summary_var.get() == "Legacy run - re-run to view a read."
