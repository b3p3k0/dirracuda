"""Content-free end-to-end branch tests for the C0B-4 scheduler."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b4_runtime as runtime
from scripts.analyst_benchmark import c0b4_checkpoint as checkpoint
from scripts.analyst_benchmark import goldset
from scripts.analyst_benchmark.c0b2_executor import (
    ControlRequest, FakeResponse, RetryableTransport, WorkRequest,
)
from scripts.analyst_benchmark.c0b2_plan import (
    build_master_manifest, master_manifest_payload,
)
from scripts.analyst_benchmark.c0b2_plan import stable_hash
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b4_plan import (
    PARENT_BINDING, SELECTION, build_master_plan, candidate_id,
)


def _hash(label: str) -> str:
    return stable_hash({"label": label})


class FlowPoint:
    def __init__(self):
        self.conn = None
        self._state = "PREPARED"
        self.calls = []
        self.attempts = []
        self.artifacts = {
            ("master_plan", "master"): {"master": True},
            **{("lane_plan", lane): {
                "lane_id": lane, "plan_sha256": _hash(f"plan:{lane}"),
                "work": [],
            } for lane in runtime.LANE_ORDER},
        }
        self.final_artifact = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def header(self):
        return {
            "run_id": "flow", "policy_id": runtime.POLICY_ID,
            "policy_sha256": runtime.POLICY_SHA256,
            "protocol_sha256": _hash("protocol"),
            "ollama_endpoint": runtime.OLLAMA_ENDPOINT,
        }

    def state(self):
        return self._state

    def _assert_parent_unchanged(self):
        self.calls.append("parent")

    def read_nonce_key(self):
        return b"n" * 32

    def read_artifact(self, kind, owner):
        return self.artifacts.get((kind, owner))

    def store_artifact(self, kind, owner, value):
        assert (kind, owner) not in self.artifacts
        self.artifacts[(kind, owner)] = dict(value)
        return runtime.sha256_json(value)

    def list_attempts(self, _attempt_id=None):
        return list(self.attempts)

    def recover_dispatching(self):
        self.calls.append("recover")
        return []

    def transition(self, state):
        self.calls.append(f"transition:{state}")
        self._state = state

    def claim_invocation(self):
        self.calls.append("claim")
        return 1

    def finalize(self, terminal, artifact, completion=None):
        self.calls.append(f"finalize:{terminal}")
        self._state = terminal
        self.final_artifact = dict(artifact)
        self.artifacts[("result", "terminal")] = dict(artifact)
        if completion is not None:
            self.artifacts[("completion", "terminal")] = dict(completion)
        return runtime.sha256_json(artifact), runtime.sha256_json(completion)


def _controls():
    return {
        name: SimpleNamespace(control={
            "control_id": _hash(name), "candidate_id": _hash("candidate")})
        for name in ("context", "cancellation", "health")
    }


def _install_flow(monkeypatch, point: FlowPoint, branch: str):
    monkeypatch.setattr(runtime.C0B4Checkpoint, "open",
                        lambda *_args, **_kwargs: point)
    monkeypatch.setattr(runtime, "GlobalExecutionLock",
                        lambda _root: nullcontext(SimpleNamespace()))
    monkeypatch.setattr(runtime, "revalidate_source_pins",
                        lambda *_args, **_kwargs: point.calls.append("source"))
    monkeypatch.setattr(runtime, "_corpus", lambda _header: object())
    monkeypatch.setattr(runtime, "validate_master_plan",
                        lambda master, **_kwargs: master)
    monkeypatch.setattr(
        runtime, "validate_run_lineage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_reconcile_runtime_events", lambda *_args: None)
    controls = _controls()
    class Resolver:
        def __init__(self, *_args, **_kwargs):
            self.prepared = SimpleNamespace(resolve_controls=lambda: controls)
            self.controls = {}
    monkeypatch.setattr(runtime, "_Resolver", Resolver)
    monkeypatch.setattr(
        runtime, "_activate_lane",
        lambda _point, _master, lane, _prerequisite: (
            point.calls.append(f"activate:{lane['lane_id']}") or
            _hash("activation")))
    monkeypatch.setattr(
        runtime, "_cursor_transition",
        lambda _point, *, lane, **_kwargs: (
            point.calls.append(f"cursor:{lane['lane_id']}") or _hash("cursor")))
    monkeypatch.setattr(runtime, "_run_preflight",
                        lambda *_args, **_kwargs: (
                            point.calls.append("preflight") or
                            ("RAW_VALID", None)))
    monkeypatch.setattr(runtime, "_lane_evidence",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, "ensure_backup_receipt",
                        lambda *_args, **_kwargs: point.calls.append("backup"))

    def run_lane(_point, _resolver, _transport, *, lane, controls, **_kwargs):
        point.calls.append(f"work:{lane['lane_id']}")
        if lane["lane_id"] == "F72_17":
            point.artifacts[("context_evidence",
                             controls["context"].control["control_id"])] = {
                                 "context": True}
        return "RAW_VALID", None

    monkeypatch.setattr(runtime, "_run_lane_work", run_lane)

    def aggregate(lane, _evidence, **kwargs):
        lane_id = lane["lane_id"]
        controls_passed = kwargs.get("controls_passed", True)
        if lane_id == "F72_17":
            if branch == "seed17_no_qualifier":
                passed = False
            elif controls_passed is None:
                passed = False
            else:
                passed = controls_passed is not False
            return {
                "lane_id": lane_id, "passed": passed,
                "cancellation_health_evidence_sha256": kwargs.get(
                    "cancellation_health_evidence_sha256"),
            }
        if lane_id == "F72_20260804":
            return {"lane_id": lane_id,
                    "passed": branch != "seed20260804_no_qualifier"}
        return {"lane_id": lane_id, "component_passed": True}

    monkeypatch.setattr(runtime, "build_lane_aggregate", aggregate)
    monkeypatch.setattr(
        runtime, "build_precontrol_lane_aggregate",
        lambda *_args, **_kwargs: (
            {"lane_id": "F72_17", "passed": False,
             "cancellation_health_evidence_sha256": None}
            if branch == "seed17_no_qualifier" else None))

    def cancel_health(_point, _resolver, _transport, *, controls, **_kwargs):
        passed = branch != "seed17_control_gate_failed"
        point.artifacts[("cancellation_health_evidence",
                         controls["cancellation"].control["control_id"])] = {
                             "passed": passed}
        return "RAW_VALID", _hash("cancel-health")

    monkeypatch.setattr(runtime, "_run_cancel_health", cancel_health)
    monkeypatch.setattr(runtime, "build_acceptance_aggregate",
                        lambda *_args, **_kwargs: {
                            "passed": branch == "confirmed"})


@pytest.mark.parametrize("branch,terminal,reason,presence", [
    ("seed17_no_qualifier", "INCONCLUSIVE", "seed17_no_qualifier",
     (False, False, False)),
    ("seed17_control_gate_failed", "INCONCLUSIVE",
     "seed17_control_gate_failed", (False, False, False)),
    ("seed20260804_no_qualifier", "INCONCLUSIVE",
     "seed20260804_no_qualifier", (True, False, False)),
    ("complete_corpus_acceptance_failed", "INCONCLUSIVE",
     "complete_corpus_acceptance_failed", (True, True, True)),
    ("confirmed", "CONFIRMED", "complete_public_acceptance_passed",
     (True, True, True)),
])
def test_all_quality_branches_are_exact_and_backed_up(
        monkeypatch, tmp_path, branch, terminal, reason, presence) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, branch)
    result = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path,
        transport_factory=lambda *_args: lambda *_request: None,
        parent_d50_loader=lambda *_args: {}, stop_at_stage_boundary=False)
    assert result["state"] == terminal
    assert point.final_artifact["reason"] == reason
    hashes = point.final_artifact["lane_aggregate_sha256s"]
    assert tuple(hashes[name] is not None for name in (
        "f72_seed20260804_sha256", "c44_scored_sha256")) == presence[:2]
    assert (point.final_artifact["acceptance_aggregate_sha256"] is not None) \
        == presence[2]
    assert (point.final_artifact["selection"] is not None) == (terminal == "CONFIRMED")
    assert point.calls[-2:] == [f"finalize:{terminal}", "backup"]
    assert point.calls.index("source") < point.calls.index("recover") \
        < point.calls.index("preflight")


def test_stage_boundary_resume_and_terminal_reentry_are_side_effect_bounded(
        monkeypatch, tmp_path) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "confirmed")
    first = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, stop_at_stage_boundary=True,
        transport_factory=lambda *_args: lambda *_request: None)
    assert first["state"] == "PAUSED_STAGE_BOUNDARY"
    assert "cursor:F72_17" not in point.calls
    first_count = len(point.calls)
    second = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, resume=True,
        transport_factory=lambda *_args: lambda *_request: None,
        parent_d50_loader=lambda *_args: {})
    assert second["state"] == "PAUSED_STAGE_BOUNDARY"
    resume_calls = point.calls[first_count:]
    assert resume_calls.index("cursor:F72_17") \
        < resume_calls.index("activate:F72_20260804") \
        < resume_calls.index("preflight")
    third = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, resume=True,
        transport_factory=lambda *_args: lambda *_request: None,
        parent_d50_loader=lambda *_args: {})
    assert third["state"] == "CONFIRMED"
    before = list(point.calls)
    terminal = runtime.run_confirmation("flow", benchmark_root=tmp_path)
    assert terminal["state"] == "CONFIRMED"
    assert point.calls[len(before):] == ["backup"]


def test_resume_after_precontrol_aggregate_uses_noncontrol_stop_reason(
        monkeypatch, tmp_path) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "seed17_no_qualifier")
    point._state = "PAUSED_STAGE_BOUNDARY"
    point.artifacts[("context_evidence", _hash("context"))] = {"context": True}
    point.artifacts[("lane_aggregate", "F72_17")] = {
        "lane_id": "F72_17", "passed": False,
        "cancellation_health_evidence_sha256": None,
    }
    before = len(point.calls)
    result = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, resume=True,
        transport_factory=lambda *_args: lambda *_request: None)
    assert result["state"] == "INCONCLUSIVE"
    assert point.final_artifact["reason"] == "seed17_no_qualifier"
    assert not any(item.startswith(("cursor:", "activate:"))
                   or item == "preflight" for item in point.calls[before:])

    before = list(point.calls)
    assert runtime.run_confirmation(
        "flow", benchmark_root=tmp_path)["state"] == "INCONCLUSIVE"
    assert point.calls[len(before):] == ["backup"]


def test_prepared_operator_cancel_is_zero_call_and_resumable(
        monkeypatch, tmp_path) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "confirmed")
    cancelled = threading.Event()
    cancelled.set()
    result = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, cancellation=cancelled,
        transport_factory=lambda *_args: pytest.fail("no transport"))
    assert result["state"] == "CANCELLED_PENDING_RESUME"
    assert "claim" not in point.calls and "preflight" not in point.calls


def test_filesystem_revalidation_failure_is_zero_call_and_backed_up(
        monkeypatch, tmp_path) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "confirmed")
    monkeypatch.setattr(
        runtime, "revalidate_source_pins",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runtime.C0B4FilesystemError("capability mismatch")))

    result = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path,
        transport_factory=lambda *_args: pytest.fail("no transport"))

    assert result["state"] == "BLOCKED_FILESYSTEM"
    assert point.final_artifact["charged_call_total"] == 0
    assert point.attempts == []
    assert "claim" not in point.calls and "preflight" not in point.calls
    assert point.calls[-2:] == ["finalize:BLOCKED_FILESYSTEM", "backup"]


@pytest.mark.parametrize("fault", ["activation", "evidence", "acceptance"])
def test_post_validation_faults_block_with_backup_and_no_false_quality(
        monkeypatch, tmp_path, fault) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "confirmed")
    if fault == "activation":
        monkeypatch.setattr(
            runtime, "_activate_lane",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()))
    elif fault == "evidence":
        monkeypatch.setattr(
            runtime, "_lane_evidence",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()))
    loader = ((lambda *_args: (_ for _ in ()).throw(RuntimeError()))
              if fault == "acceptance" else (lambda *_args: {}))
    result = runtime.run_confirmation(
        "flow", benchmark_root=tmp_path, stop_at_stage_boundary=False,
        transport_factory=lambda *_args: lambda *_request: None,
        parent_d50_loader=loader)
    assert result["state"] == "BLOCKED_PROVENANCE"
    assert point.calls[-2:] == ["finalize:BLOCKED_PROVENANCE", "backup"]
    if fault == "activation":
        assert "preflight" not in point.calls


def test_abandonment_is_terminal_backed_up_and_charges_no_call(
        monkeypatch, tmp_path) -> None:
    point = FlowPoint()
    _install_flow(monkeypatch, point, "confirmed")
    result = runtime.abandon_confirmation_run(
        "flow", benchmark_root=tmp_path)
    assert result["state"] == "ABANDONED"
    assert point.final_artifact["reason"] == "operator_abandoned"
    assert point.attempts == []
    assert point.calls[-2:] == ["finalize:ABANDONED", "backup"]


def _real_header(run_id, manifest_sha, protocol_sha, root):
    sha = "a" * 64
    mount = {
        "canonical_path": str(root), "mount_id": "1", "mountpoint": str(root),
        "fs_type": "ext4", "options": "rw", "st_dev": root.stat().st_dev,
        "kernel": "test", "mergerfs_version": "test",
        "sqlite_version": sqlite3.sqlite_version,
    }
    mount["sha256"] = checkpoint.sha256_json(mount)
    return {
        "version": checkpoint.HEADER_VERSION, "run_type": "public_confirmation",
        "benchmark_protocol_id": checkpoint.PROTOCOL_ID,
        "policy_id": checkpoint.POLICY_ID, "policy_sha256": checkpoint.POLICY_SHA256,
        "protocol_sha256": protocol_sha, "parent_binding": dict(PARENT_BINDING),
        "ollama_endpoint": runtime.OLLAMA_ENDPOINT,
        "ollama_version": runtime.OLLAMA_VERSION,
        "filesystem_selected_mode": "DELETE", "git_head": "b" * 40,
        "declared_dirty_state_sha256": sha, "task_tree_sha256": sha,
        "fixture_sha256": sha, "master_manifest_sha256": manifest_sha,
        "schema_sha256": sha, "prompt_sha256": sha, "chunker_sha256": sha,
        "detector_sha256": sha, "generation_options_sha256": sha,
        "worktree_seal_sha256": sha, "filesystem_capability_sha256": sha,
        "model_digests": {SELECTION["model"]: SELECTION["model_digest"]},
        "mount": mount, "schema_version": checkpoint.SCHEMA_VERSION,
        "journal_mode": "DELETE", "cumulative_cap": checkpoint.CUMULATIVE_CAP,
        "run_id": run_id, "limits": dict(checkpoint.LEDGER_LIMITS),
        "invocation_caps": dict(checkpoint.INVOCATION_CAPS),
    }


def _d50(corpus):
    return {
        "component": "D50_CONFIRMATION", "source_plan_sha256": "b" * 64,
        "source_aggregate_sha256": PARENT_BINDING["d4_aggregate_sha256"],
        "candidate_id": candidate_id(), "selection": dict(SELECTION),
        "document_ids": list(corpus.d_order), "expected_chunks": 66,
        "completed_chunks": 66, "first_pass_invalid_chunks": 0,
        "eventual_invalid_chunks": 0, "raw_findings": 100,
        "raw_grounded_findings": 100, "retained_findings": 100,
        "retained_grounded_findings": 100,
        "category_recall": {name: {"true_positives": 6, "support": 6}
                            for name in runtime.CATEGORIES},
        "negative_false_positive_documents": 1, "injection_pairs": 0,
        "injection_pairs_measured": 0, "injection_events": 0,
        "robustness_failures": 0, "boundary_documents": 12,
        "boundary_passed": 12, "truncation_documents": 2,
        "truncation_completed": 2, "length_outcomes": 0,
        "context_failures": 0, "channel_violations": 0,
        "component_passed": True,
    }


def _answer_for(source, document):
    findings = []
    for category, identifier in zip(
            document.categories_present, document.expected_identifiers,
            strict=False):
        if identifier in source:
            findings.append({
                "category": category, "quote": identifier,
                "offset": source.index(identifier),
            })
    return json.dumps({
        "document_type": "fixture", "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": findings,
    }, separators=(",", ":"))


def test_real_checkpoint_full_228_request_fake_flow_confirms(
        monkeypatch, tmp_path) -> None:
    root = tmp_path / "bench"
    root.mkdir(mode=0o700)
    parent = tmp_path / "parent.sqlite3"
    snapshot = tmp_path / "parent-snapshot.sqlite3"
    for path in (parent, snapshot):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE placeholder(id INTEGER)")
        conn.commit()
        conn.close()
        path.chmod(0o600)
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    corpus = load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=source)
    protocol_sha = "c" * 64
    nonce_key = bytes(range(32))
    master = build_master_plan(
        corpus=corpus, run_nonce_key=nonce_key,
        protocol_sha256=protocol_sha)
    run_id = "full-fake"
    header = _real_header(run_id, manifest.sha256, protocol_sha, root)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(checkpoint.time, "time", lambda: clock[0])
    advance = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    real_hash_fd = checkpoint._hash_fd

    def pinned_parent_hash(fd):
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if target == parent:
            return header["parent_binding"]["checkpoint_sha256"]
        if target == snapshot:
            return header["parent_binding"]["backup_snapshot_sha256"]
        return real_hash_fd(fd)

    monkeypatch.setattr(checkpoint, "_hash_fd", pinned_parent_hash)
    monkeypatch.setattr(checkpoint, "verify_parent_readonly", lambda *_a, **_k: None)
    monkeypatch.setattr(checkpoint.C0B4Checkpoint,
                        "_assert_parent_unchanged", lambda _self: None)
    # The frozen parent hash has no reproducible fixture preimage. Keep every
    # child validator live and substitute only its already-tested D50 component.
    monkeypatch.setattr(
        checkpoint, "_parent_d50_sha256",
        lambda *_args: checkpoint.sha256_json(_d50(corpus)))

    def initialize(point):
        point.store_artifact("master_plan", "master", master)
        for envelope in [*master["lane_plans"], master["acceptance_template"]]:
            lane = envelope["payload"]
            point.store_artifact("lane_plan", lane["lane_id"], lane)
        point.set_nonce_key(nonce_key)

    point = checkpoint.C0B4Checkpoint.create(
        root, run_id, header=header, parent_checkpoint=parent,
        parent_snapshot=snapshot, parent_verifier=lambda *_args: None,
        initializer=initialize)
    point.close()
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_corpus", lambda _header: corpus)
    monkeypatch.setattr(runtime, "_parent_d50", lambda *_args: _d50(corpus))
    calls = []

    def factory(resolver, _header):
        def transport(request, cancel):
            calls.append((type(request).__name__, request.call_class))
            if isinstance(request, WorkRequest):
                resolved = resolver.prepared.resolve_work(request.work_id)
                body = _answer_for(
                    resolved["chunk_text"],
                    corpus.by_id()[resolved["work"]["doc_id"]])
                return FakeResponse(body, {
                    "strict_schema_invalid": False, "semantic_invalid": False,
                    "done_reason": "stop", "prompt_eval_count": 100,
                    "tools_empty": True, "images_empty": True,
                    "unknown_message_fields_empty": True,
                }, True, "ACCEPTED")
            assert isinstance(request, ControlRequest)
            spec = resolver.controls[request.control_id]
            if spec.kind == "ps":
                body = json.dumps({
                    "purpose": spec.purpose, "config_sha256": spec.config_sha256,
                    "model": spec.expected_model, "digest": spec.expected_digest,
                    "size": 1, "size_vram": 1, "context_length": 8192,
                }, separators=(",", ":"))
                return FakeResponse(body, {"response_sha256": hashlib.sha256(
                    body.encode()).hexdigest()})
            if spec.kind == "chat" and spec.cancel_on_first_content:
                cancel.set()
                raise RetryableTransport("cancelled")
            if spec.kind == "chat":
                document = corpus.by_id()["pos_pii_013"]
                body = _answer_for(document.text, document)
                return FakeResponse(body, {
                    "strict_schema_invalid": False, "semantic_invalid": False,
                    "done_reason": "stop", "prompt_eval_count": 100,
                    "tools_empty": True, "images_empty": True,
                    "unknown_message_fields_empty": True,
                }, True, "ACCEPTED")
            return FakeResponse("{}", {"response_sha256": _hash(spec.kind)})
        return transport

    result = runtime.run_confirmation(
        run_id, benchmark_root=root, transport_factory=factory,
        parent_d50_loader=lambda *_args: _d50(corpus),
        stop_at_stage_boundary=True, sleep=advance, now=lambda: clock[0])
    assert result["state"] == "PAUSED_STAGE_BOUNDARY"
    result = runtime.run_confirmation(
        run_id, benchmark_root=root, resume=True, transport_factory=factory,
        parent_d50_loader=lambda *_args: _d50(corpus),
        stop_at_stage_boundary=True, sleep=advance, now=lambda: clock[0])
    assert result["state"] == "PAUSED_STAGE_BOUNDARY"
    result = runtime.run_confirmation(
        run_id, benchmark_root=root, resume=True, transport_factory=factory,
        parent_d50_loader=lambda *_args: _d50(corpus),
        stop_at_stage_boundary=True, sleep=advance, now=lambda: clock[0])
    assert result["state"] == "CONFIRMED"
    status = runtime.confirmation_status(run_id, benchmark_root=root)
    assert status["calls_total"] == 240
    assert sum(kind == "WorkRequest" for kind, _call_class in calls) == 228
    assert sum(kind == "ControlRequest" for kind, _call_class in calls) == 12
    checkpoint_path = root / "runs" / run_id / "checkpoint.sqlite3"
    replays = []
    real_rederive = runtime._rederive_connection

    def counted_rederive(conn, replay_root):
        replays.append(True)
        return real_rederive(conn, replay_root)

    monkeypatch.setattr(runtime, "_rederive_connection", counted_rederive)
    before_verify = checkpoint_path.read_bytes()
    verified = runtime.confirmation_verify(run_id, benchmark_root=root)
    assert verified["ok"] is True
    assert verified["backup"]["receipt_present"] is True
    assert len(replays) == 2
    assert checkpoint_path.read_bytes() == before_verify

    positive = next(row for row in master["lane_plans"][0]["payload"]["work"]
                    if row["doc_id"].startswith("pos_"))
    conn = sqlite3.connect(checkpoint_path)
    attempt_id, raw = conn.execute(
        "SELECT attempt_id,payload_json FROM attempts "
        "WHERE owner_id=? AND state='RAW_VALID'", (positive["work_id"],)
    ).fetchone()
    payload = json.loads(raw)
    replacement = json.dumps({
        "document_type": "fixture", "subject": "",
        "assessment": "no_findings", "findings": [],
    }, separators=(",", ":"))
    payload["response"] = replacement
    payload["metadata"]["raw_response_sha256"] = hashlib.sha256(
        replacement.encode()).hexdigest()
    changed = checkpoint.canonical_json(payload)
    conn.execute("UPDATE attempts SET payload_json=? WHERE attempt_id=?",
                 (changed, attempt_id))
    conn.execute(
        "UPDATE attempt_history SET payload_json=? "
        "WHERE attempt_id=? AND state='RAW_VALID'", (changed, attempt_id))
    conn.commit()
    conn.close()
    tampered = runtime.confirmation_verify(run_id, benchmark_root=root)
    assert tampered["ok"] is False
    assert tampered["errors"] == ["backup:C0B4BackupError"]
