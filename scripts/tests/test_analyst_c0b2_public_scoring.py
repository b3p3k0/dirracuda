"""Pure deterministic tests for common C0B-2 public scoring derivations."""
from __future__ import annotations

import hashlib
import hmac
import json
from fractions import Fraction

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark.c0b2_schema import canonical_json
from scripts.analyst_benchmark.c0b2_public_scoring import (
    PublicScoringError,
    answered_headroom,
    canonical_payload,
    category_metric,
    count_headroom_violating_work,
    derive_category_metrics,
    derive_health_answer_evidence,
    derive_health_nonce,
    derive_nonce,
    document_view_identity,
    f1_fraction,
    fraction_payload,
    fraction_value,
    ordered_reasons,
    require_exact,
    require_hash,
    safe_fraction,
    validate_ordered_subset,
)
from scripts.analyst_benchmark.c0b2_public_schema import ExactFraction, sha256_json


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def test_fraction_helpers_are_exact_reduced_and_fail_closed() -> None:
    assert safe_fraction(4, 8) == Fraction(1, 2)
    assert safe_fraction(7, 0) == Fraction(0, 1)
    assert f1_fraction(2, 1, 2) == Fraction(4, 7)
    assert fraction_payload(Fraction(2, 4)) == {"numerator": 1, "denominator": 2}
    assert fraction_value(fraction_payload(Fraction(-3, 5))) == Fraction(-3, 5)
    assert fraction_value({"numerator": 4, "denominator": 7}) == Fraction(4, 7)
    assert fraction_value(ExactFraction(numerator=0, denominator=1)) == 0

    for values in ((True, 1), (1, False), (-1, 1), (1, -1)):
        with pytest.raises((TypeError, ValueError)):
            safe_fraction(*values)
    with pytest.raises(TypeError):
        fraction_payload(True)
    with pytest.raises(ValidationError):
        fraction_value({"numerator": "1", "denominator": 2})


def test_category_metric_uses_frozen_formula_and_zero_convention() -> None:
    assert category_metric(0, 0, 0) == {
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "precision": {"numerator": 0, "denominator": 1},
        "recall": {"numerator": 0, "denominator": 1},
        "f1": {"numerator": 0, "denominator": 1},
    }
    metric = category_metric(2, 1, 2)
    assert metric["precision"] == {"numerator": 2, "denominator": 3}
    assert metric["recall"] == {"numerator": 1, "denominator": 2}
    assert metric["f1"] == {"numerator": 4, "denominator": 7}


def test_category_aggregates_are_document_level_macro_and_micro() -> None:
    expected = [
        ["pii"],
        ["financial", "contact"],
        [],
        ["demographic"],
    ]
    predicted = [
        ["pii", "contact"],
        ["financial"],
        [],
        [],
    ]
    metrics, summary = derive_category_metrics(expected, predicted)
    assert list(metrics) == ["pii", "financial", "contact", "demographic"]
    assert metrics["contact"]["false_positives"] == 1
    assert metrics["contact"]["false_negatives"] == 1
    assert summary == {
        "macro_f1": {"numerator": 1, "denominator": 2},
        "micro_f1": {"numerator": 4, "denominator": 7},
    }

    with pytest.raises(ValueError):
        derive_category_metrics([["contact", "pii"]], [["pii"]])
    with pytest.raises(ValueError):
        derive_category_metrics([["pii"]], [])


def test_reason_helpers_preserve_exact_enum_order() -> None:
    order = ("invalid", "grounding", "headroom")
    assert ordered_reasons(order, {
        "headroom": True, "invalid": False, "grounding": True,
    }) == ["grounding", "headroom"]
    assert validate_ordered_subset(
        ["grounding", "headroom"], order, label="failures") == (
            "grounding", "headroom")
    with pytest.raises(TypeError):
        ordered_reasons(order, {"invalid": 1})
    with pytest.raises(ValueError):
        ordered_reasons(order, {"unknown": True})
    for invalid in (["headroom", "grounding"], ["grounding", "grounding"], ["other"]):
        with pytest.raises(PublicScoringError):
            validate_ordered_subset(invalid, order, label="failures")


