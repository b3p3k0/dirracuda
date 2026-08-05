"""Offline creation and identity guards for the C0B-2B1 public runtime."""
from __future__ import annotations

import json
import shutil
import signal
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scripts.analyst_benchmark import c0b2_plan as plan
from scripts.analyst_benchmark import c0b2_runtime as runtime
from scripts.analyst_benchmark import c0b2_executor as executor
from scripts.analyst_benchmark import c0b2_transport as transport
from scripts.analyst_benchmark.c0b2_leakscan import SealEntry, WorktreeSeal


def test_public_ledger_is_exact_and_nontransferable() -> None:
    assert runtime.PUBLIC_LIMITS == {
        "C": {"scored": 264, "schema_retry": 12,
              "preflight_probe": 18, "transport_orphan": 106},
        "D": {"scored": 757, "schema_retry": 64,
              "preflight_probe": 36, "transport_orphan": 93},
        "F": {"scored": 1142, "schema_retry": 14,
              "preflight_probe": 59, "transport_orphan": 185},
    }
    assert [sum(runtime.PUBLIC_LIMITS[s].values()) for s in ("C", "D", "F")] == [
        400, 950, 1400]
    assert sum(sum(row.values()) for row in runtime.PUBLIC_LIMITS.values()) == 2750
    assert runtime.PUBLIC_CUMULATIVE_CAP == 2750


def test_generated_manifest_and_plan_payloads_reproduce_frozen_hashes() -> None:
    manifest = plan.build_master_manifest()
    stage = plan.build_c_stage_plan(b"k" * 32)
    assert plan.stable_hash(runtime._manifest_payload(manifest)) == manifest.sha256
    assert plan.stable_hash(runtime._plan_payload(stage)) == stage.sha256


def test_run_ids_and_checkpoint_paths_are_opaque(tmp_path: Path) -> None:
    generated = runtime.new_public_run_id()
    assert generated.startswith("c0b2-")
    assert runtime._checkpoint_path(
        generated, tmp_path) == tmp_path / "runs" / generated / "checkpoint.sqlite3"
    for bad in ("../escape", "/absolute", "has space"):
        with pytest.raises(ValueError):
            runtime._checkpoint_path(bad, tmp_path)


def test_task_delta_must_be_committed_but_unrelated_dirty_work_is_allowed() -> None:
    unrelated = SealEntry(
        "docs/dev/kbd_ctrl_improve/notes.md", "??", "file", 0o644, 1, "a" * 64)
    runtime._require_clean_task_delta(WorktreeSeal("b" * 40, (unrelated,)))

    task = SealEntry(
        "scripts/analyst_benchmark/c0b2_runtime.py", " M", "file", 0o644, 1,
        "b" * 64)
    with pytest.raises(runtime.RuntimeGateError, match="commit"):
        runtime._require_clean_task_delta(WorktreeSeal("b" * 40, (unrelated, task)))


def test_content_free_render_is_canonical_json() -> None:
    rendered = runtime.render_public({"state": "PREPARED", "calls_total": 0})
    assert rendered == '{"calls_total":0,"state":"PREPARED"}'
    assert json.loads(rendered) == {"state": "PREPARED", "calls_total": 0}


def _stage_c_boundary(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str,
) -> tuple[Path, runtime.Checkpoint]:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root = tmp_path / "bench"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = runtime.Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    _parent, plan_hash, _raw = point.load_plan("C")
    aggregate = {"version": "test-stage-c-aggregate"}
    aggregate_raw = runtime.canonical_json(aggregate)
    aggregate_hash = runtime.sha256_json(aggregate)
    point.conn.execute(
        "INSERT INTO stage_aggregates VALUES(?,?,?,?,?)",
        ("C", plan_hash, aggregate_hash, aggregate_raw, 1.0),
    )
    decision = {"version": "test-stage-c-selection"}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("stage-c-selection", "C", plan_hash, aggregate_hash, "ACTIVATED",
         runtime.canonical_json(decision), 1.0),
    )
    point.conn.execute(
        "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY' WHERE id=1")
    return root, point


