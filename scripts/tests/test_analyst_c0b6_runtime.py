"""Offline scheduler and recovery tests for the frozen C0B-6 runtime."""
from __future__ import annotations

import ast
import json
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import c0b6_runtime as runtime
from scripts.analyst_benchmark.c0b6_policy import FAILURE_ORIGINS
from scripts.analyst_benchmark.c0b2_executor import FakeResponse, RetryableTransport
from scripts.analyst_benchmark.c0b2_plan import stable_hash
from scripts.analyst_benchmark.c0b2_transport import RequestSpec


def _hash(label: str) -> str:
    return stable_hash({"label": label})


def _header() -> dict[str, str]:
    return {
        "policy_id": runtime.POLICY_ID,
        "policy_sha256": runtime.POLICY_SHA256,
        "protocol_sha256": _hash("protocol"),
        "run_id": "c0b6-20260818-120000-0123456789abcdef01234567",
        "ollama_version": runtime.OLLAMA_VERSION,
    }


class FakePoint:
    """Content-local checkpoint double; no filesystem, model, or network I/O."""

    def __init__(self) -> None:
        self.attempts: list[dict] = []
        self.artifacts: dict[tuple[str, str], dict] = {}
        self.events: list[dict] = []
        self.inflight: str | None = None
        self._state = "RUNNING"
        self.clock = 1_800_000_000.0
        self.invocations = 0
        self.conn = object()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def header(self):
        return _header()

    def state(self):
        return self._state

    def transition(self, state):
        self._state = state

    set_state = transition

    def claim_invocation(self):
        self.invocations += 1
        return self.invocations

    begin_invocation = claim_invocation

    def list_attempts(self):
        return [dict(row) for row in self.attempts]

    def precharge(self, **row):
        assert self.inflight is None
        self.inflight = row["attempt_id"]
        self.attempts.append({
            **row, "state": "DISPATCHING", "payload": None,
            "created": self.clock, "updated": self.clock,
        })

    def record_attempt(self, attempt_id, state, payload=None):
        assert self.inflight == attempt_id
        row = next(row for row in self.attempts if row["attempt_id"] == attempt_id)
        row.update(state=state, payload=payload, updated=self.clock)
        self.inflight = None

    def record_cancelled_attempt(self, attempt_id, *, first_byte_seen,
                                 cancel_elapsed_ms):
        deadline = datetime.fromtimestamp(
            self.clock + 2, timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z")
        self.record_attempt(attempt_id, "CANCELLED_UNVERIFIED", {
            "answered": False, "first_byte_seen": first_byte_seen,
            "cancel_elapsed_ms": cancel_elapsed_ms,
            "health_not_before_utc": deadline,
        })
        return deadline

    def read_artifact(self, kind, owner):
        value = self.artifacts.get((kind, owner))
        return None if value is None else dict(value)

    def store_artifact(self, kind, owner, value):
        prior = self.artifacts.get((kind, owner))
        if prior is not None:
            assert prior == value
        self.artifacts[(kind, owner)] = dict(value)
        return runtime.sha256_json(value)

    def store_runtime_event(self, value):
        self.events.append(dict(value))
        return value["event_sha256"]

    def list_runtime_events(self):
        return [dict(row) for row in self.events]

    def recover_dispatching(self):
        recovered = []
        for row in self.attempts:
            if row["state"] == "DISPATCHING":
                row["state"] = "ORPHANED_UNKNOWN"
                recovered.append(dict(row))
        self.inflight = None
        return recovered

    def nonce_key(self):
        return b"x" * 32

    read_nonce_key = nonce_key

    def finalize(self, terminal, artifact, completion=None):
        self._state = terminal
        quality = terminal in {"CONFIRMED", "INCONCLUSIVE"}
        self.artifacts[("result" if quality else "failure", "terminal")] = dict(artifact)
        if completion is not None:
            self.artifacts[("completion", "terminal")] = dict(completion)
        return (runtime.sha256_json(artifact),
                runtime.sha256_json(completion) if completion is not None else None)

    def _assert_parent_unchanged(self):
        return None

    def parent_paths(self):
        return ("parent-a", "parent-b", "parent-c", "parent-d")

    def parent_paths(self):
        return tuple(Path(f"/unused/parent-{index}") for index in range(4))


def _metadata(*, strict=False, semantic=False):
    return {
        "strict_schema_invalid": strict,
        "semantic_invalid": semantic,
        "done_reason": "stop",
        "prompt_eval_count": 100,
        "tools_empty": True,
        "images_empty": True,
        "unknown_message_fields_empty": True,
        "raw_response_sha256": _hash("response"),
    }


def _answer(findings=()):
    return json.dumps({
        "document_type": "fixture",
        "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": list(findings),
    }, separators=(",", ":"))


def _work(label="work"):
    return {
        "work_id": _hash(label),
        "request_sha256": _hash(f"request:{label}"),
        "model": "qwen3.6:27b",
        "worksheet": "v2",
        "doc_id": f"doc-{label}",
        "chunk_index": 0,
        "nonce": "FENCE_" + "A" * 32,
        "source": "source text",
    }


def _control_spec():
    return RequestSpec(
        kind="chat", payload={}, worksheet="v2",
        expected_model="qwen3.6:27b",
        expected_digest=runtime.SELECTION["model_digest"],
    )


def test_runtime_event_reconciliation_closes_crash_gap_once() -> None:
    point, work = FakePoint(), _work()
    point.attempts.append({
        "attempt_id": _hash("attempt"), "owner_id": work["work_id"],
        "request_sha256": work["request_sha256"], "state": "RAW_VALID",
        "payload": {"answered": True}, "created": 1_700_000_000.0,
        "updated": 1_700_000_001.0, "invocation_ordinal": 1,
    })
    resolved = {"work": work, "lane": {"lane_id": "F72_20260811"}}
    resolver = SimpleNamespace(
        work_ids=frozenset({work["work_id"]}), control_ids=frozenset(),
        prepared=SimpleNamespace(resolve_work=lambda _owner: resolved),
    )

    runtime.reconcile_runtime_events(point, resolver)
    runtime.reconcile_runtime_events(point, resolver)

    assert [row["event"] for row in point.events] == ["DISPATCHING", "RAW_VALID"]
    assert all(row["version"] == "c0b6-runtime-event-v1" for row in point.events)


def test_reconciliation_skips_exact_c0b6_preflight_owner() -> None:
    point = FakePoint()
    point.attempts.append({
        "attempt_id": _hash("preflight-attempt"),
        "owner_id": stable_hash({"c0b6_preflight": "version", "invocation": 4}),
        "request_sha256": _hash("preflight-request"), "state": "RAW_VALID",
        "call_class": "preflight_control", "invocation_ordinal": 4,
        "payload": {"answered": True}, "created": 1.0, "updated": 2.0,
    })
    resolver = SimpleNamespace(
        work_ids=frozenset(), control_ids=frozenset(), prepared=SimpleNamespace())

    runtime.reconcile_runtime_events(point, resolver)

    assert point.events == []


def test_preflight_is_exactly_three_serial_precharged_calls() -> None:
    point = FakePoint()
    calls = []

    def transport(request, _cancel):
        assert point.inflight == request.attempt_id
        calls.append(request.control_id)
        return FakeResponse("{}", {"response_sha256": _hash(request.control_id)})

    outcome, attempt = runtime._run_preflight(
        point, SimpleNamespace(controls={}), transport, 3)

    assert (outcome, attempt) == ("RAW_VALID", None)
    assert len(calls) == 3
    assert [row["call_class"] for row in point.attempts] == [
        "preflight_control"] * 3
    assert point.inflight is None


def test_preflight_transport_failure_stops_serial_dispatch() -> None:
    point = FakePoint()
    calls = 0

    def unavailable(*_args):
        nonlocal calls
        calls += 1
        raise RetryableTransport("shared GPU busy")

    outcome, attempt = runtime._run_preflight(
        point, SimpleNamespace(controls={}), unavailable, 1)

    assert outcome == "RETRYABLE_TRANSPORT"
    assert attempt == point.attempts[0]["attempt_id"]
    assert calls == 1 and point.inflight is None


def test_context_is_durable_before_schema_retry(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    context_id = _hash("context")
    controls = {"context": SimpleNamespace(
        control={"control_id": context_id})}
    order = []
    responses = [
        FakeResponse("{}", _metadata(strict=True), False, "SCHEMA_INVALID"),
        FakeResponse(_answer(), _metadata(), True, "ACCEPTED"),
    ]
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)

    def context(*_args, **_kwargs):
        order.append("context")
        point.artifacts[("context_evidence", context_id)] = {"ok": True}
        return "RAW_VALID", _hash("context-evidence")

    monkeypatch.setattr(runtime, "_ensure_context", context)

    def transport(_request, _cancel):
        order.append("scored")
        return responses.pop(0)

    outcome, owner = runtime._run_lane_work(
        point, SimpleNamespace(), transport, ordinal=1,
        lane={"lane_id": "F72_20260811", "work": [work]},
        controls=controls,
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())

    assert (outcome, owner) == ("RAW_VALID", None)
    assert order == ["scored", "context", "context", "scored"]
    assert [row["call_class"] for row in point.attempts] == ["scored", "schema_retry"]


def test_lane_transport_failure_pauses_without_second_inflight_call(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)

    def unavailable(*_args):
        raise RetryableTransport("shared GPU busy")

    outcome, attempt = runtime._run_lane_work(
        point, SimpleNamespace(), unavailable, ordinal=1,
        lane={"lane_id": "F72_20260818", "work": [work]},
        controls={},
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())

    assert outcome == "PAUSED_RESOURCE"
    assert attempt == point.attempts[0]["attempt_id"]
    assert point.attempts[0]["state"] == "RETRYABLE_TRANSPORT"
    assert point.inflight is None


def test_context_crash_recovery_reuses_valid_response_without_http() -> None:
    point, work = FakePoint(), _work()
    context_id = _hash("context-crash")
    control = {
        "control_id": context_id,
        "purpose": "c0b6_stage_f_candidate_context",
        "candidate_id": _hash("candidate"),
        "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"),
        "prompt_sha256": _hash("prompt"),
    }
    controls = {"context": SimpleNamespace(
        control=control, request_spec=RequestSpec(kind="ps"))}
    point.attempts.append({
        "attempt_id": _hash("context-attempt"), "owner_id": context_id,
        "state": "RAW_VALID", "payload": {
            "answered": True,
            "response": json.dumps({"context_length": 8192}, separators=(",", ":")),
            "metadata": {"response_sha256": _hash("ps")}},
    })

    result = runtime._ensure_context(
        point, SimpleNamespace(controls={}),
        lambda *_args: (_ for _ in ()).throw(AssertionError("duplicate HTTP")),
        ordinal=2, controls=controls, trigger_work=work,
        trigger_attempt_id=_hash("trigger"))

    assert result[0] == "RAW_VALID"
    evidence = point.artifacts[("context_evidence", context_id)]
    assert evidence["version"] == "c0b6-context-evidence-v1"
    assert evidence["lane_id"] == "F72_20260811"


