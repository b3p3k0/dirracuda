"""Hostile read-only checks for Stage-F runtime evidence ownership."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence
from scripts.analyst_benchmark import c0b2_executor, c0b2_runtime_d, c0b2_transport
from scripts.analyst_benchmark.c0b2_checkpoint import (
    INVOCATION_CAPS, ImmutableViolation, canonical_json, sha256_json,
)


def _namespace_point() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_plans(plan_key TEXT,budget_stage TEXT);
        CREATE TABLE invocations(stage TEXT);
        CREATE TABLE attempts(stage TEXT);
        CREATE TABLE phase_aggregates(plan_key TEXT);
        CREATE TABLE decisions(decision_id TEXT,stage TEXT);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT,detail_json TEXT,created REAL);
        CREATE TABLE public_artifacts(artifact_id TEXT,artifact_json TEXT);
        CREATE TABLE acceptance_plan(id INTEGER);
    """)
    return SimpleNamespace(conn=conn)


def test_readonly_backup_facade_exposes_d_owner_work_state(tmp_path) -> None:
    database = tmp_path / "checkpoint.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute(
        "CREATE TABLE work_items(work_id TEXT,state TEXT,accepted_attempt_id TEXT)")
    conn.execute("INSERT INTO work_items VALUES('done','SUCCEEDED','attempt-1')")
    point = evidence._ReadonlyPoint(conn, {"run_id": "public"})

    assert point.path == database
    assert point.work("done") == ("SUCCEEDED", "attempt-1")
    with pytest.raises(evidence.CheckpointError, match="unknown work"):
        point.work("missing")


def test_seed_activation_restores_category_order_before_strict_validation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {"plan_key": "F_SEED_1"}
    stored = {"category_metrics": {
        "contact": 1, "demographic": 1, "financial": 1, "pii": 1}}
    raw, digest = canonical_json(stored), sha256_json(stored)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE phase_aggregates(plan_key TEXT,plan_hash TEXT,"
        "aggregate_hash TEXT,aggregate_json TEXT)")
    conn.execute("INSERT INTO phase_aggregates VALUES(?,?,?,?)", (
        "F_SEED_1", sha256_json(plan), digest, raw))
    point = SimpleNamespace(conn=conn)
    seen: list[list[str]] = []

    def strict(value, model):
        if model is evidence.Seed1Evidence:
            seen.append(list(value["category_metrics"]))
        return value

    monkeypatch.setattr(evidence, "typed", strict)
    monkeypatch.setattr(evidence, "seed1_inputs", lambda *_args: ({}, {}, {}))
    monkeypatch.setattr(
        evidence, "validate_seed1_evidence", lambda value, *_args, **_kwargs: value)
    monkeypatch.setattr(
        evidence, "build_seed_activation_decision",
        lambda *_args: {"activated_group_ids": []})
    monkeypatch.setattr(
        evidence, "connection_decision",
        lambda *_args: ({"activated_group_ids": []}, "d" * 64,
                        ("F", "m", digest, "NOT_ACTIVATED", "{}")))
    master = {"plans": [{"payload": plan}]}

    result = evidence.validate_seed_activation_owner(point, master, "m", object())
    assert result[1:] == ({"activated_group_ids": []}, "d" * 64)
    assert seen == [["pii", "financial", "contact", "demographic"]]


@pytest.mark.parametrize("table,values", [
    ("invocations", ("F",)),
    ("attempts", ("F",)),
    ("phase_aggregates", ("F_SEED_17",)),
    ("decisions", ("stage-f-forged", "F")),
    ("events", (None, "F_SEED_CURSOR_TRANSITION", "{}", 1.0)),
    ("public_artifacts", ("stage-f-result", '{"stage":"F"}')),
    ("acceptance_plan", (1,)),
])
def test_fresh_namespace_rejects_every_premature_owner(
        table: str, values: tuple[object, ...]) -> None:
    point = _namespace_point()
    placeholders = ",".join("?" for _ in values)
    if table == "events":
        point.conn.execute(
            "INSERT INTO events(seq,kind,detail_json,created) VALUES(?,?,?,?)", values)
    else:
        point.conn.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
    before = tuple(point.conn.iterdump())
    with pytest.raises(ImmutableViolation, match="namespace is not empty"):
        evidence.assert_f_namespace_empty_before_master(point)
    assert tuple(point.conn.iterdump()) == before


