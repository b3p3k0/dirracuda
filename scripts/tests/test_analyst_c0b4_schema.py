"""Offline strict-schema tests for the separate C0B-4 artifact family."""
from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark import c0b4_schema as schema
from scripts.analyst_benchmark.c0b2_public_schema import sha256_json


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _binding() -> dict[str, str]:
    return schema.identity_fields(_hash("protocol"))


def _candidate() -> dict[str, object]:
    return {
        "model": "qwen3.6:27b",
        "model_digest":
            "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
        "worksheet": "v2", "chunk_chars": 8000, "overlap": 256,
        "num_ctx": 8192, "num_predict": 1024,
    }


def _dedup_body() -> dict[str, object]:
    return {
        **_binding(), "version": "c0b4-dedup-evidence-v1",
        "work_id": _hash("work"), "attempt_id": _hash("attempt"),
        "raw_response_sha256": _hash("raw"),
        "dedupe_key": "category+nfc_quote", "removed_index": 1,
        "raw_counts": {
            "findings": 2, "grounded_findings": 2,
            "first_pass_valid": False, "semantic_invalid_attempts": 1,
        },
        "retained_counts": {
            "findings": 1, "grounded_findings": 1, "eventual_valid": True,
        },
    }


def _dedup() -> dict[str, object]:
    body = _dedup_body()
    return {**body, "evidence_sha256": sha256_json(body)}


def _lane_hashes(*, complete: bool = True) -> dict[str, object]:
    return {
        "f72_seed17_sha256": _hash("seed17"),
        "f72_seed20260804_sha256": _hash("seed20260804") if complete else None,
        "c44_scored_sha256": _hash("c44") if complete else None,
    }


def _selection() -> dict[str, object]:
    return _candidate()


def test_exact_candidate_and_common_identity_reject_coercion_and_extra() -> None:
    assert schema.Candidate.model_validate(
        _candidate(), strict=True).worksheet == "v2"
    with pytest.raises(ValidationError):
        schema.Candidate.model_validate(
            {**_candidate(), "num_ctx": "8192"}, strict=True)
    value = _dedup()
    for changed in (
        {key: item for key, item in value.items() if key != "protocol_sha256"},
        {**value, "policy_id": "c0b3-assistive-bounded-fp-v1"},
        {**value, "unknown": True},
        {**value, "version": "c0b3-dedup-evidence-v1"},
    ):
        with pytest.raises((ValueError, ValidationError)):
            schema.validate_artifact(changed)


def test_self_hash_omits_only_its_own_field() -> None:
    value = _dedup()
    assert schema.validate_artifact(value) == value
    changed = deepcopy(value)
    changed["removed_index"] = 0
    with pytest.raises(ValidationError, match="evidence_sha256"):
        schema.DedupEvidence.model_validate(changed, strict=True)
    changed = deepcopy(value)
    changed["evidence_sha256"] = sha256_json({
        key: item for key, item in value.items()
        if key not in {"evidence_sha256", "protocol_sha256"}
    })
    with pytest.raises(ValidationError, match="evidence_sha256"):
        schema.DedupEvidence.model_validate(changed, strict=True)


@pytest.mark.parametrize("change", [
    {"first_pass_valid": True},
    {"semantic_invalid_attempts": 0},
    {"grounded_findings": 1},
])
def test_dedup_raw_counters_remain_honest(change) -> None:
    body = _dedup_body()
    body["raw_counts"] = {**body["raw_counts"], **change}
    value = {**body, "evidence_sha256": sha256_json(body)}
    with pytest.raises(ValidationError):
        schema.DedupEvidence.model_validate(value, strict=True)


