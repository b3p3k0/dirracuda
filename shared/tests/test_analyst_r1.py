"""R1 pure read-first model and report contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from experimental.analyst import read_worksheet
from experimental.analyst.read_worksheet import (
    EXPECTED_FACTS_PROMPT_TEMPLATE_SHA256,
    EXPECTED_FACTS_SCHEMA_SHA256,
    EXPECTED_READ_PROMPT_TEMPLATE_SHA256,
    EXPECTED_READ_REPAIR_PROMPT_TEMPLATE_SHA256,
    EXPECTED_READ_SCHEMA_SHA256,
    build_facts_prompt,
    build_read_prompt,
    facts_prompt_template_hash,
    facts_schema_hash,
    locate_quote,
    parse_facts,
    parse_read,
    read_prompt_template_hash,
    read_repair_prompt_template_hash,
    read_schema_hash,
    validate_facts,
    validate_read,
)
from experimental.analyst.report_json import (
    REPORT_SCHEMA_VERSION,
    UNVERIFIED_NOTICE,
    Coverage,
    GroundedFact,
    HostRead,
    ReportVersionError,
    RunMeta,
    TopExposure,
    build_report_json,
    dumps_report,
    rank_fact,
    reconcile_risk,
    validate_report_json,
)


NONCE = "FENCE_0123456789ABCDEF"
DIGEST = "a" * 64


def _facts_answer(*, findings: list[dict] | None = None, **extra: object) -> str:
    return json.dumps({
        "document_gist": "Synthetic account directory.",
        "findings": findings if findings is not None else [
            {"category": "contact", "quote": "ALPHA-CONTACT", "offset": 0}
        ],
        **extra,
    })


def _read_answer(**extra: object) -> str:
    return json.dumps({
        "host_summary": "Synthetic records host used for contract testing.",
        "likely_owner": "Example Lab",
        "contacts": ["ALPHA-CONTACT"],
        "risk_level": "MED",
        "top_exposures": [
            {"severity": "MED", "text": "Synthetic identity records"}
        ],
        **extra,
    })


def _fact(
    kind: str,
    rank: str,
    *,
    category: str = "pii",
    source: str = "detector",
) -> GroundedFact:
    return GroundedFact(
        kind=kind,
        category=category,
        quote=f"SYNTHETIC-{kind}",
        file=f"synthetic-{kind}.txt",
        provenance="line 1",
        rank=rank,
        source=source,
    )


def test_sha_pins_are_stable_and_prompt_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert facts_schema_hash() == EXPECTED_FACTS_SCHEMA_SHA256
    assert read_schema_hash() == EXPECTED_READ_SCHEMA_SHA256
    assert facts_prompt_template_hash() == EXPECTED_FACTS_PROMPT_TEMPLATE_SHA256
    assert read_prompt_template_hash() == EXPECTED_READ_PROMPT_TEMPLATE_SHA256
    assert (
        read_repair_prompt_template_hash()
        == EXPECTED_READ_REPAIR_PROMPT_TEMPLATE_SHA256
    )
    assert all(len(value) == 64 for value in (
        EXPECTED_FACTS_SCHEMA_SHA256,
        EXPECTED_READ_SCHEMA_SHA256,
        EXPECTED_FACTS_PROMPT_TEMPLATE_SHA256,
        EXPECTED_READ_PROMPT_TEMPLATE_SHA256,
        EXPECTED_READ_REPAIR_PROMPT_TEMPLATE_SHA256,
    ))

    monkeypatch.setattr(
        read_worksheet,
        "_FACTS_INSTRUCTIONS",
        read_worksheet._FACTS_INSTRUCTIONS + "drift",
    )
    with pytest.raises(RuntimeError, match="FACTS prompt drifted"):
        facts_prompt_template_hash()
    monkeypatch.setattr(read_worksheet, "EXPECTED_FACTS_SCHEMA_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="FACTS response schema drifted"):
        facts_schema_hash()


@pytest.mark.parametrize(
    "builder",
    [build_facts_prompt, build_read_prompt],
)
def test_prompts_fence_untrusted_content_with_caller_nonce(builder) -> None:
    prompt = builder("SYNTHETIC INPUT", nonce=NONCE)
    assert f"<<<{NONCE}\n" in prompt
    assert f"\n{NONCE}>>>" in prompt
    assert "untrusted data" in prompt
    assert "SYNTHETIC INPUT" in prompt


def test_response_schemas_accept_valid_json_and_are_strictly_bounded() -> None:
    facts = validate_facts(_facts_answer())
    read = validate_read(_read_answer())
    assert facts.document_gist == "Synthetic account directory."
    assert read.risk_level == "MED"

    with pytest.raises(ValidationError):
        validate_facts(_facts_answer(unexpected=True))
    with pytest.raises(ValidationError):
        validate_facts(_facts_answer(findings=[{
            "category": "pii",
            "quote": "X" * 241,
            "offset": 0,
        }]))
    with pytest.raises(ValidationError):
        validate_read(_read_answer(host_summary="X" * 1201))
    with pytest.raises(ValidationError):
        validate_read(_read_answer(contacts=[str(index) for index in range(11)]))


def test_locate_quote_uses_first_exact_match_and_fraction_guard() -> None:
    source = "prefix VALUE middle VALUE suffix"
    assert locate_quote(source, "VALUE") == (7, 12)
    assert locate_quote(source, "ABSENT") is None

    long_source = "A" * 64
    assert locate_quote(long_source, "A" * 39) is None
    assert locate_quote(long_source, "A" * 38) == (0, 38)


def test_parse_facts_drops_ungrounded_and_collapses_nfc_duplicates() -> None:
    decomposed = "Cafe\u0301"
    raw = _facts_answer(findings=[
        {"category": "contact", "quote": decomposed, "offset": 999},
        {"category": "contact", "quote": "Caf\u00e9", "offset": 0},
        {"category": "pii", "quote": "NOT-IN-SOURCE", "offset": 0},
    ])
    gist, facts = parse_facts(raw, f"Synthetic label: {decomposed}.")
    assert gist == "Synthetic account directory."
    assert len(facts) == 1
    assert facts[0].quote == decomposed
    assert facts[0].source == "model"
    assert facts[0].provenance.startswith("characters ")


def test_parse_read_assigns_canonical_one_based_exposure_ranks() -> None:
    parsed = parse_read(_read_answer(top_exposures=[
        {"severity": "HIGH", "text": "Synthetic account identifier"},
        {"severity": "MED", "text": "Synthetic record cluster"},
    ]))
    assert isinstance(parsed, HostRead)
    assert [item.rank for item in parsed.top_exposures] == [1, 2]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("ssn", "HIGH"),
        ("passport", "HIGH"),
        ("card", "HIGH"),
        ("routing", "HIGH"),
        ("iban", "HIGH"),
        ("bank_account", "HIGH"),
        ("dob", "MED"),
        ("email", "low"),
        ("phone", "low"),
        ("demographic_term", "low"),
        ("generic", "low"),
    ],
)
def test_rank_fact_maps_documented_detector_kinds(kind: str, expected: str) -> None:
    assert rank_fact(kind, "contact", "detector") == expected


@pytest.mark.parametrize("category", ["financial", "pii"])
def test_rank_fact_promotes_model_financial_and_pii(category: str) -> None:
    assert rank_fact("model", category, "model") == "MED"


def test_high_grounded_fact_forces_high_risk() -> None:
    assert reconcile_risk("LOW", (_fact("ssn", "HIGH"),)) == "HIGH"


def test_build_report_has_exact_shape_sorted_facts_and_model_exposures() -> None:
    run = RunMeta(
        run_id="synthetic-run",
        report_label="Synthetic Host",
        read_mode="quick",
        model_tag="synthetic-model:1",
        model_digest=DIGEST,
        created_at_utc="2026-09-18T00:00:00Z",
        files_read=3,
        files_total=4,
        flagged_files=2,
    )
    read = HostRead(
        host_summary="Synthetic records host.",
        likely_owner="Example Lab",
        contacts=("ALPHA-CONTACT",),
        risk_level="LOW",
        top_exposures=(
            TopExposure(8, "HIGH", "Synthetic account identifier"),
            TopExposure(9, "LOW", "Incidental synthetic contact"),
            TopExposure(10, "MED", "Synthetic identity cluster"),
        ),
    )
    facts = (
        _fact("phone", "low", category="contact"),
        _fact("dob", "MED"),
        _fact("ssn", "HIGH"),
    )
    coverage = Coverage(4, 4, 0, 0, 1)

    report = build_report_json(run, read, facts, coverage)
    assert set(report) == {
        "report_schema_version", "run", "read", "facts", "coverage",
        "affiliations",
    }
    assert report["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert report["read"]["unverified_notice"] == UNVERIFIED_NOTICE
    assert report["read"]["risk_level"] == "HIGH"
    assert report["read"]["top_exposures"] == [
        {"rank": 1, "severity": "HIGH", "text": "Synthetic account identifier"},
        {"rank": 2, "severity": "LOW", "text": "Incidental synthetic contact"},
        {"rank": 3, "severity": "MED", "text": "Synthetic identity cluster"},
    ]
    assert [item["rank"] for item in report["facts"]] == ["HIGH", "MED", "low"]
    validate_report_json(report)
    assert json.loads(dumps_report(report)) == report


@pytest.mark.parametrize(
    "obj",
    [
        {},
        {"report_schema_version": 999},
    ],
)
def test_validate_report_refuses_missing_or_unknown_version(obj: dict) -> None:
    with pytest.raises(ReportVersionError):
        validate_report_json(obj)
