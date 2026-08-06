"""Offline adversarial tests for pure Stage-F scoring and acceptance."""
from __future__ import annotations

import copy
from fractions import Fraction

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import chunker
from scripts.analyst_benchmark.c0b2_plan import attempt_id
from scripts.analyst_benchmark.c0b2_public_schema import (
    context_control_id, sha256_json, stage_d_candidate_id,
)
from scripts.analyst_benchmark.c0b2_public_scoring import fraction_value
from scripts.analyst_benchmark.c0b2_schema import canonical_json
from scripts.analyst_benchmark.c0b2_stage_f import (
    AcceptanceComponent, CandidateResult, StageFError, _injection_pairs, _ranking,
    _build_acceptance_aggregate_from_components, bootstrap_draw_index,
    build_acceptance_aggregate,
    build_c44_scored_aggregate, build_f_seed_result, build_inconclusive_result,
    build_provisional_decision,
    build_seed1_evidence_from_attempts, build_seed_activation_decision,
    build_stage_f_aggregate_from_attempts, validate_f_seed_result,
    validate_c44_scored_aggregate, validate_seed1_evidence,
    validate_stage_f_aggregate_from_attempts,
)
from scripts.analyst_benchmark.c0b2_stage_f_plan import (
    build_acceptance_plan, build_f_master_plan, load_public_corpus,
)

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def scoring_inputs():
    manifest = legacy_plan.build_master_manifest()
    corpus = load_public_corpus(
        legacy_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256)
    model, digest, _think = legacy_plan.MODELS[0]
    selection = {
        "model": model, "model_digest": digest, "worksheet": "v2",
        "chunk_chars": 2000, "overlap": 256,
        "num_ctx": 8192, "num_predict": 2048,
    }
    master = build_f_master_plan(
        "a" * 64, [selection], corpus=corpus, run_nonce_key=KEY)
    plan = master["plans"][0]["payload"]
    candidate_id = master["base_candidate_order"][0]
    evidence = _attempts_for_group(plan, candidate_id, corpus)
    result = build_f_seed_result(
        plan, candidate_id, evidence, corpus=corpus)
    return corpus, selection, master, plan, candidate_id, evidence, result


def _response_for(work, corpus) -> str:
    document = corpus.by_id()[work["doc_id"]]
    source, _view = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    text = chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=256
    )[work["chunk_index"]].text
    findings = [
        {"category": category, "quote": identifier, "offset": 999999}
        for category, identifier in zip(
            document.categories_present, document.expected_identifiers, strict=False)
        if identifier in text
    ]
    return canonical_json({
        "document_type": "record", "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": findings,
    }).decode("utf-8")


def _attempt(work, corpus, attempt_no_value: int = 1, *, invalid: bool = False,
             prompt_eval_count: int = 100):
    response = "{}" if invalid else _response_for(work, corpus)
    return {
        "attempt_id": attempt_id(work["work_id"], attempt_no_value),
        "work_id": work["work_id"], "attempt_no": attempt_no_value,
        "call_class": "scored" if attempt_no_value == 1 else "schema_retry",
        "request_sha256": work["request_sha256"],
        "state": "SCHEMA_INVALID" if invalid else "ACCEPTED",
        "response": response, "done_reason": "stop",
        "prompt_eval_count": prompt_eval_count,
        "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True,
    }


def _attempts_for_group(plan, candidate_id, corpus):
    return {row["work_id"]: [_attempt(row, corpus)] for row in plan["work"]
            if row["candidate_id"] == candidate_id}


def _passed_controls(master, candidate_id):
    group = master["plans"][0]["payload"]["groups"][0]
    context = group["context_control"]
    cancel = group["cancellation_control"]
    health = group["health_control"]
    probe = {
        "control_id": context["control_id"], "purpose": context["purpose"],
        "candidate_id": candidate_id, "model": context["model"],
        "model_digest": context["model_digest"],
        "config_sha256": context["config_sha256"],
        "expected_num_ctx": context["minimum_context_length"],
        "observed_context_length": context["minimum_context_length"],
        "trigger_work_id": group["first_work_id"], "state": "PASSED",
        "response_sha256": "1" * 64,
    }
    cancellation = {
        "candidate_id": candidate_id,
        "cancel_control_id": cancel["control_id"],
        "cancel_attempt_id": "2" * 64,
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": True, "cancel_elapsed_ms": 5000,
        "health_control_id": health["control_id"],
        "health_work_id": health["health_work_id"],
        "health_attempt_ids": ["3" * 64],
        "not_before_utc": "2026-08-05T12:00:02Z",
        "started_at_utc": "2026-08-05T12:00:02Z",
        "eventual_valid": True, "retained_grounded_pii": True,
        "authoritative_done_reason": "stop",
        "max_answered_prompt_eval_count": 100,
        "length_outcomes": 0, "headroom_passed": True,
        "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True, "schema_escape_empty": True,
        "passed": True, "failure_reasons": [],
    }
    return probe, cancellation


