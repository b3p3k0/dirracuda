"""Offline runtime-policy boundaries for the C0B-3 public executor."""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import chunker
from scripts.analyst_benchmark import c0b3_policy as policy
from scripts.analyst_benchmark import c0b2_runtime_f as runtime_f
from scripts.analyst_benchmark.c0b2_checkpoint import (
    ImmutableViolation, canonical_json,
)
from scripts.analyst_benchmark.c0b2_public_schema import (
    stage_d_candidate_id,
)
from scripts.analyst_benchmark.c0b2_stage_d import build_stage_d_aggregate
from scripts.analyst_benchmark.c0b2_stage_d_plan import (
    build_d4_plan,
    derive_d_context_controls,
    load_d50,
)
from scripts.analyst_benchmark.c0b2_stage_f import (
    AcceptanceComponent, _build_acceptance_aggregate_from_components,
    build_f_seed_result,
)
from scripts.analyst_benchmark.c0b2_stage_f_plan import (
    build_acceptance_plan, build_f_master_plan,
    load_public_corpus,
)

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def manifest():
    return legacy_plan.build_master_manifest()


@pytest.fixture(scope="module")
def d_corpus(manifest):
    return load_d50(
        legacy_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256,
    )


@pytest.fixture(scope="module")
def public_corpus(manifest):
    return load_public_corpus(
        legacy_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256,
    )


def _candidate(phase: str) -> dict[str, object]:
    model, digest, _think = legacy_plan.MODELS[0]
    return {
        "candidate_id": stage_d_candidate_id(model, digest, "v2"),
        "model": model,
        "model_digest": digest,
        "worksheet": "v2",
        "chunk_chars": 8000 if phase == "D4" else 2000,
        "overlap": 256,
        "num_ctx": 8192 if phase == "D4" else 8192,
        "num_predict": 2048,
    }


def _d_source(work: Mapping[str, object], corpus) -> str:
    document = corpus.by_id()[work["doc_id"]]
    view = document.view_for(work["chunk_chars"])
    source = view.text if view is not None else document.text
    return chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=256,
    )[work["chunk_index"]].text


def _f_source(work: Mapping[str, object], corpus) -> str:
    document = corpus.by_id()[work["doc_id"]]
    source, _view = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    return chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=256,
    )[work["chunk_index"]].text


def _response(work: Mapping[str, object], corpus, source: str,
              false_positive_docs: frozenset[str]) -> str:
    document = corpus.by_id()[work["doc_id"]]
    findings = []
    if work["doc_id"] in false_positive_docs:
        quote = source.split()[0]
        findings.append({"category": "pii", "quote": quote,
                         "offset": source.index(quote)})
    else:
        for category, identifier in zip(
                document.categories_present, document.expected_identifiers,
                strict=False):
            if identifier in source:
                findings.append({"category": category, "quote": identifier,
                                 "offset": source.index(identifier)})
    return json.dumps({
        "document_type": "fixture",
        "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": findings,
    }, separators=(",", ":"))


def _attempt(work: Mapping[str, object], response: str) -> dict[str, object]:
    return {
        "attempt_id": legacy_plan.attempt_id(work["work_id"], 1),
        "work_id": work["work_id"],
        "attempt_no": 1,
        "call_class": "scored",
        "request_sha256": work["request_sha256"],
        "state": "ACCEPTED",
        "response": response,
        "done_reason": "stop",
        "prompt_eval_count": 100,
        "tools_empty": True,
        "images_empty": True,
        "unknown_message_fields_empty": True,
    }


def _false_positive_docs(
        corpus, count: int, document_ids=None) -> frozenset[str]:
    order = corpus.document_order if document_ids is None else document_ids
    rows = [doc_id for doc_id in dict.fromkeys(order)
            if corpus.by_id()[doc_id].stratum in {
                "negative_clean", "negative_near_miss"}]
    assert len(rows) >= count
    return frozenset(rows[:count])


def _d_evidence(plan, corpus, count: int):
    selected = _false_positive_docs(corpus, count)
    return {
        work["work_id"]: [_attempt(
            work, _response(work, corpus, _d_source(work, corpus), selected))]
        for work in plan["work"]
    }


def _d_probes(plan, corpus):
    controls = derive_d_context_controls(
        plan, corpus=corpus, run_nonce_key=KEY)
    probes = []
    for control in controls:
        first = next(row for row in plan["work"]
                     if row["candidate_id"] == control["candidate_id"])
        probes.append({
            "control_id": control["control_id"],
            "purpose": control["purpose"],
            "candidate_id": control["candidate_id"],
            "model": control["model"],
            "model_digest": control["model_digest"],
            "config_sha256": control["config_sha256"],
            "expected_num_ctx": control["minimum_context_length"],
            "observed_context_length": control["minimum_context_length"],
            "trigger_work_id": first["work_id"],
            "state": "PASSED",
            "response_sha256": hashlib.sha256(
                (control["control_id"] + ":response").encode()).hexdigest(),
        })
    return controls, probes


