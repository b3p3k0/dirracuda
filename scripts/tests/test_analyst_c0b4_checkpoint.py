from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b4_checkpoint as checkpoint


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, synthetic=True):
    if synthetic:
        monkeypatch.setattr(
            checkpoint, "_validate_stored_artifact", lambda _kind, value: dict(value))
        monkeypatch.setattr(
            checkpoint, "validate_run_lineage", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            checkpoint, "_validate_new_attempt_identity", lambda *_args, **_kwargs: None)
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir(mode=0o700)
    db = parent_dir / "checkpoint.sqlite3"
    parent_header = {
        "run_id": "parent", "git_head": "source", "benchmark_protocol_id": "old",
        "protocol_sha256": "1" * 64, "policy_id": "old-policy",
        "policy_sha256": "2" * 64,
    }
    raw = checkpoint.canonical_json(parent_header)
    header_sha = checkpoint.sha256_json(parent_header)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE run_header(id INTEGER PRIMARY KEY,json TEXT,sha256 TEXT)")
    conn.execute("INSERT INTO run_header VALUES(1,?,?)", (raw, header_sha))
    conn.commit()
    conn.close()
    os.chmod(db, 0o600)
    snapshot = parent_dir / "snapshot.sqlite3"
    shutil.copyfile(db, snapshot)
    os.chmod(snapshot, 0o600)
    binding = dict(checkpoint.FROZEN_PARENT_BINDING)
    binding.update({
        "run_id": "parent", "source_commit": "source", "checkpoint_sha256": _file_sha(db),
        "backup_snapshot_sha256": _file_sha(snapshot), "run_header_sha256": header_sha,
        "benchmark_protocol_id": "old", "protocol_sha256": "1" * 64,
        "policy_id": "old-policy", "policy_sha256": "2" * 64,
    })
    monkeypatch.setattr(checkpoint, "FROZEN_PARENT_BINDING", binding)
    return db, snapshot, binding


def _header(run_id: str, binding: dict) -> dict:
    sha = "a" * 64
    mount = {
        "canonical_path": "test", "mount_id": "1", "mountpoint": "test",
        "fs_type": "ext4", "options": "rw", "st_dev": 1, "kernel": "test",
        "mergerfs_version": "test", "sqlite_version": sqlite3.sqlite_version,
    }
    mount["sha256"] = checkpoint.sha256_json(mount)
    return {
        "version": checkpoint.HEADER_VERSION, "run_type": "public_confirmation",
        "benchmark_protocol_id": checkpoint.PROTOCOL_ID, "policy_id": checkpoint.POLICY_ID,
        "policy_sha256": checkpoint.POLICY_SHA256, "protocol_sha256": sha,
        "parent_binding": binding, "ollama_endpoint": "http://127.0.0.1:11434",
        "ollama_version": "0.32.5", "filesystem_selected_mode": "DELETE",
        "git_head": "b" * 40, "declared_dirty_state_sha256": sha,
        "task_tree_sha256": sha, "fixture_sha256": sha, "master_manifest_sha256": sha,
        "schema_sha256": sha, "prompt_sha256": sha, "chunker_sha256": sha,
        "detector_sha256": sha, "generation_options_sha256": sha,
        "worktree_seal_sha256": sha, "filesystem_capability_sha256": sha,
        "model_digests": {"qwen3.6:27b":
            "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"},
        "mount": mount,
        "schema_version": checkpoint.SCHEMA_VERSION, "journal_mode": "DELETE",
        "cumulative_cap": checkpoint.CUMULATIVE_CAP, "run_id": run_id,
        "limits": checkpoint.LEDGER_LIMITS, "invocation_caps": checkpoint.INVOCATION_CAPS,
    }


def _initializer(point: checkpoint.C0B4Checkpoint) -> None:
    assert point.state() == "INITIALIZING"
    point.store_artifact("master_plan", "master", {"version": "test-master"})
    for lane in ("F72_17", "F72_20260804", "C44_1"):
        point.store_artifact("lane_plan", lane, {"version": "test-lane", "lane_id": lane})
    point.set_nonce_key(b"n" * 32)


def _create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    root = tmp_path / "child"
    seen = []

    def verifier(path, supplied):
        seen.append((path, supplied))
        assert not root.exists()

    point = checkpoint.C0B4Checkpoint.create(
        root, "child-run", header=_header("child-run", binding),
        parent_checkpoint=db, parent_snapshot=snapshot,
        parent_verifier=verifier, initializer=_initializer)
    return point, root, db, snapshot, seen


