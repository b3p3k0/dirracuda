"""Strict separation tests for C0B-3 policy-bound artifact schemas."""
from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark.c0b2_public_schema import (
    public_cell_id,
    public_work_id,
    sha256_json,
    stage_d_candidate_id,
)
from scripts.analyst_benchmark.c0b3_policy import (
    POLICY_ID, POLICY_SHA256, header_identity,
)
from scripts.analyst_benchmark.c0b3_schema import (
    COMPLETION_DECISION_ID,
    DInconclusiveResultV2,
    DPhasePlanV2,
    FSeedCursorTransitionV2,
    InconclusiveCompletionV2,
    StageCInconclusiveResultV2,
    completion_decision_id,
    policy_binding_fields,
    reject_legacy_plan_with_current_validator,
    require_policy_parent,
    validate_current_completion,
    validate_current_result,
    validate_completion_for_header,
    validate_result_for_header,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _binding() -> dict[str, str]:
    return {"policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}


def _stage_c_selection() -> dict[str, object]:
    models = []
    for index in range(3):
        models.append({
            "model": f"model:{index}", "model_digest": _hash(f"model:{index}"),
            "v1_passed": False, "v2_passed": False, "bootstrap": None,
            "selected_worksheet": None, "selection_basis": "no_passer",
        })
    return {
        "version": "stage-c-selection-v1", "stage": "C",
        "plan_sha256": _hash("c-plan"),
        "aggregate_sha256": _hash("c-aggregate"),
        "models": models, "survivors": [],
    }


def _d1_plan(parent: str) -> dict[str, object]:
    model, digest = "model:stable", _hash("model")
    candidate_id = stage_d_candidate_id(model, digest, "v2")
    cell_id = public_cell_id(
        budget_stage="D", candidate_id=candidate_id, chunk_chars=4000,
        num_ctx=8192, num_predict=1024, phase="D1", seed=1)
    chunk_hash, document_hash = _hash("chunk"), _hash("document")
    request_hash, nonce = _hash("request"), "FENCE_" + _hash("nonce")[:32].upper()
    work_id = public_work_id(
        cell_id=cell_id, chunk_index=0, chunk_sha256=chunk_hash,
        doc_id="d1_doc", document_sha256=document_hash, nonce=nonce,
        plan_key="D1_OUTPUT", request_sha256=request_hash, view_id=None)
    return {
        **_binding(), "version": "stage-d-phase-plan-v2", "stage": "D",
        "phase": "D1", "plan_key": "D1_OUTPUT", "budget_stage": "D",
        "parent_decision_sha256": parent,
        "candidates": [{
            "candidate_id": candidate_id, "model": model,
            "model_digest": digest, "worksheet": "v2",
            "chunk_chars": None, "overlap": None, "num_ctx": None,
            "num_predict": None,
        }],
        "work": [{
            "stage": "D", "phase": "D1", "plan_key": "D1_OUTPUT",
            "budget_stage": "D", "activation_group_id": None,
            "candidate_id": candidate_id, "cell_id": cell_id,
            "work_id": work_id, "model": model, "model_digest": digest,
            "worksheet": "v2", "doc_id": "d1_doc", "view_id": None,
            "document_sha256": document_hash, "chunk_chars": 4000,
            "overlap": 256, "num_ctx": 8192, "num_predict": 1024,
            "seed": 1, "chunk_index": 0, "chunk_sha256": chunk_hash,
            "nonce": nonce, "prompt_sha256": _hash("prompt"),
            "request_sha256": request_hash,
        }],
    }


def test_policy_binding_fields_are_fresh_and_exact() -> None:
    first = policy_binding_fields()
    first["policy_id"] = "changed"
    assert policy_binding_fields() == _binding()


@pytest.mark.parametrize("mutation", ("absent", "partial", "wrong", "null", "extra"))
def test_current_result_rejects_nonexact_policy_shape(mutation: str) -> None:
    value: dict[str, object] = {
        **_binding(), "version": "c0b3-result-v1",
        "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": _hash("aggregate"),
        "reason": "no_d3_context_survivor",
    }
    if mutation == "absent":
        value.pop("policy_id")
        value.pop("policy_sha256")
    elif mutation == "partial":
        value.pop("policy_sha256")
    elif mutation == "wrong":
        value["policy_sha256"] = _hash("wrong")
    elif mutation == "null":
        value["policy_id"] = None
    else:
        value["unknown"] = True
    with pytest.raises((ValueError, ValidationError)):
        validate_current_result(value)


def test_result_dispatch_is_closed_and_versioned() -> None:
    d_result = {
        **_binding(), "version": "c0b3-result-v1",
        "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": _hash("aggregate"),
        "reason": "no_d3_context_survivor",
    }
    assert validate_current_result(d_result) == d_result
    assert DInconclusiveResultV2.model_validate(
        d_result, strict=True).version == "c0b3-result-v1"

    stage_c = {
        **_binding(), "version": "c0b3-result-v1",
        "terminal": "INCONCLUSIVE", "stage": "C",
        "aggregate_sha256": _hash("aggregate-c"),
        "reason": "no_stage_c_survivor",
    }
    assert StageCInconclusiveResultV2.model_validate(
        stage_c, strict=True).stage == "C"
    with pytest.raises((ValueError, ValidationError)):
        validate_current_result({**d_result, "version": "c0b2-result-v1"})
    with pytest.raises(ValueError):
        validate_current_result({**d_result, "stage": "D", "terminal": "SELECTED"})


def test_completion_requires_current_decision_id_binding_and_shape() -> None:
    value = {
        **_binding(), "version": "c0b3-completion-v1",
        "outcome": "INCONCLUSIVE",
        "artifact_sha256": _hash("artifact"),
        "facts": {"deterministic_stop": True, "reason": "no survivor"},
    }
    assert validate_current_completion(COMPLETION_DECISION_ID, value) == value
    assert InconclusiveCompletionV2.model_validate(
        value, strict=True).outcome == "INCONCLUSIVE"
    with pytest.raises(ValueError):
        validate_current_completion("c0b2-completion", value)
    with pytest.raises((ValueError, ValidationError)):
        validate_current_completion(
            COMPLETION_DECISION_ID, {**value, "policy_sha256": _hash("wrong")})
    missing_version = {key: item for key, item in value.items() if key != "version"}
    with pytest.raises((ValueError, ValidationError)):
        validate_current_completion(COMPLETION_DECISION_ID, missing_version)
    with pytest.raises((ValueError, ValidationError)):
        validate_current_completion(
            COMPLETION_DECISION_ID, {**value, "version": "c0b2-completion-v1"})


def test_header_dispatch_keeps_legacy_and_current_results_separate() -> None:
    legacy = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "D",
        "aggregate_sha256": _hash("legacy-aggregate"),
        "reason": "no_d3_context_survivor",
    }
    current = {**legacy, **_binding(), "version": "c0b3-result-v1"}
    assert validate_result_for_header({}, legacy) == legacy
    assert validate_result_for_header(header_identity(), current) == current
    with pytest.raises((ValueError, ValidationError)):
        validate_result_for_header({}, current)
    with pytest.raises((ValueError, ValidationError)):
        validate_result_for_header(header_identity(), legacy)


def test_header_dispatch_selects_exact_completion_identity() -> None:
    legacy = {
        "outcome": "INCONCLUSIVE", "artifact_sha256": _hash("legacy-artifact"),
        "facts": {"deterministic_stop": True, "reason": "no survivor"},
    }
    current = {
        **legacy, **_binding(), "version": "c0b3-completion-v1",
        "artifact_sha256": _hash("current-artifact"),
    }
    assert completion_decision_id({}) == "c0b2-completion"
    assert completion_decision_id(header_identity()) == COMPLETION_DECISION_ID
    assert validate_completion_for_header({}, "c0b2-completion", legacy) == legacy
    assert validate_completion_for_header(
        header_identity(), COMPLETION_DECISION_ID, current) == current
    with pytest.raises(ValueError):
        validate_completion_for_header({}, COMPLETION_DECISION_ID, legacy)
    with pytest.raises(ValueError):
        validate_completion_for_header(
            header_identity(), "c0b2-completion", current)


def test_d1_is_the_only_unbound_stage_c_parent_exception() -> None:
    parent = _stage_c_selection()
    value_hash = sha256_json(parent)
    record_digest = _hash("stage-c-selection-decision-record")
    assert record_digest != value_hash
    child = _d1_plan(record_digest)
    require_policy_parent(
        child, parent, record_digest, record_digest, allow_stage_c_root=True)

    with pytest.raises(ValueError):
        require_policy_parent(
            {**child, "phase": "D2", "plan_key": "D2_CHUNK"},
            parent, record_digest, record_digest, allow_stage_c_root=True)
    with pytest.raises(ValueError):
        require_policy_parent(
            child, parent, _hash("wrong"), record_digest,
            allow_stage_c_root=True)
    with pytest.raises(ValueError):
        require_policy_parent(child, parent, record_digest, record_digest)
    with pytest.raises(ValueError):
        require_policy_parent(
            child, parent, record_digest, value_hash,
            allow_stage_c_root=True)


def test_v2_plan_cannot_normalize_legacy_or_missing_policy() -> None:
    parent = _stage_c_selection()
    plan = _d1_plan(sha256_json(parent))
    assert DPhasePlanV2.model_validate(plan, strict=True).phase == "D1"
    for changed in (
        {**plan, "version": "stage-d-phase-plan-v1"},
        {key: value for key, value in plan.items() if key != "policy_id"},
    ):
        with pytest.raises((ValueError, ValidationError)):
            DPhasePlanV2.model_validate(changed, strict=True)
    with pytest.raises(ValueError):
        reject_legacy_plan_with_current_validator({
            **_binding(), "version": "stage-d-phase-plan-v1"})


def test_cursor_transition_hash_includes_current_policy_binding() -> None:
    body: dict[str, object] = {
        **_binding(), "version": "c0b3-f-seed-cursor-transition-v1",
        "run_id": "c0b3-run", "from_plan_key": "F_SEED_17",
        "to_plan_key": "F_SEED_20260804",
        "f_master_plan_sha256": _hash("master"),
        "seed_activation_decision_sha256": _hash("seed-activation"),
        "from_plan_sha256": _hash("from-plan"),
        "from_activation_sha256": _hash("from-activation"),
        "to_plan_sha256": _hash("to-plan"),
        "to_activation_sha256": _hash("to-activation"),
        "activated_from_group_ids": [_hash("from-group")],
        "activated_to_group_ids": [_hash("to-group")],
        "completed_from_work_ids": [_hash("work")],
        "completed_from_work_census_sha256": _hash("census"),
        "transitioned_at_utc": "2026-08-09T12:00:00Z",
    }
    value = {**body, "transition_sha256": sha256_json(body)}
    assert FSeedCursorTransitionV2.model_validate(
        value, strict=True).transition_sha256 == value["transition_sha256"]
    changed = deepcopy(value)
    changed["policy_id"] = "c0b2-strict-zero-intermediate-v1"
    with pytest.raises(ValidationError):
        FSeedCursorTransitionV2.model_validate(changed, strict=True)