def test_backup_receipt_recovers_both_crash_windows_without_reusing_orphan(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-backup-crash-windows")
    with runtime.GlobalExecutionLock(root) as lock:
        monkeypatch.setattr(
            runtime, "_pre_receipt_commit_hook",
            lambda *_args: (_ for _ in ()).throw(OSError("pre-receipt crash")))
        with pytest.raises(OSError, match="pre-receipt crash"):
            runtime.ensure_backup_receipt(point, lock)
        assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
        orphan = tuple((point.path.parent / "backups").glob("snapshot-*.sqlite3"))
        assert len(orphan) == 1

        monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", lambda *_args: None)
        monkeypatch.setattr(
            runtime, "_receipt_return_hook",
            lambda *_args: (_ for _ in ()).throw(OSError("post-receipt crash")))
        with pytest.raises(OSError, match="post-receipt crash"):
            runtime.ensure_backup_receipt(point, lock)
        snapshots = tuple((point.path.parent / "backups").glob("snapshot-*.sqlite3"))
        assert len(snapshots) == 2 and orphan[0] in snapshots
        assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 1

        monkeypatch.setattr(runtime, "_receipt_return_hook", lambda *_args: None)
        receipt = runtime.ensure_backup_receipt(point, lock)
        assert runtime.ensure_backup_receipt(point, lock) == receipt
        assert len(tuple((point.path.parent / "backups").glob(
            "snapshot-*.sqlite3"))) == 2

        changed = runtime._current_backup_anchor(point)
        changed["charged_call_total"] += 1
        with pytest.raises(runtime.RuntimeGateError, match="differs"):
            runtime.ensure_backup_receipt(point, lock, changed)
    point.close()


@pytest.mark.parametrize("swap", ["parent", "leaf"])
def test_backup_receipt_rejects_parent_or_leaf_replacement_before_commit(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, swap: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-backup-swap-{swap}")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)

    def replace(path: Path, _receipt: dict[str, Any]) -> None:
        if swap == "parent":
            moved = path.parent.with_name("backups-original")
            path.parent.rename(moved)
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            moved = path.with_suffix(".original")
            path.rename(moved)
            path.symlink_to(outside / "replacement.sqlite3")

    monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", replace)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises((OSError, PermissionError, runtime.RuntimeGateError)):
            runtime.ensure_backup_receipt(point, lock)
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    assert not tuple(outside.iterdir())
    point.close()


def test_backup_creation_stays_on_pinned_directory_and_rejects_parent_swap(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-backup-create-parent-swap")
    outside = tmp_path / "outside-create"
    outside.mkdir(mode=0o700)
    original_open = runtime.os.open
    swapped = False

    def racing_open(path: Any, flags: int, mode: int = 0o777,
                    *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if (not swapped and dir_fd is not None and isinstance(path, str)
                and path.startswith("snapshot-")):
            backup = point.path.parent / "backups"
            backup.rename(point.path.parent / "backups-original")
            backup.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(runtime.os, "open", racing_open)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="backup"):
            runtime.ensure_backup_receipt(point, lock)
    assert swapped and not tuple(outside.iterdir())
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize("swap", ["parent", "leaf"])
def test_backup_receipt_rejects_byte_identical_inode_replacement_before_commit(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, swap: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-backup-identical-{swap}")
    changed_inode = False

    def replace(path: Path, _receipt: dict[str, Any]) -> None:
        nonlocal changed_inode
        original = path.stat()
        if swap == "parent":
            moved = path.parent.with_name("backups-original")
            path.parent.rename(moved)
            path.parent.mkdir(mode=0o700)
            shutil.copyfile(moved / path.name, path)
        else:
            moved = path.with_suffix(".original")
            path.rename(moved)
            shutil.copyfile(moved, path)
        path.chmod(0o600)
        changed_inode = path.stat().st_ino != original.st_ino

    monkeypatch.setattr(runtime, "_pre_receipt_commit_hook", replace)
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError, match="pinned inode"):
            runtime.ensure_backup_receipt(point, lock)
    assert changed_inode
    assert point.conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0] == 0
    point.close()


@pytest.mark.parametrize(
    "mutation", ["terminal", "stage", "plan", "count", "control"])
def test_backup_anchor_rejects_coherent_failure_cross_record_tampering(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    root, point = _stage_c_boundary(
        monkeypatch, tmp_path, f"c0b2-failure-cross-{mutation}")
    runtime.finish_public_run_failure(point, terminal="ABANDONED")
    rows = point.conn.execute(
        "SELECT artifact_id,artifact_json FROM public_artifacts").fetchall()
    evidence_id, evidence = next(
        (identity, json.loads(raw)) for identity, raw in rows if "evidence-v1" in raw)
    artifact_id, artifact = next(
        (identity, json.loads(raw)) for identity, raw in rows if "c0b2-failure-v1" in raw)
    if mutation == "terminal":
        evidence.update(terminal="BLOCKED_PROVENANCE",
                        reason_code="provenance_identity_failure")
        artifact.update(terminal="BLOCKED_PROVENANCE",
                        reason="provenance_identity_failure")
    elif mutation == "stage":
        evidence.update(stage="D", plan_key="D1_OUTPUT")
        artifact["stage"] = "D"
    elif mutation == "plan":
        evidence["plan_key"] = None
    elif mutation == "control":
        evidence["control_id"] = "f" * 64
    else:
        artifact["charged_call_total"] += 1
    evidence_hash = runtime.sha256_json(evidence)
    artifact["evidence_sha256"] = evidence_hash
    point.conn.execute(
        "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? WHERE artifact_id=?",
        (runtime.canonical_json(evidence), evidence_hash, evidence_id))
    point.conn.execute(
        "UPDATE public_artifacts SET artifact_json=?,artifact_hash=? WHERE artifact_id=?",
        (runtime.canonical_json(artifact), runtime.sha256_json(artifact), artifact_id))
    with runtime.GlobalExecutionLock(root) as lock:
        with pytest.raises(runtime.RuntimeGateError):
            runtime.ensure_backup_receipt(point, lock)
    point.close()


def test_result_artifact_must_match_authoritative_aggregate(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-result-aggregate-cross")
    expected, wrong = "a" * 64, "b" * 64
    artifact = {"version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "D", "aggregate_sha256": wrong,
                "reason": "no_d1_output_budget_survivor"}
    artifact_hash = runtime.freeze_public_artifact(point, "result", artifact)
    completion = {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
                  "facts": {"deterministic_stop": True,
                            "reason": "no_d1_output_budget_survivor"}}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("c0b2-completion", "D", "c" * 64, expected, "NOT_ACTIVATED",
         runtime.canonical_json(completion), 1.0))
    with pytest.raises(runtime.RuntimeGateError, match="aggregate"):
        runtime._stored_public_artifact(
            point.conn, "INCONCLUSIVE", active_stage="D",
            active_plan_key="D1_OUTPUT", active_plan_hash="c" * 64,
            aggregate_hash=expected, charged_total=0)
    point.close()


def test_completion_decision_must_match_active_plan_parent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root, point = _stage_c_boundary(
        monkeypatch, tmp_path, "c0b2-completion-parent-cross")
    aggregate_hash, active_plan_hash = "a" * 64, "b" * 64
    artifact = {"version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "D", "aggregate_sha256": aggregate_hash,
                "reason": "no_d1_output_budget_survivor"}
    artifact_hash = runtime.freeze_public_artifact(point, "result", artifact)
    completion = {"outcome": "INCONCLUSIVE", "artifact_sha256": artifact_hash,
                  "facts": {"deterministic_stop": True,
                            "reason": "no_d1_output_budget_survivor"}}
    point.conn.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
        ("c0b2-completion", "D", "c" * 64, aggregate_hash, "NOT_ACTIVATED",
         runtime.canonical_json(completion), 1.0))
    with pytest.raises(runtime.RuntimeGateError, match="completion decision"):
        runtime._stored_public_artifact(
            point.conn, "INCONCLUSIVE", active_stage="D",
            active_plan_key="D1_OUTPUT", active_plan_hash=active_plan_hash,
            aggregate_hash=aggregate_hash, charged_total=0)
    point.close()


@pytest.mark.parametrize("damage", ["deleted", "corrupt", "symlink_dir"])
def test_public_verify_detects_missing_corrupt_or_symlinked_receipt_snapshot(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, damage: str) -> None:
    run_id = f"c0b2-backup-{damage}"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    with runtime.GlobalExecutionLock(root) as lock:
        receipt = runtime.ensure_backup_receipt(point, lock)
    snapshot = runtime._receipt_snapshot_path(
        point.path.parent, receipt["snapshot_run_relative_path"])
    point.close()
    if damage == "deleted":
        snapshot.unlink()
    elif damage == "corrupt":
        snapshot.write_bytes(b"not sqlite")
        snapshot.chmod(0o600)
    else:
        backup_root = snapshot.parent
        moved = backup_root.with_name("backups-real")
        backup_root.rename(moved)
        backup_root.symlink_to(moved, target_is_directory=True)
    result = runtime.public_verify(run_id, benchmark_root=root)
    assert result["ok"] is False
    assert any(error.startswith("backup_snapshot_invalid:")
               for error in result["errors"])


@pytest.mark.parametrize("mutation", ["receipt_link", "anchor_row"])
def test_public_verify_reports_receipt_anchor_tampering_without_crashing(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    run_id = f"c0b2-backup-anchor-{mutation}"
    root, point = _stage_c_boundary(monkeypatch, tmp_path, run_id)
    with runtime.GlobalExecutionLock(root) as lock:
        runtime.ensure_backup_receipt(point, lock)
    row = point.conn.execute(
        "SELECT anchor_hash,anchor_json,receipt_json FROM backup_receipts"
    ).fetchone()
    if mutation == "receipt_link":
        value = json.loads(row[2])
        value["anchor_sha256"] = "f" * 64
        point.conn.execute(
            "UPDATE backup_receipts SET receipt_json=?,receipt_hash=?",
            (runtime.canonical_json(value), runtime.sha256_json(value)))
    else:
        value = json.loads(row[1])
        value["charged_call_total"] += 1
        point.conn.execute(
            "UPDATE backup_receipts SET anchor_json=?",
            (runtime.canonical_json(value),))
    point.close()
    result = runtime.public_verify(run_id, benchmark_root=root)
    assert result["ok"] is False
    assert any(error.startswith("backup_anchor_invalid:")
               for error in result["errors"])


def test_live_source_pin_revalidation_fails_closed(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    current = {"git_head": "a" * 40, "ollama_version": runtime.OLLAMA_VERSION}
    monkeypatch.setattr(runtime, "capture_worktree_seal", lambda _root: object())
    monkeypatch.setattr(runtime, "_source_pins", lambda _root, _seal: current)

    runtime.revalidate_source_pins(current, repo_root=tmp_path)
    with pytest.raises(runtime.RuntimeGateError, match="ollama_version"):
        runtime.revalidate_source_pins(
            {**current, "ollama_version": "changed"}, repo_root=tmp_path)


def test_public_stage_c_runs_exact_plan_and_finalizes_no_survivor(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise the whole runtime with a bounded fake, never a network client."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    run_id = runtime.create_public_run(
        benchmark_root=tmp_path / "bench", run_id="c0b2-test-public")
    calls: list[str] = []

    class FakeTransport:
        def __init__(self, resolver):
            self.resolver = resolver

        def cancel_current(self) -> None:
            return None

        def __call__(self, request, _cancel):
            spec = self.resolver(request)
            calls.append(spec.kind)
            if spec.kind != "chat":
                return executor.FakeResponse("{}")
            if spec.worksheet == "v1":
                value = {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings",
                    "categories": [
                        {"category": category, "present": False, "evidence": []}
                        for category in ("pii", "financial", "contact", "demographic")
                    ],
                }
            else:
                value = {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings", "findings": [],
                }
            return executor.FakeResponse(
                json.dumps(value, sort_keys=True, separators=(",", ":")),
                metadata={
                    "done_reason": "stop", "tools_empty": True,
                    "images_empty": True, "unknown_message_fields_empty": True,
                    "strict_schema_invalid": False,
                    "semantic_invalid": False,
                })

    result = runtime.run_public_stage_c(
        run_id, benchmark_root=tmp_path / "bench",
        transport_factory=lambda resolver, _header: FakeTransport(resolver))
    assert result == {
        "run_id": run_id, "stage": "C", "state": "INCONCLUSIVE",
        "calls_total": 272, "survivor_count": 0,
    }
    status = runtime.public_status(run_id, benchmark_root=tmp_path / "bench")
    assert status["state"] == "INCONCLUSIVE" and status["calls_total"] == 272
    assert status["backup"]["required"] is True
    assert status["backup"]["receipt_present"] is True
    assert len(status["backup"]["anchor_sha256"]) == 64
    assert len(status["backup"]["snapshot_sha256"]) == 64
    snapshots = list((tmp_path / "bench" / "runs" / run_id / "backups").glob(
        "snapshot-*.sqlite3"))
    assert len(snapshots) == 1
    assert runtime.status_readonly(snapshots[0])["state"] == "INCONCLUSIVE"
    assert calls.count("chat") == 264
    assert calls.count("ps") == 3
    assert calls.count("version") == 1
    assert calls.count("tags") == 1
    assert calls.count("show") == 3
    verification = runtime.public_verify(run_id, benchmark_root=tmp_path / "bench")
    assert verification == {"ok": True, "errors": [], "backup": status["backup"]}


def test_runtime_specs_cross_the_real_bounded_transport_offline(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prove runtime-built specs against the adapter using fake HTTP only."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-transport-seam")

    class Raw:
        def __init__(self, body: bytes):
            self.body = body

        def stream(self, *, amt: int, decode_content: bool):
            assert amt == 64 * 1024 and decode_content is False
            yield self.body

    class Response:
        def __init__(self, body: bytes, content_type: str):
            self.status_code = 200
            self.headers = {"Content-Type": content_type}
            self.raw = Raw(body)

        def close(self) -> None:
            return None

    class Session:
        def __init__(self):
            self.trust_env = True
            self.max_redirects = 30
            self.active_model = ""
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any):
            self.calls += 1
            assert kwargs["allow_redirects"] is False
            assert kwargs["proxies"] == {"http": None, "https": None}
            path = url.removeprefix(runtime.OLLAMA_ENDPOINT)
            if path == "/api/chat":
                payload = kwargs["json"]
                self.active_model = payload["model"]
                answer = ({
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings",
                    "categories": [
                        {"category": category, "present": False, "evidence": []}
                        for category in ("pii", "financial", "contact", "demographic")
                    ],
                } if "categories" in payload["format"]["properties"] else {
                    "document_type": "unknown", "subject": "",
                    "assessment": "no_findings", "findings": [],
                })
                frame = {
                    "model": self.active_model,
                    "message": {"role": "assistant", "content": json.dumps(
                        answer, sort_keys=True, separators=(",", ":"))},
                    "done": True, "done_reason": "stop",
                }
                return Response(
                    json.dumps(frame, separators=(",", ":")).encode() + b"\n",
                    "application/x-ndjson")
            if path == "/api/version":
                value = {"version": runtime.OLLAMA_VERSION}
            elif path == "/api/tags":
                value = {"models": [
                    {"name": model, "model": model, "digest": digest}
                    for model, digest, _think in plan.MODELS]}
            elif path == "/api/show":
                value = {"capabilities": [], "details": {}, "model_info": {}}
            elif path == "/api/ps":
                digest = next(row[1] for row in plan.MODELS
                              if row[0] == self.active_model)
                value = {"models": [{
                    "name": self.active_model, "model": self.active_model,
                    "digest": digest, "size": 1, "size_vram": 0,
                    "context_length": 8192,
                }]}
            else:  # pragma: no cover - exact resolver set is asserted above
                raise AssertionError(path)
            return Response(
                json.dumps(value, separators=(",", ":")).encode(),
                "application/json")

    session = Session()
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda resolver, header: transport.BoundedOllamaTransport(
            resolver, endpoint=header["ollama_endpoint"], session=session))
    assert result["state"] == "INCONCLUSIVE"
    assert result["calls_total"] == session.calls == 272


def test_first_signal_before_recovery_consumes_no_invocation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-early-signal")
    original = executor.DurableExecutor.recover_and_start

    def interrupt_before_recovery(self, stage):
        signal.raise_signal(signal.SIGINT)
        return original(self, stage)

    class NoCallTransport:
        def cancel_current(self) -> None:
            return None

        def __call__(self, *_args):
            pytest.fail("an early signal must prevent transport contact")

    monkeypatch.setattr(
        executor.DurableExecutor, "recover_and_start", interrupt_before_recovery)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda *_args: NoCallTransport())
    assert result["state"] == "CANCELLED_PENDING_RESUME"
    assert result["calls_total"] == 0
    conn = sqlite3.connect(runtime._checkpoint_path(run_id, root))
    try:
        assert conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 0
    finally:
        conn.close()


def test_first_signal_during_invocation_claim_consumes_no_invocation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The in-transaction claim guard closes the signal/INSERT race."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda _header: None)
    root = tmp_path / "bench"
    run_id = runtime.create_public_run(
        benchmark_root=root, run_id="c0b2-test-claim-signal")
    original = runtime.Checkpoint.claim_invocation

    def interrupt_during_claim(
            self, stage, *, claim_guard=None, budget_failure_callback=None):
        first_guard = True

        def guarded_claim():
            nonlocal first_guard
            if first_guard:
                first_guard = False
                signal.raise_signal(signal.SIGINT)
            assert claim_guard is not None
            claim_guard()

        return original(
            self, stage, claim_guard=guarded_claim,
            budget_failure_callback=budget_failure_callback)

    class NoCallTransport:
        def cancel_current(self) -> None:
            return None

        def __call__(self, *_args):
            pytest.fail("a signal during claim must prevent transport contact")

    monkeypatch.setattr(
        runtime.Checkpoint, "claim_invocation", interrupt_during_claim)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda *_args: NoCallTransport())
    assert result["state"] == "CANCELLED_PENDING_RESUME"
    assert result["calls_total"] == 0
    conn = sqlite3.connect(runtime._checkpoint_path(run_id, root))
    try:
        assert conn.execute("SELECT count(*) FROM invocations").fetchone()[0] == 0
    finally:
        conn.close()
