"""Offline adversarial tests for C0B-4 lane and acceptance scoring."""
from __future__ import annotations

import copy
import json

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import chunker
from scripts.analyst_benchmark.c0b2_public_schema import canonical_json, sha256_json
from scripts.analyst_benchmark.c0b2_schema import CATEGORIES
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b4_plan import (
    PARENT_BINDING, POLICY_ID, POLICY_SHA256, SELECTION, build_master_plan,
    candidate_id, lane_from_master,
)
from scripts.analyst_benchmark.c0b4_scoring import (
    C0B4ScoringError, _component_from_lane, build_acceptance_aggregate,
    build_lane_aggregate, build_precontrol_lane_aggregate,
    derive_parent_d50_component,
    validate_lane_aggregate,
)

KEY = bytes(range(32))
PROTOCOL_SHA256 = "a" * 64


@pytest.fixture(scope="module")
def scoring_inputs():
    manifest = legacy_plan.build_master_manifest()
    corpus = load_public_corpus(
        legacy_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=KEY,
        protocol_sha256=PROTOCOL_SHA256)
    plans = {lane_id: lane_from_master(
        master, lane_id, corpus=corpus, run_nonce_key=KEY)
             for lane_id in ("F72_17", "F72_20260804", "C44_1")}
    return corpus, plans


def _findings(work, corpus) -> list[dict[str, str]]:
    document = corpus.by_id()[work["doc_id"]]
    source, _view = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    text = chunker.chunk(
        source, chunk_chars=work["chunk_chars"],
        overlap_chars=work["overlap"])[work["chunk_index"]].text
    return [{"category": category, "quote": identifier}
            for category, identifier in zip(
                document.categories_present, document.expected_identifiers,
                strict=False)
            if identifier in text]


def _raw_valid_evidence(work, corpus) -> dict[str, object]:
    findings = _findings(work, corpus)
    categories = [category for category in CATEGORIES
                  if any(row["category"] == category for row in findings)]
    count = len(findings)
    return {
        "chunk": {
            "work_id": work["work_id"], "doc_id": work["doc_id"],
            "chunk_index": work["chunk_index"], "first_pass_valid": True,
            "eventual_valid": True, "charged_attempt_count": 1,
            "strict_schema_invalid_attempts": 0,
            "semantic_invalid_attempts": 0,
            "assessment": "findings_present" if findings else "no_findings",
            "predicted_categories": categories, "raw_findings": count,
            "raw_grounded_findings": count, "retained_findings": count,
            "retained_grounded_findings": count,
            "authoritative_done_reason": "stop", "length_outcomes": 0,
            "max_answered_prompt_eval_count": 100, "headroom_passed": True,
            "tools_empty": True, "images_empty": True,
            "unknown_message_fields_empty": True, "schema_escape_empty": True,
            "marker_in_answer": False, "raw_first_pass_valid": True,
            "final_outcome": "RAW_VALID", "redundant_rows": 0,
            "removed_finding_indices": [], "dedup_evidence_sha256": None,
        },
        "retained_findings": findings,
        "dedup_evidence": None,
    }


def _evidence(plan, corpus) -> dict[str, dict[str, object]]:
    return {work["work_id"]: _raw_valid_evidence(work, corpus)
            for work in plan["work"]}


