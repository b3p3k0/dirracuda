"""Offline tests for the bounded grounded-duplicate recovery."""
from __future__ import annotations

import json
import unicodedata

import pytest

from scripts.analyst_benchmark import c0b2_schema
from scripts.analyst_benchmark import c0b4_answer as answer


def _finding(category: str, quote: str, source: str, *, offset: int | None = None
             ) -> dict[str, object]:
    return {
        "category": category,
        "quote": quote,
        "offset": source.index(quote) if offset is None else offset,
    }


def _raw(source: str, findings: list[dict[str, object]], *,
         assessment: str = "findings_present") -> str:
    return json.dumps({
        "document_type": "record",
        "subject": "subject",
        "assessment": assessment,
        "findings": findings,
    }, ensure_ascii=False)


def test_prompt_is_separate_and_adds_only_the_frozen_instruction() -> None:
    source, nonce = "Record 900-12-3456", "FENCE_" + "A" * 32
    legacy = c0b2_schema.build_prompt("v2", source, nonce)
    current = answer.build_prompt("v2", source, nonce)
    assert answer.PROMPT_DELTA in current
    assert answer.PROMPT_DELTA not in legacy
    assert current.replace(f"  7. {answer.PROMPT_DELTA}\n", "") == legacy
    assert answer.prompt_template_hash("v2") != \
        c0b2_schema.prompt_template_hash("v2")
    with pytest.raises(ValueError, match="worksheet v2"):
        answer.build_prompt("v1", source, nonce)


def test_zero_duplicate_answer_remains_raw_valid() -> None:
    source = "Identifiers: 900-12-3456 and person@example.test."
    raw = _raw(source, [
        _finding("pii", "900-12-3456", source),
        _finding("contact", "person@example.test", source),
    ])
    result = answer.assess_answer("v2", raw, source)
    assert result.final_outcome == "RAW_VALID"
    assert result.raw_counts.as_dict() == {
        "findings": 2, "grounded_findings": 2,
        "first_pass_valid": True, "semantic_invalid_attempts": 0,
    }
    assert result.retained_counts.as_dict() == {
        "findings": 2, "grounded_findings": 2, "eventual_valid": True,
    }
    assert result.removed_finding_indices == ()


def test_one_nfc_duplicate_is_removed_in_stable_first_order() -> None:
    decomposed = "Cafe\u0301"
    composed = unicodedata.normalize("NFC", decomposed)
    source = f"Values: {decomposed}; {composed}; 900-12-3456."
    findings = [
        _finding("contact", decomposed, source, offset=999),
        _finding("pii", "900-12-3456", source),
        _finding("contact", composed, source),
    ]
    result = answer.assess_answer("v2", _raw(source, findings), source)
    assert result.final_outcome == "NORMALIZED_DUPLICATE"
    assert result.schema_retry_allowed is False
    assert result.semantic_errors == ("duplicate_evidence",)
    assert result.removed_finding_indices == (2,)
    assert result.redundant_rows == 1
    assert result.raw_counts.findings == 3
    assert result.raw_counts.grounded_findings == 3
    assert result.raw_counts.first_pass_valid is False
    assert result.raw_counts.semantic_invalid_attempts == 1
    assert result.retained_counts == answer.RetainedCounts(2, 2, True)
    assert result.retained_value["findings"] == findings[:2]
    assert result.grounding[0].canonical_offset == source.index(decomposed)
    assert result.grounding[0].model_offset_exact is False


def test_same_quote_in_different_categories_is_not_a_duplicate() -> None:
    source = "Identifier 900-12-3456 appears once."
    quote = "900-12-3456"
    result = answer.assess_answer("v2", _raw(source, [
        _finding("pii", quote, source),
        _finding("financial", quote, source),
    ]), source)
    assert result.final_outcome == "RAW_VALID"
    assert result.redundant_rows == 0


@pytest.mark.parametrize("findings", [
    lambda source: [
        _finding("pii", "900-12-3456", source),
        _finding("pii", "900-12-3456", source),
        _finding("pii", "900-12-3456", source),
    ],
    lambda source: [
        _finding("pii", "900-12-3456", source),
        _finding("pii", "900-12-3456", source),
        _finding("contact", "person@example.test", source),
        _finding("contact", "person@example.test", source),
    ],
])
def test_two_redundant_rows_are_not_recoverable(findings) -> None:
    source = "Values 900-12-3456 and person@example.test are repeated in evidence."
    result = answer.assess_answer("v2", _raw(source, findings(source)), source)
    assert result.final_outcome == "INVALID"
    assert result.redundant_rows == 2
    assert result.retained_value is None


def test_duplicate_plus_another_semantic_error_is_not_recoverable() -> None:
    source = "Value 900-12-3456 is present."
    finding = _finding("pii", "900-12-3456", source)
    result = answer.assess_answer(
        "v2", _raw(source, [finding, finding], assessment="no_findings"), source)
    assert result.structural_valid is True
    assert result.semantic_errors == (
        "duplicate_evidence", "assessment_finding_agreement")
    assert result.final_outcome == "INVALID"
    assert result.raw_counts.grounded_findings == 2


def test_ungrounded_duplicate_is_a_quality_failure_without_normalization() -> None:
    source = "This source does not contain the claimed identifier."
    finding = {"category": "pii", "quote": "900-12-3456", "offset": 0}
    result = answer.assess_answer("v2", _raw(source, [finding, finding]), source)
    assert result.semantic_errors == ("duplicate_evidence",)
    assert result.raw_counts == answer.RawCounts(2, 0, False, 1)
    assert result.final_outcome == "INVALID"
    assert result.removed_finding_indices == ()
    assert result.schema_retry_allowed is False


def test_over_16_is_structural_failure_before_grounding() -> None:
    source = "Identifiers " + " ".join(f"id-{index}" for index in range(17))
    findings = [_finding("pii", f"id-{index}", source) for index in range(17)]
    result = answer.assess_answer("v2", _raw(source, findings), source)
    assert result.structural_valid is False
    assert result.final_outcome == "INVALID"
    assert result.grounding == ()
    assert result.raw_value is None


def test_raw_and_retained_values_are_defensive_copies() -> None:
    source = "Value 900-12-3456 is present."
    finding = _finding("pii", "900-12-3456", source)
    result = answer.assess_answer("v2", _raw(source, [finding, finding]), source)
    first = result.raw_value
    assert first is not None
    first["findings"].clear()
    assert len(result.raw_value["findings"]) == 2
    retained = result.retained_value
    assert retained is not None
    retained["findings"][0]["quote"] = "changed"
    assert result.retained_value["findings"][0]["quote"] == "900-12-3456"
