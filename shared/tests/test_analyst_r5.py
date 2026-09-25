"""R5 report rendering and warn-not-block seal acceptance."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest

from experimental.analyst.report import (
    ReportFinalizationError,
    finalize_report,
    open_completed_report_relaxed,
)
from experimental.analyst.report_contract import HTML_CSP
from experimental.analyst.report_json import (
    UNVERIFIED_NOTICE,
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
)
from experimental.analyst.report_render import (
    render,
    render_facts_csv,
    render_html,
    render_markdown,
    render_text,
)
from experimental.analyst.service import (
    AnalystServiceError,
    read_report_json,
)
from shared.tests.test_analyst_c12 import _empty_phase2


def _report() -> dict:
    injected = '<script>alert("synthetic")</script>'
    return build_report_json(
        RunMeta(
            run_id="synthetic-run",
            report_label=injected,
            read_mode="quick",
            model_tag="synthetic:1",
            model_digest="a" * 64,
            created_at_utc="2026-09-18T00:00:00Z",
            files_read=3,
            files_total=4,
            flagged_files=2,
        ),
        HostRead(
            host_summary=f"Synthetic host {injected}",
            likely_owner=f"Owner {injected}",
            contacts=(f"contact-{injected}",),
            risk_level="HIGH",
            top_exposures=(TopExposure(1, "HIGH", f"Exposure {injected}"),),
        ),
        (
            GroundedFact(
                kind="ssn", category="pii", quote=injected,
                file="high.txt", provenance="line 1", rank="HIGH",
                source="detector",
            ),
            GroundedFact(
                kind="phone", category="contact", quote="LOW-ONLY-QUOTE",
                file="low.txt", provenance="line 2", rank="low",
                source="detector",
            ),
        ),
        Coverage(4, 4, 0, 0, 0),
    )


def test_markdown_leads_with_read_notice_and_facts_without_low_exposure() -> None:
    rendered = render_markdown(_report())

    assert rendered.startswith("# WHAT THIS IS\n")
    assert rendered.count(UNVERIFIED_NOTICE) == 1
    assert rendered.index("## TOP EXPOSURES") < rendered.index(UNVERIFIED_NOTICE)
    assert rendered.index(UNVERIFIED_NOTICE) < rendered.index("## FACTS")
    assert "| Kind | Value | File | Seen | Rank |" in rendered
    assert "LOW-ONLY-QUOTE" not in rendered.split(UNVERIFIED_NOTICE, 1)[0]
    assert "LOW-ONLY-QUOTE" in rendered.split("## FACTS", 1)[1]


def test_markdown_keeps_literal_text_without_allowing_structure_injection() -> None:
    payload = _report()
    payload["read"]["host_summary"] = "# [x](y) ` Anytown Tax & Books LLC"
    payload["read"]["likely_owner"] = "Anytown Tax & Books LLC"
    payload["read"]["contacts"] = ["office@anytowntax.example"]
    payload["facts"][0].update({
        "kind": "# kind | [x](y)",
        "quote": "value | # ` [x](y)\r\ncontinued",
        "file": "2023_returns.xlsx | [x](y)",
    })

    rendered = render_markdown(payload)

    assert "Anytown Tax & Books LLC" in rendered
    assert "&amp;" not in rendered
    assert "office@anytowntax.example" in rendered
    assert "2023_returns.xlsx" in rendered
    assert "2023\\_returns.xlsx" not in rendered
    assert "\n# [x](y)" not in rendered
    assert "\\[x\\](y)" in rendered
    fact_rows = rendered.split("|---|---|---|---|---|\n", 1)[1].splitlines()
    assert len(fact_rows) == 2
    assert all(len(re.findall(r"(?<!\\)\|", row)) == 6 for row in fact_rows)
    assert "\\|" in fact_rows[0]
    assert "\r" not in fact_rows[0] and "continued" in fact_rows[0]
    assert "`` value \\| # ` [x](y) continued ``" in fact_rows[0]


def test_html_escapes_every_injected_value_and_carries_frozen_csp() -> None:
    rendered = render_html(_report())

    assert HTML_CSP in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(&quot;synthetic&quot;)&lt;/script&gt;" in rendered
    assert rendered.count("&lt;script&gt;") == 6
    assert "<script src=" not in rendered


def test_facts_csv_prefix_guards_spreadsheet_cells() -> None:
    payload = _report()
    payload["facts"][1].update({
        "kind": "=kind", "quote": "+quote", "file": "@file",
    })
    rows = list(csv.reader(io.StringIO(render_facts_csv(payload))))

    assert rows[0] == ["Kind", "Value", "File", "Seen", "Rank"]
    assert rows[2] == ["'=kind", "'+quote", "'@file", "", "low"]


@pytest.mark.parametrize("fmt", ["md", "txt", "html", "csv"])
def test_dispatcher_renders_every_supported_format(fmt: str) -> None:
    assert render(_report(), fmt)


def test_direct_renderers_accept_valid_report() -> None:
    payload = _report()
    assert all((
        render_markdown(payload),
        render_text(payload),
        render_html(payload),
        render_facts_csv(payload),
    ))


def test_changed_artifact_opens_with_warning_and_validated_json(tmp_path: Path) -> None:
    path, handoff = _empty_phase2(tmp_path)
    finalize_report(handoff, path=path)
    artifact = tmp_path / "output" / "report.html"
    artifact.write_bytes(artifact.read_bytes() + b" ")

    opened = open_completed_report_relaxed(handoff.fence.run_id, path=path)
    payload, changed = read_report_json(handoff.fence.run_id, path=path)

    assert opened.changed is True
    assert opened.report_json_path == tmp_path / "output" / "report.json"
    assert payload["run"]["run_id"] == handoff.fence.run_id
    assert changed is True


def test_unchanged_artifacts_open_without_warning(tmp_path: Path) -> None:
    path, handoff = _empty_phase2(tmp_path)
    finalize_report(handoff, path=path)

    opened = open_completed_report_relaxed(handoff.fence.run_id, path=path)
    _payload, changed = read_report_json(handoff.fence.run_id, path=path)

    assert opened.changed is False
    assert changed is False


@pytest.mark.parametrize("tamper", ["mode", "symlink"])
def test_unsafe_report_json_artifact_still_fails_closed(
    tmp_path: Path, tamper: str,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    finalize_report(handoff, path=path)
    artifact = tmp_path / "output" / "report.json"
    if tamper == "mode":
        artifact.chmod(0o644)
    else:
        victim = tmp_path / "victim.json"
        victim.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(victim)

    with pytest.raises(ReportFinalizationError):
        open_completed_report_relaxed(handoff.fence.run_id, path=path)
    with pytest.raises(AnalystServiceError):
        read_report_json(handoff.fence.run_id, path=path)


@pytest.mark.parametrize("tamper", ["invalid", "foreign"])
def test_invalid_or_foreign_report_json_still_fails_closed(
    tmp_path: Path, tamper: str,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    finalize_report(handoff, path=path)
    artifact = tmp_path / "output" / "report.json"
    if tamper == "invalid":
        artifact.write_text("{invalid", encoding="utf-8")
    else:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        payload["run"]["run_id"] = "foreign-run"
        artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnalystServiceError):
        read_report_json(handoff.fence.run_id, path=path)
