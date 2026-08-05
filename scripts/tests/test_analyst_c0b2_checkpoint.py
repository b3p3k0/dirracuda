"""Offline proofs for the C0B-2A durable benchmark foundation."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import sqlite3
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b2_checkpoint as ck
from scripts.analyst_benchmark import c0b2_executor as ex
from scripts.analyst_benchmark import c0b2_fsprobe as fs
from scripts.analyst_benchmark import c0b2_plan as plan
from scripts.analyst_benchmark import c0b2_stage_c as stage_c


def _limits(*, scored: int = 3, schema: int = 2,
            control: int = 3, transport: int = 4):
    return {"C": {"scored": scored, "schema_retry": schema,
                  "preflight_probe": control, "transport_orphan": transport}}


def _plan(*work_ids: str, request_hash: str = "r") -> dict:
    return {"work": [{"work_id": work_id, "cell_id": "cell",
                       "request_sha256": request_hash, "model": "model",
                       "model_digest": "a" * 64} for work_id in work_ids]}


def _attempt(work_id: str, attempt_no: int) -> str:
    return plan.attempt_id(work_id, attempt_no)


def _control_attempt(control_id: str, attempt_no: int) -> str:
    return plan.attempt_id(f"control:{control_id}", attempt_no)


def _header(root: Path, *, probe: bool = False,
            run_type: str = "public") -> dict:
    digest = "a" * 64
    if probe:
        result = fs.probe_filesystem(root)
        mount = asdict(result.fingerprint)
        capability, selected = result.capability_sha256, result.selected_mode
    else:
        mount = {
            "canonical_path": str(root.resolve()), "mount_id": "1", "mountpoint": "/",
            "fs_type": "test", "options": "rw", "st_dev": 1, "kernel": "test",
            "mergerfs_version": "test", "sqlite_version": sqlite3.sqlite_version,
        }
        mount["sha256"] = hashlib.sha256(json.dumps(
            mount, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        capability, selected = digest, "DELETE"
    header = {
        "run_type": run_type, "ollama_endpoint": "http://127.0.0.1:11434",
        "ollama_version": "0.32.5", "filesystem_selected_mode": selected,
        "protocol_sha256": digest, "git_head": "b" * 40,
        "declared_dirty_state_sha256": digest, "task_tree_sha256": digest,
        "fixture_sha256": digest, "master_manifest_sha256": digest,
        "schema_sha256": digest, "prompt_sha256": digest,
        "chunker_sha256": digest, "detector_sha256": digest,
        "generation_options_sha256": digest, "worktree_seal_sha256": digest,
        "filesystem_capability_sha256": capability,
        "model_digests": {"model": digest},
        "mount": mount,
    }
    if run_type == "private":
        header["parent_selection_sha256"] = digest
    return header


def _checkpoint(tmp_path: Path, run_id: str = "run-a", **limit_kwargs) -> ck.Checkpoint:
    limits = _limits(**limit_kwargs)
    cap = sum(limits["C"].values())
    root = tmp_path / "bench"
    point = ck.Checkpoint.create(
        root, run_id, header=_header(root, probe=True), limits=limits,
        cumulative_cap=cap, journal_mode="DELETE")
    point.freeze_plan("C", "root", _plan("w1", "w2", "w3"))
    return point


def _work(point: ck.Checkpoint, work_id: str, request_hash: str = "r") -> None:
    point.register_work(work_id, "C", "cell", request_hash)


def _start(point: ck.Checkpoint) -> None:
    point.transition("RUNNING")


def _complete_work(point: ck.Checkpoint, stage: str, work_id: str,
                   *, request_hash: str = "r") -> None:
    point.register_work(work_id, stage, "cell", request_hash)
    if point.state() != "RUNNING":
        point.transition("RUNNING")
    point.precharge(
        attempt_id=_attempt(work_id, 1), stage=stage, call_class="scored",
        request_hash=request_hash, attempt_no=1, work_id=work_id)
    point.finish_attempt(
        _attempt(work_id, 1), outcome="ACCEPTED", response="ok",
        metadata={}, accept_work=True)


def _pause_model_for_resource_probe(point: ck.Checkpoint, root: Path,
                                    now: list[float]) -> None:
    _work(point, "w1")

    def fail(_request, _cancel):
        raise ex.RetryableTransport()

    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(point, lock, fail, now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        for attempt in range(1, 7):
            result = executor.run(ex.WorkRequest(
                "C", "w1", "model", "r", attempt,
                "scored" if attempt == 1 else "transport_orphan"))
            now[0] = result.retry_not_before
    assert point.state() == "PAUSED_RESOURCE"


def _standard_preflight(executor: ex.DurableExecutor, stage: str,
                        ordinal: int) -> None:
    controls = (("version", ex.SERVER_CONTROL_MODEL),
                ("tags", ex.SERVER_CONTROL_MODEL),
                *(("show", model) for model in sorted(executor._stage_models(stage))))
    for kind, model in controls:
        request = ex.ControlRequest(
            stage, ex.control_id(stage, ordinal, kind, model),
            model, f"{stage}-{ordinal}-{kind}-{model}", 1)
        assert executor.run_control(request, kind=kind).outcome == "ACCEPTED"


def _seed_preflight(point: ck.Checkpoint, stage: str, ordinal: int) -> None:
    items = json.loads(point.load_plan(stage)[2])["work"]
    models = sorted({item["model"] for item in items})
    controls = (("version", ex.SERVER_CONTROL_MODEL),
                ("tags", ex.SERVER_CONTROL_MODEL),
                *(("show", model) for model in models))
    for kind, model in controls:
        identity = ex.control_id(stage, ordinal, kind, model)
        attempt = _control_attempt(identity, 1)
        point.precharge(
            attempt_id=attempt, stage=stage, call_class="preflight_probe",
            request_hash=f"{stage}-{ordinal}-{kind}-{model}", attempt_no=1,
            control_id=identity, invocation_ordinal=ordinal)
        point.finish_attempt(
            attempt, outcome="ACCEPTED", response="ok", metadata={}, accept_work=False)


def test_create_permissions_pragmas_and_header(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        assert stat.S_IMODE(point.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(point.path.parent.stat().st_mode) == 0o700
        assert point.conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert point.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert point.conn.execute("PRAGMA mmap_size").fetchone()[0] == 0
        header = point.header()
        assert header["schema_version"] == 2
        assert header["journal_mode"] == "DELETE"
    finally:
        point.close()


@pytest.mark.parametrize("mutation", ("missing", "extra", "coerced"))
def test_header_pins_are_complete_strict_and_exact(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "bench"
    header = json.loads(json.dumps(_header(root)))
    if mutation == "missing":
        del header["protocol_sha256"]
    elif mutation == "extra":
        header["unreviewed"] = "value"
    else:
        header["mount"]["st_dev"] = "1"
    with pytest.raises(ValueError):
        ck.Checkpoint.create(
            root, mutation, header=header, limits=_limits(),
            cumulative_cap=11)
    assert not (tmp_path / "bench").exists()


def test_header_mount_path_must_match_the_real_benchmark_root(tmp_path: Path) -> None:
    root = tmp_path / "actual"
    with pytest.raises(ck.ImmutableViolation, match="filesystem pins"):
        ck.Checkpoint.create(
            root, "wrong-root", header=_header(tmp_path / "wrong"),
            limits=_limits(), cumulative_cap=11)
    assert not root.exists()


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        ck.canonical_json({"bad": math.nan})


@pytest.mark.parametrize("cap", [True, -1, 1.5])
def test_create_rejects_bad_caps_before_stranding_a_run(tmp_path: Path, cap) -> None:
    root = tmp_path / "bench"
    with pytest.raises(ValueError):
        ck.Checkpoint.create(root, "bad", header={}, limits=_limits(), cumulative_cap=cap)
    assert not root.exists()
    with pytest.raises(ValueError):
        ck.Checkpoint.create(
            root, "bad", header={}, limits={"C": {"scored": True}}, cumulative_cap=1)
    assert not root.exists()


def test_open_rejects_file_outside_canonical_run_tree(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    (root / "runs").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    db = other / "checkpoint.sqlite3"
    sqlite3.connect(db).close()
    with pytest.raises(PermissionError):
        ck.Checkpoint.open(db, root)


def test_plan_decision_and_work_identity_are_immutable(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        digest = point.freeze_plan("C", "root", _plan("w1", "w2", "w3"))
        assert len(digest) == 64
        with pytest.raises(ck.ImmutableViolation):
            point.freeze_plan("C", "root", _plan("changed"))
        _work(point, "w1")
        with pytest.raises(ck.ImmutableViolation):
            point.register_work("w1", "C", "cell", "different")
        with pytest.raises(ck.ImmutableViolation):
            point.register_work("w2", "C", "different-cell", "r")
        for work_id in ("w1", "w2", "w3"):
            if work_id != "w1":
                _work(point, work_id)
        _start(point)
        for work_id in ("w1", "w2", "w3"):
            point.precharge(
                attempt_id=_attempt(work_id, 1), stage="C", call_class="scored",
                request_hash="r", attempt_no=1, work_id=work_id)
            point.finish_attempt(
                _attempt(work_id, 1), outcome="ACCEPTED", response="ok",
                metadata={}, accept_work=True)
        point.freeze_decision("worksheet", "C", digest, "agg", "ACTIVATED", {"v": 2})
        point.freeze_decision("worksheet", "C", digest, "agg", "ACTIVATED", {"v": 2})
        with pytest.raises(ck.ImmutableViolation):
            point.freeze_decision("worksheet", "C", digest, "agg", "ACTIVATED", {"v": 1})
    finally:
        point.close()


def test_adaptive_decision_hash_chains_the_next_plan_across_reopen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    limits = {"C": {"scored": 1}, "D": {"scored": 0}}
    root = tmp_path / "bench"
    point = ck.Checkpoint.create(
        root, "adaptive", header=_header(root), limits=limits,
        cumulative_cap=1)
    c_hash = point.freeze_plan("C", "master", _plan("w1"))
    _complete_work(point, "C", "w1")
    decision_hash = point.freeze_decision(
        "worksheet", "C", c_hash, "c-aggregate", "ACTIVATED", {"worksheet": "v2"})
    d_hash = point.freeze_plan("D", decision_hash, _plan())
    path, root = point.path, point.root
    point.close()
    reopened = ck.Checkpoint.open(path, root)
    try:
        monkeypatch.setattr(
            reopened, "freeze_decision",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("resume must not recompute")))
        assert reopened.load_decision("worksheet") == (
            decision_hash, {"worksheet": "v2"})
        assert reopened.freeze_plan("D", decision_hash, _plan()) == d_hash
    finally:
        reopened.close()


def test_plan_and_allowance_table_tampering_is_detected(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        point.conn.execute(
            "UPDATE plans SET plan_json=? WHERE stage='C'",
            (ck.canonical_json(_plan("w1")),))
        with pytest.raises(ck.ImmutableViolation, match="plan hash mismatch"):
            point.register_work("w1", "C", "cell", "r")
        point.conn.execute(
            "UPDATE plans SET plan_json=? WHERE stage='C'",
            (ck.canonical_json(_plan("w1", "w2", "w3")),))
        _work(point, "w1")
        point.transition("RUNNING")
        point.conn.execute(
            "UPDATE class_limits SET allowance=99 WHERE stage='C' AND call_class='scored'")
        with pytest.raises(ck.ImmutableViolation, match="allowances"):
            point.precharge(
                attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                request_hash="r", attempt_no=1, work_id="w1")
    finally:
        point.close()


def test_state_table_rejects_illegal_and_terminal_transitions(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        with pytest.raises(ck.CheckpointError):
            point.transition("SELECTED")
        point.transition("RUNNING")
        with pytest.raises(ck.CheckpointError, match="artifact finalization"):
            point.transition("INCONCLUSIVE")
        with pytest.raises(ck.CheckpointError, match="artifact finalization"):
            point.transition("FAIL_OPERATIONAL")
        point.transition("BLOCKED_PROVENANCE")
        with pytest.raises(ck.CheckpointError):
            point.transition("RUNNING")
        with pytest.raises(ck.CheckpointError):
            point.cancel()
    finally:
        point.close()


def test_invocation_ordinals_are_atomic_persistent_and_capped(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        with pytest.raises(ck.CheckpointError):
            point.claim_invocation("C")
        point.transition("RUNNING")
        calls = []
        def cancel_second_guard() -> None:
            calls.append(None)
            if len(calls) == 2: raise ex.InvocationCancelled
        with pytest.raises(ex.InvocationCancelled):
            point.claim_invocation("C", claim_guard=cancel_second_guard)
        assert calls == [None, None] and not point.conn.execute(
            "SELECT 1 FROM invocations").fetchone()
        assert [point.claim_invocation("C") for _ in range(3)] == [1, 2, 3]
        with pytest.raises(ck.CapExceeded):
            point.claim_invocation("C")
        assert point.state() == "BLOCKED_BUDGET"
        assert point.conn.execute(
            "SELECT ordinal FROM invocations WHERE stage='C' ORDER BY ordinal").fetchall() == \
            [(1,), (2,), (3,)]
    finally:
        point.close()


def test_atomic_precharge_is_idempotent_and_prevents_duplicate_success(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _work(point, "w1")
        _start(point)
        args = dict(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                    request_hash="r", attempt_no=1, work_id="w1")
        assert point.precharge(**args)
        assert not point.precharge(**args)
        point.finish_attempt(_attempt("w1", 1), outcome="ACCEPTED", response='{"ok":true}',
                             metadata={"n": 1}, accept_work=True)
        assert point.work("w1") == ("SUCCEEDED", _attempt("w1", 1))
        with pytest.raises(ck.CheckpointError):
            point.precharge(attempt_id=_attempt("w1", 2), stage="C", call_class="schema_retry",
                            request_hash="r", attempt_no=2, work_id="w1")
        assert point.usage()["total"] == 1
    finally:
        point.close()


def test_every_finished_outcome_has_one_deterministic_work_state(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    outcomes = sorted(ck.FINISH_OUTCOMES)
    point = ck.Checkpoint.create(
        root, "closed-outcomes", header=_header(root, probe=True),
        limits={"C": {"scored": len(outcomes)}}, cumulative_cap=len(outcomes))
    point.freeze_plan("C", "root", _plan(*(
        f"w{index}" for index in range(len(outcomes)))))
    point.transition("RUNNING")
    for index, outcome in enumerate(outcomes):
        work_id = f"w{index}"
        _work(point, work_id)
        attempt = _attempt(work_id, 1)
        point.precharge(
            attempt_id=attempt, stage="C", call_class="scored",
            request_hash="r", attempt_no=1, work_id=work_id)
        point.finish_attempt(
            attempt, outcome=outcome, response=None, metadata={},
            accept_work=outcome == "ACCEPTED")
        expected = "SUCCEEDED" if outcome == "ACCEPTED" else "PENDING"
        assert point.work(work_id)[0] == expected
        assert point.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()[0] == outcome
    point.close()


def test_attempt_numbers_cannot_skip(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _work(point, "w1")
        _start(point)
        point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                        request_hash="r", attempt_no=1, work_id="w1")
        point.finish_attempt(_attempt("w1", 1), outcome="SCHEMA_INVALID", response="{}",
                             metadata={}, accept_work=False)
        with pytest.raises(ck.ImmutableViolation, match="attempt id"):
            point.precharge(attempt_id="caller-chosen", stage="C",
                            call_class="schema_retry", request_hash="r",
                            attempt_no=2, work_id="w1")
        with pytest.raises(ck.CheckpointError):
            point.precharge(attempt_id=_attempt("w1", 3), stage="C", call_class="schema_retry",
                            request_hash="r", attempt_no=3, work_id="w1")
        assert point.precharge(attempt_id=_attempt("w1", 2), stage="C", call_class="schema_retry",
                               request_hash="r", attempt_no=2, work_id="w1")
    finally:
        point.close()


def test_class_cap_blocks_atomically_without_extra_attempt(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=1, schema=0, control=0, transport=0)
    try:
        _work(point, "w1")
        _work(point, "w2")
        _start(point)
        point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                        request_hash="r", attempt_no=1, work_id="w1")
        with pytest.raises(ck.CapExceeded):
            point.precharge(attempt_id=_attempt("w2", 1), stage="C", call_class="scored",
                            request_hash="r", attempt_no=1, work_id="w2")
        assert point.state() == "BLOCKED_BUDGET"
        assert point.usage()["total"] == 1
    finally:
        point.close()


def test_stage_hard_cap_is_the_exact_sum_of_frozen_class_allowances(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=2, schema=1, control=1, transport=1)
    try:
        for work_id in ("w1", "w2"):
            _work(point, work_id)
        _start(point)
        point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                        request_hash="r", attempt_no=1, work_id="w1")
        point.finish_attempt(_attempt("w1", 1), outcome="SCHEMA_INVALID", response="{}",
                             metadata={}, accept_work=False)
        point.precharge(attempt_id=_attempt("w1", 2), stage="C", call_class="schema_retry",
                        request_hash="r", attempt_no=2, work_id="w1")
        point.finish_attempt(_attempt("w1", 2), outcome="ACCEPTED", response="ok",
                             metadata={}, accept_work=True)
        point.precharge(attempt_id=_attempt("w2", 1), stage="C", call_class="scored",
                        request_hash="r", attempt_no=1, work_id="w2")
        point.finish_attempt(_attempt("w2", 1), outcome="RETRYABLE_TRANSPORT", response=None,
                             metadata={}, accept_work=False)
        point.precharge(
            attempt_id=_attempt("w2", 2), stage="C", call_class="transport_orphan",
            request_hash="r", attempt_no=2, work_id="w2")
        point.finish_attempt(_attempt("w2", 2), outcome="ACCEPTED", response="ok",
                             metadata={}, accept_work=True)
        control = "C:inv1:preflight"
        point.precharge(
            attempt_id=_control_attempt(control, 1), stage="C",
            call_class="preflight_probe", request_hash="probe", attempt_no=1,
            control_id=control)
        assert point.usage()["total"] == 5
        assert point.conn.execute(
            "SELECT hard_cap FROM stage_limits WHERE stage='C'").fetchone()[0] == 5
    finally:
        point.close()


def test_cumulative_cap_blocks_independently_of_stage_and_class_caps(tmp_path: Path) -> None:
    limits = {stage: {"scored": 2} for stage in ("C", "D")}
    root = tmp_path / "bench"
    point = ck.Checkpoint.create(
        root, "run-a", header=_header(root), limits=limits,
        cumulative_cap=3, journal_mode="DELETE")
    try:
        point.freeze_plan("C", "root", {
            "work": [{"work_id": work_id, "cell_id": "cell", "request_sha256": work_id}
                     for work_id in ("c1", "c2")]})
        _complete_work(point, "C", "c1", request_hash="c1")
        _complete_work(point, "C", "c2", request_hash="c2")
        c_hash = point.load_plan("C")[1]
        c_decision = point.freeze_decision(
            "c-next", "C", c_hash, "c-aggregate", "ACTIVATED", {"next": "D"})
        point.freeze_plan("D", c_decision, {
            "work": [{"work_id": work_id, "cell_id": "cell", "request_sha256": work_id}
                     for work_id in ("d1", "d2")]})
        for work_id, stage in (("c1", "C"), ("c2", "C"), ("d1", "D"), ("d2", "D")):
            if stage == "D":
                point.register_work(work_id, stage, "cell", work_id)
        for work_id, stage in (("d1", "D"),):
            point.precharge(attempt_id=_attempt(work_id, 1), stage=stage, call_class="scored",
                            request_hash=work_id, attempt_no=1, work_id=work_id)
            point.finish_attempt(_attempt(work_id, 1), outcome="ACCEPTED", response="ok",
                                 metadata={}, accept_work=True)
        with pytest.raises(ck.CapExceeded):
            point.precharge(attempt_id=_attempt("d2", 1), stage="D", call_class="scored",
                            request_hash="d2", attempt_no=1, work_id="d2")
        assert point.usage()["total"] == 3
        assert point.state() == "BLOCKED_BUDGET"
    finally:
        point.close()


def test_precharge_cannot_move_registered_work_to_another_stage(tmp_path: Path) -> None:
    limits = {stage: {"scored": 1} for stage in ("C", "D")}
    root = tmp_path / "bench"
    point = ck.Checkpoint.create(
        root, "run-a", header=_header(root), limits=limits,
        cumulative_cap=2, journal_mode="DELETE")
    try:
        point.freeze_plan("C", "root", _plan("w1", request_hash="request"))
        point.register_work("w1", "C", "cell", "request")
        point.transition("RUNNING")
        with pytest.raises(ck.ImmutableViolation):
            point.precharge(
                attempt_id=_attempt("w1", 1), stage="D", call_class="scored",
                request_hash="request", attempt_no=1, work_id="w1")
        assert point.usage()["total"] == 0
    finally:
        point.close()


def test_work_and_control_identity_cannot_transfer_call_class_budget(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=0, schema=1, control=1, transport=1)
    try:
        _work(point, "w1")
        _start(point)
        with pytest.raises(ck.ImmutableViolation, match="requires scored"):
            point.precharge(
                attempt_id=_attempt("w1", 1), stage="C", call_class="preflight_probe",
                request_hash="r", attempt_no=1, work_id="w1")
        with pytest.raises(ck.ImmutableViolation, match="requires preflight_probe"):
            point.precharge(
                attempt_id=_control_attempt("probe", 1), stage="C", call_class="scored",
                request_hash="probe", attempt_no=1, control_id="probe")
        assert point.usage()["total"] == 0
    finally:
        point.close()


def test_response_and_work_rollback_together_then_crash_recovery(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _work(point, "w1")
        _start(point)
        point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                        request_hash="r", attempt_no=1, work_id="w1")
        with pytest.raises(RuntimeError):
            point.finish_attempt(_attempt("w1", 1), outcome="ACCEPTED", response="answer",
                                 metadata={"done": True}, accept_work=True,
                                 before_commit=lambda: (_ for _ in ()).throw(RuntimeError("crash")))
        assert point.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?",
            (_attempt("w1", 1),)).fetchone()[0] == "DISPATCHING"
        assert point.work("w1") == ("DISPATCHING", None)
        assert point.recover() == 1
        assert point.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?",
            (_attempt("w1", 1),)).fetchone()[0] == "ORPHANED_UNKNOWN"
        assert point.work("w1") == ("PENDING", None)
        assert point.state() == "CANCELLED_PENDING_RESUME"
        point.transition("RUNNING")
        point.precharge(attempt_id=_attempt("w1", 2), stage="C", call_class="transport_orphan",
                        request_hash="r", attempt_no=2, work_id="w1")
        point.finish_attempt(_attempt("w1", 2), outcome="ACCEPTED", response="answer",
                             metadata={}, accept_work=True)
        assert point.work("w1") == ("SUCCEEDED", _attempt("w1", 2))
        assert point.usage()["total"] == 2
    finally:
        point.close()


def test_crash_before_precharge_rolls_back_without_phantom_attempt(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _work(point, "w1")
        _start(point)
        with pytest.raises(RuntimeError, match="before-precharge"):
            point.precharge(
                attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                request_hash="r", attempt_no=1, work_id="w1",
                claim_guard=lambda: (_ for _ in ()).throw(
                    RuntimeError("before-precharge")))
        assert point.usage()["total"] == 0
        assert point.work("w1") == ("PENDING", None)
    finally:
        point.close()


def test_persisted_running_reopens_recovers_and_resumes_without_duplicate(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    _start(point)
    point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                    request_hash="r", attempt_no=1, work_id="w1")
    path, root = point.path, point.root
    point.close()
    reopened = ck.Checkpoint.open(path, root)
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            reopened, lock, lambda _r, _c: ex.FakeResponse("accepted"))
        assert executor.recover_and_start("C") == (1, 1)
        _seed_preflight(reopened, "C", 1)
        result = executor.run(ex.WorkRequest(
            "C", "w1", "model", "r", 2, "transport_orphan"))
        assert result.outcome == "ACCEPTED"
    assert reopened.work("w1")[0] == "SUCCEEDED"
    assert reopened.usage()["total"] == 5
    reopened.close()


def test_safety_terminal_and_attempt_commit_atomically(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    _start(point)
    point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                    request_hash="r", attempt_no=1, work_id="w1")
    with pytest.raises(RuntimeError):
        point.finish_attempt(
            _attempt("w1", 1), outcome="FAILED_SAFETY", response=None, metadata={},
            accept_work=False, terminal_state="FAILED_SAFETY",
            before_commit=lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    assert point.state() == "RUNNING"
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?",
        (_attempt("w1", 1),)).fetchone()[0] == "DISPATCHING"
    point.finish_attempt(
        _attempt("w1", 1), outcome="FAILED_SAFETY", response=None, metadata={},
        accept_work=False, terminal_state="FAILED_SAFETY")
    assert point.state() == "FAILED_SAFETY"
    point.close()


def test_control_call_identity_is_sequential_and_charged(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _start(point)
        point.precharge(attempt_id=_control_attempt("inv1:tags", 1), stage="C", call_class="preflight_probe",
                        request_hash="tags", attempt_no=1, control_id="inv1:tags")
        point.finish_attempt(_control_attempt("inv1:tags", 1), outcome="ACCEPTED", response=None,
                             metadata={}, accept_work=False)
        with pytest.raises(ck.CheckpointError):
            point.precharge(attempt_id=_control_attempt("inv1:tags", 3), stage="C", call_class="preflight_probe",
                            request_hash="tags", attempt_no=3, control_id="inv1:tags")
    finally:
        point.close()


def test_control_crash_and_operator_cancel_remain_charged(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        _start(point)
        point.precharge(attempt_id=_control_attempt("C:inv1:tags", 1), stage="C", call_class="preflight_probe",
                        request_hash="tags", attempt_no=1, control_id="C:inv1:tags")
        assert point.recover() == 1
        assert point.state() == "CANCELLED_PENDING_RESUME"
        point.transition("RUNNING")
        point.precharge(attempt_id=_control_attempt("C:inv1:tags", 2), stage="C", call_class="transport_orphan",
                        request_hash="tags", attempt_no=2, control_id="C:inv1:tags")
        point.cancel(_control_attempt("C:inv1:tags", 2))
        states = point.conn.execute(
            "SELECT state FROM attempts ORDER BY attempt_no").fetchall()
        assert states == [("ORPHANED_UNKNOWN",), ("CANCELLED_UNVERIFIED",)]
        assert point.usage()["total"] == 2
    finally:
        point.close()


def test_global_lock_excludes_all_runs_and_abandon_requires_it(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    lock1, lock2 = fs.GlobalExecutionLock(root), fs.GlobalExecutionLock(root)
    first = _checkpoint(tmp_path, "run-a")
    second = _checkpoint(tmp_path, "run-b")
    try:
        lock1.acquire()
        with pytest.raises(ck.LockUnavailable):
            lock2.acquire()
        executor = ex.DurableExecutor(
            first, lock1, lambda _request, _cancel: ex.FakeResponse(""))
        executor.abandon()
        assert first.state() == "ABANDONED" and second.state() == "PREPARED"
        lock1.release()
        lock2.acquire()
        ex.DurableExecutor(
            second, lock2, lambda _request, _cancel: ex.FakeResponse("")).abandon()
        assert second.state() == "ABANDONED"
    finally:
        lock1.release()
        lock2.release()
        first.close()
        second.close()


def test_executor_cannot_continue_after_global_lock_is_released(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    calls: list[str] = []
    lock = fs.GlobalExecutionLock(tmp_path / "bench").acquire()
    executor = ex.DurableExecutor(
        point, lock, lambda _r, _c: calls.append("called") or ex.FakeResponse("ok"))
    executor.recover_and_start("C")
    lock.release()
    with pytest.raises(ck.CheckpointError, match="lost its matching global lock"):
        executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
    assert calls == [] and point.usage()["by_class"].get(("D", "scored"), 0) == 0
    point.close()


def test_readonly_status_verify_create_no_sqlite_sidecars(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    db = point.path
    point.close()
    before = {p.name for p in db.parent.iterdir()}
    assert fs.verify_readonly(db).ok
    assert fs.status_readonly(db) == {"state": "PREPARED", "calls_total": 0}
    assert {p.name for p in db.parent.iterdir()} == before


def test_readonly_inspection_refuses_live_wal_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    header = _header(root)
    header["filesystem_selected_mode"] = "WAL"
    point = ck.Checkpoint.create(
        root, "wal-run", header=header,
        limits=_limits(), cumulative_cap=11, journal_mode="WAL")
    try:
        point.conn.execute("INSERT INTO events(kind,detail_json,created) VALUES('x','{}',0)")
        before = {p.name for p in point.path.parent.iterdir()}
        result = fs.verify_readonly(point.path)
        assert not result.ok
        with pytest.raises(ck.CheckpointError):
            fs.status_readonly(point.path)
        assert {p.name for p in point.path.parent.iterdir()} == before
    finally:
        point.close()


def test_online_backup_is_unique_verified_and_owner_only(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
            first = fs.backup_snapshot(
                point, tmp_path / "bench" / "snapshots", lock=lock)
            second = fs.backup_snapshot(
                point, tmp_path / "bench" / "snapshots", lock=lock)
        assert first != second
        assert fs.verify_readonly(first).ok
        assert stat.S_IMODE(first.stat().st_mode) == 0o600
    finally:
        point.close()


def test_verified_snapshot_restores_to_a_unique_new_path(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
            snapshot = fs.backup_snapshot(
                point, tmp_path / "bench" / "snapshots", lock=lock)
        with fs.GlobalExecutionLock(tmp_path / "wrong-root") as lock:
            with pytest.raises(ck.CheckpointError, match="canonical root"):
                fs.restore_snapshot(snapshot, tmp_path / "wrong-root", lock=lock)
        assert not (tmp_path / "wrong-root" / "runs").exists()
        with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
            restored = fs.restore_snapshot(snapshot, tmp_path / "bench", lock=lock)
        assert restored != point.path
        assert fs.verify_readonly(restored).ok
        assert fs.status_readonly(restored) == {"state": "PREPARED", "calls_total": 0}
        assert stat.S_IMODE(restored.stat().st_mode) == 0o600
        with ck.Checkpoint.open(restored, tmp_path / "bench") as recovered:
            assert recovered.header()["run_id"] == "run-a"
            recovered.transition("RUNNING")
        with ck.Checkpoint.open(restored, tmp_path / "bench") as recovered:
            assert recovered.state() == "RUNNING"
    finally:
        point.close()


def test_restore_record_origin_hash_is_bound_inside_checkpoint(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    try:
        with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
            snapshot = fs.backup_snapshot(
                point, tmp_path / "bench" / "snapshots", lock=lock)
        with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
            restored = fs.restore_snapshot(snapshot, tmp_path / "bench", lock=lock)
        record_path = restored.parent / "restore.json"
        record = json.loads(record_path.read_text("utf-8"))
        record["snapshot_sha256"] = "0" * 64
        record_path.write_text(ck.canonical_json(record) + "\n", encoding="utf-8")
        with pytest.raises(ck.ImmutableViolation, match="restore origin"):
            ck.Checkpoint.open(restored, tmp_path / "bench")
    finally:
        point.close()


def test_corruption_is_copied_as_a_complete_evidence_set(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    db = point.path
    point.close()
    with db.open("r+b") as handle:
        handle.write(b"not-a-sqlite-database")
        handle.flush()
        os.fsync(handle.fileno())
    assert not fs.verify_readonly(db).ok
    sidecar = Path(str(db) + "-journal")
    sidecar.write_bytes(b"journal-evidence")
    os.chmod(sidecar, 0o600)
    with fs.GlobalExecutionLock(tmp_path / "other-bench") as wrong_lock:
        with pytest.raises(ck.LockUnavailable):
            fs.quarantine_corrupt(
                db, tmp_path / "bench" / "quarantine", reason="integrity_failed",
                lock=wrong_lock)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        qdir = fs.quarantine_corrupt(
            db, tmp_path / "bench" / "quarantine", reason="integrity_failed", lock=lock)
    record = json.loads((qdir / "quarantine.json").read_text("utf-8"))
    assert db.name in record["members"]
    assert sidecar.name in record["members"]
    assert db.exists() and sidecar.exists(), "quarantine preserves original evidence"


def test_wal_and_shm_are_preserved_with_corrupt_main_database(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    root.mkdir()
    db = root / "wal.sqlite3"
    code = """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('PRAGMA journal_mode=WAL')