def test_seed_result_rederives_exact_metrics_and_rejects_tampered_summary(
        scoring_inputs) -> None:
    corpus, _selection, _master, plan, candidate_id, evidence, result = scoring_inputs
    assert result["passed"] is True
    assert result["failure_reasons"] == []
    assert result["planned_chunks"] == result["completed_chunks"] == 122
    assert result["boundary_passed"] == result["boundary_documents"] == 12
    assert result["injection_pairs_measured"] == 4
    assert fraction_value(result["macro_f1"]) == Fraction(1, 1)
    assert all(metric["recall"] == {"numerator": 1, "denominator": 1}
               for metric in result["category_metrics"].values())

    hostile = copy.deepcopy(result)
    hostile["raw_findings"] += 1
    with pytest.raises(StageFError, match="not exact"):
        validate_f_seed_result(
            hostile, plan, candidate_id, evidence, corpus=corpus)


def test_injection_pairing_uses_component_documents_not_f_only_order(
        scoring_inputs) -> None:
    corpus, _selection, _master, _plan, _candidate_id, _evidence, _result = (
        scoring_inputs)
    documents = []
    for doc_id in (item for item in corpus.c_order if item.startswith("inj_")):
        source = corpus.by_id()[doc_id]
        predicted = list(source.categories_present)
        documents.append({
            "doc_id": doc_id, "stratum": source.stratum,
            "expected_categories": predicted, "predicted_categories": predicted,
            "expected_chunk_count": 1, "completed_chunk_count": 1,
            "eventual_invalid_chunks": 0,
            "chunks": [{
                "assessment": "findings_present", "marker_in_answer": False,
            }],
            "channel_violations": 0,
        })
    pairs = _injection_pairs(documents, corpus)
    assert [row["pair_id"] for row in pairs] == [
        "inj_01", "inj_02", "inj_03", "inj_04"]
    assert all(not row["robustness_failure"] for row in pairs)


def test_c44_scored_aggregate_rebuilds_exact_attempt_evidence(scoring_inputs) -> None:
    corpus, _selection, master, _plan, candidate_id, _evidence, _result = scoring_inputs
    acceptance = build_acceptance_plan(
        master, candidate_id=candidate_id,
        provisional_decision_sha256="9" * 64)
    attempts = _attempts_for_group(acceptance, candidate_id, corpus)
    scored = build_c44_scored_aggregate(
        acceptance, attempts, corpus=corpus)
    assert scored["evidence"]["planned_chunks"] == 44
    assert [row["pair_id"] for row in scored["evidence"]["injection_pairs"]] == [
        "inj_01", "inj_02", "inj_03", "inj_04"]
    validate_c44_scored_aggregate(
        scored, acceptance, attempts, corpus=corpus)

    hostile = copy.deepcopy(scored)
    hostile["evidence"]["raw_findings"] += 1
    with pytest.raises(StageFError, match="not exact"):
        validate_c44_scored_aggregate(
            hostile, acceptance, attempts, corpus=corpus)


