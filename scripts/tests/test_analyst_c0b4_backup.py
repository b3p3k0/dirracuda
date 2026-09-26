from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b4_backup as backup
from scripts.analyst_benchmark import c0b4_checkpoint as checkpoint
from scripts.analyst_benchmark.c0b2_fsprobe import GlobalExecutionLock
from scripts.tests.test_analyst_c0b4_checkpoint import _create, _file_sha

ORIGINAL_PARENT_BINDING = deepcopy(checkpoint.FROZEN_PARENT_BINDING)


def _synthetic_semantic_verifier(_conn):
    """Synthetic fixtures have no real responses to rescore."""


@pytest.fixture(autouse=True)
def _strict_schema_with_synthetic_parent(monkeypatch):
    """Keep strict type validation while adapting the synthetic parent fixture."""
    strict_validate = backup.validate_artifact

    def validate(value):
        if value.get("version") != backup.ANCHOR_VERSION:
            return strict_validate(value)
        schema_value = deepcopy(value)
        schema_value["parent_binding"] = ORIGINAL_PARENT_BINDING
        schema_value["anchor_sha256"] = checkpoint.sha256_json(
            schema_value, omit="anchor_sha256")
        strict_validate(schema_value)
        return dict(value)

    monkeypatch.setattr(backup, "validate_artifact", validate)
    monkeypatch.setattr(backup, "validate_run_lineage", lambda *_args: None)


def _terminal(point: checkpoint.C0B4Checkpoint, *, quality: bool = True):
    point.transition("RUNNING")
    header = point.header()
    identity = {
        "policy_id": header["policy_id"], "policy_sha256": header["policy_sha256"],
        "protocol_sha256": header["protocol_sha256"],
    }
    if quality:
        master = point.conn.execute(
            "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0]
        lane = point.store_artifact("lane_aggregate", "F72_17", {
            "passed": False, "cancellation_health_evidence_sha256": None,
            "failure_reasons": ["macro_f1_below_0_90"]})
        result_value = {
            "version": "c0b4-result-v1", **identity, "terminal": "INCONCLUSIVE",
            "reason": "seed17_no_qualifier", "master_plan_sha256": master,
            "lane_aggregate_sha256s": {
                "f72_seed17_sha256": lane, "f72_seed20260804_sha256": None,
                "c44_scored_sha256": None},
            "acceptance_aggregate_sha256": None, "selection": None,
        }
        result_digest = checkpoint.sha256_json(result_value)
        result, completion = point.finalize(
            "INCONCLUSIVE", result_value,
            completion={
                "version": "c0b4-completion-v1", **identity,
                "outcome": "INCONCLUSIVE", "artifact_sha256": result_digest,
                "facts": {"deterministic_stop": True, "reason": "seed17_no_qualifier"},
            })
    else:
        ordinal = point.claim_invocation()
        point.precharge(
            attempt_id="b" * 64, owner_id="preflight", call_class="preflight_control",
            invocation_ordinal=ordinal, request_sha256="c" * 64)
        point.record_attempt("b" * 64, "FAILED_SAFETY", {"answered": False})
        evidence = {
            "version": "c0b4-failure-evidence-v1", **identity,
            "terminal": "FAILED_SAFETY", "reason": "safety_envelope_failure",
            "lane_id": None, "plan_sha256": None,
            "attempt_id": "b" * 64, "control_id": None, "charged_call_total": 1,
        }
        evidence["evidence_sha256"] = checkpoint.sha256_json(evidence)
        evidence_sha = point.store_artifact(
            "failure_evidence", "terminal", evidence)
        result, completion = point.finalize(
            "FAILED_SAFETY",
            {"version": "c0b4-failure-v1", **identity,
             "terminal": "FAILED_SAFETY", "reason": "safety_envelope_failure",
             "evidence_sha256": evidence_sha, "charged_call_total": 1})
    return result, completion


def test_quality_backup_is_non_circular_verified_and_idempotent(tmp_path, monkeypatch):
    point, root, parent, parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    parent_before = (_file_sha(parent), _file_sha(parent_snapshot))
    result, completion = _terminal(point)
    with GlobalExecutionLock(root) as lock:
        receipt = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
        again = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
    assert again == receipt
    snapshot = point.path.parent / receipt["snapshot_run_relative_path"]
    assert _file_sha(snapshot) == receipt["snapshot_sha256"]
    assert stat_mode(snapshot) == 0o600
    # The snapshot is deliberately made before the source receipt is inserted.
    import sqlite3
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM backup_receipts").fetchone() == (0,)
    finally:
        conn.close()
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone() == (1,)
    semantic_receipt_counts = []
    assert backup.verify_backup_readonly(
        point.path, semantic_verifier=lambda conn: semantic_receipt_counts.append(
            conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0])
    )["ok"] is True
    assert semantic_receipt_counts == [1, 0]
    assert (_file_sha(parent), _file_sha(parent_snapshot)) == parent_before
    point.close()


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_failure_backup_requires_exact_null_completion(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point, quality=False)
    with GlobalExecutionLock(root) as lock:
        receipt = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
    row = point.conn.execute("SELECT anchor_json FROM backup_receipts").fetchone()
    import json
    assert json.loads(row[0])["completion_sha256"] is None
    assert backup.verify_backup_readonly(
        point.path, semantic_verifier=_synthetic_semantic_verifier)["ok"] is True
    point.close()