def test_seed1_replay_namespace_rejects_coherently_rehashed_future_decision() -> None:
    point = _namespace_point()
    point.conn.execute(
        "INSERT INTO decisions VALUES('stage-f-provisional-selection','F')")
    with pytest.raises(ImmutableViolation, match="future rows"):
        evidence.assert_seed1_replay_namespace(point)


def test_phase_windows_treat_unequal_paired_timestamps_as_one_transaction() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_plans(plan_key TEXT,plan_json TEXT);
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        CREATE TABLE events(kind TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_1',1.0);
        INSERT INTO plan_activations VALUES('F_SEED_17',10.0);
        INSERT INTO plan_activations VALUES('F_SEED_20260804',10.0001);
    """)
    point = SimpleNamespace(conn=conn)
    windows = evidence._phase_windows(point, {
        "F_SEED_1": {}, "F_SEED_17": {}, "F_SEED_20260804": {}})
    assert windows == [(1.0, 10.0001, "F_SEED_1"),
                       (10.0001, None, "F_SEED_17")]


def test_paired_f17_backup_exception_rejects_future_attempt_or_marker() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE runtime_cursor(active_plan_key TEXT,updated REAL,id INTEGER);
        INSERT INTO runtime_cursor VALUES('F_SEED_17',1.0,1);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT,detail_json TEXT,created REAL);
        CREATE TABLE attempts(work_id TEXT);
        CREATE TABLE phase_work_registry(work_id TEXT,plan_key TEXT);
        INSERT INTO phase_work_registry VALUES('future','F_SEED_20260804');
    """)
    evidence._validate_backup_transition(
        conn, {"run_id": "run"}, "a" * 64, "b" * 64,
        {}, {}, {})
    conn.execute("INSERT INTO attempts VALUES('future')")
    with pytest.raises(ImmutableViolation, match="future evidence"):
        evidence._validate_backup_transition(
            conn, {"run_id": "run"}, "a" * 64, "b" * 64,
            {}, {}, {})
    conn.execute("DELETE FROM attempts")
    body = {"forged": True}
    conn.execute(
        "INSERT INTO events(kind,detail_json,created) VALUES(?,?,?)",
        ("F_SEED_CURSOR_TRANSITION", canonical_json(body), 2.0))
    with pytest.raises(ImmutableViolation, match="future evidence"):
        evidence._validate_backup_transition(
            conn, {"run_id": "run"}, "a" * 64, "b" * 64,
            {}, {}, {})


def test_transition_self_hash_rejects_cross_domain_payload_hash() -> None:
    body = {
        "version": "c0b2-f-seed-cursor-transition-v1", "run_id": "run",
        "from_plan_key": "F_SEED_17", "to_plan_key": "F_SEED_20260804",
        "f_master_plan_sha256": "1" * 64,
        "seed_activation_decision_sha256": "2" * 64,
        "from_plan_sha256": "3" * 64, "from_activation_sha256": "4" * 64,
        "to_plan_sha256": "5" * 64, "to_activation_sha256": "6" * 64,
        "activated_from_group_ids": ["7" * 64],
        "activated_to_group_ids": ["8" * 64],
        "completed_from_work_ids": ["9" * 64],
        "completed_from_work_census_sha256": "a" * 64,
        "transitioned_at_utc": "2026-08-06T12:00:00Z",
    }
    exact = {**body, "transition_sha256": sha256_json(body)}
    evidence.typed(exact, evidence.FSeedCursorTransition)
    with pytest.raises(ValueError, match="self-hash"):
        evidence.typed(
            {**exact, "transition_sha256": sha256_json({"payload": body})},
            evidence.FSeedCursorTransition)


