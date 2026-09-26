"""AN1 reliable semantic host READ acceptance tests."""

from __future__ import annotations

import json

from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    READ_MAX_SOURCE_CHARS,
    READ_NUM_CTX,
    READ_NUM_PREDICT,
    OllamaStatus,
    PromptKind,
    build_read_chat_request,
    build_read_repair_chat_request,
    validate_chat_request,
)
from experimental.analyst.read_reduce import _render_summary
from experimental.analyst.read_worksheet import (
    MAX_HOST_SUMMARY_CHARS,
    build_read_repair_prompt,
    parse_read,
)
from experimental.analyst.report_json import (
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
)
from shared.tests.test_analyst_c9_client import (
    FakeResponse,
    FakeSession,
    _chat_wire,
)


NONCE = "FENCE_0123456789ABCDEF"
DIGEST = "a" * 64


def _read_object(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "host_summary": "S" * (MAX_HOST_SUMMARY_CHARS + 200),
        "likely_owner": "  Example Research Family  ",
        "contacts": [
            "owner@example.test", "", "owner@example.test", 15550100,
            *[f"contact-{index}" for index in range(20)],
        ],
        "risk_level": "medium",
        "top_exposures": [
            {"severity": "high", "text": f" item {index} ", "notes": "drop"}
            for index in range(6)
        ] + [{"severity": "LOW", "text": ""}],
        "reasoning": "unknown top-level fields are ignored",
    }
    value.update(changes)
    return value


def test_parse_read_extracts_normalizes_and_bounds_real_model_output() -> None:
    raw = (
        "<think>discard malformed {reasoning}</think>\n```json\n"
        + json.dumps(_read_object())
        + "\n```\ntrailing prose"
    )
    read = parse_read(raw)

    assert len(read.host_summary) == MAX_HOST_SUMMARY_CHARS
    assert read.likely_owner == "Example Research Family"
    assert read.risk_level == "MED"
    assert read.contacts[:2] == ("owner@example.test", "15550100")
    assert len(read.contacts) == 10
    assert len(set(read.contacts)) == len(read.contacts)
    assert [item.rank for item in read.top_exposures] == [1, 2, 3, 4, 5]
    assert read.top_exposures[0].text == "item 0"


def test_read_repair_prompt_and_request_use_read_specific_profile() -> None:
    source = "x" * READ_MAX_SOURCE_CHARS
    primary = build_read_chat_request(source, nonce=NONCE)
    repair = build_read_repair_chat_request(source, nonce=NONCE)

    validate_chat_request(primary)
    validate_chat_request(repair)
    assert primary.prompt_kind is PromptKind.PRIMARY
    assert repair.prompt_kind is PromptKind.MODEL_INVALID_REPAIR
    assert repair.payload()["options"]["num_ctx"] == READ_NUM_CTX
    assert repair.payload()["options"]["num_predict"] == READ_NUM_PREDICT
    assert "return ONLY the JSON object" in build_read_repair_prompt(
        source, nonce=NONCE,
    )


def test_production_transport_leaves_read_shape_for_tolerant_parser() -> None:
    content = "analysis before output\n" + json.dumps(_read_object(
        host_summary="Synthetic semantic host.",
        contacts=[],
        top_exposures=[],
    ))
    client = OllamaClient(session=FakeSession(FakeResponse([_chat_wire(content)])))
    request = build_read_chat_request("synthetic input", nonce=NONCE)
    result = client.chat(
        request,
        expected_sha256=request.request_sha256,
        cancel=lambda: False,
    )

    assert result.status is OllamaStatus.SUCCESS
    assert parse_read(result.content).host_summary == "Synthetic semantic host."


def test_rendered_read_input_contains_inventory_and_more_than_16_fact_values() -> None:
    files = [
        {
            "relative_path": "credentials-and-medical.txt",
            "format_name": "text",
            "terminal_code": "complete_model_reviewed",
            "stage": "model_response_valid",
            "detector_kinds": "email",
            "model_categories": "contact",
            "finding_count": 20,
        },
        {
            "relative_path": "family/minor-genetics.pdf",
            "format_name": "pdf",
            "terminal_code": "complete_detector_only",
            "stage": "detector_scanned",
            "detector_kinds": None,
            "model_categories": None,
            "finding_count": 0,
        },
        {
            "relative_path": "archive/ordinary.csv",
            "format_name": "csv",
            "terminal_code": "complete_detector_only",
            "stage": "detector_scanned",
            "detector_kinds": None,
            "model_categories": None,
            "finding_count": 0,
        },
    ]
    facts = [
        {
            "relative_path": "credentials-and-medical.txt",
            "category": "contact",
            "kind": "email",
            "value": f"person-{index}@example.test",
        }
        for index in range(20)
    ]
    chunks = [{
        "relative_path": "credentials-and-medical.txt",
        "document_type": "credential and medical notes",
        "subject": "Example household",
        "assessment": "findings_present",
    }]

    rendered = _render_summary(
        files,
        chunks,
        {"pii": 0, "financial": 0, "contact": 20, "demographic": 0},
        facts,
    )

    assert len(rendered) <= READ_MAX_SOURCE_CHARS
    assert all(row["relative_path"] in rendered for row in files)
    assert "format_name=\"pdf\"" in rendered
    assert "terminal_code=\"complete_model_reviewed\"" in rendered
    assert "model_reviewed=yes" in rendered
    assert "finding_count=20" in rendered
    assert "person-19@example.test" in rendered


def test_report_preserves_semantic_exposure_and_applies_grounded_floor() -> None:
    run = RunMeta(
        "run", "Synthetic", "full", "model:local", DIGEST,
        "2026-09-19T00:00:00Z", 1, 1, 1,
    )
    read = HostRead(
        "A family archive.", None, (), "MED",
        (TopExposure(1, "LOW", "Genetic notes for a minor in family/genetics.pdf"),),
    )
    facts = (GroundedFact(
        "ssn", "pii", "000-00-0000", "identity.txt", "line 1", "HIGH", "detector",
    ),)

    report = build_report_json(run, read, facts, Coverage(1, 1, 0, 0, 0))
    assert report["read"]["risk_level"] == "HIGH"
    assert report["read"]["top_exposures"] == [{
        "rank": 1,
        "severity": "LOW",
        "text": "Genetic notes for a minor in family/genetics.pdf",
    }]
