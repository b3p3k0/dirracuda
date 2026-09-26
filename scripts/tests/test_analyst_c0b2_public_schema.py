"""Focused strict-schema tests for the public C0B-2 D/F artifact catalog."""
from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark.c0b2_public_schema import (
    AcceptancePlan,
    AcceptanceTemplateEnvelope,
    BackupAnchor,
    BackupReceipt,
    BackupStatus,
    CancellationHealthEvidence,
    DInconclusiveResult,
    DPhasePlan,
    ExactFraction,
    FInconclusiveResult,
    FMasterPlan,
    FSeedPlan,
    FSeedPlanEnvelope,
    FSelectedResult,
    FailureArtifact,
    FailureEvidence,
    PlanActivation,
    PublicWork,
    SelectedCompletion,
    activation_group_id,
    cancellation_control_id,
    context_control_id,
    health_control_id,
    health_work_id,
    public_cell_id,
    public_work_id,
    sha256_json,
    stage_d_candidate_id,
    stage_f_candidate_id,
    validate_artifact,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _nonce(label: str) -> str:
    return "FENCE_" + _hash(label)[:32].upper()


def _selection() -> dict[str, object]:
    return {
        "model": "model:stable",
        "model_digest": _hash("model"),
        "worksheet": "v2",
        "chunk_chars": 4000,
        "overlap": 256,
        "num_ctx": 8192,
        "num_predict": 2048,
    }


def _f_candidate(parent: str) -> dict[str, object]:
    selection = _selection()
    return {
        "candidate_id": stage_f_candidate_id(selection, parent),
        **selection,
    }


def _work(
    candidate: dict[str, object],
    *,
    stage: str,
    phase: str,
    plan_key: str,
    seed: int,
    doc_id: str,
    group_id: str | None,
) -> dict[str, object]:
    suffix = f"{plan_key}:{candidate['candidate_id']}:{doc_id}:{seed}"
    cell_id = public_cell_id(
        budget_stage=stage,
        candidate_id=str(candidate["candidate_id"]),
        chunk_chars=int(candidate["chunk_chars"]),
        num_ctx=int(candidate["num_ctx"]),
        num_predict=int(candidate["num_predict"]),
        phase=phase,
        seed=seed,
    )
    request_sha256 = _hash("request:" + suffix)
    chunk_sha256 = _hash("chunk:" + suffix)
    document_sha256 = _hash("document:" + suffix)
    nonce = _nonce(suffix)
    work_id = public_work_id(
        cell_id=cell_id,
        chunk_index=0,
        chunk_sha256=chunk_sha256,
        doc_id=doc_id,
        document_sha256=document_sha256,
        nonce=nonce,
        plan_key=plan_key,
        request_sha256=request_sha256,
        view_id=None,
    )
    return {
        "stage": stage,
        "phase": phase,
        "plan_key": plan_key,
        "budget_stage": stage,
        "activation_group_id": group_id,
        "candidate_id": candidate["candidate_id"],
        "cell_id": cell_id,
        "work_id": work_id,
        "model": candidate["model"],
        "model_digest": candidate["model_digest"],
        "worksheet": candidate["worksheet"],
        "doc_id": doc_id,
        "view_id": None,
        "document_sha256": document_sha256,
        "chunk_chars": candidate["chunk_chars"],
        "overlap": candidate["overlap"],
        "num_ctx": candidate["num_ctx"],
        "num_predict": candidate["num_predict"],
        "seed": seed,
        "chunk_index": 0,
        "chunk_sha256": chunk_sha256,
        "nonce": nonce,
        "prompt_sha256": _hash("prompt:" + suffix),
        "request_sha256": request_sha256,
    }


def _controls(candidate: dict[str, object]) -> tuple[dict[str, object], ...]:
    candidate_id = str(candidate["candidate_id"])
    config_sha256 = _hash("config")
    payload_sha256 = _hash("context-payload")
    context = {
        "control_id": context_control_id(
            candidate_id=candidate_id,
            config_sha256=config_sha256,
            model=str(candidate["model"]),
            model_digest=str(candidate["model_digest"]),
            payload_sha256=payload_sha256,
            purpose="stage_f_candidate_context",
        ),
        "kind": "context_probe",
        "purpose": "stage_f_candidate_context",
        "candidate_id": candidate_id,
        "model": candidate["model"],
        "model_digest": candidate["model_digest"],
        "config_sha256": config_sha256,
        "minimum_context_length": candidate["num_ctx"],
        "trigger_rule": "first_http_terminal_seed1",
        "payload_sha256": payload_sha256,
    }
    cancel_request = _hash("cancel-request")
    cancellation = {
        "control_id": cancellation_control_id(
            candidate_id=candidate_id, request_sha256=cancel_request),
        "kind": "cancellation_probe",
        "candidate_id": candidate_id,
        "source_doc_id": "pos_pii_013",
        "chunk_index": 0,
        "request_sha256": cancel_request,
        "max_close_after_first_byte_ms": 5000,
        "health_not_before_ms": 2000,
    }
    health_request = _hash("health-request")
    nonce = _nonce("health:" + candidate_id)
    health = {
        "control_id": health_control_id(
            candidate_id=candidate_id,
            nonce=nonce,
            request_sha256=health_request,
        ),
        "kind": "cancellation_health",
        "candidate_id": candidate_id,
        "source_doc_id": "pos_pii_013",
        "chunk_index": 0,
        "nonce": nonce,
        "health_work_id": health_work_id(
            candidate_id=candidate_id, request_sha256=health_request),
        "request_sha256": health_request,
    }
    return context, cancellation, health


def _seed_plan(
    parent: str, seed: int,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    candidates = candidates or [_f_candidate(parent)]
    plan_key = f"F_SEED_{seed}"
    seed_one = seed == 1
    work: list[dict[str, object]] = []
    groups: list[dict[str, object]] = []
    for candidate in candidates:
        group_id = activation_group_id(str(candidate["candidate_id"]), plan_key)
        group_work = [
            _work(
                candidate,
                stage="F",
                phase=plan_key,
                plan_key=plan_key,
                seed=seed,
                doc_id=f"f_doc_{index:03d}",
                group_id=group_id,
            )
            for index in range(72)
        ]
        context, cancellation, health = _controls(candidate)
        work.extend(group_work)
        groups.append({
            "group_id": group_id,
            "candidate_id": candidate["candidate_id"],
            "activation_predicate": (
                "unconditional_stage_d_finalist" if seed_one else "seed1_qualifier"),
            "first_work_id": group_work[0]["work_id"],
            "last_work_id": group_work[-1]["work_id"],
            "planned_work_count": len(group_work),
            "context_control": context if seed_one else None,
            "cancellation_control": cancellation if seed_one else None,
            "health_control": health if seed_one else None,
        })
    return {
        "version": "stage-f-seed-plan-v1",
        "stage": "F",
        "phase": plan_key,
        "plan_key": plan_key,
        "budget_stage": "F",
        "parent_decision_sha256": parent,
        "candidates": candidates,
        "work": work,
        "groups": groups,
    }


def _acceptance_template(
    parent: str, candidate: dict[str, object] | None = None,
) -> dict[str, object]:
    candidate = candidate or _f_candidate(parent)
    work = [
        _work(
            candidate,
            stage="F",
            phase="F_ACCEPTANCE",
            plan_key="F_ACCEPTANCE",
            seed=1,
            doc_id=f"c_doc_{index:03d}",
            group_id=None,
        )
        for index in range(44)
    ]
    return {
        "version": "stage-f-acceptance-plan-v1",
        "stage": "F",
        "phase": "F_ACCEPTANCE",
        "plan_key": "F_ACCEPTANCE",
        "budget_stage": "F",
        "parent_decision_sha256": None,
        "candidates": [candidate],
        "work": work,
    }


def test_exact_fraction_and_candidate_types_are_strict() -> None:
    assert ExactFraction.model_validate(
        {"numerator": 0, "denominator": 1}, strict=True).numerator == 0
    assert ExactFraction.model_validate(
        {"numerator": -3, "denominator": 5}, strict=True).model_dump() == {
            "numerator": -3, "denominator": 5}
    for invalid in (
        {"numerator": 1, "denominator": 2, "extra": 0},
        {"numerator": "1", "denominator": 2},
        {"numerator": 2, "denominator": 4},
        {"numerator": 0, "denominator": 2},
    ):
        with pytest.raises(ValidationError):
            ExactFraction.model_validate(invalid, strict=True)


@pytest.mark.parametrize("seed", (True, 1.0, "1", None))
def test_public_cell_identity_rejects_coerced_seeds(seed: object) -> None:
    with pytest.raises(ValueError):
        public_cell_id(
            budget_stage="D",
            candidate_id=_hash("candidate"),
            chunk_chars=4000,
            num_ctx=8192,
            num_predict=2048,
            phase="D1",
            seed=seed,
        )


def test_public_identity_helpers_reject_coerced_scalars() -> None:
    with pytest.raises(ValueError):
        stage_d_candidate_id(True, _hash("model"), "v2")
    with pytest.raises(ValidationError):
        stage_f_candidate_id(
            {**_selection(), "num_ctx": True}, _hash("stage-d-decision"))
    with pytest.raises(ValueError):
        activation_group_id(_hash("candidate"), True)
    with pytest.raises(ValueError):
        public_work_id(
            cell_id=_hash("cell"),
            chunk_index=True,
            chunk_sha256=_hash("chunk"),
            doc_id="doc-1",
            document_sha256=_hash("document"),
            nonce=_nonce("work"),
            plan_key="D1_OUTPUT",
            request_sha256=_hash("request"),
            view_id=None,
        )


def test_public_work_recomputes_identity_and_enforces_d_seed() -> None:
    candidate = {
        "candidate_id": stage_d_candidate_id("model:stable", _hash("model"), "v2"),
        **_selection(),
    }
    valid = _work(
        candidate,
        stage="D",
        phase="D1",
        plan_key="D1_OUTPUT",
        seed=1,
        doc_id="d_doc_001",
        group_id=None,
    )
    assert PublicWork.model_validate(valid, strict=True).work_id == valid["work_id"]
    for field, replacement in (("cell_id", _hash("wrong")), ("seed", 17)):
        changed = {**valid, field: replacement}
        if field == "seed":
            changed["cell_id"] = public_cell_id(
                budget_stage="D",
                candidate_id=str(candidate["candidate_id"]),
                chunk_chars=4000,
                num_ctx=8192,
                num_predict=2048,
                phase="D1",
                seed=17,
            )
        with pytest.raises(ValidationError):
            PublicWork.model_validate(changed, strict=True)


def test_d_phase_plan_rejects_unselected_factor_and_foreign_work() -> None:
    model = "model:stable"
    digest = _hash("model")
    candidate_id = stage_d_candidate_id(model, digest, "v2")
    work_candidate = {"candidate_id": candidate_id, **_selection()}
    work = _work(
        work_candidate,
        stage="D",
        phase="D1",
        plan_key="D1_OUTPUT",
        seed=1,
        doc_id="d_doc_001",
        group_id=None,
    )
    plan = {
        "version": "stage-d-phase-plan-v1",
        "stage": "D",
        "phase": "D1",
        "plan_key": "D1_OUTPUT",
        "budget_stage": "D",
        "parent_decision_sha256": _hash("parent"),
        "candidates": [{
            "candidate_id": candidate_id,
            "model": model,
            "model_digest": digest,
            "worksheet": "v2",
            "chunk_chars": None,
            "overlap": None,
            "num_ctx": None,
            "num_predict": None,
        }],
        "work": [work],
    }
    assert validate_artifact(DPhasePlan, plan)["phase"] == "D1"
    plan["candidates"][0]["chunk_chars"] = 4000
    with pytest.raises(ValidationError):
        DPhasePlan.model_validate(plan, strict=True)


def test_seed_plans_bind_groups_candidates_work_and_controls() -> None:
    parent = _hash("stage-d-decision")
    seed_one = _seed_plan(parent, 1)
    parsed = FSeedPlan.model_validate(seed_one, strict=True)
    assert parsed.groups[0].context_control is not None

    later = _seed_plan(parent, 17)
    assert FSeedPlan.model_validate(later, strict=True).groups[0].context_control is None

    invalid = deepcopy(seed_one)
    invalid["groups"][0]["planned_work_count"] = 73
    with pytest.raises(ValidationError):
        FSeedPlan.model_validate(invalid, strict=True)

    invalid = deepcopy(seed_one)
    invalid["groups"][0]["context_control"]["minimum_context_length"] = 4096
    with pytest.raises(ValidationError):
        FSeedPlan.model_validate(invalid, strict=True)


def test_master_plan_pins_seed_order_hashes_and_acceptance_candidate() -> None:
    parent = _hash("stage-d-decision")
    plans = []
    for seed in (1, 17, 20260804):
        payload = _seed_plan(parent, seed)
        plans.append({"plan_sha256": sha256_json(payload), "payload": payload})
    template = _acceptance_template(parent)
    candidate_id = str(template["candidates"][0]["candidate_id"])
    envelope = {
        "template_sha256": sha256_json(template),
        "candidate_id": candidate_id,
        "payload": template,
    }
    master = {
        "version": "stage-f-master-plan-v1",
        "stage": "F",
        "budget_stage": "F",
        "parent_decision_sha256": parent,
        "master_manifest_sha256": _hash("manifest"),
        "base_candidate_order": [candidate_id],
        "seed_order": [1, 17, 20260804],
        "plans": plans,
        "acceptance_templates": [envelope],
    }
    assert FMasterPlan.model_validate(master, strict=True).seed_order[-1] == 20260804
    assert FSeedPlanEnvelope.model_validate(plans[0], strict=True).payload.phase == "F_SEED_1"
    assert AcceptanceTemplateEnvelope.model_validate(
        envelope, strict=True).candidate_id == candidate_id

    invalid = deepcopy(master)
    invalid["seed_order"] = [17, 1, 20260804]
    with pytest.raises(ValidationError):
        FMasterPlan.model_validate(invalid, strict=True)

    invalid = deepcopy(master)
    invalid["acceptance_templates"][0]["payload"]["candidates"][0]["num_ctx"] = 4096
    with pytest.raises(ValidationError):
        FMasterPlan.model_validate(invalid, strict=True)


def test_master_plan_rejects_cross_candidate_health_nonce_in_later_seed_work() -> None:
    parent = _hash("stage-d-decision")
    first = _f_candidate(parent)
    second_selection = {
        **_selection(),
        "model": "model:second",
        "model_digest": _hash("model-second"),
    }
    second = {
        "candidate_id": stage_f_candidate_id(second_selection, parent),
        **second_selection,
    }
    candidates = [first, second]
    plans = []
    for seed in (1, 17, 20260804):
        payload = _seed_plan(parent, seed, candidates)
        plans.append({"plan_sha256": sha256_json(payload), "payload": payload})
    templates = []
    for candidate in candidates:
        payload = _acceptance_template(parent, candidate)
        templates.append({
            "template_sha256": sha256_json(payload),
            "candidate_id": candidate["candidate_id"],
            "payload": payload,
        })
    master = {
        "version": "stage-f-master-plan-v1",
        "stage": "F",
        "budget_stage": "F",
        "parent_decision_sha256": parent,
        "master_manifest_sha256": _hash("manifest"),
        "base_candidate_order": [row["candidate_id"] for row in candidates],
        "seed_order": [1, 17, 20260804],
        "plans": plans,
        "acceptance_templates": templates,
    }
    assert len(FMasterPlan.model_validate(master, strict=True).base_candidate_order) == 2

    invalid = deepcopy(master)
    health_nonce = invalid["plans"][0]["payload"]["groups"][0][
        "health_control"]["nonce"]
    row = invalid["plans"][2]["payload"]["work"][73]
    row["nonce"] = health_nonce
    row["work_id"] = public_work_id(
        cell_id=row["cell_id"],
        chunk_index=row["chunk_index"],
        chunk_sha256=row["chunk_sha256"],
        doc_id=row["doc_id"],
        document_sha256=row["document_sha256"],
        nonce=row["nonce"],
        plan_key=row["plan_key"],
        request_sha256=row["request_sha256"],
        view_id=row["view_id"],
    )
    invalid["plans"][2]["plan_sha256"] = sha256_json(
        invalid["plans"][2]["payload"])
    with pytest.raises(ValidationError, match="health nonce collides"):
        FMasterPlan.model_validate(invalid, strict=True)


def test_acceptance_plan_requires_exact_c44_candidate_configuration() -> None:
    parent = _hash("stage-d-decision")
    template = _acceptance_template(parent)
    activated = {
        **template,
        "parent_decision_sha256": _hash("provisional"),
        "master_plan_sha256": _hash("master"),
        "template_sha256": sha256_json(template),
    }
    assert AcceptancePlan.model_validate(
        activated, strict=True).work[-1].doc_id == "c_doc_043"
    activated["work"][0]["num_ctx"] = 4096
    with pytest.raises(ValidationError):
        AcceptancePlan.model_validate(activated, strict=True)


def _cancellation_health() -> dict[str, object]:
    return {
        "candidate_id": _hash("candidate"),
        "cancel_control_id": _hash("cancel-control"),
        "cancel_attempt_id": _hash("cancel-attempt"),
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": True,
        "cancel_elapsed_ms": 4999,
        "health_control_id": _hash("health-control"),
        "health_work_id": _hash("health-work"),
        "health_attempt_ids": [_hash("health-attempt")],
        "not_before_utc": "2026-08-05T10:00:00.9Z",
        "started_at_utc": "2026-08-05T10:00:01Z",
        "eventual_valid": True,
        "retained_grounded_pii": True,
        "authoritative_done_reason": "stop",
        "max_answered_prompt_eval_count": 4000,
        "length_outcomes": 0,
        "headroom_passed": True,
        "tools_empty": True,
        "images_empty": True,
        "unknown_message_fields_empty": True,
        "schema_escape_empty": True,
        "passed": True,
        "failure_reasons": [],
    }


def test_cancellation_health_enforces_temporal_and_gate_consistency() -> None:
    evidence = _cancellation_health()
    assert CancellationHealthEvidence.model_validate(evidence, strict=True).passed

    invalid = {**evidence, "started_at_utc": "2026-08-05T10:00:00.10Z"}
    with pytest.raises(ValidationError):
        CancellationHealthEvidence.model_validate(invalid, strict=True)

    invalid = {**evidence, "passed": False, "failure_reasons": [
        "health_missing", "cancel_after_5_seconds"]}
    with pytest.raises(ValidationError):
        CancellationHealthEvidence.model_validate(invalid, strict=True)

    invalid = {**evidence, "authoritative_done_reason": None}
    with pytest.raises(ValidationError):
        CancellationHealthEvidence.model_validate(invalid, strict=True)


@pytest.mark.parametrize(
    ("overrides", "reasons"),
    (
        ({"cancel_first_byte_seen": False}, ["cancel_not_observed"]),
        ({"cancel_elapsed_ms": 5001}, ["cancel_after_5_seconds"]),
        ({
            "eventual_valid": False,
            "retained_grounded_pii": False,
            "authoritative_done_reason": None,
            "max_answered_prompt_eval_count": None,
            "headroom_passed": False,
        }, ["health_missing", "health_context_headroom_failure"]),
        ({
            "eventual_valid": False,
            "retained_grounded_pii": False,
            "authoritative_done_reason": None,
        }, ["health_eventual_invalid"]),
        ({"retained_grounded_pii": False}, ["health_pii_missing"]),
        ({"retained_grounded_pii": False}, ["health_grounding_failure"]),
        ({
            "authoritative_done_reason": "length",
            "length_outcomes": 1,
        }, ["health_length_outcome"]),
        ({"tools_empty": False}, ["health_channel_violation"]),
        ({"headroom_passed": False}, ["health_context_headroom_failure"]),
    ),
)
def test_cancellation_health_reasons_are_exactly_derived(
    overrides: dict[str, object], reasons: list[str],
) -> None:
    value = {**_cancellation_health(), **overrides,
             "passed": False, "failure_reasons": reasons}
    assert CancellationHealthEvidence.model_validate(
        value, strict=True).failure_reasons == reasons


@pytest.mark.parametrize(
    "overrides",
    (
        {"eventual_valid": True, "max_answered_prompt_eval_count": None},
        {
            "eventual_valid": False,
            "retained_grounded_pii": False,
            "authoritative_done_reason": None,
            "max_answered_prompt_eval_count": None,
            "headroom_passed": True,
            "failure_reasons": ["health_missing"],
        },
        {"authoritative_done_reason": "length", "length_outcomes": 0},
        {"cancel_first_byte_seen": False, "failure_reasons": []},
        {"failure_reasons": ["cancel_not_observed"]},
        {"retained_grounded_pii": False, "failure_reasons": []},
        {"retained_grounded_pii": False, "failure_reasons": [
            "health_pii_missing", "health_grounding_failure"]},
        {
            "eventual_valid": False,
            "retained_grounded_pii": False,
            "authoritative_done_reason": None,
            "failure_reasons": ["health_pii_missing"],
        },
    ),
)
def test_cancellation_health_rejects_fact_reason_mismatches(
    overrides: dict[str, object],
) -> None:
    value = {**_cancellation_health(), **overrides, "passed": False}
    with pytest.raises(ValidationError):
        CancellationHealthEvidence.model_validate(value, strict=True)


@pytest.mark.parametrize(
    ("plan_key", "stage", "groups", "evidence"),
    (
        ("D1_OUTPUT", "D", [], None),
        ("F_SEED_1", "F", [_hash("group")], None),
        ("F_SEED_17", "F", [_hash("group")], _hash("seed1")),
        ("F_ACCEPTANCE", "F", [], _hash("aggregate")),
    ),
)
def test_plan_activation_accepts_only_legal_matrix(
    plan_key: str, stage: str, groups: list[str], evidence: str | None,
) -> None:
    value = {
        "version": "c0b2-plan-activation-v1",
        "run_id": "public-run",
        "budget_stage": stage,
        "plan_key": plan_key,
        "plan_sha256": _hash("plan"),
        "parent_decision_sha256": _hash("parent"),
        "state": "ACTIVATED",
        "activated_group_ids": groups,
        "evidence_sha256": evidence,
    }
    assert PlanActivation.model_validate(value, strict=True).plan_key == plan_key
    with pytest.raises(ValidationError):
        PlanActivation.model_validate({**value, "budget_stage": "D" if stage == "F" else "F"}, strict=True)


def test_failure_result_completion_and_backup_contracts() -> None:
    evidence = {
        "version": "c0b2-failure-evidence-v1",
        "terminal": "BLOCKED_PROVENANCE",
        "stage": "D",
        "reason_code": "provenance_identity_failure",
        "attempt_id": None,
        "control_id": _hash("control"),
        "plan_key": "D2_CHUNK",
    }
    parsed_evidence = FailureEvidence.model_validate(evidence, strict=True)
    failure = {
        "version": "c0b2-failure-v1",
        "terminal": "BLOCKED_PROVENANCE",
        "stage": "D",
        "reason": "provenance_identity_failure",
        "evidence_sha256": sha256_json(parsed_evidence),
        "charged_call_total": 8,
    }
    assert FailureArtifact.model_validate(failure, strict=True).charged_call_total == 8
    with pytest.raises(ValidationError):
        FailureEvidence.model_validate({**evidence, "stage": "F"}, strict=True)
    with pytest.raises(ValidationError):
        FailureArtifact.model_validate({**failure, "reason": "operator_abandoned"}, strict=True)

    selected = {
        "version": "c0b2-result-v1",
        "terminal": "SELECTED",
        "stage": "F",
        **{name: _hash(name) for name in (
            "master_manifest_sha256", "stage_c_selection_sha256",
            "stage_d_decision_sha256", "stage_f_aggregate_sha256",
            "provisional_decision_sha256", "acceptance_plan_sha256",
            "acceptance_aggregate_sha256",
        )},
        "selection": _selection(),
    }
    assert FSelectedResult.model_validate(selected, strict=True).selection.overlap == 256
    assert FInconclusiveResult.model_validate({
        "version": "c0b2-result-v1",
        "terminal": "INCONCLUSIVE",
        "stage": "F",
        "aggregate_sha256": _hash("aggregate"),
        "reason": "ranking_not_decisive",
    }, strict=True).reason == "ranking_not_decisive"
    assert DInconclusiveResult.model_validate({
        "version": "c0b2-result-v1",
        "terminal": "INCONCLUSIVE",
        "stage": "D",
        "aggregate_sha256": _hash("d-aggregate"),
        "reason": "no_d2_chunk_survivor",
    }, strict=True).reason == "no_d2_chunk_survivor"

    gates = {name: True for name in (
        "strict_validity", "first_pass_invalid_bound", "raw_grounding",
        "retained_grounding", "category_recall", "false_positive_bound",
        "injection_robustness", "boundary_identifiers", "truncation_complete",
        "context_channel_cancellation_provenance_safety",
    )}
    assert SelectedCompletion.model_validate({
        "outcome": "SELECTED",
        "artifact_sha256": _hash("result"),
        "facts": {"accepted_document_count": 166, "gates": gates},
    }, strict=True).facts.accepted_document_count == 166

    anchor = {
        "version": "c0b2-backup-anchor-v1",
        "run_id": "public-run",
        "active_stage": "D",
        "state": "PAUSED_STAGE_BOUNDARY",
        "f_master_plan_sha256": None,
        "plans": [
            {"plan_key": "C", "plan_sha256": _hash("c-plan"), "activation_sha256": None},
            {"plan_key": "D1_OUTPUT", "plan_sha256": _hash("d1-plan"),
             "activation_sha256": _hash("d1-activation")},
            {"plan_key": "D2_CHUNK", "plan_sha256": _hash("d2-plan"),
             "activation_sha256": _hash("d2-activation")},
            {"plan_key": "D3_CONTEXT", "plan_sha256": _hash("d3-plan"),
             "activation_sha256": _hash("d3-activation")},
        ],
        "aggregate_sha256": _hash("d-aggregate"),
        "decision_or_artifact_sha256": _hash("d-decision"),
        "charged_call_total": 20,
    }
    assert BackupAnchor.model_validate(anchor, strict=True).plans[-1].plan_key == "D3_CONTEXT"
    with pytest.raises(ValidationError):
        BackupAnchor.model_validate({**anchor, "aggregate_sha256": None}, strict=True)
    with pytest.raises(ValidationError):
        BackupAnchor.model_validate({**anchor, "active_stage": "C"}, strict=True)


def _backup_plans(*keys: str) -> list[dict[str, object]]:
    return [{
        "plan_key": key,
        "plan_sha256": _hash(key + "-plan"),
        "activation_sha256": None if key == "C" else _hash(key + "-activation"),
    } for key in keys]


def _backup_anchor(
    *, stage: str, state: str, keys: tuple[str, ...],
    aggregate: str | None = None,
) -> dict[str, object]:
    return {
        "version": "c0b2-backup-anchor-v1",
        "run_id": "public-run",
        "active_stage": stage,
        "state": state,
        "f_master_plan_sha256": _hash("f-master") if stage == "F" else None,
        "plans": _backup_plans(*keys),
        "aggregate_sha256": aggregate,
        "decision_or_artifact_sha256": _hash("decision-or-artifact"),
        "charged_call_total": 42,
    }


def test_backup_anchor_accepts_legal_terminal_prefixes() -> None:
    d_inconclusive = _backup_anchor(
        stage="D", state="INCONCLUSIVE",
        keys=("C", "D1_OUTPUT", "D2_CHUNK"),
        aggregate=_hash("d-aggregate"),
    )
    assert BackupAnchor.model_validate(
        d_inconclusive, strict=True).state == "INCONCLUSIVE"

    f_inconclusive = _backup_anchor(
        stage="F", state="INCONCLUSIVE",
        keys=("C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT", "F_SEED_1"),
        aggregate=_hash("seed1-evidence"),
    )
    assert BackupAnchor.model_validate(
        f_inconclusive, strict=True).plans[-1].plan_key == "F_SEED_1"

    failed_prefix = _backup_anchor(
        stage="F", state="BLOCKED_BUDGET",
        keys=("C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT",
              "D4_CONFIRMATION", "F_SEED_1", "F_SEED_17"),
    )
    assert BackupAnchor.model_validate(
        failed_prefix, strict=True).state == "BLOCKED_BUDGET"

    selected = _backup_anchor(
        stage="F", state="SELECTED",
        keys=("C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT",
              "F_SEED_1", "F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE"),
        aggregate=_hash("acceptance-aggregate"),
    )
    assert BackupAnchor.model_validate(selected, strict=True).state == "SELECTED"


def test_backup_anchor_rejects_impossible_lineage_and_state_endpoints() -> None:
    impossible = (
        _backup_anchor(
            stage="D", state="SELECTED",
            keys=("C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT"),
            aggregate=_hash("d-aggregate"),
        ),
        _backup_anchor(
            stage="F", state="PAUSED_STAGE_BOUNDARY",
            keys=("C", "D1_OUTPUT", "D2_CHUNK", "D3_CONTEXT", "F_SEED_1"),
            aggregate=_hash("f-aggregate"),
        ),
        _backup_anchor(
            stage="F", state="ABANDONED", keys=("C", "F_SEED_1")),
        _backup_anchor(
            stage="D", state="BLOCKED_PROVENANCE",
            keys=("C", "D1_OUTPUT", "D3_CONTEXT")),
    )
    for anchor in impossible:
        with pytest.raises(ValidationError):
            BackupAnchor.model_validate(anchor, strict=True)


def test_backup_receipt_path_and_status_are_fail_closed() -> None:
    receipt = {
        "version": "c0b2-backup-receipt-v1",
        "anchor_sha256": _hash("anchor"),
        "snapshot_run_relative_path": "backup/checkpoint.sqlite3",
        "snapshot_sha256": _hash("snapshot"),
        "snapshot_size_bytes": 4096,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "created_at_utc": "2026-08-05T12:34:56.125Z",
    }
    assert BackupReceipt.model_validate(
        receipt, strict=True).snapshot_size_bytes == 4096
    for path in ("/tmp/checkpoint.sqlite3", "backup/../checkpoint.sqlite3", "backup//x"):
        with pytest.raises(ValidationError):
            BackupReceipt.model_validate(
                {**receipt, "snapshot_run_relative_path": path}, strict=True)
    assert BackupStatus.model_validate({
        "required": True,
        "receipt_present": False,
        "anchor_sha256": _hash("anchor"),
        "snapshot_sha256": None,
    }, strict=True).required
    with pytest.raises(ValidationError):
        BackupStatus.model_validate({
            "required": False,
            "receipt_present": True,
            "anchor_sha256": _hash("anchor"),
            "snapshot_sha256": _hash("snapshot"),
        }, strict=True)
