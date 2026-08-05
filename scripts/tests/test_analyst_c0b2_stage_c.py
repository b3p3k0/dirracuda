"""Pure Stage-C planning, selective loading, scoring and selection tests."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b2_plan as plan
from scripts.analyst_benchmark import c0b2_stage_c as stage_c


@pytest.fixture(scope="module")
def frozen_plan() -> plan.StagePlan:
    return plan.build_c_stage_plan(bytes(range(32)))


@pytest.fixture(scope="module")
def c44(frozen_plan: plan.StagePlan) -> stage_c.C44Corpus:
    return stage_c.load_c44(frozen_plan)


def _answer(worksheet: str, document: stage_c.C44Document, *,
            subject: str = "public fixture") -> str:
    categories = set(document.categories_present)
    quote = document.expected_identifiers[0] if categories else None
    offset = document.text.index(quote) if quote else 0
    assessment = "findings_present" if categories else "no_findings"
    if worksheet == "v1":
        rows = []
        for category in plan.worksheet_schema("v1")["$defs"]["V1CategoryRow"][
                "properties"]["category"]["enum"]:
            evidence = ([{"quote": quote, "offset": offset}]
                        if category in categories else [])
            rows.append({"category": category, "present": bool(evidence),
                         "evidence": evidence})
        value = {"document_type": "record", "subject": subject,
                 "assessment": assessment, "categories": rows}
    else:
        findings = [{"category": category, "quote": quote, "offset": offset}
                    for category in ("pii", "financial", "contact", "demographic")
                    if category in categories]
        value = {"document_type": "record", "subject": subject,
                 "assessment": assessment, "findings": findings}
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _no_findings_answer(worksheet: str) -> str:
    value: dict[str, object] = {
        "document_type": "record", "subject": "public fixture",
        "assessment": "no_findings",
    }
    if worksheet == "v1":
        value["categories"] = [
            {"category": category, "present": False, "evidence": []}
            for category in ("pii", "financial", "contact", "demographic")
        ]
    else:
        value["findings"] = []
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _perfect_evidence(stage_plan: plan.StagePlan,
                      corpus: stage_c.C44Corpus) -> dict[str, list[stage_c.AttemptEvidence]]:
    documents = corpus.by_id()
    return {
        item.work_id: [stage_c.AttemptEvidence(
            1, "scored", "ACCEPTED",
            _answer(item.worksheet, documents[item.doc_id]), "stop", True, True, True)]
        for item in stage_plan.work
    }


def test_plan_is_model_major_without_changing_request_set(
        frozen_plan: plan.StagePlan) -> None:
    expected = [
        (model, worksheet, doc_id)
        for model, _digest, _think in plan.MODELS
        for worksheet in plan.WORKSHEETS
        for doc_id in plan.build_master_manifest().split.c
    ]
    assert [(item.model, item.worksheet, item.doc_id)
            for item in frozen_plan.work] == expected
    assert len(frozen_plan.work) == len({item.work_id for item in frozen_plan.work}) == 264
    assert len({item.request_sha256 for item in frozen_plan.work}) == 264


def test_plan_payload_helpers_are_serializable_and_hash_exact(
        frozen_plan: plan.StagePlan) -> None:
    stage_payload = plan.stage_plan_payload(frozen_plan)
    master = plan.build_master_manifest()
    master_payload = plan.master_manifest_payload(master)

    assert json.loads(json.dumps(stage_payload)) == stage_payload
    assert json.loads(json.dumps(master_payload)) == master_payload
    assert plan.stable_hash(stage_payload) == frozen_plan.sha256
    assert plan.stable_hash(master_payload) == master.sha256


def test_selective_loader_opens_manifest_and_only_c44_fixture_bytes(
        frozen_plan: plan.StagePlan) -> None:
    opened: list[Path] = []

    def tracked(path: Path) -> bytes:
        opened.append(path)
        return path.read_bytes()

    corpus = stage_c.load_c44(frozen_plan, read_bytes=tracked)
    fixture_paths = opened[1:]
    assert opened[0] == plan.goldset.MANIFEST
    assert len(fixture_paths) == 44
    assert [path.stem for path in fixture_paths] == [doc.doc_id for doc in corpus.documents]
    assert not any("bnd_" in path.name or "trunc_" in path.name for path in fixture_paths)


def test_resolve_work_reconstructs_exact_payload_and_rejects_source_drift(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    item = frozen_plan.work[0]
    resolved = stage_c.resolve_work(frozen_plan, item.work_id, corpus=c44)
    assert plan.stable_hash(resolved.payload) == item.request_sha256
    assert resolved.payload["model"] == item.model
    assert resolved.payload["messages"] == [{"role": "user", "content": resolved.prompt}]

    changed = replace(c44.documents[0], text=c44.documents[0].text + "changed")
    bad = replace(c44, documents=(changed, *c44.documents[1:]))
    with pytest.raises(stage_c.StageCError, match="document differs"):
        stage_c.resolve_work(frozen_plan, item.work_id, corpus=bad)


def test_structural_then_semantic_classification_is_mutually_exclusive(
        c44: stage_c.C44Corpus) -> None:
    valid = json.loads(_answer("v1", c44.documents[0]))
    assert stage_c.classify_answer("v1", json.dumps(valid)).valid

    extra = {**valid, "approved": True}
    classified = stage_c.classify_answer("v1", json.dumps(extra))
    assert not classified.structural_valid
    assert not classified.semantic_valid
    assert not classified.schema_escape_empty

    reversed_rows = copy.deepcopy(valid)
    reversed_rows["categories"].reverse()
    classified = stage_c.classify_answer("v1", json.dumps(reversed_rows))
    assert classified.structural_valid
    assert not classified.semantic_valid
    assert classified.errors == ("canonical_category_order",)

    wrong_type = copy.deepcopy(valid)
    wrong_type["categories"][0]["evidence"][0]["offset"] = True
    classified = stage_c.classify_answer("v1", json.dumps(wrong_type))
    assert not classified.structural_valid
    assert "evidence" in classified.errors


def test_document_scoring_uses_first_http_answer_and_eventual_retry(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    item = frozen_plan.work[0]
    document = c44.by_id()[item.doc_id]
    invalid = json.loads(_answer(item.worksheet, document))
    invalid["categories"].reverse()
    scored = stage_c.score_document(item, document, [
        stage_c.AttemptEvidence(1, "scored", "SCHEMA_INVALID",
                                json.dumps(invalid), "stop", True, True, True),
        stage_c.AttemptEvidence(2, "schema_retry", "ACCEPTED",
                                _answer(item.worksheet, document), "stop", True, True, True),
    ])
    assert scored.row["first_pass_valid"] is False
    assert scored.row["eventual_valid"] is True
    assert scored.row["strict_schema_invalid_attempts"] == 0
    assert scored.row["semantic_invalid_attempts"] == 1
    assert scored.row["charged_attempt_count"] == 2


def test_transport_attempt_is_charged_but_not_the_first_answer(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    item = frozen_plan.work[0]
    document = c44.by_id()[item.doc_id]
    scored = stage_c.score_document(item, document, [
        stage_c.AttemptEvidence(
            1, "scored", "RETRYABLE_TRANSPORT", None, None, True, True, True),
        stage_c.AttemptEvidence(
            2, "transport_orphan", "ACCEPTED",
            _answer(item.worksheet, document), "stop", True, True, True),
    ])
    assert scored.row["charged_attempt_count"] == 2
    assert scored.row["first_pass_valid"] is True
    assert scored.row["eventual_valid"] is True


def test_attempt_outcome_must_match_independent_validation(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    item = frozen_plan.work[0]
    document = c44.by_id()[item.doc_id]
    with pytest.raises(stage_c.StageCError, match="outcome contradicts"):
        stage_c.score_document(item, document, [stage_c.AttemptEvidence(
            1, "scored", "SCHEMA_INVALID",
            _answer(item.worksheet, document), "stop", True, True, True)])


def test_perfect_aggregate_has_exact_schema_and_all_cells_pass(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stage_c.goldset, "load",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage-C scorer opened the all-166 loader")))
    aggregate = stage_c.build_stage_c_aggregate(
        frozen_plan, _perfect_evidence(frozen_plan, c44), corpus=c44)

    assert set(aggregate) == {
        "version", "stage", "plan_sha256", "master_manifest_sha256",
        "category_order", "cells"}
    assert [cell["model"] for cell in aggregate["cells"]] == [
        model for model, _digest, _think in plan.MODELS for _worksheet in plan.WORKSHEETS]
    assert all(cell["passed"] for cell in aggregate["cells"])
    assert all(cell["first_pass_valid_count"] == 44 for cell in aggregate["cells"])
    assert all(cell["negative_documents"] == 12 for cell in aggregate["cells"])
    assert all(cell["injection_pairs_measured"] == 4 for cell in aggregate["cells"])
    assert all(cell["failure_reasons"] == [] for cell in aggregate["cells"])
    expected_cell_keys = {
        "cell_id", "model", "model_digest", "worksheet", "plan_sha256",
        "documents", "first_pass_valid_count", "eventual_valid_count",
        "strict_schema_invalid_attempts", "semantic_invalid_attempts",
        "raw_grounded_findings", "raw_findings", "category_recall",
        "negative_false_positive_documents", "negative_documents",
        "injection_pairs", "injection_pairs_measured", "injection_events",
        "robustness_failures", "length_outcomes", "channel_violations",
        "passed", "failure_reasons"}
    assert all(set(cell) == expected_cell_keys for cell in aggregate["cells"])


def test_marker_echo_is_a_deterministic_injection_failure(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    evidence = _perfect_evidence(frozen_plan, c44)
    target = next(item for item in frozen_plan.work
                  if item.model == plan.MODELS[0][0] and item.worksheet == "v1"
                  and item.doc_id == "inj_01")
    evidence[target.work_id] = [stage_c.AttemptEvidence(
        1, "scored", "ACCEPTED",
        _answer("v1", c44.by_id()["inj_01"], subject=plan.MARKERS["inj_01"]),
        "stop", True, True, True)]
    aggregate = stage_c.build_stage_c_aggregate(frozen_plan, evidence, corpus=c44)
    cell = aggregate["cells"][0]
    pair = cell["injection_pairs"][0]
    assert pair["events"] == ["marker_in_answer"]
    assert not pair["passed"]
    assert cell["failure_reasons"][:2] == [
        "injection_event_present", "injection_robustness_failure"]
    assert stage_c.validate_stage_c_aggregate_semantics(aggregate) == aggregate


def test_length_on_invalid_first_answer_fails_cell_after_valid_retry(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    evidence = _perfect_evidence(frozen_plan, c44)
    target = frozen_plan.work[0]
    evidence[target.work_id] = [
        stage_c.AttemptEvidence(
            1, "scored", "SCHEMA_INVALID", "{", "length", True, True, True),
        stage_c.AttemptEvidence(
            2, "schema_retry", "ACCEPTED",
            _answer(target.worksheet, c44.by_id()[target.doc_id]),
            "stop", True, True, True),
    ]

    aggregate = stage_c.build_stage_c_aggregate(frozen_plan, evidence, corpus=c44)
    document = aggregate["cells"][0]["documents"][0]
    cell = aggregate["cells"][0]
    assert document["done_reason"] == "stop"
    assert cell["length_outcomes"] == 1
    assert "length_outcome_present" in cell["failure_reasons"]
    assert not cell["passed"]


def test_exact_sha256_bootstrap_and_default_selection_are_reproducible(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    aggregate = stage_c.build_stage_c_aggregate(
        frozen_plan, _perfect_evidence(frozen_plan, c44), corpus=c44)
    first = stage_c.build_stage_c_selection(aggregate)
    second = stage_c.build_stage_c_selection(aggregate)
    assert first == second
    assert [row["selected_worksheet"] for row in first["models"]] == ["v2"] * 3
    assert [row["selection_basis"] for row in first["models"]] == [
        "v2_engineering_default"] * 3
    assert len(first["survivors"]) == 3
    for row in first["models"]:
        assert row["bootstrap"] == {
            "replicates": 10_000, "seed": 20260804, "rng": "sha256-counter-v1",
            "point": {"numerator": 0, "denominator": 1},
            "ci_low": {"numerator": 0, "denominator": 1},
            "ci_high": {"numerator": 0, "denominator": 1},
            "lower_index": 83, "upper_index": 9_916, "v1_decisive": False}


def test_bootstrap_selects_v1_only_when_lower_bound_exceeds_margin(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    evidence = _perfect_evidence(frozen_plan, c44)
    model = plan.MODELS[0][0]
    missed = {category: 0 for category in
              ("pii", "financial", "contact", "demographic")}
    for item in frozen_plan.work:
        document = c44.by_id()[item.doc_id]
        if (item.model == model and item.worksheet == "v2"
                and document.stratum == "positive_control"):
            category = document.categories_present[0]
            if missed[category] < 2:
                missed[category] += 1
                evidence[item.work_id] = [stage_c.AttemptEvidence(
                    1, "scored", "ACCEPTED", _no_findings_answer("v2"),
                    "stop", True, True, True)]
    aggregate = stage_c.build_stage_c_aggregate(frozen_plan, evidence, corpus=c44)
    selection = stage_c.build_stage_c_selection(aggregate)
    assert selection["models"][0]["selected_worksheet"] == "v1"
    assert selection["models"][0]["selection_basis"] == "v1_bootstrap"
    assert selection["models"][0]["bootstrap"]["v1_decisive"] is True


@pytest.mark.parametrize("mutate, message", [
    (lambda aggregate: aggregate["cells"][0].__setitem__(
        "first_pass_valid_count", 43), "counters differ"),
    (lambda aggregate: aggregate["cells"][0]["documents"][0].__setitem__(
        "strict_schema_invalid_attempts", 1), "attempt summary"),
    (lambda aggregate: aggregate["cells"][0]["injection_pairs"][0].update(
        events=["recall_drop"], passed=False), "injection events differ"),
    (lambda aggregate: aggregate["cells"][1]["documents"][0].__setitem__(
        "stratum", "negative_clean"), "labels drift"),
    (lambda aggregate: aggregate["cells"][0].update(
        passed=False, failure_reasons=["length_outcome_present"]),
     "pass result differs"),
])
def test_selection_rejects_aggregate_facts_not_derived_from_rows(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus,
        mutate, message: str) -> None:
    aggregate = stage_c.build_stage_c_aggregate(
        frozen_plan, _perfect_evidence(frozen_plan, c44), corpus=c44)
    mutate(aggregate)
    with pytest.raises(stage_c.StageCError, match=message):
        stage_c.build_stage_c_selection(aggregate)


def test_aggregate_rejects_unknown_work_evidence(
        frozen_plan: plan.StagePlan, c44: stage_c.C44Corpus) -> None:
    with pytest.raises(stage_c.StageCError, match="outside"):
        stage_c.build_stage_c_aggregate(
            frozen_plan, {"not-planned": []}, corpus=c44)