def test_existing_context_rederives_trigger_from_first_answered_attempt() -> None:
    point = FakePoint()
    first, second = _work("first-trigger"), _work("second-trigger")
    context_id = _hash("context-trigger-order")
    control = {
        "control_id": context_id,
        "purpose": "c0b6_stage_f_candidate_context",
        "candidate_id": _hash("candidate"),
        "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"),
        "prompt_sha256": _hash("prompt"),
    }
    controls = {
        "context": SimpleNamespace(
            control=control, request_spec=RequestSpec(kind="ps")),
        "cancellation": SimpleNamespace(
            control={"control_id": _hash("cancel")}),
    }
    point.artifacts[("lane_plan", "F72_20260811")] = {
        "lane_id": "F72_20260811", "work": [first, second]}
    point.attempts.extend([{
        "attempt_id": _hash("first-attempt"), "owner_id": first["work_id"],
        "state": "RAW_VALID", "payload": {"response": "{}"},
    }, {
        "attempt_id": _hash("second-attempt"), "owner_id": second["work_id"],
        "state": "RAW_VALID", "payload": {"response": "{}"},
    }, {
        "attempt_id": _hash("context-attempt"), "owner_id": context_id,
        "state": "RAW_VALID", "payload": {
            "answered": True,
            "response": json.dumps({"context_length": 8192}, separators=(",", ":")),
            "metadata": {"response_sha256": _hash("ps")}},
    }])
    resolver = SimpleNamespace(prepared=SimpleNamespace(
        resolve_work=lambda _owner: {"chunk_text": "source text"}), controls={})

    runtime._ensure_context(
        point, resolver, lambda *_args: None, ordinal=1, controls=controls,
        trigger_work=second, trigger_attempt_id=_hash("second-attempt"))

    with pytest.raises(
            runtime.C0B6RuntimeError,
            match="differs on resume|does not rederive"):
        runtime._verify_existing_control_evidence(point, resolver, controls)


