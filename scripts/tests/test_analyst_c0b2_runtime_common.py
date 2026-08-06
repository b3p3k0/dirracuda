"""Focused offline proofs for the C0B-2 phase/runtime substrate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b2_checkpoint as ck
from scripts.analyst_benchmark import c0b2_executor as ex
from scripts.analyst_benchmark import c0b2_fsprobe as fs
from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import c0b2_runtime as public_runtime
from scripts.analyst_benchmark import c0b2_runtime_common as runtime
from scripts.analyst_benchmark.c0b2_public_schema import (
    activation_group_id, cancellation_control_id, context_control_id,
    health_control_id, health_work_id, public_cell_id, public_work_id,
    stage_d_candidate_id, stage_f_candidate_id,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


HEALTH_SOURCE = "Patient SSN 123-45-6789."


@pytest.fixture(autouse=True)
def _stub_typed_d_boundary_for_shared_substrate(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep generic F-activation tests focused; runtime-D owns full D evidence."""
    from scripts.analyst_benchmark import c0b2_runtime_d

    monkeypatch.setattr(c0b2_runtime_d, "load_stage_d_inputs", lambda _point: object())
    monkeypatch.setattr(
        c0b2_runtime_d, "validate_final_d_boundary",
        lambda point, _inputs: (_decision_hash(point, "stage-d-selection"), {}),
    )


def _header(root: Path, *, run_type: str = "public") -> dict[str, object]:
    digest = "a" * 64
    probe = fs.probe_filesystem(root)
    header = {
        "run_type": run_type, "ollama_endpoint": "http://127.0.0.1:11434",
        "ollama_version": "0.32.5", "filesystem_selected_mode": probe.selected_mode,
        "protocol_sha256": digest, "git_head": "b" * 40,
        "declared_dirty_state_sha256": digest, "task_tree_sha256": digest,
        "fixture_sha256": digest, "master_manifest_sha256": digest,
        "schema_sha256": digest, "prompt_sha256": digest,
        "chunker_sha256": digest, "detector_sha256": digest,
        "generation_options_sha256": digest, "worktree_seal_sha256": digest,
        "filesystem_capability_sha256": probe.capability_sha256,
        "model_digests": {"model:stable": digest},
        "mount": asdict(probe.fingerprint),
    }
    if run_type == "private":
        header["parent_selection_sha256"] = digest
    return header


def _checkpoint(
        tmp_path: Path, *, run_type: str = "public",
        classes: dict[str, int] | None = None,
        cumulative_cap: int | None = None) -> ck.Checkpoint:
    root = tmp_path / "bench"
    if classes is None and run_type == "public":
        limits = {stage: dict(values)
                  for stage, values in public_runtime.PUBLIC_LIMITS.items()}
        default_cap = public_runtime.PUBLIC_CUMULATIVE_CAP
    else:
        class_limits = classes or {
            "scored": 200, "schema_retry": 5,
            "preflight_probe": 20, "transport_orphan": 10,
        }
        limits = {stage: class_limits for stage in ("C", "D", "F")}
        default_cap = sum(class_limits.values()) * 3
    point = ck.Checkpoint.create(
        root, f"{run_type}-run", header=_header(root, run_type=run_type),
        limits=limits,
        cumulative_cap=(default_cap if cumulative_cap is None else cumulative_cap),
        journal_mode="DELETE",
    )
    c_plan = {"work": [
        {"work_id": "c-work", "cell_id": "c-cell",
         "request_sha256": "c-request", "model": "model:stable",
         "model_digest": "a" * 64},
        {"work_id": "second", "cell_id": "cell",
         "request_sha256": "second-request", "model": "model:stable",
         "model_digest": "a" * 64},
    ]}
    c_hash = point.freeze_plan("C", "root", c_plan)
    point.register_work("c-work", "C", "c-cell", "c-request")
    aggregate_raw = ck.canonical_json({"version": "test-stage-c-aggregate"})
    aggregate_hash = ck.sha256_json(json.loads(aggregate_raw))
    point.conn.execute(
        "INSERT INTO stage_aggregates VALUES(?,?,?,?,?)",
        ("C", c_hash, aggregate_hash, aggregate_raw, 1.0),
    )
    raw = ck.canonical_json({"survivors": ["fixture"]})
    frozen = ("C", c_hash, aggregate_hash, "ACTIVATED", raw)
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-c-selection", *frozen, 1.0),
    )
    point.conn.execute(
        "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY' WHERE id=1")
    return point


def _decision_hash(point: ck.Checkpoint, decision_id: str) -> str:
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    assert row is not None
    return ck.sha256_json((decision_id, *row))


def _d1_plan(parent: str, *, doc_id: str = "public-doc") -> dict[str, object]:
    model, digest, worksheet = "model:stable", "a" * 64, "v2"
    candidate_id = stage_d_candidate_id(model, digest, worksheet)
    cell_id = public_cell_id(
        budget_stage="D", candidate_id=candidate_id, chunk_chars=4000,
        num_ctx=8192, num_predict=2048, phase="D1", seed=1,
    )
    request_hash = _hash("request:" + doc_id)
    chunk_hash = _hash("chunk:" + doc_id)
    document_hash = _hash("document:" + doc_id)
    nonce = "FENCE_" + _hash("nonce:" + doc_id)[:32].upper()
    work_id = public_work_id(
        cell_id=cell_id, chunk_index=0, chunk_sha256=chunk_hash,
        doc_id=doc_id, document_sha256=document_hash, nonce=nonce,
        plan_key="D1_OUTPUT", request_sha256=request_hash, view_id=None,
    )
    return {
        "version": "stage-d-phase-plan-v1", "stage": "D", "phase": "D1",
        "plan_key": "D1_OUTPUT", "budget_stage": "D",
        "parent_decision_sha256": parent,
        "candidates": [{
            "candidate_id": candidate_id, "model": model,
            "model_digest": digest, "worksheet": worksheet,
            "chunk_chars": None, "overlap": None,
            "num_ctx": None, "num_predict": None,
        }],
        "work": [{
            "stage": "D", "phase": "D1", "plan_key": "D1_OUTPUT",
            "budget_stage": "D", "activation_group_id": None,
            "candidate_id": candidate_id, "cell_id": cell_id,
            "work_id": work_id, "model": model, "model_digest": digest,
            "worksheet": worksheet, "doc_id": doc_id, "view_id": None,
            "document_sha256": document_hash, "chunk_chars": 4000,
            "overlap": 256, "num_ctx": 8192, "num_predict": 2048,
            "seed": 1, "chunk_index": 0, "chunk_sha256": chunk_hash,
            "nonce": nonce, "prompt_sha256": _hash("prompt:" + doc_id),
            "request_sha256": request_hash,
        }],
    }


