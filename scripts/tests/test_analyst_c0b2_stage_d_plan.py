"""Focused offline tests for the pure C0B-2 Stage-D planner."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import goldset
from scripts.analyst_benchmark.c0b2_public_schema import (
    DPhasePlan,
    sha256_json,
    stage_d_candidate_id,
)
from scripts.analyst_benchmark.c0b2_schema import canonical_json, stable_hash
from scripts.analyst_benchmark.c0b2_stage_d_plan import (
    D1_PANEL,
    D2_CHUNKS,
    StageDPlanError,
    build_d1_plan,
    build_d2_plan,
    build_d3_plan,
    build_d4_plan,
    d1_candidates_from_stage_c_selection,
    derive_d_context_controls,
    load_d50,
    request_spec_for_d_work,
    resolve_d_work,
    validate_d_plan,
    verified_run_nonce_key,
)
from scripts.analyst_benchmark.c0b2_transport import request_spec_hash

KEY = bytes(range(32))


@pytest.fixture(scope="module")
def planning_inputs():
    master = legacy_plan.build_master_manifest()
    payload = legacy_plan.master_manifest_payload(master)
    corpus = load_d50(payload, master_manifest_sha256=master.sha256)
    return master, payload, corpus


def _candidate_rows(phase: str, *, chunk_chars: int = 2000,
                    num_ctx: int = 8192, num_predict: int = 2048,
                    worksheet: str = "v2") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for model, digest, _think in legacy_plan.MODELS:
        factors = {
            "D1": (None, None, None, None),
            "D2": (None, None, None, num_predict),
            "D3": (chunk_chars, 256, None, num_predict),
            "D4": (chunk_chars, 256, num_ctx, num_predict),
        }[phase]
        rows.append({
            "candidate_id": stage_d_candidate_id(model, digest, worksheet),
            "model": model, "model_digest": digest, "worksheet": worksheet,
            "chunk_chars": factors[0], "overlap": factors[1],
            "num_ctx": factors[2], "num_predict": factors[3],
        })
    return rows


def _stage_c_selection() -> dict[str, object]:
    models = []
    survivors = []
    for model, digest, _think in legacy_plan.MODELS:
        models.append({
            "model": model, "model_digest": digest,
            "v1_passed": False, "v2_passed": True,
            "selected_worksheet": "v2", "selection_basis": "only_passer",
            "bootstrap": None,
        })
        survivors.append({
            "model": model, "model_digest": digest, "worksheet": "v2",
            "chunk_chars": 4000, "overlap": 256,
            "num_ctx": 8192, "num_predict": 4096,
        })
    return {
        "version": "stage-c-selection-v1", "stage": "C",
        "plan_sha256": "1" * 64, "aggregate_sha256": "2" * 64,
        "models": models, "survivors": survivors,
    }


def test_load_d50_reads_only_manifest_and_frozen_d50(planning_inputs) -> None:
    master, payload, _corpus = planning_inputs
    opened: list[Path] = []

    def reader(path: Path) -> bytes:
        opened.append(path.resolve())
        return path.read_bytes()

    corpus = load_d50(
        payload, master_manifest_sha256=master.sha256, read_bytes=reader)
    expected_paths = {goldset.MANIFEST.resolve()} | {
        (goldset.GOLD_ROOT / "docs" / f"{doc_id}.txt").resolve()
        for doc_id in master.split.d
    }
    assert set(opened) == expected_paths
    assert len(opened) == 51
    assert corpus.document_order == master.split.d
    assert len(corpus.documents) == 50
    assert [view.chunk_chars for view in corpus.by_id()["bnd_01_s02"].boundary_views] \
        == [2000, 4000, 8000]
    assert not set(master.split.c + master.split.f) & {
        path.stem for path in opened if path != goldset.MANIFEST.resolve()
    }


def test_load_d50_rejects_rehashed_split_and_boundary_tampering(planning_inputs) -> None:
    _master, payload, _corpus = planning_inputs
    reordered = copy.deepcopy(payload)
    reordered["split"]["d"][0], reordered["split"]["d"][1] = (
        reordered["split"]["d"][1], reordered["split"]["d"][0])
    with pytest.raises(StageDPlanError, match="split order"):
        load_d50(reordered, master_manifest_sha256=stable_hash(reordered))

    bad_view = copy.deepcopy(payload)
    row = bad_view["boundary_views"][0]
    row["text"] = "X" + row["text"][1:]
    row["sha256"] = hashlib.sha256(row["text"].encode("ascii")).hexdigest()
    with pytest.raises(StageDPlanError, match="boundary-view derivation"):
        load_d50(bad_view, master_manifest_sha256=stable_hash(bad_view))


def test_load_d50_rejects_unsafe_selected_fixture_path(planning_inputs) -> None:
    _master, payload, _corpus = planning_inputs
    raw = json.loads(goldset.MANIFEST.read_text(encoding="utf-8"))
    selected = next(row for row in raw["documents"] if row["doc_id"] == "pos_pii_007")
    selected["path"] = "../outside.txt"
    encoded = json.dumps(raw, sort_keys=False, separators=(",", ":")).encode()
    changed = copy.deepcopy(payload)
    changed["gold_manifest_sha256"] = hashlib.sha256(encoded).hexdigest()

    def reader(path: Path) -> bytes:
        return encoded if path == goldset.MANIFEST else path.read_bytes()

    with pytest.raises(StageDPlanError, match="unsafe fixture path"):
        load_d50(
            changed, master_manifest_sha256=stable_hash(changed), read_bytes=reader)


def test_nonce_key_manifest_must_rederive_c_plan_byte_for_byte() -> None:
    frozen = legacy_plan.build_c_stage_plan(KEY)
    manifest = {"version": "c0b2-run-nonce-key-v1", "key_hex": KEY.hex()}
    assert verified_run_nonce_key(manifest, frozen) == KEY

    altered = legacy_plan.stage_plan_payload(frozen)
    altered["work"][0]["nonce"] = "FENCE_" + "A" * 32
    with pytest.raises(StageDPlanError, match="does not re-derive"):
        verified_run_nonce_key(manifest, altered)
    with pytest.raises(StageDPlanError, match="malformed"):
        verified_run_nonce_key({**manifest, "key_hex": "AA" * 32}, frozen)


def test_stage_c_selection_translation_is_strict_and_ordered() -> None:
    rows = d1_candidates_from_stage_c_selection(_stage_c_selection())
    assert [row["model"] for row in rows] == [row[0] for row in legacy_plan.MODELS]
    assert all(tuple(row[name] for name in
                     ("chunk_chars", "overlap", "num_ctx", "num_predict"))
               == (None, None, None, None) for row in rows)
    hostile = _stage_c_selection()
    hostile["survivors"] = list(reversed(hostile["survivors"]))
    with pytest.raises(StageDPlanError, match="selection is invalid"):
        d1_candidates_from_stage_c_selection(hostile)


def test_d1_plan_has_exact_model_budget_panel_order_and_count(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    plan = build_d1_plan(
        "1" * 64, _candidate_rows("D1"), corpus=corpus, run_nonce_key=KEY)
    assert len(plan["work"]) == 55
    DPhasePlan.model_validate(plan, strict=True)
    expected = []
    budgets = ((2048, 3072, 4096),
               (1024, 2048, 3072, 4096),
               (1024, 2048, 3072, 4096))
    for candidate, candidate_budgets in zip(plan["candidates"], budgets, strict=True):
        expected.extend(
            (candidate["candidate_id"], budget, doc_id)
            for budget in candidate_budgets for doc_id in D1_PANEL)
    assert [(row["candidate_id"], row["num_predict"], row["doc_id"])
            for row in plan["work"]] == expected
    same_doc = [row for row in plan["work"]
                if row["candidate_id"] == plan["candidates"][0]["candidate_id"]
                and row["doc_id"] == "pos_pii_007"]
    assert len({row["nonce"] for row in same_doc}) == 1
    assert canonical_json(plan) == canonical_json(build_d1_plan(
        "1" * 64, _candidate_rows("D1"), corpus=corpus, run_nonce_key=KEY))


def test_d2_plan_uses_exact_logical_and_derived_identities(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    plan = build_d2_plan(
        "2" * 64, _candidate_rows("D2"), corpus=corpus, run_nonce_key=KEY)
    assert len(plan["work"]) == 216
    boundary_order = [doc_id for doc_id in corpus.document_order
                      if corpus.by_id()[doc_id].stratum == "boundary"]
    first_candidate = plan["candidates"][0]["candidate_id"]
    rows = [row for row in plan["work"] if row["candidate_id"] == first_candidate]
    assert [(row["chunk_chars"], row["doc_id"], row["chunk_index"])
            for row in rows] == [
                (size, doc_id, chunk_index)
                for size in D2_CHUNKS for doc_id in boundary_order
                for chunk_index in (0, 1)
            ]
    first = rows[0]
    doc = corpus.by_id()[first["doc_id"]]
    assert first["document_sha256"] == doc.document_sha256
    assert first["view_id"] == doc.view_for(2000).view_id
    assert rows[0]["nonce"] == rows[1]["nonce"]
    assert rows[0]["nonce"] != rows[24]["nonce"]


def test_d3_d4_counts_controls_and_paired_nonce(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    d3 = build_d3_plan(
        "3" * 64, _candidate_rows("D3"), corpus=corpus, run_nonce_key=KEY)
    d4 = build_d4_plan(
        "4" * 64, _candidate_rows("D4"), corpus=corpus, run_nonce_key=KEY)
    assert len(d3["work"]) == len(d4["work"]) == 243
    assert [row["doc_id"] for row in d3["work"][:81] if row["chunk_index"] == 0] \
        == list(corpus.document_order)

    d3_by_identity = {(row["candidate_id"], row["doc_id"], row["chunk_index"]): row
                      for row in d3["work"]}
    for row in d4["work"]:
        paired = d3_by_identity[(row["candidate_id"], row["doc_id"], row["chunk_index"])]
        assert row["nonce"] == paired["nonce"]
        assert row["request_sha256"] != paired["request_sha256"]

    for plan, purpose, trigger, minimum in (
            (d3, "d3_context_16384", "first_http_terminal_d3", 16384),
            (d4, "d4_context_selected", "first_http_terminal_d4", 8192)):
        controls = derive_d_context_controls(
            plan, corpus=corpus, run_nonce_key=KEY)
        assert [row["candidate_id"] for row in controls] == [
            row["candidate_id"] for row in plan["candidates"]]
        assert all(row["purpose"] == purpose and row["trigger_rule"] == trigger
                   and row["minimum_context_length"] == minimum for row in controls)
        first = plan["work"][0]
        expected_config = {
            "keep_alive": "15m",
            "options": {"min_p": 0.0, "num_ctx": first["num_ctx"],
                        "num_predict": first["num_predict"], "repeat_last_n": 0,
                        "repeat_penalty": 1.0, "seed": 1, "temperature": 0.0,
                        "top_k": 1, "top_p": 1.0},
            "think": "low",
        }
        assert controls[0]["config_sha256"] == sha256_json(expected_config)


def test_resolver_reconstructs_hashes_and_strict_transport_spec(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    plan = build_d3_plan(
        "3" * 64, _candidate_rows("D3", chunk_chars=4000),
        corpus=corpus, run_nonce_key=KEY)
    work = next(row for row in plan["work"] if row["view_id"] is not None)
    resolved = resolve_d_work(
        plan, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert hashlib.sha256(resolved["chunk_text"].encode()).hexdigest() \
        == work["chunk_sha256"]
    assert stable_hash(resolved["payload"]) == work["request_sha256"]
    spec = request_spec_for_d_work(
        plan, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert spec.kind == "chat"
    assert request_spec_hash(spec) == work["request_sha256"]


def test_rederivation_rejects_order_and_identity_tampering(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    plan = build_d2_plan(
        "2" * 64, _candidate_rows("D2"), corpus=corpus, run_nonce_key=KEY)
    reordered = copy.deepcopy(plan)
    reordered["work"][0], reordered["work"][1] = (
        reordered["work"][1], reordered["work"][0])
    with pytest.raises(StageDPlanError, match="re-derivation"):
        validate_d_plan(reordered, corpus=corpus, run_nonce_key=KEY)

    candidates = _candidate_rows("D2")
    candidates.reverse()
    with pytest.raises(StageDPlanError, match="survivor order"):
        build_d2_plan("2" * 64, candidates, corpus=corpus, run_nonce_key=KEY)

    bad = copy.deepcopy(plan)
    bad["work"][0]["document_sha256"] = "f" * 64
    with pytest.raises(StageDPlanError, match="strict schema"):
        validate_d_plan(bad, corpus=corpus, run_nonce_key=KEY)


def test_d4_rejects_reuse_candidates_and_wrong_key_length(planning_inputs) -> None:
    _master, _payload, corpus = planning_inputs
    with pytest.raises(StageDPlanError, match="lower-context"):
        build_d4_plan(
            "4" * 64, _candidate_rows("D4", num_ctx=16384),
            corpus=corpus, run_nonce_key=KEY)
    with pytest.raises(StageDPlanError, match="exactly 32"):
        build_d1_plan(
            "1" * 64, _candidate_rows("D1"),
            corpus=corpus, run_nonce_key=KEY + b"x")
