"""Offline hostile-path tests for the durable C0B-2 Stage-D runtime driver."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b2_plan as legacy_plan
from scripts.analyst_benchmark import c0b2_runtime as public_runtime
from scripts.analyst_benchmark import c0b2_runtime_d as runtime_d
from scripts.analyst_benchmark.c0b2_checkpoint import (
    ImmutableViolation, canonical_json, sha256_json,
)
from scripts.analyst_benchmark.c0b2_executor import (
    SERVER_CONTROL_MODEL, ControlRequest, DurableExecutor, FakeResponse, WorkRequest,
    control_id,
)
from scripts.analyst_benchmark.c0b2_fsprobe import GlobalExecutionLock
from scripts.analyst_benchmark.c0b2_public_schema import stage_d_candidate_id
from scripts.analyst_benchmark.c0b2_stage_d_plan import (
    build_d1_plan, build_d3_plan, build_d4_plan, derive_d_context_controls,
)
from scripts.analyst_benchmark.c0b2_stage_d import d3_decision_record_sha256


def _stage_c_selection(plan_hash: str, aggregate_hash: str,
                       *, worksheet: str = "v2") -> dict[str, object]:
    models = []
    survivors = []
    for model, digest, _think in legacy_plan.MODELS:
        models.append({
            "model": model, "model_digest": digest,
            "v1_passed": worksheet == "v1", "v2_passed": worksheet == "v2",
            "selected_worksheet": worksheet, "selection_basis": "only_passer",
            "bootstrap": None,
        })
        survivors.append({
            "model": model, "model_digest": digest, "worksheet": worksheet,
            "chunk_chars": 4000, "overlap": 256,
            "num_ctx": 8192, "num_predict": 4096,
        })
    return {
        "version": "stage-c-selection-v1", "stage": "C",
        "plan_sha256": plan_hash, "aggregate_sha256": aggregate_hash,
        "models": models, "survivors": survivors,
    }


def _created_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                      run_id: str = "c0b2-runtime-d"):
    monkeypatch.setattr(public_runtime, "_require_clean_task_delta", lambda _seal: None)
    root = tmp_path / "bench"
    public_runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path(run_id, root), root)
    _parent, plan_hash, _raw = point.load_plan("C")
    aggregate_hash = sha256_json({"test": "stage-c-aggregate"})
    selection = _stage_c_selection(plan_hash, aggregate_hash)
    point.conn.execute(
        "INSERT INTO stage_aggregates VALUES(?,?,?,?,?)",
        ("C", plan_hash, aggregate_hash,
         canonical_json({"test": "stage-c-aggregate"}), time.time()),
    )
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-c-selection", "C", plan_hash, aggregate_hash, "ACTIVATED",
         canonical_json(selection), time.time()),
    )
    point.conn.execute(
        "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY' WHERE id=1")
    with GlobalExecutionLock(root) as lock:
        public_runtime.ensure_backup_receipt(point, lock)
    return root, point, runtime_d.load_stage_d_inputs(point)


def test_start_d_atomically_freezes_exact_d1_without_controls(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(monkeypatch, tmp_path)
    try:
        active = runtime_d.start_stage_d(point, inputs)
        assert active.plan["phase"] == "D1"
        assert len(active.plan["work"]) == 55
        assert active.controls == ()
        assert point.state() == "RUNNING"
        assert point.runtime_position().active_plan_key == "D1_OUTPUT"
        assert point.conn.execute(
            "SELECT count(*) FROM phase_work_registry "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0] == 55
        assert runtime_d.load_active_d_phase(point, inputs) == active
    finally:
        point.close()


def test_stage_d_activation_rolls_back_plan_registry_cursor_and_state(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-rollback")
    original = runtime_d.freeze_activate_phase_plan

    def crash_after_activation(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("fault after registry")

    monkeypatch.setattr(runtime_d, "freeze_activate_phase_plan", crash_after_activation)
    try:
        with pytest.raises(OSError, match="fault after registry"):
            runtime_d.start_stage_d(point, inputs)
        assert point.state() == "PAUSED_STAGE_BOUNDARY"
        assert point.runtime_position().active_plan_key == "C"
        for table in ("phase_plans", "plan_activations", "phase_work_registry",
                      "runtime_controls"):
            assert point.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert point.conn.execute(
            "SELECT count(*) FROM work_items WHERE stage='D'").fetchone()[0] == 0
    finally:
        point.close()


def test_resume_rejects_coherently_rehashed_plan_activation_and_registry(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-parent-tamper")
    try:
        active = runtime_d.start_stage_d(point, inputs)
        parent_hash = active.plan["parent_decision_sha256"]
        changed_candidates = []
        for row in active.plan["candidates"]:
            changed = dict(row)
            changed["worksheet"] = "v1"
            changed["candidate_id"] = stage_d_candidate_id(
                changed["model"], changed["model_digest"], "v1")
            changed_candidates.append(changed)
        changed_plan = build_d1_plan(
            parent_hash, changed_candidates, corpus=inputs.corpus,
            run_nonce_key=inputs.run_nonce_key)
        changed_hash = sha256_json(changed_plan)
        activation = json.loads(point.conn.execute(
            "SELECT activation_json FROM plan_activations "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0])
        activation["plan_sha256"] = changed_hash
        point.conn.execute("DELETE FROM phase_work_registry WHERE plan_key='D1_OUTPUT'")
        point.conn.execute("DELETE FROM work_items WHERE stage='D'")
        point.conn.execute(
            "UPDATE phase_plans SET plan_hash=?,plan_json=? WHERE plan_key='D1_OUTPUT'",
            (changed_hash, canonical_json(changed_plan)),
        )
        point.conn.execute(
            "UPDATE plan_activations SET activation_hash=?,activation_json=? "
            "WHERE plan_key='D1_OUTPUT'",
            (sha256_json(activation), canonical_json(activation)),
        )
        for item in changed_plan["work"]:
            point.conn.execute(
                "INSERT INTO work_items VALUES(?,?,?,?,'PENDING',NULL)",
                (item["work_id"], "D", item["cell_id"], item["request_sha256"]),
            )
            point.conn.execute(
                "INSERT INTO phase_work_registry VALUES(?,? ,NULL)",
                (item["work_id"], "D1_OUTPUT"),
            )
        with pytest.raises((ImmutableViolation, runtime_d.StageDRuntimeError),
                           match="typed parent|re-derived"):
            runtime_d.load_active_d_phase(point, inputs)
    finally:
        point.close()


def test_d1_rejects_coherent_parent_and_plan_rewrite_outside_c_receipt(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-c-receipt-owner")
    try:
        active = runtime_d.start_stage_d(point, inputs)
        changed_aggregate = {"test": "changed-stage-c-aggregate"}
        changed_aggregate_hash = sha256_json(changed_aggregate)
        selection = json.loads(point.conn.execute(
            "SELECT value_json FROM decisions "
            "WHERE decision_id='stage-c-selection'").fetchone()[0])
        selection["aggregate_sha256"] = changed_aggregate_hash
        point.conn.execute(
            "UPDATE stage_aggregates SET aggregate_hash=?,aggregate_json=? "
            "WHERE stage='C'",
            (changed_aggregate_hash, canonical_json(changed_aggregate)),
        )
        point.conn.execute(
            "UPDATE decisions SET aggregate_hash=?,value_json=? "
            "WHERE decision_id='stage-c-selection'",
            (changed_aggregate_hash, canonical_json(selection)),
        )
        parent_hash, _selection = runtime_d._decision_digest(
            point, "stage-c-selection")
        changed_plan = build_d1_plan(
            parent_hash, active.plan["candidates"], corpus=inputs.corpus,
            run_nonce_key=inputs.run_nonce_key)
        changed_hash = sha256_json(changed_plan)
        activation = json.loads(point.conn.execute(
            "SELECT activation_json FROM plan_activations "
            "WHERE plan_key='D1_OUTPUT'").fetchone()[0])
        activation["parent_decision_sha256"] = parent_hash
        activation["plan_sha256"] = changed_hash
        point.conn.execute("DELETE FROM phase_work_registry WHERE plan_key='D1_OUTPUT'")
        point.conn.execute("DELETE FROM work_items WHERE stage='D'")
        point.conn.execute(
            "UPDATE phase_plans SET parent_decision_sha256=?,plan_hash=?,plan_json=? "
            "WHERE plan_key='D1_OUTPUT'",
            (parent_hash, changed_hash, canonical_json(changed_plan)),
        )
        point.conn.execute(
            "UPDATE plan_activations SET activation_hash=?,activation_json=? "
            "WHERE plan_key='D1_OUTPUT'",
            (sha256_json(activation), canonical_json(activation)),
        )
        for item in changed_plan["work"]:
            point.conn.execute(
                "INSERT INTO work_items VALUES(?,?,?,?,'PENDING',NULL)",
                (item["work_id"], "D", item["cell_id"], item["request_sha256"]),
            )
            point.conn.execute(
                "INSERT INTO phase_work_registry VALUES(?,?,NULL)",
                (item["work_id"], "D1_OUTPUT"),
            )
        with pytest.raises(runtime_d.StageDRuntimeError, match="cannot be re-derived"):
            runtime_d.load_active_d_phase(point, inputs)
    finally:
        point.close()


def _d3_phase_for_barrier(point, inputs, *, model_index: int = 0):
    parent_value = {"test": "d2-parent"}
    parent_row = ("D", "7" * 64, "8" * 64, "ACTIVATED",
                  canonical_json(parent_value))
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-d-d2-selection", *parent_row, time.time()),
    )
    parent = sha256_json(("stage-d-d2-selection", *parent_row))
    model, digest, _think = legacy_plan.MODELS[model_index]
    candidate = {
        "candidate_id": stage_d_candidate_id(model, digest, "v2"),
        "model": model, "model_digest": digest, "worksheet": "v2",
        "chunk_chars": 8000, "overlap": 256,
        "num_ctx": None, "num_predict": 2048,
    }
    plan = build_d3_plan(
        parent, [candidate], corpus=inputs.corpus,
        run_nonce_key=inputs.run_nonce_key)
    controls = tuple(derive_d_context_controls(
        plan, corpus=inputs.corpus, run_nonce_key=inputs.run_nonce_key))
    point.conn.execute(
        "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
        ("D3_CONTEXT", "D", "D3", parent, sha256_json(plan),
         canonical_json(plan), time.time()),
    )
    for item in plan["work"]:
        point.conn.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,'PENDING',NULL)",
            (item["work_id"], "D", item["cell_id"], item["request_sha256"]),
        )
        point.conn.execute(
            "INSERT INTO phase_work_registry VALUES(?,?,NULL)",
            (item["work_id"], "D3_CONTEXT"),
        )
    for control in controls:
        point.conn.execute(
            "INSERT INTO runtime_controls VALUES(?,?,?,?,?,'PENDING',NULL,NULL,NULL,?)",
            (control["control_id"], "D3_CONTEXT", "context_probe",
             sha256_json(control), canonical_json(control), time.time()),
        )
        point.conn.execute(
            "INSERT INTO runtime_control_events VALUES(?,1,'PENDING',NULL,NULL,NULL,?)",
            (control["control_id"], time.time()),
        )
    return runtime_d.ActiveDPhase(plan, sha256_json(plan), controls)


def _answer_work(point, item, attempt_no: int = 1, *, response: str = "{}",
                 metadata: dict[str, object] | None = None) -> None:
    request = WorkRequest(
        "D", item["work_id"], item["model"], item["request_sha256"],
        attempt_no, "scored" if attempt_no == 1 else "schema_retry")
    exact_metadata = metadata or {
        "done_reason": "stop", "prompt_eval_count": 1,
        "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True,
    }
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request.attempt_id, item["work_id"], None, "D", 1,
         request.call_class, attempt_no, item["request_sha256"], "ACCEPTED", response,
         canonical_json(exact_metadata), time.time(), time.time()),
    )
    point.conn.execute(
        "UPDATE work_items SET state='SUCCEEDED',accepted_attempt_id=? WHERE work_id=?",
        (request.attempt_id, item["work_id"]),
    )


def _finish_d1_inconclusive(point, inputs):
    phase = runtime_d.start_stage_d(point, inputs)
    point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
    no_findings = canonical_json({
        "document_type": "unknown", "subject": "",
        "assessment": "no_findings", "findings": [],
    })
    for item in phase.plan["work"]:
        _answer_work(point, item, response=no_findings)
    result = runtime_d.finalize_d_phase(point, phase, inputs)
    assert result.outcome == "INCONCLUSIVE"
    assert point.state() == "INCONCLUSIVE"
    return phase


def test_d_phase_evidence_rejects_work_and_invocation_ownership_tamper(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-evidence-owner")
    try:
        phase = runtime_d.start_stage_d(point, inputs)
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        first = phase.plan["work"][0]
        _answer_work(point, first)
        assert len(runtime_d._phase_evidence(point, phase)[first["work_id"]]) == 1
        point.conn.execute(
            "UPDATE work_items SET accepted_attempt_id=? WHERE work_id=?",
            ("0" * 64, first["work_id"]),
        )
        with pytest.raises(ImmutableViolation, match="authoritative attempts"):
            runtime_d._phase_evidence(point, phase)
        point.conn.execute(
            "UPDATE work_items SET accepted_attempt_id=(SELECT attempt_id FROM attempts "
            "WHERE work_id=?) WHERE work_id=?",
            (first["work_id"], first["work_id"]),
        )
        point.conn.execute(
            "UPDATE attempts SET stage='C',invocation_ordinal=NULL WHERE work_id=?",
            (first["work_id"],),
        )
        with pytest.raises(ImmutableViolation, match="ownership changed"):
            runtime_d._phase_evidence(point, phase)
    finally:
        point.close()


def test_d_dispatched_crash_row_is_recovered_before_exact_evidence_validation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-dispatch-recover")
    try:
        phase = runtime_d.start_stage_d(point, inputs)
        first = phase.plan["work"][0]
        request = WorkRequest(
            "D", first["work_id"], first["model"], first["request_sha256"],
            1, "scored")
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.attempt_id, first["work_id"], None, "D", 1, "scored", 1,
             first["request_sha256"], "DISPATCHING", None, None,
             time.time(), time.time()),
        )
        point.conn.execute(
            "UPDATE work_items SET state='DISPATCHING' WHERE work_id=?",
            (first["work_id"],),
        )
        with GlobalExecutionLock(root) as lock:
            executor = DurableExecutor(
                point, lock,
                lambda _request, _cancel: (_ for _ in ()).throw(
                    AssertionError("recovery must not contact transport")))
            recovered, ordinal = executor.recover_and_start("D")
            evidence = runtime_d._work_attempt_evidence(point, first)
        assert (recovered, ordinal) == (1, 2)
        assert evidence[0]["state"] == "ORPHANED_UNKNOWN"
        assert point.work(first["work_id"]) == ("PENDING", None)
        mutations = (
            ("attempt_id", "f" * 64, request.attempt_id),
            ("request_hash", "0" * 64, first["request_sha256"]),
            ("call_class", "schema_retry", "scored"),
            ("attempt_no", 2, 1),
        )
        for column, bad, exact in mutations:
            point.conn.execute(
                f"UPDATE attempts SET {column}=? WHERE work_id=?",
                (bad, first["work_id"]),
            )
            with pytest.raises(ImmutableViolation, match="identity, sequence"):
                runtime_d._work_attempt_evidence(point, first)
            point.conn.execute(
                f"UPDATE attempts SET {column}=? WHERE work_id=?",
                (exact, first["work_id"]),
            )
    finally:
        point.close()


def test_d3_first_answer_barrier_blocks_later_work_until_control_complete(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-barrier")
    try:
        phase = _d3_phase_for_barrier(point, inputs)
        assert runtime_d._pending_context_control(point, phase) is None
        first, second = phase.plan["work"][:2]
        _answer_work(point, first)
        assert runtime_d._pending_context_control(point, phase) == phase.controls[0]
        _answer_work(point, second)
        with pytest.raises(ImmutableViolation, match="crossed a pending context barrier"):
            runtime_d._pending_context_control(point, phase)
    finally:
        point.close()


def test_d_phase_call_caps_are_exact_and_include_retries(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-call-cap")
    try:
        phase = runtime_d.start_stage_d(point, inputs)
        first = phase.plan["work"][0]
        _answer_work(point, first)
        assert runtime_d._scored_calls(point, "D1_OUTPUT") == 1
        monkeypatch.setitem(runtime_d.PHASE_CALL_CAPS, "D1_OUTPUT", 0)
        with pytest.raises(ImmutableViolation, match="scored-call maximum"):
            runtime_d.validate_phase_call_cap(point, phase)
    finally:
        point.close()


def test_d_context_completion_rejects_coherent_attempt_identity_tamper(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-control-tamper")
    try:
        phase = _d3_phase_for_barrier(point, inputs)
        first, control = phase.plan["work"][0], phase.controls[0]
        _answer_work(point, first)
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        response = {
            "purpose": control["purpose"],
            "config_sha256": control["config_sha256"],
            "model": control["model"], "digest": control["model_digest"],
            "size": 100, "size_vram": 80,
            "context_length": control["minimum_context_length"],
        }
        response_hash = sha256_json(response)
        request = ControlRequest(
            "D", control["control_id"], control["model"],
            control["payload_sha256"], 1, "preflight_probe")
        metadata = {"http_status": 200, "control": "ps",
                    "response_sha256": response_hash}
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.attempt_id, None, control["control_id"], "D", 1,
             "preflight_probe", 1, control["payload_sha256"], "ACCEPTED",
             canonical_json(response), canonical_json(metadata), time.time(), time.time()),
        )
        evidence = {
            "control_id": control["control_id"], "purpose": control["purpose"],
            "candidate_id": control["candidate_id"], "model": control["model"],
            "model_digest": control["model_digest"],
            "config_sha256": control["config_sha256"],
            "expected_num_ctx": control["minimum_context_length"],
            "observed_context_length": control["minimum_context_length"],
            "trigger_work_id": first["work_id"], "state": "PASSED",
            "response_sha256": response_hash,
        }
        point.conn.execute(
            "UPDATE runtime_controls SET state='COMPLETE',evidence_hash=?,"
            "evidence_json=? WHERE control_id=?",
            (sha256_json(evidence), canonical_json(evidence), control["control_id"]),
        )
        point.conn.execute(
            "INSERT INTO runtime_control_events VALUES(?,2,'COMPLETE',?,?,NULL,?)",
            (control["control_id"], sha256_json(evidence),
             canonical_json(evidence), time.time()),
        )
        assert runtime_d._load_exact_controls(
            point, phase.plan, inputs=inputs) == phase.controls
        point.conn.execute(
            "UPDATE attempts SET request_hash=? WHERE attempt_id=?",
            ("0" * 64, request.attempt_id),
        )
        with pytest.raises(ImmutableViolation, match="identity or sequence"):
            runtime_d._load_exact_controls(point, phase.plan, inputs=inputs)
        point.conn.execute(
            "UPDATE attempts SET request_hash=? WHERE attempt_id=?",
            (control["payload_sha256"], request.attempt_id),
        )
        changed_response = dict(response)
        changed_response["config_sha256"] = "f" * 64
        changed_response_hash = sha256_json(changed_response)
        changed_metadata = {
            "http_status": 200, "control": "ps",
            "response_sha256": changed_response_hash,
        }
        changed_evidence = dict(evidence)
        changed_evidence["response_sha256"] = changed_response_hash
        point.conn.execute(
            "UPDATE attempts SET response=?,metadata_json=? WHERE attempt_id=?",
            (canonical_json(changed_response), canonical_json(changed_metadata),
             request.attempt_id),
        )
        point.conn.execute(
            "UPDATE runtime_controls SET evidence_hash=?,evidence_json=? "
            "WHERE control_id=?",
            (sha256_json(changed_evidence), canonical_json(changed_evidence),
             control["control_id"]),
        )
        point.conn.execute(
            "UPDATE runtime_control_events SET evidence_hash=?,evidence_json=? "
            "WHERE control_id=? AND seq=2",
            (sha256_json(changed_evidence), canonical_json(changed_evidence),
             control["control_id"]),
        )
        with pytest.raises(ImmutableViolation, match="differs from its response"):
            runtime_d._load_exact_controls(point, phase.plan, inputs=inputs)
    finally:
        point.close()


def test_pending_d_context_attempt_history_is_exact_before_contact(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-pending-context-history")
    try:
        phase = _d3_phase_for_barrier(point, inputs)
        control = phase.controls[0]
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        request = ControlRequest(
            "D", control["control_id"], control["model"],
            control["payload_sha256"], 1, "preflight_probe")
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.attempt_id, None, control["control_id"], "D", 1,
             "preflight_probe", 1, control["payload_sha256"],
             "ORPHANED_UNKNOWN", None, None, time.time(), time.time()),
        )
        assert runtime_d._load_exact_controls(
            point, phase.plan, inputs=inputs) == phase.controls
        point.conn.execute(
            "UPDATE attempts SET request_hash=? WHERE attempt_id=?",
            ("0" * 64, request.attempt_id),
        )
        with pytest.raises(ImmutableViolation, match="identity or sequence"):
            runtime_d._load_exact_controls(point, phase.plan, inputs=inputs)
    finally:
        point.close()


def test_historical_d_control_charge_is_rederived_with_exact_ownership(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-control-history")
    try:
        phase = runtime_d.start_stage_d(point, inputs)
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        kind, model, spec = runtime_d._preflight_specs(point.header(), phase)[0]
        identity = control_id("D", 1, kind, model)
        request_hash = runtime_d.request_spec_hash(spec)
        request = ControlRequest(
            "D", identity, model, request_hash, 1, "preflight_probe")
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.attempt_id, None, identity, "D", 1, "preflight_probe", 1,
             request_hash, "ACCEPTED", "{}", "{}", time.time(), time.time()),
        )
        runtime_d._validate_d_control_history(point, inputs)
        point.conn.execute(
            "UPDATE attempts SET stage='C' WHERE attempt_id=?",
            (request.attempt_id,),
        )
        with pytest.raises(ImmutableViolation, match="exact Stage-C receipt"):
            runtime_d._validate_d_control_history(point, inputs)
        point.conn.execute(
            "UPDATE attempts SET stage='D',control_id=? WHERE attempt_id=?",
            ("f" * 64, request.attempt_id),
        )
        with pytest.raises(ImmutableViolation, match="no phase-owned request"):
            runtime_d._validate_d_control_history(point, inputs)
    finally:
        point.close()


@pytest.mark.parametrize("charge_kind", ["context", "resource"])
def test_d3_control_cannot_be_backdated_into_an_earlier_invocation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        charge_kind: str) -> None:
    """A later phase cannot lend either control identity to ordinal one."""
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, f"c0b2-runtime-d-backdated-{charge_kind}")
    model, digest, _think = legacy_plan.MODELS[0]
    context = {
        "control_id": "c" * 64, "payload_sha256": "d" * 64,
        "model": model,
    }

    monkeypatch.setattr(
        runtime_d, "_stage_c_boundary_parent",
        lambda *_args, **_kwargs: "e" * 64)

    def exact_phase(_point, key, _inputs):
        plan_hash, raw = point.conn.execute(
            "SELECT plan_hash,plan_json FROM phase_plans WHERE plan_key=?",
            (key,),
        ).fetchone()
        plan = json.loads(raw)
        controls = (context,) if plan["phase"] == "D3" else ()
        return runtime_d.ActiveDPhase(plan, plan_hash, controls)

    monkeypatch.setattr(runtime_d, "_load_exact_phase_by_key", exact_phase)
    monkeypatch.setattr(
        runtime_d, "_resource_probe_spec",
        lambda _point, candidate: runtime_d.RequestSpec(
            kind="chat", payload={"candidate": candidate}))
    trigger_spec = runtime_d.RequestSpec(
        kind="chat", payload={"control": "trigger"})
    monkeypatch.setattr(
        runtime_d, "_trigger_resource_probe_spec",
        lambda *_args: trigger_spec)

    try:
        for index, (key, phase, activated) in enumerate((
                ("D1_OUTPUT", "D1", 10.0),
                ("D2_CHUNK", "D2", 25.0),
                ("D3_CONTEXT", "D3", 40.0)), 1):
            plan = {
                "plan_key": key, "phase": phase,
                "candidates": [{"model": model, "model_digest": digest}],
                "work": [],
            }
            plan_hash = sha256_json(plan)
            activation = {"plan_key": key, "sequence": index}
            point.conn.execute(
                "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
                (key, "D", phase, str(index) * 64, plan_hash,
                 canonical_json(plan), activated),
            )
            point.conn.execute(
                "INSERT INTO plan_activations VALUES(?,?,?,?)",
                (key, sha256_json(activation), canonical_json(activation),
                 activated),
            )
        point.conn.executemany(
            "INSERT INTO invocations VALUES('D',?,?)", ((1, 20.0), (2, 30.0)))

        if charge_kind == "context":
            identity = context["control_id"]
            request_hash = context["payload_sha256"]
            call_class = "preflight_probe"
        else:
            request_hash = runtime_d.request_spec_hash(trigger_spec)
            identity = runtime_d.resource_probe_id("D", 1, model, request_hash)
            call_class = "transport_orphan"
        attempt_id = runtime_d.stable_attempt_id(f"control:{identity}", 1)
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, None, identity, "D", 1, call_class, 1, request_hash,
             "ORPHANED_UNKNOWN", None, None, 41.0, 41.0),
        )

        with pytest.raises(ImmutableViolation, match="outside its invocation window"):
            runtime_d._validate_d_control_history(point, inputs)
    finally:
        point.close()


def test_historical_control_rejects_an_underived_future_phase(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A validly typed D2 row cannot become an owner without its D1 decision."""
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-underived-future")
    try:
        d1 = runtime_d.start_stage_d(point, inputs)
        invoked = time.time() + 1
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (invoked,))
        candidates = [
            {**dict(row), "num_predict": 2048}
            for row in d1.plan["candidates"]
        ]
        d2 = runtime_d.build_d2_plan(
            "f" * 64, candidates, corpus=inputs.corpus,
            run_nonce_key=inputs.run_nonce_key)
        activated = invoked + 1
        point.conn.execute(
            "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
            ("D2_CHUNK", "D", "D2", "f" * 64, sha256_json(d2),
             canonical_json(d2), activated),
        )
        point.conn.execute(
            "INSERT INTO plan_activations VALUES(?,?,?,?)",
            ("D2_CHUNK", "e" * 64, "{}", activated),
        )
        model = candidates[0]["model"]
        spec = runtime_d._resource_probe_spec(point, model)
        request_hash = runtime_d.request_spec_hash(spec)
        identity = runtime_d.resource_probe_id("D", 1, model, request_hash)
        attempt_id = runtime_d.stable_attempt_id(f"control:{identity}", 1)
        point.conn.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, None, identity, "D", 1, "transport_orphan", 1,
             request_hash, "RETRYABLE_TRANSPORT", None, "{}", activated + 1,
             activated + 1),
        )

        with pytest.raises(
                (ImmutableViolation, runtime_d.StageDRuntimeError),
                match="re-derived|aggregate|parent decision"):
            runtime_d._validate_d_control_history(point, inputs)
    finally:
        point.close()


