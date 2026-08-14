"""Offline adversarial tests for C0B-6 lane and complete-corpus scoring."""
from __future__ import annotations

import copy

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import chunker
from scripts.analyst_benchmark.c0b2_public_schema import sha256_json
from scripts.analyst_benchmark.c0b2_schema import CATEGORIES
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b6_lineage import FROZEN_PARENT_BINDING
from scripts.analyst_benchmark.c0b6_plan import (
    POLICY_ID, POLICY_SHA256, SELECTION, build_master_plan, candidate_id,
    lane_from_master,
)
from scripts.analyst_benchmark.c0b6_scoring import (
    C0B6ScoringError, build_acceptance_aggregate, build_lane_aggregate,
    build_precontrol_lane_aggregate, build_public_summary, false_positive_rows,
    template_family,
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
        corpus=corpus, run_nonce_key=KEY, protocol_sha256=PROTOCOL_SHA256)
    plans = {lane: lane_from_master(
        master, lane, corpus=corpus, run_nonce_key=KEY)
             for lane in ("F72_20260811", "F72_20260818", "C44_1")}
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
                strict=False) if identifier in text]


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
            "strict_schema_invalid_attempts": 0, "semantic_invalid_attempts": 0,
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
        "retained_findings": findings, "dedup_evidence": None,
    }


def _evidence(plan, corpus) -> dict[str, dict[str, object]]:
    return {work["work_id"]: _raw_valid_evidence(work, corpus)
            for work in plan["work"]}


def _negative_work(plan, corpus):
    documents = corpus.by_id()
    return [row for row in plan["work"]
            if not documents[row["doc_id"]].categories_present]


def _add_false_positives(evidence, work, corpus, count: int = 1) -> None:
    document = corpus.by_id()[work["doc_id"]]
    source, _view = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    text = chunker.chunk(
        source, chunk_chars=work["chunk_chars"],
        overlap_chars=work["overlap"])[work["chunk_index"]].text
    candidates = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    assert len(candidates) >= count
    findings = [{"category": CATEGORIES[index % len(CATEGORIES)], "quote": quote}
                for index, quote in enumerate(candidates[:count])]
    row = evidence[work["work_id"]]
    row["retained_findings"] = findings
    row["chunk"].update({
        "assessment": "findings_present",
        "predicted_categories": [category for category in CATEGORIES
                                 if any(item["category"] == category
                                        for item in findings)],
        "raw_findings": count, "raw_grounded_findings": count,
        "retained_findings": count, "retained_grounded_findings": count,
    })


def _d50_component(corpus, *, documents: int = 1, findings: int = 1):
    return {
        "component": "D50_CONFIRMATION", "source_plan_sha256": "b" * 64,
        "source_aggregate_sha256":
            FROZEN_PARENT_BINDING["execution_parent"]["d4_aggregate_sha256"],
        "candidate_id": candidate_id(), "selection": dict(SELECTION),
        "document_ids": list(corpus.d_order), "expected_chunks": 66,
        "completed_chunks": 66, "first_pass_invalid_chunks": 0,
        "eventual_invalid_chunks": 0, "raw_findings": 100,
        "raw_grounded_findings": 100, "retained_findings": 100,
        "retained_grounded_findings": 100,
        "category_recall": {name: {"true_positives": 6, "support": 6}
                            for name in CATEGORIES},
        "negative_false_positive_documents": documents,
        "negative_retained_findings": findings,
        "injection_pairs": 0, "injection_pairs_measured": 0,
        "injection_events": 0, "robustness_failures": 0,
        "boundary_documents": 12, "boundary_passed": 12,
        "truncation_documents": 2, "truncation_completed": 2,
        "length_outcomes": 0, "context_failures": 0,
        "channel_violations": 0, "component_passed": True,
    }


def _f72(plans, corpus, evidence=None, *, lane="F72_20260811"):
    plan = plans[lane]
    evidence = _evidence(plan, corpus) if evidence is None else evidence
    kwargs = {}
    if lane == "F72_20260811":
        kwargs = {
            "context_evidence_sha256": "c" * 64,
            "cancellation_health_evidence_sha256": "d" * 64,
        }
    return build_lane_aggregate(plan, evidence, corpus=corpus, **kwargs)