def _no_seed1_terminal_point(monkeypatch: pytest.MonkeyPatch) -> tuple[
        SimpleNamespace, dict[str, object], str, str, str]:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_plans(plan_key TEXT,budget_stage TEXT);
        INSERT INTO phase_plans VALUES('F_SEED_1','F');
        INSERT INTO phase_plans VALUES('F_SEED_17','F');
        INSERT INTO phase_plans VALUES('F_SEED_20260804','F');
        CREATE TABLE phase_aggregates(plan_key TEXT);
        INSERT INTO phase_aggregates VALUES('F_SEED_1');
        CREATE TABLE plan_activations(plan_key TEXT,activation_hash TEXT,
          activation_json TEXT);
        CREATE TABLE phase_work_registry(work_id TEXT,plan_key TEXT,
          activation_group_id TEXT);
        CREATE TABLE work_items(work_id TEXT,stage TEXT,cell_id TEXT,
          request_hash TEXT,state TEXT,accepted_attempt_id TEXT);
        CREATE TABLE attempts(stage TEXT,work_id TEXT,control_id TEXT);
        CREATE TABLE runtime_controls(control_id TEXT,plan_key TEXT);
        CREATE TABLE context_obligations(stage TEXT);
        CREATE TABLE model_backoff(model TEXT,failures INTEGER,
          retry_not_before REAL,updated REAL);
        CREATE TABLE stage_limits(stage TEXT,hard_cap INTEGER);
        CREATE TABLE class_limits(stage TEXT,call_class TEXT,allowance INTEGER);
        CREATE TABLE decisions(decision_id TEXT,stage TEXT,parent_hash TEXT,
          aggregate_hash TEXT,activation TEXT,value_json TEXT);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,
          detail_json TEXT);
        CREATE TABLE public_artifacts(artifact_id TEXT,terminal TEXT,
          artifact_hash TEXT,artifact_json TEXT);
        CREATE TABLE acceptance_plan(id INTEGER);
        CREATE TABLE runtime_cursor(id INTEGER,active_stage TEXT,active_plan_key TEXT);
        INSERT INTO runtime_cursor VALUES(1,'F','F_SEED_1');
    """)
    from scripts.analyst_benchmark.c0b2_runtime import (
        PUBLIC_CUMULATIVE_CAP, PUBLIC_LIMITS,
    )
    for stage, values in PUBLIC_LIMITS.items():
        conn.execute("INSERT INTO stage_limits VALUES(?,?)", (
            stage, sum(values.values())))
        conn.executemany(
            "INSERT INTO class_limits VALUES(?,?,?)",
            ((stage, kind, value) for kind, value in values.items()),
        )
    master_hash, plan_hash, seed1_hash = "1" * 64, "2" * 64, "3" * 64
    work_id, cell_id, request_hash, group_id = (
        "5" * 64, "6" * 64, "7" * 64, "a" * 64)
    seed1_plan = {"plan_key": "F_SEED_1", "budget_stage": "F",
                  "groups": [{"group_id": group_id}], "work": [{
                      "work_id": work_id, "activation_group_id": group_id,
                      "cell_id": cell_id, "request_sha256": request_hash}]}
    master = {"plans": [{"payload": seed1_plan},
                        {"payload": {"plan_key": "F_SEED_17"}},
                        {"payload": {"plan_key": "F_SEED_20260804"}}]}
    provisional = {
        "version": "stage-f-selection-v1", "stage": "F",
        "plan_sha256": master_hash, "aggregate_sha256": seed1_hash,
        "outcome": "INCONCLUSIVE", "reason": "no_seed1_qualifier",
        "selection": None,
    }
    artifact = {
        "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE", "stage": "F",
        "aggregate_sha256": seed1_hash, "reason": "no_seed1_qualifier",
    }
    artifact_hash = sha256_json(artifact)
    completion = {
        "outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
        "facts": {"deterministic_stop": True, "reason": "no_seed1_qualifier"},
    }
    conn.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?)", [
        ("stage-d-selection", "D", "8" * 64, "9" * 64, "ACTIVATED", "{}"),
        ("stage-f-seed-activation", "F", master_hash, seed1_hash,
         "NOT_ACTIVATED", "{}"),
        ("stage-f-provisional-selection", "F", master_hash, seed1_hash,
         "NOT_ACTIVATED", canonical_json(provisional)),
        ("c0b2-completion", "F", plan_hash, seed1_hash,
         "NOT_ACTIVATED", canonical_json(completion)),
    ])
    conn.execute(
        "INSERT INTO public_artifacts VALUES(?,?,?,?)",
        ("stage-f-result", "INCONCLUSIVE", artifact_hash,
         canonical_json(artifact)))
    from scripts.analyst_benchmark.c0b2_runtime_common import _decision_digest
    point = SimpleNamespace(
        conn=conn, state=lambda: "INCONCLUSIVE",
        header=lambda: {
            "run_id": "run", "model_digests": {}, "run_type": "public",
            "limits": PUBLIC_LIMITS, "cumulative_cap": PUBLIC_CUMULATIVE_CAP,
            "invocation_caps": INVOCATION_CAPS,
        })
    activation = {
        "version": "c0b2-plan-activation-v1", "run_id": "run",
        "budget_stage": "F", "plan_key": "F_SEED_1",
        "plan_sha256": sha256_json(seed1_plan),
        "parent_decision_sha256": _decision_digest(point, "stage-d-selection"),
        "state": "ACTIVATED", "activated_group_ids": [group_id],
        "evidence_sha256": None,
    }
    conn.execute("INSERT INTO plan_activations VALUES(?,?,?)", (
        "F_SEED_1", sha256_json(activation), canonical_json(activation)))
    conn.execute("INSERT INTO phase_work_registry VALUES(?,?,?)",
                 (work_id, "F_SEED_1", group_id))
    conn.execute("INSERT INTO work_items VALUES(?,?,?,?,?,NULL)",
                 (work_id, "F", cell_id, request_hash, "SUCCEEDED"))
    monkeypatch.setattr(
        evidence, "build_no_seed1_provisional_decision",
        lambda _master, _seed1: provisional)
    monkeypatch.setattr(evidence, "_work_attempt_evidence", lambda *_args, **_kw: [])
    point.master = master
    return point, provisional, master_hash, plan_hash, seed1_hash


def test_no_seed1_terminal_binds_exact_result_and_completion(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point, _provisional, master_hash, plan_hash, seed1_hash = \
        _no_seed1_terminal_point(monkeypatch)
    decision = {
        "qualifier_candidate_ids": [], "activated_group_ids": [],
        "inactive_group_ids": ["4" * 64],
    }
    assert evidence._validate_no_seed1_terminal_rows(
        point, point.master, master_hash, plan_hash, {}, seed1_hash, decision) == \
        point.conn.execute(
            "SELECT artifact_hash FROM public_artifacts").fetchone()[0]


@pytest.mark.parametrize("poison", ["relabeled_result", "selected_lineage"])
def test_no_seed1_terminal_rejects_coherently_rehashed_terminal_poison(
        monkeypatch: pytest.MonkeyPatch, poison: str) -> None:
    point, provisional, master_hash, plan_hash, seed1_hash = \
        _no_seed1_terminal_point(monkeypatch)
    if poison == "relabeled_result":
        selected = {"version": "c0b2-result-v1", "terminal": "SELECTED",
                    "stage": "F", "selection": {"forged": True}}
        point.conn.execute(
            "UPDATE public_artifacts SET terminal='SELECTED',artifact_hash=?,"
            "artifact_json=? WHERE artifact_id='stage-f-result'",
            (sha256_json(selected), canonical_json(selected)))
    else:
        selected = {**provisional, "outcome": "PROVISIONAL_SELECTED",
                    "reason": "single_qualifier", "selection": {"forged": True}}
        point.conn.execute(
            "UPDATE decisions SET activation='ACTIVATED',value_json=? "
            "WHERE decision_id='stage-f-provisional-selection'",
            (canonical_json(selected),))
    decision = {
        "qualifier_candidate_ids": [], "activated_group_ids": [],
        "inactive_group_ids": ["4" * 64],
    }
    with pytest.raises(ImmutableViolation, match="result|provisional"):
        evidence._validate_no_seed1_terminal_rows(
            point, point.master, master_hash, plan_hash, {}, seed1_hash, decision)


@pytest.mark.parametrize("plan_key,groups", [
    ("F_SEED_17", ["a" * 64]), ("F_ACCEPTANCE", []),
])
def test_plan_serial_order_covers_seed_and_acceptance_work(
        plan_key: str, groups: list[str]) -> None:
    work_ids = ["1" * 64, "2" * 64]
    group_id = groups[0] if groups else "b" * 64
    plan = {"plan_key": plan_key, "work": [
        {"work_id": work_id, "activation_group_id": group_id}
        for work_id in work_ids]}
    activation = {
        "version": "c0b2-plan-activation-v1", "run_id": "run",
        "budget_stage": "F", "plan_key": plan_key,
        "plan_sha256": sha256_json(plan),
        "parent_decision_sha256": "c" * 64, "state": "ACTIVATED",
        "activated_group_ids": groups, "evidence_sha256": "d" * 64,
    }
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE plan_activations(plan_key TEXT,activation_hash TEXT,
          activation_json TEXT);
        CREATE TABLE phase_work_registry(work_id TEXT,plan_key TEXT);
        CREATE TABLE attempts(work_id TEXT,created REAL,updated REAL);
    """)
    conn.execute(
        "INSERT INTO plan_activations VALUES(?,?,?)",
        (plan_key, sha256_json(activation), canonical_json(activation)))
    conn.executemany(
        "INSERT INTO phase_work_registry VALUES(?,?)",
        [(work_id, plan_key) for work_id in work_ids])
    conn.executemany(
        "INSERT INTO attempts VALUES(?,?,?)",
        [(work_ids[0], 1.0, 2.0), (work_ids[1], 2.0, 3.0)])
    point = SimpleNamespace(conn=conn)
    assert evidence.validate_plan_work_serial_order(point, plan) == tuple(work_ids)
    conn.execute(
        "UPDATE attempts SET created=1.5 WHERE work_id=?", (work_ids[1],))
    with pytest.raises(ImmutableViolation, match="serial order"):
        evidence.validate_plan_work_serial_order(point, plan)