def test_malformed_context_is_failed_before_raw_valid() -> None:
    point, work = FakePoint(), _work()
    context_id = _hash("context-malformed")
    control = {
        "control_id": context_id,
        "purpose": "c0b6_stage_f_candidate_context",
        "candidate_id": _hash("candidate"),
        "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"),
        "prompt_sha256": _hash("prompt"),
    }
    controls = {"context": SimpleNamespace(
        control=control, request_spec=RequestSpec(kind="ps"))}

    outcome, attempt_id = runtime._ensure_context(
        point, SimpleNamespace(controls={}),
        lambda *_args: FakeResponse("{}", {"response_sha256": _hash("ps")}),
        ordinal=1, controls=controls, trigger_work=work,
        trigger_attempt_id=_hash("trigger"))

    assert outcome == "FAILED_SAFETY"
    assert next(row for row in point.list_attempts()
                if row["attempt_id"] == attempt_id)["state"] == "FAILED_SAFETY"


def test_soft_wall_and_operator_cancel_charge_nothing(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)
    common = dict(
        point=point, resolver=SimpleNamespace(), transport=lambda *_: None,
        ordinal=1, lane={"lane_id": "C44_1", "work": [work]},
        controls={}, clock_started=0,
        soft_wall_seconds=10,
    )
    assert runtime._run_lane_work(
        **common, monotonic=lambda: 10,
        cancellation=threading.Event())[0] == "PAUSED_SOFT_WALL"
    cancelled = threading.Event()
    cancelled.set()
    assert runtime._run_lane_work(
        **common, monotonic=lambda: 1,
        cancellation=cancelled)[0] == "CANCELLED_PENDING_RESUME"
    assert point.attempts == []


