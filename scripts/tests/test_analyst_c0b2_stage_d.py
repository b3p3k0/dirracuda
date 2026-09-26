"""Hostile offline tests for the frozen C0B-2 Stage-D scorer."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark.c0b2_public_schema import (
    sha256_json,
    stage_d_candidate_id,
)
from scripts.analyst_benchmark.c0b2_stage_d import (
    AttemptEvidence,
    StageDError,
    build_d4_final_decision,
    build_stage_d_aggregate,
    build_stage_d_decision,
    d3_decision_record_sha256,
    derive_work_evidence,
    validate_final_stage_d_decision,
    validate_stage_d_aggregate,
)
from scripts.analyst_benchmark.c0b2_stage_d_plan import (
    build_d1_plan,
    build_d2_plan,
    build_d3_plan,
    build_d4_plan,
    derive_d_context_controls,
    load_d50,
)

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def corpus():
    master = legacy_plan.build_master_manifest()
    return load_d50(
        legacy_plan.master_manifest_payload(master),
        master_manifest_sha256=master.sha256,
    )


def _candidate(model_index: int, phase: str, *, chunk: int = 8000,
               context: int = 4096, output: int = 2048) -> dict[str, object]:
    model, digest, _think = legacy_plan.MODELS[model_index]
    factors = {
        "D1": (None, None, None, None),
        "D2": (None, None, None, output),
        "D3": (chunk, 256, None, output),
        "D4": (chunk, 256, context, output),
    }[phase]
    return {
        "candidate_id": stage_d_candidate_id(model, digest, "v2"),
        "model": model, "model_digest": digest, "worksheet": "v2",
        "chunk_chars": factors[0], "overlap": factors[1],
        "num_ctx": factors[2], "num_predict": factors[3],
    }


def _chunk_source(work: dict[str, object], corpus) -> str:
    document = corpus.by_id()[work["doc_id"]]
    view = document.view_for(work["chunk_chars"])
    source = view.text if view is not None else document.text
    from scripts.analyst_benchmark import chunker
    return chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=256
    )[work["chunk_index"]].text


def _response(work: dict[str, object], corpus, *, false_positive: bool = False) -> str:
    document = corpus.by_id()[work["doc_id"]]
    source = _chunk_source(work, corpus)
    findings = []
    if false_positive and not document.categories_present:
        quote = source.split()[0]
        findings.append({"category": "pii", "quote": quote,
                         "offset": source.index(quote)})
    elif document.categories_present:
        category = document.categories_present[0]
        for identifier in document.expected_identifiers:
            if identifier in source:
                findings.append({"category": category, "quote": identifier,
                                 "offset": source.index(identifier)})
                break
    return json.dumps({
        "document_type": "fixture", "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": findings,
    }, separators=(",", ":"))


def _evidence(plan: dict[str, object], corpus, *, prompt_counts=1000,
              length_budgets=frozenset(), false_positive_candidate=None):
    values = {}
    for work in plan["work"]:
        prompt_count = (prompt_counts.get(work["candidate_id"], 1000)
                        if isinstance(prompt_counts, dict) else prompt_counts)
        false_positive = (false_positive_candidate == work["candidate_id"]
                          and corpus.by_id()[work["doc_id"]].stratum
                          in {"negative_clean", "negative_near_miss"})
        values[work["work_id"]] = [{
            "attempt_id": legacy_plan.attempt_id(work["work_id"], 1),
            "work_id": work["work_id"], "attempt_no": 1,
            "call_class": "scored", "request_sha256": work["request_sha256"],
            "state": "ACCEPTED", "response": _response(
                work, corpus, false_positive=false_positive),
            "done_reason": "length" if work["num_predict"] in length_budgets else "stop",
            "prompt_eval_count": prompt_count,
            "tools_empty": True, "images_empty": True,
            "unknown_message_fields_empty": True,
        }]
    return values


def _probes(plan: dict[str, object], corpus):
    controls = derive_d_context_controls(plan, corpus=corpus, run_nonce_key=KEY)
    probes = []
    for control in controls:
        first = next(row for row in plan["work"]
                     if row["candidate_id"] == control["candidate_id"])
        probes.append({
            "control_id": control["control_id"], "purpose": control["purpose"],
            "candidate_id": control["candidate_id"], "model": control["model"],
            "model_digest": control["model_digest"],
            "config_sha256": control["config_sha256"],
            "expected_num_ctx": control["minimum_context_length"],
            "observed_context_length": control["minimum_context_length"],
            "trigger_work_id": first["work_id"], "state": "PASSED",
            "response_sha256": hashlib.sha256(
                (control["control_id"] + ":response").encode()).hexdigest(),
        })
    return controls, probes


def _aggregate(plan, corpus, **evidence_options):
    controls, probes = ((), ()) if plan["phase"] in {"D1", "D2"} else _probes(
        plan, corpus)
    return build_stage_d_aggregate(
        plan, _evidence(plan, corpus, **evidence_options), corpus=corpus,
        context_controls=controls, context_probes=probes,
    )


def test_attempt_schema_is_exact_and_refuses_coercion(corpus) -> None:
    plan = build_d1_plan("1" * 64, [_candidate(0, "D1")],
                         corpus=corpus, run_nonce_key=KEY)
    work = plan["work"][0]
    row = _evidence(plan, corpus)[work["work_id"]][0]
    AttemptEvidence.model_validate(row, strict=True)
    for name, value in (("attempt_no", True), ("tools_empty", 1),
                        ("prompt_eval_count", "1000")):
        hostile = {**row, name: value}
        with pytest.raises(ValidationError):
            AttemptEvidence.model_validate(hostile, strict=True)
    with pytest.raises(ValidationError):
        AttemptEvidence.model_validate({**row, "extra": False}, strict=True)
    with pytest.raises(ValidationError):
        AttemptEvidence.model_validate({**row, "response": None}, strict=True)


def test_work_derivation_checks_attempt_authority_and_answer_state(corpus) -> None:
    plan = build_d1_plan("1" * 64, [_candidate(0, "D1")],
                         corpus=corpus, run_nonce_key=KEY)
    work = plan["work"][0]
    row = _evidence(plan, corpus)[work["work_id"]][0]
    source = _chunk_source(work, corpus)
    assert derive_work_evidence(work, source, 0, [row]).eventual_valid
    with pytest.raises(StageDError, match="authority"):
        derive_work_evidence(work, source, 0, [{**row, "work_id": "f" * 64}])
    with pytest.raises(StageDError, match="contradicts"):
        derive_work_evidence(work, source, 0, [{
            **row, "state": "SCHEMA_INVALID"}])
    retry = {**row, "attempt_id": legacy_plan.attempt_id(work["work_id"], 2),
             "attempt_no": 2,
             "call_class": "schema_retry"}
    with pytest.raises(StageDError, match="class differs|after authoritative"):
        derive_work_evidence(work, source, 0, [row, retry])


def test_attempt_lineage_is_contiguous_stable_and_prior_state_owned(corpus) -> None:
    plan = build_d1_plan("1" * 64, [_candidate(0, "D1")],
                         corpus=corpus, run_nonce_key=KEY)
    work = plan["work"][0]
    valid = _evidence(plan, corpus)[work["work_id"]][0]
    source = _chunk_source(work, corpus)
    with pytest.raises(StageDError, match="contiguous"):
        derive_work_evidence(work, source, 0, [{
            **valid, "attempt_no": 2,
            "attempt_id": legacy_plan.attempt_id(work["work_id"], 2)}])
    with pytest.raises(StageDError, match="attempt ID"):
        derive_work_evidence(work, source, 0, [{**valid, "attempt_id": "f" * 64}])
    invalid = {
        **valid, "state": "SCHEMA_INVALID", "response": "{}",
    }
    retry = {
        **valid, "attempt_no": 2,
        "attempt_id": legacy_plan.attempt_id(work["work_id"], 2),
        "call_class": "transport_orphan",
    }
    with pytest.raises(StageDError, match="class differs"):
        derive_work_evidence(work, source, 0, [invalid, retry])
    retry["call_class"] = "schema_retry"
    assert derive_work_evidence(work, source, 0, [invalid, retry]).eventual_valid


def test_d1_selects_smallest_passing_budget_and_orders_reasons(corpus) -> None:
    plan = build_d1_plan("1" * 64, [_candidate(0, "D1")],
                         corpus=corpus, run_nonce_key=KEY)
    aggregate = _aggregate(plan, corpus, length_budgets={2048})
    row = aggregate["candidates"][0]
    assert row["selected_num_predict"] == 3072
    assert row["levels"][0]["quality"]["failure_reasons"] == [
        "length_outcome_present"]
    decision = build_stage_d_decision(aggregate, plan)
    assert decision["outcome"] == "CONTINUE"
    assert decision["selections"][0]["num_predict"] == 3072


def test_d2_selects_largest_passing_chunk_and_enforces_completeness(corpus) -> None:
    plan = build_d2_plan("2" * 64, [_candidate(0, "D2")],
                         corpus=corpus, run_nonce_key=KEY)
    evidence = _evidence(plan, corpus)
    aggregate = build_stage_d_aggregate(plan, evidence, corpus=corpus)
    row = aggregate["candidates"][0]
    assert row["selected_chunk_chars"] == 8000
    assert [level["chunk_chars"] for level in row["levels"]] == [2000, 4000, 8000]
    incomplete = dict(evidence)
    incomplete.pop(next(iter(incomplete)))
    with pytest.raises(StageDError, match="cover exactly"):
        build_stage_d_aggregate(plan, incomplete, corpus=corpus)


@pytest.fixture(scope="module")
def mixed_d3(corpus):
    candidates = [_candidate(0, "D3"), _candidate(1, "D3")]
    plan = build_d3_plan("3" * 64, candidates, corpus=corpus, run_nonce_key=KEY)
    counts = {candidates[0]["candidate_id"]: 11000,
              candidates[1]["candidate_id"]: 1000}
    aggregate = _aggregate(plan, corpus, prompt_counts=counts)
    return plan, aggregate


def test_d3_census_is_measured_and_probe_is_exact(corpus, mixed_d3) -> None:
    plan, aggregate = mixed_d3
    assert [row["selected_num_ctx"] for row in aggregate["candidates"]] == [16384, 4096]
    assert [row["num_ctx"] for row in aggregate["candidates"][0]["context_census"]] \
        == [4096, 8192, 16384]
    decision = build_stage_d_decision(aggregate, plan)
    assert decision["outcome"] == "CONTINUE"
    controls, probes = _probes(plan, corpus)
    probes[0] = {**probes[0], "trigger_work_id": "f" * 64}
    with pytest.raises(StageDError, match="differs"):
        build_stage_d_aggregate(
            plan, _evidence(plan, corpus, prompt_counts={
                plan["candidates"][0]["candidate_id"]: 11000,
                plan["candidates"][1]["candidate_id"]: 1000}),
            corpus=corpus, context_controls=controls, context_probes=probes)


def test_d4_detects_json_escaped_marker_in_decoded_answer(corpus) -> None:
    plan = build_d4_plan(
        "4" * 64, [_candidate(0, "D4")],
        corpus=corpus, run_nonce_key=KEY)
    evidence = _evidence(plan, corpus)
    work = plan["work"][0]
    row = evidence[work["work_id"]][0]
    escaped = work["nonce"].replace("F", "\\u0046", 1)
    row["response"] = row["response"].replace(
        '"subject":""', f'"subject":"{escaped}"')
    controls, probes = _probes(plan, corpus)

    aggregate = build_stage_d_aggregate(
        plan, evidence, corpus=corpus,
        context_controls=controls, context_probes=probes)

    quality = aggregate["candidates"][0]["quality"]
    assert quality["marker_empty"] is False
    assert "channel_violation_present" in quality["failure_reasons"]


def test_d3_all_reuse_final_decision_has_exact_owner(corpus) -> None:
    plan = build_d3_plan("3" * 64, [_candidate(0, "D3")],
                         corpus=corpus, run_nonce_key=KEY)
    aggregate = _aggregate(plan, corpus, prompt_counts=11000)
    decision = build_stage_d_decision(aggregate, plan)
    assert (decision["phase"], decision["outcome"],
            decision["selections"][0]["evidence_source"]) == (
                "D3", "FINALISTS", "D3_REUSE")
    assert validate_final_stage_d_decision(
        decision, owner_plan=plan, owner_aggregate=aggregate) == decision
    hostile = copy.deepcopy(decision)
    hostile["selections"][0]["source_aggregate_sha256"] = "f" * 64
    with pytest.raises(StageDError, match="differs"):
        validate_final_stage_d_decision(
            hostile, owner_plan=plan, owner_aggregate=aggregate)


def test_d4_merge_truth_table_preserves_d3_order(corpus, mixed_d3) -> None:
    d3_plan, d3_aggregate = mixed_d3
    d3_decision = build_stage_d_decision(d3_aggregate, d3_plan)
    reruns = [row for row in d3_decision["selections"] if row["num_ctx"] != 16384]
    d4_plan = build_d4_plan(
        d3_decision_record_sha256(d3_decision), reruns,
        corpus=corpus, run_nonce_key=KEY)
    passed = _aggregate(d4_plan, corpus)
    with pytest.raises(StageDError, match="exact merge"):
        build_stage_d_decision(passed, d4_plan)
    final = build_d4_final_decision(
        passed, d4_plan, d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    assert [row["evidence_source"] for row in final["selections"]] == [
        "D3_REUSE", "D4_RERUN"]
    assert validate_final_stage_d_decision(
        final, owner_plan=d4_plan, owner_aggregate=passed,
        d3_plan=d3_plan, d3_aggregate=d3_aggregate) == final

    failed = _aggregate(
        d4_plan, corpus, false_positive_candidate=reruns[0]["candidate_id"])
    final = build_d4_final_decision(
        failed, d4_plan, d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    assert [row["evidence_source"] for row in final["selections"]] == ["D3_REUSE"]

    wrong_parent = copy.deepcopy(d4_plan)
    wrong_parent["parent_decision_sha256"] = "f" * 64
    with pytest.raises(StageDError, match="ownership|parent"):
        build_d4_final_decision(
            passed, wrong_parent, d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    wrong_factor = copy.deepcopy(d4_plan)
    wrong_factor["candidates"][0]["num_ctx"] = 8192
    wrong_factor_aggregate = copy.deepcopy(passed)
    wrong_factor_aggregate["plan_sha256"] = sha256_json(wrong_factor)
    with pytest.raises(StageDError, match="factor|probe"):
        build_d4_final_decision(
            wrong_factor_aggregate, wrong_factor,
            d3_aggregate=d3_aggregate, d3_plan=d3_plan)


def test_d4_merged_empty_is_exact_inconclusive(corpus) -> None:
    candidates = [_candidate(0, "D3"), _candidate(1, "D3")]
    d3_plan = build_d3_plan(
        "3" * 64, candidates, corpus=corpus, run_nonce_key=KEY)
    counts = {row["candidate_id"]: 1000 for row in candidates}
    d3_aggregate = _aggregate(d3_plan, corpus, prompt_counts=counts)
    d3_decision = build_stage_d_decision(d3_aggregate, d3_plan)
    d4_plan = build_d4_plan(
        d3_decision_record_sha256(d3_decision), d3_decision["selections"],
        corpus=corpus, run_nonce_key=KEY)
    evidence = _evidence(d4_plan, corpus)
    for work in d4_plan["work"]:
        document = corpus.by_id()[work["doc_id"]]
        if document.stratum in {"negative_clean", "negative_near_miss"}:
            evidence[work["work_id"]] = _evidence(
                {"work": [work]}, corpus,
                false_positive_candidate=work["candidate_id"])[work["work_id"]]
    controls, probes = _probes(d4_plan, corpus)
    d4_aggregate = build_stage_d_aggregate(
        d4_plan, evidence, corpus=corpus,
        context_controls=controls, context_probes=probes)
    final = build_d4_final_decision(
        d4_aggregate, d4_plan,
        d3_aggregate=d3_aggregate, d3_plan=d3_plan)
    assert (final["outcome"], final["reason"], final["selections"]) == (
        "INCONCLUSIVE", "no_d4_confirmation_finalist", [])


def test_strict_aggregate_rejects_shape_coercion_and_order_tamper(corpus) -> None:
    plan = build_d1_plan("1" * 64, [_candidate(0, "D1")],
                         corpus=corpus, run_nonce_key=KEY)
    aggregate = _aggregate(plan, corpus)
    for hostile in (
            {**aggregate, "extra": 1},
            {**aggregate, "candidate_order": ["f" * 64]},
            copy.deepcopy(aggregate)):
        if hostile is not aggregate and hostile.get("extra") is None \
                and hostile["candidate_order"] == aggregate["candidate_order"]:
            hostile["candidates"][0]["levels"][0]["quality"]["planned_chunks"] = True
        with pytest.raises(StageDError):
            validate_stage_d_aggregate(hostile)
    duplicate = copy.deepcopy(aggregate)
    duplicate["candidate_order"] = duplicate["candidate_order"] * 2
    duplicate["candidates"] = duplicate["candidates"] * 2
    with pytest.raises(StageDError, match="strict validation"):
        validate_stage_d_aggregate(duplicate)

    from collections import UserDict
    with pytest.raises(StageDError, match="exact JSON object"):
        build_stage_d_decision(aggregate, UserDict(plan))
