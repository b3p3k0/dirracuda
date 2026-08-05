"""Offline creation and identity guards for the C0B-2B1 public runtime."""
from __future__ import annotations

import json
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

    real_backup = runtime.backup_snapshot
    monkeypatch.setattr(
        runtime, "backup_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("snapshot fault")))
    with pytest.raises(OSError, match="snapshot fault"):
        runtime.run_public_stage_c(
            run_id, benchmark_root=tmp_path / "bench",
            transport_factory=lambda resolver, _header: FakeTransport(resolver))

    assert runtime.public_status(
        run_id, benchmark_root=tmp_path / "bench") == {
            "state": "INCONCLUSIVE", "calls_total": 272}
    monkeypatch.setattr(runtime, "backup_snapshot", real_backup)
    result = runtime.run_public_stage_c(
        run_id, resume=True, benchmark_root=tmp_path / "bench",
        transport_factory=lambda *_args: pytest.fail(
            "snapshot repair must not construct a transport"))
    assert result == {
        "run_id": run_id, "stage": "C", "state": "INCONCLUSIVE",
        "calls_total": 272,
    }
    assert any(
        runtime.status_readonly(path)["state"] == "INCONCLUSIVE"
        for path in (tmp_path / "bench" / "snapshots" / run_id).glob(
            "snapshot-*.sqlite3"))
    assert calls.count("chat") == 264
    assert calls.count("ps") == 3
    assert calls.count("version") == 1
    assert calls.count("tags") == 1
    assert calls.count("show") == 3
    assert runtime.public_verify(
        run_id, benchmark_root=tmp_path / "bench") == {"ok": True, "errors": []}


def test_runtime_specs_cross_the_real_bounded_transport_offline(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prove runtime-built specs against the adapter using fake HTTP only."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
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
