"""Strict offline contract tests for the closed C0B-5 artifact family."""
from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark import c0b5_schema as schema
from scripts.analyst_benchmark.c0b2_public_schema import (
    PublicWork, sha256_json, stage_f_candidate_id,
)


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


def _work(*, phase: str = "F_SEED_20260811", seed: int = 20260811,
          activation: bool = True) -> dict[str, object]:
    candidate_id = schema.CANDIDATE_ID
    cell_id = schema.c0b5_cell_id(
        candidate_id=candidate_id, phase=phase, seed=seed)
    nonce = "FENCE_" + "A" * 32
    values = {
        "stage": "F", "phase": phase, "plan_key": phase, "budget_stage": "F",
        "activation_group_id": schema.c0b5_activation_group_id(
            candidate_id, phase) if activation else None,
        "candidate_id": candidate_id, "cell_id": cell_id,
        **_candidate(), "doc_id": "neg_clean_001", "view_id": None,
        "document_sha256": _hash("document"), "seed": seed, "chunk_index": 0,
        "chunk_sha256": _hash("chunk"), "nonce": nonce,
        "prompt_sha256": _hash("prompt"), "request_sha256": _hash("request"),
    }
    values["work_id"] = schema.c0b5_work_id(
        cell_id=cell_id, chunk_index=0, chunk_sha256=values["chunk_sha256"],
        doc_id=values["doc_id"], document_sha256=values["document_sha256"],
        nonce=nonce, plan_key=phase, request_sha256=values["request_sha256"],
        view_id=None)
    return values


def _summary(*, reason: str = "seed20260804_no_qualifier") -> dict[str, object]:
    second = reason in {
        "seed20260811_no_qualifier", "complete_public_acceptance_passed",
        "complete_corpus_acceptance_failed"}
    final = reason in {
        "complete_public_acceptance_passed", "complete_corpus_acceptance_failed"}
    terminal = "CONFIRMED" if reason == "complete_public_acceptance_passed" \
        else "INCONCLUSIVE"
    rows = [
        {"component": "D50_CONFIRMATION", "document_id": "neg_clean_001",
         "categories": ["pii"],
         "public_template_family": "clean_boiler_maintenance_log",
         "negative_retained_findings": 1},
        {"component": "F72_SEED20260804", "document_id": "neg_nearmiss_001",
         "categories": ["financial"],
         "public_template_family": "near_miss_ssn_shaped_part_number",
         "negative_retained_findings": 1},
    ]
    if final:
        rows.insert(0, {
            "component": "C44_RERUN", "document_id": "neg_clean_002",
            "categories": ["contact"],
            "public_template_family": "clean_library_acquisition_notes",
            "negative_retained_findings": 1})
    if second:
        rows.append({
            "component": "F72_SEED20260811", "document_id": "neg_clean_001",
            "categories": ["demographic"],
            "public_template_family": "clean_boiler_maintenance_log",
            "negative_retained_findings": 1})
    rows.sort(key=lambda row: (row["component"], row["document_id"]))
    count = {"negative_false_positive_documents": 1,
             "negative_retained_findings": 1}
    body = {
        **_binding(), "version": "c0b5-public-summary-v1", "run_id": "run",
        "terminal": terminal, "reason": reason, "result_sha256": _hash("result"),
        "completion_sha256": _hash("completion"),
        "lane_aggregate_sha256s": {
            "f72_seed20260804_sha256": _hash("first"),
            "f72_seed20260811_sha256": _hash("second") if second else None,
            "c44_scored_sha256": _hash("c44") if final else None,
        },
        "acceptance_aggregate_sha256": _hash("acceptance") if final else None,
        "false_positive_documents": rows,
        "fresh_f_union_document_ids":
            ["neg_clean_001", "neg_nearmiss_001"] if second else None,
        "fresh_f_intersection_document_ids": [] if second else None,
        "component_counts": {
            "C44_RERUN": count if final else None, "D50_CONFIRMATION": count,
            "F72_SEED20260804": count,
            "F72_SEED20260811": count if second else None,
        },
        "total_human_rejection_rows": 3 if final else None,
    }
    return {**body, "summary_sha256": sha256_json(body)}


def test_local_work_identity_supports_new_seed_and_rejects_inherited_domain() -> None:
    value = _work()
    assert schema.CANDIDATE_ID == stage_f_candidate_id(
        _candidate(), schema.EXECUTION_PARENT_BINDING["final_d_decision_sha256"])
    assert list(schema.C0B5PublicWork.model_fields) == list(PublicWork.model_fields)
    assert schema.C0B5PublicWork.model_validate(value, strict=True).seed == 20260811
    inherited = deepcopy(value)
    inherited["work_id"] = _hash("old-domain")
    with pytest.raises(ValidationError, match="work ID"):
        schema.C0B5PublicWork.model_validate(inherited, strict=True)
    with pytest.raises(ValidationError):
        schema.C0B5PublicWork.model_validate(
            {**value, "seed": "20260811"}, strict=True)


