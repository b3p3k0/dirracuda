"""Offline transaction and provenance tests for the Stage-F activation substrate."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b2_runtime as public_runtime
from scripts.analyst_benchmark import c0b2_runtime_common as runtime_common
from scripts.analyst_benchmark import c0b2_executor as executor_module
from scripts.analyst_benchmark import c0b2_runtime_f as runtime_f
from scripts.analyst_benchmark import c0b2_schema as schema_module
from scripts.analyst_benchmark import c0b2_transport as transport_module
from scripts.analyst_benchmark.c0b2_checkpoint import (
    CheckpointError, ImmutableViolation, canonical_json, sha256_json,
)
from scripts.analyst_benchmark.c0b2_executor import DurableExecutor, WorkRequest
from scripts.analyst_benchmark.c0b2_plan import (
    OPTIONS_C, attempt_id as stable_attempt_id,
)


def _point() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_aggregates(
          plan_key TEXT PRIMARY KEY, plan_hash TEXT, aggregate_hash TEXT,
          aggregate_json TEXT, created REAL);
        CREATE TABLE decisions(
          decision_id TEXT PRIMARY KEY, stage TEXT, parent_hash TEXT,
          aggregate_hash TEXT, activation TEXT, value_json TEXT, created REAL);
        CREATE TABLE invocations(stage TEXT, ordinal INTEGER, created REAL,
          PRIMARY KEY(stage,ordinal));
        CREATE TABLE plan_activations(
          plan_key TEXT PRIMARY KEY, activation_hash TEXT,
          activation_json TEXT, created REAL);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT,detail_json TEXT,created REAL);
        CREATE TABLE public_artifacts(
          artifact_id TEXT,terminal TEXT,artifact_hash TEXT,artifact_json TEXT,created REAL);
        CREATE TABLE acceptance_plan(id INTEGER);
    """)
    return SimpleNamespace(conn=conn)


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def test_phase_evidence_transaction_rolls_back_and_replay_is_exact() -> None:
    point = _point()
    value = {"bounded": "seed1"}
    with pytest.raises(OSError, match="crash"):
        with runtime_f.runtime_transaction(point):
            runtime_f._freeze_phase_evidence(point, "F_SEED_1", "1" * 64, value)
            raise OSError("crash")
    assert _rows(point.conn, "phase_aggregates") == []

    with runtime_f.runtime_transaction(point):
        first = runtime_f._freeze_phase_evidence(
            point, "F_SEED_1", "1" * 64, value)
    before = _rows(point.conn, "phase_aggregates")
    with runtime_f.runtime_transaction(point):
        assert runtime_f._freeze_phase_evidence(
            point, "F_SEED_1", "1" * 64, value) == first
    assert _rows(point.conn, "phase_aggregates") == before
    with pytest.raises(ImmutableViolation, match="already changed"):
        with runtime_f.runtime_transaction(point):
            runtime_f._freeze_phase_evidence(
                point, "F_SEED_1", "1" * 64, {"bounded": "drift"})
    assert _rows(point.conn, "phase_aggregates") == before


def test_decision_digest_uses_row_domain_and_rejects_payload_substitution() -> None:
    point = _point()
    value = {"decision": "exact"}
    with runtime_f.runtime_transaction(point):
        digest = runtime_f._freeze_decision(
            point, "stage-f-seed-activation", "2" * 64, "3" * 64,
            "ACTIVATED", value)
    assert digest != sha256_json(value)
    row = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id='stage-f-seed-activation'").fetchone()
    assert digest == sha256_json(("stage-f-seed-activation", *row))
    before = _rows(point.conn, "decisions")
    with runtime_f.runtime_transaction(point):
        assert runtime_f._freeze_decision(
            point, "stage-f-seed-activation", "2" * 64, "3" * 64,
            "ACTIVATED", value) == digest
    assert _rows(point.conn, "decisions") == before
    with pytest.raises(ImmutableViolation, match="already changed"):
        runtime_f._freeze_decision(
            point, "stage-f-seed-activation", sha256_json(value), "3" * 64,
            "ACTIVATED", value)
    assert _rows(point.conn, "decisions") == before


def test_attempt_window_rejects_backdating_and_post_seed1_activation() -> None:
    point = _point()
    point.conn.execute(
        "INSERT INTO plan_activations VALUES('F_SEED_1','a','{}',10.0)")
    point.conn.executemany(
        "INSERT INTO invocations VALUES('F',?,?)", [(1, 20.0), (2, 30.0)])
    assert runtime_f._attempt_in_invocation(point, 1, 20.0)
    assert not runtime_f._attempt_in_invocation(point, 1, 19.999)
    assert not runtime_f._attempt_in_invocation(point, 1, 30.0)
    point.conn.execute(
        "INSERT INTO plan_activations VALUES('F_SEED_17','b','{}',25.0)")
    point.conn.execute(
        "INSERT INTO plan_activations VALUES('F_SEED_20260804','c','{}',25.0)")
    assert runtime_f._attempt_in_invocation(point, 1, 24.999)
    assert not runtime_f._attempt_in_invocation(point, 1, 25.0)


def test_production_control_census_hook_fails_closed_without_mutation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _point()
    monkeypatch.delattr(
        public_runtime, "validate_b4_f_control_census", raising=False)
    before = point.conn.iterdump()
    snapshot = tuple(before)
    with pytest.raises(ImmutableViolation, match="cannot rebuild its owners"):
        runtime_f.validate_b4_f_control_census(point)
    assert tuple(point.conn.iterdump()) == snapshot


