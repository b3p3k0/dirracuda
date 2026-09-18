"""R7 read-first Analyst report window tests."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from experimental.analyst.report_json import (
    UNVERIFIED_NOTICE,
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
    dumps_report,
)
from experimental.analyst.report_render import render_markdown
from gui.components import analyst_report_window as report_window


@pytest.fixture
def report():
    return build_report_json(
        RunMeta(
            run_id="a" * 32,
            report_label="host12",
            read_mode="quick",
            model_tag="qwen-test",
            model_digest="0" * 64,
            created_at_utc="2026-09-18T12:00:00Z",
            files_read=310,
            files_total=325,
            flagged_files=22,
        ),
        HostRead(
            host_summary=(
                "Small-business accounting server holding client tax and payroll files."
            ),
            likely_owner="Anytown Tax & Books LLC",
            contacts=("office@anytowntax.example", "(555) 123-4567"),
            risk_level="HIGH",
            top_exposures=(
                TopExposure(
                    rank=1,
                    severity="HIGH",
                    text="Client SSNs in 2023_returns.xlsx (48 rows)",
                ),
                TopExposure(
                    rank=2,
                    severity="MED",
                    text="Payroll bank accounts in payroll_q3.csv",
                ),
            ),
        ),
        (
            GroundedFact(
                kind="ssn",
                category="pii",
                quote="123-45-6789",
                file="2023_returns.xlsx",
                provenance="sheet 1 row 2",
                rank="HIGH",
                source="detector",
            ),
            GroundedFact(
                kind="bank_account",
                category="financial",
                quote="ending in 4321",
                file="payroll_q3.csv",
                provenance="row 8",
                rank="MED",
                source="model",
            ),
            GroundedFact(
                kind="email",
                category="contact",
                quote="office@anytowntax.example",
                file="contacts.vcf",
                provenance="line 4",
                rank="low",
                source="detector",
            ),
            GroundedFact(
                kind="age",
                category="demographic",
                quote="age 42",
                file="client.txt",
                provenance="line 1",
                rank="low",
                source="model",
            ),
        ),
        Coverage(
            discovered=325,
            terminal=325,
            no_text_layer=5,
            parse_failed=4,
            unsupported=6,
        ),
    )


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr(report_window.AnalystReportWindow, "_load_runs", lambda self: None)
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
def test_read_block_populates_from_report_json(view, report):
    view._show_report(report, changed=False)

    assert view._risk_var.get() == "Risk: ● HIGH"
    assert view._host_summary_var.get() == report["read"]["host_summary"]
    assert view._owner_var.get() == "Likely owner : Anytown Tax & Books LLC"
    assert view._contacts_var.get() == (
        "Contacts     : office@anytowntax.example, (555) 123-4567"
    )
    assert view._counts_var.get() == "Files read   : 310      Flagged files: 22"
    assert "1. HIGH  Client SSNs" in view._exposures_var.get()
    assert "2. MED  Payroll bank accounts" in view._exposures_var.get()
    assert view._notice_var.get() == UNVERIFIED_NOTICE


@pytest.mark.gui_smoke
def test_facts_category_filter_narrows_rows(view, report):
    view._show_report(report, changed=False)
    assert len(view._facts.get_children("")) == 4

    view._category_var.set("Financial")
    view._apply_fact_filter()

    rows = [
        view._facts.item(item, "values")
        for item in view._facts.get_children("")
    ]
    assert rows == [("bank_account", "ending in 4321", "payroll_q3.csv", "MED")]


@pytest.mark.gui_smoke
@pytest.mark.parametrize(
    ("extension", "expected"),
    ((".md", render_markdown), (".json", dumps_report)),
)
def test_export_writes_requested_renderer(
    view, report, tmp_path, monkeypatch, extension, expected,
):
    view._show_report(report, changed=False)
    target = tmp_path / f"analyst{extension}"
    chooser = MagicMock(return_value=str(target))
    monkeypatch.setattr(report_window.filedialog, "asksaveasfilename", chooser)

    view._export_report()

    assert target.read_text(encoding="utf-8") == expected(report)
    assert chooser.call_args.kwargs["defaultextension"] == ".md"


@pytest.mark.gui_smoke
def test_copy_places_markdown_on_clipboard(view, report, monkeypatch):
    view._show_report(report, changed=False)
    clear = MagicMock()
    append = MagicMock()
    monkeypatch.setattr(view, "clipboard_clear", clear)
    monkeypatch.setattr(view, "clipboard_append", append)

    view._copy_report()

    clear.assert_called_once_with()
    append.assert_called_once_with(render_markdown(report))


@pytest.mark.gui_smoke
def test_changed_badge_and_legacy_run_state(view, report, monkeypatch):
    view._show_report(report, changed=True)
    assert view._changed_var.get() == "changed since saved"
    assert str(view._export_btn["state"]) == "normal"
    assert str(view._copy_btn["state"]) == "normal"

    view._runs = [("b" * 32, "legacy", "2026-09-18T12:00:00Z")]
    read_report_json = MagicMock(side_effect=RuntimeError("no report.json"))
    monkeypatch.setattr(
        "experimental.analyst.service.read_report_json", read_report_json,
    )
    view._open_selected(0)

    assert view._status_var.get() == "Legacy run - re-run to view a read."
    assert view._host_summary_var.get() == "Legacy run - re-run to view a read."
    assert str(view._export_btn["state"]) == "disabled"
    assert str(view._copy_btn["state"]) == "disabled"