def test_planned_cancellation_waits_then_health_checks() -> None:
    point = FakePoint()
    cancel_id, health_id = _hash("cancel"), _hash("health")
    controls = {
        "cancellation": SimpleNamespace(
            control={"control_id": cancel_id, "candidate_id": _hash("candidate")},
            request_spec=_control_spec()),
        "health": SimpleNamespace(
            control={"control_id": health_id, "health_work_id": _hash("health-work"),
                     "prompt_sha256": _hash("prompt")},
            request_spec=_control_spec(), source_chunk="SSN 900-12-3456"),
    }
    events = []

    def transport(request, cancel):
        if request.control_id == cancel_id:
            events.append("cancel")
            cancel.set()
            raise RetryableTransport("cancelled")
        events.append("health")
        finding = {"category": "pii", "quote": "900-12-3456", "offset": 4}
        return FakeResponse(_answer([finding]), _metadata(), True, "ACCEPTED")

    outcome, digest = runtime._run_cancel_health(
        point, SimpleNamespace(controls={}), transport, ordinal=1,
        controls=controls, monotonic=lambda: 1.0,
        sleep=lambda seconds: events.append(f"sleep:{seconds}"),
        now=lambda: point.clock)

    assert outcome == "RAW_VALID" and len(digest) == 64
    assert events == ["cancel", "sleep:2.0", "health"]
    assert point.attempts[0]["state"] == "CANCELLED_UNVERIFIED"
    evidence = point.artifacts[("cancellation_health_evidence", cancel_id)]
    assert evidence["passed"] is True
    assert evidence["version"] == "c0b6-cancellation-health-evidence-v1"