def _seed_resource_retry_history(
        monkeypatch: pytest.MonkeyPatch, point, *, include_d2: bool,
) -> tuple[str, str]:
    """Create exact-loader seams for ownership-only historical tests."""
    model, digest, _think = legacy_plan.MODELS[0]
    activated = [("D1_OUTPUT", "D1", 10.0)]
    if include_d2:
        activated.append(("D2_CHUNK", "D2", 25.0))
    phases = {}
    for index, (key, phase_name, created) in enumerate(activated, 1):
        plan = {
            "plan_key": key, "phase": phase_name,
            "candidates": [{"model": model, "model_digest": digest}],
            "work": [],
        }
        plan_hash = sha256_json(plan)
        activation = {"plan_key": key, "sequence": index}
        point.conn.execute(
            "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
            (key, "D", phase_name, str(index) * 64, plan_hash,
             canonical_json(plan), created),
        )
        point.conn.execute(
            "INSERT INTO plan_activations VALUES(?,?,?,?)",
            (key, sha256_json(activation), canonical_json(activation), created),
        )
        phases[key] = runtime_d.ActiveDPhase(plan, plan_hash, ())
    monkeypatch.setattr(
        runtime_d, "_stage_c_boundary_parent",
        lambda *_args, **_kwargs: "e" * 64)
    monkeypatch.setattr(
        runtime_d, "_load_exact_phase_by_key",
        lambda _point, key, _inputs: phases[key])
    monkeypatch.setattr(
        runtime_d, "_resource_probe_spec",
        lambda _point, candidate: runtime_d.RequestSpec(
            kind="chat", payload={"candidate": candidate}))
    point.conn.execute("INSERT INTO invocations VALUES('D',1,15.0)")
    request_hash = runtime_d.request_spec_hash(
        runtime_d._resource_probe_spec(point, model))
    return model, request_hash


