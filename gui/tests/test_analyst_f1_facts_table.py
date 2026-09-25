"""F1: the report window's FACTS table shows a count instead of repeating."""

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


def _report(facts: list[dict]) -> dict:
    return {
        "report_schema_version": 3,
        "run": {
            "run_id": "r" * 32, "report_label": "136.50.251.81",
            "read_mode": "full", "model_tag": "qwen3.8-27b",
            "model_digest": None, "identity_kind": "reported",
            "model_path": None, "model_n_params": None,
            "model_size_bytes": None, "model_ftype": None,
            "model_n_vocab": None, "model_n_ctx": None,
            "model_n_ctx_train": None, "server_fingerprint": None,
            "created_at_utc": "2026-09-25T12:00:00Z",
            "files_read": 3, "files_total": 3, "flagged_files": 1,
        },
        "read": {
            "unverified_notice": UNVERIFIED_NOTICE,
            "host_summary": "h", "likely_owner": None, "contacts": [],
            "risk_level": "HIGH", "top_exposures": [],
        },
        "facts": facts,
        "coverage": {
            "discovered": 3, "terminal": 3, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }


def _fact(**overrides) -> dict:
    fact = {
        "kind": "ssn", "category": "pii", "quote": "630-15-0629",
        "file": "Sabina/Fed loan/IncomeDrivenRepayment_2018.pdf",
        "provenance": "page page-1", "rank": "HIGH", "source": "detector",
        "occurrences": 1, "plausibility": "valid",
    }
    fact.update(overrides)
    return fact


def _rows(view):
    return [
        view._facts.item(item, "values")
        for item in view._facts.get_children("")
    ]


@pytest.mark.gui_smoke
def test_the_table_has_a_seen_column(view) -> None:
    assert tuple(view._facts.cget("columns")) == (
        "kind", "value", "file", "seen", "rank",
    )


@pytest.mark.gui_smoke
def test_a_repeated_value_shows_a_count_not_four_rows(view) -> None:
    """The screenshot that started this: four identical SSN rows."""
    view._show_report(_report([_fact(occurrences=4)]), changed=False)
    rows = _rows(view)
    assert len(rows) == 1
    assert rows[0][3] == "4x"


@pytest.mark.gui_smoke
def test_a_single_occurrence_leaves_the_count_blank(view) -> None:
    view._show_report(_report([_fact()]), changed=False)
    assert _rows(view)[0][3] == ""


@pytest.mark.gui_smoke
def test_a_demoted_fact_says_why(view) -> None:
    view._show_report(
        _report([_fact(rank="MED", plausibility="suspect")]), changed=False,
    )
    assert _rows(view)[0][4] == "MED · suspect"


@pytest.mark.gui_smoke
def test_a_v2_report_without_the_new_fields_still_renders(view) -> None:
    """Reports written before F1 must keep opening."""
    fact = _fact()
    fact.pop("occurrences")
    fact.pop("plausibility")
    report = _report([fact])
    report["report_schema_version"] = 2
    view._show_report(report, changed=False)
    assert _rows(view)[0] == (
        "ssn", "630-15-0629",
        "Sabina/Fed loan/IncomeDrivenRepayment_2018.pdf", "", "HIGH",
    )