def test_cancel_success_reused_when_health_resumes_as_orphan() -> None:
    point = FakePoint()
    cancel_id, health_id = _hash("cancel-resume"), _hash("health-resume")
    controls = {
        "cancellation": SimpleNamespace(
            control={"control_id": cancel_id, "candidate_id": _hash("candidate")},
            request_spec=_control_spec()),
        "health": SimpleNamespace(
            control={"control_id": health_id, "health_work_id": _hash("health-work"),
                     "prompt_sha256": _hash("prompt")},
            request_spec=_control_spec(), source_chunk="SSN 900-12-3456"),
    }
    calls = []

    def first(request, cancel):
        if request.control_id == cancel_id:
            calls.append("cancel")
            cancel.set()
            raise RetryableTransport("cancelled")
        calls.append("health-busy")
        raise RetryableTransport("busy")

    paused = runtime._run_cancel_health(
        point, SimpleNamespace(controls={}), first, ordinal=1,
        controls=controls, monotonic=lambda: 1.0,
        sleep=lambda _seconds: None, now=lambda: point.clock)
    assert paused[0] == "PAUSED_RESOURCE"

    def resumed(request, _cancel):
        calls.append("health-ok")
        finding = {"category": "pii", "quote": "900-12-3456", "offset": 4}
        return FakeResponse(_answer([finding]), _metadata(), True, "ACCEPTED")

    done = runtime._run_cancel_health(
        point, SimpleNamespace(controls={}), resumed, ordinal=2,
        controls=controls, monotonic=lambda: 2.0,
        sleep=lambda _seconds: None, now=lambda: point.clock + 10)

    assert done[0] == "RAW_VALID"
    assert calls == ["cancel", "health-busy", "health-ok"]
    assert [row["call_class"] for row in point.attempts
            if row["owner_id"] == health_id] == [
                "preflight_control", "transport_orphan"]


def test_schema_retry_budget_is_partitioned_per_lane() -> None:
    point = FakePoint()
    first, second = _work(), _work("work-2")
    invalid = {"answered": True, "response": "{}", "metadata": {}}
    point.attempts = [{
        "attempt_id": _hash("a1"), "owner_id": first["work_id"],
        "call_class": "schema_retry", "state": "SCHEMA_INVALID",
        "payload": invalid,
    }, {
        "attempt_id": _hash("a2"), "owner_id": second["work_id"],
        "call_class": "scored", "state": "SCHEMA_INVALID", "payload": invalid,
    }]
    both = frozenset({first["work_id"], second["work_id"]})

    assert runtime._next_work_disposition(point, second, both)[0] == "budget"
    assert runtime._next_work_disposition(
        point, second, frozenset({second["work_id"]}))[0] == "schema_retry"


