"""Hostile tests for the read-only Stage-F namespace census."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b2_runtime_f_evidence as evidence
from scripts.analyst_benchmark import c0b2_runtime_f_namespace as namespace
from scripts.analyst_benchmark.c0b2_checkpoint import (
    INVOCATION_CAPS, ImmutableViolation, canonical_json, sha256_json,
)
from scripts.analyst_benchmark.c0b2_runtime_common import _decision_digest, _rfc3339


def _activation(plan: dict[str, object], groups: list[str], *, run: str = "run",
                parent: str = "1" * 64, evidence_hash: str | None = "2" * 64
                ) -> dict[str, object]:
    return {
        "version": "c0b2-plan-activation-v1", "run_id": run,
        "budget_stage": "F", "plan_key": plan["plan_key"],
        "plan_sha256": sha256_json(plan),
        "parent_decision_sha256": parent, "state": "ACTIVATED",
        "activated_group_ids": groups, "evidence_sha256": evidence_hash,
    }


def _owner_point() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE plan_activations(plan_key TEXT,activation_hash TEXT,
          activation_json TEXT);
        CREATE TABLE decisions(decision_id TEXT,stage TEXT,parent_hash TEXT,
          aggregate_hash TEXT,activation TEXT,value_json TEXT);
    """)
    return SimpleNamespace(conn=conn, header=lambda: {"run_id": "run"})


def test_later_activation_preserves_valid_rotated_group_order() -> None:
    groups = [str(index) * 64 for index in (3, 4, 5)]
    plan = {"plan_key": "F_SEED_17", "groups": [
        {"group_id": group} for group in groups], "work": [
        {"work_id": str(index + 6) * 64, "activation_group_id": group,
         "cell_id": str(index + 10) * 64, "request_sha256": str(index + 13) * 64}
        for index, group in enumerate(groups)]}
    rotated = [groups[2], groups[0], groups[1]]
    value = _activation(plan, rotated)
    point = _owner_point()
    point.conn.execute("INSERT INTO plan_activations VALUES(?,?,?)", (
        plan["plan_key"], sha256_json(value), canonical_json(value)))
    _stored, work = namespace._activation_owner(point, plan)
    assert [row["activation_group_id"] for row in work] == rotated


def test_seed1_activation_binds_exact_parent_lineage() -> None:
    group = "3" * 64
    plan = {"plan_key": "F_SEED_1", "groups": [{"group_id": group}], "work": []}
    point = _owner_point()
    point.conn.execute(
        "INSERT INTO decisions VALUES('stage-d-selection','D',?,?,?,'{}')",
        ("4" * 64, "5" * 64, "ACTIVATED"))
    exact = _activation(
        plan, [group], parent=_decision_digest(point, "stage-d-selection"),
        evidence_hash=None)
    point.conn.execute("INSERT INTO plan_activations VALUES(?,?,?)", (
        "F_SEED_1", sha256_json(exact), canonical_json(exact)))
    namespace._activation_owner(point, plan)
    poison = {**exact, "parent_decision_sha256": "6" * 64}
    point.conn.execute("UPDATE plan_activations SET activation_hash=?,activation_json=?", (
        sha256_json(poison), canonical_json(poison)))
    with pytest.raises(ImmutableViolation, match="lineage"):
        namespace._activation_owner(point, plan)


