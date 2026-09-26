"""Focused offline tests for the pure C0B-2 Stage-F planner."""
from __future__ import annotations

import copy

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import c0b2_stage_f_plan as stage_f_plan
from scripts.analyst_benchmark.c0b2_public_schema import (
    health_control_id, health_work_id, sha256_json,
)
from scripts.analyst_benchmark.c0b2_schema import canonical_json
from scripts.analyst_benchmark.c0b2_stage_f_plan import (
    EXPECTED_F_CHUNKS, StageFPlanError, build_acceptance_plan,
    build_f_master_plan, load_public_corpus, request_spec_for_f_work,
    request_specs_for_activated_f_plan, request_specs_for_f_plan,
    resolve_f_seed1_control, resolve_f_work, rotated_candidate_ids,
    validate_f_master_plan,
)
from scripts.analyst_benchmark.c0b2_transport import request_spec_hash

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def planning_inputs():
    master = legacy_plan.build_master_manifest()
    corpus = load_public_corpus(
        legacy_plan.master_manifest_payload(master),
        master_manifest_sha256=master.sha256)
    selections = []
    for (model, digest, _think), chunk in zip(
            legacy_plan.MODELS, (2000, 4000, 8000), strict=True):
        selections.append({
            "model": model, "model_digest": digest, "worksheet": "v2",
            "chunk_chars": chunk, "overlap": 256, "num_ctx": 8192,
            "num_predict": 2048,
        })
    return master, corpus, selections


def test_master_freezes_every_seed_group_control_and_c44_template(planning_inputs) -> None:
    master_manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "a" * 64, selections, corpus=corpus, run_nonce_key=KEY)
    assert master["seed_order"] == [1, 17, 20260804]
    assert [row["payload"]["plan_key"] for row in master["plans"]] == [
        "F_SEED_1", "F_SEED_17", "F_SEED_20260804"]
    assert [len(row["payload"]["work"]) for row in master["plans"]] == [314] * 3
    assert [row["planned_work_count"]
            for row in master["plans"][0]["payload"]["groups"]] == [122, 100, 92]
    assert all(row["plan_sha256"] == sha256_json(row["payload"])
               for row in master["plans"])
    assert [len(row["payload"]["work"])
            for row in master["acceptance_templates"]] == [44, 44, 44]
    assert all(row["template_sha256"] == sha256_json(row["payload"])
               for row in master["acceptance_templates"])
    assert master["master_manifest_sha256"] == master_manifest.sha256

    seed1 = master["plans"][0]["payload"]
    for group in seed1["groups"]:
        assert all(group[name] is not None for name in (
            "context_control", "cancellation_control", "health_control"))
        candidate_work = [row for row in seed1["work"]
                          if row["candidate_id"] == group["candidate_id"]]
        assert group["first_work_id"] == candidate_work[0]["work_id"]
        assert group["context_control"]["trigger_rule"] == \
            "first_http_terminal_seed1"
    assert all(all(group[name] is None for name in (
        "context_control", "cancellation_control", "health_control"))
        for envelope in master["plans"][1:] for group in envelope["payload"]["groups"])

    validate_f_master_plan(master, corpus=corpus, run_nonce_key=KEY)


def test_candidate_rotation_and_injection_nonce_registry_are_exact(planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "b" * 64, selections, corpus=corpus, run_nonce_key=KEY)
    base = master["base_candidate_order"]
    assert rotated_candidate_ids(master, 1) == base
    assert rotated_candidate_ids(master, 17) == base[1:] + base[:1]
    assert rotated_candidate_ids(master, 20260804) == base[2:] + base[:2]

    for envelope in master["plans"]:
        plan = envelope["payload"]
        for candidate_id in base:
            rows = [row for row in plan["work"]
                    if row["candidate_id"] == candidate_id]
            inj = next(row for row in rows if row["doc_id"] == "inj_05")
            twin = next(row for row in rows if row["doc_id"] == "inj_twin_05")
            assert inj["nonce"] == twin["nonce"]
    seed_nonces = {row["nonce"] for envelope in master["plans"]
                   for row in envelope["payload"]["work"]}
    acceptance_nonces = {row["nonce"] for template in master["acceptance_templates"]
                         for row in template["payload"]["work"]}
    assert not seed_nonces & acceptance_nonces
    assert all(nonce not in corpus.by_id()[row["doc_id"]].text
               for template in master["acceptance_templates"]
               for row in template["payload"]["work"]
               for nonce in [row["nonce"]])


