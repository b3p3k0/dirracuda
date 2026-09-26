"""F3: the report window shows which organisations the host deals with."""

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


def _report(organizations, numbers=(), total=0, version: int = 4) -> dict:
    report = {
        "report_schema_version": version,
        "run": {
            "run_id": "r" * 32, "report_label": "136.50.251.81",
            "read_mode": "full", "model_tag": "qwen3.8-27b",
            "model_digest": None, "identity_kind": "reported",
            "model_path": None, "model_n_params": None,
            "model_size_bytes": None, "model_ftype": None,
            "model_n_vocab": None, "model_n_ctx": None,
            "model_n_ctx_train": None, "server_fingerprint": None,
            "created_at_utc": "2026-09-25T12:00:00Z",
            "files_read": 6, "files_total": 6, "flagged_files": 0,
        },
        "read": {
            "unverified_notice": UNVERIFIED_NOTICE, "host_summary": "h",
            "likely_owner": None, "contacts": [], "risk_level": "LOW",
            "top_exposures": [],
        },
        "facts": [],
        "coverage": {
            "discovered": 6, "terminal": 6, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }
    if version >= 4:
        report["affiliations"] = {
            "organizations": list(organizations),
            "toll_free": list(numbers),
            "toll_free_total": total,
        }
    return report


@pytest.mark.gui_smoke
def test_the_view_lists_organisations_by_file_count(view) -> None:
    view._show_report(_report([
        {"domain": "utsa.edu", "files": 19, "occurrences": 36},
        {"domain": "neisd.net", "files": 8, "occurrences": 14},
    ]), changed=False)
    shown = view._affiliations_var.get()
    assert "utsa.edu" in shown and "19 files" in shown
    assert shown.index("utsa.edu") < shown.index("neisd.net")


@pytest.mark.gui_smoke
def test_toll_free_is_shown_as_a_count_not_a_list(view) -> None:
    """159 numbers would swamp the panel; the export carries them."""
    view._show_report(_report(
        [],
        [{"value": "800-772-1213", "files": 5, "example_file": "a.pdf"}],
        159,
    ), changed=False)
    shown = view._affiliations_var.get()
    assert "159 toll-free" in shown
    assert "not analysed" in shown
    assert "800-772-1213" not in shown


@pytest.mark.gui_smoke
def test_an_empty_block_reads_as_a_finding_not_an_error(view) -> None:
    view._show_report(_report([]), changed=False)
    assert "appears in enough files" in view._affiliations_var.get()


@pytest.mark.gui_smoke
def test_a_pre_v4_report_still_opens(view) -> None:
    view._show_report(_report([], version=3), changed=False)
    assert view._affiliations_var.get() == "(none)"


@pytest.mark.gui_smoke
def test_clearing_the_view_resets_the_block(view) -> None:
    view._show_report(_report([
        {"domain": "utsa.edu", "files": 19, "occurrences": 36},
    ]), changed=False)
    view._clear_report()
    assert view._affiliations_var.get() == "(none)"
