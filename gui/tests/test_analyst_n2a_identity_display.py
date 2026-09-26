"""N2a: the report view names the model identity kind, and never calls a
reported identity verified (erratum E18)."""

from __future__ import annotations

import tkinter as tk

import pytest

from experimental.analyst.report_json import (
    UNVERIFIED_NOTICE,
    model_identity_label,
)
from gui.components import analyst_report_window as report_window

_DIGEST = "a" * 64


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


def _report(run_overrides: dict) -> dict:
    run = {
        "run_id": "r" * 32,
        "report_label": "Report",
        "read_mode": "quick",
        "model_tag": "qwen3.6:27b",
        "model_digest": _DIGEST,
        "created_at_utc": "2026-09-22T12:00:00Z",
        "files_read": 1,
        "files_total": 1,
        "flagged_files": 0,
    }
    run.update(run_overrides)
    return {
        "report_schema_version": 2,
        "run": run,
        "read": {
            "risk_level": "LOW",
            "host_summary": "Loaded report",
            "likely_owner": None,
            "contacts": [],
            "top_exposures": [],
            "unverified_notice": UNVERIFIED_NOTICE,
        },
        "facts": [],
        "coverage": {
            "discovered": 1, "terminal": 1, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }


# --------------------------------------------------------------------------
# The pure label
# --------------------------------------------------------------------------

def test_a_digest_run_reads_as_verified():
    assert model_identity_label(
        {"model_tag": "qwen3.6:27b", "identity_kind": "digest"}
    ) == "qwen3.6:27b (verified digest)"


def test_a_reported_run_never_reads_as_verified():
    label = model_identity_label(
        {"model_tag": "qwen3.8-27b", "identity_kind": "reported"}
    )
    assert "not verified" in label
    assert "verified digest" not in label


def test_a_reported_run_names_the_server_build_when_known():
    label = model_identity_label({
        "model_tag": "qwen3.8-27b",
        "identity_kind": "reported",
        "server_fingerprint": "b1-f280b26",
    })
    assert "b1-f280b26" in label
    assert "not verified" in label


def test_a_v1_report_without_a_kind_reads_as_a_digest_run():
    """Reports written before N2a carry no kind and are digest runs."""
    assert model_identity_label({"model_tag": "qwen3.6:27b"}) == (
        "qwen3.6:27b (verified digest)"
    )


def test_an_absent_model_tag_does_not_render_as_empty():
    assert "(unknown model)" in model_identity_label({"identity_kind": "digest"})


# --------------------------------------------------------------------------
# The report window
# --------------------------------------------------------------------------

@pytest.mark.gui_smoke
def test_the_window_shows_a_verified_digest_run(view):
    view._show_report(_report({}), changed=False)
    assert "qwen3.6:27b (verified digest)" in view._header_var.get()


@pytest.mark.gui_smoke
def test_the_window_never_shows_a_reported_run_as_verified(view):
    view._show_report(
        _report({
            "model_tag": "qwen3.8-27b",
            "model_digest": None,
            "identity_kind": "reported",
            "server_fingerprint": "b1-f280b26",
        }),
        changed=False,
    )
    shown = view._header_var.get()
    assert "qwen3.8-27b" in shown
    assert "not verified" in shown
    assert "verified digest" not in shown


@pytest.mark.gui_smoke
def test_the_window_renders_a_v1_report(view):
    """A report already on disk must still open."""
    report = _report({})
    report["report_schema_version"] = 1
    view._show_report(report, changed=False)
    assert "verified digest" in view._header_var.get()


@pytest.mark.gui_smoke
def test_clearing_the_window_resets_the_run_header(view):
    view._show_report(_report({}), changed=False)
    view._clear_report()
    assert view._header_var.get() == "Select a completed report."