def _f1_plan(
        point: ck.Checkpoint, *, worksheets: tuple[str, ...] = ("v2",),
        d_plan_key: str = "D4_CONFIRMATION"):
    d_phase = {"D3_CONTEXT": "D3", "D4_CONFIRMATION": "D4"}[d_plan_key]
    d_plan = {"version": f"test-{d_phase.lower()}-plan",
              "worksheets": list(worksheets)}
    d_plan_raw, d_plan_hash = ck.canonical_json(d_plan), ck.sha256_json(d_plan)
    d_activation = {"version": "test-d4-activation", "plan_sha256": d_plan_hash}
    d_activation_raw = ck.canonical_json(d_activation)
    d_aggregate = {"version": "test-d4-aggregate", "plan_sha256": d_plan_hash}
    d_aggregate_raw = ck.canonical_json(d_aggregate)
    d_aggregate_hash = ck.sha256_json(d_aggregate)
    point.conn.execute(
        "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
        (d_plan_key, "D", d_phase, _hash("d-predecessor-decision"), d_plan_hash,
         d_plan_raw, 1.0),
    )
    point.conn.execute(
        "INSERT INTO plan_activations VALUES(?,?,?,?)",
        (d_plan_key, ck.sha256_json(d_activation), d_activation_raw, 1.0),
    )
    point.conn.execute(
        "INSERT INTO phase_aggregates VALUES(?,?,?,?,?)",
        (d_plan_key, d_plan_hash, d_aggregate_hash,
         d_aggregate_raw, 1.0),
    )
    decision_value = {"finalists": list(worksheets)}
    decision_raw = ck.canonical_json(decision_value)
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-d-selection", "D", d_plan_hash, d_aggregate_hash,
         "ACTIVATED", decision_raw, 1.0),
    )
    parent = _decision_hash(point, "stage-d-selection")
    point.conn.execute(
        "UPDATE runtime_cursor SET active_stage='D',"
        "active_plan_key=? WHERE id=1", (d_plan_key,))
    point.conn.execute(
        "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY' WHERE id=1")
    candidates = []
    groups = []
    work = []
    controls = []
    for worksheet in worksheets:
        selection = {
            "model": "model:stable", "model_digest": "a" * 64,
            "worksheet": worksheet, "chunk_chars": 4000, "overlap": 256,
            "num_ctx": 8192, "num_predict": 2048,
        }
        candidate_id = stage_f_candidate_id(selection, parent)
        group_id = activation_group_id(candidate_id, "F_SEED_1")
        cell_id = public_cell_id(
            budget_stage="F", candidate_id=candidate_id, chunk_chars=4000,
            num_ctx=8192, num_predict=2048, phase="F_SEED_1", seed=1)
        context_payload = _hash("context-payload:" + worksheet)
        config_hash = _hash("config:" + worksheet)
        context_id = context_control_id(
            candidate_id=candidate_id, config_sha256=config_hash,
            model="model:stable", model_digest="a" * 64,
            payload_sha256=context_payload, purpose="stage_f_candidate_context")
        context = {
            "control_id": context_id, "kind": "context_probe",
            "purpose": "stage_f_candidate_context", "candidate_id": candidate_id,
            "model": "model:stable", "model_digest": "a" * 64,
            "config_sha256": config_hash, "minimum_context_length": 8192,
            "trigger_rule": "first_http_terminal_seed1",
            "payload_sha256": context_payload,
        }
        cancel_request = _hash("cancel-request:" + worksheet)
        cancel_id = cancellation_control_id(
            candidate_id=candidate_id, request_sha256=cancel_request)
        cancellation = {
            "control_id": cancel_id, "kind": "cancellation_probe",
            "candidate_id": candidate_id, "source_doc_id": "pos_pii_013",
            "chunk_index": 0, "request_sha256": cancel_request,
            "max_close_after_first_byte_ms": 5000, "health_not_before_ms": 2000,
        }
        health_request = _hash("health-request:" + worksheet)
        health_nonce = "FENCE_" + _hash("health-nonce:" + worksheet)[:32].upper()
        health_id = health_control_id(
            candidate_id=candidate_id, nonce=health_nonce,
            request_sha256=health_request)
        health = {
            "control_id": health_id, "kind": "cancellation_health",
            "candidate_id": candidate_id, "source_doc_id": "pos_pii_013",
            "chunk_index": 0, "nonce": health_nonce,
            "health_work_id": health_work_id(
                candidate_id=candidate_id, request_sha256=health_request),
            "request_sha256": health_request,
        }
        candidates.append({"candidate_id": candidate_id, **selection})
        group_rows = []
        for index in range(72):
            doc_id = "pos_pii_013" if index == 0 else f"f_doc_{index:03d}"
            document_hash = _hash("f-document:" + doc_id)
            chunk_hash = (hashlib.sha256(HEALTH_SOURCE.encode("utf-8")).hexdigest()
                          if doc_id == "pos_pii_013" else _hash("f-chunk:" + doc_id))
            request_hash = _hash(f"f-request:{worksheet}:{doc_id}")
            nonce = "FENCE_" + _hash(
                f"f-nonce:{worksheet}:{doc_id}")[:32].upper()
            work_id = public_work_id(
                cell_id=cell_id, chunk_index=0, chunk_sha256=chunk_hash,
                doc_id=doc_id, document_sha256=document_hash, nonce=nonce,
                plan_key="F_SEED_1", request_sha256=request_hash, view_id=None)
            group_rows.append({
                "stage": "F", "phase": "F_SEED_1", "plan_key": "F_SEED_1",
                "budget_stage": "F", "activation_group_id": group_id,
                "candidate_id": candidate_id, "cell_id": cell_id,
                "work_id": work_id, "model": "model:stable",
                "model_digest": "a" * 64, "worksheet": worksheet,
                "doc_id": doc_id, "view_id": None,
                "document_sha256": document_hash, "chunk_chars": 4000,
                "overlap": 256, "num_ctx": 8192, "num_predict": 2048,
                "seed": 1, "chunk_index": 0, "chunk_sha256": chunk_hash,
                "nonce": nonce,
                "prompt_sha256": _hash(f"f-prompt:{worksheet}:{doc_id}"),
                "request_sha256": request_hash,
            })
        groups.append({
            "group_id": group_id, "candidate_id": candidate_id,
            "activation_predicate": "unconditional_stage_d_finalist",
            "first_work_id": group_rows[0]["work_id"],
            "last_work_id": group_rows[-1]["work_id"],
            "planned_work_count": 72, "context_control": context,
            "cancellation_control": cancellation, "health_control": health,
        })
        work.extend(group_rows)
        controls.append((context, cancellation, health))
    value = {
        "version": "stage-f-seed-plan-v1", "stage": "F",
        "phase": "F_SEED_1", "plan_key": "F_SEED_1", "budget_stage": "F",
        "parent_decision_sha256": parent, "candidates": candidates,
        "work": work, "groups": groups,
    }
    return value, tuple(controls)


def _later_seed_plan(seed1: dict, seed: int) -> dict:
    plan_key = f"F_SEED_{seed}"
    work = []
    groups = []
    for candidate in seed1["candidates"]:
        candidate_id = candidate["candidate_id"]
        group_id = activation_group_id(candidate_id, plan_key)
        cell_id = public_cell_id(
            budget_stage="F", candidate_id=candidate_id,
            chunk_chars=candidate["chunk_chars"], num_ctx=candidate["num_ctx"],
            num_predict=candidate["num_predict"], phase=plan_key, seed=seed)
        rows = []
        for source in (row for row in seed1["work"]
                       if row["candidate_id"] == candidate_id):
            suffix = f"{plan_key}:{candidate_id}:{source['doc_id']}"
            request_hash = _hash("request:" + suffix)
            nonce = "FENCE_" + _hash("nonce:" + suffix)[:32].upper()
            work_id = public_work_id(
                cell_id=cell_id, chunk_index=source["chunk_index"],
                chunk_sha256=source["chunk_sha256"], doc_id=source["doc_id"],
                document_sha256=source["document_sha256"], nonce=nonce,
                plan_key=plan_key, request_sha256=request_hash,
                view_id=source["view_id"])
            rows.append({
                **source, "phase": plan_key, "plan_key": plan_key,
                "activation_group_id": group_id, "cell_id": cell_id,
                "work_id": work_id, "seed": seed, "nonce": nonce,
                "prompt_sha256": _hash("prompt:" + suffix),
                "request_sha256": request_hash,
            })
        groups.append({
            "group_id": group_id, "candidate_id": candidate_id,
            "activation_predicate": "seed1_qualifier",
            "first_work_id": rows[0]["work_id"],
            "last_work_id": rows[-1]["work_id"],
            "planned_work_count": len(rows), "context_control": None,
            "cancellation_control": None, "health_control": None,
        })
        work.extend(rows)
    return {
        "version": "stage-f-seed-plan-v1", "stage": "F",
        "phase": plan_key, "plan_key": plan_key, "budget_stage": "F",
        "parent_decision_sha256": seed1["parent_decision_sha256"],
        "candidates": seed1["candidates"], "work": work, "groups": groups,
    }


def _seed_preflight(point: ck.Checkpoint, stage: str, ordinal: int) -> None:
    for kind, model in (("version", ex.SERVER_CONTROL_MODEL),
                        ("tags", ex.SERVER_CONTROL_MODEL),
                        ("show", "model:stable")):
        control_id = ex.control_id(stage, ordinal, kind, model)
        request_hash = _hash(f"preflight:{stage}:{ordinal}:{kind}:{model}")
        attempt_id = legacy_plan.attempt_id(f"control:{control_id}", 1)
        assert point.precharge(
            attempt_id=attempt_id, stage=stage, call_class="preflight_probe",
            request_hash=request_hash, attempt_no=1, control_id=control_id,
            invocation_ordinal=ordinal)
        point.finish_attempt(
            attempt_id, outcome="ACCEPTED", response="{}", metadata={},
            accept_work=False)