def _f_evidence(plan, candidate_id: str, corpus, count: int):
    candidate_work = [work for work in plan["work"]
                      if work["candidate_id"] == candidate_id]
    selected = _false_positive_docs(
        corpus, count, (work["doc_id"] for work in candidate_work))
    return {
        work["work_id"]: [_attempt(
            work, _response(work, corpus, _f_source(work, corpus), selected))]
        for work in candidate_work
    }


def _acceptance_component(
        name: str, selection, candidate_id: str, document_ids, *,
        chunks: int, supports: tuple[int, int, int, int], pairs: int,
        boundaries: int, truncations: int, false_positives: int) -> dict[str, object]:
    return {
        **policy.policy_binding(), "version": "stage-f-acceptance-component-v2",
        "component": name, "source_plan_sha256": "1" * 64,
        "source_aggregate_sha256": "2" * 64, "candidate_id": candidate_id,
        "selection": selection, "document_ids": list(document_ids),
        "expected_chunks": chunks, "completed_chunks": chunks,
        "first_pass_invalid_chunks": 0, "eventual_invalid_chunks": 0,
        "raw_findings": 20, "raw_grounded_findings": 20,
        "retained_findings": 20, "retained_grounded_findings": 20,
        "category_recall": {category: {"true_positives": support, "support": support}
                            for category, support in zip(
                                ("pii", "financial", "contact", "demographic"),
                                supports, strict=True)},
        "negative_false_positive_documents": false_positives,
        "injection_pairs": pairs, "injection_pairs_measured": pairs,
        "injection_events": 0, "robustness_failures": 0,
        "boundary_documents": boundaries, "boundary_passed": boundaries,
        "truncation_documents": truncations, "truncation_completed": truncations,
        "length_outcomes": 0, "context_failures": 0, "channel_violations": 0,
        "component_passed": True,
    }


@pytest.mark.parametrize("module_name,function_name", [
    ("scripts.analyst_benchmark.c0b2_runtime", "run_public_stage_c"),
    ("scripts.analyst_benchmark.c0b2_runtime_d", "run_public_stage_d"),
    ("scripts.analyst_benchmark.c0b2_runtime_f", "run_public_stage_f"),
])
@pytest.mark.parametrize("stored_header,expected_protocol_id", [
    ({"run_id": "legacy"}, policy.BENCHMARK_PROTOCOL_ID),
    ({"run_id": "current", **policy.header_identity()}, None),
])
def test_namespace_mismatch_fails_before_checkpoint_open(
        monkeypatch, tmp_path, module_name, function_name, stored_header,
        expected_protocol_id) -> None:
    runtime_c = importlib.import_module(
        "scripts.analyst_benchmark.c0b2_runtime")
    module = importlib.import_module(module_name)
    runner = getattr(module, function_name)
    opened = []

    def guard(_path, expected):
        return policy.require_expected_header(stored_header, expected)

    def forbidden_open(*_args, **_kwargs):
        opened.append(True)
        raise AssertionError("Checkpoint.open ran before the namespace guard")

    monkeypatch.setattr(policy, "require_checkpoint_header", guard)
    monkeypatch.setattr(runtime_c, "require_checkpoint_header", guard)
    monkeypatch.setattr(module.Checkpoint, "open", forbidden_open)
    with pytest.raises(policy.PolicyIdentityError):
        runner(
            "display-prefix-is-not-authority",
            benchmark_root=tmp_path,
            expected_protocol_id=expected_protocol_id,
            transport_factory=lambda *_args, **_kwargs: pytest.fail(
                "transport was constructed before the namespace guard"),
        )
    assert opened == []


def test_current_no_seed1_terminal_requires_exact_owners() -> None:
    point = SimpleNamespace(header=lambda: {"run_id": "current", **policy.header_identity()})
    with pytest.raises(ImmutableViolation, match="lacks exact owners"):
        runtime_f._finish_no_seed1(point, "a" * 64, "b" * 64, "c" * 64)