def test_acceptance_requires_state_independent_stored_d_owner(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _point()
    called: list[bool] = []
    monkeypatch.setattr(runtime_f, "_typed", lambda value, _model: dict(value))
    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census", lambda _point: None)
    monkeypatch.setattr(runtime_f, "assert_acceptance_namespace_clean", lambda _point: None)

    def blocked(_point):
        called.append(True)
        raise CheckpointError("stored D owner unavailable")

    monkeypatch.setattr(runtime_f, "_validate_stored_d_owner", blocked)
    before = tuple(point.conn.iterdump())
    with pytest.raises(CheckpointError, match="stored D owner unavailable"):
        runtime_f.activate_f_acceptance(
            point, {"plan_key": "F_ACCEPTANCE"},
            final_aggregate_sha256="f" * 64)
    assert called == [True]
    assert tuple(point.conn.iterdump()) == before


def _transition_point(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE run_state(id INTEGER PRIMARY KEY,state TEXT,updated REAL);
        INSERT INTO run_state VALUES(1,'RUNNING',0.0);
        CREATE TABLE runtime_cursor(
          id INTEGER PRIMARY KEY,active_stage TEXT,active_plan_key TEXT,updated REAL);
        INSERT INTO runtime_cursor VALUES(1,'F','F_SEED_17',1.0);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT,detail_json TEXT,created REAL);
        CREATE TABLE work_items(work_id TEXT PRIMARY KEY,stage TEXT,cell_id TEXT,
          request_hash TEXT,state TEXT,accepted_attempt_id TEXT);
        CREATE TABLE phase_work_registry(
          work_id TEXT PRIMARY KEY,plan_key TEXT,activation_group_id TEXT);
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_17',1.0);
        INSERT INTO plan_activations VALUES('F_SEED_20260804',1.0);
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        INSERT INTO invocations VALUES('F',1,1.5);
        CREATE TABLE attempts(attempt_id TEXT,work_id TEXT,control_id TEXT,
          stage TEXT,invocation_ordinal INTEGER,request_hash TEXT,
          call_class TEXT,state TEXT,response TEXT,metadata_json TEXT,
          attempt_no INTEGER,created REAL,updated REAL);
    """)
    f17_work, f202_work = "1" * 64, "2" * 64
    accepted = stable_attempt_id(f17_work, 1)
    conn.execute(
        "INSERT INTO work_items VALUES(?,?,?,?,?,?)",
        (f17_work, "F", "4" * 64, "5" * 64, "SUCCEEDED", accepted))
    conn.execute(
        "INSERT INTO work_items VALUES(?,?,?,?,?,NULL)",
        (f202_work, "F", "6" * 64, "7" * 64, "PENDING"))
    conn.executemany(
        "INSERT INTO phase_work_registry VALUES(?,?,?)", [
            (f17_work, "F_SEED_17", "8" * 64),
            (f202_work, "F_SEED_20260804", "9" * 64),
        ])
    conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (accepted, f17_work, None, "F", 1, "5" * 64, "scored", "ACCEPTED",
         "{}", canonical_json({"done_reason": "stop", "prompt_eval_count": 1,
                               "tools_empty": True, "images_empty": True,
                               "unknown_message_fields_empty": True}), 1, 2.0, 3.0))
    conn.commit()
    point = SimpleNamespace(
        conn=conn, state=lambda: conn.execute(
            "SELECT state FROM run_state WHERE id=1").fetchone()[0],
        header=lambda: {"run_id": "runtime-f-transition"},
    )
    master_hash, evidence_hash, decision_digest = "a" * 64, "b" * 64, "c" * 64
    from_plan = {"plan_key": "F_SEED_17", "groups": [{"group_id": "8" * 64}],
                 "work": [{"work_id": f17_work, "stage": "F", "cell_id": "4" * 64,
                           "request_sha256": "5" * 64,
                           "plan_key": "F_SEED_17",
                           "activation_group_id": "8" * 64}]}
    to_plan = {"plan_key": "F_SEED_20260804",
               "groups": [{"group_id": "9" * 64}],
               "work": [{"work_id": f202_work, "stage": "F", "cell_id": "6" * 64,
                         "request_sha256": "7" * 64,
                         "plan_key": "F_SEED_20260804",
                         "activation_group_id": "9" * 64}]}
    master = {"plans": [
        {"payload": {"plan_key": "F_SEED_1"}},
        {"payload": from_plan}, {"payload": to_plan}]}
    activations = {
        "F_SEED_17": ({"activated_group_ids": ["8" * 64],
                        "parent_decision_sha256": decision_digest}, "d" * 64),
        "F_SEED_20260804": ({"activated_group_ids": ["9" * 64],
                              "parent_decision_sha256": decision_digest}, "e" * 64),
    }
    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census", lambda _point: None)
    monkeypatch.setattr(runtime_f, "_master", lambda _point: (master, master_hash))
    monkeypatch.setattr(runtime_f, "_public_inputs", lambda _point: (object(), b"key"))
    monkeypatch.setattr(
        runtime_f, "_validate_stored_d_owner",
        lambda _point: (master_hash, {"selections": []}))
    monkeypatch.setattr(runtime_f, "validate_f_master_plan", lambda *args, **kwargs: master)
    monkeypatch.setattr(
        runtime_f, "_stored_plan_activation",
        lambda _point, key: (
            from_plan if key == "F_SEED_17" else to_plan,
            ("f" if key == "F_SEED_17" else "0") * 64,
            activations[key][0], activations[key][1]))
    decision = {"seed1_evidence_sha256": evidence_hash,
                "activated_group_ids": ["8" * 64, "9" * 64]}
    monkeypatch.setattr(
        runtime_f, "validate_seed_activation_owner",
        lambda *_args: ({}, decision, decision_digest))
    # The runtime additionally binds D selections to seed-1 candidates. This narrow
    # transaction fixture has no candidate payload, so bypass only that outer lookup.
    master["parent_decision_sha256"] = master_hash
    master["base_candidate_order"] = []
    master["plans"][0]["payload"]["candidates"] = []
    return point


def test_seed_cursor_transition_is_atomic_and_replay_exact(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _transition_point(monkeypatch)
    original = runtime_f._before_activation_commit
    monkeypatch.setattr(
        runtime_f, "_before_activation_commit",
        lambda: (_ for _ in ()).throw(OSError("crash before commit")))
    with pytest.raises(OSError, match="crash before commit"):
        runtime_f.advance_f_seed_cursor(point)
    assert point.conn.execute(
        "SELECT active_plan_key FROM runtime_cursor").fetchone()[0] == "F_SEED_17"
    assert point.conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0

    monkeypatch.setattr(runtime_f, "_before_activation_commit", original)
    value = runtime_f.advance_f_seed_cursor(point)
    assert value["transition_sha256"] == sha256_json({
        key: item for key, item in value.items() if key != "transition_sha256"})
    before = tuple(point.conn.iterdump())
    assert runtime_f.advance_f_seed_cursor(point) == value
    assert tuple(point.conn.iterdump()) == before


@pytest.mark.parametrize("owner", ["F_SEED_17", "F_SEED_20260804"])
def test_seed_cursor_replay_rejects_attempts_across_transition(
        monkeypatch: pytest.MonkeyPatch, owner: str) -> None:
    point = _transition_point(monkeypatch)
    runtime_f.advance_f_seed_cursor(point)
    marker = point.conn.execute(
        "SELECT created FROM events WHERE kind='F_SEED_CURSOR_TRANSITION'"
    ).fetchone()[0]
    work_id = "1" * 64 if owner == "F_SEED_17" else "2" * 64
    created = marker + 1 if owner == "F_SEED_17" else marker - 1
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stable_attempt_id(work_id, 2), work_id, None, "F", 1,
         "5" * 64 if owner == "F_SEED_17" else "7" * 64,
         "transport_orphan", "RETRYABLE_TRANSPORT", None, None, 2,
         created, created))
    with pytest.raises(ImmutableViolation, match="crossed|predates"):
        runtime_f.advance_f_seed_cursor(point)


def test_seed_cursor_wrong_rotation_makes_no_mutation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _transition_point(monkeypatch)
    original = runtime_f._stored_plan_activation

    def wrong(point_arg, key):
        plan, digest, activation, activation_hash = original(point_arg, key)
        if key == "F_SEED_17":
            activation = {**activation, "activated_group_ids": ["9" * 64]}
        return plan, digest, activation, activation_hash

    monkeypatch.setattr(runtime_f, "_stored_plan_activation", wrong)
    before = tuple(point.conn.iterdump())
    with pytest.raises(ImmutableViolation, match="rotation"):
        runtime_f.advance_f_seed_cursor(point)
    assert tuple(point.conn.iterdump()) == before


def test_seed1_scheduler_enforces_context_cancel_health_order(
        monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(work_id TEXT,state TEXT)")
    work_states = {"w1": "PENDING", "w2": "PENDING"}
    point = SimpleNamespace(
        conn=conn, work=lambda work_id: (work_states[work_id], None))
    controls = {
        "context": SimpleNamespace(state="PENDING"),
        "cancel": SimpleNamespace(state="PENDING"),
        "health": SimpleNamespace(state="PENDING"),
    }
    monkeypatch.setattr(
        runtime_common, "load_runtime_control",
        lambda _point, identity: controls[identity])
    group = {
        "candidate_id": "candidate", "first_work_id": "w1",
        "context_control": {"control_id": "context"},
        "cancellation_control": {"control_id": "cancel"},
        "health_control": {"control_id": "health"},
    }
    work = (
        {"work_id": "w1", "candidate_id": "candidate"},
        {"work_id": "w2", "candidate_id": "candidate"},
    )
    phase = runtime_f.ActiveFPhase(
        {"groups": [group], "plan_key": "F_SEED_1"}, "a" * 64, work)

    assert runtime_f._seed1_next(point, phase) == ("work", work[0])
    conn.execute("INSERT INTO attempts VALUES('w1','ACCEPTED')")
    assert runtime_f._seed1_next(point, phase) == (
        "context", group["context_control"])
    controls["context"] = SimpleNamespace(state="COMPLETE")
    work_states["w1"] = "SUCCEEDED"
    assert runtime_f._seed1_next(point, phase) == ("work", work[1])
    conn.execute("INSERT INTO attempts VALUES('w2','ACCEPTED')")
    work_states["w2"] = "SUCCEEDED"
    assert runtime_f._seed1_next(point, phase) == (
        "cancel", group["cancellation_control"])
    controls["cancel"] = SimpleNamespace(state="CANCELLED_UNVERIFIED")
    assert runtime_f._seed1_next(point, phase) == (
        "health", group["health_control"])
    controls["health"] = SimpleNamespace(state="COMPLETE")
    assert runtime_f._seed1_next(point, phase) is None


def test_health_not_before_wait_cancellation_spends_no_invocation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = SimpleNamespace(
        state=lambda: "RUNNING", header=lambda: {"run_id": "run"})
    claimed: list[str] = []

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def interruptible_backoff(self, deadline):
            assert deadline == 123.5
            return False

        def recover_and_start(self, stage):
            claimed.append(stage)
            return 0, 1

    monkeypatch.setattr(runtime_f, "_master", lambda _point: ({}, "a" * 64))
    monkeypatch.setattr(runtime_f, "_public_inputs", lambda _point: (object(), b"k"))
    monkeypatch.setattr(
        runtime_f, "_preclaim_f_phase_index",
        lambda *_args: (runtime_f.ActiveFPhase(
            {"plan_key": "F_SEED_1"}, "b" * 64, ()), {}))
    monkeypatch.setattr(runtime_f, "_pending_health_not_before", lambda _point: 123.5)
    monkeypatch.setattr(runtime_f, "DurableExecutor", FakeExecutor)
    monkeypatch.setattr(
        runtime_f, "runtime_position",
        lambda _point: runtime_f.RuntimePosition("F", "F_SEED_1"))
    factories: list[str] = []
    outcome = runtime_f.run_stage_f_invocation(
        point, object(),
        transport_factory=lambda *_args: factories.append("factory"),
        cancellation=object())
    assert claimed == [] and factories == []
    assert outcome.retry_not_before == 123.5
    assert outcome.active_plan_key == "F_SEED_1"


@pytest.mark.parametrize("health_attempted", (False, True))
def test_pending_health_orders_around_active_model_resource_recovery(
        monkeypatch: pytest.MonkeyPatch, health_attempted: bool) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_d
    from scripts.analyst_benchmark import c0b2_runtime_f_namespace as namespace

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE runtime_controls(control_id TEXT,evidence_json TEXT)")
    conn.execute("CREATE TABLE attempts(control_id TEXT)")
    conn.execute("INSERT INTO runtime_controls VALUES(?,?)", (
        "cancel", canonical_json({"cancel_attempt_id": "a" * 64})))
    if health_attempted:
        conn.execute("INSERT INTO attempts VALUES('health')")
    point = SimpleNamespace(
        conn=conn, state=lambda: "RUNNING",
        header=lambda: {"run_id": "run"})
    health = {
        "control_id": "health", "candidate_id": "candidate",
        "request_sha256": "request",
    }
    phase = runtime_f.ActiveFPhase({
        "plan_key": "F_SEED_1", "groups": [{
            "candidate_id": "candidate",
            "cancellation_control": {"control_id": "cancel"},
            "health_control": health,
        }],
    }, "b" * 64, ())
    spec = SimpleNamespace(
        expected_model="active", worksheet="v1",
        payload={"options": {"num_predict": 1, "num_ctx": 2}})
    actions: list[str] = []
    obligations = iter((("health", health), None))

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def interruptible_backoff(self, _deadline):
            return True

        def recover_and_start(self, _stage, *, post_recovery_guard):
            post_recovery_guard()
            return 0, 1

        def _f_context_recovery_owner(self):
            return None

        def _resource_obligation(self):
            return SimpleNamespace(model="active", failures=6)

        def run_cancellation_health(self, *_args, **_kwargs):
            actions.append("health")
            return executor_module.ExecutionResult("ACCEPTED")

        def run_resource_probe(self, *_args, **_kwargs):
            actions.append("resource")
            return executor_module.ExecutionResult("PAUSED_RESOURCE")

    monkeypatch.setattr(runtime_f, "_master", lambda _point: ({}, "a" * 64))
    monkeypatch.setattr(runtime_f, "_public_inputs", lambda _point: (object(), b"k"))
    monkeypatch.setattr(
        runtime_f, "_preclaim_f_phase_index", lambda *_args: (phase, {}))
    monkeypatch.setattr(runtime_f, "_pending_health_not_before", lambda _point: None)
    monkeypatch.setattr(runtime_f, "_active_f_phase", lambda *_args, **_kw: phase)
    monkeypatch.setattr(runtime_f, "_preflight_specs", lambda *_args: ())
    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census", lambda _point: None)
    monkeypatch.setattr(namespace, "validate_active_f_namespace", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_f, "DurableExecutor", FakeExecutor)
    monkeypatch.setattr(runtime_f, "_seed1_next", lambda *_args: next(obligations))
    monkeypatch.setattr(
        runtime_f, "resolve_f_seed1_control",
        lambda *_args, **_kwargs: SimpleNamespace(
            request_spec=spec, source_chunk="source"))
    monkeypatch.setattr(runtime_f, "request_spec_hash", lambda _spec: "request")
    monkeypatch.setattr(
        runtime_f, "_attempt_number",
        lambda *_args, **kwargs: (1, kwargs.get("first_class", "preflight_probe")))
    monkeypatch.setattr(c0b2_runtime_d, "_resource_probe_spec", lambda *_args: spec)
    monkeypatch.setattr(
        runtime_f, "runtime_position",
        lambda _point: runtime_f.RuntimePosition("F", "F_SEED_1"))

    outcome = runtime_f.run_stage_f_invocation(
        point, object(), transport_factory=lambda *_args: object())
    assert actions == (["resource"] if health_attempted else ["health", "resource"])
    assert outcome.outcome == "PAUSED_RESOURCE"


def test_corrupt_preclaim_namespace_sends_no_transport_or_invocation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_namespace as namespace

    calls: list[str] = []
    point = SimpleNamespace(header=lambda: {"run_id": "run"})
    monkeypatch.setattr(runtime_f, "_master", lambda _point: ({}, "a" * 64))
    monkeypatch.setattr(runtime_f, "_public_inputs", lambda _point: (object(), b"k"))

    def invalid(_point, *, strict_controls=False):
        assert not strict_controls
        calls.append("namespace")
        raise ImmutableViolation("active F namespace changed")

    monkeypatch.setattr(namespace, "validate_active_f_namespace", invalid)
    monkeypatch.setattr(
        runtime_f, "DurableExecutor",
        lambda *_args, **_kwargs: calls.append("executor"))
    with pytest.raises(ImmutableViolation, match="namespace changed"):
        runtime_f.run_stage_f_invocation(
            point, object(),
            transport_factory=lambda *_args: calls.append("transport"))
    assert calls == ["namespace"]


def test_preflight_uses_only_the_activated_qualifier_model() -> None:
    phase = runtime_f.ActiveFPhase(
        {"candidates": [{"model": "inactive"}, {"model": "qualifier"}]},
        "a" * 64,
        ({"model": "qualifier"},),
    )
    header = {
        "ollama_version": "1.2.3",
        "model_digests": {"inactive": "1" * 64, "qualifier": "2" * 64},
    }
    specs = runtime_f._preflight_specs(header, phase)
    assert [(kind, model) for kind, model, _spec in specs] == [
        ("version", "__server__"), ("tags", "__server__"),
        ("show", "qualifier"),
    ]


def _phased_f_executor(
        key: str, models: list[str], activated_indexes: list[int],
) -> tuple[DurableExecutor, dict[str, object], list[dict[str, str]]]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(f"""
        CREATE TABLE runtime_cursor(id INTEGER PRIMARY KEY,active_stage TEXT,
          active_plan_key TEXT,updated REAL);
        INSERT INTO runtime_cursor VALUES(1,'F','{key}',1.0);
        CREATE TABLE plan_activations(plan_key TEXT PRIMARY KEY,
          activation_hash TEXT,activation_json TEXT,created REAL);
        CREATE TABLE phase_work_registry(work_id TEXT PRIMARY KEY,
          plan_key TEXT,activation_group_id TEXT);
        CREATE TABLE work_items(work_id TEXT PRIMARY KEY,stage TEXT,cell_id TEXT,
          request_hash TEXT,state TEXT,accepted_attempt_id TEXT);
        CREATE TABLE model_backoff(model TEXT PRIMARY KEY,failures INTEGER,
          retry_not_before REAL,updated REAL);
    """)
    groups = [f"{index + 1:x}" * 64 for index in range(len(models))]
    work = [{
        "work_id": f"{index + 4:x}" * 64,
        "activation_group_id": groups[index],
        "cell_id": f"{index + 7:x}" * 64,
        "request_sha256": f"{index + 10:x}" * 64,
        "model": model, "model_digest": f"{index + 13:x}" * 64,
    } for index, model in enumerate(models)]
    plan = {"plan_key": key,
            "groups": [{"group_id": group} for group in groups], "work": work}
    activated = [groups[index] for index in activated_indexes]
    activation = {
        "run_id": "run", "plan_key": key,
        "plan_sha256": sha256_json(plan), "state": "ACTIVATED",
        "activated_group_ids": activated,
    }
    conn.execute(
        "INSERT INTO plan_activations VALUES(?,?,?,1.0)",
        (key, sha256_json(activation), canonical_json(activation)))
    active_work = [work[index] for index in activated_indexes]
    for active in active_work:
        conn.execute(
            "INSERT INTO phase_work_registry VALUES(?,?,?)",
            (active["work_id"], key, active["activation_group_id"]))
        conn.execute(
            "INSERT INTO work_items VALUES(?,?,?,?, 'PENDING',NULL)",
            (active["work_id"], "F", active["cell_id"],
             active["request_sha256"]))
    point = SimpleNamespace(
        conn=conn, header=lambda: {
            "run_id": "run", "model_digests": {
                item["model"]: item["model_digest"] for item in work}})
    executor = object.__new__(DurableExecutor)
    executor.checkpoint = point
    executor._active_plan_raw = lambda _stage: (canonical_json(plan), True)
    return executor, plan, work


def test_executor_authorizes_only_registered_f_qualifier_work() -> None:
    executor, _plan, work = _phased_f_executor(
        "F_SEED_17", ["inactive", "qualifier"], [1])
    active = work[1]

    assert executor._stage_models("F") == {"qualifier"}
    with pytest.raises(CheckpointError, match="not unique"):
        executor._require_work_request_identity(WorkRequest(
            "F", work[0]["work_id"], "inactive",
            work[0]["request_sha256"], 1))
    executor._require_work_request_identity(WorkRequest(
        "F", active["work_id"], "qualifier", active["request_sha256"], 1))
    executor.invocation_stage = "F"
    executor.checkpoint.conn.execute(
        "INSERT INTO model_backoff VALUES('inactive',6,10.0,1.0)")
    assert executor._resource_obligation() is None
    executor.checkpoint.conn.execute(
        "INSERT INTO model_backoff VALUES('qualifier',6,20.0,1.0)")
    assert executor._resource_obligation().model == "qualifier"


@pytest.mark.parametrize(("key", "rotation"), [
    ("F_SEED_17", [2, 0, 1]),
    ("F_SEED_20260804", [1, 2, 0]),
])
def test_rotated_three_qualifier_activation_owns_preflight_and_work_order(
        key: str, rotation: list[int]) -> None:
    models = ["first", "second", "third"]
    executor, plan, _work = _phased_f_executor(key, models, rotation)
    active = executor._planned_items("F")
    expected = [models[index] for index in rotation]
    assert [item["model"] for item in active] == expected
    assert executor._stage_models("F") == set(models)
    phase = runtime_f.ActiveFPhase(plan, sha256_json(plan), tuple(active))
    header = {**executor.checkpoint.header(), "ollama_version": "1.2.3"}
    assert [model for kind, model, _spec in runtime_f._preflight_specs(header, phase)
            if kind == "show"] == expected


def test_post_recovery_guard_prevents_transition_and_invocation_claim(
        monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    events: list[str] = []
    mount = {"canonical_path": str(tmp_path), "mount_id": "1",
             "mountpoint": str(tmp_path), "fs_type": "tmpfs", "options": "rw",
             "st_dev": 1, "kernel": "test", "mergerfs_version": "none",
             "sqlite_version": sqlite3.sqlite_version, "sha256": "a" * 64}

    class Point:
        root, path, conn = tmp_path, tmp_path / "point.sqlite", object()

        def state(self):
            return "CANCELLED_PENDING_RESUME"

        def header(self):
            return {"mount": mount, "journal_mode": "WAL",
                    "filesystem_capability_sha256": "b" * 64,
                    "run_type": "public"}

        def recover(self):
            events.append("recover")
            return 1

        def transition(self, _state):
            events.append("transition")

        def claim_invocation(self, *_args, **_kwargs):
            events.append("claim")
            return 1

    lock = SimpleNamespace(held=True, root=tmp_path)
    executor = DurableExecutor(Point(), lock, lambda *_args: events.append("transport"))
    executor._active_plan_raw = lambda _stage: ("{}", False)
    executor._require_budget_ledger = lambda _header: None
    monkeypatch.setattr(executor_module, "revalidate_filesystem", lambda *_args: None)
    monkeypatch.setattr(
        executor_module, "verify_connection", lambda _conn: SimpleNamespace(ok=True))

    def blocked():
        events.append("guard")
        raise ImmutableViolation("recovered namespace changed")

    with pytest.raises(ImmutableViolation, match="namespace changed"):
        executor.recover_and_start("F", post_recovery_guard=blocked)
    assert events == ["recover", "guard"]


def test_tampered_f_nonpreflight_allowance_sends_no_transport_or_claim(
        tmp_path) -> None:
    events: list[str] = []
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE stage_limits(stage TEXT,hard_cap INTEGER);
        CREATE TABLE class_limits(stage TEXT,call_class TEXT,allowance INTEGER);
    """)
    limits = {stage: dict(values)
              for stage, values in public_runtime.PUBLIC_LIMITS.items()}
    limits["F"]["transport_orphan"] += 1
    for stage, values in limits.items():
        conn.execute("INSERT INTO stage_limits VALUES(?,?)", (
            stage, sum(values.values())))
        conn.executemany(
            "INSERT INTO class_limits VALUES(?,?,?)",
            ((stage, kind, value) for kind, value in values.items()),
        )
    header = {
        "limits": limits,
        "invocation_caps": executor_module.INVOCATION_CAPS,
        "cumulative_cap": public_runtime.PUBLIC_CUMULATIVE_CAP + 1,
        "run_type": "public",
    }

    class Point:
        root, path = tmp_path, tmp_path / "point.sqlite"

        def __init__(self):
            self.conn = conn

        def state(self):
            return "CANCELLED_PENDING_RESUME"

        def header(self):
            return header

        def recover(self):
            events.append("recover")
            return 1

        def transition(self, _state):
            events.append("transition")

        def claim_invocation(self, *_args, **_kwargs):
            events.append("claim")
            return 1

    lock = SimpleNamespace(held=True, root=tmp_path)
    executor = DurableExecutor(
        Point(), lock, lambda *_args: events.append("transport-send"),
        enforce_public_budget_contract=True)
    executor._active_plan_raw = lambda _stage: ("{}", False)
    with pytest.raises(ImmutableViolation, match="budget ledger"):
        executor.recover_and_start("F")
    assert events == []