def test_quality_and_failure_completion_ownership_cannot_mix(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point)
    with GlobalExecutionLock(root) as lock:
        with pytest.raises(backup.C0B4BackupError, match="requires a completion"):
            backup.ensure_backup_receipt(
                point, lock, terminal_artifact_sha256=result, completion_sha256=None)
    assert not (point.path.parent / "backups").exists()
    point.close()


def test_receipt_and_snapshot_tampering_fail_closed(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point)
    with GlobalExecutionLock(root) as lock:
        receipt = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
    snapshot = point.path.parent / receipt["snapshot_run_relative_path"]
    with snapshot.open("ab") as stream:
        stream.write(b"tamper")
    assert backup.verify_backup_readonly(
        point.path, semantic_verifier=_synthetic_semantic_verifier)["ok"] is False
    point.close()


def test_backup_verify_requires_and_runs_semantic_rederivation(
        tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    ordinal = point.claim_invocation()
    point.precharge(
        attempt_id="scored-attempt", owner_id="w1", call_class="scored",
        invocation_ordinal=ordinal, request_sha256="a" * 64)
    point.record_attempt(
        "scored-attempt", "RAW_VALID",
        {"answered": True, "response": "original"})
    result, completion = _terminal(point)

    tampered = checkpoint.canonical_json(
        {"answered": True, "response": "coherently changed"})
    point.conn.execute(
        "UPDATE attempts SET payload_json=? WHERE attempt_id='scored-attempt'",
        (tampered,))
    point.conn.execute(
        "UPDATE attempt_history SET payload_json=? "
        "WHERE attempt_id='scored-attempt' AND state='RAW_VALID'",
        (tampered,))
    with GlobalExecutionLock(root) as lock:
        backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)

    def rederive(snapshot):
        raw = snapshot.execute(
            "SELECT payload_json FROM attempts WHERE attempt_id='scored-attempt'"
        ).fetchone()[0]
        if json.loads(raw)["response"] != "original":
            raise ValueError("private response must not escape through verification")

    assert backup.verify_backup_readonly(point.path)["ok"] is False
    assert backup.verify_backup_readonly(
        point.path, semantic_verifier=_synthetic_semantic_verifier)["ok"] is True
    checked = backup.verify_backup_readonly(
        point.path, semantic_verifier=rederive)
    assert checked["ok"] is False
    assert checked["errors"] == ["C0B4BackupError"]
    point.close()


