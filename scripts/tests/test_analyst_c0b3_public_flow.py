"""Offline production-flow proof for the C0B-3 public namespace."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from scripts.analyst_benchmark import c0b2_runtime as runtime
from scripts.analyst_benchmark import c0b2_runtime_d as runtime_d
from scripts.analyst_benchmark import c0b2_runtime_f as runtime_f
from scripts.analyst_benchmark.c0b2_transport import (
    BoundedOllamaTransport,
    RequestSpec,
)
from scripts.analyst_benchmark.c0b2_checkpoint import (
    ImmutableViolation,
    sha256_json,
)
from scripts.analyst_benchmark.c0b2_runtime_common import abandon_public_run
from scripts.analyst_benchmark.c0b3_policy import (
    BENCHMARK_PROTOCOL_ID,
    POLICY_ID,
    POLICY_SHA256,
)
from scripts.tests.test_analyst_c0b2_public_flow import (
    _FakeOllamaSession,
    _identifier_labels,
    _install_no_external_access_guards,
)


def _factory(session: _FakeOllamaSession):
    def build(
            resolver: Callable[[Any], RequestSpec],
            header: Mapping[str, Any],
    ) -> BoundedOllamaTransport:
        return BoundedOllamaTransport(
            session.resolver(resolver), endpoint=header["ollama_endpoint"],
            session=session)
    return build


def _reach_final_d(
        root: Path, run_id: str, session: _FakeOllamaSession,
) -> None:
    factory = _factory(session)
    runtime.create_public_run(
        benchmark_root=root, run_id=run_id,
        protocol_id=BENCHMARK_PROTOCOL_ID)
    c_result = runtime.run_public_stage_c(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert c_result["state"] == "PAUSED_STAGE_BOUNDARY"
    d_result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert d_result["state"] == "PAUSED_STAGE_BOUNDARY"
    assert d_result["active_plan_key"] == "D4_CONFIRMATION"


def _run_f_to_terminal(
        root: Path, run_id: str, session: _FakeOllamaSession,
) -> dict[str, Any]:
    factory = _factory(session)
    result = runtime_f.run_public_stage_f(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    for _ in range(7):
        if result["state"] in {"SELECTED", "INCONCLUSIVE"}:
            return result
        result = runtime_f.run_public_stage_f(
            run_id, resume=True, benchmark_root=root,
            transport_factory=factory,
            expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    pytest.fail(f"C0B-3 F did not reach a quality terminal: {result}")


def _assert_current_terminal(root: Path, run_id: str, terminal: str) -> None:
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        header = json.loads(conn.execute(
            "SELECT json FROM run_header WHERE id=1").fetchone()[0])
        artifact = json.loads(conn.execute(
            "SELECT artifact_json FROM public_artifacts "
            "WHERE artifact_id='stage-f-result'").fetchone()[0])
        decision_id, completion_raw = conn.execute(
            "SELECT decision_id,value_json FROM decisions "
            "WHERE decision_id='c0b3-completion'").fetchone()
    binding = {"policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}
    assert header["benchmark_protocol_id"] == BENCHMARK_PROTOCOL_ID
    assert {key: header[key] for key in binding} == binding
    assert artifact["version"] == "c0b3-result-v1"
    assert artifact["terminal"] == terminal
    assert {key: artifact[key] for key in binding} == binding
    completion = json.loads(completion_raw)
    assert decision_id == "c0b3-completion"
    assert completion["version"] == "c0b3-completion-v1"
    assert completion["outcome"] == terminal
    assert {key: completion[key] for key in binding} == binding
    status = runtime.public_status(run_id, benchmark_root=root)
    assert status["state"] == terminal
    assert status["benchmark_protocol_id"] == BENCHMARK_PROTOCOL_ID
    assert status["policy_id"] == POLICY_ID
    assert status["policy_sha256"] == POLICY_SHA256
    verified = runtime.public_verify(run_id, benchmark_root=root)
    assert verified["ok"] is True, verified["errors"]
    assert verified["benchmark_protocol_id"] == BENCHMARK_PROTOCOL_ID
    assert verified["policy_id"] == POLICY_ID
    assert verified["policy_sha256"] == POLICY_SHA256


def test_current_stage_c_no_survivor_is_policy_bound_and_verifiable(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cover the deterministic current Stage-C terminal without reaching D or F."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root, run_id = tmp_path / "bench", "c0b3-stage-c-no-survivor"
    session = _FakeOllamaSession(
        _identifier_labels(), no_findings_stages=frozenset({"C"}),
        inject_crash=False, inject_stream_error=False)
    runtime.create_public_run(
        benchmark_root=root, run_id=run_id,
        protocol_id=BENCHMARK_PROTOCOL_ID)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root, transport_factory=_factory(session),
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert result["state"] == "INCONCLUSIVE"
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        event = json.loads(conn.execute(
            "SELECT detail_json FROM events WHERE kind='FINAL_ARTIFACT'"
        ).fetchone()[0])
        artifact = event["artifact"]
        completion = json.loads(conn.execute(
            "SELECT value_json FROM decisions WHERE decision_id='c0b3-completion'"
        ).fetchone()[0])
    binding = {"policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}
    assert artifact["stage"] == "C" and artifact["reason"] == "no_stage_c_survivor"
    assert {key: artifact[key] for key in binding} == binding
    assert completion["facts"]["reason"] == "no_stage_c_survivor"
    assert {key: completion[key] for key in binding} == binding
    verified = runtime.public_verify(run_id, benchmark_root=root)
    assert verified["ok"] is True, verified["errors"]
    assert {key: verified[key] for key in binding} == binding
    with sqlite3.connect(checkpoint) as conn:
        raw = conn.execute(
            "SELECT value_json FROM decisions WHERE decision_id='c0b3-completion'"
        ).fetchone()[0]
        legacy = json.loads(raw)
        legacy.pop("policy_id")
        legacy.pop("policy_sha256")
        legacy.pop("version")
        conn.execute(
            "UPDATE decisions SET decision_id='c0b2-completion',value_json=? "
            "WHERE decision_id='c0b3-completion'",
            (runtime.canonical_json(legacy),))
    poisoned = checkpoint.read_bytes()
    with pytest.raises((runtime.RuntimeGateError, ValueError, ImmutableViolation)):
        runtime.public_status(run_id, benchmark_root=root)
    assert runtime.public_verify(run_id, benchmark_root=root)["ok"] is False
    assert checkpoint.read_bytes() == poisoned


def test_current_d_terminal_rejects_legacy_decision_lineage(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A current D terminal verifies, then fails closed if its decision becomes v1."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root, run_id = tmp_path / "bench", "c0b3-stage-d-mixed-lineage"
    session = _FakeOllamaSession(
        _identifier_labels(), no_findings_stages=frozenset({"D"}),
        inject_crash=False, inject_stream_error=False)
    factory = _factory(session)
    runtime.create_public_run(
        benchmark_root=root, run_id=run_id, protocol_id=BENCHMARK_PROTOCOL_ID)
    assert runtime.run_public_stage_c(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)["state"] == "PAUSED_STAGE_BOUNDARY"
    result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert result["state"] == "INCONCLUSIVE"
    assert runtime.public_verify(run_id, benchmark_root=root)["ok"] is True
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        row = conn.execute(
            "SELECT value_json FROM decisions WHERE decision_id='stage-d-d1-selection'"
        ).fetchone()
        decision = json.loads(row[0])
        assert decision["version"] == "stage-d-decision-v2"
        decision.pop("policy_id")
        decision.pop("policy_sha256")
        decision["version"] = "stage-d-decision-v1"
        conn.execute(
            "UPDATE decisions SET value_json=? WHERE decision_id='stage-d-d1-selection'",
            (runtime.canonical_json(decision),))
    verified = runtime.public_verify(run_id, benchmark_root=root)
    assert verified["ok"] is False
    assert "backup_anchor_invalid:ImmutableViolation" in verified["errors"]


def test_current_d_abandon_validates_lineage_before_and_after_mutation(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root, run_id = tmp_path / "bench", "c0b3-stage-d-abandon-lineage"
    session = _FakeOllamaSession(
        _identifier_labels(), inject_crash=False, inject_stream_error=False)
    _reach_final_d(root, run_id, session)
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        current_raw = conn.execute(
            "SELECT value_json FROM decisions WHERE decision_id='stage-d-selection'"
        ).fetchone()[0]
        legacy = json.loads(current_raw)
        legacy.pop("policy_id")
        legacy.pop("policy_sha256")
        legacy["version"] = "stage-d-decision-v1"
        conn.execute(
            "UPDATE decisions SET value_json=? WHERE decision_id='stage-d-selection'",
            (runtime.canonical_json(legacy),))
    with pytest.raises(ImmutableViolation):
        abandon_public_run(
            run_id, benchmark_root=root,
            expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    with sqlite3.connect(checkpoint) as conn:
        assert conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0] \
            == "PAUSED_STAGE_BOUNDARY"
        conn.execute(
            "UPDATE decisions SET value_json=? WHERE decision_id='stage-d-selection'",
            (current_raw,))
    abandoned = abandon_public_run(
        run_id, benchmark_root=root,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert abandoned["state"] == "ABANDONED"
    with sqlite3.connect(checkpoint) as conn:
        conn.execute(
            "UPDATE decisions SET value_json=? WHERE decision_id='stage-d-selection'",
            (runtime.canonical_json(legacy),))
    poisoned = checkpoint.read_bytes()
    with pytest.raises(ImmutableViolation):
        runtime.public_status(run_id, benchmark_root=root)
    verified = runtime.public_verify(run_id, benchmark_root=root)
    assert verified["ok"] is False
    assert any(error.startswith("backup_anchor_invalid:")
               for error in verified["errors"])
    assert checkpoint.read_bytes() == poisoned


def test_current_d_blocked_terminal_receipts_after_nonce_and_source_drift(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retain the frozen provenance-terminal receipt exception for C0B-3."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root, run_id = tmp_path / "bench", "c0b3-d-blocked-provenance"
    session = _FakeOllamaSession(
        _identifier_labels(), inject_crash=False, inject_stream_error=False)
    factory = _factory(session)
    runtime.create_public_run(
        benchmark_root=root, run_id=run_id,
        protocol_id=BENCHMARK_PROTOCOL_ID)
    assert runtime.run_public_stage_c(
        run_id, benchmark_root=root, transport_factory=factory,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)["state"] \
        == "PAUSED_STAGE_BOUNDARY"
    checkpoint = runtime._checkpoint_path(run_id, root)
    point = runtime.Checkpoint.open(checkpoint, root)
    try:
        inputs = runtime_d.load_stage_d_inputs(point)
        runtime_d.start_stage_d(point, inputs)
        runtime.finish_public_run_failure(
            point, terminal="BLOCKED_PROVENANCE")
        malformed_key = {
            "version": "c0b2-run-nonce-key-v1", "key_hex": "0" * 63,
        }
        point.conn.execute(
            "UPDATE manifests SET manifest_hash=?,manifest_json=? "
            "WHERE name='run_nonce_key'",
            (sha256_json(malformed_key), runtime.canonical_json(malformed_key)),
        )
    finally:
        point.close()
    monkeypatch.setattr(
        runtime, "revalidate_source_pins",
        lambda _header: (_ for _ in ()).throw(
            runtime.RuntimeGateError("source drift")))
    contacted = False

    def forbidden_transport(*_args, **_kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("terminal re-entry must not construct transport")

    result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=root, transport_factory=forbidden_transport,
        expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    assert result["state"] == "BLOCKED_PROVENANCE"
    reopened = runtime.Checkpoint.open(checkpoint, root)
    try:
        assert reopened.conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == 2
        assert contacted is False
    finally:
        reopened.close()


def test_current_final_d_provenance_receipt_rejects_mixed_selection(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Skip nonce rederive, but never skip the unreferenced final D owner."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root, run_id = tmp_path / "bench", "c0b3-final-d-provenance"
    session = _FakeOllamaSession(
        _identifier_labels(), inject_crash=False, inject_stream_error=False)
    _reach_final_d(root, run_id, session)
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        malformed_key = {
            "version": "c0b2-run-nonce-key-v1", "key_hex": "0" * 63,
        }
        conn.execute(
            "UPDATE manifests SET manifest_hash=?,manifest_json=? "
            "WHERE name='run_nonce_key'",
            (sha256_json(malformed_key), runtime.canonical_json(malformed_key)),
        )
    monkeypatch.setattr(
        runtime, "revalidate_source_pins",
        lambda _header: (_ for _ in ()).throw(
            runtime.RuntimeGateError("source drift")))
    with pytest.raises(runtime.RuntimeGateError, match="source drift"):
        runtime_d.run_public_stage_d(
            run_id, benchmark_root=root,
            transport_factory=lambda *_args, **_kwargs: pytest.fail(
                "provenance terminal must not construct transport"),
            expected_protocol_id=BENCHMARK_PROTOCOL_ID)
    with sqlite3.connect(checkpoint) as conn:
        assert conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0] \
            == "BLOCKED_PROVENANCE"
        assert conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0] == 3
        raw = conn.execute(
            "SELECT value_json FROM decisions "
            "WHERE decision_id='stage-d-selection'").fetchone()[0]
        legacy = json.loads(raw)
        legacy.pop("policy_id")
        legacy.pop("policy_sha256")
        legacy["version"] = "stage-d-decision-v1"
        conn.execute(
            "UPDATE decisions SET value_json=? "
            "WHERE decision_id='stage-d-selection'",
            (runtime.canonical_json(legacy),))
    poisoned = checkpoint.read_bytes()
    with pytest.raises((ValueError, ImmutableViolation, runtime.RuntimeGateError)):
        runtime.public_status(run_id, benchmark_root=root)
    verified = runtime.public_verify(run_id, benchmark_root=root)
    assert verified["ok"] is False
    assert any(error.startswith("backup_anchor_invalid:")
               for error in verified["errors"])
    assert checkpoint.read_bytes() == poisoned


@pytest.mark.parametrize(("no_findings", "terminal"), [
    (False, "SELECTED"),
    (True, "INCONCLUSIVE"),
])
def test_current_fake_transport_reaches_both_f_terminals(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        no_findings: bool, terminal: str,
) -> None:
    """Drive real C/D/F orchestration without model, network, or private data."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda *_args: None)
    _install_no_external_access_guards(monkeypatch)
    root = tmp_path / "bench"
    stages = frozenset({"F"}) if no_findings else frozenset()
    session = _FakeOllamaSession(
        _identifier_labels(), no_findings_stages=stages,
        inject_crash=False, inject_stream_error=False)
    run_id = f"c0b3-public-flow-{terminal.lower()}"
    _reach_final_d(root, run_id, session)
    result = _run_f_to_terminal(root, run_id, session)
    assert result["state"] == terminal
    _assert_current_terminal(root, run_id, terminal)
    if terminal == "SELECTED":
        checkpoint = runtime._checkpoint_path(run_id, root)
        with sqlite3.connect(checkpoint) as conn:
            raw = conn.execute(
                "SELECT value_json FROM decisions "
                "WHERE decision_id='stage-f-provisional-selection'"
            ).fetchone()[0]
            legacy = json.loads(raw)
            legacy.pop("policy_id")
            legacy.pop("policy_sha256")
            legacy["version"] = "stage-f-selection-v1"
            conn.execute(
                "UPDATE decisions SET value_json=? "
                "WHERE decision_id='stage-f-provisional-selection'",
                (runtime.canonical_json(legacy),))
        poisoned = checkpoint.read_bytes()
        with pytest.raises((ImmutableViolation, runtime.RuntimeGateError)):
            runtime.public_status(run_id, benchmark_root=root)
        verified = runtime.public_verify(run_id, benchmark_root=root)
        assert verified["ok"] is False
        assert any(error.startswith("backup_anchor_invalid:")
                   for error in verified["errors"])
        assert checkpoint.read_bytes() == poisoned
    assert session.paths and all(response.close_count == 1
                                 for response in session.responses)