@pytest.mark.parametrize("invoked,attempted", [(99.0, 100.0), (100.0, 99.0)])
def test_health_not_before_binds_invocation_and_first_attempt(
        invoked: float, attempted: float) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        CREATE TABLE runtime_controls(control_id TEXT,plan_key TEXT);
        INSERT INTO runtime_controls VALUES('health','F_SEED_1');
        CREATE TABLE attempts(stage TEXT,invocation_ordinal INTEGER,work_id TEXT,
          control_id TEXT,attempt_no INTEGER,call_class TEXT,created REAL);
    """)
    conn.execute("INSERT INTO invocations VALUES('F',2,?)", (invoked,))
    conn.execute("INSERT INTO attempts VALUES('F',2,NULL,'health',1,"
                 "'preflight_probe',?)", (attempted,))
    point = SimpleNamespace(conn=conn)
    cancel = (None, None, None, None, None, None, None, 1, None, 90.0, 91.0)
    health = [(None, None, None, None, None, None, None, 2, None,
               attempted, attempted + 1)]
    record = SimpleNamespace(not_before_utc="1970-01-01T00:01:40Z")
    with pytest.raises(ImmutableViolation, match="invocation order"):
        evidence._validate_health_not_before(
            point, record, cancel, "health", health)


def test_health_recovery_skips_only_preflight_prefix_invocations() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        INSERT INTO invocations VALUES('F',2,100.0),('F',3,102.0);
        CREATE TABLE runtime_controls(control_id TEXT,plan_key TEXT);
        INSERT INTO runtime_controls VALUES('health','F_SEED_1');
        CREATE TABLE attempts(stage TEXT,invocation_ordinal INTEGER,work_id TEXT,
          control_id TEXT,attempt_no INTEGER,call_class TEXT,created REAL);
        INSERT INTO attempts VALUES('F',2,NULL,'version',1,'preflight_probe',101.0);
        INSERT INTO attempts VALUES('F',3,NULL,'health',1,'preflight_probe',103.0);
    """)
    point = SimpleNamespace(conn=conn)
    cancel = (None, None, None, None, None, None, None, 1, None, 90.0, 91.0)
    health = [(None, None, None, None, None, None, None, 3, None, 103.0, 104.0)]
    record = SimpleNamespace(not_before_utc="1970-01-01T00:01:40Z")
    evidence._validate_health_not_before(
        point, record, cancel, "health", health)
    conn.execute(
        "INSERT INTO attempts VALUES('F',3,NULL,'resource',1,"
        "'transport_orphan',102.5)")
    with pytest.raises(ImmutableViolation, match="invocation order"):
        evidence._validate_health_not_before(
            point, record, cancel, "health", health)
    conn.execute("DELETE FROM attempts WHERE control_id='resource'")
    conn.execute(
        "INSERT INTO attempts VALUES('F',2,'work',NULL,1,'scored',101.5)")
    with pytest.raises(ImmutableViolation, match="invocation order"):
        evidence._validate_health_not_before(
            point, record, cancel, "health", health)