def test_creation_verifies_parent_before_child_and_publishes_prepared(tmp_path, monkeypatch):
    point, root, parent, snapshot, seen = _create(tmp_path, monkeypatch)
    try:
        assert len(seen) == 1
        assert point.state() == "PREPARED"
        assert point.path == root / "runs" / "child-run" / "checkpoint.sqlite3"
        assert stat.S_IMODE(point.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(point.path.parent.stat().st_mode) == 0o700
        assert dict(point.conn.execute("SELECT * FROM class_limits")) == checkpoint.LEDGER_LIMITS
        assert point.conn.execute("SELECT count(*) FROM artifacts").fetchone() == (4,)
        assert point.conn.execute("SELECT length(value) FROM protected_values").fetchone() == (32,)
        assert not list((root / "runs").glob(".c0b4-initializing-*"))
        assert _file_sha(parent) == checkpoint.FROZEN_PARENT_BINDING["checkpoint_sha256"]
        assert _file_sha(snapshot) == checkpoint.FROZEN_PARENT_BINDING["backup_snapshot_sha256"]
    finally:
        point.close()


def test_parent_failure_has_zero_child_side_effects(tmp_path, monkeypatch):
    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    root = tmp_path / "child"

    def reject(*_args):
        raise checkpoint.C0B4CheckpointError("no")

    with pytest.raises(checkpoint.C0B4CheckpointError):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", binding),
            parent_checkpoint=db, parent_snapshot=snapshot,
            parent_verifier=reject, initializer=_initializer)
    assert not root.exists()


def test_incomplete_initializer_is_not_published(tmp_path, monkeypatch):
    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    root = tmp_path / "child"

    with pytest.raises(checkpoint.C0B4CheckpointError, match="three lane"):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", binding),
            parent_checkpoint=db, parent_snapshot=snapshot,
            parent_verifier=lambda *_: None,
            initializer=lambda point: point.set_nonce_key(b"x" * 32))
    assert not (root / "runs" / "child-run").exists()


def test_hard_crash_complete_staging_is_promoted_without_regeneration(tmp_path, monkeypatch):
    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    root = tmp_path / "child"

    def hard_crash(_point):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", binding),
            parent_checkpoint=db, parent_snapshot=snapshot,
            parent_verifier=lambda *_: None, initializer=_initializer,
            pre_promotion_hook=hard_crash)
    staged = list((root / "runs").glob(".c0b4-initializing-*"))
    assert len(staged) == 1
    conn = sqlite3.connect(staged[0] / "checkpoint.sqlite3")
    before = (conn.execute("SELECT value FROM protected_values").fetchone()[0],
              conn.execute("SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0])
    conn.close()

    def must_not_regenerate(_point):
        raise AssertionError("complete staged artifacts must be recovered")

    point = checkpoint.C0B4Checkpoint.create(
        root, "child-run", header=_header("child-run", binding),
        parent_checkpoint=db, parent_snapshot=snapshot,
        parent_verifier=lambda *_: None, initializer=must_not_regenerate)
    try:
        assert point.state() == "PREPARED"
        after = (point.read_nonce_key(), point.conn.execute(
            "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0])
        assert after == before
        assert not list((root / "runs").glob(".c0b4-initializing-*"))
    finally:
        point.close()


def test_corrupt_prepared_staging_with_nonce_fails_closed(tmp_path, monkeypatch):
    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    root = tmp_path / "child"

    with pytest.raises(KeyboardInterrupt):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", binding),
            parent_checkpoint=db, parent_snapshot=snapshot,
            parent_verifier=lambda *_: None, initializer=_initializer,
            pre_promotion_hook=lambda _point: (_ for _ in ()).throw(KeyboardInterrupt()))
    staged = list((root / "runs").glob(".c0b4-initializing-*"))
    assert len(staged) == 1
    conn = sqlite3.connect(staged[0] / "checkpoint.sqlite3")
    conn.execute(
        "UPDATE artifacts SET json=? WHERE kind='lane_plan' AND owner_id='F72_17'",
        ('{"lane_id":"F72_17","version":"test-lane","corrupt":true}',))
    conn.commit()
    conn.close()

    with pytest.raises(checkpoint.C0B4CheckpointError, match="HI review"):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", binding),
            parent_checkpoint=db, parent_snapshot=snapshot,
            parent_verifier=lambda *_: None,
            initializer=lambda _point: pytest.fail("corrupt staging must not regenerate"))
    assert staged[0].exists()
    assert not (root / "runs" / "child-run").exists()


def test_header_rejects_other_family_and_extra_keys(tmp_path, monkeypatch):
    _db, _snapshot, binding = _parent(tmp_path, monkeypatch)
    header = _header("child-run", binding)
    header["version"] = "c0b3-run-header-v1"
    with pytest.raises(checkpoint.C0B4CheckpointError):
        checkpoint.validate_header(header)
    header = _header("child-run", binding)
    header["legacy"] = True
    with pytest.raises(checkpoint.C0B4CheckpointError):
        checkpoint.validate_header(header)


def test_no_replace_and_parent_drift_fail_closed(tmp_path, monkeypatch):
    point, root, parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.precharge(attempt_id="a1", owner_id="w1", call_class="scored",
                    invocation_ordinal=ordinal, request_sha256="a" * 64)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="one .* in flight"):
        point.precharge(attempt_id="a2", owner_id="w2", call_class="scored",
                        invocation_ordinal=ordinal, request_sha256="b" * 64)
    point.record_attempt("a1", "RAW_VALID", {"ok": True})
    with parent.open("ab") as stream:
        stream.write(b"drift")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="parent evidence changed"):
        point.claim_invocation()
    point.close()

    # Existing final storage can never be replaced by a new create.
    with pytest.raises(Exception):
        checkpoint.C0B4Checkpoint.create(
            root, "child-run", header=_header("child-run", checkpoint.FROZEN_PARENT_BINDING),
            parent_checkpoint=parent, parent_snapshot=_snapshot,
            parent_verifier=lambda *_: None, initializer=_initializer)