def test_headroom_uses_every_bounded_answer_and_counts_work_not_attempts() -> None:
    assert answered_headroom(
        [4800, 4900], num_predict=2048, num_ctx=8192) == (4900, True)
    assert answered_headroom(
        [5000, 4800], num_predict=2048, num_ctx=8192) == (5000, False)
    assert answered_headroom(
        [], num_predict=2048, num_ctx=8192) == (None, False)
    assert count_headroom_violating_work({
        "work-a": [5000, 6000],
        "work-b": [4800, 4900],
        "work-c": [4916],
    }, num_predict=2048, num_ctx=8192) == 2
    with pytest.raises(PublicScoringError):
        count_headroom_violating_work(
            {"unanswered": []}, num_predict=2048, num_ctx=8192)
    with pytest.raises(TypeError):
        answered_headroom([True], num_predict=2048, num_ctx=8192)


def _health_attempt(
    label: str, state: str, response: str | None, *, prompt_count: int = 4800,
    done_reason: str = "stop", tools_empty: bool = True,
    images_empty: bool = True, unknown_empty: bool = True,
) -> dict[str, object]:
    return {
        "attempt_id": _hash(label),
        "state": state,
        "response": response,
        "metadata": {
            "done_reason": done_reason,
            "prompt_eval_count": prompt_count,
            "tools_empty": tools_empty,
            "images_empty": images_empty,
            "unknown_message_fields_empty": unknown_empty,
        },
    }


def _recovery_attempt(
    label: str, state: str, *, metadata: object = None,
) -> dict[str, object]:
    return {
        "attempt_id": _hash(label),
        "state": state,
        "response": None,
        "metadata": metadata,
    }


def test_health_answer_derivation_retries_and_inspects_every_answer() -> None:
    source = "Public fixture record with SSN 900-12-3456 and sufficient surrounding text."
    valid = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "findings_present",
        "findings": [{
            "category": "pii",
            "quote": "900-12-3456",
            "offset": source.index("900-12-3456"),
        }],
    }, sort_keys=True, separators=(",", ":"))
    invalid = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
        "escaped": True,
    }, sort_keys=True, separators=(",", ":"))
    attempts = [
        _health_attempt(
            "first", "SCHEMA_INVALID", invalid, prompt_count=5000,
            done_reason="length", images_empty=False),
        _health_attempt("retry", "ACCEPTED", valid, prompt_count=4800),
    ]
    assert derive_health_answer_evidence(
        attempts, worksheet="v2", source=source,
        num_predict=2048, num_ctx=8192) == {
            "eventual_valid": True,
            "retained_grounded_pii": True,
            "authoritative_done_reason": "stop",
            "max_answered_prompt_eval_count": 5000,
            "length_outcomes": 1,
            "headroom_passed": False,
            "tools_empty": True,
            "images_empty": False,
            "unknown_message_fields_empty": True,
            "schema_escape_empty": False,
        }


def test_health_answer_derivation_rejects_laundered_or_incomplete_evidence() -> None:
    no_findings = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
    }, sort_keys=True, separators=(",", ":"))
    evidence = derive_health_answer_evidence(
        [_health_attempt("accepted", "ACCEPTED", no_findings)],
        worksheet="v2", source="a public fixture source",
        num_predict=2048, num_ctx=8192)
    assert evidence["eventual_valid"] is True
    assert evidence["retained_grounded_pii"] is False

    contradicted = _health_attempt("wrong-state", "SCHEMA_INVALID", no_findings)
    with pytest.raises(PublicScoringError):
        derive_health_answer_evidence(
            [contradicted], worksheet="v2", source="source",
            num_predict=2048, num_ctx=8192)
    missing_usage = _health_attempt("missing-usage", "ACCEPTED", no_findings)
    del missing_usage["metadata"]["prompt_eval_count"]
    with pytest.raises(PublicScoringError):
        derive_health_answer_evidence(
            [missing_usage], worksheet="v2", source="source",
            num_predict=2048, num_ctx=8192)

    unanswered = _health_attempt("transport", "RETRYABLE_TRANSPORT", None)
    assert derive_health_answer_evidence(
        [unanswered], worksheet="v2", source="source",
        num_predict=2048, num_ctx=8192)["max_answered_prompt_eval_count"] is None
    with pytest.raises(PublicScoringError):
        derive_health_answer_evidence(
            [], worksheet="v2", source="source",
            num_predict=2048, num_ctx=8192)
    with pytest.raises(PublicScoringError):
        derive_health_answer_evidence(
            [
                _health_attempt("accepted-first", "ACCEPTED", no_findings),
                _health_attempt("extra-answer", "ACCEPTED", no_findings),
            ],
            worksheet="v2", source="source",
            num_predict=2048, num_ctx=8192,
        )