def test_first_invalid_and_headroom_threshold_equalities_are_exact(scoring_inputs) -> None:
    corpus, _selection, _master, plan, candidate_id, evidence, _result = scoring_inputs
    rows = [row for row in plan["work"] if row["candidate_id"] == candidate_id]
    one_retry = copy.deepcopy(evidence)
    one_retry[rows[0]["work_id"]] = [
        _attempt(rows[0], corpus, invalid=True),
        _attempt(rows[0], corpus, 2),
    ]
    result = build_f_seed_result(plan, candidate_id, one_retry, corpus=corpus)
    assert result["first_pass_invalid_chunks"] == 1
    assert "first_pass_invalid_chunks_above_1" not in result["failure_reasons"]

    two_retries = copy.deepcopy(one_retry)
    two_retries[rows[1]["work_id"]] = [
        _attempt(rows[1], corpus, invalid=True),
        _attempt(rows[1], corpus, 2),
    ]
    result = build_f_seed_result(plan, candidate_id, two_retries, corpus=corpus)
    assert result["first_pass_invalid_chunks"] == 2
    assert "first_pass_invalid_chunks_above_1" in result["failure_reasons"]

    limit = (85 * rows[0]["num_ctx"]) // 100 - rows[0]["num_predict"]
    exact = copy.deepcopy(evidence)
    exact[rows[0]["work_id"]] = [
        _attempt(rows[0], corpus, prompt_eval_count=limit)]
    assert "context_headroom_failure" not in build_f_seed_result(
        plan, candidate_id, exact, corpus=corpus)["failure_reasons"]
    exact[rows[0]["work_id"]] = [
        _attempt(rows[0], corpus, prompt_eval_count=limit + 1)]
    assert "context_headroom_failure" in build_f_seed_result(
        plan, candidate_id, exact, corpus=corpus)["failure_reasons"]


def test_seed1_activation_rebuilds_attempts_controls_and_rejects_forgery(
        scoring_inputs) -> None:
    corpus, _selection, master, _plan, candidate_id, evidence, _result = scoring_inputs
    probe, cancellation = _passed_controls(master, candidate_id)
    seed1 = build_seed1_evidence_from_attempts(
        master, {candidate_id: evidence}, {candidate_id: probe},
        {candidate_id: cancellation}, corpus=corpus)
    assert seed1["candidates"][0]["qualified"] is True
    activation = build_seed_activation_decision(master, seed1)
    assert activation["qualifier_candidate_ids"] == [candidate_id]
    assert len(activation["activated_group_ids"]) == 2
    assert activation["inactive_group_ids"] == []

    hostile = copy.deepcopy(seed1)
    hostile["candidates"][0]["seed_result"]["raw_findings"] += 1
    with pytest.raises(StageFError, match="not exact"):
        validate_seed1_evidence(
            hostile, master, {candidate_id: evidence}, {candidate_id: probe},
            {candidate_id: cancellation}, corpus=corpus)


def test_stage_f_aggregate_rebuilds_all_seeds_and_uses_persisted_decision_digest(
        scoring_inputs) -> None:
    corpus, _selection, master, _plan, candidate_id, evidence, _result = scoring_inputs
    probe, cancellation = _passed_controls(master, candidate_id)
    seed1 = build_seed1_evidence_from_attempts(
        master, {candidate_id: evidence}, {candidate_id: probe},
        {candidate_id: cancellation}, corpus=corpus)
    activation = build_seed_activation_decision(master, seed1)
    attempts = {candidate_id: {}}
    for envelope in master["plans"]:
        plan = envelope["payload"]
        seed = plan["work"][0]["seed"]
        attempts[candidate_id][seed] = _attempts_for_group(
            plan, candidate_id, corpus)

    persisted_digest = "d" * 64
    assert sha256_json(activation) != persisted_digest
    aggregate = build_stage_f_aggregate_from_attempts(
        master, seed1, activation, attempts,
        seed_activation_decision_sha256=persisted_digest, corpus=corpus)
    assert aggregate["seed_activation_decision_sha256"] == persisted_digest
    assert aggregate["ranking"]["winner_candidate_id"] == candidate_id
    assert build_provisional_decision(aggregate)["reason"] == "single_qualifier"
    validate_stage_f_aggregate_from_attempts(
        aggregate, master, seed1, activation, attempts,
        seed_activation_decision_sha256=persisted_digest, corpus=corpus)

    hostile = copy.deepcopy(aggregate)
    hostile["candidates"][0]["seed_results"][1]["raw_findings"] += 1
    with pytest.raises(StageFError, match="not exact"):
        validate_stage_f_aggregate_from_attempts(
            hostile, master, seed1, activation, attempts,
            seed_activation_decision_sha256=persisted_digest, corpus=corpus)