def test_readonly_status_has_no_side_effects(tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    path = point.path
    point.close()
    before = _file_sha(path)
    assert checkpoint.status_readonly(path)["state"] == "PREPARED"
    assert checkpoint.verify_readonly(path)["ok"] is True
    assert _file_sha(path) == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_attempt_finish_is_atomic_and_payload_reads_are_canonical(tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.precharge(attempt_id="a1", owner_id="w1", call_class="scored",
                    invocation_ordinal=ordinal, request_sha256="a" * 64)
    point.conn.execute(
        "CREATE TRIGGER reject_final BEFORE INSERT ON attempt_history "
        "WHEN NEW.state!='DISPATCHING' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        point.record_attempt("a1", "RAW_VALID", {"ok": True})
    assert point.conn.execute(
        "SELECT state,payload_json FROM attempts WHERE attempt_id='a1'").fetchone() == (
            "DISPATCHING", None)
    point.conn.execute("DROP TRIGGER reject_final")
    point.record_attempt("a1", "RAW_VALID", {"ok": True})
    point.conn.execute("UPDATE attempts SET payload_json=' {\"ok\":true}' WHERE attempt_id='a1'")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="noncanonical"):
        point.list_attempts("a1")
    point.close()


def test_operator_cancel_is_durable_and_budget_exhaustion_is_typed(
        tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.precharge(attempt_id="cancelled", owner_id="w1", call_class="scored",
                    invocation_ordinal=ordinal, request_sha256="a" * 64)
    point.record_attempt("cancelled", "CANCELLED", {"answered": False})
    attempt = point.list_attempts("cancelled")[0]
    assert attempt["state"] == "CANCELLED"
    assert [row[0] for row in point.conn.execute(
        "SELECT state FROM attempt_history WHERE attempt_id='cancelled' ORDER BY seq"
    )] == ["DISPATCHING", "CANCELLED"]
    assert checkpoint.verify_readonly(point.path)["ok"] is True

    point.conn.executemany(
        "INSERT INTO invocations VALUES(?,?)",
        ((value, 0.0) for value in range(2, checkpoint.INVOCATION_CAPS["total"] + 1)))
    with pytest.raises(checkpoint.C0B4BudgetError, match="invocation cap"):
        point.claim_invocation()
    assert point.state() == "RUNNING"
    point.close()

    ledger_tmp = tmp_path / "ledger"
    ledger_tmp.mkdir()
    point, _root, _parent, _snapshot, _seen = _create(ledger_tmp, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.conn.executemany(
        "INSERT INTO attempts VALUES(?,?,?,?,?,'RAW_VALID',NULL,0,0)",
        ((f"used-{value}", f"owner-{value}", "scored", ordinal, "a" * 64)
         for value in range(checkpoint.LEDGER_LIMITS["scored"])))
    with pytest.raises(checkpoint.C0B4BudgetError, match="call ledger"):
        point.precharge(
            attempt_id="over-budget", owner_id="w2", call_class="scored",
            invocation_ordinal=ordinal, request_sha256="b" * 64)
    assert not point.list_attempts("over-budget")
    point.close()


def test_cancellation_deadline_binds_to_durable_update_and_rejects_early_health(
        tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.precharge(
        attempt_id="cancelled", owner_id="cancel-control",
        call_class="preflight_control", invocation_ordinal=ordinal,
        request_sha256="a" * 64)
    monkeypatch.setattr(checkpoint.time, "time", lambda: 1_000.25)
    deadline = point.record_cancelled_attempt(
        "cancelled", first_byte_seen=True, cancel_elapsed_ms=15)
    cancel = point.list_attempts("cancelled")[0]
    cancel["payload_json"] = checkpoint.canonical_json(cancel.pop("payload"))
    assert cancel["updated"] == 1_000.25
    assert deadline == checkpoint._utc_timestamp(1_002.25)

    evidence = {"not_before_utc": deadline}
    checkpoint._validate_cancel_health_timing(
        cancel, [{"created": 1_002.25}], evidence)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="preceded"):
        checkpoint._validate_cancel_health_timing(
            cancel, [{"created": 1_002.249}], evidence)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="preceded"):
        checkpoint._validate_cancel_health_timing(
            cancel, [{"created": 1_002.25}],
            {"not_before_utc": checkpoint._utc_timestamp(1_002.26)})
    point.close()


def test_closed_artifact_validator_rejects_legacy_and_cross_kind_versions(monkeypatch):
    with pytest.raises(Exception):
        checkpoint._validate_stored_artifact(
            "result", {"version": "c0b3-result-v1"})
    monkeypatch.setattr(checkpoint, "validate_artifact", lambda value: dict(value))
    with pytest.raises(checkpoint.C0B4CheckpointError, match="kind/version"):
        checkpoint._validate_stored_artifact(
            "lane_aggregate", {"version": "c0b4-result-v1"})
    with pytest.raises(checkpoint.C0B4CheckpointError, match="kind/version"):
        checkpoint._validate_stored_artifact(
            "work_evidence", {"version": "c0b4-lane-aggregate-v1"})
    assert checkpoint._validate_stored_artifact(
        "lane_plan", {"version": "c0b4-lane-plan-v1"})["version"] == \
        "c0b4-lane-plan-v1"
    assert checkpoint._validate_stored_artifact(
        "lane_plan", {"version": "c0b4-acceptance-plan-v1"})["version"] == \
        "c0b4-acceptance-plan-v1"


def test_real_master_plan_stores_without_runtime_derivation_arguments(
        tmp_path, monkeypatch):
    from scripts.analyst_benchmark import c0b2_plan, c0b4_schema
    from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
    from scripts.analyst_benchmark.c0b4_plan import build_master_plan

    db, snapshot, binding = _parent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        checkpoint, "_validate_stored_artifact",
        lambda _kind, value: c0b4_schema.validate_artifact(value))
    manifest = c0b2_plan.build_master_manifest()
    corpus = load_public_corpus(
        c0b2_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=bytes(range(32)),
        protocol_sha256="a" * 64)

    def initialize(point):
        point.store_artifact("master_plan", "master", master)
        envelopes = [*master["lane_plans"], master["acceptance_template"]]
        for envelope in envelopes:
            lane = envelope["payload"]
            point.store_artifact("lane_plan", lane["lane_id"], lane)
        point.set_nonce_key(bytes(range(32)))

    point = checkpoint.C0B4Checkpoint.create(
        tmp_path / "child", "child-run", header=_header("child-run", binding),
        parent_checkpoint=db, parent_snapshot=snapshot,
        parent_verifier=lambda *_: None, initializer=initialize)
    try:
        assert point.read_artifact("master_plan", "master") == master
    finally:
        point.close()


def _real_lineage_db(protocol_sha256="a" * 64, nonce=bytes(range(32))):
    from scripts.analyst_benchmark import c0b2_plan
    from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
    from scripts.analyst_benchmark.c0b4_plan import build_master_plan

    manifest = c0b2_plan.build_master_manifest()
    corpus = load_public_corpus(
        c0b2_plan.master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=nonce,
        protocol_sha256=protocol_sha256)
    header = {
        "policy_id": checkpoint.POLICY_ID,
        "policy_sha256": checkpoint.POLICY_SHA256,
        "protocol_sha256": protocol_sha256,
        "parent_binding": master["parent_binding"],
        "master_manifest_sha256": manifest.sha256,
        "ollama_version": "0.32.5",
        "model_digests": {"qwen3.6:27b":
            "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"},
    }
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE artifacts(kind TEXT,owner_id TEXT,sha256 TEXT,json TEXT);
        CREATE TABLE protected_values(name TEXT,value BLOB,sha256 TEXT);
        CREATE TABLE parent_files(id INTEGER,db_path TEXT,snapshot_path TEXT);
        CREATE TABLE attempts(attempt_id TEXT,owner_id TEXT,call_class TEXT,
            invocation_ordinal INTEGER,request_sha256 TEXT,state TEXT,
            payload_json TEXT,created REAL,updated REAL);
        CREATE TABLE attempt_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id TEXT,state TEXT,payload_json TEXT,created REAL);
        CREATE TABLE invocations(ordinal INTEGER,created REAL);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,
            detail_json TEXT,created REAL);
        CREATE TABLE run_state(id INTEGER PRIMARY KEY,state TEXT,updated REAL);
        CREATE TABLE backup_receipts(anchor_sha256 TEXT);
    """)
    conn.execute("INSERT INTO run_state VALUES(1,'RUNNING',0)")
    conn.execute(
        "INSERT INTO protected_values VALUES('nonce_key',?,?)",
        (nonce, hashlib.sha256(nonce).hexdigest()))
    return conn, header, master


def _insert_real_plan_tree(conn, master, *, lane_master=None):
    owner = lane_master or master
    values = [("master_plan", "master", master)]
    for envelope in [*owner["lane_plans"], owner["acceptance_template"]]:
        values.append(("lane_plan", envelope["payload"]["lane_id"],
                       envelope["payload"]))
    conn.executemany(
        "INSERT INTO artifacts VALUES(?,?,?,?)",
        ((kind, owner_id, checkpoint.sha256_json(value),
          checkpoint.canonical_json(value)) for kind, owner_id, value in values))


def _insert_attempt(conn, attempt_id, owner_id, call_class, ordinal, request_sha,
                    state="RAW_VALID", payload=None):
    raw = checkpoint.canonical_json(payload) if payload is not None else None
    conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,0,0)", (
        attempt_id, owner_id, call_class, ordinal, request_sha, state, raw))
    conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,0)",
        (attempt_id,))
    conn.execute("INSERT INTO attempt_history VALUES(NULL,?,?,?,0)",
                 (attempt_id, state, raw))


def _insert_preflight(conn, header, ordinal, *, count=3):
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    for owner, (_kind, request_sha) in list(
            checkpoint._preflight_attempt_catalog(header, ordinal).items())[:count]:
        _insert_attempt(
            conn, stable_attempt_id(f"control:{owner}", 1), owner,
            "preflight_control", ordinal, request_sha)


def test_real_lineage_rejects_mixed_protocol_and_nonce_plan_tree():
    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    checkpoint.validate_run_lineage(conn, header)
    conn.close()

    conn, header, mixed_protocol = _real_lineage_db("b" * 64)
    _insert_real_plan_tree(conn, mixed_protocol)
    header["protocol_sha256"] = "a" * 64
    with pytest.raises(checkpoint.C0B4CheckpointError, match="policy/protocol"):
        checkpoint.validate_run_lineage(conn, header)
    conn.close()

    conn, header, master_a = _real_lineage_db()
    _other, _header_b, master_b = _real_lineage_db(
        nonce=bytes(range(1, 33)))
    _insert_real_plan_tree(conn, master_a, lane_master=master_b)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="lane differs"):
        checkpoint.validate_run_lineage(conn, header)
    conn.close()
    _other.close()


def test_real_lineage_rejects_tampered_and_cross_attempt_events():
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    work = master["lane_plans"][0]["payload"]["work"][0]
    attempt_id = stable_attempt_id(work["work_id"], 1)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    _insert_preflight(conn, header, 1)
    _insert_attempt(
        conn, attempt_id, work["work_id"], "scored", 1,
        work["request_sha256"])
    event = {
        "version": "c0b4-runtime-event-v1",
        "policy_id": header["policy_id"], "policy_sha256": header["policy_sha256"],
        "protocol_sha256": header["protocol_sha256"], "event": "RAW_VALID",
        "lane_id": "F72_17", "source_attempt_id": attempt_id,
        "request_sha256": work["request_sha256"], "nonce": work["nonce"],
        "occurred_at_utc": "2026-08-11T12:00:00.000000Z",
    }
    event["event_sha256"] = checkpoint.sha256_json(event)
    dispatch = dict(event)
    dispatch["event"] = "DISPATCHING"
    dispatch["event_sha256"] = checkpoint.sha256_json(
        dispatch, omit="event_sha256")
    conn.execute(
        "INSERT INTO events(kind,detail_json,created) VALUES(?,?,0)",
        (dispatch["event"], checkpoint.canonical_json(dispatch)))
    conn.execute(
        "INSERT INTO events(kind,detail_json,created) VALUES(?,?,0)",
        (event["event"], checkpoint.canonical_json(event)))
    checkpoint.validate_run_lineage(conn, header)
    conn.execute("DELETE FROM events WHERE kind='RAW_VALID'")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="incomplete"):
        checkpoint.validate_run_lineage(conn, header)
    conn.execute(
        "INSERT INTO events(kind,detail_json,created) VALUES(?,?,0)",
        (event["event"], checkpoint.canonical_json(event)))
    conn.execute("UPDATE events SET kind='NORMALIZED_DUPLICATE',detail_json='{}'")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="runtime event"):
        checkpoint.validate_run_lineage(conn, header)
    conn.execute("DELETE FROM events")
    crossed = dict(event)
    crossed["request_sha256"] = master["lane_plans"][0]["payload"]["work"][1][
        "request_sha256"]
    crossed["event_sha256"] = checkpoint.sha256_json(
        crossed, omit="event_sha256")
    conn.execute(
        "INSERT INTO events(kind,detail_json,created) VALUES(?,?,0)",
        (crossed["event"], checkpoint.canonical_json(crossed)))
    with pytest.raises(checkpoint.C0B4CheckpointError, match="misbound"):
        checkpoint.validate_run_lineage(conn, header)
    conn.close()


@pytest.mark.parametrize("mutation", [
    "non_sha_attempt", "invented_owner", "wrong_request", "wrong_class",
])
def test_standard_preflight_attempt_identity_is_closed(mutation):
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    owner, (_kind, request_sha256) = next(iter(
        checkpoint._preflight_attempt_catalog(header, 1).items()))
    attempt_id = stable_attempt_id(f"control:{owner}", 1)
    call_class = "preflight_control"
    if mutation == "non_sha_attempt":
        attempt_id = "not-a-sha"
    elif mutation == "invented_owner":
        owner = "1" * 64
        attempt_id = stable_attempt_id(f"control:{owner}", 1)
    elif mutation == "wrong_request":
        request_sha256 = "2" * 64
    else:
        call_class = "scored"
    conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,'RAW_VALID',NULL,0,0)",
        (attempt_id, owner, call_class, 1, request_sha256))
    conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,0)",
        (attempt_id,))
    conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'RAW_VALID',NULL,0)",
        (attempt_id,))
    with pytest.raises(checkpoint.C0B4CheckpointError):
        checkpoint.validate_run_lineage(conn, header)
    conn.close()


def test_exact_standard_preflight_identity_passes_precharge_boundary():
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    owner, (_kind, request_sha256) = next(iter(
        checkpoint._preflight_attempt_catalog(header, 1).items()))
    checkpoint._validate_new_attempt_identity(
        conn, header,
        attempt_id=stable_attempt_id(f"control:{owner}", 1),
        owner_id=owner, call_class="preflight_control",
        invocation_ordinal=1, request_sha256=request_sha256)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="owner"):
        checkpoint._validate_new_attempt_identity(
            conn, header, attempt_id="a" * 64, owner_id="b" * 64,
            call_class="preflight_control", invocation_ordinal=1,
            request_sha256=request_sha256)
    conn.close()


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicate"])
def test_invocation_preflight_barrier_rejects_inexact_census_or_order(mutation):
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    preflight = list(checkpoint._preflight_attempt_catalog(header, 1).items())
    ordered = preflight[:2] if mutation == "missing" else (
        [preflight[1], preflight[0], preflight[2]]
        if mutation == "reordered" else [preflight[0], preflight[0], *preflight[1:]])
    for owner, (_kind, request_sha) in ordered:
        _insert_attempt(conn, stable_attempt_id(f"control:{owner}", 1), owner,
                        "preflight_control", 1, request_sha)
    work = master["lane_plans"][0]["payload"]["work"][0]
    _insert_attempt(conn, stable_attempt_id(work["work_id"], 1),
                    work["work_id"], "scored", 1, work["request_sha256"])
    with pytest.raises(checkpoint.C0B4CheckpointError):
        checkpoint.validate_run_lineage(
            conn, header, require_event_completeness=False)
    conn.close()


def test_paused_invocation_accepts_only_an_exact_preflight_prefix():
    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    _insert_preflight(conn, header, 1, count=2)
    checkpoint.validate_run_lineage(conn, header)
    conn.close()


@pytest.mark.parametrize(("first_state", "first_payload", "retry_class"), [
    ("RETRYABLE_TRANSPORT", {"answered": False}, "transport_orphan"),
    ("SCHEMA_INVALID", {"answered": True, "response": "{}"}, "schema_retry"),
])
def test_retry_identity_retains_frozen_work_request_and_class_transition(
        first_state, first_payload, retry_class):
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    work = master["lane_plans"][0]["payload"]["work"][0]
    first_id = stable_attempt_id(work["work_id"], 1)
    conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,0,0)",
        (first_id, work["work_id"], "scored", 1, work["request_sha256"],
         first_state, checkpoint.canonical_json(first_payload)))

    retry_id = stable_attempt_id(work["work_id"], 2)
    checkpoint._validate_new_attempt_identity(
        conn, header, attempt_id=retry_id, owner_id=work["work_id"],
        call_class=retry_class, invocation_ordinal=2,
        request_sha256=work["request_sha256"])
    wrong_class = ("schema_retry" if retry_class == "transport_orphan"
                   else "transport_orphan")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="identity"):
        checkpoint._validate_new_attempt_identity(
            conn, header, attempt_id=retry_id, owner_id=work["work_id"],
            call_class=wrong_class, invocation_ordinal=2,
            request_sha256=work["request_sha256"])
    conn.close()


def test_schema_retry_partition_is_one_per_scored_lane():
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    _insert_preflight(conn, header, 1)
    for work in master["lane_plans"][0]["payload"]["work"][:2]:
        _insert_attempt(
            conn, stable_attempt_id(work["work_id"], 1), work["work_id"],
            "scored", 1, work["request_sha256"], "SCHEMA_INVALID",
            {"answered": True, "response": "{}"})
        _insert_attempt(
            conn, stable_attempt_id(work["work_id"], 2), work["work_id"],
            "schema_retry", 1, work["request_sha256"], "RAW_VALID",
            {"answered": True, "response": "{}"})
    with pytest.raises(checkpoint.C0B4CheckpointError, match="partition"):
        checkpoint.validate_run_lineage(
            conn, header, require_event_completeness=False)
    conn.close()


def test_lane_activation_rejects_a_present_but_failing_prerequisite():
    with pytest.raises(checkpoint.C0B4CheckpointError, match="did not pass"):
        checkpoint._require_passed_prerequisite(
            {"F72_17": ({"passed": False}, "a" * 64)}, "F72_17")
    checkpoint._require_passed_prerequisite(
        {"F72_17": ({"passed": True}, "a" * 64)}, "F72_17")


def test_failure_lineage_requires_durable_attempt_count_and_real_budget_exhaustion():
    def failure_values(header, *, terminal="FAILED_SAFETY", attempt_id="e" * 64,
                       charged=0):
        reasons = {
            "FAILED_SAFETY": "safety_envelope_failure",
            "BLOCKED_BUDGET": "call_allowance_exhausted",
        }
        evidence = {
            "version": "c0b4-failure-evidence-v1",
            "policy_id": header["policy_id"],
            "policy_sha256": header["policy_sha256"],
            "protocol_sha256": header["protocol_sha256"],
            "terminal": terminal, "reason": reasons[terminal],
            "lane_id": None, "plan_sha256": None,
            "attempt_id": attempt_id, "control_id": None,
            "charged_call_total": charged,
        }
        evidence["evidence_sha256"] = checkpoint.sha256_json(evidence)
        failure = {
            "version": "c0b4-failure-v1",
            "policy_id": header["policy_id"],
            "policy_sha256": header["policy_sha256"],
            "protocol_sha256": header["protocol_sha256"],
            "terminal": terminal, "reason": reasons[terminal],
            "evidence_sha256": checkpoint.sha256_json(evidence),
            "charged_call_total": charged,
        }
        return evidence, failure

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    evidence, failure = failure_values(header)
    conn.execute(
        "INSERT INTO artifacts VALUES('failure_evidence','terminal',?,?)",
        (checkpoint.sha256_json(evidence), checkpoint.canonical_json(evidence)))
    with pytest.raises(checkpoint.C0B4CheckpointError, match="coherent attempt"):
        checkpoint.validate_failure_terminal_ownership(conn, failure)
    conn.close()

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,'FAILED_SAFETY',NULL,0,0)",
        ("e" * 64, "preflight", "preflight_control", 1, "f" * 64))
    conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,0)",
        ("e" * 64,))
    conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'FAILED_SAFETY',NULL,0)",
        ("e" * 64,))
    evidence, failure = failure_values(header, charged=1)
    conn.execute(
        "INSERT INTO artifacts VALUES('failure_evidence','terminal',?,?)",
        (checkpoint.sha256_json(evidence), checkpoint.canonical_json(evidence)))
    checkpoint.validate_failure_terminal_ownership(conn, failure)
    conn.execute("DELETE FROM artifacts WHERE kind='failure_evidence'")
    evidence, wrong_count = failure_values(header, charged=0)
    conn.execute(
        "INSERT INTO artifacts VALUES('failure_evidence','terminal',?,?)",
        (checkpoint.sha256_json(evidence), checkpoint.canonical_json(evidence)))
    with pytest.raises(checkpoint.C0B4CheckpointError, match="ledger facts"):
        checkpoint.validate_failure_terminal_ownership(conn, wrong_count)
    conn.close()

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    evidence, budget = failure_values(
        header, terminal="BLOCKED_BUDGET", attempt_id=None)
    conn.execute(
        "INSERT INTO artifacts VALUES('failure_evidence','terminal',?,?)",
        (checkpoint.sha256_json(evidence), checkpoint.canonical_json(evidence)))
    with pytest.raises(checkpoint.C0B4CheckpointError, match="exhausted allowance"):
        checkpoint.validate_failure_terminal_ownership(conn, budget)
    conn.close()


def test_terminal_state_pairing_rejects_state_rollback_and_missing_receipt():
    from scripts.analyst_benchmark.c0b2_plan import attempt_id as stable_attempt_id

    conn, header, master = _real_lineage_db()
    _insert_real_plan_tree(conn, master)
    conn.execute("INSERT INTO invocations VALUES(1,0)")
    _insert_preflight(conn, header, 1)
    control = master["control_plan"]["context"]
    attempt_id = stable_attempt_id(f"control:{control['control_id']}", 1)
    _insert_attempt(conn, attempt_id, control["control_id"], "preflight_control", 1,
                    control["payload_sha256"], "FAILED_SAFETY", {"answered": False})
    identity = {key: header[key] for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}
    evidence = {
        "version": "c0b4-failure-evidence-v1", **identity,
        "terminal": "FAILED_SAFETY", "reason": "safety_envelope_failure",
        "lane_id": "F72_17", "plan_sha256": checkpoint.sha256_json(
            master["lane_plans"][0]["payload"]),
        "attempt_id": attempt_id, "control_id": control["control_id"],
        "charged_call_total": 4,
    }
    evidence["evidence_sha256"] = checkpoint.sha256_json(evidence)
    failure = {
        "version": "c0b4-failure-v1", **identity, "terminal": "FAILED_SAFETY",
        "reason": "safety_envelope_failure",
        "evidence_sha256": checkpoint.sha256_json(evidence), "charged_call_total": 4,
    }
    for kind, value in (("failure_evidence", evidence), ("failure", failure)):
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (
            kind, "terminal", checkpoint.sha256_json(value),
            checkpoint.canonical_json(value)))
    conn.execute("UPDATE run_state SET state='FAILED_SAFETY'")
    checkpoint.validate_run_lineage(conn, header, require_event_completeness=False)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="receipt"):
        checkpoint.validate_run_lineage(
            conn, header, require_event_completeness=False,
            require_terminal_receipt=True)
    conn.execute("UPDATE run_state SET state='RUNNING'")
    with pytest.raises(checkpoint.C0B4CheckpointError, match="state"):
        checkpoint.validate_run_lineage(conn, header, require_event_completeness=False)
    conn.close()