def _partial_census_point(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    seed1 = {"plan_key": "F_SEED_1", "groups": [], "candidates": [], "work": []}
    master = {"plans": [{"payload": seed1}]}
    monkeypatch.setattr(
        evidence, "_frozen_f_inputs",
        lambda _point: (master, "1" * 64, object(), b"key"))
    monkeypatch.setattr(
        c0b2_runtime_d, "_preflight_specs",
        lambda _header, _phase: [(kind, "model", kind)
                                 for kind in ("version", "tags", "show")])
    monkeypatch.setattr(c0b2_runtime_d, "_resource_probe_spec", lambda *_args: "r")
    monkeypatch.setattr(c0b2_transport, "request_spec_hash", lambda spec: str(spec))
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_plans(plan_key TEXT,plan_json TEXT);
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_1',0.0);
        CREATE TABLE events(kind TEXT,created REAL);
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        CREATE TABLE attempts(attempt_id TEXT,control_id TEXT,invocation_ordinal INTEGER,
          call_class TEXT,attempt_no INTEGER,request_hash TEXT,state TEXT,response TEXT,
          created REAL,updated REAL,work_id TEXT,stage TEXT);
        CREATE TABLE runtime_controls(control_id TEXT,plan_key TEXT,kind TEXT,state TEXT);
    """)
    return SimpleNamespace(
        conn=conn, header=lambda: {"model_digests": {"model": "d"}})


def test_f_control_census_allows_empty_and_partial_current_preflight(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _partial_census_point(monkeypatch)
    evidence.validate_b4_f_control_census(point)
    point.conn.execute("INSERT INTO invocations VALUES('F',1,1.0)")
    identity = c0b2_executor.control_id("F", 1, "version", "model")
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            evidence.stable_attempt_id(f"control:{identity}", 1), identity, 1,
            "preflight_probe", 1, "version", "RETRYABLE_TRANSPORT", None,
            2.0, 3.0, None, "F"))
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            evidence.stable_attempt_id(f"control:{identity}", 2), identity, 1,
            "transport_orphan", 2, "version", "ACCEPTED", "{}", 4.0, 5.0,
            None, "F"))
    evidence.validate_b4_f_control_census(point)
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "f" * 64, None, 1, "scored", 1, "x", "ACCEPTED", "{}", 4.0,
            5.0, "e" * 64, "F"))
    with pytest.raises(ImmutableViolation, match="preflight census"):
        evidence.validate_b4_f_control_census(point)


def test_f_control_census_allows_historical_ordered_prefix_after_resume(
        monkeypatch: pytest.MonkeyPatch) -> None:
    point = _partial_census_point(monkeypatch)
    point.conn.executemany(
        "INSERT INTO invocations VALUES('F',?,?)", [(1, 1.0), (2, 10.0)])
    identity = c0b2_executor.control_id("F", 1, "version", "model")
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            evidence.stable_attempt_id(f"control:{identity}", 1), identity, 1,
            "preflight_probe", 1, "version", "ACCEPTED", "{}", 2.0, 3.0,
            None, "F"))
    evidence.validate_b4_f_control_census(point)
    point.conn.execute("DELETE FROM attempts")
    wrong = c0b2_executor.control_id("F", 1, "tags", "model")
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            evidence.stable_attempt_id(f"control:{wrong}", 1), wrong, 1,
            "preflight_probe", 1, "tags", "ACCEPTED", "{}", 2.0, 3.0,
            None, "F"))
    with pytest.raises(ImmutableViolation, match="preflight census"):
        evidence.validate_b4_f_control_census(point)


def test_context_adjacency_is_strict_only_for_completed_group() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(work_id TEXT,control_id TEXT,state TEXT,"
                 "created REAL,updated REAL,attempt_no INTEGER,stage TEXT)")
    group = {"candidate_id": "c", "first_work_id": "w1",
             "context_control": {"control_id": "ctx"}}
    seed1 = {"groups": [group], "work": [
        {"candidate_id": "c", "work_id": "w1"},
        {"candidate_id": "c", "work_id": "w2"}]}
    point = SimpleNamespace(conn=conn)
    evidence._validate_context_adjacency(point, seed1, {}, set())
    conn.executemany("INSERT INTO attempts VALUES(?,?,?,?,?,?,?)", [
        ("w1", None, "ACCEPTED", 1.0, 2.0, 1, "F"),
        (None, "ctx", "ACCEPTED", 3.0, 3.1, 1, "F"),
        ("w2", None, "ACCEPTED", 4.0, 5.0, 1, "F"),
    ])
    evidence._validate_context_adjacency(point, seed1, {}, {"ctx"})
    conn.execute("UPDATE attempts SET created=2.5 WHERE work_id='w2'")
    with pytest.raises(ImmutableViolation, match="barrier|predates"):
        evidence._validate_context_adjacency(point, seed1, {}, {"ctx"})


def test_acceptance_attempt_inputs_preserve_frozen_work_order(
        monkeypatch: pytest.MonkeyPatch) -> None:
    work = [{"work_id": "1" * 64}, {"work_id": "2" * 64}]
    plan = {"plan_key": "F_ACCEPTANCE", "work": work}
    monkeypatch.setattr(evidence, "typed", lambda value, _model: value)
    observed: list[object] = []
    monkeypatch.setattr(
        evidence, "validate_plan_work_serial_order",
        lambda _point, value: observed.append(value))
    monkeypatch.setattr(
        evidence, "_work_attempt_evidence",
        lambda _point, row: [{"owner": row["work_id"]}])
    result = evidence.acceptance_attempt_inputs(SimpleNamespace(), plan)
    assert list(result) == [row["work_id"] for row in work]
    assert observed == [plan]


def test_final_f_owner_uses_exact_provisional_decision_row_digest(
        monkeypatch: pytest.MonkeyPatch) -> None:
    master_hash, aggregate_hash = "1" * 64, "2" * 64
    provisional = {
        "version": "stage-f-selection-v1", "stage": "F",
        "plan_sha256": master_hash, "aggregate_sha256": aggregate_hash,
        "outcome": "INCONCLUSIVE", "reason": "no_all_seed_qualifier",
        "selection": None,
    }
    row = ("F", master_hash, aggregate_hash, "NOT_ACTIVATED",
           canonical_json(provisional))
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE phase_aggregates(plan_key TEXT,aggregate_hash TEXT)")
    conn.execute("INSERT INTO phase_aggregates VALUES('F_SEED_20260804',?)",
                 (aggregate_hash,))
    conn.execute("CREATE TABLE decisions(decision_id TEXT,stage TEXT,parent_hash TEXT,"
                 "aggregate_hash TEXT,activation TEXT,value_json TEXT)")
    conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                 ("stage-f-provisional-selection", *row))
    monkeypatch.setattr(
        evidence, "validate_final_aggregate_owner",
        lambda *_args: ({"aggregate": True}, {}, {}, "d" * 64))
    monkeypatch.setattr(evidence, "build_provisional_decision", lambda _value: provisional)
    point = SimpleNamespace(conn=conn)
    actual = evidence.validate_final_f_owner(
        point, {}, master_hash, object())
    assert actual == ({"aggregate": True}, aggregate_hash, provisional,
                      sha256_json(("stage-f-provisional-selection", *row)))
    conn.execute("UPDATE decisions SET activation='ACTIVATED'")
    with pytest.raises(ImmutableViolation, match="provisional"):
        evidence.validate_final_f_owner(point, {}, master_hash, object())


@pytest.mark.parametrize("plan_key,invoked,attempted", [
    ("F_SEED_20260804", 15.0, 21.0), ("F_ACCEPTANCE", 25.0, 31.0),
])
def test_attempt_invocation_cannot_cross_f_phase_window(
        plan_key: str, invoked: float, attempted: float) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_1',0.0);
        INSERT INTO plan_activations VALUES('F_SEED_17',10.0);
        INSERT INTO plan_activations VALUES('F_SEED_20260804',10.0);
        INSERT INTO plan_activations VALUES('F_ACCEPTANCE',30.0);
        CREATE TABLE events(kind TEXT,created REAL);
        INSERT INTO events VALUES('F_SEED_CURSOR_TRANSITION',20.0);
    """)
    conn.execute("INSERT INTO invocations VALUES('F',1,?)", (invoked,))
    assert not evidence.attempt_in_invocation(
        SimpleNamespace(conn=conn), 1, attempted, plan_key)