def test_perfect_fresh_lanes_pass_and_control_ownership_is_exact(
        scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    first = _f72(plans, corpus)
    second = _f72(plans, corpus, lane="F72_20260818")
    assert first["passed"] is True and second["passed"] is True
    assert first["context_evidence_sha256"] == "c" * 64
    assert second["context_evidence_sha256"] is None
    assert second["cancellation_health_evidence_sha256"] is None
    assert first["retained_metrics"]["negative_retained_findings"] == 0
    assert validate_lane_aggregate(
        first, plans["F72_20260811"],
        _evidence(plans["F72_20260811"], corpus), corpus=corpus,
        context_evidence_sha256="c" * 64,
        cancellation_health_evidence_sha256="d" * 64) == first


@pytest.mark.parametrize("affected", (0, 1, 2, 3))
def test_f_lane_document_and_row_boundary(scoring_inputs, affected: int) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_20260818"]
    evidence = _evidence(plan, corpus)
    for work in _negative_work(plan, corpus)[:affected]:
        _add_false_positives(evidence, work, corpus)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    retained = aggregate["retained_metrics"]
    assert retained["negative_false_positive_documents"] == affected
    assert retained["negative_retained_findings"] == affected
    expected = [] if affected <= 2 else [
        "negative_false_positive_above_2", "negative_retained_findings_above_2"]
    assert aggregate["failure_reasons"] == expected


def test_f_lane_row_cap_is_independent_of_document_cap(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_20260818"]
    evidence = _evidence(plan, corpus)
    _add_false_positives(evidence, _negative_work(plan, corpus)[0], corpus, count=3)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    assert aggregate["retained_metrics"]["negative_false_positive_documents"] == 1
    assert aggregate["retained_metrics"]["negative_retained_findings"] == 3
    assert aggregate["failure_reasons"] == ["negative_retained_findings_above_2"]


def test_precontrol_stop_never_fabricates_control_evidence(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_20260811"]
    passing = _evidence(plan, corpus)
    assert build_precontrol_lane_aggregate(
        plan, passing, corpus=corpus,
        context_evidence_sha256="c" * 64) is None
    failing = copy.deepcopy(passing)
    for work in _negative_work(plan, corpus)[:3]:
        _add_false_positives(failing, work, corpus)
    result = build_precontrol_lane_aggregate(
        plan, failing, corpus=corpus,
        context_evidence_sha256="c" * 64)
    assert result is not None and result["passed"] is False
    assert result["cancellation_health_evidence_sha256"] is None


def test_template_family_and_public_rows_are_deterministic(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    plan = plans["F72_20260818"]
    evidence = _evidence(plan, corpus)
    targets = _negative_work(plan, corpus)[:2]
    for work in targets:
        _add_false_positives(evidence, work, corpus)
    aggregate = build_lane_aggregate(plan, evidence, corpus=corpus)
    rows = false_positive_rows(aggregate, component="F72_SEED20260818")
    assert [row["document_id"] for row in rows] == sorted(
        work["doc_id"] for work in targets)
    assert all(row["negative_retained_findings"] == 1 for row in rows)
    assert template_family("neg_clean_001") == "clean_boiler_maintenance_log"
    assert template_family("neg_nearmiss_020") == \
        "near_miss_checksum_failed_barcode"
    with pytest.raises(C0B6ScoringError, match="outside"):
        template_family("neg_nearmiss_021")


@pytest.mark.parametrize(("c44_count", "expected_total", "passes"), [
    (0, 3, True), (1, 4, True), (2, 5, False),
])
def test_final_document_and_row_boundaries(
        scoring_inputs, c44_count: int, expected_total: int, passes: bool) -> None:
    corpus, plans = scoring_inputs
    f_plan = plans["F72_20260811"]
    f_evidence = _evidence(f_plan, corpus)
    for work in _negative_work(f_plan, corpus)[:2]:
        _add_false_positives(f_evidence, work, corpus)
    f72 = _f72(plans, corpus, f_evidence)
    c_plan = plans["C44_1"]
    c_evidence = _evidence(c_plan, corpus)
    for work in _negative_work(c_plan, corpus)[:c44_count]:
        _add_false_positives(c_evidence, work, corpus)
    c44 = build_lane_aggregate(c_plan, c_evidence, corpus=corpus)
    result = build_acceptance_aggregate(
        c44, _d50_component(corpus), f72, corpus=corpus,
        acceptance_plan_sha256=c_plan["plan_sha256"])
    assert result["totals"]["negative_false_positive_documents"] == expected_total
    assert result["totals"]["negative_retained_findings"] == expected_total
    assert result["passed"] is passes
    expected = [] if passes else [
        "negative_false_positive_above_4", "negative_retained_findings_above_4"]
    assert result["failure_reasons"] == expected


def test_final_row_cap_is_independent_and_components_are_exact(scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    f72 = _f72(plans, corpus)
    c_plan = plans["C44_1"]
    c44 = build_lane_aggregate(
        c_plan, _evidence(c_plan, corpus), corpus=corpus)
    result = build_acceptance_aggregate(
        c44, _d50_component(corpus, documents=1, findings=5), f72,
        corpus=corpus, acceptance_plan_sha256=c_plan["plan_sha256"])
    assert result["totals"]["negative_false_positive_documents"] == 1
    assert result["totals"]["negative_retained_findings"] == 5
    assert result["failure_reasons"] == ["negative_retained_findings_above_4"]
    assert set(result["component_hashes"]) == {
        "c44_rerun_aggregate_sha256", "d50_confirmation_aggregate_sha256",
        "f72_seed20260811_aggregate_sha256",
    }


def test_public_summary_rederives_component_rows_and_fresh_seed_sets(
        scoring_inputs) -> None:
    corpus, plans = scoring_inputs
    first_plan, second_plan = plans["F72_20260811"], plans["F72_20260818"]
    first_evidence, second_evidence = (
        _evidence(first_plan, corpus), _evidence(second_plan, corpus))
    shared = _negative_work(first_plan, corpus)[0]
    second_shared = next(row for row in _negative_work(second_plan, corpus)
                         if row["doc_id"] == shared["doc_id"])
    _add_false_positives(first_evidence, shared, corpus)
    _add_false_positives(second_evidence, second_shared, corpus)
    first = _f72(plans, corpus, first_evidence)
    second = _f72(plans, corpus, second_evidence, lane="F72_20260818")
    c_plan = plans["C44_1"]
    c44 = build_lane_aggregate(c_plan, _evidence(c_plan, corpus), corpus=corpus)
    d50 = _d50_component(corpus)
    acceptance = build_acceptance_aggregate(
        c44, d50, first, corpus=corpus,
        acceptance_plan_sha256=c_plan["plan_sha256"])
    lane_hashes = {
        "f72_seed20260811_sha256": sha256_json(first),
        "f72_seed20260818_sha256": sha256_json(second),
        "c44_scored_sha256": sha256_json(c44),
    }
    result = {
        "version": "c0b6-result-v1", "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256, "protocol_sha256": PROTOCOL_SHA256,
        "terminal": "CONFIRMED", "reason": "complete_public_acceptance_passed",
        "master_plan_sha256": "e" * 64,
        "lane_aggregate_sha256s": lane_hashes,
        "acceptance_aggregate_sha256": sha256_json(acceptance),
        "selection": dict(SELECTION),
    }
    completion = {
        "version": "c0b6-completion-v1", "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256, "protocol_sha256": PROTOCOL_SHA256,
        "outcome": "CONFIRMED", "artifact_sha256": sha256_json(result),
        "facts": {"confirmed": True},
    }
    d50_rows = [{
        "component": "D50_CONFIRMATION", "document_id": "neg_clean_007",
        "categories": ["pii"],
        "public_template_family": template_family("neg_clean_007"),
        "negative_retained_findings": 1,
    }]
    summary = build_public_summary(
        run_id="c0b6-test", result=result, completion=completion,
        f72_seed20260811_lane=first, f72_seed20260818_lane=second,
        c44_lane=c44, acceptance_aggregate=acceptance,
        d50_component=d50, d50_false_positive_documents=d50_rows,
        corpus=corpus)
    assert summary["fresh_f_union_document_ids"] == [shared["doc_id"]]
    assert summary["fresh_f_intersection_document_ids"] == [shared["doc_id"]]
    assert summary["total_human_rejection_rows"] == 2
    assert summary["component_counts"]["F72_SEED20260818"] == {
        "negative_false_positive_documents": 1,
        "negative_retained_findings": 1,
    }
    changed = copy.deepcopy(d50_rows)
    changed[0]["negative_retained_findings"] = 2
    with pytest.raises(C0B6ScoringError, match="D50 public rows"):
        build_public_summary(
            run_id="c0b6-test", result=result, completion=completion,
            f72_seed20260811_lane=first, f72_seed20260818_lane=second,
            c44_lane=c44, acceptance_aggregate=acceptance,
            d50_component=d50, d50_false_positive_documents=changed,
            corpus=corpus)
