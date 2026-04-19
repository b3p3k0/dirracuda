"""Tests for shared batch summary dialog helpers."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def test_show_batch_summary_dialog_wires_vertical_scrollbar(monkeypatch):
    """Summary dialog should wire Treeview y-scroll to a right-side scrollbar."""
    tree_instances = []
    scrollbar_instances = []

    class _DummyBase:
        def __init__(self, *args, **kwargs):
            pass

        def pack(self, *args, **kwargs):
            return None

        def destroy(self):
            return None

    class _DummyTop(_DummyBase):
        def title(self, *_args, **_kwargs):
            return None

        def geometry(self, *_args, **_kwargs):
            return None

        def transient(self, *_args, **_kwargs):
            return None

        def grab_set(self):
            return None

    class _DummyTree(_DummyBase):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.yscrollcommand = kwargs.get("yscrollcommand")
            self.yview = MagicMock()
            tree_instances.append(self)

        def heading(self, *_args, **_kwargs):
            return None

        def column(self, *_args, **_kwargs):
            return None

        def insert(self, *_args, **_kwargs):
            return None

    class _DummyScrollbar(_DummyBase):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.command = None
            self.set = MagicMock()
            scrollbar_instances.append(self)

        def config(self, **kwargs):
            self.command = kwargs.get("command")

    monkeypatch.setattr(summary.tk, "Toplevel", _DummyTop)
    monkeypatch.setattr(summary.tk, "Frame", _DummyBase)
    monkeypatch.setattr(summary.tk, "Button", _DummyBase)
    monkeypatch.setattr(summary.tk, "Label", _DummyBase)
    monkeypatch.setattr(summary.ttk, "Treeview", _DummyTree)
    monkeypatch.setattr(summary.ttk, "Scrollbar", _DummyScrollbar)

    theme = MagicMock()
    theme.apply_to_widget = lambda *_a, **_k: None
    theme.apply_theme_to_application = lambda *_a, **_k: None

    summary.show_batch_summary_dialog(
        parent=MagicMock(),
        theme=theme,
        job_type="probe",
        results=[{"ip_address": "1.2.3.4", "action": "probe", "status": "success", "notes": "ok"}],
        show_export=False,
        wait=False,
        modal=False,
    )

    assert len(tree_instances) == 1
    assert len(scrollbar_instances) == 1

    tree = tree_instances[0]
    scrollbar = scrollbar_instances[0]
    tree.yscrollcommand("0.0", "1.0")
    scrollbar.set.assert_called_once_with("0.0", "1.0")

    scrollbar.command("moveto", "0.5")
    tree.yview.assert_called_once_with("moveto", "0.5")