def test_public_template_mapping_is_hash_pinned_and_fails_closed() -> None:
    assert sha256_json(schema.PUBLIC_TEMPLATE_FAMILY_PAYLOAD) == \
        schema.PUBLIC_TEMPLATE_FAMILY_SHA256
    assert schema.public_template_family(
        "neg_clean_001") == "clean_boiler_maintenance_log"
    assert schema.public_template_family(
        "neg_nearmiss_020") == "near_miss_checksum_failed_barcode"
    for value in ("neg_clean_000", "neg_clean_021", "neg_clean_01", "pos_pii_013"):
        with pytest.raises(ValueError, match="outside frozen template rules"):
            schema.public_template_family(value)


def test_raw_valid_retry_requires_visible_prior_invalid_attempt() -> None:
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
    assert schema.C0B5ChunkRow.model_validate(row, strict=True).eventual_valid
    with pytest.raises(ValidationError, match="retry"):
        schema.C0B5ChunkRow.model_validate({
            **row, "charged_attempt_count": 1, "strict_schema_invalid_attempts": 0,
        }, strict=True)


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_f_lane_document_and_row_caps_are_independent_and_exact(count: int) -> None:
    reasons = [] if count <= 2 else [
        "negative_false_positive_above_2", "negative_retained_findings_above_2"]
    retained = schema.RetainedMetrics.model_construct(
        negative_false_positive_documents=count, negative_retained_findings=count)
    aggregate = schema.LaneAggregate.model_construct(
        lane_id="F72_20260811", seed=20260811,
        context_evidence_sha256=None, cancellation_health_evidence_sha256=None,
        retained_metrics=retained, passed=not reasons, failure_reasons=reasons)
    assert aggregate.exact_gate() is aggregate
    wrong = schema.LaneAggregate.model_construct(
        lane_id="F72_20260811", seed=20260811,
        context_evidence_sha256=None, cancellation_health_evidence_sha256=None,
        retained_metrics=retained, passed=not reasons,
        failure_reasons=reasons[:1])
    if count <= 2:
        assert wrong.exact_gate() is wrong
    else:
        with pytest.raises(ValueError, match="exact review gates"):
            wrong.exact_gate()


@pytest.mark.parametrize("count", [3, 4, 5])
def test_final_document_and_row_caps_are_independent_and_exact(count: int) -> None:
    reasons = [] if count <= 4 else [
        "negative_false_positive_above_4", "negative_retained_findings_above_4"]
    recovery = schema.RecoveryCounters.model_construct()
    totals = schema.AcceptanceTotals.model_construct(
        negative_false_positive_documents=count, negative_retained_findings=count,
        recovery_counters=recovery)
    aggregate = schema.AcceptanceAggregate.model_construct(
        totals=totals, recovery_counters=recovery, passed=not reasons,
        failure_reasons=reasons)
    assert aggregate.exact_gate() is aggregate
    if count == 5:
        aggregate.failure_reasons = list(reversed(reasons))
        with pytest.raises(ValueError, match="frozen-order"):
            aggregate.exact_gate()


@pytest.mark.parametrize("reason", [
    "seed20260804_no_qualifier", "seed20260811_no_qualifier",
    "complete_public_acceptance_passed", "complete_corpus_acceptance_failed",
])
def test_public_summary_exact_stop_ownership_counts_and_self_hash(reason: str) -> None:
    value = _summary(reason=reason)
    assert schema.validate_public_summary(value) == value
    changed = deepcopy(value)
    changed["component_counts"]["D50_CONFIRMATION"][
        "negative_retained_findings"] = 2
    changed["summary_sha256"] = sha256_json({
        key: item for key, item in changed.items() if key != "summary_sha256"})
    with pytest.raises(ValidationError, match="component counts"):
        schema.PublicSummary.model_validate(changed, strict=True)
    changed = deepcopy(value)
    changed["false_positive_documents"][0][
        "public_template_family"] = "clean_sprint_retrospective"
    changed["summary_sha256"] = sha256_json({
        key: item for key, item in changed.items() if key != "summary_sha256"})
    with pytest.raises(ValidationError, match="template family"):
        schema.PublicSummary.model_validate(changed, strict=True)


def test_summary_is_derived_only_and_mixed_artifact_versions_fail_closed() -> None:
    value = _summary()
    with pytest.raises(ValueError, match="unknown C0B-5 artifact version"):
        schema.validate_artifact(value)
    changed = {**value, "version": "c0b4-public-summary-v1"}
    with pytest.raises(ValidationError):
        schema.validate_public_summary(changed)
    changed = {**value, "policy_id": "c0b4-bounded-grounded-dedup-v1"}
    with pytest.raises(ValueError):
        schema.validate_public_summary(changed)