def _work_namespace_point(plan: dict[str, object], activation: dict[str, object]
                          ) -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE phase_plans(plan_key TEXT,budget_stage TEXT);
        CREATE TABLE plan_activations(plan_key TEXT,activation_hash TEXT,
          activation_json TEXT);
        CREATE TABLE phase_work_registry(work_id TEXT,plan_key TEXT,
          activation_group_id TEXT);
        CREATE TABLE work_items(work_id TEXT,stage TEXT,cell_id TEXT,
          request_hash TEXT,state TEXT,accepted_attempt_id TEXT);
        CREATE TABLE runtime_controls(control_id TEXT,plan_key TEXT);
        CREATE TABLE attempts(stage TEXT,work_id TEXT,control_id TEXT);
    """)
    conn.execute("INSERT INTO phase_plans VALUES(?,'F')", (plan["plan_key"],))
    conn.execute("INSERT INTO plan_activations VALUES(?,?,?)", (
        plan["plan_key"], sha256_json(activation), canonical_json(activation)))
    for item in plan["work"]:
        conn.execute("INSERT INTO phase_work_registry VALUES(?,?,?)", (
            item["work_id"], plan["plan_key"], item["activation_group_id"]))
        conn.execute("INSERT INTO work_items VALUES(?,?,?,?,?,NULL)", (
            item["work_id"], "F", item["cell_id"], item["request_sha256"],
            "PENDING"))
    return SimpleNamespace(conn=conn, header=lambda: {"run_id": "run"})


@pytest.mark.parametrize("poison", ["rogue_work", "ownerless_attempt"])
def test_exact_work_namespace_rejects_unowned_rows(poison: str) -> None:
    group, work_id = "3" * 64, "4" * 64
    item = {"work_id": work_id, "activation_group_id": group,
            "cell_id": "5" * 64, "request_sha256": "6" * 64}
    plan = {"plan_key": "F_SEED_17", "groups": [{"group_id": group}],
            "work": [item]}
    activation = _activation(plan, [group])
    point = _work_namespace_point(plan, activation)
    if poison == "rogue_work":
        point.conn.execute("INSERT INTO work_items VALUES(?,?,?,?,?,NULL)", (
            "7" * 64, "F", "8" * 64, "9" * 64, "PENDING"))
    else:
        point.conn.execute("INSERT INTO attempts VALUES('F',NULL,NULL)")
    with pytest.raises(ImmutableViolation, match="work-item|attempt"):
        namespace._exact_work_namespace(
            point, {"F_SEED_17": plan}, ("F_SEED_17",),
            lambda *_args, **_kwargs: [], terminal=False)


def _progress_point(states: list[str], attempts: list[tuple[str, str]]) -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE work_items(work_id TEXT,state TEXT);
        CREATE TABLE attempts(work_id TEXT,state TEXT);
    """)
    conn.executemany("INSERT INTO work_items VALUES(?,?)",
                     [(str(index), state) for index, state in enumerate(states)])
    conn.executemany("INSERT INTO attempts VALUES(?,?)", attempts)
    return SimpleNamespace(conn=conn)


@pytest.mark.parametrize("controls", [
    {"c1": "PENDING", "x1": "PENDING", "h1": "PENDING",
     "c2": "COMPLETE", "x2": "PENDING", "h2": "PENDING"},
    {"c1": "COMPLETE", "x1": "PENDING", "h1": "COMPLETE",
     "c2": "PENDING", "x2": "PENDING", "h2": "PENDING"},
])
def test_seed1_progress_rejects_future_or_out_of_order_controls(
        controls: dict[str, str]) -> None:
    seed1 = {"groups": [
        {"candidate_id": "a", "context_control": {"control_id": "c1"},
         "cancellation_control": {"control_id": "x1"},
         "health_control": {"control_id": "h1"}},
        {"candidate_id": "b", "context_control": {"control_id": "c2"},
         "cancellation_control": {"control_id": "x2"},
         "health_control": {"control_id": "h2"}}],
        "work": [{"work_id": "0", "candidate_id": "a"},
                 {"work_id": "1", "candidate_id": "b"}]}
    point = _progress_point(["PENDING", "PENDING"], [])
    with pytest.raises(ImmutableViolation, match="prefix"):
        namespace._seed1_progress_prefix(point, seed1, controls)


def test_completed_context_requires_attempt_derived_evidence(
        monkeypatch: pytest.MonkeyPatch) -> None:
    context = {"control_id": "ctx", "payload_sha256": "1" * 64}
    group = {
        "context_control": context,
        "cancellation_control": {"control_id": "cancel"},
        "health_control": {"control_id": "health"},
    }
    monkeypatch.setattr(
        evidence, "_control_evidence", lambda *_args: ({"stored": True}, object()))
    monkeypatch.setattr(evidence, "_control_attempt_rows", lambda *_args: [
        ("attempt", 1, "preflight_probe", "ACCEPTED", "{}", "{}",
         "F", 1, "1" * 64, 1.0, 2.0)])
    monkeypatch.setattr(
        evidence, "_context_evidence", lambda *_args: {"stored": False})
    with pytest.raises(ImmutableViolation, match="context evidence changed"):
        namespace._completed_control_owners(
            SimpleNamespace(), {}, None, group,
            {"ctx": "COMPLETE", "health": "PENDING"})