def _insert_resource_retry_attempt(
        point, identity: str, attempt_no: int, state: str,
        request_hash: str, created: float) -> None:
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (runtime_d.stable_attempt_id(f"control:{identity}", attempt_no), None,
         identity, "D", 1, "transport_orphan", attempt_no, request_hash, state,
         None, None, created, created),
    )


def test_generic_resource_retry_group_cannot_straddle_phase_activation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-resource-cross-phase")
    try:
        model, request_hash = _seed_resource_retry_history(
            monkeypatch, point, include_d2=True)
        identity = runtime_d.resource_probe_id("D", 1, model, request_hash)
        _insert_resource_retry_attempt(
            point, identity, 1, "RETRYABLE_TRANSPORT", request_hash, 20.0)
        _insert_resource_retry_attempt(
            point, identity, 2, "ORPHANED_UNKNOWN", request_hash, 30.0)

        with pytest.raises(ImmutableViolation, match="changed phase ownership"):
            runtime_d._validate_d_control_history(point, inputs)
    finally:
        point.close()


def test_generic_resource_retry_group_remains_valid_within_one_phase(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-resource-same-phase")
    try:
        model, request_hash = _seed_resource_retry_history(
            monkeypatch, point, include_d2=False)
        identity = runtime_d.resource_probe_id("D", 1, model, request_hash)
        _insert_resource_retry_attempt(
            point, identity, 1, "RETRYABLE_TRANSPORT", request_hash, 20.0)
        _insert_resource_retry_attempt(
            point, identity, 2, "ORPHANED_UNKNOWN", request_hash, 21.0)

        runtime_d._validate_d_control_history(point, inputs)
    finally:
        point.close()


def test_d4_parent_is_exact_d3_checkpoint_row_digest(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-row-digest")
    try:
        model, digest, _think = legacy_plan.MODELS[0]
        selection = {
            "candidate_id": stage_d_candidate_id(model, digest, "v2"),
            "model": model, "model_digest": digest, "worksheet": "v2",
            "chunk_chars": 8000, "overlap": 256,
            "num_ctx": 8192, "num_predict": 2048,
        }
        decision = {
            "version": "stage-d-decision-v1", "stage": "D", "phase": "D3",
            "plan_sha256": "3" * 64, "aggregate_sha256": "4" * 64,
            "outcome": "CONTINUE", "reason": "phase_passed",
            "selections": [selection],
        }
        point.conn.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
            ("stage-d-d3-selection", "D", decision["plan_sha256"],
             decision["aggregate_sha256"], "ACTIVATED",
             canonical_json(decision), time.time()),
        )
        db_digest, _stored = runtime_d._decision_digest(
            point, "stage-d-d3-selection")
        assert db_digest == d3_decision_record_sha256(decision)
        assert db_digest != sha256_json(decision)
        d4 = build_d4_plan(
            db_digest, [selection], corpus=inputs.corpus,
            run_nonce_key=inputs.run_nonce_key)
        assert d4["parent_decision_sha256"] == db_digest
    finally:
        point.close()


def test_d_context_probe_uses_fake_transport_and_completes_durable_barrier(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-fake-context")
    try:
        phase = _d3_phase_for_barrier(point, inputs)
        first, control = phase.plan["work"][0], phase.controls[0]
        _answer_work(point, first)
        point.conn.execute(
            "UPDATE runtime_cursor SET active_stage='D',active_plan_key='D3_CONTEXT' "
            "WHERE id=1")
        point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        unrelated = next(model for model, _digest, _think in legacy_plan.MODELS
                         if model != control["model"])
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (unrelated, time.time()),
        )
        preflights = [
            ("version", SERVER_CONTROL_MODEL), ("tags", SERVER_CONTROL_MODEL),
            ("show", control["model"]),
        ]
        for kind, model in preflights:
            identity = control_id("D", 1, kind, model)
            request = ControlRequest("D", identity, model, "a" * 64, 1)
            point.conn.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request.attempt_id, None, identity, "D", 1, "preflight_probe", 1,
                 "a" * 64, "ACCEPTED", "{}", "{}", time.time(), time.time()),
            )
        response = {
            "purpose": control["purpose"],
            "config_sha256": control["config_sha256"],
            "model": control["model"], "digest": control["model_digest"],
            "size": 100, "size_vram": 80,
            "context_length": control["minimum_context_length"],
        }
        calls = []

        def fake_transport(request, _cancel):
            calls.append(request.control_id)
            return FakeResponse(
                canonical_json(response),
                {"http_status": 200, "control": "ps",
                 "response_sha256": sha256_json(response)})

        with GlobalExecutionLock(root) as lock:
            executor = DurableExecutor(point, lock, fake_transport)
            executor.invocation_stage, executor.invocation_ordinal = "D", 1
            result = runtime_d.run_d_context_probe(executor, phase, control)
        assert result.outcome == "ACCEPTED"
        assert calls == [control["control_id"]]
        assert point.conn.execute(
            "SELECT state FROM runtime_controls WHERE control_id=?",
            (control["control_id"],)).fetchone() == ("COMPLETE",)
        assert runtime_d._pending_context_control(point, phase) is None
        assert point.backoff(unrelated).failures == 6
    finally:
        point.close()


