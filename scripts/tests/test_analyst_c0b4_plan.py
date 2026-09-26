"""Focused offline tests for the frozen C0B-4 public planner."""
from __future__ import annotations

import copy

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b2_transport import request_spec_hash
from scripts.analyst_benchmark.c0b4_answer import PROMPT_DELTA
from scripts.analyst_benchmark import c0b4_plan
from scripts.analyst_benchmark.c0b4_plan import (
    C0B4PlanError, PARENT_BINDING, build_master_plan, candidate_id,
    build_request_resolver, lane_from_master, request_spec_for_work,
    resolve_controls, resolve_work, validate_master_plan,
)

KEY = bytes(range(32))
PROTOCOL_SHA256 = "a" * 64


@pytest.fixture(scope="module")
def planning_inputs():
    manifest = legacy_plan.build_master_manifest()
    corpus = load_public_corpus(
        legacy_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=KEY,
        protocol_sha256=PROTOCOL_SHA256)
    return corpus, master


def test_master_freezes_exact_228_requests_and_parent(planning_inputs) -> None:
    corpus, master = planning_inputs
    assert master["lane_order"] == ["F72_17", "F72_20260804", "C44_1"]
    lanes = [row["payload"] for row in master["lane_plans"]] + [
        master["acceptance_template"]["payload"]]
    assert [len(row["work"]) for row in lanes] == [92, 92, 44]
    assert sum(len(row["work"]) for row in lanes) == 228
    assert master["parent_binding"] == PARENT_BINDING
    assert candidate_id() == \
        "7c6864367346c171a2ad008965e26d536ac4218bce0b26324018828add52e6c9"
    assert all(row["candidate_id"] == candidate_id()
               for lane in lanes for row in lane["work"])
    validate_master_plan(master, corpus=corpus, run_nonce_key=KEY)


def test_prompt_uses_single_c0b4_authority_and_request_hash(planning_inputs) -> None:
    corpus, master = planning_inputs
    work = master["lane_plans"][0]["payload"]["work"][0]
    resolved = resolve_work(
        master, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert f"  7. {PROMPT_DELTA}\n" in resolved["prompt"]
    assert resolved["prompt"].index(PROMPT_DELTA) < \
        resolved["prompt"].index("The schema you must satisfy")
    spec = request_spec_for_work(
        master, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert request_spec_hash(spec) == work["request_sha256"]


def test_lanes_and_controls_have_separate_fresh_nonce_identities(
        planning_inputs) -> None:
    corpus, master = planning_inputs
    lanes = [lane_from_master(
        master, lane_id, corpus=corpus, run_nonce_key=KEY)
             for lane_id in ("F72_17", "F72_20260804", "C44_1")]
    nonce_sets = [{row["nonce"] for row in lane["work"]} for lane in lanes]
    assert not nonce_sets[0] & nonce_sets[1]
    assert not nonce_sets[0] & nonce_sets[2]
    assert not nonce_sets[1] & nonce_sets[2]

    controls = resolve_controls(master, corpus=corpus, run_nonce_key=KEY)
    assert list(controls) == ["context", "cancellation", "health"]
    cancel = controls["cancellation"]
    health = controls["health"]
    scored = next(row for row in lanes[0]["work"]
                  if row["doc_id"] == "pos_pii_013" and row["chunk_index"] == 0)
    assert len({scored["nonce"], cancel.control["nonce"],
                health.control["nonce"]}) == 3
    assert len({scored["request_sha256"], cancel.control["request_sha256"],
                health.control["request_sha256"]}) == 3
    assert cancel.request_spec.cancel_on_first_content is True
    assert health.request_spec.cancel_on_first_content is False
    assert controls["context"].request_spec.kind == "ps"


def test_fresh_run_key_changes_every_scored_and_control_nonce(
        planning_inputs) -> None:
    corpus, first = planning_inputs
    second = build_master_plan(
        corpus=corpus, run_nonce_key=bytes(reversed(range(32))),
        protocol_sha256=PROTOCOL_SHA256)
    first_work = [row for envelope in first["lane_plans"]
                  for row in envelope["payload"]["work"]] + \
                 first["acceptance_template"]["payload"]["work"]
    second_work = [row for envelope in second["lane_plans"]
                   for row in envelope["payload"]["work"]] + \
                  second["acceptance_template"]["payload"]["work"]
    assert all(left["nonce"] != right["nonce"]
               and left["request_sha256"] != right["request_sha256"]
               and left["work_id"] != right["work_id"]
               for left, right in zip(first_work, second_work, strict=True))
    first_controls = first["control_plan"]
    second_controls = second["control_plan"]
    for name in ("cancellation", "health"):
        assert first_controls[name]["nonce"] != second_controls[name]["nonce"]
        assert first_controls[name]["request_sha256"] != \
            second_controls[name]["request_sha256"]


def test_master_and_parent_tampering_fail_closed(planning_inputs) -> None:
    corpus, master = planning_inputs
    changed = copy.deepcopy(master)
    changed["lane_plans"][0]["payload"]["work"][0]["nonce"] = \
        "FENCE_" + "A" * 32
    with pytest.raises(C0B4PlanError, match="differs"):
        validate_master_plan(changed, corpus=corpus, run_nonce_key=KEY)

    changed = copy.deepcopy(master)
    changed["parent_binding"]["run_id"] = "wrong"
    with pytest.raises(C0B4PlanError, match="parent binding"):
        validate_master_plan(changed, corpus=corpus, run_nonce_key=KEY)


def test_invocation_resolver_validates_master_once(
        planning_inputs, monkeypatch) -> None:
    corpus, master = planning_inputs
    original = c0b4_plan.validate_master_plan
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(c0b4_plan, "validate_master_plan", counted)
    resolver = build_request_resolver(
        master, corpus=corpus, run_nonce_key=KEY)
    work = master["lane_plans"][0]["payload"]["work"]
    for row in (work[0], work[1], work[-1]):
        assert request_spec_hash(resolver.request_spec_for_work(
            row["work_id"])) == row["request_sha256"]
    assert set(resolver.resolve_controls()) == {
        "context", "cancellation", "health"}
    assert calls == 1