def test_bootstrap_counter_and_identical_pair_ranking_are_deterministic(
        scoring_inputs) -> None:
    _corpus, selection, master, _plan, candidate_id, _evidence, result = scoring_inputs
    assert bootstrap_draw_index(0, 0, 0) == 4
    probe, cancellation = _passed_controls(master, candidate_id)
    second_id = "f" * 64
    candidates = []
    for current_id in (candidate_id, second_id):
        results = []
        for seed in (1, 17, 20260804):
            row = copy.deepcopy(result)
            row["candidate_id"] = current_id
            row["seed"] = seed
            results.append(row)
        current_probe = {**probe, "candidate_id": current_id,
                         "control_id": "4" * 64}
        current_cancel = {**cancellation, "candidate_id": current_id,
                          "cancel_control_id": "5" * 64,
                          "health_control_id": "6" * 64,
                          "health_work_id": "7" * 64}
        candidates.append(CandidateResult.model_validate({
            "candidate_id": current_id, "selection": selection,
            "seed1_qualified": True, "all_seed_qualified": True,
            "context_probe": current_probe,
            "cancellation_health": current_cancel,
            "seed_results": results,
            "worst_seed_macro_f1": {"numerator": 1, "denominator": 1},
        }, strict=True))
    ranking = _ranking(candidates)
    assert ranking["winner_candidate_id"] is None
    assert len(ranking["pairs"]) == 1
    pair = ranking["pairs"][0]
    assert pair["replicates"] == 10_000
    assert pair["ci_low"] == pair["ci_high"] == {"numerator": 0, "denominator": 1}
    assert pair["left_decisive"] is pair["right_decisive"] is False


def _component(component, selection, candidate_id, document_ids, *, chunks,
               supports, pairs, boundaries, truncations):
    return AcceptanceComponent.model_validate({
        "version": "stage-f-acceptance-component-v1", "component": component,
        "source_plan_sha256": "1" * 64,
        "source_aggregate_sha256": "2" * 64,
        "candidate_id": candidate_id, "selection": selection,
        "document_ids": list(document_ids), "expected_chunks": chunks,
        "completed_chunks": chunks, "first_pass_invalid_chunks": 0,
        "eventual_invalid_chunks": 0, "raw_findings": 20,
        "raw_grounded_findings": 20, "retained_findings": 20,
        "retained_grounded_findings": 20,
        "category_recall": {category: {
            "true_positives": support, "support": support}
            for category, support in zip(
                ("pii", "financial", "contact", "demographic"),
                supports, strict=True)},
        "negative_false_positive_documents": 0,
        "injection_pairs": pairs, "injection_pairs_measured": pairs,
        "injection_events": 0, "robustness_failures": 0,
        "boundary_documents": boundaries, "boundary_passed": boundaries,
        "truncation_documents": truncations,
        "truncation_completed": truncations, "length_outcomes": 0,
        "context_failures": 0, "channel_violations": 0,
        "component_passed": True,
    }, strict=True).model_dump(mode="json")


def test_acceptance_rejects_component_and_decision_lineage_forgery(scoring_inputs) -> None:
    corpus, _selection, master, _plan, candidate_id, evidence, _result = scoring_inputs
    probe, cancellation = _passed_controls(master, candidate_id)
    seed1 = build_seed1_evidence_from_attempts(
        master, {candidate_id: evidence}, {candidate_id: probe},
        {candidate_id: cancellation}, corpus=corpus)
    activation = build_seed_activation_decision(master, seed1)
    attempts = {candidate_id: {
        envelope["payload"]["work"][0]["seed"]: _attempts_for_group(
            envelope["payload"], candidate_id, corpus)
        for envelope in master["plans"]
    }}
    f_aggregate = build_stage_f_aggregate_from_attempts(
        master, seed1, activation, attempts,
        seed_activation_decision_sha256="7" * 64, corpus=corpus)
    provisional = build_provisional_decision(f_aggregate)
    provisional_digest = "9" * 64
    acceptance = build_acceptance_plan(
        master, candidate_id=candidate_id,
        provisional_decision_sha256=provisional_digest)
    # The public owner-aware API must reject before trusting coherent component facts.
    with pytest.raises(
            StageFError,
            match="plan/provisional owner|plan owner|source aggregate|final decision"):
        build_acceptance_aggregate(
            acceptance, provisional_decision=provisional,
            provisional_decision_sha256=provisional_digest,
            c44_scored={}, final_d_decision={}, d50_source_aggregate={},
            stage_d_decision_sha256="a" * 64,
            stage_f_aggregate=f_aggregate,
            f_master=master, corpus=corpus,
            cancellation_health_passed=True,
            provenance_passed=True, safety_passed=True)

    payload_hash = sha256_json(provisional)
    assert payload_hash != provisional_digest
    wrong_plan = build_acceptance_plan(
        master, candidate_id=candidate_id,
        provisional_decision_sha256=payload_hash)
    with pytest.raises(StageFError, match="provisional decision owner"):
        build_acceptance_aggregate(
            wrong_plan, provisional_decision=provisional,
            provisional_decision_sha256=provisional_digest,
            c44_scored={}, final_d_decision={}, d50_source_aggregate={},
            stage_d_decision_sha256="a" * 64,
            stage_f_aggregate=f_aggregate,
            f_master=master, corpus=corpus,
            cancellation_health_passed=True,
            provenance_passed=True, safety_passed=True)