def test_context_recovery_replays_trigger_then_observes_ps_before_scored_work(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class StopAfterOrderingProbe(RuntimeError):
        pass

    root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-context-recovery-order")
    try:
        # Use the lexically later model so an unrelated global resource
        # obligation would win unless the pending context is prioritized.
        phase = _d3_phase_for_barrier(point, inputs, model_index=1)
        first, control = phase.plan["work"][0], phase.controls[0]
        point.conn.execute("INSERT INTO invocations VALUES('D',1,?)", (time.time(),))
        _answer_work(point, first)
        point.conn.execute(
            "UPDATE runtime_cursor SET active_stage='D',active_plan_key='D3_CONTEXT' "
            "WHERE id=1")
        point.conn.execute("UPDATE run_state SET state='PAUSED_RESOURCE' WHERE id=1")
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (control["model"], time.time()),
        )
        unrelated = min(model for model, _digest, _think in legacy_plan.MODELS
                        if model != control["model"])
        assert unrelated < control["model"]
        point.conn.execute(
            "INSERT INTO model_backoff VALUES(?,6,0,?)",
            (unrelated, time.time()),
        )
        monkeypatch.setattr(runtime_d, "load_active_d_phase", lambda *_args: phase)
        monkeypatch.setattr(runtime_d, "_validate_d_control_history", lambda *_args: None)
        actions = []
        no_findings = canonical_json({
            "document_type": "unknown", "subject": "",
            "assessment": "no_findings", "findings": [],
        })

        def factory(resolver, _header):
            def transport(request, _cancel):
                spec = resolver(request)
                if spec.kind in {"version", "tags", "show"}:
                    return FakeResponse("{}")
                if spec.kind == "ps":
                    actions.append(("ps", request.control_id))
                    response = {
                        "purpose": control["purpose"],
                        "config_sha256": control["config_sha256"],
                        "model": control["model"],
                        "digest": control["model_digest"],
                        "size": 100, "size_vram": 80,
                        "context_length": control["minimum_context_length"],
                    }
                    return FakeResponse(canonical_json(response), {
                        "http_status": 200, "control": "ps",
                        "response_sha256": sha256_json(response),
                    })
                if isinstance(request, ControlRequest):
                    actions.append(("recovery", request.control_id,
                                    request.request_hash))
                    return FakeResponse(no_findings, {
                        "done_reason": "stop", "prompt_eval_count": 1,
                        "tools_empty": True, "images_empty": True,
                        "unknown_message_fields_empty": True,
                    })
                actions.append(("scored", request.work_id))
                raise StopAfterOrderingProbe
            return transport

        with GlobalExecutionLock(root) as lock:
            with pytest.raises(StopAfterOrderingProbe):
                runtime_d.run_stage_d_invocation(
                    point, lock, inputs, transport_factory=factory)
        assert [row[0] for row in actions] == [
            "recovery", "ps", "recovery", "scored"]
        assert actions[0][2] == first["request_sha256"]
        assert actions[0][1] != first["work_id"]
        assert actions[1][1] == control["control_id"]
        assert actions[2][2] != first["request_sha256"]
    finally:
        point.close()


