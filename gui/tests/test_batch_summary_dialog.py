"""Tests for shared batch summary dialog helpers."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components import batch_summary_dialog as summary


def test_resolve_summary_columns_default_shape():
    columns, headings, widths = summary._resolve_summary_columns(show_protocol=False)

    assert columns == ("ip", "action", "status", "notes")
    assert headings["ip"] == "IP Address"
    assert "protocol" not in headings
    assert widths["notes"] == 360


def test_resolve_summary_columns_protocol_shape():
    columns, headings, widths = summary._resolve_summary_columns(show_protocol=True)

    assert columns == ("ip", "protocol", "action", "status", "notes")
    assert headings["protocol"] == "Protocol"
    assert widths["protocol"] == 90


def test_build_summary_row_default():
    row = summary._build_summary_row(
        {
            "ip_address": "203.0.113.5",
            "action": "probe",
            "status": "success",
            "notes": "ok",
            "protocol": "FTP",
        },
        job_type="probe",
        status="success",
        show_protocol=False,
    )

    assert row == ("203.0.113.5", "Probe", "Success", "ok")


def test_build_summary_row_with_protocol():
    row = summary._build_summary_row(
        {
            "ip_address": "203.0.113.5",
            "action": "probe",
            "status": "success",
            "notes": "ok",
            "protocol": "FTP",
        },
        job_type="probe",
        status="success",
        show_protocol=True,
    )

    assert row == ("203.0.113.5", "FTP", "Probe", "Success", "ok")


def test_export_batch_summary_writes_protocol_csv(monkeypatch, tmp_path):
    out_path = tmp_path / "summary.csv"

    monkeypatch.setattr(
        "gui.components.batch_summary_dialog.filedialog.asksaveasfilename",
        lambda **_kwargs: str(out_path),
    )
    monkeypatch.setattr(
        "gui.components.batch_summary_dialog.messagebox.showinfo",
        lambda *_args, **_kwargs: None,
    )

    summary._export_batch_summary(
        [
            {
                "ip_address": "203.0.113.5",
                "protocol": "FTP",
                "action": "probe",
                "status": "success",
                "notes": "ok",
            }
        ],
        job_type="probe",
        parent=object(),
        show_protocol=True,
    )

    with out_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))

    assert rows[0] == ["ip_address", "protocol", "action", "status", "notes"]
    assert rows[1] == ["203.0.113.5", "FTP", "probe", "success", "ok"]