def test_resolver_rederives_boundary_view_and_request_hash(planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "c" * 64, selections, corpus=corpus, run_nonce_key=KEY)
    plan = master["plans"][0]["payload"]
    work = next(row for row in plan["work"]
                if row["candidate_id"] == master["base_candidate_order"][0]
                and row["doc_id"] == "bnd_04_s02" and row["chunk_index"] == 0)
    assert work["view_id"] is not None
    resolved = resolve_f_work(
        master, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert resolved["work"] == work
    assert request_spec_for_f_work(
        master, work["work_id"], corpus=corpus, run_nonce_key=KEY
    ).payload == resolved["payload"]

    tampered = copy.deepcopy(master)
    owner = tampered["plans"][0]
    target = next(row for row in owner["payload"]["work"]
                  if row["work_id"] == work["work_id"])
    target["request_sha256"] = "0" * 64
    owner["plan_sha256"] = sha256_json(owner["payload"])
    with pytest.raises(StageFPlanError, match="strict validation|re-derivation"):
        validate_f_master_plan(tampered, corpus=corpus, run_nonce_key=KEY)


def test_seed1_control_resolver_rebuilds_exact_requests_and_sources(
        planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "8" * 64, selections[:1], corpus=corpus, run_nonce_key=KEY)
    seed1 = master["plans"][0]["payload"]
    group = seed1["groups"][0]
    candidate = seed1["candidates"][0]
    source = next(row for row in seed1["work"]
                  if row["doc_id"] == "pos_pii_013" and row["chunk_index"] == 0)

    context = resolve_f_seed1_control(
        master, group["context_control"]["control_id"],
        corpus=corpus, run_nonce_key=KEY)
    assert context.request_spec.kind == "ps"
    assert context.source_work_id == group["first_work_id"]
    assert context.source_chunk is None
    assert request_spec_hash(context.request_spec) == \
        group["context_control"]["payload_sha256"]

    cancellation = resolve_f_seed1_control(
        master, group["cancellation_control"]["control_id"],
        corpus=corpus, run_nonce_key=KEY)
    assert cancellation.request_spec.kind == "chat"
    assert cancellation.request_spec.cancel_on_first_content is True
    assert cancellation.source_work_id == source["work_id"]
    assert cancellation.request_spec.payload == request_spec_for_f_work(
        master, source["work_id"], corpus=corpus,
        run_nonce_key=KEY).payload
    assert request_spec_hash(cancellation.request_spec) == \
        group["cancellation_control"]["request_sha256"]

    health = resolve_f_seed1_control(
        master, group["health_control"]["control_id"],
        corpus=corpus, run_nonce_key=KEY)
    assert health.request_spec.kind == "chat"
    assert health.request_spec.cancel_on_first_content is False
    assert health.source_work_id == source["work_id"]
    assert health.source_chunk == cancellation.source_chunk
    assert group["health_control"]["nonce"] in \
        health.request_spec.payload["messages"][0]["content"]
    assert group["health_control"]["nonce"] not in health.source_chunk
    assert group["health_control"]["nonce"] != source["nonce"]
    assert health.request_spec.payload != cancellation.request_spec.payload
    assert health.request_spec.expected_model == candidate["model"]
    assert health.request_spec.expected_digest == candidate["model_digest"]
    assert health.request_spec.worksheet == candidate["worksheet"]
    assert health.request_spec.payload["options"]["num_ctx"] == candidate["num_ctx"]
    assert health.request_spec.payload["options"]["num_predict"] == \
        candidate["num_predict"]
    assert request_spec_hash(health.request_spec) == \
        group["health_control"]["request_sha256"]


def test_seed1_control_resolver_rejects_unknown_and_health_hash_substitution(
        planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "9" * 64, selections[:1], corpus=corpus, run_nonce_key=KEY)
    with pytest.raises(StageFPlanError, match="unknown or duplicate"):
        resolve_f_seed1_control(
            master, "0" * 64, corpus=corpus, run_nonce_key=KEY)

    duplicate = copy.deepcopy(master)
    duplicate_envelope = duplicate["plans"][0]
    duplicate_group = duplicate_envelope["payload"]["groups"][0]
    duplicate_group["health_control"]["control_id"] = \
        duplicate_group["context_control"]["control_id"]
    duplicate_envelope["plan_sha256"] = sha256_json(
        duplicate_envelope["payload"])
    with pytest.raises(StageFPlanError, match="strict validation"):
        resolve_f_seed1_control(
            duplicate, duplicate_group["context_control"]["control_id"],
            corpus=corpus, run_nonce_key=KEY)

    tampered = copy.deepcopy(master)
    envelope = tampered["plans"][0]
    group = envelope["payload"]["groups"][0]
    health = group["health_control"]
    health["request_sha256"] = group["cancellation_control"]["request_sha256"]
    health["control_id"] = health_control_id(
        candidate_id=health["candidate_id"], nonce=health["nonce"],
        request_sha256=health["request_sha256"])
    health["health_work_id"] = health_work_id(
        candidate_id=health["candidate_id"],
        request_sha256=health["request_sha256"])
    envelope["plan_sha256"] = sha256_json(envelope["payload"])
    with pytest.raises(StageFPlanError, match="re-derivation"):
        resolve_f_seed1_control(
            tampered, health["control_id"], corpus=corpus, run_nonce_key=KEY)


def test_bulk_seed_resolver_validates_once_and_preserves_exact_order(
        monkeypatch, planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "b" * 64, selections[:1], corpus=corpus, run_nonce_key=KEY)
    plan = master["plans"][1]["payload"]
    original = stage_f_plan.validate_f_master_plan
    original_index = stage_f_plan._validated_work_index
    validation_calls = 0
    index_calls = 0

    def counted(*args, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        return original(*args, **kwargs)

    def counted_index(*args, **kwargs):
        nonlocal index_calls
        index_calls += 1
        return original_index(*args, **kwargs)

    monkeypatch.setattr(stage_f_plan, "validate_f_master_plan", counted)
    monkeypatch.setattr(stage_f_plan, "_validated_work_index", counted_index)
    specs = request_specs_for_f_plan(
        master, "F_SEED_17", corpus=corpus, run_nonce_key=KEY)

    assert validation_calls == 1
    assert index_calls == 1
    assert list(specs) == [row["work_id"] for row in plan["work"]]
    for row in plan["work"]:
        spec = specs[row["work_id"]]
        assert request_spec_hash(spec) == row["request_sha256"]
        assert spec.expected_model == row["model"]
        assert spec.expected_digest == row["model_digest"]
        assert spec.worksheet == row["worksheet"]


def test_bulk_acceptance_requires_and_binds_exact_activated_plan(
        monkeypatch, planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "c" * 64, selections[:1], corpus=corpus, run_nonce_key=KEY)
    template = master["acceptance_templates"][0]
    activated = build_acceptance_plan(
        master, candidate_id=template["candidate_id"],
        provisional_decision_sha256="d" * 64)

    with pytest.raises(StageFPlanError, match="requires its plan"):
        request_specs_for_f_plan(
            master, "F_ACCEPTANCE", corpus=corpus, run_nonce_key=KEY)

    original = stage_f_plan.validate_f_master_plan
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(stage_f_plan, "validate_f_master_plan", counted)
    specs = request_specs_for_activated_f_plan(
        master, activated, corpus=corpus, run_nonce_key=KEY)

    assert calls == 1
    assert list(specs) == [row["work_id"]
                           for row in template["payload"]["work"]]
    for row in template["payload"]["work"]:
        assert request_spec_hash(specs[row["work_id"]]) == row["request_sha256"]

    tampered = copy.deepcopy(activated)
    tampered["master_plan_sha256"] = "0" * 64
    with pytest.raises(StageFPlanError, match="frozen template"):
        request_specs_for_activated_f_plan(
            master, tampered, corpus=corpus, run_nonce_key=KEY)


@pytest.mark.parametrize("chunk_chars", [2000, 4000, 8000])
def test_f72_expected_chunk_census_is_frozen(planning_inputs, chunk_chars: int) -> None:
    _manifest, corpus, selections = planning_inputs
    one = [{**selections[0], "chunk_chars": chunk_chars}]
    master = build_f_master_plan(
        "d" * 64, one, corpus=corpus, run_nonce_key=KEY)
    assert all(len(envelope["payload"]["work"]) == EXPECTED_F_CHUNKS[chunk_chars]
               for envelope in master["plans"])


def test_acceptance_plan_copies_template_under_distinct_parent_hash(planning_inputs) -> None:
    _manifest, corpus, selections = planning_inputs
    master = build_f_master_plan(
        "e" * 64, selections[:1], corpus=corpus, run_nonce_key=KEY)
    template = master["acceptance_templates"][0]
    plan = build_acceptance_plan(
        master, candidate_id=template["candidate_id"],
        provisional_decision_sha256="f" * 64)
    assert plan["candidates"] == template["payload"]["candidates"]
    assert plan["work"] == template["payload"]["work"]
    assert plan["template_sha256"] == template["template_sha256"]
    assert sha256_json(plan) != template["template_sha256"]

    wrong = copy.deepcopy(master)
    wrong["parent_decision_sha256"] = "1" * 64
    with pytest.raises(StageFPlanError, match="strict validation"):
        build_acceptance_plan(
            wrong, candidate_id=template["candidate_id"],
            provisional_decision_sha256="f" * 64)