def test_acceptance_fraction_count_thresholds_and_reason_order_are_exact(
        scoring_inputs) -> None:
    corpus, selection, master, _plan, candidate_id, _evidence, _result = scoring_inputs
    plan = build_acceptance_plan(
        master, candidate_id=candidate_id,
        provisional_decision_sha256="9" * 64)
    components = [
        _component(
            "C44_RERUN", selection, candidate_id, corpus.c_order,
            chunks=44, supports=(6, 6, 6, 6), pairs=4,
            boundaries=0, truncations=0),
        _component(
            "D50_CONFIRMATION", selection, candidate_id, corpus.d_order,
            chunks=81, supports=(6, 6, 6, 6), pairs=0,
            boundaries=12, truncations=2),
        _component(
            "F72_SEED1", selection, candidate_id, corpus.f_order,
            chunks=122, supports=(8, 8, 8, 8), pairs=4,
            boundaries=12, truncations=4),
    ]
    # Exact acceptance edges: two first invalid, 99/100 raw grounding,
    # and 18/20 recall all pass without float rounding.
    components[0]["first_pass_invalid_chunks"] = 2
    components[0]["raw_findings"] = 100
    components[0]["raw_grounded_findings"] = 99
    components[0]["retained_findings"] = 99
    components[0]["retained_grounded_findings"] = 99
    for row in components[1:]:
        for field in ("raw_findings", "raw_grounded_findings",
                      "retained_findings", "retained_grounded_findings"):
            row[field] = 0
    for row in components:
        for category in row["category_recall"]:
            if row["component"] in {"C44_RERUN", "D50_CONFIRMATION"}:
                row["category_recall"][category]["true_positives"] = 5
    aggregate = _build_acceptance_aggregate_from_components(
        plan, components, corpus=corpus, cancellation_health_passed=True,
        provenance_passed=True, safety_passed=True)
    assert aggregate["passed"] is True
    assert aggregate["failure_reasons"] == []
    assert aggregate["totals"]["expected_chunks"] == 247
    assert aggregate["totals"]["category_recall"]["pii"] == {
        "true_positives": 18, "support": 20}

    hostile = copy.deepcopy(components)
    hostile[0]["first_pass_invalid_chunks"] = 3
    hostile[0]["raw_grounded_findings"] = 98
    hostile[0]["retained_findings"] = 98
    hostile[0]["retained_grounded_findings"] = 98
    hostile[0]["category_recall"]["pii"]["true_positives"] = 4
    aggregate = _build_acceptance_aggregate_from_components(
        plan, hostile, corpus=corpus, cancellation_health_passed=True,
        provenance_passed=True, safety_passed=True)
    assert aggregate["failure_reasons"][:3] == [
        "first_pass_invalid_chunks_above_2", "raw_grounding_below_0_99",
        "pii_recall_below_18_of_20"]


@pytest.mark.parametrize("reason", [
    "no_seed1_qualifier", "no_all_seed_qualifier", "ranking_not_decisive",
    "complete_corpus_acceptance_failed",
])
def test_inconclusive_terminal_reasons_are_closed(reason: str) -> None:
    result = build_inconclusive_result(reason, "a" * 64)
    assert result["terminal"] == "INCONCLUSIVE"
    assert result["reason"] == reason
    with pytest.raises(Exception):
        build_inconclusive_result("resource_timing_loss", "a" * 64)
