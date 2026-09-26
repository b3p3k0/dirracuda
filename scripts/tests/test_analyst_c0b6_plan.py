"""Focused offline tests for the frozen C0B-6 public planner."""
from __future__ import annotations

import copy

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b2_transport import request_spec_hash
from scripts.analyst_benchmark.c0b6_lineage import FROZEN_PARENT_BINDING
from scripts.analyst_benchmark.c0b6_plan import (
    C0B6PlanError, LANE_ORDER, build_master_plan, build_request_resolver,
    candidate_id, lane_from_master, request_spec_for_work, resolve_controls,
    validate_master_plan,
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
        corpus=corpus, run_nonce_key=KEY, protocol_sha256=PROTOCOL_SHA256)
    return corpus, master


def _lanes(master):
    return [row["payload"] for row in master["lane_plans"]] + [
        master["acceptance_template"]["payload"]]


def test_master_freezes_exact_new_lanes_and_228_requests(planning_inputs) -> None:
    corpus, master = planning_inputs
    lanes = _lanes(master)
    assert master["lane_order"] == list(LANE_ORDER)
    assert [(row["lane_id"], row["seed"], len(row["work"])) for row in lanes] == [
        ("F72_20260811", 20260811, 92),
        ("F72_20260818", 20260818, 92),
        ("C44_1", 1, 44),
    ]
    assert sum(len(row["work"]) for row in lanes) == 228
    assert master["parent_binding"] == FROZEN_PARENT_BINDING
    assert candidate_id() == \
        "7c6864367346c171a2ad008965e26d536ac4218bce0b26324018828add52e6c9"
    assert validate_master_plan(
        master, corpus=corpus, run_nonce_key=KEY) == master


def test_seed20260818_and_c0b6_domains_are_fresh(planning_inputs) -> None:
    _corpus, master = planning_inputs
    lanes = _lanes(master)
    assert {row["phase"] for row in lanes[1]["work"]} == {"F_SEED_20260818"}
    assert {row["plan_key"] for row in lanes[1]["work"]} == {"F_SEED_20260818"}
    nonce_sets = [{row["nonce"] for row in lane["work"]} for lane in lanes]
    work_sets = [{row["work_id"] for row in lane["work"]} for lane in lanes]
    for left in range(3):
        for right in range(left + 1, 3):
            assert not nonce_sets[left] & nonce_sets[right]
            assert not work_sets[left] & work_sets[right]


def test_controls_are_owned_only_by_seed20260811(planning_inputs) -> None:
    corpus, master = planning_inputs
    controls = master["control_plan"]
    assert controls["context"] | {} == controls["context"]
    assert controls["context"]["lane_id"] == "F72_20260811"
    assert controls["context"]["purpose"] == "c0b6_stage_f_candidate_context"
    assert controls["context"]["trigger_rule"] == \
        "first_bounded_http_terminal_seed20260811"
    assert controls["cancellation"]["seed"] == 20260811
    assert controls["health"]["seed"] == 20260811
    assert all(row["lane_id"] == "F72_20260811" for row in controls.values())
    resolved = resolve_controls(master, corpus=corpus, run_nonce_key=KEY)
    assert list(resolved) == ["context", "cancellation", "health"]
    assert resolved["context"].request_spec.kind == "ps"
    assert resolved["cancellation"].request_spec.cancel_on_first_content is True
    assert resolved["health"].request_spec.cancel_on_first_content is False


def test_request_resolver_rederives_seed20260818_payload(planning_inputs) -> None:
    corpus, master = planning_inputs
    lane = lane_from_master(
        master, "F72_20260818", corpus=corpus, run_nonce_key=KEY)
    work = lane["work"][0]
    spec = request_spec_for_work(
        master, work["work_id"], corpus=corpus, run_nonce_key=KEY)
    assert request_spec_hash(spec) == work["request_sha256"]
    assert spec.payload["options"]["seed"] == 20260818
    resolver = build_request_resolver(
        master, corpus=corpus, run_nonce_key=KEY)
    assert request_spec_hash(resolver.request_spec_for_work(work["work_id"])) == \
        work["request_sha256"]


def test_fresh_run_key_changes_all_request_identities(planning_inputs) -> None:
    corpus, first = planning_inputs
    second = build_master_plan(
        corpus=corpus, run_nonce_key=bytes(reversed(range(32))),
        protocol_sha256=PROTOCOL_SHA256)
    first_work = [row for lane in _lanes(first) for row in lane["work"]]
    second_work = [row for lane in _lanes(second) for row in lane["work"]]
    assert all(left["nonce"] != right["nonce"]
               and left["request_sha256"] != right["request_sha256"]
               and left["work_id"] != right["work_id"]
               for left, right in zip(first_work, second_work, strict=True))
    for name in ("cancellation", "health"):
        assert first["control_plan"][name]["nonce"] != \
            second["control_plan"][name]["nonce"]


@pytest.mark.parametrize("mutation", ("seed", "parent", "work"))
def test_master_tampering_fails_closed(planning_inputs, mutation: str) -> None:
    corpus, master = planning_inputs
    changed = copy.deepcopy(master)
    if mutation == "seed":
        changed["lane_plans"][1]["payload"]["seed"] = 17
    elif mutation == "parent":
        changed["parent_binding"]["observed_c0b4"]["run_id"] = "wrong"
    else:
        changed["lane_plans"][0]["payload"]["work"][0]["nonce"] = \
            "FENCE_" + "A" * 32
    with pytest.raises((C0B6PlanError, RuntimeError), match="parent|differs|identity"):
        validate_master_plan(changed, corpus=corpus, run_nonce_key=KEY)
