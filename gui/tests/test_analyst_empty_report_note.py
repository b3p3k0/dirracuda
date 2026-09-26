"""The report view explains an empty report instead of just showing nothing."""

from __future__ import annotations

import tkinter as tk

import pytest

from experimental.analyst.report_json import UNVERIFIED_NOTICE
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


def _report(*, files_read: int, discovered: int) -> dict:
    return {
        "report_schema_version": 2,
        "run": {
            "run_id": "r" * 32,
            "report_label": "136.40.65.205",
            "read_mode": "quick",
            "model_tag": "gpt-oss-120b",
            "model_digest": None,
            "identity_kind": "reported",
            "server_fingerprint": None,
            "created_at_utc": "2026-09-24T17:13:40Z",
            "files_read": files_read,
            "files_total": discovered,
            "flagged_files": 0,
        },
        "read": {
            "risk_level": "LOW",
            "host_summary": "Automated read unavailable. 0 files reviewed, 0 flagged.",
            "likely_owner": None,
            "contacts": [],
            "top_exposures": [],
            "unverified_notice": UNVERIFIED_NOTICE,
        },
        "facts": [],
        "coverage": {
            "discovered": discovered, "terminal": discovered,
            "no_text_layer": 0, "parse_failed": 0, "unsupported": 0,
        },
    }


@pytest.mark.gui_smoke
def test_an_empty_report_says_why_it_is_empty(view) -> None:
    view._show_report(_report(files_read=0, discovered=1), changed=False)
    assert view._status_var.get() == (
        "Nothing was sent to the model. 1 file found and none could be read "
        "(no readable text)."
    )


@pytest.mark.gui_smoke
def test_a_report_with_content_carries_no_such_line(view) -> None:
    view._show_report(_report(files_read=1, discovered=1), changed=False)
    assert view._status_var.get() == ""