def test_activation_partition_and_cursor_census_are_exact() -> None:
    point = FakePoint()
    lanes = [{
        "lane_id": lane, "plan_sha256": _hash(f"plan:{lane}"),
        "work": [{"work_id": _hash(f"work:{lane}")}],
    } for lane in runtime.LANE_ORDER]
    master = {
        "lane_plans": [{"payload": lanes[0]}, {"payload": lanes[1]}],
        "acceptance_template": {"payload": lanes[2]},
    }

    runtime._activate_lane(point, master, lanes[0], _hash("master"))
    runtime._cursor_transition(
        point, lane=lanes[0], next_lane=lanes[1],
        aggregate_sha256=_hash("aggregate"))

    activation = point.artifacts[("plan_activation", "F72_20260811")]
    assert activation["activated_work_ids"] == [lanes[0]["work"][0]["work_id"]]
    assert activation["inactive_work_ids"] == sorted([
        lanes[1]["work"][0]["work_id"], lanes[2]["work"][0]["work_id"]])
    cursor = point.artifacts[("cursor_transition", "F72_20260811")]
    assert cursor["from_lane_id"] == "F72_20260811"
    assert cursor["to_lane_id"] == "F72_20260818"


@pytest.mark.parametrize("lane_id,reason", [
    ("F72_20260811", "seed20260811_no_qualifier"),
    ("F72_20260818", "seed20260818_no_qualifier"),
])
def test_quality_failure_reason_owns_exact_stop_point(
        monkeypatch, lane_id, reason) -> None:
    point = FakePoint()
    master = {"master": True}
    first_hash = _hash("first")
    second_hash = _hash("second") if lane_id == "F72_20260818" else None
    backed_up = []
    monkeypatch.setattr(runtime, "ensure_backup_receipt",
                        lambda *_args, **_kwargs: backed_up.append(True))

    runtime._finish_quality(
        point, SimpleNamespace(), master=master, terminal="INCONCLUSIVE",
        reason=reason, first_hash=first_hash, second_hash=second_hash)

    result = point.artifacts[("result", "terminal")]
    assert result["terminal"] == "INCONCLUSIVE" and result["reason"] == reason
    assert result["lane_aggregate_sha256s"]["f72_seed20260811_sha256"] == first_hash
    assert (result["lane_aggregate_sha256s"]["f72_seed20260818_sha256"]
            == second_hash)
    assert ("completion", "terminal") in point.artifacts
    assert backed_up == [True]


def test_failure_terminal_owns_evidence_and_no_completion(monkeypatch) -> None:
    point = FakePoint()
    backed_up = []
    monkeypatch.setattr(runtime, "ensure_backup_receipt",
                        lambda *_args, **_kwargs: backed_up.append(True))

    result = runtime._finish_failure(
        point, SimpleNamespace(), terminal="BLOCKED_BUDGET",
        failure_origin="budget_claim",
        lane_id="F72_20260818", plan_sha256=_hash("plan"))

    assert result["state"] == "BLOCKED_BUDGET"
    assert ("failure_evidence", "terminal") in point.artifacts
    assert ("failure", "terminal") in point.artifacts
    assert point.artifacts[("failure", "terminal")]["failure_origin"] == \
        "budget_claim"
    assert ("completion", "terminal") not in point.artifacts
    assert backed_up == [True]


def test_every_durable_failure_call_names_a_closed_origin() -> None:
    tree = ast.parse(Path(runtime.__file__).read_text())
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "_finish_failure"]
    assert len(calls) >= 15
    for call in calls:
        keyword = next((item for item in call.keywords
                        if item.arg == "failure_origin"), None)
        assert keyword is not None
        if isinstance(keyword.value, ast.Constant):
            assert keyword.value.value in FAILURE_ORIGINS

    assert runtime._origin_for_terminal(
        "FAILED_SAFETY", "lane_execution") == "safety_transport"
    assert runtime._origin_for_terminal(
        "BLOCKED_BUDGET", "lane_execution") == "budget_claim"
    assert runtime._origin_for_terminal(
        "BLOCKED_PROVENANCE", "lane_execution") == "lane_execution"