def test_completed_health_requires_attempt_derived_evidence(
        monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = {"control_id": "cancel", "request_sha256": "1" * 64}
    health = {
        "control_id": "health", "request_sha256": "2" * 64,
        "source_doc_id": "doc", "chunk_index": 0,
    }
    group = {
        "candidate_id": "candidate",
        "context_control": {"control_id": "ctx"},
        "cancellation_control": cancel, "health_control": health,
    }
    seed1 = {"candidates": [{
        "candidate_id": "candidate", "chunk_chars": 4000, "worksheet": "v1",
        "num_predict": 10, "num_ctx": 100,
    }]}
    record = SimpleNamespace()

    def control_owner(_point, control, _state):
        if control["control_id"] == "cancel":
            return {"cancel_attempt_id": "attempt"}, record
        return {"stored": True}, record

    monkeypatch.setattr(evidence, "_control_evidence", control_owner)
    monkeypatch.setattr(evidence, "_control_attempt_rows", lambda _p, identity, _h: (
        [("attempt",)] if identity == "cancel" else [("health-attempt",)]))
    monkeypatch.setattr(evidence, "_validate_health_not_before", lambda *_args: None)
    monkeypatch.setattr(evidence, "_health_evidence", lambda *_args, **_kw: {
        "stored": False})
    document = SimpleNamespace(source_for=lambda *_args, **_kw: ("source", None))
    corpus = SimpleNamespace(by_id=lambda: {"doc": document})
    with pytest.raises(ImmutableViolation, match="health evidence changed"):
        namespace._completed_control_owners(
            SimpleNamespace(), seed1, corpus, group,
            {"ctx": "PENDING", "health": "COMPLETE"})


def test_pending_health_barrier_rejects_coherently_rehashed_far_future() -> None:
    control_id, attempt_id = "3" * 64, "4" * 64
    control = {"control_id": control_id, "health_not_before_ms": 2000}
    evidence_value = {
        "version": "c0b2-cancellation-observation-v1",
        "cancel_control_id": control_id, "cancel_attempt_id": attempt_id,
        "cancel_state": "CANCELLED_UNVERIFIED", "cancel_first_byte_seen": True,
        "cancel_elapsed_ms": 10,
    }
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(attempt_id TEXT,control_id TEXT,state TEXT,"
                 "metadata_json TEXT,updated REAL)")
    metadata = {"cancel_elapsed_ms": 10, "cancel_first_byte_seen": True,
                "owned_stream_cancelled": True}
    conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?)", (
        attempt_id, control_id, "CANCELLED_UNVERIFIED", canonical_json(metadata), 10.0))
    exact = _rfc3339(12.0)
    record = SimpleNamespace(
        evidence_json=canonical_json(evidence_value),
        evidence_sha256=sha256_json(evidence_value), not_before_utc=exact)
    namespace._cancellation_barrier(SimpleNamespace(conn=conn), control, record)
    record.not_before_utc = _rfc3339(9999.0)
    with pytest.raises(ImmutableViolation, match="barrier"):
        namespace._cancellation_barrier(SimpleNamespace(conn=conn), control, record)


@pytest.mark.parametrize("poison", ["obligation", "unknown_backoff", "infinite"])
def test_runtime_state_namespace_rejects_legacy_or_unbounded_state(poison: str) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE context_obligations(stage TEXT);
        CREATE TABLE model_backoff(model TEXT,failures INTEGER,
          retry_not_before REAL,updated REAL);
    """)
    if poison == "obligation":
        conn.execute("INSERT INTO context_obligations VALUES('F')")
    elif poison == "unknown_backoff":
        conn.execute("INSERT INTO model_backoff VALUES('rogue',1,10.0,1.0)")
    else:
        conn.execute("INSERT INTO model_backoff VALUES('known',1,?,1.0)",
                     (float("inf"),))
    point = SimpleNamespace(conn=conn, header=lambda: {
        "model_digests": {"known": "1" * 64}})
    with pytest.raises(ImmutableViolation, match="obligation|backoff|finite"):
        namespace._runtime_state_namespace(point)


def test_runtime_state_namespace_allows_recovery_failure_seven() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE context_obligations(stage TEXT);
        CREATE TABLE model_backoff(model TEXT,failures INTEGER,
          retry_not_before REAL,updated REAL);
        INSERT INTO model_backoff VALUES('known',7,301.0,1.0);
    """)
    namespace._runtime_state_namespace(SimpleNamespace(
        conn=conn, header=lambda: {"model_digests": {"known": "1" * 64}}))