def _activated_f1(point: ck.Checkpoint, *, worksheets: tuple[str, ...] = ("v2",)):
    plan, controls = _f1_plan(point, worksheets=worksheets)
    groups = tuple(group["group_id"] for group in plan["groups"])
    point.freeze_activate_phase_plan(plan, activated_group_ids=groups)
    for rows in controls:
        for value in rows:
            point.freeze_runtime_control(
                "F_SEED_1", value["control_id"], value["kind"], value)
    return plan, controls


def _complete_f_group(
        point: ck.Checkpoint, plan: dict, candidate_id: str, ordinal: int) -> None:
    for item in (row for row in plan["work"]
                 if row["candidate_id"] == candidate_id):
        prior = point.conn.execute(
            "SELECT attempt_no,state FROM attempts WHERE work_id=? "
            "ORDER BY attempt_no DESC LIMIT 1", (item["work_id"],)).fetchone()
        attempt_no = int(prior[0]) + 1 if prior else 1
        call_class = "schema_retry" if prior else "scored"
        attempt_id = legacy_plan.attempt_id(item["work_id"], attempt_no)
        point.precharge(
            attempt_id=attempt_id, stage="F", call_class=call_class,
            request_hash=item["request_sha256"], attempt_no=attempt_no,
            work_id=item["work_id"], invocation_ordinal=ordinal)
        point.finish_attempt(
            attempt_id, outcome="ACCEPTED", response="{}", metadata={},
            accept_work=True)