def test_run_public_stage_d_rejects_resume_at_c_boundary_without_contact(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point, _inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-entry-gate")
    point.close()
    contacted = False

    def transport_factory(_resolver, _header):
        nonlocal contacted
        contacted = True
        raise AssertionError("must not construct transport")

    with pytest.raises(runtime_d.StageDRuntimeError, match="run D requires"):
        runtime_d.run_public_stage_d(
            "c0b2-runtime-d-entry-gate", resume=True,
            benchmark_root=root, transport_factory=transport_factory)
    assert contacted is False


def test_d3_all_reuse_rejects_any_preexisting_d4_branch(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point, inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-stale-d4")
    try:
        phase = _d3_phase_for_barrier(point, inputs)
        candidate = dict(phase.plan["candidates"][0])
        candidate["num_ctx"] = 8192
        d4 = build_d4_plan(
            "6" * 64, [candidate], corpus=inputs.corpus,
            run_nonce_key=inputs.run_nonce_key)
        point.conn.execute(
            "INSERT INTO phase_plans VALUES(?,?,?,?,?,?,?)",
            ("D4_CONFIRMATION", "D", "D4", "6" * 64, sha256_json(d4),
             canonical_json(d4), time.time()),
        )
        with pytest.raises(ImmutableViolation, match="cannot coexist"):
            runtime_d._require_no_d4_branch(point)
    finally:
        point.close()


def test_stage_d_terminal_reentry_rejects_active_c_before_receipt_or_contact(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point, _inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-terminal-cross")
    point.conn.execute(
        "UPDATE run_state SET state='BLOCKED_PROVENANCE' WHERE id=1")
    before = point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0]
    point.close()
    contacted = False

    def transport_factory(_resolver, _header):
        nonlocal contacted
        contacted = True
        raise AssertionError("must not construct transport")

    with pytest.raises(runtime_d.StageDRuntimeError, match="differs"):
        runtime_d.run_public_stage_d(
            "c0b2-runtime-d-terminal-cross", benchmark_root=root,
            transport_factory=transport_factory)
    reopened = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path("c0b2-runtime-d-terminal-cross", root), root)
    try:
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == before
        assert contacted is False
    finally:
        reopened.close()


def test_run_d_source_pin_drift_freezes_blocked_receipt_without_transport(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point, _inputs = _created_boundary(
        monkeypatch, tmp_path, "c0b2-runtime-d-source-drift")
    point.close()
    monkeypatch.setattr(
        public_runtime, "revalidate_source_pins",
        lambda _header: (_ for _ in ()).throw(
            public_runtime.RuntimeGateError("source drift")))
    contacted = False

    def transport_factory(_resolver, _header):
        nonlocal contacted
        contacted = True

    with pytest.raises(public_runtime.RuntimeGateError, match="source drift"):
        runtime_d.run_public_stage_d(
            "c0b2-runtime-d-source-drift", benchmark_root=root,
            transport_factory=transport_factory)
    reopened = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path("c0b2-runtime-d-source-drift", root), root)
    try:
        assert reopened.state() == "BLOCKED_PROVENANCE"
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == 2
        assert contacted is False
    finally:
        reopened.close()


def test_exact_d_blocked_terminal_is_receiptable_after_source_and_key_drift(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-runtime-d-blocked-crash-window"
    root, point, inputs = _created_boundary(monkeypatch, tmp_path, run_id)
    runtime_d.start_stage_d(point, inputs)
    public_runtime.finish_public_run_failure(
        point, terminal="BLOCKED_PROVENANCE")
    malformed_key = {"version": "c0b2-run-nonce-key-v1", "key_hex": "0" * 63}
    point.conn.execute(
        "UPDATE manifests SET manifest_hash=?,manifest_json=? "
        "WHERE name='run_nonce_key'",
        (sha256_json(malformed_key), canonical_json(malformed_key)),
    )
    point.close()
    monkeypatch.setattr(
        public_runtime, "revalidate_source_pins",
        lambda _header: (_ for _ in ()).throw(
            public_runtime.RuntimeGateError("source drift")))
    contacted = False

    def transport_factory(_resolver, _header):
        nonlocal contacted
        contacted = True
        raise AssertionError("terminal re-entry must not construct transport")

    result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=root, transport_factory=transport_factory)
    assert result["state"] == "BLOCKED_PROVENANCE"
    reopened = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path(run_id, root), root)
    try:
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == 2
        assert contacted is False
    finally:
        reopened.close()


