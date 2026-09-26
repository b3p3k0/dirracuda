"""F4: the report window shows a RUN header above the model's read."""

from __future__ import annotations

import tkinter as tk

import pytest

from experimental.analyst.report_json import (
    REPORT_SCHEMA_VERSION,
    UNVERIFIED_NOTICE,
)
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


def _report(**overrides) -> dict:
    run = {
        "run_id": "a12d48838f7591e1cc08e663629d097b",
        "report_label": "read 2", "read_mode": "full",
        "model_tag": "qwen3.8-27b", "model_digest": None,
        "identity_kind": "reported", "model_path": None,
        "model_n_params": None, "model_size_bytes": None, "model_ftype": None,
        "model_n_vocab": None, "model_n_ctx": None, "model_n_ctx_train": None,
        "server_fingerprint": None,
        "created_at_utc": "2026-09-25T15:18:01.170721Z",
        "report_written_at_utc": "2026-09-26T07:01:24.000000Z",
        "files_read": 932, "files_total": 1244, "flagged_files": 47,
        "source_root": "/home/kevin/testing_docs/Ted and Sabina",
        "output_root": "/home/kevin/.dirracuda/data/experimental/out",
        "detector_rules_version": "analyst-detectors-v2",
    }
    run.update(overrides)
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run": run,
        "read": {
            "unverified_notice": UNVERIFIED_NOTICE, "host_summary": "h",
            "likely_owner": None, "contacts": [], "risk_level": "LOW",
            "top_exposures": [],
        },
        "facts": [],
        "affiliations": {
            "organizations": [], "toll_free": [], "toll_free_total": 0,
        },
        "coverage": {
            "discovered": 1244, "terminal": 1244, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }


@pytest.mark.gui_smoke
def test_the_header_shows_when_it_ran_and_for_how_long(view) -> None:
    view._show_report(_report(), changed=False)
    header = view._header_var.get()
    assert "2026-09-25 15:18 → 2026-09-26 07:01 UTC" in header
    assert "15h 43m" in header
    assert "79 files/hour" in header


@pytest.mark.gui_smoke
def test_the_header_shows_the_folders(view) -> None:
    view._show_report(_report(), changed=False)
    header = view._header_var.get()
    assert "/home/kevin/testing_docs/Ted and Sabina" in header
    assert "/home/kevin/.dirracuda/data/experimental/out" in header


@pytest.mark.gui_smoke
def test_the_model_identity_moved_into_the_header(view) -> None:
    """E18 still holds: a reported identity never reads as verified."""
    view._show_report(_report(), changed=False)
    header = view._header_var.get()
    assert "qwen3.8-27b (reported by the server, not verified)" in header
    assert "verified digest" not in header


@pytest.mark.gui_smoke
def test_the_header_names_the_rules_that_produced_the_findings(view) -> None:
    """Two reports on one host can disagree because the rules changed."""
    view._show_report(_report(), changed=False)
    assert "analyst-detectors-v2" in view._header_var.get()


@pytest.mark.gui_smoke
def test_a_pre_f4_report_still_opens(view) -> None:
    run_overrides = _report()["run"]
    for key in (
        "source_root", "output_root", "report_written_at_utc",
        "detector_rules_version",
    ):
        run_overrides.pop(key)
    report = _report()
    report["run"] = run_overrides
    report["report_schema_version"] = 2
    report.pop("affiliations")
    view._show_report(report, changed=False)
    header = view._header_var.get()
    assert "2026-09-25 15:18 UTC" in header
    assert "Source" not in header


@pytest.mark.gui_smoke
def test_clearing_resets_the_header(view) -> None:
    view._show_report(_report(), changed=False)
    view._clear_report()
    assert view._header_var.get() == "Select a completed report."