def test_recovery_counters_require_sorted_unique_matching_censuses() -> None:
    first, second = _hash("a"), _hash("b")
    valid = {
        "redundant_rows": 2,
        "affected_work_ids": sorted([first, second]),
        "affected_chunk_count": 2,
        "affected_document_ids": ["doc-a", "doc-b"],
        "affected_document_count": 2,
        "normalized_duplicate_chunks": 2,
    }
    assert schema.RecoveryCounters.model_validate(
        valid, strict=True).affected_chunk_count == 2
    for changed in (
        {**valid, "affected_work_ids": list(reversed(valid["affected_work_ids"]))},
        {**valid, "affected_chunk_count": 1},
        {**valid, "affected_document_ids": ["doc-b", "doc-a"]},
        {**valid, "normalized_duplicate_chunks": 0},
    ):
        with pytest.raises(ValidationError):
            schema.RecoveryCounters.model_validate(changed, strict=True)
    with pytest.raises(ValidationError):
        schema.RecoveryCounters.model_validate({
            **valid, "redundant_rows": 3,
        }, strict=True)


def test_raw_valid_retry_preserves_first_answer_failure() -> None:
    row = {
        "work_id": _hash("work"), "doc_id": "doc", "chunk_index": 0,
        "first_pass_valid": False, "eventual_valid": True,
        "charged_attempt_count": 2, "strict_schema_invalid_attempts": 1,
        "semantic_invalid_attempts": 0, "assessment": "no_findings",
        "predicted_categories": [], "raw_findings": 0,
        "raw_grounded_findings": 0, "retained_findings": 0,
        "retained_grounded_findings": 0, "authoritative_done_reason": "stop",
        "length_outcomes": 0, "max_answered_prompt_eval_count": 1,
        "headroom_passed": True, "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True, "schema_escape_empty": True,
        "marker_in_answer": False, "raw_first_pass_valid": False,
        "final_outcome": "RAW_VALID", "redundant_rows": 0,
        "removed_finding_indices": [], "dedup_evidence_sha256": None,
    }
    assert schema.C0B4ChunkRow.model_validate(row, strict=True).eventual_valid
    with pytest.raises(ValidationError):
        schema.C0B4ChunkRow.model_validate({
            **row, "charged_attempt_count": 1,
            "strict_schema_invalid_attempts": 0,
        }, strict=True)


def test_seed17_data_failure_can_precede_cancellation_control() -> None:
    preliminary = schema.LaneAggregate.model_construct(
        lane_id="F72_17", seed=17,
        context_evidence_sha256=_hash("context"),
        cancellation_health_evidence_sha256=None, passed=False,
        failure_reasons=["pii_recall_below_7_of_8"],
    )
    assert preliminary.exact_gate() is preliminary
    with pytest.raises(ValueError):
        schema.LaneAggregate.model_construct(
            lane_id="F72_17", seed=17,
            context_evidence_sha256=_hash("context"),
            cancellation_health_evidence_sha256=None, passed=False,
            failure_reasons=["cancellation_health_failure"],
        ).exact_gate()


def test_quality_terminal_catalog_and_completion_facts_are_closed() -> None:
    confirmed = {
        **_binding(), "version": "c0b4-result-v1", "terminal": "CONFIRMED",
        "reason": "complete_public_acceptance_passed",
        "master_plan_sha256": _hash("master"),
        "lane_aggregate_sha256s": _lane_hashes(),
        "acceptance_aggregate_sha256": _hash("acceptance"),
        "selection": _selection(),
    }
    assert schema.validate_artifact(confirmed) == confirmed
    for changed in (
        {**confirmed, "reason": "seed17_no_qualifier"},
        {**confirmed, "selection": None},
        {**confirmed, "terminal": "SELECTED"},
    ):
        with pytest.raises((ValueError, ValidationError)):
            schema.validate_artifact(changed)

    completion = {
        **_binding(), "version": "c0b4-completion-v1",
        "outcome": "CONFIRMED", "artifact_sha256": _hash("result"),
        "facts": {"confirmed": True},
    }
    assert schema.validate_artifact(completion) == completion
    with pytest.raises(ValidationError):
        schema.Completion.model_validate({
            **completion, "outcome": "INCONCLUSIVE",
        }, strict=True)

    complete_failure = {
        **confirmed, "terminal": "INCONCLUSIVE",
        "reason": "complete_corpus_acceptance_failed", "selection": None,
    }
    assert schema.validate_artifact(complete_failure) == complete_failure
    with pytest.raises(ValidationError):
        schema.Result.model_validate({
            **complete_failure, "acceptance_aggregate_sha256": None,
        }, strict=True)
    with pytest.raises(ValidationError):
        schema.Result.model_validate({
            **complete_failure, "reason": "seed17_no_qualifier",
        }, strict=True)
    for reason, later, c44, acceptance in (
        ("seed17_no_qualifier", None, None, None),
        ("seed17_control_gate_failed", None, None, None),
        ("seed20260804_no_qualifier", _hash("later"), None, None),
    ):
        value = {
            **confirmed, "terminal": "INCONCLUSIVE", "reason": reason,
            "selection": None, "acceptance_aggregate_sha256": acceptance,
            "lane_aggregate_sha256s": {
                **_lane_hashes(), "f72_seed20260804_sha256": later,
                "c44_scored_sha256": c44,
            },
        }
        assert schema.validate_artifact(value) == value
        with pytest.raises(ValidationError):
            schema.Result.model_validate({
                **value, "lane_aggregate_sha256s": {
                    **value["lane_aggregate_sha256s"],
                    "c44_scored_sha256": _hash("unexpected-c44"),
                },
            }, strict=True)