def _add_recovery(evidence, work, corpus, protocol_sha256=PROTOCOL_SHA256) -> None:
    row = evidence[work["work_id"]]
    chunk = row["chunk"]
    assert row["retained_findings"]
    chunk.update({
        "first_pass_valid": False, "raw_first_pass_valid": False,
        "semantic_invalid_attempts": 1,
        "raw_findings": chunk["retained_findings"] + 1,
        "raw_grounded_findings": chunk["retained_findings"] + 1,
        "final_outcome": "NORMALIZED_DUPLICATE", "redundant_rows": 1,
        "removed_finding_indices": [chunk["retained_findings"]],
    })
    dedup = {
        "version": "c0b4-dedup-evidence-v1",
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": protocol_sha256, "work_id": work["work_id"],
        "attempt_id": sha256_json({"attempt": work["work_id"]}),
        "raw_response_sha256": sha256_json({"raw": work["work_id"]}),
        "dedupe_key": "category+nfc_quote",
        "removed_index": chunk["retained_findings"],
        "raw_counts": {
            "findings": chunk["raw_findings"],
            "grounded_findings": chunk["raw_grounded_findings"],
            "first_pass_valid": False, "semantic_invalid_attempts": 1,
        },
        "retained_counts": {
            "findings": chunk["retained_findings"],
            "grounded_findings": chunk["retained_grounded_findings"],
            "eventual_valid": True,
        },
    }
    dedup["evidence_sha256"] = sha256_json(dedup)
    chunk["dedup_evidence_sha256"] = dedup["evidence_sha256"]
    row["dedup_evidence"] = dedup


def _invalidate(evidence, work) -> None:
    row = evidence[work["work_id"]]
    row["retained_findings"] = []
    row["dedup_evidence"] = None
    row["chunk"].update({
        "first_pass_valid": False, "raw_first_pass_valid": False,
        "eventual_valid": False, "semantic_invalid_attempts": 2,
        "assessment": None, "predicted_categories": [], "raw_findings": 0,
        "raw_grounded_findings": 0, "retained_findings": 0,
        "retained_grounded_findings": 0, "authoritative_done_reason": None,
        "final_outcome": "INVALID", "redundant_rows": 0,
        "removed_finding_indices": [], "dedup_evidence_sha256": None,
    })


def _d50_component(corpus) -> dict[str, object]:
    return {
        "component": "D50_CONFIRMATION", "source_plan_sha256": "b" * 64,
        "source_aggregate_sha256": PARENT_BINDING["d4_aggregate_sha256"],
        "candidate_id": candidate_id(), "selection": dict(SELECTION),
        "document_ids": list(corpus.d_order), "expected_chunks": 66,
        "completed_chunks": 66, "first_pass_invalid_chunks": 0,
        "eventual_invalid_chunks": 0, "raw_findings": 100,
        "raw_grounded_findings": 100, "retained_findings": 100,
        "retained_grounded_findings": 100,
        "category_recall": {name: {"true_positives": 6, "support": 6}
                            for name in CATEGORIES},
        "negative_false_positive_documents": 1, "injection_pairs": 0,
        "injection_pairs_measured": 0, "injection_events": 0,
        "robustness_failures": 0, "boundary_documents": 12,
        "boundary_passed": 12, "truncation_documents": 2,
        "truncation_completed": 2, "length_outcomes": 0,
        "context_failures": 0, "channel_violations": 0,
        "component_passed": True,
    }


def _positive_work(plan, evidence, *, skip=0):
    matches = [work for work in plan["work"]
               if evidence[work["work_id"]]["retained_findings"]]
    return matches[skip]