def test_d_inconclusive_crash_window_rebuilds_terminal_before_receipt(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-runtime-d-inconclusive-crash-window"
    root, point, inputs = _created_boundary(monkeypatch, tmp_path, run_id)
    _finish_d1_inconclusive(point, inputs)
    assert point.conn.execute(
        "SELECT count(*) FROM backup_receipts").fetchone()[0] == 1
    point.close()
    contacted = False

    def transport_factory(_resolver, _header):
        nonlocal contacted
        contacted = True
        raise AssertionError("terminal re-entry must not construct transport")

    result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=root, transport_factory=transport_factory)
    assert result["state"] == "INCONCLUSIVE"
    reopened = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path(run_id, root), root)
    try:
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == 2
        completion = json.loads(reopened.conn.execute(
            "SELECT value_json FROM decisions "
            "WHERE decision_id='c0b2-completion'").fetchone()[0])
        completion["facts"]["reason"] = "no_d2_chunk_survivor"
        reopened.conn.execute(
            "UPDATE decisions SET value_json=? WHERE decision_id='c0b2-completion'",
            (canonical_json(completion),),
        )
    finally:
        reopened.close()
    with pytest.raises(ImmutableViolation, match="completion changed"):
        runtime_d.run_public_stage_d(
            run_id, benchmark_root=root, transport_factory=transport_factory)
    assert contacted is False