def test_anchor_and_receipt_self_digests_omit_only_self_field(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point)
    anchor = backup.build_anchor(
        point, terminal_artifact_sha256=result, completion_sha256=completion)
    assert anchor["anchor_sha256"] == checkpoint.sha256_json(anchor, omit="anchor_sha256")
    with GlobalExecutionLock(root) as lock:
        receipt = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
    assert receipt["receipt_sha256"] == checkpoint.sha256_json(
        receipt, omit="receipt_sha256")
    changed = dict(receipt)
    changed["snapshot_size_bytes"] += 1
    with pytest.raises(backup.C0B4BackupError):
        backup.validate_receipt(changed, point.header())
    point.close()


def test_backup_requires_matching_lock_and_terminal_state(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    master = point.conn.execute(
        "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0]
    with GlobalExecutionLock(root) as lock:
        with pytest.raises(backup.C0B4BackupError, match="terminal evidence"):
            backup.ensure_backup_receipt(
                point, lock, terminal_artifact_sha256=master,
                completion_sha256=master)
    point.close()


def test_backup_rejects_terminal_with_inflight_attempt(tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point)
    point.conn.execute("INSERT INTO invocations VALUES(1,0)")
    point.conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,'DISPATCHING',NULL,0,0)",
        ("late", "w1", "scored", 1, "a" * 64))
    point.conn.execute(
        "INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,0)",
        ("late",))
    with pytest.raises(backup.C0B4BackupError, match="in-flight"):
        backup.build_anchor(
            point, terminal_artifact_sha256=result,
            completion_sha256=completion)
    point.close()