def test_health_recovery_states_are_non_answer_history_and_preserve_retry() -> None:
    no_findings = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
    }, sort_keys=True, separators=(",", ":"))
    invalid = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
        "extra": "schema escape",
    }, sort_keys=True, separators=(",", ":"))
    non_answer_metadata = {
        "done_reason": "length",
        "prompt_eval_count": 99999,
        "tools_empty": False,
        "images_empty": False,
        "unknown_message_fields_empty": False,
    }
    attempts = [
        _recovery_attempt("orphan-1", "ORPHANED_UNKNOWN"),
        _recovery_attempt("cancel-1", "CANCELLED_UNVERIFIED"),
        _recovery_attempt(
            "transport-1", "RETRYABLE_TRANSPORT",
            metadata=non_answer_metadata),
        _health_attempt("invalid-answer", "SCHEMA_INVALID", invalid),
        _recovery_attempt("orphan-2", "ORPHANED_UNKNOWN", metadata={}),
        _recovery_attempt("cancel-2", "CANCELLED_UNVERIFIED"),
        _health_attempt("accepted-retry", "ACCEPTED", no_findings),
    ]
    result = derive_health_answer_evidence(
        attempts, worksheet="v2", source="source",
        num_predict=2048, num_ctx=8192)
    assert result["eventual_valid"] is True
    assert result["max_answered_prompt_eval_count"] == 4800
    assert result["length_outcomes"] == 0
    assert result["tools_empty"] is True
    assert result["images_empty"] is True
    assert result["unknown_message_fields_empty"] is True
    assert result["schema_escape_empty"] is False


@pytest.mark.parametrize(
    "attempts",
    (
        [_recovery_attempt("unknown", "UNKNOWN_RECOVERY")],
        [{
            **_recovery_attempt("orphan-response", "ORPHANED_UNKNOWN"),
            "response": "{}",
        }],
        [_recovery_attempt(
            "cancel-metadata", "CANCELLED_UNVERIFIED", metadata="not-an-object")],
    ),
)
def test_health_recovery_states_reject_malformed_evidence(
    attempts: list[dict[str, object]],
) -> None:
    with pytest.raises(PublicScoringError):
        derive_health_answer_evidence(
            attempts, worksheet="v2", source="source",
            num_predict=2048, num_ctx=8192)


def test_health_history_rejects_recovery_after_answer_terminal() -> None:
    no_findings = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
    }, sort_keys=True, separators=(",", ":"))
    invalid = json.dumps({
        "document_type": "record",
        "subject": "public fixture",
        "assessment": "no_findings",
        "findings": [],
        "extra": True,
    }, sort_keys=True, separators=(",", ":"))
    for attempts in (
        [
            _health_attempt("accepted-terminal", "ACCEPTED", no_findings),
            _recovery_attempt("orphan-after-accepted", "ORPHANED_UNKNOWN"),
        ],
        [
            _health_attempt("invalid-1", "SCHEMA_INVALID", invalid),
            _health_attempt("invalid-2", "SCHEMA_INVALID", invalid),
            _recovery_attempt("cancel-after-invalid", "CANCELLED_UNVERIFIED"),
        ],
    ):
        with pytest.raises(PublicScoringError):
            derive_health_answer_evidence(
                attempts, worksheet="v2", source="source",
                num_predict=2048, num_ctx=8192)


def test_document_view_identity_uses_mutually_exclusive_domains() -> None:
    digest = _hash("document")
    assert document_view_identity(
        doc_id="doc-1", document_sha256=digest, pair_id="pair-1") == "pair:pair-1"
    assert document_view_identity(
        doc_id="doc-1", document_sha256=digest,
        view_sha256=_hash("view")) == f"view:{_hash('view')}"
    assert document_view_identity(
        doc_id="doc-1", document_sha256=digest) == f"doc:doc-1:{digest}"
    with pytest.raises(ValueError):
        document_view_identity(
            doc_id="doc-1", document_sha256=digest,
            pair_id="pair-1", view_sha256=_hash("view"))
    with pytest.raises(ValueError):
        document_view_identity(doc_id="doc-1", document_sha256="not-a-hash")


