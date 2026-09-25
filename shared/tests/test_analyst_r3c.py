"""R3c report.json finalization, fallback, and bounded-ranking acceptance."""

from __future__ import annotations

import json
from pathlib import Path

from experimental.analyst.report import finalize_report
from experimental.analyst.report_contract import (
    MAX_REPORT_JSON_FACTS,
    REPORT_ARTIFACT_NAMES,
)
from experimental.analyst.report_json import (
    HostRead,
    TopExposure,
    dumps_report,
    validate_report_json,
)
from experimental.analyst.store import open_connection, write_host_read
from shared.tests.test_analyst_c12 import _reviewed_phase2


_NO_FINDINGS = json.dumps({
    "document_type": "Public note",
    "subject": "Synthetic",
    "assessment": "no_findings",
    "findings": [],
}, separators=(",", ":"))


def _reviewed(tmp_path: Path):
    return _reviewed_phase2(tmp_path, "ordinary synthetic words", _NO_FINDINGS)


def _detector_value(kind: str, index: int) -> str:
    """One distinct, plausible value per hit.

    F2 screens a value against published allocation rules, so a placeholder
    like "value-0" now reports as suspect and ranks down. F1 folds repeats, so
    every value must also be distinct for the cap to be exercised.
    """
    if kind == "ssn":
        return f"422-52-{5000 + index:04d}"
    if kind == "phone":
        return f"210-234-{5000 + index:04d}"
    if kind == "dob":
        return f"03/{1 + index % 28:02d}/19{50 + index % 40:02d}"
    return f"value-{index}"


def _insert_detector_hits(path: Path, kinds: tuple[str, ...]) -> None:
    conn = open_connection(path)
    try:
        file_id = int(conn.execute(
            "SELECT file_id FROM analyst_files",
        ).fetchone()[0])
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO analyst_detector_hits("
            "file_id,ordinal,kind,value,start_char,end_char) VALUES(?,?,?,?,?,?)",
            (
                (file_id, index, kind, _detector_value(kind, index),
                 index * 20,
                 index * 20 + len(_detector_value(kind, index)))
                for index, kind in enumerate(kinds)
            ),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _payload(tmp_path: Path) -> dict[str, object]:
    raw = (tmp_path / "output" / "report.json").read_bytes()
    payload = json.loads(raw)
    validate_report_json(payload)
    assert raw == dumps_report(payload).encode("utf-8")
    return payload


def test_finalized_report_json_validates_and_fallback_is_deterministic(
    tmp_path: Path,
) -> None:
    path, handoff = _reviewed(tmp_path)
    _insert_detector_hits(path, ("ssn",))

    result = finalize_report(handoff, path=path)
    payload = _payload(tmp_path)

    assert tuple(item.name for item in result.manifest.artifacts) \
        == REPORT_ARTIFACT_NAMES
    assert payload["read"] == {
        "unverified_notice": "Model's read - not verified. Facts below are grounded.",
        "host_summary": "Automated read unavailable. 1 files reviewed, 1 flagged.",
        "likely_owner": None,
        "contacts": [],
        "risk_level": "HIGH",
        "top_exposures": [{
            "rank": 1,
            "severity": "HIGH",
            "text": f"SSN in {payload['facts'][0]['file']}",
        }],
    }


def test_finalized_report_json_uses_persisted_model_read(tmp_path: Path) -> None:
    path, handoff = _reviewed(tmp_path)
    read = HostRead(
        host_summary="The host contains a synthetic public note.",
        likely_owner="Example Operations",
        contacts=("ops@example.test",),
        risk_level="MED",
        top_exposures=(TopExposure(1, "MED", "Synthetic public note"),),
    )
    write_host_read(
        handoff.fence,
        read,
        read_mode="full",
        files_read=1,
        files_total=1,
        flagged_files=0,
        now_utc="2026-09-18T16:00:00Z",
        path=path,
    )

    finalize_report(handoff, path=path)
    payload = _payload(tmp_path)

    assert payload["read"]["host_summary"] == read.host_summary
    assert payload["read"]["likely_owner"] == read.likely_owner
    assert payload["read"]["contacts"] == list(read.contacts)
    assert payload["read"]["top_exposures"] == [{
        "rank": 1,
        "severity": "MED",
        "text": "Synthetic public note",
    }]


def test_report_json_facts_are_ranked_bounded_and_jsonl_stays_full(
    tmp_path: Path,
) -> None:
    path, handoff = _reviewed(tmp_path)
    kinds = ("phone",) * 10 + ("dob",) * 10 + ("ssn",) * 490
    _insert_detector_hits(path, kinds)

    finalize_report(handoff, path=path)
    payload = _payload(tmp_path)
    facts = payload["facts"]

    assert len(facts) == MAX_REPORT_JSON_FACTS
    assert [fact["rank"] for fact in facts] == ["HIGH"] * 490 + ["MED"] * 10
    jsonl = (tmp_path / "output" / "findings.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    assert len(jsonl) == len(kinds)
    assert sum(json.loads(line)["evidence_kind"] == "detector" for line in jsonl) \
        == len(kinds)