def test_missing_d_usage_blocks_before_the_next_scored_contact(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "c0b2-runtime-d-missing-usage"
    root, point, _inputs = _created_boundary(monkeypatch, tmp_path, run_id)
    point.close()
    chat_calls = 0
    no_findings = canonical_json({
        "document_type": "unknown", "subject": "",
        "assessment": "no_findings", "findings": [],
    })

    def transport_factory(resolver, _header):
        def transport(request, _cancel):
            nonlocal chat_calls
            spec = resolver(request)
            if spec.kind != "chat":
                return FakeResponse("{}")
            chat_calls += 1
            return FakeResponse(no_findings, {
                "done_reason": "stop", "tools_empty": True,
                "images_empty": True, "unknown_message_fields_empty": True,
            })
        return transport

    with pytest.raises(ImmutableViolation, match="incomplete or mistyped"):
        runtime_d.run_public_stage_d(
            run_id, benchmark_root=root, transport_factory=transport_factory)
    reopened = public_runtime.Checkpoint.open(
        public_runtime._checkpoint_path(run_id, root), root)
    try:
        assert reopened.state() == "BLOCKED_PROVENANCE"
        assert chat_calls == 1
        assert reopened.conn.execute(
            "SELECT count(*) FROM attempts WHERE stage='D' AND call_class='scored'"
        ).fetchone()[0] == 1
    finally:
        reopened.close()