def test_perfect_f72_passes_and_stored_value_rederives(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    evidence = _evidence(plans["F72_17"], corpus)
    aggregate = build_lane_aggregate(
        plans["F72_17"], evidence, corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64)
    assert aggregate["passed"] is True
    assert aggregate["failure_reasons"] == []
    assert aggregate["planned_chunks"] == aggregate["completed_chunks"] == 92
    assert aggregate["retained_metrics"]["boundary_passed"] == 12
    assert aggregate["retained_metrics"]["injection_pairs_measured"] == 4
    assert validate_lane_aggregate(
        aggregate, plans["F72_17"], evidence, corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64) == aggregate
    changed = copy.deepcopy(aggregate)
    changed["raw_metrics"]["raw_findings"] += 1
    with pytest.raises(C0B4ScoringError, match="not exact"):
        validate_lane_aggregate(
            changed, plans["F72_17"], evidence, corpus=corpus,
            context_evidence_sha256="c" * 64,
            cancellation_health_evidence_sha256="d" * 64)


def test_one_grounded_duplicate_recovery_passes_but_two_fail(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_20260804"]
    evidence = _evidence(plan, corpus)
    first = _positive_work(plan, evidence)
    _add_recovery(evidence, first, corpus)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["passed"] is True
    assert aggregate["raw_metrics"]["first_pass_invalid_chunks"] == 1
    assert aggregate["recovery_counters"] == {
        "redundant_rows": 1, "affected_work_ids": [first["work_id"]],
        "affected_chunk_count": 1, "affected_document_ids": [first["doc_id"]],
        "affected_document_count": 1, "normalized_duplicate_chunks": 1,
    }
    changed = copy.deepcopy(evidence)
    changed[first["work_id"]]["dedup_evidence"]["removed_index"] = 0
    with pytest.raises(C0B4ScoringError, match="dedup evidence"):
        build_lane_aggregate(plan, changed, corpus=corpus)

    second = _positive_work(plan, evidence, skip=1)
    _add_recovery(evidence, second, corpus)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["passed"] is False
    assert aggregate["failure_reasons"] == [
        "first_pass_invalid_chunks_above_1", "redundant_rows_above_1",
        "affected_chunks_above_1", "affected_documents_above_1",
    ]


def test_c44_uses_only_evidence_gates_not_standalone_quality(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["C44_1"]
    evidence = _evidence(plan, corpus)
    for work in plan["work"]:
        row = evidence[work["work_id"]]
        if row["retained_findings"]:
            count = row["chunk"]["retained_findings"]
            row["retained_findings"] = []
            row["chunk"].update({
                "assessment": "no_findings", "predicted_categories": [],
                "raw_findings": 0, "raw_grounded_findings": 0,
                "retained_findings": 0, "retained_grounded_findings": 0,
            })
            assert count > 0
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["component_passed"] is True
    assert aggregate["failure_reasons"] == []
    assert "passed" not in aggregate
    assert "lane_plan_sha256" not in aggregate
    assert aggregate["acceptance_plan_sha256"] == plan["plan_sha256"]
    assert aggregate["retained_metrics"]["macro_f1"] == {
        "numerator": 0, "denominator": 1}


def test_c44_eventual_invalid_and_duplicate_bounds_are_closed(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["C44_1"]
    evidence = _evidence(plan, corpus)
    _invalidate(evidence, plan["work"][0])
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["component_passed"] is False
    assert aggregate["failure_reasons"] == ["eventual_invalid_chunk_present"]

    evidence = _evidence(plan, corpus)
    _add_recovery(evidence, _positive_work(plan, evidence), corpus)
    _add_recovery(evidence, _positive_work(plan, evidence, skip=1), corpus)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["failure_reasons"] == [
        "redundant_rows_above_1", "affected_chunks_above_1",
        "affected_documents_above_1"]


def test_c44_bounded_noncanonical_evidence_becomes_component_failure(
        scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["C44_1"]
    evidence = _evidence(plan, corpus)
    work = _positive_work(plan, evidence)
    evidence[work["work_id"]]["retained_findings"][0]["quote"] = \
        "bounded-but-not-in-the-source"
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["component_passed"] is False
    assert aggregate["failure_reasons"] == ["noncanonical_evidence"]


def test_seed17_noncontrol_failure_can_stop_before_cancellation(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_17"]
    evidence = _evidence(plan, corpus)
    _invalidate(evidence, plan["work"][0])
    aggregate = build_lane_aggregate(
        plan, evidence, corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256=None, controls_passed=None)
    assert aggregate["passed"] is False
    assert aggregate["cancellation_health_evidence_sha256"] is None
    assert "eventual_invalid_chunk_present" in aggregate["failure_reasons"]
    assert "cancellation_health_failure" not in aggregate["failure_reasons"]

    with pytest.raises(C0B4ScoringError, match="requires cancellation evidence"):
        build_lane_aggregate(
            plan, _evidence(plan, corpus), corpus=corpus,
            context_evidence_sha256="c" * 64,
            cancellation_health_evidence_sha256=None, controls_passed=None)


def test_precontrol_evaluator_has_no_fabricated_control_hash(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_17"]
    passing = _evidence(plan, corpus)
    assert build_precontrol_lane_aggregate(
        plan, passing, corpus=corpus,
        context_evidence_sha256="c" * 64) is None
    failing = copy.deepcopy(passing)
    _invalidate(failing, plan["work"][0])
    aggregate = build_precontrol_lane_aggregate(
        plan, failing, corpus=corpus,
        context_evidence_sha256="c" * 64)
    assert aggregate is not None and not aggregate["passed"]
    assert aggregate["cancellation_health_evidence_sha256"] is None


def test_complete_166_acceptance_passes_and_candidate_lineage_matches(
        scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    c44 = build_lane_aggregate(
        plans["C44_1"], _evidence(plans["C44_1"], corpus), corpus=corpus)
    f72 = build_lane_aggregate(
        plans["F72_17"], _evidence(plans["F72_17"], corpus), corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64)
    d50 = _d50_component(corpus)
    components = [
        _component_from_lane(c44, corpus=corpus, component="C44_RERUN"),
        d50,
        _component_from_lane(f72, corpus=corpus, component="F72_SEED17"),
    ]
    assert [row["candidate_id"] for row in components] == [candidate_id()] * 3
    result = build_acceptance_aggregate(
        c44, d50, f72, corpus=corpus,
        acceptance_plan_sha256=plans["C44_1"]["plan_sha256"])
    assert result["passed"] is True
    assert result["failure_reasons"] == []
    assert result["totals"]["document_count"] == 166
    assert result["totals"]["expected_chunks"] == 202
    assert all(row == {"true_positives": 20, "support": 20}
               for row in result["totals"]["category_recall"].values())


def test_scoring_aggregates_survive_canonical_json_round_trip(
        scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    f72 = build_lane_aggregate(
        plans["F72_17"], _evidence(plans["F72_17"], corpus), corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64)
    stored_f72 = json.loads(canonical_json(f72))
    assert validate_lane_aggregate(
        stored_f72, plans["F72_17"], _evidence(plans["F72_17"], corpus),
        corpus=corpus, context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64) == stored_f72

    c44 = build_lane_aggregate(
        plans["C44_1"], _evidence(plans["C44_1"], corpus), corpus=corpus)
    acceptance = build_acceptance_aggregate(
        c44, _d50_component(corpus), f72, corpus=corpus,
        acceptance_plan_sha256=plans["C44_1"]["plan_sha256"])
    assert build_acceptance_aggregate(
        json.loads(canonical_json(c44)),
        json.loads(canonical_json(_d50_component(corpus))), stored_f72,
        corpus=corpus,
        acceptance_plan_sha256=plans["C44_1"]["plan_sha256"],
    ) == json.loads(canonical_json(acceptance))


def test_acceptance_rejects_d50_and_parent_tampering(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    c44 = build_lane_aggregate(
        plans["C44_1"], _evidence(plans["C44_1"], corpus), corpus=corpus)
    f72 = build_lane_aggregate(
        plans["F72_17"], _evidence(plans["F72_17"], corpus), corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64)
    d50 = _d50_component(corpus)
    d50["candidate_id"] = "e" * 64
    with pytest.raises(C0B4ScoringError, match="parent evidence"):
        build_acceptance_aggregate(
            c44, d50, f72, corpus=corpus,
            acceptance_plan_sha256=plans["C44_1"]["plan_sha256"])
    with pytest.raises(C0B4ScoringError, match="parent hashes"):
        derive_parent_d50_component({}, {}, corpus=corpus)
