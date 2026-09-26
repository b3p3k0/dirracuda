"""R6b Analyst batch-export helper tests."""

from __future__ import annotations

import csv
import io
import json
import stat
from pathlib import Path

import pytest

from experimental.analyst.report_export import export_reports
from experimental.analyst.report_json import (
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
    dumps_report,
)
from experimental.analyst.report_render import render_markdown


def _report(label: str, run_id: str, quote: str = "123-45-6789") -> dict:
    return build_report_json(
        RunMeta(
            run_id=run_id,
            report_label=label,
            read_mode="quick",
            model_tag="qwen-test",
            model_digest="0" * 64,
            created_at_utc="2026-09-18T12:00:00Z",
            files_read=2,
            files_total=3,
            flagged_files=1,
        ),
        HostRead(
            host_summary=f"Read for {label}",
            likely_owner=None,
            contacts=(),
            risk_level="HIGH",
            top_exposures=(TopExposure(1, "HIGH", f"Exposure for {label}"),),
        ),
        (
            GroundedFact(
                kind="ssn",
                category="pii",
                quote=quote,
                file=f"{label}.txt",
                provenance="line 1",
                rank="HIGH",
                source="detector",
            ),
        ),
        Coverage(3, 3, 0, 0, 0),
    )


def test_per_report_writes_markdown_and_json_owner_only(tmp_path: Path) -> None:
    one = _report("host-one", "a" * 32)
    two = _report("host-two", "b" * 32)

    result = export_reports(
        (("host-one", one), ("host-two", two)),
        formats={"md", "json"},
        layout="per_report",
        include={"read", "facts"},
        dest_dir=tmp_path,
    )

    assert result.report_count == 2
    assert result.file_count == 4
    assert {path.name for path in result.written_paths} == {
        "host-one.md", "host-one.json", "host-two.md", "host-two.json",
    }
    assert (tmp_path / "host-one.md").read_text(encoding="utf-8") == (
        render_markdown(one)
    )
    assert (tmp_path / "host-two.json").read_text(encoding="utf-8") == (
        dumps_report(two)
    )
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in result.written_paths
    )


def test_combined_writes_one_file_per_format_with_both_reports(
    tmp_path: Path,
) -> None:
    reports = (
        ("host-one", _report("host-one", "a" * 32)),
        ("host-two", _report("host-two", "b" * 32)),
    )

    result = export_reports(
        reports,
        formats={"md", "json", "txt"},
        layout="combined",
        include={"read", "facts"},
        dest_dir=tmp_path,
    )

    assert result.file_count == 3
    assert {path.name for path in result.paths} == {
        "analyst-reports.md", "analyst-reports.json", "analyst-reports.txt",
    }
    markdown = (tmp_path / "analyst-reports.md").read_text(encoding="utf-8")
    assert "Analyst report: host-one" in markdown
    assert "Analyst report: host-two" in markdown
    assert "\n---\n" in markdown
    payload = json.loads(
        (tmp_path / "analyst-reports.json").read_text(encoding="utf-8")
    )
    assert [item["run"]["report_label"] for item in payload] == [
        "host-one", "host-two",
    ]


def test_include_read_only_and_facts_only_trim_each_rendered_shape(
    tmp_path: Path,
) -> None:
    report = _report("host", "a" * 32)
    read_dir = tmp_path / "read"
    facts_dir = tmp_path / "facts"
    read_dir.mkdir()
    facts_dir.mkdir()

    export_reports(
        (("host", report),),
        formats={"md", "json"},
        layout="per_report",
        include={"read"},
        dest_dir=read_dir,
    )
    export_reports(
        (("host", report),),
        formats={"md", "json"},
        layout="per_report",
        include={"facts"},
        dest_dir=facts_dir,
    )

    assert "## FACTS" not in (read_dir / "host.md").read_text(encoding="utf-8")
    assert "facts" not in json.loads(
        (read_dir / "host.json").read_text(encoding="utf-8")
    )
    facts_markdown = (facts_dir / "host.md").read_text(encoding="utf-8")
    assert facts_markdown.startswith("## FACTS\n")
    assert "WHAT THIS IS" not in facts_markdown
    facts_json = json.loads(
        (facts_dir / "host.json").read_text(encoding="utf-8")
    )
    assert "facts" in facts_json
    assert "read" not in facts_json


def test_labels_are_sanitized_and_collisions_are_deduplicated(
    tmp_path: Path,
) -> None:
    result = export_reports(
        (
            ("host/one", _report("first", "a" * 32)),
            ("host?one", _report("second", "b" * 32)),
        ),
        formats={"md", "json"},
        layout="per_report",
        include={"read", "facts"},
        dest_dir=tmp_path,
    )

    assert {path.name for path in result.paths} == {
        "host-one.md", "host-one.json", "host-one-2.md", "host-one-2.json",
    }


def test_empty_include_is_rejected_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="include"):
        export_reports(
            (("host", _report("host", "a" * 32)),),
            formats={"md"},
            layout="per_report",
            include=set(),
            dest_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_csv_facts_keep_spreadsheet_formula_guard(tmp_path: Path) -> None:
    report = _report("host", "a" * 32, quote="=SUM(A1:A2)")

    export_reports(
        (("host", report),),
        formats={"csv"},
        layout="per_report",
        include={"facts"},
        dest_dir=tmp_path,
    )

    rows = list(csv.reader(io.StringIO(
        (tmp_path / "host.csv").read_text(encoding="utf-8")
    )))
    assert rows[1][1] == "'=SUM(A1:A2)"