def test_f17_census_invokes_full_work_attempt_authority(
        monkeypatch: pytest.MonkeyPatch) -> None:
    work_id, group_id = "1" * 64, "2" * 64
    item = {"work_id": work_id, "activation_group_id": group_id,
            "stage": "F", "cell_id": "3" * 64,
            "request_sha256": "4" * 64}
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_work_registry(work_id TEXT,plan_key TEXT);
        CREATE TABLE work_items(work_id TEXT,state TEXT,accepted_attempt_id TEXT,
          stage TEXT,cell_id TEXT,request_hash TEXT);
        CREATE TABLE attempts(attempt_id TEXT,work_id TEXT,state TEXT,attempt_no INTEGER);
    """)
    conn.execute("INSERT INTO phase_work_registry VALUES(?,'F_SEED_17')", (work_id,))
    conn.execute("INSERT INTO work_items VALUES(?,?,?,?,?,?)",
                 (work_id, "SUCCEEDED", "5" * 64, "F", "3" * 64, "4" * 64))
    conn.execute("INSERT INTO attempts VALUES(?,?,?,1)",
                 ("5" * 64, work_id, "ACCEPTED"))
    called: list[object] = []
    monkeypatch.setattr(
        evidence, "_work_attempt_evidence",
        lambda _point, work: called.append(work) or [{}])
    rows, ids = evidence.f17_terminal_census(
        SimpleNamespace(conn=conn), {"work": [item]}, [group_id])
    assert called == [item] and ids == [work_id] and rows[0]["state"] == "SUCCEEDED"
    monkeypatch.setattr(
        evidence, "_work_attempt_evidence",
        lambda *_args: (_ for _ in ()).throw(ImmutableViolation("later orphan")))
    with pytest.raises(ImmutableViolation, match="later orphan"):
        evidence.f17_terminal_census(
            SimpleNamespace(conn=conn), {"work": [item]}, [group_id])