def test_poisoned_transition_after_recovery_sends_no_transport_or_claim(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_namespace as namespace

    events: list[str] = []
    point = SimpleNamespace(header=lambda: {"run_id": "run"})
    phase = runtime_f.ActiveFPhase(
        {"plan_key": "F_SEED_20260804"}, "a" * 64, ())

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def interruptible_backoff(self, _deadline):
            return True

        def recover_and_start(self, _stage, *, post_recovery_guard):
            events.append("recover")
            post_recovery_guard()
            events.append("claim")
            return 0, 1

    def validate(_point, *, strict_controls=False):
        assert strict_controls
        events.append("strict-census")
        raise ImmutableViolation("F transition lineage changed")

    monkeypatch.setattr(runtime_f, "_master", lambda _point: ({}, "b" * 64))
    monkeypatch.setattr(runtime_f, "_public_inputs", lambda _point: (object(), b"k"))
    monkeypatch.setattr(
        runtime_f, "_preclaim_f_phase_index", lambda *_args: (phase, {}))
    monkeypatch.setattr(runtime_f, "_pending_health_not_before", lambda _point: None)
    monkeypatch.setattr(runtime_f, "DurableExecutor", FakeExecutor)
    monkeypatch.setattr(namespace, "validate_active_f_namespace", validate)

    def factory(*_args):
        events.append("factory")
        return lambda *_call: events.append("transport-send")

    with pytest.raises(ImmutableViolation, match="lineage changed"):
        runtime_f.run_stage_f_invocation(
            point, object(), transport_factory=factory)
    assert events == ["recover", "strict-census"]


def test_deferred_transport_constructs_once_on_first_contact() -> None:
    calls: list[str] = []

    class Transport:
        def __call__(self, request, _cancel):
            calls.append(request)
            return request

        def cancel_current(self):
            calls.append("cancel")

    deferred = runtime_f.DeferredTransport(
        lambda: calls.append("factory") or Transport())
    deferred.cancel_current()
    assert deferred("first", object()) == "first"
    assert deferred("second", object()) == "second"
    deferred.cancel_current()
    assert calls == ["factory", "first", "second", "cancel"]


@pytest.mark.parametrize("path", ("scored", "resource", "standard"))
@pytest.mark.parametrize(
    "failure", (executor_module.SafetyLimit, executor_module.ProvenanceFailure))
def test_operator_cancellation_precedes_executor_dispatch_failure(
        path, failure) -> None:
    cancelled: list[str] = []

    class Point:
        root = object()
        conn = None

        def cancel(self, attempt_id=None):
            cancelled.append(attempt_id)

        def pending_context_obligation(self, _stage):
            return None

        def backoff(self, model):
            return SimpleNamespace(
                model=model, failures=6 if path == "resource" else 0,
                retry_not_before=0.0)

        def precharge(self, **_kwargs):
            return True

        def header(self):
            pytest.fail("cancellation must precede terminal classification")

    point = Point()
    executor = object.__new__(DurableExecutor)
    executor.checkpoint = point
    executor.lock = SimpleNamespace(held=True, root=point.root)
    executor.cancellation = executor_module.CancellationController()
    executor.invocation_stage, executor.invocation_ordinal = "C", 1
    executor.current_attempt = None
    executor.clock = SimpleNamespace(crossed=lambda: False)
    executor._now = lambda: 0.0
    executor._require_budget_ledger = lambda _header: None
    executor._require_work_request_identity = lambda _request: None
    executor._require_context_probe_configuration = lambda *_args: None
    executor._require_preflight_complete = lambda _stage: None
    executor._require_f_scored_control_order = lambda _stage: None
    executor._control_resource_models = lambda _request: set()
    executor._resource_obligation = lambda: (
        SimpleNamespace(model="model", failures=6, retry_not_before=0.0)
        if path == "resource" else None)

    def transport(*_args):
        executor.cancellation.first_signal()
        raise failure("after cancel")

    executor.transport = transport
    if path == "scored":
        request = WorkRequest("C", "work", "model", "hash", 1, "scored")
        result = executor.run(request)
    elif path == "resource":
        identity = executor_module.resource_probe_id("C", 1, "model", "hash")
        request = executor_module.ControlRequest(
            "C", identity, "model", "hash", 1, "transport_orphan")
        result = executor.run_resource_probe(request)
    else:
        identity = executor_module.control_id(
            "C", 1, "version", executor_module.SERVER_CONTROL_MODEL)
        request = executor_module.ControlRequest(
            "C", identity, executor_module.SERVER_CONTROL_MODEL, "hash", 1)
        result = executor.run_control(request, kind="version")
    assert result.outcome == "CANCELLED_PENDING_RESUME"
    assert cancelled == [request.attempt_id]


@pytest.mark.parametrize("path", ("context", "planned_cancel", "health"))
@pytest.mark.parametrize(
    "failure", (executor_module.SafetyLimit, executor_module.ProvenanceFailure))
def test_operator_cancellation_precedes_shared_control_failure(
        monkeypatch: pytest.MonkeyPatch, path, failure) -> None:
    cancelled: list[str] = []
    point = SimpleNamespace(cancel=lambda attempt=None: cancelled.append(attempt))
    executor = SimpleNamespace(
        checkpoint=point, cancellation=executor_module.CancellationController(),
        invocation_stage="F", invocation_ordinal=1, current_attempt=None,
        _now=lambda: 1.0, _require_lock=lambda: None,
        _require_invocation_stage=lambda _stage: None,
        _require_preflight_complete=lambda _stage: None,
        _require_f_cancellation_ready=lambda _candidate: None,
        _resource_gate=lambda _model: None)
    executor._finish_operator_cancellation = lambda attempt: (
        DurableExecutor._finish_operator_cancellation(executor, attempt))

    def transport(*_args):
        executor.cancellation.first_signal()
        raise failure("after cancel")

    executor.transport = transport
    monkeypatch.setattr(runtime_common, "_precharge_control", lambda *_args: None)
    monkeypatch.setattr(runtime_common, "_context_trigger", lambda *_args: "work")
    monkeypatch.setattr(
        runtime_common, "_finish_public_failure",
        lambda *_args: pytest.fail("cancellation must precede terminal classification"))
    control = {"candidate_id": "candidate"}
    monkeypatch.setattr(
        runtime_common, "_active_control",
        lambda *_args: (SimpleNamespace(state="PENDING"), control, {}))
    request = executor_module.ControlRequest(
        "F", "control", "model", "hash", 1)
    if path == "context":
        result = runtime_common.run_context_probe(executor, request)
    elif path == "planned_cancel":
        result = runtime_common.run_cancellation_probe(executor, request)
    else:
        monkeypatch.setattr(
            runtime_common, "_cancelled_predecessor",
            lambda *_args: SimpleNamespace(
                not_before_utc="1970-01-01T00:00:00.000000Z"))
        result = runtime_common.run_cancellation_health(
            executor, request, cancelled_attempt_id="old",
            source="source", worksheet="v2", num_predict=1, num_ctx=2)
    assert result.outcome == "CANCELLED_PENDING_RESUME"
    assert cancelled == [request.attempt_id]


def test_planned_probe_observes_operator_cancel_during_blocked_headers(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe-owned event must also expose operator intent to real transport."""
    class Response:
        status_code = 200
        headers = {"Content-Type": "application/x-ndjson"}

        def __init__(self) -> None:
            self.closed = threading.Event()
            self.raw = SimpleNamespace(stream=lambda **_kwargs: iter(()))

        def close(self) -> None:
            self.closed.set()

    response = Response()

    class Session:
        trust_env = True
        max_redirects = 30

        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def request(self, *_args, **_kwargs):
            self.started.set()
            self.release.wait(1)
            return response

    payload = {
        "model": "model:stable", "messages": [{"role": "user", "content": "x"}],
        "stream": True, "format": schema_module.worksheet_schema("v2"),
        "options": dict(OPTIONS_C), "think": False, "keep_alive": "15m",
    }
    spec = transport_module.RequestSpec(
        kind="chat", payload=payload, worksheet="v2",
        expected_model="model:stable", expected_digest="a" * 64,
        cancel_on_first_content=True)
    session = Session()
    bounded = transport_module.BoundedOllamaTransport(
        lambda _request: spec, session=session)
    cancelled: list[str | None] = []
    planned = SimpleNamespace(state="PENDING", evidence_json=None)
    point = SimpleNamespace(cancel=lambda attempt=None: cancelled.append(attempt))
    owner = SimpleNamespace(
        checkpoint=point, cancellation=executor_module.CancellationController(),
        invocation_stage="F", invocation_ordinal=1, current_attempt=None,
        _require_lock=lambda: None, _require_invocation_stage=lambda _stage: None,
        _require_preflight_complete=lambda _stage: None,
        _require_f_cancellation_ready=lambda _candidate: None,
        _resource_gate=lambda _model: None, transport=bounded)
    owner._finish_operator_cancellation = lambda attempt: (
        DurableExecutor._finish_operator_cancellation(owner, attempt))
    monkeypatch.setattr(runtime_common, "_precharge_control", lambda *_args: None)
    monkeypatch.setattr(
        runtime_common, "_active_control",
        lambda *_args: (planned, {"candidate_id": "candidate"}, {}))
    monkeypatch.setattr(
        runtime_common, "_persist_planned_cancellation",
        lambda *_args: pytest.fail("operator cancel cannot become probe evidence"))
    request = executor_module.ControlRequest(
        "F", "control", "model:stable",
        transport_module.request_spec_hash(spec), 1)
    returned = threading.Event()

    def cancel_then_release() -> None:
        assert session.started.wait(1)
        owner.cancellation.first_signal()
        returned.wait(0.5)
        session.release.set()

    interrupter = threading.Thread(target=cancel_then_release)
    interrupter.start()
    started = time.monotonic()
    result = runtime_common.run_cancellation_probe(owner, request)
    elapsed = time.monotonic() - started
    returned.set()
    interrupter.join(1)
    assert result.outcome == "CANCELLED_PENDING_RESUME" and elapsed < 0.25
    assert cancelled == [request.attempt_id]
    assert planned.state == "PENDING" and planned.evidence_json is None
    assert response.closed.wait(1)
    assert transport_module._REQUEST_WORKER_SLOT.acquire(timeout=1)
    transport_module._REQUEST_WORKER_SLOT.release()


def test_pending_health_barrier_uses_exact_durable_timestamp(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = SimpleNamespace()
    plan = {"groups": [{
        "cancellation_control": {"control_id": "cancel"},
        "health_control": {"control_id": "health"},
    }]}
    records = {
        "cancel": SimpleNamespace(
            state="CANCELLED_UNVERIFIED",
            not_before_utc="2026-08-06T12:34:56.500000Z",
            plan_key="F_SEED_1", kind="cancellation_probe",
            control_sha256=sha256_json(plan["groups"][0]["cancellation_control"]),
            control_json=canonical_json(plan["groups"][0]["cancellation_control"])),
        "health": SimpleNamespace(
            state="PENDING", plan_key="F_SEED_1", kind="cancellation_health",
            control_sha256=sha256_json(plan["groups"][0]["health_control"]),
            control_json=canonical_json(plan["groups"][0]["health_control"])),
    }
    monkeypatch.setattr(
        runtime_f, "runtime_position",
        lambda _point: runtime_f.RuntimePosition("F", "F_SEED_1"))
    monkeypatch.setattr(
        runtime_f, "load_phase_plan",
        lambda *_args: ("parent", "digest", canonical_json(plan)))
    monkeypatch.setattr(
        runtime_common, "load_runtime_control",
        lambda _point, identity: records[identity])
    monkeypatch.setattr(
        runtime_common, "_bound_control",
        lambda _point, _key, _kind, control: control)
    assert runtime_f._pending_health_not_before(point) == 1786019696.5


def test_failure_terminal_reentry_has_no_quality_validation_or_transport(
        monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[str] = []

    class FakeLock:
        def __init__(self, root):
            self.root, self.held = root, True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    point = SimpleNamespace(
        state=lambda: "FAILED_SAFETY", header=lambda: {"run_id": "run"},
        usage=lambda: {"total": 7}, close=lambda: calls.append("close"))
    monkeypatch.setattr(runtime_f, "GlobalExecutionLock", FakeLock)
    monkeypatch.setattr(
        runtime_f.Checkpoint, "open",
        classmethod(lambda _cls, _path, _root: point))
    monkeypatch.setattr(
        runtime_f, "runtime_position",
        lambda _point: runtime_f.RuntimePosition("F", "F_SEED_1"))
    monkeypatch.setattr(public_runtime, "_checkpoint_path",
                        lambda _run_id, root: root / "run.sqlite")
    monkeypatch.setattr(
        "scripts.analyst_benchmark.c0b3_policy.require_checkpoint_header",
        lambda *_args: {})
    monkeypatch.setattr(public_runtime, "revalidate_source_pins",
                        lambda _header: calls.append("pins"))
    monkeypatch.setattr(public_runtime, "ensure_backup_receipt",
                        lambda _point, _lock: calls.append("backup"))
    monkeypatch.setattr(
        runtime_f, "validate_b4_terminal_owner",
        lambda *_args: calls.append("quality"))
    result = runtime_f.run_public_stage_f(
        "run", resume=True, benchmark_root=tmp_path,
        transport_factory=lambda *_args: calls.append("transport"))
    assert result["state"] == "FAILED_SAFETY"
    assert calls == ["pins", "backup", "close"]


def _finalizer_point(plan_key: str) -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript(f"""
        CREATE TABLE phase_aggregates(
          plan_key TEXT PRIMARY KEY, plan_hash TEXT, aggregate_hash TEXT,
          aggregate_json TEXT, created REAL);
        CREATE TABLE decisions(
          decision_id TEXT PRIMARY KEY, stage TEXT, parent_hash TEXT,
          aggregate_hash TEXT, activation TEXT, value_json TEXT, created REAL);
        CREATE TABLE run_state(id INTEGER PRIMARY KEY,state TEXT,updated REAL);
        INSERT INTO run_state VALUES(1,'RUNNING',0.0);
        CREATE TABLE runtime_cursor(
          id INTEGER PRIMARY KEY,active_stage TEXT,active_plan_key TEXT);
        INSERT INTO runtime_cursor VALUES(1,'F','{plan_key}');
        CREATE TABLE public_artifacts(
          artifact_id TEXT PRIMARY KEY,terminal TEXT,artifact_hash TEXT,
          artifact_json TEXT,created REAL);
    """)
    return SimpleNamespace(
        conn=conn,
        state=lambda: conn.execute("SELECT state FROM run_state").fetchone()[0],
        header=lambda: {"run_id": "run"})


def _assert_inconclusive_terminal(point, reason: str, owner_hash: str,
                                  aggregate_hash: str) -> str:
    assert point.state() == "INCONCLUSIVE"
    artifact_row = point.conn.execute(
        "SELECT artifact_hash,artifact_json FROM public_artifacts "
        "WHERE artifact_id='stage-f-result'").fetchone()
    artifact = json.loads(artifact_row[1])
    assert artifact == {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "F",
        "aggregate_sha256": aggregate_hash, "reason": reason,
    }
    completion = point.conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions "
        "WHERE decision_id='c0b2-completion'").fetchone()
    assert completion[:4] == ("F", owner_hash, aggregate_hash, "NOT_ACTIVATED")
    assert json.loads(completion[4]) == {
        "outcome": "INCONCLUSIVE", "artifact_sha256": artifact_row[0],
        "facts": {"deterministic_stop": True, "reason": reason},
    }
    return artifact_row[0]


def test_seed1_no_qualifier_persists_exact_terminal_and_calls_validator(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _finalizer_point("F_SEED_1")
    master_hash, seed1_hash, evidence_hash = "1" * 64, "2" * 64, "3" * 64
    evidence, decision = {"seed": 1}, {"qualifiers": []}
    calls: list[str] = []

    monkeypatch.setattr(runtime_f, "_seed1_inputs", lambda *_args: ({}, {}, {}))
    monkeypatch.setattr(
        runtime_f, "build_seed1_evidence_from_attempts",
        lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(
        runtime_f, "build_seed_activation_decision",
        lambda _master, _value: decision)

    def activate(_point, _evidence, _decision):
        assert (_evidence, _decision) == (evidence, decision)
        with runtime_f.runtime_transaction(point):
            runtime_f._finish_no_seed1(
                point, master_hash, seed1_hash, evidence_hash)
        return runtime_f.FLaterActivation(
            evidence_hash, "4" * 64, (), "no_seed1_qualifier")

    def validate(*_args):
        assert point.conn.in_transaction is False
        calls.append("validator")
        return _assert_inconclusive_terminal(
            point, "no_seed1_qualifier", seed1_hash, evidence_hash)

    monkeypatch.setattr(runtime_f, "activate_f_later_seeds", activate)
    monkeypatch.setattr(runtime_f, "validate_b4_terminal_owner", validate)
    result = runtime_f._finalize_seed1(point, {}, object())
    assert result.terminal_reason == "no_seed1_qualifier"
    assert calls == ["validator"]


def test_all_seed_finalizer_rebuilds_authority_inside_write_transaction(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    point = _finalizer_point("F_SEED_20260804")
    calls: list[str] = []

    def inside(name, value):
        assert point.conn.in_transaction
        calls.append(name)
        return value

    master = {"plans": [{"payload": {
        "plan_key": "F_SEED_20260804", "frozen": True}}]}
    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census",
                        lambda _point: inside("census", None))
    monkeypatch.setattr(
        runtime_f, "validate_seed_activation_owner",
        lambda *_args: inside("seed-owner", ({}, {}, "b" * 64)))
    monkeypatch.setattr(
        evidence, "all_seed_attempt_inputs",
        lambda *_args: inside("attempts", {}))
    aggregate = {"ranking": {"winner_candidate_id": None}}
    monkeypatch.setattr(
        runtime_f, "build_stage_f_aggregate_from_attempts",
        lambda *_args, **_kwargs: inside("aggregate", aggregate))
    provisional = {
        "outcome": "INCONCLUSIVE", "reason": "ranking_not_decisive"}
    monkeypatch.setattr(
        runtime_f, "build_provisional_decision",
        lambda _aggregate: inside("provisional", provisional))
    monkeypatch.setattr(
        runtime_f, "build_inconclusive_result",
        lambda reason, digest: {"terminal": "INCONCLUSIVE", "reason": reason,
                                "aggregate_sha256": digest})
    monkeypatch.setattr(
        runtime_f, "_terminal_artifact",
        lambda *_args, **_kwargs: inside("terminal", "c" * 64))
    monkeypatch.setattr(
        runtime_f, "validate_b4_terminal_owner",
        lambda *_args: inside("validator", "c" * 64))
    assert runtime_f._finalize_all_seeds(
        point, master, "d" * 64, object()) == "ranking_not_decisive"
    assert calls == [
        "census", "seed-owner", "attempts", "aggregate", "provisional",
        "terminal", "validator"]


def test_all_seed_no_qualifier_persists_exact_terminal_and_calls_validator(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    point = _finalizer_point("F_SEED_20260804")
    plan = {"plan_key": "F_SEED_20260804"}
    master = {"plans": [{"payload": plan}]}
    aggregate = {"ranking": {"winner_candidate_id": None}}
    aggregate_hash, owner_hash = sha256_json(aggregate), sha256_json(plan)
    provisional = {
        "outcome": "INCONCLUSIVE", "reason": "no_all_seed_qualifier"}
    calls: list[str] = []

    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census", lambda _point: None)
    monkeypatch.setattr(
        runtime_f, "validate_seed_activation_owner",
        lambda *_args: ({}, {}, "4" * 64))
    monkeypatch.setattr(evidence, "all_seed_attempt_inputs", lambda *_args: {})
    monkeypatch.setattr(
        runtime_f, "build_stage_f_aggregate_from_attempts",
        lambda *_args, **_kwargs: aggregate)
    monkeypatch.setattr(
        runtime_f, "build_provisional_decision", lambda _aggregate: provisional)

    def validate(*_args):
        assert point.conn.in_transaction
        calls.append("validator")
        return _assert_inconclusive_terminal(
            point, "no_all_seed_qualifier", owner_hash, aggregate_hash)

    monkeypatch.setattr(runtime_f, "validate_b4_terminal_owner", validate)
    assert runtime_f._finalize_all_seeds(
        point, master, "5" * 64, object()) == "no_all_seed_qualifier"
    assert calls == ["validator"]


def test_acceptance_finalizer_rebuilds_every_owner_inside_write_transaction(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    point = _finalizer_point("F_ACCEPTANCE")
    calls: list[str] = []

    def inside(name, value):
        assert point.conn.in_transaction
        calls.append(name)
        return value

    winner = "1" * 64
    f_aggregate = {
        "ranking": {"winner_candidate_id": winner},
        "candidates": [{"candidate_id": winner,
                        "cancellation_health": {"passed": True}}],
    }
    phase = runtime_f.ActiveFPhase(
        {"plan_key": "F_ACCEPTANCE"}, "2" * 64, ())
    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census",
                        lambda _point: inside("census", None))
    monkeypatch.setattr(
        evidence, "validate_final_f_owner",
        lambda *_args: inside(
            "f-owner", (f_aggregate, "3" * 64, {}, "4" * 64)))
    monkeypatch.setattr(
        runtime_f, "_validate_stored_d_owner",
        lambda _point: inside("d-owner", ("5" * 64, {})))
    monkeypatch.setattr(
        runtime_f, "_d50_source_aggregate",
        lambda *_args: inside("d-aggregate", {}))
    monkeypatch.setattr(
        evidence, "acceptance_attempt_inputs",
        lambda *_args: inside("attempts", {}))
    monkeypatch.setattr(
        runtime_f, "build_c44_scored_aggregate",
        lambda *_args, **_kwargs: inside("c44", {}))
    acceptance = {"passed": True}
    monkeypatch.setattr(
        runtime_f, "build_acceptance_aggregate",
        lambda *_args, **_kwargs: inside("acceptance", acceptance))
    artifact = {"terminal": "SELECTED"}
    monkeypatch.setattr(
        runtime_f, "build_final_result",
        lambda **_kwargs: inside("result", artifact))
    monkeypatch.setattr(runtime_f, "_decision_digest", lambda *_args: "6" * 64)
    monkeypatch.setattr(
        runtime_f, "_terminal_artifact",
        lambda *_args, **_kwargs: inside("terminal", "7" * 64))
    monkeypatch.setattr(
        runtime_f, "validate_b4_terminal_owner",
        lambda *_args: inside("validator", "7" * 64))
    assert runtime_f._finalize_acceptance(
        point, phase, {}, "8" * 64,
        SimpleNamespace(master_manifest_sha256="9" * 64)) == "SELECTED"
    assert calls == [
        "census", "f-owner", "d-owner", "d-aggregate", "attempts",
        "c44", "acceptance", "result", "terminal", "validator"]


def test_failed_acceptance_persists_exact_terminal_and_calls_validator(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence

    point = _finalizer_point("F_ACCEPTANCE")
    winner = "1" * 64
    f_aggregate = {
        "ranking": {"winner_candidate_id": winner},
        "candidates": [{"candidate_id": winner,
                        "cancellation_health": {"passed": True}}],
    }
    phase = runtime_f.ActiveFPhase(
        {"plan_key": "F_ACCEPTANCE"}, "2" * 64, ())
    acceptance = {"passed": False}
    aggregate_hash = sha256_json(acceptance)
    artifact = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "F",
        "aggregate_sha256": aggregate_hash,
        "reason": "complete_corpus_acceptance_failed",
    }
    calls: list[str] = []

    monkeypatch.setattr(runtime_f, "validate_b4_f_control_census", lambda _point: None)
    monkeypatch.setattr(
        evidence, "validate_final_f_owner",
        lambda *_args: (f_aggregate, "3" * 64, {}, "4" * 64))
    monkeypatch.setattr(
        runtime_f, "_validate_stored_d_owner", lambda _point: ("5" * 64, {}))
    monkeypatch.setattr(runtime_f, "_d50_source_aggregate", lambda *_args: {})
    monkeypatch.setattr(evidence, "acceptance_attempt_inputs", lambda *_args: {})
    monkeypatch.setattr(
        runtime_f, "build_c44_scored_aggregate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runtime_f, "build_acceptance_aggregate",
        lambda *_args, **_kwargs: acceptance)
    monkeypatch.setattr(runtime_f, "build_final_result", lambda **_kwargs: artifact)
    monkeypatch.setattr(runtime_f, "_decision_digest", lambda *_args: "6" * 64)

    def validate(*_args):
        assert point.conn.in_transaction
        calls.append("validator")
        return _assert_inconclusive_terminal(
            point, "complete_corpus_acceptance_failed",
            phase.plan_sha256, aggregate_hash)

    monkeypatch.setattr(runtime_f, "validate_b4_terminal_owner", validate)
    assert runtime_f._finalize_acceptance(
        point, phase, {}, "7" * 64,
        SimpleNamespace(master_manifest_sha256="8" * 64)) == "INCONCLUSIVE"
    assert calls == ["validator"]