def test_finalize_rejects_mismatched_completion_and_failure_evidence(
        tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    header = point.header()
    identity = {key: header[key] for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}
    master = point.conn.execute(
        "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0]
    lane = point.store_artifact("lane_aggregate", "F72_17", {
        "passed": False, "cancellation_health_evidence_sha256": None,
        "failure_reasons": ["macro_f1_below_0_90"]})
    result = {
        "version": "c0b4-result-v1", **identity, "terminal": "INCONCLUSIVE",
        "reason": "seed17_no_qualifier", "master_plan_sha256": master,
        "lane_aggregate_sha256s": {
            "f72_seed17_sha256": lane, "f72_seed20260804_sha256": None,
            "c44_scored_sha256": None},
        "acceptance_aggregate_sha256": None, "selection": None,
    }
    with pytest.raises(checkpoint.C0B4CheckpointError, match="facts differ"):
        point.finalize("INCONCLUSIVE", result, completion={
            "version": "c0b4-completion-v1", **identity,
            "outcome": "INCONCLUSIVE", "artifact_sha256": checkpoint.sha256_json(result),
            "facts": {"deterministic_stop": True,
                      "reason": "complete_corpus_acceptance_failed"},
        })

    evidence = {
        "version": "c0b4-failure-evidence-v1", **identity,
        "terminal": "FAILED_SAFETY", "reason": "safety_envelope_failure",
        "lane_id": "F72_17", "plan_sha256": "a" * 64,
        "attempt_id": "b" * 64, "control_id": None, "charged_call_total": 0,
    }
    evidence["evidence_sha256"] = checkpoint.sha256_json(evidence)
    evidence_sha = point.store_artifact("failure_evidence", "terminal", evidence)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="failure result/evidence"):
        point.finalize("FAILED_SAFETY", {
            "version": "c0b4-failure-v1", **identity,
            "terminal": "FAILED_SAFETY", "reason": "safety_envelope_failure",
            "evidence_sha256": evidence_sha, "charged_call_total": 1,
        })
    point.close()


def test_finalize_requires_exact_aggregate_kind_and_owner(tmp_path, monkeypatch):
    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    point.transition("RUNNING")
    header = point.header()
    identity = {key: header[key] for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}
    master = point.conn.execute(
        "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0]
    misplaced = point.store_artifact(
        "lane_aggregate", "F72_20260804", {"test": "misowned-lane"})
    result = {
        "version": "c0b4-result-v1", **identity, "terminal": "INCONCLUSIVE",
        "reason": "seed17_no_qualifier", "master_plan_sha256": master,
        "lane_aggregate_sha256s": {
            "f72_seed17_sha256": misplaced, "f72_seed20260804_sha256": None,
            "c44_scored_sha256": None},
        "acceptance_aggregate_sha256": None, "selection": None,
    }
    with pytest.raises(checkpoint.C0B4CheckpointError, match="misowned evidence"):
        point.finalize("INCONCLUSIVE", result, completion={
            "version": "c0b4-completion-v1", **identity,
            "outcome": "INCONCLUSIVE", "artifact_sha256": checkpoint.sha256_json(result),
            "facts": {"deterministic_stop": True, "reason": "seed17_no_qualifier"},
        })
    point.close()


def test_finalize_rejects_wrong_stop_semantics_and_inflight_attempt(
        tmp_path, monkeypatch):
    def values(point, lane_value):
        point.transition("RUNNING")
        header = point.header()
        identity = {key: header[key] for key in (
            "policy_id", "policy_sha256", "protocol_sha256")}
        master = point.conn.execute(
            "SELECT sha256 FROM artifacts WHERE kind='master_plan'").fetchone()[0]
        lane = point.store_artifact("lane_aggregate", "F72_17", lane_value)
        result = {
            "version": "c0b4-result-v1", **identity, "terminal": "INCONCLUSIVE",
            "reason": "seed17_no_qualifier", "master_plan_sha256": master,
            "lane_aggregate_sha256s": {
                "f72_seed17_sha256": lane, "f72_seed20260804_sha256": None,
                "c44_scored_sha256": None},
            "acceptance_aggregate_sha256": None, "selection": None,
        }
        completion = {
            "version": "c0b4-completion-v1", **identity,
            "outcome": "INCONCLUSIVE", "artifact_sha256": checkpoint.sha256_json(result),
            "facts": {"deterministic_stop": True, "reason": "seed17_no_qualifier"},
        }
        return result, completion

    point, _root, _parent, _snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = values(point, {
        "passed": True, "cancellation_health_evidence_sha256": "a" * 64,
        "failure_reasons": []})
    with pytest.raises(checkpoint.C0B4CheckpointError, match="aggregate facts"):
        point.finalize("INCONCLUSIVE", result, completion=completion)
    point.close()

    other = tmp_path / "inflight"
    other.mkdir()
    point, _root, _parent, _snapshot, _seen = _create(other, monkeypatch)
    result, completion = values(point, {
        "passed": False, "cancellation_health_evidence_sha256": None,
        "failure_reasons": ["macro_f1_below_0_90"]})
    ordinal = point.claim_invocation()
    point.precharge(
        attempt_id="in-flight", owner_id="w1", call_class="scored",
        invocation_ordinal=ordinal, request_sha256="a" * 64)
    with pytest.raises(checkpoint.C0B4CheckpointError, match="in-flight"):
        point.finalize("INCONCLUSIVE", result, completion=completion)
    assert point.state() == "RUNNING"
    point.close()


def test_backup_schema_rejects_coerced_receipt_fields(tmp_path, monkeypatch):
    point, root, _parent, _parent_snapshot, _seen = _create(tmp_path, monkeypatch)
    result, completion = _terminal(point)
    with GlobalExecutionLock(root) as lock:
        receipt = backup.ensure_backup_receipt(
            point, lock, terminal_artifact_sha256=result,
            completion_sha256=completion)
    for field, value in (("created_at_utc", 1), ("foreign_key_violations", False)):
        changed = dict(receipt)
        changed[field] = value
        changed["receipt_sha256"] = checkpoint.sha256_json(
            changed, omit="receipt_sha256")
        with pytest.raises(backup.C0B4BackupError, match="strict schema"):
            backup.validate_receipt(changed, point.header())
    point.close()