c.execute('CREATE TABLE evidence(value TEXT)')
c.execute("INSERT INTO evidence VALUES('committed')")
os._exit(19)
"""
    result = subprocess.run([sys.executable, "-c", code, str(db)], check=False)
    assert result.returncode == 19
    wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
    assert wal.exists() and shm.exists()
    with db.open("r+b") as handle:
        handle.write(b"corrupt-main")
        handle.flush()
        os.fsync(handle.fileno())
    with fs.GlobalExecutionLock(root) as lock:
        qdir = fs.quarantine_corrupt(
            db, root / "quarantine", reason="integrity_failed", lock=lock)
    record = json.loads((qdir / "quarantine.json").read_text("utf-8"))
    assert {db.name, wal.name, shm.name}.issubset(record["members"])
    assert db.exists() and wal.exists() and shm.exists()


def test_corrupt_resume_closes_connection_and_quarantines_under_matching_lock(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = _checkpoint(tmp_path)
    db = point.path
    monkeypatch.setattr(
        ex, "verify_connection", lambda _conn: fs.Verification(False, ("corrupt",)))
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("must-not-run"))
        with pytest.raises(ck.CheckpointError, match="verification failed"):
            executor.recover_and_start("C")
    with pytest.raises(sqlite3.ProgrammingError):
        point.conn.execute("SELECT 1")
    records = list((tmp_path / "bench" / "quarantine").glob(
        "quarantine-*/quarantine.json"))
    assert len(records) == 1
    assert db.name in json.loads(records[0].read_text("utf-8"))["members"]
    assert db.exists(), "quarantine preserves original evidence"


def test_mount_fingerprint_is_stable_and_detects_mount_drift(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    root.mkdir()
    info1 = "10 1 0:1 / / rw,relatime - ext4 /dev/test rw\n"
    info2 = "10 1 0:1 / / rw,noatime - ext4 /dev/test rw\n"
    one = fs.mount_fingerprint(root, mountinfo=info1, kernel="k", mergerfs_version="m")
    again = fs.mount_fingerprint(root, mountinfo=info1, kernel="k", mergerfs_version="m")
    drift = fs.mount_fingerprint(root, mountinfo=info2, kernel="k", mergerfs_version="m")
    assert one == again
    assert one.sha256 != drift.sha256
    assert one.sqlite_version == sqlite3.sqlite_version


def test_filesystem_probe_proves_both_crash_outcomes_and_exclusion(tmp_path: Path) -> None:
    result = fs.probe_filesystem(tmp_path / "probe-root")
    assert result.selected_mode == "DELETE"
    assert result.capability_sha256 and not result.power_loss_tested
    assert all(mode.ok for mode in result.modes)
    for mode in result.modes:
        assert {"process-crash-old-or-new", "sqlite-exclusion", "flock-exclusion",
                "resume", "integrity"}.issubset(mode.checks)
    assert not any(p.name.startswith(".c0b2-fsprobe-")
                   for p in (tmp_path / "probe-root").iterdir())


def test_invocation_guard_rechecks_filesystem_before_recovery(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = _checkpoint(tmp_path)
    root = tmp_path / "bench"
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("unused"))
        assert executor.recover_and_start("C") == (0, 1)
    point.close()

    blocked = _checkpoint(tmp_path, run_id="run-b")
    calls: list[str] = []
    monkeypatch.setattr(
        ex, "revalidate_filesystem",
        lambda *_args: (_ for _ in ()).throw(ck.CheckpointError("fingerprint drift")))
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            blocked, lock,
            lambda _r, _c: calls.append("called") or ex.FakeResponse("unused"))
        with pytest.raises(ck.CheckpointError):
            executor.recover_and_start("C")
    assert blocked.state() == "BLOCKED_FILESYSTEM"
    assert calls == [] and blocked.usage()["total"] == 0
    blocked.close()


def test_executor_success_is_idempotent_and_uses_fake_transport(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    calls: list[str] = []

    def fake(request, _cancel) -> ex.FakeResponse:
        if isinstance(request, ex.WorkRequest):
            calls.append(request.work_id)
        return ex.FakeResponse('{"ok":true}', {"tokens": 4})

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, fake)
        executor.recover_and_start("C")
        _standard_preflight(executor, "C", 1)
        request = ex.WorkRequest("C", "w1", "model", "r", 1)
        assert executor.run(request).outcome == "ACCEPTED"
        assert executor.run(request).outcome == "ALREADY_ACCEPTED"
    assert calls == ["w1"]
    point.close()


def test_executor_rejects_work_model_outside_exact_frozen_item(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    calls = []
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda request, _cancel: calls.append(request) or ex.FakeResponse("ok"))
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        with pytest.raises(ck.CheckpointError, match="frozen plan identity"):
            executor.run(ex.WorkRequest("C", "w1", "not-frozen", "r", 1))
    assert calls == [] and point.work("w1") == ("PENDING", None)
    point.close()


@pytest.mark.parametrize("response", (
    ex.FakeResponse("{}", accepted=True, outcome="SCHEMA_INVALID"),
    ex.FakeResponse("{}", accepted=False, outcome="ACCEPTED"),
    ex.FakeResponse("{}", accepted=False, outcome="RETRYABLE_TRANSPORT"),
    ex.FakeResponse("{}", accepted=False, outcome="FAILED_SAFETY"),
    ex.FakeResponse("{}", accepted=False, outcome="BLOCKED_SECURITY"),
    ex.FakeResponse("{}", accepted=1, outcome="ACCEPTED"),
    ex.FakeResponse("{}", accepted=0, outcome="SCHEMA_INVALID"),
    ex.FakeResponse("{}", accepted="yes", outcome="ACCEPTED"),
    ex.FakeResponse("{}", accepted=None, outcome="SCHEMA_INVALID"),
))
def test_returned_error_or_contradictory_work_response_fails_closed(
        tmp_path: Path, response: ex.FakeResponse) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _request, _cancel: response)
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        result = executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
    assert result.outcome == "FAILED_SAFETY" and point.state() == "FAILED_SAFETY"
    assert point.work("w1") == ("PENDING", None)
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?",
        (result.attempt_id,)).fetchone()[0] == "FAILED_SAFETY"
    point.close()


def test_work_is_blocked_until_preflight_and_retryable_preflight_persists_pause(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    calls = []
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda request, _event: calls.append(request)
            or (_ for _ in ()).throw(ex.RetryableTransport()),
            now=lambda: 100.0)
        executor.recover_and_start("C")
        with pytest.raises(ck.CheckpointError, match="missing accepted"):
            executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
        version = ex.ControlRequest(
            "C", ex.control_id("C", 1, "version", ex.SERVER_CONTROL_MODEL),
            ex.SERVER_CONTROL_MODEL, "version", 1)
        result = executor.run_control(version, kind="version")
        assert result.outcome == "PAUSED_PREFLIGHT"
        assert result.retry_not_before == 115.0
    assert len(calls) == 1 and point.state() == "PAUSED_PREFLIGHT"
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?",
        (version.attempt_id,)).fetchone()[0] == "RETRYABLE_TRANSPORT"
    assert point.usage()["by_class"].get(("C", "scored"), 0) == 0
    assert point.backoff("model") == ck.BackoffRecord("model", 1, 115.0)
    point.close()


def test_returned_retry_label_cannot_bypass_control_failure_state(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda _request, _event: ex.FakeResponse(
                "", accepted=False, outcome="RETRYABLE_TRANSPORT"))
        executor.recover_and_start("C")
        version = ex.ControlRequest(
            "C", ex.control_id("C", 1, "version", ex.SERVER_CONTROL_MODEL),
            ex.SERVER_CONTROL_MODEL, "version", 1)
        result = executor.run_control(version, kind="version")
    assert result.outcome == "FAILED_SAFETY"
    assert point.state() == "FAILED_SAFETY"
    assert point.backoff("model").failures == 0
    point.close()


def test_invocation_stage_cannot_dispatch_another_stages_work(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {stage: {"scored": 1} for stage in ("C", "D")}
    point = ck.Checkpoint.create(
        root, "stage-bound", header=_header(root, probe=True), limits=limits,
        cumulative_cap=2)
    point.freeze_plan("C", "root", _plan("c1"))
    _complete_work(point, "C", "c1")
    c_hash = point.load_plan("C")[1]
    c_decision = point.freeze_decision(
        "c-next", "C", c_hash, "c-aggregate", "ACTIVATED", {"next": "D"})
    point.freeze_plan("D", c_decision, _plan("d1"))
    point.register_work("d1", "D", "cell", "r")
    calls = []
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda request, _cancel: calls.append(request) or ex.FakeResponse("ok"))
        executor.recover_and_start("C")
        with pytest.raises(ck.CheckpointError, match="invocation stage C"):
            executor.run(ex.WorkRequest("D", "d1", "model", "r", 1))
    assert calls == [] and point.usage()["by_class"].get(("D", "scored"), 0) == 0
    point.close()


def test_adaptive_stages_require_activated_predecessor_decision_chain(
        tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {"C": {"scored": 1}, "D": {"scored": 0}, "F": {"scored": 0}}
    point = ck.Checkpoint.create(
        root, "ordered", header=_header(root), limits=limits, cumulative_cap=1)
    with pytest.raises(ck.ImmutableViolation, match="D decision"):
        point.freeze_plan("F", "arbitrary", _plan())
    c_hash = point.freeze_plan("C", "root", _plan("c1"))
    _complete_work(point, "C", "c1")
    stopped = point.freeze_decision(
        "c-stop", "C", c_hash, "aggregate", "NOT_ACTIVATED", {"stop": True})
    with pytest.raises(ck.ImmutableViolation, match="C decision"):
        point.freeze_plan("D", stopped, _plan())
    point.close()


def test_post_f_acceptance_plan_requires_provisional_decision_and_exact_c44(
        tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {"C": {"scored": 44}, "D": {"scored": 1}, "F": {"scored": 1}}
    point = ck.Checkpoint.create(
        root, "acceptance", header=_header(root), limits=limits, cumulative_cap=46)
    c_plan = {"work": [
        {"work_id": f"c{index}", "cell_id": "cell",
         "request_sha256": f"c-request-{index}", "doc_id": f"c-doc-{index}"}
        for index in range(44)
    ]}
    c_hash = point.freeze_plan("C", "root", c_plan)
    for index in range(44):
        _complete_work(point, "C", f"c{index}", request_hash=f"c-request-{index}")
    c_decision = point.freeze_decision(
        "c-next", "C", c_hash, "c-aggregate", "ACTIVATED", {"next": "D"})
    d_hash = point.freeze_plan("D", c_decision, _plan("d1"))
    _complete_work(point, "D", "d1")
    d_decision = point.freeze_decision(
        "d-next", "D", d_hash, "d-aggregate", "ACTIVATED", {"next": "F"})
    f_hash = point.freeze_plan("F", d_decision, _plan("f1"))
    _complete_work(point, "F", "f1")
    selection = {
        "model": "model", "model_digest": "a" * 64, "worksheet": "v2",
        "chunk_chars": 4000, "overlap": 256, "num_ctx": 8192,
        "num_predict": 4096,
    }
    provisional = point.freeze_decision(
        "f-provisional", "F", f_hash, "f-aggregate", "ACTIVATED",
        {"outcome": "PROVISIONAL_SELECTED", "selection": selection})

    def acceptance_plan(count: int) -> dict:
        return {"work": [
            {"work_id": f"a{index}", "cell_id": "acceptance",
             "request_sha256": f"request-{index}", "doc_id": f"c-doc-{index}",
             **selection}
            for index in range(count)
        ]}

    with pytest.raises(ck.CheckpointError, match="C44"):
        point.freeze_acceptance_plan(provisional, acceptance_plan(43))
    digest = point.freeze_acceptance_plan(provisional, acceptance_plan(44))
    assert point.load_acceptance_plan()[1] == digest
    point.register_work("a0", "F", "acceptance", "request-0")
    with pytest.raises(ck.ImmutableViolation):
        point.register_work("a1", "F", "wrong-cell", "request-1")
    point.close()


def test_executor_persists_retry_backoff_then_resumes(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    now = [100.0]
    outcomes = [ex.RetryableTransport(), ex.FakeResponse("ok")]

    def fake(_request, _cancel):
        value = outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, fake, now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        first = executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
        assert first.outcome == "RETRY_WAIT" and first.retry_not_before == 115.0
        assert point.backoff("model").failures == 1
        now[0] = 115.0
        second = executor.run(ex.WorkRequest(
            "C", "w1", "model", "r", 2, "transport_orphan"))
        assert second.outcome == "ACCEPTED"
        assert point.backoff("model").failures == 0
    point.close()


def test_retry_outcome_and_backoff_rollback_together(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    original = point.finish_attempt

    def crash_finish(*args, before_commit=None, **kwargs):
        def crash() -> None:
            if before_commit:
                before_commit()
            raise RuntimeError("crash before commit")
        return original(*args, before_commit=crash, **kwargs)

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: (_ for _ in ()).throw(ex.RetryableTransport()),
            now=lambda: 100.0)
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        monkeypatch.setattr(point, "finish_attempt", crash_finish)
        with pytest.raises(RuntimeError, match="crash before commit"):
            executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
    assert point.backoff("model").failures == 0
    assert point.state() == "RUNNING"
    assert point.conn.execute(
        "SELECT state FROM attempts WHERE attempt_id=?",
        (ex.WorkRequest("C", "w1", "model", "r", 1).attempt_id,)).fetchone()[0] == \
        "DISPATCHING"
    point.close()


def test_schema_invalid_response_still_resets_resource_sequence(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    now = [0.0]
    outcomes = [ex.RetryableTransport(),
                ex.FakeResponse("{}", accepted=False, outcome="SCHEMA_INVALID"),
                ex.RetryableTransport(),
                ex.FakeResponse("{}", accepted=False, outcome="SCHEMA_INVALID")]

    def transport(_request, _cancel):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, transport, now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        first = executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
        assert first.outcome == "RETRY_WAIT"
        now[0] = first.retry_not_before
        result = executor.run(ex.WorkRequest(
            "C", "w1", "model", "r", 2, "transport_orphan"))
        assert result.outcome == "SCHEMA_INVALID"
        assert point.backoff("model").failures == 0
        assert point.work("w1")[0] == "PENDING"
        retry = executor.run(ex.WorkRequest(
            "C", "w1", "model", "r", 3, "schema_retry"))
        assert retry.outcome == "RETRY_WAIT"
        now[0] = retry.retry_not_before
        assert executor.run(ex.WorkRequest(
            "C", "w1", "model", "r", 4, "transport_orphan")).outcome == "SCHEMA_INVALID"
        assert point.work("w1") == ("COMPLETED_INVALID", None)
    point.close()


def test_sixth_resource_failure_pauses_atomically(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=1, schema=0, control=7, transport=6)
    _work(point, "w1")
    now = [0.0]

    def fail(_request, _cancel):
        raise ex.RetryableTransport()

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, fail, now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        for attempt in range(1, 7):
            call_class = "scored" if attempt == 1 else "transport_orphan"
            result = executor.run(ex.WorkRequest(
                "C", "w1", "model", "r", attempt, call_class))
            now[0] = result.retry_not_before
        assert result.outcome == "PAUSED_RESOURCE"
        assert point.state() == "PAUSED_RESOURCE"
        assert point.backoff("model").failures == 6
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        resumed = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("probe-ok"),
            now=lambda: now[0])
        assert resumed.recover_and_start("C") == (0, 2)
        _seed_preflight(point, "C", 2)
        with pytest.raises(ck.CheckpointError, match="resource probe required for model"):
            resumed.run(ex.WorkRequest(
                "C", "w1", "model", "r", 7, "transport_orphan"))
        probe_id = ex.resource_probe_id("C", 2, "model", "probe")
        result = resumed.run_resource_probe(ex.ControlRequest(
            "C", probe_id, "model", "probe", 1, "transport_orphan"))
        assert result.outcome == "ACCEPTED"
        assert point.backoff("model").failures == 0
    point.close()


def test_invocation_cancellation_before_claim_spends_no_ordinal_or_call(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    calls = []
    cancellation = ex.CancellationController()
    cancellation.first_signal()
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
                point, lock,
                lambda request, _event: calls.append(request) or ex.FakeResponse("bad"),
                cancellation=cancellation)
        with pytest.raises(ex.InvocationCancelled, match="before invocation"):
            executor.recover_and_start("C")
    assert point.state() == "CANCELLED_PENDING_RESUME"
    assert calls == [] and point.usage()["total"] == 0
    assert not point.conn.execute("SELECT 1 FROM invocations").fetchone()
    point.close()


def test_duplicate_resource_probe_precharge_never_recontacts_transport(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=1, schema=0, control=7, transport=6)
    now = [0.0]
    _pause_model_for_resource_probe(point, tmp_path / "bench", now)
    calls = []
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda request, _event: calls.append(request) or ex.FakeResponse("bad"),
            now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 2)
        probe = ex.ControlRequest(
            "C", ex.resource_probe_id("C", 2, "model", "probe"),
            "model", "probe", 1, "transport_orphan")
        assert point.precharge(
            attempt_id=probe.attempt_id, stage="C", call_class="transport_orphan",
            request_hash="probe", attempt_no=1, control_id=probe.control_id,
            invocation_ordinal=2, first_control_class="transport_orphan")
        assert executor.run_resource_probe(probe).outcome == "ALREADY_DISPATCHING"
    assert calls == [] and point.usage()["total"] == 13
    point.close()


def test_resource_probe_safety_limit_commits_terminal_state(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=1, schema=0, control=7, transport=6)
    now = [0.0]
    _pause_model_for_resource_probe(point, tmp_path / "bench", now)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda _request, _event: (_ for _ in ()).throw(ex.SafetyLimit()),
            now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 2)
        probe = ex.ControlRequest(
            "C", ex.resource_probe_id("C", 2, "model", "probe"),
            "model", "probe", 1, "transport_orphan")
        assert executor.run_resource_probe(probe).outcome == "FAILED_SAFETY"
        assert point.state() == "FAILED_SAFETY"
        assert point.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?",
            (probe.attempt_id,)).fetchone()[0] == "FAILED_SAFETY"
    point.close()


def test_returned_retry_label_cannot_clear_resource_probe_obligation(
        tmp_path: Path) -> None:
    point = _checkpoint(tmp_path, scored=1, schema=0, control=7, transport=6)
    now = [0.0]
    _pause_model_for_resource_probe(point, tmp_path / "bench", now)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock,
            lambda _request, _event: ex.FakeResponse(
                "", accepted=False, outcome="RETRYABLE_TRANSPORT"),
            now=lambda: now[0])
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 2)
        probe_id = ex.resource_probe_id("C", 2, "model", "probe")
        result = executor.run_resource_probe(ex.ControlRequest(
            "C", probe_id, "model", "probe", 1, "transport_orphan"))
    assert result.outcome == "FAILED_SAFETY"
    assert point.state() == "FAILED_SAFETY"
    assert point.backoff("model").failures == 6
    point.close()


def test_stage_f_cancellation_probe_requires_resume_before_following_health(
        tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {"C": {"scored": 1}, "D": {"scored": 1},
              "F": {"preflight_probe": 10}}
    header = _header(root, probe=True)
    header["model_digests"]["model-2"] = "c" * 64
    point = ck.Checkpoint.create(
        root, "cancel-probe", header=header, limits=limits,
        cumulative_cap=12)
    c_hash = point.freeze_plan("C", "root", _plan("c1"))
    _complete_work(point, "C", "c1")
    c_decision = point.freeze_decision(
        "c-next", "C", c_hash, "c-aggregate", "ACTIVATED", {"next": "D"})
    d_hash = point.freeze_plan("D", c_decision, _plan("d1"))
    _complete_work(point, "D", "d1")
    d_decision = point.freeze_decision(
        "d-next", "D", d_hash, "d-aggregate", "ACTIVATED", {"next": "F"})
    point.freeze_plan("F", d_decision, {"work": [
        *_plan("f-cancellation")["work"],
        {"work_id": "f-cancellation-2", "cell_id": "cell-2",
         "request_sha256": "r-2", "model": "model-2",
         "model_digest": "c" * 64},
    ]})
    calls = []
    cancellation = ex.CancellationController()

    def cancel_transport(request, _event):
        calls.append(request.control_id)
        cancellation.first_signal()
        return ex.FakeResponse("cancel-stream-closed")

    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            point, lock, cancel_transport, cancellation=cancellation)
        executor.recover_and_start("F")
        _seed_preflight(point, "F", 1)
        cancel_request = ex.ControlRequest(
            "F", ex.control_id("F", 1, "cancellation_probe", "model"),
            "model", "cancel", 1)
        assert executor.run_cancellation_probe(cancel_request).outcome == \
            "CANCELLED_PENDING_RESUME"
        assert len(calls) == 1 and point.state() == "CANCELLED_PENDING_RESUME"

    with fs.GlobalExecutionLock(root) as lock:
        resumed = ex.DurableExecutor(
            point, lock,
            lambda request, _event: calls.append(request.control_id)
            or ex.FakeResponse("healthy"))
        assert resumed.recover_and_start("F") == (0, 2)
        _seed_preflight(point, "F", 2)
        health_request = ex.ControlRequest(
            "F", ex.control_id("F", 2, "cancellation_health", "model"),
            "model", "health", 1)
        assert resumed.run_cancellation_health(
            health_request, cancelled_attempt_id=cancel_request.attempt_id).outcome == \
            "ACCEPTED"
        with pytest.raises(ck.CheckpointError, match="model-2"):
            resumed._require_cancellation_health_event()
    assert len(calls) == 2
    event = point.conn.execute(
        "SELECT detail_json FROM events WHERE kind='CANCELLATION_HEALTH_PASS'").fetchone()
    assert event and json.loads(event[0]) == {
        "cancelled_attempt_id": cancel_request.attempt_id,
        "health_attempt_id": health_request.attempt_id,
    }
    point.close()


def test_soft_wall_and_cancel_pause_without_phantom_calls(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    mono = [0.0]
    clock = ex.InvocationClock(10, monotonic=lambda: mono[0])
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(point, lock, lambda _r, _c: ex.FakeResponse("ok"),
                                      clock=clock)
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        mono[0] = 10.0
        assert executor.run(ex.WorkRequest("C", "w1", "model", "r", 1)).outcome == \
            "PAUSED_SOFT_WALL"
        assert point.usage()["by_class"].get(("C", "scored"), 0) == 0
        point.transition("RUNNING")
        executor.cancel()
        assert point.state() == "CANCELLED_PENDING_RESUME"
        assert point.usage()["by_class"].get(("C", "scored"), 0) == 0
    point.close()


def test_soft_wall_crossing_inside_atomic_claim_creates_no_attempt(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    times = iter((0.0, 0.0, 10.0))
    clock = ex.InvocationClock(10, monotonic=lambda: next(times))
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("must-not-run"), clock=clock)
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        result = executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
    assert result.outcome == "PAUSED_SOFT_WALL"
    assert (point.state() == "PAUSED_SOFT_WALL"
            and point.usage()["by_class"].get(("C", "scored"), 0) == 0)
    point.close()


def test_interruptible_backoff_honors_the_invocation_soft_wall(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = _checkpoint(tmp_path)
    mono = [0.0]
    clock = ex.InvocationClock(10, monotonic=lambda: mono[0])
    cancellation = ex.CancellationController()

    def advance(_seconds: float) -> bool:
        mono[0] = 10.0
        return False

    monkeypatch.setattr(cancellation, "wait", advance)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("unused"),
            clock=clock, cancellation=cancellation, now=lambda: 100.0)
        executor.recover_and_start("C")
        assert not executor.interruptible_backoff(120.0)
    assert point.state() == "PAUSED_SOFT_WALL"
    assert point.usage()["total"] == 0
    point.close()


def test_forced_second_signal_leaves_dispatching_for_orphan_recovery(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    _work(point, "w1")
    cancellation = ex.CancellationController()

    def interrupted(_request, _event):
        cancellation.second_signal()
        raise KeyboardInterrupt

    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(point, lock, interrupted,
                                      cancellation=cancellation)
        executor.recover_and_start("C")
        _seed_preflight(point, "C", 1)
        with pytest.raises(KeyboardInterrupt):
            executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
        assert cancellation.forced
        assert point.recover() == 1
    assert point.work("w1") == ("PENDING", None)
    assert point.usage()["by_class"][("C", "scored")] == 1
    point.close()


def test_successful_finalization_requires_every_work_item(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {
        "C": {"scored": 3, "preflight_probe": 3},
        "D": {},
        "F": {},
    }
    point = ck.Checkpoint.create(
        root, "complete", header=_header(root, probe=True), limits=limits,
        cumulative_cap=6)
    c_hash = point.freeze_plan("C", "root", _plan("w1", "w2", "w3"))
    artifact = {"reason": "no decisive candidate"}
    artifact_hash = hashlib.sha256(
        ck.canonical_json(artifact).encode("utf-8")).hexdigest()
    for work_id in ("w1", "w2", "w3"):
        _work(point, work_id)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        c_executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("ok"))
        c_executor.recover_and_start("C")
        _standard_preflight(c_executor, "C", 1)
        c_executor.run(ex.WorkRequest("C", "w1", "model", "r", 1))
        with pytest.raises(ck.CheckpointError, match="frozen work completes"):
            point.freeze_decision(
                "c-stop", "C", c_hash, "c-aggregate", "NOT_ACTIVATED", {})
        c_executor.run(ex.WorkRequest("C", "w2", "model", "r", 1))
        c_executor.run(ex.WorkRequest("C", "w3", "model", "r", 1))
        point.freeze_decision(
            "c-stop", "C", c_hash, "c-aggregate", "NOT_ACTIVATED",
            {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
             "facts": {"deterministic_stop": True,
                       "reason": "no decisive candidate"}})
        digest = c_executor.finalize_complete("INCONCLUSIVE", artifact)
    assert point.state() == "INCONCLUSIVE"
    record = json.loads(point.conn.execute(
        "SELECT detail_json FROM events WHERE kind='FINAL_ARTIFACT'").fetchone()[0])
    assert record["sha256"] == digest and record["artifact"] == artifact
    point.close()


def test_empty_public_shell_cannot_finalize_selected(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    limits = {stage: {"scored": 0} for stage in ("C", "D", "F")}
    point = ck.Checkpoint.create(
        root, "empty", header=_header(root, probe=True), limits=limits,
        cumulative_cap=0)
    with pytest.raises(ck.CheckpointError, match="Stage C plan cannot be empty"):
        point.freeze_plan("C", "root", _plan())
    assert point.state() == "PREPARED"
    point.close()


def test_public_finalization_requires_all_public_stage_plans(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path)
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("unused"))
        executor.recover_and_start("C")
        with pytest.raises(ck.CheckpointError, match="required stage limits and plans"):
            executor.finalize_complete("SELECTED", {"selection": "model"})
    point.close()


@pytest.mark.parametrize("outcome", ("FAIL_OPERATIONAL", "INCOMPLETE"))
def test_private_nonpass_terminal_requires_and_persists_aggregate_artifact(
        tmp_path: Path, outcome: str) -> None:
    root = tmp_path / outcome.lower()
    point = ck.Checkpoint.create(
        root, "private", header=_header(root, probe=True, run_type="private"),
        limits={"E": {"preflight_probe": 2}}, cumulative_cap=2)
    e_hash = point.freeze_plan("E", "a" * 64, _plan())
    artifact = {"outcome": outcome, "reason": "frozen target could not complete"}
    artifact_hash = hashlib.sha256(
        ck.canonical_json(artifact).encode("utf-8")).hexdigest()
    point.freeze_decision(
        "e-stop", "E", e_hash, "e-aggregate", "NOT_ACTIVATED",
        {"outcome": outcome, "artifact_sha256": artifact_hash,
         "facts": {"deterministic_stop": True,
                   "reason": "frozen target could not complete"}})
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("ok"))
        executor.recover_and_start("E")
        _standard_preflight(executor, "E", 1)
        digest = executor.finalize_complete(outcome, artifact)
    event = json.loads(point.conn.execute(
        "SELECT detail_json FROM events WHERE kind='FINAL_ARTIFACT'").fetchone()[0])
    assert point.state() == outcome and event["sha256"] == digest
    point.close()


def test_private_pass_counts_must_match_selection_parent_and_frozen_e_plan(
        tmp_path: Path) -> None:
    root = tmp_path / "private-pass"
    point = ck.Checkpoint.create(
        root, "private", header=_header(root, probe=True, run_type="private"),
        limits={"E": {"scored": 1, "preflight_probe": 3}}, cumulative_cap=4)
    e_plan = {"work": [{"work_id": "e1", "cell_id": "private",
                         "request_sha256": "request", "doc_id": "private-doc",
                         "model": "model", "model_digest": "a" * 64}]}
    with pytest.raises(ck.ImmutableViolation, match="public selection"):
        point.freeze_plan("E", "0" * 64, e_plan)
    e_hash = point.freeze_plan("E", "a" * 64, e_plan)
    point.register_work("e1", "E", "private", "request")
    artifact = {"outcome": "PASS_OPERATIONAL"}
    artifact_hash = hashlib.sha256(
        ck.canonical_json(artifact).encode("utf-8")).hexdigest()
    with fs.GlobalExecutionLock(root) as lock:
        executor = ex.DurableExecutor(
            point, lock, lambda _r, _c: ex.FakeResponse("ok"))
        executor.recover_and_start("E")
        _standard_preflight(executor, "E", 1)
        executor.run(ex.WorkRequest("E", "e1", "model", "request", 1))
        point.freeze_decision(
            "e-pass", "E", e_hash, "e-aggregate", "ACTIVATED",
            {"outcome": "PASS_OPERATIONAL", "artifact_sha256": artifact_hash,
             "facts": {"target": 20, "processed": 20,
                       "gates": {gate: True for gate in ex.PRIVATE_OPERATIONAL_GATES}}})
        with pytest.raises(ck.CheckpointError, match="frozen E plan"):
            executor.finalize_complete("PASS_OPERATIONAL", artifact)
    assert point.state() == "RUNNING"
    point.close()


def test_control_id_binds_stage_invocation_kind_and_model() -> None:
    base = ex.control_id("C", 1, "preflight", "model-a")
    assert base == ex.control_id("C", 1, "preflight", "model-a")
    assert len({base, ex.control_id("D", 1, "preflight", "model-a"),
                ex.control_id("C", 2, "preflight", "model-a"),
                ex.control_id("C", 1, "health", "model-a"),
                ex.control_id("C", 1, "preflight", "model-b")}) == 5


def test_offline_executor_has_no_network_unload_or_process_kill_capability() -> None:
    tree = ast.parse(Path(ex.__file__).read_text("utf-8"))
    imports = {alias.name.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names}
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not imports.intersection({"httpx", "requests", "socket", "subprocess", "urllib"})
    assert not calls.intersection({"kill", "killpg", "terminate", "send_signal"})


def test_stage_c_aggregate_facts_are_bound_to_checkpoint_attempts(tmp_path: Path) -> None:
    point = _checkpoint(tmp_path); _work(point, "w1"); _start(point)
    metadata = {"done_reason": "stop", "tools_empty": True, "images_empty": True,
                "unknown_message_fields_empty": True, "strict_schema_invalid": False, "semantic_invalid": False}
    point.precharge(attempt_id=_attempt("w1", 1), stage="C", call_class="scored",
                    request_hash="r", attempt_no=1, work_id="w1")
    point.finish_attempt(_attempt("w1", 1), outcome="ACCEPTED", response="{}", metadata=metadata, accept_work=True)
    plan_raw = ck.canonical_json({"work": [{"work_id": "w1", "cell_id": "cell", "doc_id": "doc"}]})
    document = {"doc_id": "doc", "charged_attempt_count": 1, "first_pass_valid": True,
                "eventual_valid": True, "strict_schema_invalid_attempts": 0,
                "semantic_invalid_attempts": 0, "done_reason": "stop", "tools_empty": True,
                "images_empty": True, "unknown_message_fields_empty": True}
    aggregate = {"cells": [{"cell_id": "cell", "documents": [document], "length_outcomes": 0}]}
    point._validate_stage_c_attempt_facts(aggregate, plan_raw)
    for key, bad in (("charged_attempt_count", 2), ("strict_schema_invalid_attempts", 1),
                     ("tools_empty", False)):
        original, document[key] = document[key], bad
        with pytest.raises(ck.ImmutableViolation, match="checkpoint attempts"): point._validate_stage_c_attempt_facts(aggregate, plan_raw)
        document[key] = original
    aggregate["cells"][0]["length_outcomes"] = 1
    with pytest.raises(ck.ImmutableViolation, match="length outcomes"): point._validate_stage_c_attempt_facts(aggregate, plan_raw)
    point.close()

def test_stage_c_terminal_paths_recompute_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    point = _checkpoint(tmp_path); plan_hash, aggregate_hash = "b" * 64, "c" * 64
    models = [{"model": m, "model_digest": d, "v1_passed": False, "v2_passed": False,
               "selected_worksheet": None,
               "selection_basis": "no_passer", "bootstrap": None}
              for m, d, _think in plan.MODELS]
    selection = {"version": "stage-c-selection-v1", "stage": "C", "plan_sha256": plan_hash,
                 "aggregate_sha256": aggregate_hash,
                 "models": models, "survivors": []}
    wrong = json.loads(json.dumps(selection)); wrong["models"][0].update(
        v1_passed=True, selected_worksheet="v1", selection_basis="only_passer")
    wrong["survivors"] = [{"model": plan.MODELS[0][0], "model_digest": plan.MODELS[0][1],
                           "worksheet": "v1", "chunk_chars": 4000, "overlap": 256,
                           "num_ctx": 8192, "num_predict": 4096}]
    monkeypatch.setattr(point, "load_plan", lambda _stage: ("root", plan_hash, ck.canonical_json({"work": [{}] * 264})))
    monkeypatch.setattr(point, "load_aggregate", lambda _stage: (plan_hash, aggregate_hash, ck.canonical_json({})))
    monkeypatch.setattr(stage_c, "build_stage_c_selection", lambda _aggregate: selection)
    with pytest.raises(ck.ImmutableViolation, match="frozen survivor"):
        point.freeze_stage_boundary_decision("stage-c-selection", "C", plan_hash, aggregate_hash, wrong)
    monkeypatch.setattr(stage_c, "build_stage_c_selection", lambda _aggregate: wrong)
    artifact = {"version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "C",
                "aggregate_sha256": aggregate_hash, "reason": "no_stage_c_survivor"}
    with fs.GlobalExecutionLock(tmp_path / "bench") as lock:
        executor = ex.DurableExecutor(point, lock, lambda _request, _cancel: None)
        with pytest.raises(ck.CheckpointError, match="frozen evidence"): executor.finalize_stage_c_inconclusive(selection, artifact)
    point.close()