def test_create_and_open_guard_additive_runtime_schema(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    path, root = point.path, point.root
    expected = {
        "runtime_cursor", "phase_plans", "plan_activations",
        "phase_work_registry", "runtime_controls", "runtime_control_events",
    }
    tables = {row[0] for row in point.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= tables
    assert point.runtime_position() == runtime.RuntimePosition("C", "C")
    point.close()

    reopened = ck.Checkpoint.open(path, root)
    assert reopened.header()["schema_version"] == 2
    assert reopened.runtime_position() == runtime.RuntimePosition("C", "C")
    reopened.close()


def test_phase_activation_is_atomic_immutable_and_registers_exact_work(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan = _d1_plan(_decision_hash(point, "stage-c-selection"))
    work = plan["work"][0]

    plan_hash = runtime.freeze_phase_plan(point, plan)
    assert point.conn.execute(
        "SELECT count(*) FROM work_items WHERE stage='D'").fetchone()[0] == 0
    result = point.freeze_activate_phase_plan(plan)
    assert result.plan_sha256 == plan_hash
    assert result.registered_work_ids == (work["work_id"],)
    assert point.runtime_position() == runtime.RuntimePosition("D", "D1_OUTPUT")
    assert point.state() == "RUNNING"
    assert point.work(work["work_id"]) == ("PENDING", None)
    assert point.freeze_activate_phase_plan(plan) == result

    changed = json.loads(json.dumps(plan))
    changed["work"][0]["prompt_sha256"] = _hash("changed")
    with pytest.raises((ck.ImmutableViolation, ValueError)):
        point.freeze_activate_phase_plan(changed)
    assert point.conn.execute(
        "SELECT count(*) FROM phase_work_registry").fetchone()[0] == 1
    point.close()


def test_activation_failure_rolls_back_plan_and_work(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan = _d1_plan(_decision_hash(point, "stage-c-selection"))
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")

    with pytest.raises(ck.CheckpointError, match="boundary"):
        point.freeze_activate_phase_plan(plan)
    assert point.conn.execute("SELECT count(*) FROM phase_plans").fetchone()[0] == 0
    assert point.conn.execute("SELECT count(*) FROM work_items WHERE stage='D'").fetchone()[0] == 0
    point.close()


def test_seed1_partial_group_activation_is_rejected(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, _controls = _f1_plan(point, worksheets=("v1", "v2"))
    first_group = plan["groups"][0]["group_id"]

    with pytest.raises(ck.ImmutableViolation, match="every frozen group"):
        point.freeze_activate_phase_plan(
            plan, activated_group_ids=(first_group,))
    assert point.conn.execute(
        "SELECT count(*) FROM phase_plans WHERE plan_key='F_SEED_1'"
    ).fetchone()[0] == 0
    assert point.conn.execute("SELECT count(*) FROM work_items WHERE stage='F'").fetchone()[0] == 0
    point.close()


def test_seed1_accepts_exact_d3_reuse_predecessor(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, _controls = _f1_plan(point, d_plan_key="D3_CONTEXT")
    groups = tuple(group["group_id"] for group in plan["groups"])

    point.freeze_activate_phase_plan(plan, activated_group_ids=groups)

    assert point.runtime_position() == runtime.RuntimePosition("F", "F_SEED_1")
    point.close()


def test_seed1_rejects_d3_reuse_when_any_d4_branch_exists(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, _controls = _f1_plan(point, d_plan_key="D3_CONTEXT")
    point.conn.execute(
        "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
        ("D4_CONFIRMATION", "D", "D4", _hash("parent"), _hash("rogue-plan"),
         ck.canonical_json({"rogue": True}), 1.0),
    )
    groups = tuple(group["group_id"] for group in plan["groups"])

    with pytest.raises(ck.ImmutableViolation, match="cannot coexist"):
        point.freeze_activate_phase_plan(plan, activated_group_ids=groups)

    assert point.runtime_position() == runtime.RuntimePosition("D", "D3_CONTEXT")
    assert point.conn.execute(
        "SELECT count(*) FROM phase_plans WHERE plan_key='F_SEED_1'"
    ).fetchone()[0] == 0
    point.close()


def test_seed1_rejects_when_typed_d_boundary_cannot_be_rebuilt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_d

    point = _checkpoint(tmp_path)
    plan, _controls = _f1_plan(point)
    groups = tuple(group["group_id"] for group in plan["groups"])
    monkeypatch.setattr(
        c0b2_runtime_d, "validate_final_d_boundary",
        lambda _point, _inputs: (_ for _ in ()).throw(
            c0b2_runtime_d.StageDRuntimeError("tampered final D evidence")),
    )

    with pytest.raises(ck.ImmutableViolation, match="cannot be rebuilt"):
        point.freeze_activate_phase_plan(plan, activated_group_ids=groups)

    assert point.runtime_position() == runtime.RuntimePosition("D", "D4_CONFIRMATION")
    assert point.conn.execute(
        "SELECT count(*) FROM phase_plans WHERE plan_key='F_SEED_1'"
    ).fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize("mutation", ("activation", "aggregate", "decision", "cursor"))
def test_seed1_rejects_inexact_d4_predecessor(
        tmp_path: Path, mutation: str) -> None:
    point = _checkpoint(tmp_path)
    plan, _controls = _f1_plan(point)
    if mutation == "activation":
        point.conn.execute(
            "DELETE FROM plan_activations WHERE plan_key='D4_CONFIRMATION'")
    elif mutation == "aggregate":
        point.conn.execute(
            "UPDATE phase_aggregates SET plan_hash=? "
            "WHERE plan_key='D4_CONFIRMATION'", (_hash("wrong-plan"),))
    elif mutation == "decision":
        point.conn.execute(
            "UPDATE decisions SET activation='NOT_ACTIVATED' "
            "WHERE decision_id='stage-d-selection'")
    else:
        point.conn.execute(
            "UPDATE runtime_cursor SET active_plan_key='D2_CHUNK' WHERE id=1")
    groups = tuple(group["group_id"] for group in plan["groups"])

    with pytest.raises((ck.CheckpointError, ck.ImmutableViolation)):
        point.freeze_activate_phase_plan(plan, activated_group_ids=groups)

    assert point.runtime_position().active_stage == "D"
    assert point.conn.execute(
        "SELECT count(*) FROM phase_plans WHERE plan_key='F_SEED_1'"
    ).fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize("seed", (17, 20260804))
def test_generic_activation_rejects_later_seed_without_any_mutation(
        tmp_path: Path, seed: int) -> None:
    point = _checkpoint(tmp_path)
    seed1, _controls = _activated_f1(point, worksheets=("v1", "v2"))
    later = _later_seed_plan(seed1, seed)
    group_ids = tuple(group["group_id"] for group in later["groups"])
    tables = (
        "decisions", "phase_plans", "plan_activations",
        "phase_work_registry", "work_items", "runtime_cursor", "run_state",
    )
    before = {table: point.conn.execute(f"SELECT * FROM {table}").fetchall()
              for table in tables}

    with pytest.raises(ck.CheckpointError, match="B4 atomic activation"):
        point.freeze_activate_phase_plan(
            later, activated_group_ids=group_ids,
            evidence_sha256=_hash("seed1-evidence"),
            activation_parent_decision_sha256=_hash("seed-activation"))

    after = {table: point.conn.execute(f"SELECT * FROM {table}").fetchall()
             for table in tables}
    assert after == before
    assert point.runtime_position() == runtime.RuntimePosition("F", "F_SEED_1")
    point.close()


def test_generic_activation_holds_acceptance_before_any_mutation(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    before = point.conn.total_changes
    with pytest.raises(ck.CheckpointError, match="B4 atomic activation"):
        point.freeze_activate_phase_plan({"plan_key": "F_ACCEPTANCE"})
    assert point.conn.total_changes == before
    point.close()


@pytest.mark.parametrize("mutation", ("run_id", "budget", "groups", "registry"))
def test_backup_activation_rejects_coherently_rehashed_seed1_lineage(
        tmp_path: Path, mutation: str) -> None:
    point = _checkpoint(tmp_path)
    plan, _controls = _activated_f1(point, worksheets=("v1", "v2"))
    row = point.conn.execute(
        "SELECT activation_json FROM plan_activations "
        "WHERE plan_key='F_SEED_1'"
    ).fetchone()
    activation = json.loads(row[0])
    public_runtime._validate_backup_activation(
        point.conn, point.header(), "F_SEED_1", plan, activation)

    if mutation == "run_id":
        activation["run_id"] = "other-public-run"
    elif mutation == "budget":
        activation["budget_stage"] = "D"
    elif mutation == "groups":
        activation["activated_group_ids"] = activation["activated_group_ids"][:1]
    else:
        point.conn.execute(
            "DELETE FROM phase_work_registry WHERE work_id=?",
            (plan["work"][-1]["work_id"],))
    raw = ck.canonical_json(activation)
    point.conn.execute(
        "UPDATE plan_activations SET activation_json=?,activation_hash=? "
        "WHERE plan_key='F_SEED_1'", (raw, ck.sha256_json(activation)))

    with pytest.raises(public_runtime.RuntimeGateError):
        public_runtime._validate_backup_activation(
            point.conn, point.header(), "F_SEED_1", plan, activation)
    point.close()


@pytest.mark.parametrize("key", ("F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE"))
def test_backup_anchor_delegates_b4_typed_activations(
        monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence

    received = []
    monkeypatch.setattr(
        c0b2_runtime_f_evidence, "validate_b4_backup_activation",
        lambda *args: received.append(args))
    values = (object(), {"run_id": "public-run"}, key, {}, {})
    public_runtime._validate_backup_activation(*values)
    assert received == [values]


def test_runtime_controls_must_be_strict_bound_and_active(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _f1_plan(point, worksheets=("v1", "v2"))
    groups = tuple(row["group_id"] for row in plan["groups"])
    point.freeze_activate_phase_plan(plan, activated_group_ids=groups)
    context = controls[0][0]
    with pytest.raises(ValueError):
        point.freeze_runtime_control(
            "F_SEED_1", context["control_id"], "context_probe",
            {**context, "unreviewed": True})
    changed = {**context, "payload_sha256": _hash("changed-payload")}
    changed["control_id"] = context_control_id(
        candidate_id=changed["candidate_id"],
        config_sha256=changed["config_sha256"], model=changed["model"],
        model_digest=changed["model_digest"],
        payload_sha256=changed["payload_sha256"], purpose=changed["purpose"])
    with pytest.raises(ck.ImmutableViolation, match="exact control"):
        point.freeze_runtime_control(
            "F_SEED_1", changed["control_id"], "context_probe", changed)

    activation = json.loads(point.conn.execute(
        "SELECT activation_json FROM plan_activations WHERE plan_key='F_SEED_1'"
    ).fetchone()[0])
    activation["activated_group_ids"] = [groups[0]]
    raw = ck.canonical_json(activation)
    point.conn.execute(
        "UPDATE plan_activations SET activation_json=?,activation_hash=? "
        "WHERE plan_key='F_SEED_1'", (raw, ck.sha256_json(activation)))
    second = controls[1][0]
    with pytest.raises(ck.ImmutableViolation, match="exact control"):
        point.freeze_runtime_control(
            "F_SEED_1", second["control_id"], "context_probe", second)
    point.close()


def test_first_f_answer_creates_global_context_barrier_before_next_work(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point, worksheets=("v1", "v2"))
    first = plan["work"][0]
    second_candidate = controls[1][0]["candidate_id"]
    other = next(row for row in plan["work"]
                 if row["candidate_id"] == second_candidate)
    context, cancellation, _health = controls[0]

    def transport(request, _cancel_event):
        if isinstance(request, ex.ControlRequest):
            value = {
                "purpose": context["purpose"],
                "config_sha256": context["config_sha256"],
                "model": context["model"], "digest": context["model_digest"],
                "size": 10, "size_vram": 0, "context_length": 8192,
            }
            return ex.FakeResponse(
                ck.canonical_json(value),
                metadata={"response_sha256": ck.sha256_json(value)})
        return ex.FakeResponse("{}")

    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(point, lock, transport)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        first_request = ex.WorkRequest(
            "F", first["work_id"], "model:stable",
            first["request_sha256"], 1)
        assert executor.run(first_request).outcome == "ACCEPTED"
        other_request = ex.WorkRequest(
            "F", other["work_id"], "model:stable",
            other["request_sha256"], 1)
        with pytest.raises(ck.CheckpointError, match="context probe is required"):
            executor.run(other_request)
        assert point.conn.execute(
            "SELECT 1 FROM attempts WHERE work_id=?", (other["work_id"],)
        ).fetchone() is None
        context_request = ex.ControlRequest(
            "F", context["control_id"], "model:stable",
            context["payload_sha256"], 1)
        assert executor.run_context_probe(context_request).outcome == "ACCEPTED"
        with pytest.raises(ck.CheckpointError, match="completed context and seed-1 work"):
            executor.run_cancellation_probe(ex.ControlRequest(
                "F", cancellation["control_id"], "model:stable",
                cancellation["request_sha256"], 1))
        assert executor.run(other_request).outcome == "ACCEPTED"
    point.close()


def test_f_context_rejects_zero_or_wrong_candidate_trigger_uncharged(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point, worksheets=("v1", "v2"))
    contacts = []

    def transport(request, _event):
        contacts.append(request.attempt_id)
        return ex.FakeResponse("{}")

    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(point, lock, transport)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        baseline = point.usage()["total"]
        for context in (controls[0][0],):
            request = ex.ControlRequest(
                "F", context["control_id"], "model:stable",
                context["payload_sha256"], 1)
            with pytest.raises(ck.CheckpointError, match="first answered trigger"):
                executor.run_context_probe(request)
            assert point.usage()["total"] == baseline and contacts == []
            assert point.conn.execute(
                "SELECT 1 FROM attempts WHERE attempt_id=?", (request.attempt_id,)
            ).fetchone() is None
        first = plan["work"][0]
        assert executor.run(ex.WorkRequest(
            "F", first["work_id"], "model:stable",
            first["request_sha256"], 1)).outcome == "ACCEPTED"
        contacts.clear()
        context = controls[1][0]
        wrong = ex.ControlRequest(
            "F", context["control_id"], "model:stable",
            context["payload_sha256"], 1)
        baseline = point.usage()["total"]
        with pytest.raises(
                ck.CheckpointError,
                match="pending Stage-F context has priority"):
            executor.run_context_probe(wrong)
        assert point.usage()["total"] == baseline and contacts == []
        assert point.conn.execute(
            "SELECT 1 FROM attempts WHERE attempt_id=?", (wrong.attempt_id,)
        ).fetchone() is None
    point.close()


def test_f_context_recovery_replays_trigger_then_observes_ps_before_other_work(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point, worksheets=("v1", "v2"))
    first = plan["work"][0]
    context = controls[0][0]
    other_context = controls[1][0]
    unrelated_model = "aaa-unrelated"
    actions = []
    resource_hash = first["request_sha256"]
    resource_id = ex.resource_probe_id(
        "F", 1, context["model"], resource_hash)

    def transport(request, _event):
        if isinstance(request, ex.WorkRequest):
            actions.append(("work", request.work_id))
            return ex.FakeResponse("{}")
        if request.control_id == resource_id:
            actions.append(("recovery", request.request_hash))
            return ex.FakeResponse(
                "{}", accepted=False, outcome="SCHEMA_INVALID")
        if request.control_id == context["control_id"]:
            actions.append(("ps", request.control_id))
            value = {
                "purpose": context["purpose"],
                "config_sha256": context["config_sha256"],
                "model": context["model"], "digest": context["model_digest"],
                "size": 10, "size_vram": 0, "context_length": 8192,
            }
            return ex.FakeResponse(
                ck.canonical_json(value),
                metadata={"response_sha256": ck.sha256_json(value)})
        actions.append(("generic", request.request_hash))
        return ex.FakeResponse("{}")

    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(point, lock, transport)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        assert executor.run(ex.WorkRequest(
            "F", first["work_id"], context["model"], resource_hash, 1,
        )).outcome == "ACCEPTED"
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (context["model"], 1.0))
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (unrelated_model, 1.0))
        generic = ex.ControlRequest(
            "F", ex.resource_probe_id("F", 1, unrelated_model, "generic"),
            unrelated_model, "generic", 1, "transport_orphan")
        baseline = point.usage()["total"]
        with pytest.raises(
                ck.CheckpointError, match="context has priority over generic"):
            executor.run_resource_probe(generic)
        assert point.usage()["total"] == baseline

        recovery = ex.ControlRequest(
            "F", resource_id, context["model"], resource_hash,
            1, "transport_orphan")
        assert executor.run_resource_probe(
            recovery,
            prioritized_context_control_id=context["control_id"],
        ).outcome == "SCHEMA_INVALID"
        assert point.backoff(context["model"]).failures == 0
        assert point.backoff(unrelated_model).failures == 6

        other = next(row for row in plan["work"]
                     if row["work_id"] != first["work_id"])
        with pytest.raises(ck.CheckpointError, match="context probe is required"):
            executor.run(ex.WorkRequest(
                "F", other["work_id"], other["model"],
                other["request_sha256"], 1))
        with pytest.raises(
                ck.CheckpointError, match="context has priority over another"):
            executor.run_context_probe(ex.ControlRequest(
                "F", other_context["control_id"], other_context["model"],
                other_context["payload_sha256"], 1))
        with pytest.raises(
                ck.CheckpointError, match="context has priority over generic"):
            executor.run_resource_probe(generic)

        assert executor.run_context_probe(ex.ControlRequest(
            "F", context["control_id"], context["model"],
            context["payload_sha256"], 1)).outcome == "ACCEPTED"
        with pytest.raises(
                ck.CheckpointError, match="persisted resource obligation"):
            executor.run_resource_probe(generic)
        assert point.backoff(unrelated_model).failures == 6

    assert actions == [
        ("work", first["work_id"]),
        ("recovery", first["request_sha256"]),
        ("ps", context["control_id"]),
    ]
    point.close()


@pytest.mark.parametrize("mismatch", ("owner", "request", "model"))
def test_f_context_recovery_rejects_mismatched_trigger_uncharged(
        tmp_path: Path, mismatch: str) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point, worksheets=("v1", "v2"))
    first, context = plan["work"][0], controls[0][0]
    contacts = []

    def transport(request, _event):
        contacts.append(request.attempt_id)
        return ex.FakeResponse("{}")

    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(point, lock, transport)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        assert executor.run(ex.WorkRequest(
            "F", first["work_id"], first["model"],
            first["request_sha256"], 1)).outcome == "ACCEPTED"
        contacts.clear()
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (context["model"], 1.0))
        with pytest.raises(
                ck.CheckpointError, match="exact recovery replay"):
            executor.run_context_probe(ex.ControlRequest(
                "F", context["control_id"], context["model"],
                context["payload_sha256"], 1))

        owner = (controls[1][0]["control_id"] if mismatch == "owner"
                 else context["control_id"])
        request_hash = (_hash("wrong-trigger") if mismatch == "request"
                        else first["request_sha256"])
        model = "other:model" if mismatch == "model" else context["model"]
        request = ex.ControlRequest(
            "F", ex.resource_probe_id("F", 1, model, request_hash),
            model, request_hash, 1, "transport_orphan")
        baseline = point.usage()["total"]
        with pytest.raises(
                ck.CheckpointError, match="differs from its context trigger"):
            executor.run_resource_probe(
                request, prioritized_context_control_id=owner)
        assert contacts == [] and point.usage()["total"] == baseline
        assert point.conn.execute(
            "SELECT 1 FROM attempts WHERE attempt_id=?", (request.attempt_id,)
        ).fetchone() is None
    point.close()


def test_public_checkpoint_primitives_reject_naked_failure_terminals(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    for terminal in ("FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
                     "BLOCKED_FILESYSTEM", "BLOCKED_SECURITY", "ABANDONED"):
        with pytest.raises(ck.CheckpointError, match="atomic artifact finalization"):
            point.transition(terminal)
    attempt_id = legacy_plan.attempt_id("c-work", 1)
    point.precharge(
        attempt_id=attempt_id, stage="C", call_class="scored",
        request_hash="c-request", attempt_no=1, work_id="c-work")
    for terminal in ("FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_SECURITY"):
        with pytest.raises(ck.CheckpointError, match="atomic artifact finalization"):
            point.finish_attempt(
                attempt_id, outcome=terminal, response=None, metadata={},
                accept_work=False, terminal_state=terminal)
    assert point.state() == "RUNNING"
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 0
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()[0] == "DISPATCHING"
    point.close()


def test_runtime_control_transition_joins_outer_transaction(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _f1_plan(point)
    group_ids = tuple(group["group_id"] for group in plan["groups"])
    point.freeze_activate_phase_plan(plan, activated_group_ids=group_ids)
    control = controls[0][1]
    control_id = control["control_id"]
    digest = point.freeze_runtime_control(
        "F_SEED_1", control_id, "cancellation_probe", control)
    assert point.load_runtime_control(control_id).control_sha256 == digest

    attempt_id = legacy_plan.attempt_id(f"control:{control_id}", 1)
    assert point.precharge(
        attempt_id=attempt_id, stage="F", call_class="preflight_probe",
        request_hash=control["request_sha256"], attempt_no=1,
        control_id=control_id, first_control_class="preflight_probe")
    evidence = {
        "version": "c0b2-cancellation-observation-v1",
        "cancel_control_id": control_id, "cancel_attempt_id": attempt_id,
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": True, "cancel_elapsed_ms": 25,
    }
    point.conn.execute("BEGIN IMMEDIATE")
    point.conn.execute(
        "UPDATE attempts SET state='CANCELLED_UNVERIFIED',metadata_json=?,updated=100 "
        "WHERE attempt_id=?", (ck.canonical_json({
            "cancel_elapsed_ms": 25, "cancel_first_byte_seen": True,
            "owned_stream_cancelled": True,
        }), attempt_id))
    point.update_runtime_control(
        control_id, expected_state="PENDING", state="CANCELLED_UNVERIFIED",
        evidence=evidence, not_before_utc="1970-01-01T00:01:42Z")
    point.conn.rollback()
    assert point.load_runtime_control(control_id).state == "PENDING"
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()[0] == "DISPATCHING"
    assert point.conn.execute(
        "SELECT count(*) FROM runtime_control_events WHERE control_id=?",
        (control_id,),
    ).fetchone()[0] == 1

    point.conn.execute("BEGIN IMMEDIATE")
    point.conn.execute(
        "UPDATE attempts SET state='CANCELLED_UNVERIFIED',metadata_json=?,updated=100 "
        "WHERE attempt_id=?", (ck.canonical_json({
            "cancel_elapsed_ms": 25, "cancel_first_byte_seen": True,
            "owned_stream_cancelled": True,
        }), attempt_id))
    complete = point.update_runtime_control(
        control_id, expected_state="PENDING", state="CANCELLED_UNVERIFIED",
        evidence=evidence, not_before_utc="1970-01-01T00:01:42Z")
    point.conn.commit()
    assert complete.state == "CANCELLED_UNVERIFIED"
    assert complete.evidence_sha256 == ck.sha256_json(evidence)
    with pytest.raises((ck.ImmutableViolation, ValueError)):
        point.freeze_runtime_control(
            "F_SEED_1", control_id, "cancellation_probe",
            {**control, "health_not_before_ms": 3000})
    point.close()


def test_cancellation_requires_and_retains_exact_not_before(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _f1_plan(point)
    group_ids = tuple(group["group_id"] for group in plan["groups"])
    point.freeze_activate_phase_plan(plan, activated_group_ids=group_ids)
    control = controls[0][1]
    control_id = control["control_id"]
    point.freeze_runtime_control(
        "F_SEED_1", control_id, "cancellation_probe", control)
    attempt_id = legacy_plan.attempt_id(f"control:{control_id}", 1)
    assert point.precharge(
        attempt_id=attempt_id, stage="F", call_class="preflight_probe",
        request_hash=control["request_sha256"], attempt_no=1,
        control_id=control_id, first_control_class="preflight_probe")
    point.conn.execute(
        "UPDATE attempts SET state='CANCELLED_UNVERIFIED',metadata_json=?,updated=100 "
        "WHERE attempt_id=?", (ck.canonical_json({
            "cancel_elapsed_ms": 25, "cancel_first_byte_seen": True,
            "owned_stream_cancelled": True,
        }), attempt_id))
    evidence = {
        "version": "c0b2-cancellation-observation-v1",
        "cancel_control_id": control_id, "cancel_attempt_id": attempt_id,
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": True, "cancel_elapsed_ms": 25,
    }
    with pytest.raises(ck.CheckpointError, match="not-before"):
        point.update_runtime_control(
            control_id, expected_state="PENDING",
            state="CANCELLED_UNVERIFIED", evidence=evidence)
    cancelled = point.update_runtime_control(
        control_id, expected_state="PENDING", state="CANCELLED_UNVERIFIED",
        evidence=evidence, not_before_utc="1970-01-01T00:01:42Z")
    assert cancelled.not_before_utc == "1970-01-01T00:01:42Z"
    assert cancelled.state == "CANCELLED_UNVERIFIED"
    point.close()


def test_f_controls_derive_context_owned_cancel_and_health_evidence(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point)
    context, cancellation, health = controls[0]
    first_work = plan["work"][0]
    point.precharge(
        attempt_id=legacy_plan.attempt_id(first_work["work_id"], 1), stage="F",
        call_class="scored", request_hash=first_work["request_sha256"],
        attempt_no=1, work_id=first_work["work_id"])
    point.finish_attempt(
        legacy_plan.attempt_id(first_work["work_id"], 1),
        outcome="SCHEMA_INVALID", response="{}", metadata={}, accept_work=False)
    now = [100.0]
    responses: list[ex.FakeResponse] = []
    owned_events = []

    def transport(_request, cancel_event):
        if responses:
            return responses.pop(0)
        owned_events.append(cancel_event)
        cancel_event.set()
        raise ex.RetryableTransport

    with fs.GlobalExecutionLock(point.root) as lock:
        executor1 = ex.DurableExecutor(point, lock, transport, now=lambda: now[0])
        assert executor1.recover_and_start("F")[1] == 1
        _seed_preflight(point, "F", 1)
        ps_value = {
            "purpose": context["purpose"],
            "config_sha256": context["config_sha256"],
            "model": context["model"], "digest": context["model_digest"],
            "size": 10, "size_vram": 0, "context_length": 8192,
        }
        ps_raw = ck.canonical_json(ps_value)
        responses.append(ex.FakeResponse(
            ps_raw, metadata={"response_sha256": ck.sha256_json(ps_value)}))
        context_request = ex.ControlRequest(
            "F", context["control_id"], "model:stable",
            context["payload_sha256"], 1)
        assert executor1.run_context_probe(context_request).outcome == "ACCEPTED"
        context_record = point.load_runtime_control(context["control_id"])
        context_evidence = json.loads(str(context_record.evidence_json))
        assert context_evidence["trigger_work_id"] == first_work["work_id"]
        assert context_evidence["observed_context_length"] == 8192

        with pytest.raises(ck.CheckpointError, match="completed context and seed-1 work"):
            executor1.run_cancellation_probe(ex.ControlRequest(
                "F", cancellation["control_id"], "model:stable",
                cancellation["request_sha256"], 1))
        _complete_f_group(point, plan, context["candidate_id"], 1)
        blocked_work = plan["work"][1]
        with pytest.raises(ck.CheckpointError, match="cancellation probe is required"):
            executor1.run(ex.WorkRequest(
                "F", blocked_work["work_id"], "model:stable",
                blocked_work["request_sha256"], 2, "schema_retry"))

        cancel_request = ex.ControlRequest(
            "F", cancellation["control_id"], "model:stable",
            cancellation["request_sha256"], 1)
        cancel_result = executor1.run_cancellation_probe(cancel_request)
        assert cancel_result.outcome == "CANCELLED_UNVERIFIED"
        assert owned_events and owned_events[0] is not executor1.cancellation.event
        assert executor1.cancellation.event.is_set() is False
        cancel_record = point.load_runtime_control(cancellation["control_id"])
        assert cancel_record.not_before_utc == "1970-01-01T00:01:42.000000Z"

        executor2 = ex.DurableExecutor(point, lock, transport, now=lambda: now[0])
        assert executor2.recover_and_start("F")[1] == 2
        _seed_preflight(point, "F", 2)
        with pytest.raises(ck.CheckpointError, match="cancellation health is required"):
            executor2.run(ex.WorkRequest(
                "F", blocked_work["work_id"], "model:stable",
                blocked_work["request_sha256"], 2, "schema_retry"))
        health_request1 = ex.ControlRequest(
            "F", health["control_id"], "model:stable",
            health["request_sha256"], 1)
        now[0] = 101.0
        waiting = executor2.run_cancellation_health(
            health_request1, cancelled_attempt_id=cancel_request.attempt_id,
            source=HEALTH_SOURCE, worksheet="v2", num_predict=2048, num_ctx=8192)
        assert waiting.outcome == "RETRY_WAIT" and waiting.retry_not_before == 102.0
        now[0] = 102.0
        point.precharge(
            attempt_id=health_request1.attempt_id, stage="F",
            call_class="preflight_probe", request_hash=health["request_sha256"],
            attempt_no=1, control_id=health["control_id"], invocation_ordinal=2,
            first_control_class="preflight_probe")
        assert point.recover() == 1
        executor3 = ex.DurableExecutor(point, lock, transport, now=lambda: now[0])
        assert executor3.recover_and_start("F")[1] == 3
        _seed_preflight(point, "F", 3)
        invalid_metadata = {
            "done_reason": "stop", "prompt_eval_count": 100,
            "tools_empty": True, "images_empty": True,
            "unknown_message_fields_empty": True,
        }
        responses.append(ex.FakeResponse(
            "{}", metadata=invalid_metadata, accepted=False,
            outcome="SCHEMA_INVALID"))
        health_request2 = ex.ControlRequest(
            "F", health["control_id"], "model:stable",
            health["request_sha256"], 2, "transport_orphan")
        assert executor3.run_cancellation_health(
            health_request2, cancelled_attempt_id=cancel_request.attempt_id,
            source=HEALTH_SOURCE, worksheet="v2", num_predict=2048,
            num_ctx=8192).outcome == "SCHEMA_INVALID"
        accepted = ck.canonical_json({
            "document_type": "record", "subject": "patient",
            "assessment": "findings_present",
            "findings": [{"category": "pii", "quote": "123-45-6789", "offset": 12}],
        })
        responses.append(ex.FakeResponse(accepted, metadata={
            **invalid_metadata, "prompt_eval_count": 200}))
        health_request3 = ex.ControlRequest(
            "F", health["control_id"], "model:stable",
            health["request_sha256"], 3, "schema_retry")
        assert executor3.run_cancellation_health(
            health_request3, cancelled_attempt_id=cancel_request.attempt_id,
            source=HEALTH_SOURCE, worksheet="v2", num_predict=2048,
            num_ctx=8192).outcome == "ACCEPTED"

    health_record = point.load_runtime_control(health["control_id"])
    evidence = json.loads(str(health_record.evidence_json))
    assert health_record.state == "COMPLETE"
    assert evidence["passed"] is True and evidence["failure_reasons"] == []
    assert evidence["retained_grounded_pii"] is True
    assert evidence["max_answered_prompt_eval_count"] == 200
    assert len(evidence["health_attempt_ids"]) == 3
    with pytest.raises(ck.ImmutableViolation, match="inputs differ"):
        runtime._health_evidence(
            point, health, cancel_record, source="laundered source",
            worksheet="v2", num_predict=2048, num_ctx=8192)
    point.close()


def test_owned_probe_never_masks_operator_cancellation(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    plan, controls = _activated_f1(point)
    context, cancellation, _health = controls[0]
    first = plan["work"][0]
    first_attempt = legacy_plan.attempt_id(first["work_id"], 1)
    point.precharge(
        attempt_id=first_attempt, stage="F", call_class="scored",
        request_hash=first["request_sha256"], attempt_no=1,
        work_id=first["work_id"])
    point.finish_attempt(
        first_attempt, outcome="SCHEMA_INVALID", response="{}",
        metadata={}, accept_work=False)

    def transport(request, _owned_event):
        if request.control_id == context["control_id"]:
            value = {
                "purpose": context["purpose"],
                "config_sha256": context["config_sha256"],
                "model": context["model"], "digest": context["model_digest"],
                "size": 10, "size_vram": 0, "context_length": 8192,
            }
            return ex.FakeResponse(
                ck.canonical_json(value),
                metadata={"response_sha256": ck.sha256_json(value)})
        executor.cancellation.first_signal()
        raise ex.RetryableTransport

    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(point, lock, transport)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        assert executor.run_context_probe(ex.ControlRequest(
            "F", context["control_id"], "model:stable",
            context["payload_sha256"], 1)).outcome == "ACCEPTED"
        _complete_f_group(point, plan, context["candidate_id"], 1)
        request = ex.ControlRequest(
            "F", cancellation["control_id"], "model:stable",
            cancellation["request_sha256"], 1)
        result = executor.run_cancellation_probe(request)
    assert result.outcome == "CANCELLED_PENDING_RESUME"
    assert point.state() == "CANCELLED_PENDING_RESUME"
    assert point.load_runtime_control(cancellation["control_id"]).state == "PENDING"
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?", (request.attempt_id,)
    ).fetchone()[0] == "CANCELLED_UNVERIFIED"
    point.close()


def _public_budget_callback(point: ck.Checkpoint, seen: list[dict[str, object]]):
    def callback(payload):
        assert point.conn.in_transaction is True
        seen.append(dict(payload))
        public_runtime.finish_public_budget_failure(point, payload)
    return callback


def _one_accepted_c_work(point: ck.Checkpoint) -> None:
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    point.precharge(
        attempt_id=legacy_plan.attempt_id("c-work", 1), stage="C",
        call_class="scored", request_hash="c-request", attempt_no=1,
        work_id="c-work")
    point.finish_attempt(
        legacy_plan.attempt_id("c-work", 1), outcome="ACCEPTED",
        response="{}", metadata={}, accept_work=True)


def _assert_budget_terminal(point: ck.Checkpoint, payload: dict[str, object]) -> None:
    assert point.state() == "BLOCKED_BUDGET"
    rows = point.conn.execute(
        "SELECT terminal,artifact_json FROM public_artifacts ORDER BY artifact_id"
    ).fetchall()
    assert len(rows) == 2 and {row[0] for row in rows} == {"BLOCKED_BUDGET"}
    values = [json.loads(row[1]) for row in rows]
    evidence = next(row for row in values if row["version"].endswith("evidence-v1"))
    assert evidence["attempt_id"] is None
    assert evidence["control_id"] == payload["control_id"]


def test_public_invocation_cap_finalizes_exact_uncharged_failure(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    assert [point.claim_invocation("C", budget_failure_callback=lambda _p: None)
            for _ in range(3)] == [1, 2, 3]
    seen: list[dict[str, object]] = []
    with pytest.raises(ck.CapExceeded):
        point.claim_invocation(
            "C", budget_failure_callback=_public_budget_callback(point, seen))
    expected = {"stage": "C", "attempt_id": None,
                "control_id": None, "work_id": None}
    assert seen == [expected]
    assert point.conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 3
    assert point.conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
    _assert_budget_terminal(point, expected)
    point.close()


def test_public_class_cap_finalizes_exact_uncharged_failure(tmp_path: Path) -> None:
    classes = {"scored": 1, "schema_retry": 2,
               "preflight_probe": 2, "transport_orphan": 2}
    point = _checkpoint(tmp_path, classes=classes)
    _one_accepted_c_work(point)
    point.register_work("second", "C", "cell", "second-request")
    seen: list[dict[str, object]] = []
    with pytest.raises(ck.CapExceeded):
        point.precharge(
            attempt_id=legacy_plan.attempt_id("second", 1), stage="C",
            call_class="scored", request_hash="second-request", attempt_no=1,
            work_id="second", budget_failure_callback=_public_budget_callback(point, seen))
    expected = {"stage": "C", "attempt_id": None,
                "control_id": None, "work_id": "second"}
    assert seen == [expected]
    assert point.conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
    assert point.work("second") == ("PENDING", None)
    _assert_budget_terminal(point, expected)
    point.close()


def test_public_cumulative_cap_finalizes_control_failure(tmp_path: Path) -> None:
    classes = {"scored": 3, "schema_retry": 2,
               "preflight_probe": 3, "transport_orphan": 2}
    point = _checkpoint(tmp_path, classes=classes, cumulative_cap=1)
    _one_accepted_c_work(point)
    control_id = _hash("cumulative-control")
    attempt_id = legacy_plan.attempt_id(f"control:{control_id}", 1)
    seen: list[dict[str, object]] = []
    with pytest.raises(ck.CapExceeded):
        point.precharge(
            attempt_id=attempt_id, stage="C", call_class="preflight_probe",
            request_hash="probe", attempt_no=1, control_id=control_id,
            budget_failure_callback=_public_budget_callback(point, seen))
    expected = {"stage": "C", "attempt_id": None,
                "control_id": control_id, "work_id": None}
    assert seen == [expected]
    assert point.conn.execute(
        "SELECT 1 FROM attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone() is None
    _assert_budget_terminal(point, expected)
    point.close()


@pytest.mark.parametrize("callback_mode", ["missing", "throws"])
def test_public_cap_failure_finalizer_must_succeed_or_everything_rolls_back(
        tmp_path: Path, callback_mode: str) -> None:
    classes = {"scored": 1, "schema_retry": 1,
               "preflight_probe": 1, "transport_orphan": 1}
    point = _checkpoint(tmp_path, classes=classes)
    _one_accepted_c_work(point)
    point.register_work("second", "C", "cell", "second-request")
    kwargs = {}
    if callback_mode == "throws":
        kwargs["budget_failure_callback"] = lambda _payload: (_ for _ in ()).throw(
            RuntimeError("artifact crash"))
        expected_error = RuntimeError
    else:
        expected_error = ck.CheckpointError
    with pytest.raises(expected_error):
        point.precharge(
            attempt_id=legacy_plan.attempt_id("second", 1), stage="C",
            call_class="scored", request_hash="second-request", attempt_no=1,
            work_id="second", **kwargs)
    assert point.state() == "RUNNING"
    assert point.conn.in_transaction is False
    assert point.conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize("callback_mode", ["missing", "throws"])
def test_public_invocation_cap_callback_failure_rolls_back(
        tmp_path: Path, callback_mode: str) -> None:
    point = _checkpoint(tmp_path)
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    for _ in range(3):
        point.claim_invocation("C")
    kwargs = {}
    if callback_mode == "throws":
        kwargs["budget_failure_callback"] = lambda _payload: (_ for _ in ()).throw(
            RuntimeError("artifact crash"))
        expected_error = RuntimeError
    else:
        expected_error = ck.CheckpointError
    with pytest.raises(expected_error):
        point.claim_invocation("C", **kwargs)
    assert point.state() == "RUNNING" and point.conn.in_transaction is False
    assert point.conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 3
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 0
    point.close()


def test_private_caps_ignore_public_callback_contract(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, run_type="private")
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    called = []
    callback = lambda payload: called.append(payload)
    for _ in range(3):
        point.claim_invocation("C", budget_failure_callback=callback)
    with pytest.raises(ck.CapExceeded):
        point.claim_invocation("C", budget_failure_callback=callback)
    assert called == [] and point.state() == "BLOCKED_BUDGET"
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 0
    point.close()


def test_stage_c_public_failure_is_atomic_rollback_safe_and_idempotent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
    attempt_id = legacy_plan.attempt_id("c-work", 1)
    point.precharge(
        attempt_id=attempt_id, stage="C", call_class="scored",
        request_hash="c-request", attempt_no=1, work_id="c-work")
    original = public_runtime.freeze_public_artifact
    calls = []

    def crash_second(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 2:
            raise RuntimeError("artifact crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(public_runtime, "freeze_public_artifact", crash_second)
    with pytest.raises(RuntimeError, match="artifact crash"):
        public_runtime.finish_public_failure_attempt(
            point, attempt_id=attempt_id, terminal="FAILED_SAFETY")
    assert point.state() == "RUNNING" and point.conn.in_transaction is False
    assert point.work("c-work") == ("DISPATCHING", None)
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()[0] == "DISPATCHING"
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 0

    monkeypatch.setattr(public_runtime, "freeze_public_artifact", original)
    with fs.GlobalExecutionLock(point.root) as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda *_args: pytest.fail("no transport expected"))
        executor._finish_failure_attempt(attempt_id, "FAILED_SAFETY")
    first_hash = public_runtime.finish_public_failure_attempt(
        point, attempt_id=attempt_id, terminal="FAILED_SAFETY")
    assert len(first_hash) == 64 and point.state() == "FAILED_SAFETY"
    assert point.conn.execute("SELECT count(*) FROM public_artifacts").fetchone()[0] == 2
    point.close()


@pytest.mark.parametrize(
    "mutation", [
        "plan", "activation", "evidence", "unknown_key",
        "decision_stage", "decision_parent", "decision_aggregate",
    ])
def test_public_verify_reports_semantic_backup_tampering(
        tmp_path: Path, mutation: str) -> None:
    point = _checkpoint(tmp_path)
    plan = _d1_plan(_decision_hash(point, "stage-c-selection"))
    point.freeze_activate_phase_plan(plan)
    public_runtime.finish_public_run_failure(point, terminal="ABANDONED")
    if mutation == "unknown_key":
        unknown_plan = {"version": "unknown-plan"}
        unknown_activation = {"version": "unknown-activation"}
        point.conn.execute(
            "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
            ("UNKNOWN", "D", "D1", _hash("parent"),
             ck.sha256_json(unknown_plan), ck.canonical_json(unknown_plan), 2.0))
        point.conn.execute(
            "INSERT INTO plan_activations VALUES(?,?,?,?)",
            ("UNKNOWN", ck.sha256_json(unknown_activation),
             ck.canonical_json(unknown_activation), 2.0))
    elif mutation.startswith("decision_"):
        column = {
            "decision_stage": "stage",
            "decision_parent": "parent_hash",
            "decision_aggregate": "aggregate_hash",
        }[mutation]
        replacement = "F" if column == "stage" else _hash(mutation)
        point.conn.execute(
            f"UPDATE decisions SET {column}=? WHERE decision_id='stage-c-selection'",
            (replacement,),
        )
        parent = _decision_hash(point, "stage-c-selection")
        value = json.loads(point.conn.execute(
            "SELECT plan_json FROM phase_plans WHERE plan_key='D1_OUTPUT'"
        ).fetchone()[0])
        value["parent_decision_sha256"] = parent
        raw, digest = ck.canonical_json(value), ck.sha256_json(value)
        point.conn.execute(
            "UPDATE phase_plans SET parent_decision_sha256=?,plan_json=?,plan_hash=? "
            "WHERE plan_key='D1_OUTPUT'", (parent, raw, digest))
        activation = json.loads(point.conn.execute(
            "SELECT activation_json FROM plan_activations "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0])
        activation["parent_decision_sha256"] = parent
        activation["plan_sha256"] = digest
        activation_raw = ck.canonical_json(activation)
        point.conn.execute(
            "UPDATE plan_activations SET activation_json=?,activation_hash=? "
            "WHERE plan_key='D1_OUTPUT'",
            (activation_raw, ck.sha256_json(activation)))
    elif mutation == "plan":
        value = json.loads(point.conn.execute(
            "SELECT plan_json FROM phase_plans WHERE plan_key='D1_OUTPUT'"
        ).fetchone()[0])
        value["unreviewed"] = True
        raw, digest = ck.canonical_json(value), ck.sha256_json(value)
        activation = json.loads(point.conn.execute(
            "SELECT activation_json FROM plan_activations "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0])
        activation["plan_sha256"] = digest
        activation_raw = ck.canonical_json(activation)
        point.conn.execute(
            "UPDATE phase_plans SET plan_json=?,plan_hash=? WHERE plan_key='D1_OUTPUT'",
            (raw, digest))
        point.conn.execute(
            "UPDATE plan_activations SET activation_json=?,activation_hash=? "
            "WHERE plan_key='D1_OUTPUT'",
            (activation_raw, ck.sha256_json(activation)))
    elif mutation == "activation":
        value = json.loads(point.conn.execute(
            "SELECT activation_json FROM plan_activations "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0])
        value["budget_stage"] = "F"
        point.conn.execute(
            "UPDATE plan_activations SET activation_json=?,activation_hash=? "
            "WHERE plan_key='D1_OUTPUT'",
            (ck.canonical_json(value), ck.sha256_json(value)))
    else:
        row = point.conn.execute(
            "SELECT artifact_id,artifact_json FROM public_artifacts "
            "WHERE artifact_json LIKE '%failure-evidence-v1%'"
        ).fetchone()
        evidence = json.loads(row[1])
        evidence["reason_code"] = "safety_envelope_failure"
        evidence_hash = ck.sha256_json(evidence)
        point.conn.execute(
            "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? "
            "WHERE artifact_id=?",
            (ck.canonical_json(evidence), evidence_hash, row[0]))
        artifact_row = point.conn.execute(
            "SELECT artifact_id,artifact_json FROM public_artifacts "
            "WHERE artifact_json LIKE '%c0b2-failure-v1%'"
        ).fetchone()
        artifact = json.loads(artifact_row[1])
        artifact["evidence_sha256"] = evidence_hash
        point.conn.execute(
            "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? "
            "WHERE artifact_id=?",
            (ck.canonical_json(artifact), ck.sha256_json(artifact), artifact_row[0]))
    point.close()
    result = public_runtime.public_verify("public-run", benchmark_root=tmp_path / "bench")
    assert result["ok"] is False
    assert any(error.startswith("backup_anchor_invalid:") for error in result["errors"])


def _legacy_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(ck._SCHEMA)
    return conn


def test_zero_runtime_tables_migrate_once() -> None:
    conn = _legacy_connection()
    ck._ensure_runtime_schema(conn)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(ck._RUNTIME_COLUMNS) <= tables
    assert conn.execute(
        "SELECT id,active_stage,active_plan_key FROM runtime_cursor"
    ).fetchall() == [(1, "C", "C")]
    ck._ensure_runtime_schema(conn)
    assert conn.execute("SELECT count(*) FROM runtime_cursor").fetchone()[0] == 1
    conn.close()


def test_partial_runtime_schema_is_rejected_not_recreated() -> None:
    conn = _legacy_connection()
    ck._ensure_runtime_schema(conn)
    conn.execute("DROP TABLE backup_receipts")

    with pytest.raises(ck.ImmutableViolation, match="runtime schema is partial"):
        ck._ensure_runtime_schema(conn)
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' "
        "AND name='backup_receipts'").fetchone()[0] == 0
    conn.close()


def test_missing_runtime_cursor_is_rejected_not_reset() -> None:
    conn = _legacy_connection()
    ck._ensure_runtime_schema(conn)
    conn.execute("DELETE FROM runtime_cursor")

    with pytest.raises(ck.ImmutableViolation, match="cursor is not the singleton row"):
        ck._ensure_runtime_schema(conn)
    assert conn.execute("SELECT count(*) FROM runtime_cursor").fetchone()[0] == 0
    conn.close()