def test_current_acceptance_rejects_legacy_provisional_without_mutation() -> None:
    selection = {
        "model": "m", "model_digest": "d" * 64, "worksheet": "v2",
        "chunk_chars": 2000, "overlap": 256,
        "num_ctx": 8192, "num_predict": 2048,
    }
    legacy = {
        "version": "stage-f-selection-v1", "stage": "F",
        "plan_sha256": "a" * 64, "aggregate_sha256": "b" * 64,
        "outcome": "PROVISIONAL_SELECTED", "reason": "single_qualifier",
        "selection": selection,
    }
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE decisions(decision_id,stage,parent_hash,aggregate_hash,"
        "activation,value_json)")
    conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)", (
        "stage-f-provisional-selection", "F", "a" * 64, "b" * 64,
        "ACTIVATED", canonical_json(legacy)))
    point = SimpleNamespace(
        conn=conn, header=lambda: {"run_id": "current", **policy.header_identity()})
    before = tuple(conn.iterdump())
    with pytest.raises(ImmutableViolation, match="mixed lineage"):
        runtime_f._provisional_selection(
            point, {}, "a" * 64, "b" * 64, {})
    assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("resolved_policy,false_positives,expected_pass,reason", [
    (policy.LEGACY_POLICY, 0, True, None),
    (policy.LEGACY_POLICY, 1, False, "negative_false_positive_present"),
    (policy.LEGACY_POLICY, 2, False, "negative_false_positive_present"),
    (policy.CURRENT_POLICY, 0, True, None),
    (policy.CURRENT_POLICY, 1, True, None),
    (policy.CURRENT_POLICY, 2, False, "negative_false_positive_above_1"),
])
def test_d4_false_positive_document_boundaries(
        d_corpus, resolved_policy, false_positives,
        expected_pass, reason) -> None:
    plan = build_d4_plan(
        "4" * 64, [_candidate("D4")], corpus=d_corpus,
        run_nonce_key=KEY, policy=resolved_policy)
    controls, probes = _d_probes(plan, d_corpus)
    aggregate = build_stage_d_aggregate(
        plan, _d_evidence(plan, d_corpus, false_positives),
        corpus=d_corpus, context_controls=controls, context_probes=probes)
    quality = aggregate["candidates"][0]["quality"]
    assert quality["negative_false_positive_documents"] == false_positives
    assert quality["passed"] is expected_pass
    assert (reason in quality["failure_reasons"]) is (reason is not None)
    assert [item for item in quality["failure_reasons"]
            if item.startswith("negative_false_positive_")] == (
                [] if reason is None else [reason])


@pytest.mark.parametrize("resolved_policy,false_positives,expected_pass,reason", [
    (policy.LEGACY_POLICY, 0, True, None),
    (policy.LEGACY_POLICY, 1, False, "negative_false_positive_present"),
    (policy.LEGACY_POLICY, 2, False, "negative_false_positive_present"),
    (policy.CURRENT_POLICY, 0, True, None),
    (policy.CURRENT_POLICY, 1, True, None),
    (policy.CURRENT_POLICY, 2, False, "negative_false_positive_above_1"),
])
def test_each_stage_f_seed_uses_its_policy_boundary(
        public_corpus, resolved_policy, false_positives,
        expected_pass, reason) -> None:
    selection = dict(_candidate("F"))
    selection.pop("candidate_id")
    master = build_f_master_plan(
        "a" * 64, [selection], corpus=public_corpus,
        run_nonce_key=KEY, policy=resolved_policy)
    candidate_id = master["base_candidate_order"][0]
    for envelope in master["plans"]:
        plan = envelope["payload"]
        evidence = _f_evidence(
            plan, candidate_id, public_corpus, false_positives)
        result = build_f_seed_result(
            plan, candidate_id, evidence, corpus=public_corpus)
        assert result["seed"] in {1, 17, 20260804}
        assert result["negative_false_positive_documents"] == false_positives
        assert result["passed"] is expected_pass
        assert (reason in result["failure_reasons"]) is (reason is not None)
        assert [item for item in result["failure_reasons"]
                if item.startswith("negative_false_positive_")] == (
                [] if reason is None else [reason])


@pytest.mark.parametrize("false_positives,expected_pass", [(0, True), (1, True), (2, False)])
def test_final_acceptance_keeps_exact_one_of_40_boundary(
        public_corpus, false_positives: int, expected_pass: bool) -> None:
    selection = dict(_candidate("F"))
    selection.pop("candidate_id")
    master = build_f_master_plan(
        "a" * 64, [selection], corpus=public_corpus,
        run_nonce_key=KEY, policy=policy.CURRENT_POLICY)
    candidate_id = master["base_candidate_order"][0]
    plan = build_acceptance_plan(
        master, candidate_id=candidate_id,
        provisional_decision_sha256="9" * 64)
    fp = (min(false_positives, 1), max(false_positives - 1, 0), 0)
    components = [
        _acceptance_component(
            "C44_RERUN", selection, candidate_id, public_corpus.c_order,
            chunks=44, supports=(6, 6, 6, 6), pairs=4,
            boundaries=0, truncations=0, false_positives=fp[0]),
        _acceptance_component(
            "D50_CONFIRMATION", selection, candidate_id, public_corpus.d_order,
            chunks=81, supports=(6, 6, 6, 6), pairs=0,
            boundaries=12, truncations=2, false_positives=fp[1]),
        _acceptance_component(
            "F72_SEED1", selection, candidate_id, public_corpus.f_order,
            chunks=122, supports=(8, 8, 8, 8), pairs=4,
            boundaries=12, truncations=4, false_positives=fp[2]),
    ]
    assert all(AcceptanceComponent.model_validate({
        **{key: value for key, value in row.items()
           if key not in {"policy_id", "policy_sha256"}},
        "version": "stage-f-acceptance-component-v1",
    }, strict=True) for row in components)
    aggregate = _build_acceptance_aggregate_from_components(
        plan, components, corpus=public_corpus,
        cancellation_health_passed=True, provenance_passed=True,
        safety_passed=True)
    assert aggregate["totals"]["negative_false_positive_documents"] == false_positives
    assert aggregate["passed"] is expected_pass
    assert ("negative_false_positive_above_1" in aggregate["failure_reasons"]) \
        is (not expected_pass)