def test_public_budget_namespace_rejects_coherent_allowance_poison() -> None:
    from scripts.analyst_benchmark.c0b2_runtime import (
        PUBLIC_CUMULATIVE_CAP, PUBLIC_LIMITS,
    )

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE stage_limits(stage TEXT,hard_cap INTEGER);
        CREATE TABLE class_limits(stage TEXT,call_class TEXT,allowance INTEGER);
    """)
    limits = {stage: dict(values) for stage, values in PUBLIC_LIMITS.items()}
    limits["F"]["transport_orphan"] += 1
    for stage, values in limits.items():
        conn.execute("INSERT INTO stage_limits VALUES(?,?)", (
            stage, sum(values.values())))
        conn.executemany(
            "INSERT INTO class_limits VALUES(?,?,?)",
            ((stage, kind, value) for kind, value in values.items()),
        )
    header = {
        "run_type": "public", "limits": limits,
        "cumulative_cap": PUBLIC_CUMULATIVE_CAP + 1,
        "invocation_caps": INVOCATION_CAPS,
    }
    point = SimpleNamespace(conn=conn, header=lambda: header)
    with pytest.raises(ImmutableViolation, match="implementation contract"):
        namespace._public_budget_namespace(point)


def _partial_attempt_point(states: list[str], *, created: float = 2.0
                           ) -> tuple[SimpleNamespace, dict[str, str]]:
    work_id, request = "3" * 64, "4" * 64
    work = {"work_id": work_id, "plan_key": "F_SEED_1",
            "cell_id": "5" * 64, "request_sha256": request}
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE work_items(work_id TEXT,stage TEXT,cell_id TEXT,
          request_hash TEXT,state TEXT,accepted_attempt_id TEXT);
        CREATE TABLE attempts(attempt_id TEXT,attempt_no INTEGER,call_class TEXT,
          state TEXT,response TEXT,metadata_json TEXT,request_hash TEXT,stage TEXT,
          invocation_ordinal INTEGER,created REAL,updated REAL,work_id TEXT);
        CREATE TABLE invocations(stage TEXT,ordinal INTEGER,created REAL);
        INSERT INTO invocations VALUES('F',1,1.0);
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_1',0.0);
        CREATE TABLE events(kind TEXT,created REAL);
    """)
    conn.execute("INSERT INTO work_items VALUES(?,?,?,?,?,NULL)",
                 (work_id, "F", work["cell_id"], request, "PENDING"))
    metadata = canonical_json({"done_reason": "stop", "prompt_eval_count": 1,
                               "tools_empty": True, "images_empty": True,
                               "unknown_message_fields_empty": True})
    for index, state in enumerate(states, 1):
        conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            evidence.stable_attempt_id(work_id, index), index,
            "scored" if index == 1 else "schema_retry", state, "{}", metadata,
            request, "F", 1, created + index, created + index + 0.5, work_id))
    return SimpleNamespace(conn=conn), work


@pytest.mark.parametrize("states", [["ACCEPTED"],
                                     ["SCHEMA_INVALID", "SCHEMA_INVALID"]])
def test_pending_work_rejects_terminal_attempt_history(states: list[str]) -> None:
    point, work = _partial_attempt_point(states)
    with pytest.raises(ImmutableViolation, match="pending F work"):
        evidence._work_attempt_evidence(point, work, terminal_required=False)


def test_work_attempt_rejects_infinite_timestamp() -> None:
    point, work = _partial_attempt_point(["ACCEPTED"], created=float("inf"))
    point.conn.execute("DELETE FROM plan_activations")
    point.conn.executemany("INSERT INTO plan_activations VALUES(?,?)", [
        ("F_SEED_1", 0.0), ("F_SEED_17", 0.5),
        ("F_SEED_20260804", 0.5), ("F_ACCEPTANCE", 0.75)])
    point.conn.execute(
        "INSERT INTO events VALUES('F_SEED_CURSOR_TRANSITION',0.6)")
    work["plan_key"] = "F_ACCEPTANCE"
    with pytest.raises(ImmutableViolation, match="finite real timestamp"):
        evidence._work_attempt_evidence(point, work, terminal_required=False)


def test_phase_windows_reject_infinite_transition_marker() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE plan_activations(plan_key TEXT,created REAL);
        INSERT INTO plan_activations VALUES('F_SEED_1',0.0);
        INSERT INTO plan_activations VALUES('F_SEED_17',1.0);
        INSERT INTO plan_activations VALUES('F_SEED_20260804',1.0);
        CREATE TABLE events(kind TEXT,created REAL);
    """)
    conn.execute("INSERT INTO events VALUES('F_SEED_CURSOR_TRANSITION',?)",
                 (float("inf"),))
    with pytest.raises(ImmutableViolation, match="finite real timestamp"):
        evidence._phase_windows(SimpleNamespace(conn=conn), {
            "F_SEED_1": {}, "F_SEED_17": {}, "F_SEED_20260804": {}})