@pytest.mark.parametrize("terminal,reason,attempt", [
    ("FAILED_SAFETY", "safety_envelope_failure", _hash("attempt")),
    ("BLOCKED_PROVENANCE", "provenance_identity_failure", None),
    ("BLOCKED_BUDGET", "call_allowance_exhausted", None),
    ("BLOCKED_FILESYSTEM", "filesystem_capability_or_integrity_failure", None),
    ("ABANDONED", "operator_abandoned", None),
])
def test_failure_terminal_owner_catalog_is_exact(terminal, reason, attempt) -> None:
    body = {
        **_binding(), "version": "c0b4-failure-evidence-v1",
        "terminal": terminal, "reason": reason,
        "lane_id": "F72_17", "plan_sha256": _hash("plan"),
        "attempt_id": attempt, "control_id": None, "charged_call_total": 1,
    }
    value = {**body, "evidence_sha256": sha256_json(body)}
    assert schema.validate_artifact(value) == value
    wrong_reason = ("call_allowance_exhausted"
                    if reason == "operator_abandoned" else "operator_abandoned")
    wrong = {**body, "reason": wrong_reason}
    wrong["evidence_sha256"] = sha256_json(wrong)
    with pytest.raises(ValidationError):
        schema.FailureEvidence.model_validate(wrong, strict=True)


def test_attemptless_failure_families_reject_attempts() -> None:
    body = {
        **_binding(), "version": "c0b4-failure-evidence-v1",
        "terminal": "BLOCKED_BUDGET", "reason": "call_allowance_exhausted",
        "lane_id": None, "plan_sha256": None,
        "attempt_id": _hash("attempt"), "control_id": None,
        "charged_call_total": 295,
    }
    value = {**body, "evidence_sha256": sha256_json(body)}
    with pytest.raises(ValidationError, match="attemptless"):
        schema.FailureEvidence.model_validate(value, strict=True)


@pytest.mark.parametrize("state,reason", schema.RUNTIME_REASON_BY_STATE.items())
def test_runtime_pause_state_reason_pairs_are_closed(state, reason) -> None:
    value = {**_binding(), "state": state, "reason": reason}
    assert schema.RuntimePause.model_validate(value, strict=True).reason == reason
    with pytest.raises(ValidationError):
        schema.RuntimePause.model_validate(
            {**value, "reason": "different"}, strict=True)


def test_c44_reason_catalog_does_not_accept_f72_quality_reasons() -> None:
    assert "macro_f1_below_0_90" not in schema.C44_FAILURE_REASONS
    assert "first_pass_invalid_chunks_above_1" not in schema.C44_FAILURE_REASONS
    assert schema.C44_FAILURE_REASONS == (
        "incomplete_chunk_coverage", "eventual_invalid_chunk_present",
        "noncanonical_evidence", "redundant_rows_above_1",
        "affected_chunks_above_1", "affected_documents_above_1",
    )