def _scheduler_plan():
    lanes = [{
        "lane_id": lane_id,
        "plan_sha256": _hash(f"plan:{lane_id}"),
        "work": [{"work_id": _hash(f"work:{lane_id}")}],
    } for lane_id in runtime.LANE_ORDER]
    master = {
        "lane_plans": [{"payload": lanes[0]}, {"payload": lanes[1]}],
        "acceptance_template": {"payload": lanes[2]},
    }
    return master, lanes


def _aggregate(lane_id, *, passed=True, failures=()):
    return {
        "lane_id": lane_id,
        "passed": passed,
        "component_passed": passed,
        "failure_reasons": list(failures),
        "cancellation_health_evidence_sha256": (
            _hash("cancel-health") if lane_id == "F72_20260811" else None),
    }


def test_serial_scheduler_requires_both_explicit_boundary_resumes(
        monkeypatch, tmp_path) -> None:
    point = FakePoint()
    point._state = "PREPARED"
    master, lanes = _scheduler_plan()
    point.artifacts[("master_plan", "master")] = master
    for lane in lanes:
        point.artifacts[("lane_plan", lane["lane_id"])] = lane
    context_id = _hash("scheduler-context")
    point.artifacts[("context_evidence", context_id)] = {"passed": True}
    resolver = SimpleNamespace(
        controls_resolved={
            "context": SimpleNamespace(control={"control_id": context_id}),
            "cancellation": SimpleNamespace(control={"control_id": _hash("cancel")}),
        },
        control_ids=frozenset(), work_ids=frozenset(),
        prepared=SimpleNamespace(),
    )
    order = []
    backed_up = []
    monkeypatch.setattr(runtime, "GlobalExecutionLock",
                        lambda _root: nullcontext(SimpleNamespace()))
    monkeypatch.setattr(runtime.C0B6Checkpoint, "open",
                        lambda *_args, **_kwargs: point)
    monkeypatch.setattr(runtime, "validate_run_lineage", lambda *_args: None)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_recheck_before_terminal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "verify_parents_readonly",
                        lambda *_args: SimpleNamespace())
    monkeypatch.setattr(runtime, "_corpus", lambda *_args: object())
    monkeypatch.setattr(runtime, "validate_master_plan",
                        lambda value, **_kwargs: value)
    monkeypatch.setattr(runtime, "_Resolver", lambda *_args: resolver)
    monkeypatch.setattr(runtime, "reconcile_runtime_events", lambda *_args: None)
    monkeypatch.setattr(runtime, "_validate_execution_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime, "_verify_existing_control_evidence", lambda *_args: None)

    def preflight(*_args, **_kwargs):
        order.append("preflight")
        return "RAW_VALID", None

    def run_lane(_point, _resolver, _transport, *, lane, **_kwargs):
        order.append(lane["lane_id"])
        return "RAW_VALID", None

    monkeypatch.setattr(runtime, "_run_preflight", preflight)
    monkeypatch.setattr(runtime, "_run_lane_work", run_lane)
    monkeypatch.setattr(
        runtime, "_derive_lane",
        lambda *_args, lane, **_kwargs: (_aggregate(lane["lane_id"]), None, None))
    monkeypatch.setattr(runtime, "_lane_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runtime, "build_precontrol_lane_aggregate",
        lambda lane, *_args, **_kwargs: _aggregate(lane["lane_id"]))
    monkeypatch.setattr(
        runtime, "build_lane_aggregate",
        lambda lane, *_args, **_kwargs: _aggregate(lane["lane_id"]))
    monkeypatch.setattr(runtime, "build_acceptance_aggregate",
                        lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(runtime, "ensure_backup_receipt",
                        lambda *_args, **_kwargs: backed_up.append(True))

    kwargs = {
        "benchmark_root": tmp_path,
        "transport_factory": lambda *_args: object(),
        "parent_d50_loader": lambda *_args: {},
        "monotonic": lambda: 1.0,
    }
    first = runtime.run_confirmation(_header()["run_id"], **kwargs)
    assert first["state"] == "PAUSED_STAGE_BOUNDARY"
    assert order == ["preflight", "F72_20260811"]
    with pytest.raises(runtime.C0B6RuntimeError,
                       match="requires --resume"):
        runtime.run_confirmation(_header()["run_id"], **kwargs)

    second = runtime.run_confirmation(_header()["run_id"], resume=True, **kwargs)
    assert second["state"] == "PAUSED_STAGE_BOUNDARY"
    assert order == [
        "preflight", "F72_20260811", "preflight", "F72_20260818"]

    final = runtime.run_confirmation(_header()["run_id"], resume=True, **kwargs)
    assert final["state"] == "CONFIRMED"
    assert order == [
        "preflight", "F72_20260811", "preflight", "F72_20260818",
        "preflight", "C44_1"]
    assert point.invocations == 3
    assert backed_up == [True]


@pytest.mark.parametrize("first,second,reason", [
    (_aggregate("F72_20260811", passed=False,
                failures=("negative_false_positive_above_2",)),
     None, "seed20260811_no_qualifier"),
    (_aggregate("F72_20260811"),
     _aggregate("F72_20260818", passed=False,
                failures=("negative_retained_findings_above_2",)),
     "seed20260818_no_qualifier"),
])
def test_existing_failed_f_lane_stops_before_any_new_dispatch(
        monkeypatch, first, second, reason) -> None:
    point = FakePoint()
    master, _lanes = _scheduler_plan()
    point.artifacts[("lane_aggregate", "F72_20260811")] = first
    if second is not None:
        point.artifacts[("lane_aggregate", "F72_20260818")] = second
    backed_up = []
    monkeypatch.setattr(runtime, "ensure_backup_receipt",
                        lambda *_args, **_kwargs: backed_up.append(True))

    result = runtime._terminal_from_existing(point, SimpleNamespace(), master)

    assert result["state"] == "INCONCLUSIVE"
    assert point.artifacts[("result", "terminal")]["reason"] == reason
    assert point.attempts == []
    assert backed_up == [True]


def test_one_child_and_staged_child_both_fail_closed(tmp_path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    child = runs / "c0b6-20260812-120000-0123456789abcdef01234567"
    child.mkdir()
    with pytest.raises(runtime.C0B6RuntimeError, match="already owned"):
        runtime._assert_child_allowance(tmp_path)
    child.rmdir()
    (runs / ".c0b6-initializing-review-required").mkdir()
    with pytest.raises(runtime.C0B6RuntimeError, match="requires review"):
        runtime._assert_child_allowance(tmp_path)


def test_terminal_verify_returns_independently_replayed_public_summary(
        monkeypatch, tmp_path) -> None:
    point = FakePoint()
    point._state = "INCONCLUSIVE"
    summary = {"version": "c0b6-public-summary-v1", "terminal": "INCONCLUSIVE"}
    monkeypatch.setattr(runtime, "status_readonly", lambda _path: {
        "ok": True, "state": "INCONCLUSIVE", "charged_calls": 97, "errors": []})
    monkeypatch.setattr(runtime.C0B6Checkpoint, "open",
                        lambda *_args, **_kwargs: point)
    monkeypatch.setattr(runtime, "revalidate_source_pins", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "verify_parents_readonly",
                        lambda *_args: SimpleNamespace())
    monkeypatch.setattr(runtime, "verify_backup_readonly", lambda *_args, **_kwargs: {
        "ok": True, "snapshot": str(tmp_path / "snapshot.sqlite3")})
    monkeypatch.setattr(runtime, "verify_c0b6_terminal_readonly",
                        lambda *_args, **_kwargs: SimpleNamespace(
                            public_summary=summary,
                            backup_anchor_sha256=_hash("anchor"),
                            backup_snapshot_sha256=_hash("snapshot")))

    result = runtime.confirmation_verify(
        _header()["run_id"], benchmark_root=tmp_path)

    assert result["ok"] is True
    assert result["public_summary"] == summary
    assert result["backup"]["receipt_present"] is True