@pytest.mark.parametrize("doc_id", (True, 1, 1.0, "", None))
def test_document_view_identity_rejects_non_string_document_ids(
    doc_id: object,
) -> None:
    with pytest.raises(ValueError):
        document_view_identity(doc_id=doc_id, document_sha256=_hash("document"))


@pytest.mark.parametrize("pair_id", (True, 1, 1.0, ""))
def test_document_view_identity_rejects_non_string_pair_ids(
    pair_id: object,
) -> None:
    with pytest.raises(ValueError):
        document_view_identity(
            doc_id="doc-1", document_sha256=_hash("document"), pair_id=pair_id)


def test_nonce_derivation_matches_frozen_hmac_bytes_and_separates_health() -> None:
    key = bytes(range(32))
    identity = f"doc:doc-1:{_hash('document')}"
    nonce = derive_nonce(
        key,
        nonce_domain="F",
        document_view_identity=identity,
        seed=17,
        worksheet="v2",
    )
    message = canonical_json({
        "document_view_identity": identity,
        "domain": "c0b2-nonce-v1",
        "nonce_domain": "F",
        "seed": 17,
        "worksheet": "v2",
    })
    expected = "FENCE_" + hmac.new(
        key, message, hashlib.sha256).digest()[:16].hex().upper()
    assert nonce == expected
    assert nonce == derive_nonce(
        key,
        nonce_domain="F",
        document_view_identity=identity,
        seed=17,
        worksheet="v2",
    )
    health = derive_health_nonce(
        key,
        candidate_id=_hash("candidate"),
        document_view_identity=identity,
        worksheet="v2",
    )
    assert health.startswith("FENCE_") and health != nonce
    with pytest.raises(ValueError):
        derive_nonce(
            b"short",
            nonce_domain="F",
            document_view_identity=identity,
            seed=17,
            worksheet="v2",
        )


@pytest.mark.parametrize("seed", (True, 1.0, "1", None))
def test_nonce_derivation_rejects_coerced_seeds(seed: object) -> None:
    with pytest.raises(ValueError):
        derive_nonce(
            bytes(range(32)),
            nonce_domain="F",
            document_view_identity=f"doc:doc-1:{_hash('document')}",
            seed=seed,
            worksheet="v2",
        )


@pytest.mark.parametrize("identity", (True, 1, 1.0, "", None))
def test_nonce_derivation_rejects_non_string_identities(identity: object) -> None:
    with pytest.raises(ValueError):
        derive_nonce(
            bytes(range(32)),
            nonce_domain="F",
            document_view_identity=identity,
            seed=1,
            worksheet="v2",
        )
    with pytest.raises(ValueError):
        derive_health_nonce(
            bytes(range(32)),
            candidate_id=_hash("candidate"),
            document_view_identity=identity,
            worksheet="v2",
        )


def test_nonce_derivation_rejects_other_coerced_scalars() -> None:
    key = bytes(range(32))
    identity = f"doc:doc-1:{_hash('document')}"
    for changes in (
        {"nonce_domain": True},
        {"worksheet": True},
        {"run_nonce_key": bytearray(key)},
    ):
        values = {
            "run_nonce_key": key,
            "nonce_domain": "F",
            "document_view_identity": identity,
            "seed": 1,
            "worksheet": "v2",
            **changes,
        }
        with pytest.raises(ValueError):
            derive_nonce(
                values["run_nonce_key"],
                nonce_domain=values["nonce_domain"],
                document_view_identity=values["document_view_identity"],
                seed=values["seed"],
                worksheet=values["worksheet"],
            )


def test_exact_equality_and_hash_primitives_reject_drift() -> None:
    value = {"b": [2, 3], "a": {"numerator": 1, "denominator": 2}}
    equivalent = {"a": {"denominator": 2, "numerator": 1}, "b": [2, 3]}
    expected_hash = sha256_json(value)
    assert require_exact(value, equivalent, label="aggregate") == expected_hash
    require_hash(value, expected_hash, label="aggregate")
    assert canonical_payload(value) == value
    with pytest.raises(PublicScoringError):
        require_exact(value, {**value, "b": [3, 2]}, label="aggregate")
    with pytest.raises(PublicScoringError):
        require_hash(value, _hash("wrong"), label="aggregate")
    with pytest.raises(TypeError):
        canonical_payload([value])
