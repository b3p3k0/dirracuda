"""Offline scheduler-boundary tests for C0B-4."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.analyst_benchmark import c0b4_runtime as runtime
from scripts.analyst_benchmark.c0b2_executor import FakeResponse, RetryableTransport
from scripts.analyst_benchmark.c0b2_plan import stable_hash
from scripts.analyst_benchmark.c0b2_transport import RequestSpec


def _hash(label: str) -> str:
    return stable_hash({"label": label})


def _header() -> dict[str, str]:
    return {
        "policy_id": runtime.POLICY_ID, "policy_sha256": runtime.POLICY_SHA256,
        "protocol_sha256": _hash("protocol"), "run_id": "run",
        "ollama_version": runtime.OLLAMA_VERSION,
    }


class FakePoint:
    def __init__(self):
        self.attempts = []
        self.artifacts = {}
        self.events = []
        self.inflight = None
        self._state = "RUNNING"
        self.clock = 1_800_000_000.0

    def header(self):
        return _header()

    def state(self):
        return self._state

    def list_attempts(self, attempt_id=None):
        rows = self.attempts
        if attempt_id is not None:
            rows = [row for row in rows if row["attempt_id"] == attempt_id]
        return [dict(row) for row in rows]

    def precharge(self, **row):
        assert self.inflight is None
        self.inflight = row["attempt_id"]
        self.attempts.append({
            **row, "state": "DISPATCHING", "payload": None,
            "created": self.clock, "updated": self.clock})

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
            "health_not_before_utc": deadline})
        return deadline

    def read_artifact(self, kind, owner):
        return self.artifacts.get((kind, owner))

    def store_artifact(self, kind, owner, value):
        assert (kind, owner) not in self.artifacts
        self.artifacts[(kind, owner)] = dict(value)
        return runtime.sha256_json(value)

    def store_runtime_event(self, value):
        self.events.append(dict(value))
        return value["event_sha256"]

    def list_runtime_events(self):
        return [dict(row) for row in self.events]

    def finalize(self, terminal, artifact, completion=None):
        self._state = terminal
        self.artifacts[("failure", "terminal")] = dict(artifact)
        return runtime.sha256_json(artifact), None


def _metadata(*, strict=False, semantic=False):
    return {
        "strict_schema_invalid": strict, "semantic_invalid": semantic,
        "done_reason": "stop", "prompt_eval_count": 100,
        "tools_empty": True, "images_empty": True,
        "unknown_message_fields_empty": True,
        "raw_response_sha256": _hash("response"),
    }


def _answer(findings=()):
    return json.dumps({
        "document_type": "fixture", "subject": "",
        "assessment": "findings_present" if findings else "no_findings",
        "findings": list(findings),
    }, separators=(",", ":"))


def _work():
    return {
        "work_id": _hash("work"), "request_sha256": _hash("request"),
        "model": "qwen3.6:27b", "worksheet": "v2", "doc_id": "doc",
        "chunk_index": 0, "nonce": "FENCE_" + "A" * 32,
        "source": "source text",
    }


def test_runtime_event_reconciliation_closes_finish_crash_gap_once() -> None:
    point = FakePoint()
    work = _work()
    point.attempts.append({
        "attempt_id": _hash("attempt"), "owner_id": work["work_id"],
        "request_sha256": work["request_sha256"], "state": "RAW_VALID",
        "payload": {"answered": True}, "created": 1_700_000_000.0,
        "updated": 1_700_000_001.0,
    })
    resolved = {"work": work, "lane": {"lane_id": "F72_17"}}
    resolver = SimpleNamespace(
        work_ids=frozenset({work["work_id"]}), control_ids=frozenset(),
        prepared=SimpleNamespace(resolve_work=lambda _owner: resolved))

    runtime._reconcile_runtime_events(point, resolver)
    assert [row["event"] for row in point.events] == [
        "DISPATCHING", "RAW_VALID"]
    runtime._reconcile_runtime_events(point, resolver)
    assert [row["event"] for row in point.events] == [
        "DISPATCHING", "RAW_VALID"]


def test_runtime_event_reconciliation_skips_exact_preflight_owner() -> None:
    point = FakePoint()
    ordinal = 4
    point.attempts.append({
        "attempt_id": _hash("preflight-attempt"),
        "owner_id": stable_hash({
            "c0b4_preflight": "version", "invocation": ordinal}),
        "request_sha256": _hash("preflight-request"), "state": "RAW_VALID",
        "call_class": "preflight_control", "invocation_ordinal": ordinal,
        "payload": {"answered": True}, "created": 1_700_000_000.0,
        "updated": 1_700_000_001.0,
    })
    resolver = SimpleNamespace(
        work_ids=frozenset(), control_ids=frozenset(),
        prepared=SimpleNamespace())
    runtime._reconcile_runtime_events(point, resolver)
    assert point.events == []


def test_preflight_is_exactly_three_serial_precharged_calls() -> None:
    point = FakePoint()
    resolver = SimpleNamespace(controls={})
    calls = []

    def transport(request, _cancel):
        assert point.inflight == request.attempt_id
        calls.append(request.control_id)
        return FakeResponse("{}", {"response_sha256": _hash(request.control_id)})

    outcome, attempt = runtime._run_preflight(point, resolver, transport, 3)
    assert (outcome, attempt) == ("RAW_VALID", None)
    assert len(calls) == 3
    assert [row["call_class"] for row in point.attempts] == [
        "preflight_control"] * 3
    assert point.inflight is None


def test_context_is_durable_before_schema_retry(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    lane = {"lane_id": "F72_17", "work": [work]}
    context_id = _hash("context")
    controls = {"context": SimpleNamespace(control={"control_id": context_id})}
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
        point, SimpleNamespace(), transport, ordinal=1, lane=lane,
        master={}, corpus=None, key=b"x" * 32, controls=controls,
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())
    assert (outcome, owner) == ("RAW_VALID", None)
    assert order == ["scored", "context", "scored"]
    evidence = runtime._work_evidence(point, work)
    assert evidence["chunk"]["first_pass_valid"] is False
    assert evidence["chunk"]["eventual_valid"] is True
    assert [row["call_class"] for row in point.attempts] == [
        "scored", "schema_retry"]


def test_transport_failure_pauses_without_second_inflight_call(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)

    def unavailable(*_args):
        raise RetryableTransport("shared GPU busy")

    outcome, attempt = runtime._run_lane_work(
        point, SimpleNamespace(), unavailable, ordinal=1,
        lane={"lane_id": "F72_20260804", "work": [work]},
        master={}, corpus=None, key=b"x" * 32, controls={},
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())
    assert outcome == "PAUSED_RESOURCE"
    assert attempt == point.attempts[0]["attempt_id"]
    assert point.attempts[0]["state"] == "RETRYABLE_TRANSPORT"
    assert point.inflight is None


def test_planned_cancellation_waits_then_health_checks() -> None:
    point = FakePoint()
    cancel_id, health_id = _hash("cancel"), _hash("health")
    source = "SSN 900-12-3456"
    spec = RequestSpec(
        kind="chat", payload={}, worksheet="v2", expected_model="qwen3.6:27b",
        expected_digest=runtime.SELECTION["model_digest"])
    controls = {
        "cancellation": SimpleNamespace(
            control={"control_id": cancel_id, "candidate_id": _hash("candidate")},
            request_spec=spec),
        "health": SimpleNamespace(
            control={"control_id": health_id, "health_work_id": _hash("health-work"),
                     "prompt_sha256": _hash("prompt")},
            request_spec=spec, source_chunk=source),
    }
    resolver = SimpleNamespace(controls={})
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
        point, resolver, transport, ordinal=1, controls=controls,
        monotonic=lambda: 1.0,
        sleep=lambda seconds: events.append(f"sleep:{seconds}"),
        now=lambda: 1_800_000_000.0)
    assert outcome == "RAW_VALID" and len(digest) == 64
    assert events == ["cancel", "sleep:2.0", "health"]
    assert point.attempts[0]["state"] == "CANCELLED_UNVERIFIED"
    evidence = point.artifacts[("cancellation_health_evidence", cancel_id)]
    assert evidence["passed"] is True


def test_soft_wall_and_operator_cancel_charge_nothing(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)
    common = dict(
        point=point, resolver=SimpleNamespace(), transport=lambda *_: None,
        ordinal=1, lane={"lane_id": "C44_1", "work": [work]}, master={},
        corpus=None, key=b"x" * 32, controls={}, clock_started=0,
        soft_wall_seconds=10)
    assert runtime._run_lane_work(
        **common, monotonic=lambda: 10,
        cancellation=threading.Event())[0] == "PAUSED_SOFT_WALL"
    cancelled = threading.Event()
    cancelled.set()
    assert runtime._run_lane_work(
        **common, monotonic=lambda: 1,
        cancellation=cancelled)[0] == "CANCELLED_PENDING_RESUME"
    assert point.attempts == []


def test_pending_context_resumes_as_orphan_before_any_scored_retry(monkeypatch) -> None:
    point, work = FakePoint(), _work()
    lane = {"lane_id": "F72_17", "work": [work]}
    context_id = _hash("context")
    control = {
        "control_id": context_id, "purpose": "c0b4_stage_f_candidate_context",
        "candidate_id": _hash("candidate"), "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"), "prompt_sha256": _hash("prompt"),
    }
    controls = {"context": SimpleNamespace(
        control=control, request_spec=RequestSpec(
            kind="ps", expected_model="qwen3.6:27b",
            expected_digest=runtime.SELECTION["model_digest"], min_context=8192,
            purpose="c0b4_stage_f_candidate_context",
            config_sha256=control["config_sha256"]))}
    resolver = SimpleNamespace(controls={})
    monkeypatch.setattr(runtime, "_resolved_work", lambda *_args: work)
    calls = []

    def first_transport(request, _cancel):
        calls.append(type(request).__name__)
        if hasattr(request, "control_id"):
            raise RetryableTransport("busy")
        return FakeResponse(_answer(), _metadata(), True, "ACCEPTED")

    first = runtime._run_lane_work(
        point, resolver, first_transport, ordinal=1, lane=lane,
        master={}, corpus=None, key=b"x" * 32, controls=controls,
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())
    assert first[0] == "PAUSED_RESOURCE"

    def resumed_transport(request, _cancel):
        calls.append(type(request).__name__)
        assert hasattr(request, "control_id")
        body = json.dumps({
            "purpose": control["purpose"],
            "config_sha256": control["config_sha256"],
            "model": control["model"], "digest": control["model_digest"],
            "size": 1, "size_vram": 1, "context_length": 8192,
        }, separators=(",", ":"))
        return FakeResponse(body, {"response_sha256": _hash("ps")})

    second = runtime._run_lane_work(
        point, resolver, resumed_transport, ordinal=2, lane=lane,
        master={}, corpus=None, key=b"x" * 32, controls=controls,
        clock_started=0, monotonic=lambda: 1, soft_wall_seconds=10,
        cancellation=threading.Event())
    assert second == ("RAW_VALID", None)
    assert calls == ["WorkRequest", "ControlRequest", "ControlRequest"]
    context_attempts = [row for row in point.attempts
                        if row["owner_id"] == context_id]
    assert [row["call_class"] for row in context_attempts] == [
        "preflight_control", "transport_orphan"]


def test_context_crash_recovery_reuses_valid_response_without_http() -> None:
    point, work = FakePoint(), _work()
    context_id = _hash("context-crash")
    control = {
        "control_id": context_id, "purpose": "c0b4_stage_f_candidate_context",
        "candidate_id": _hash("candidate"), "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"), "prompt_sha256": _hash("prompt"),
    }
    controls = {"context": SimpleNamespace(
        control=control, request_spec=RequestSpec(kind="ps"))}
    body = json.dumps({"context_length": 8192}, separators=(",", ":"))
    point.attempts.append({
        "attempt_id": _hash("context-attempt"), "owner_id": context_id,
        "state": "RAW_VALID", "payload": {
            "answered": True, "response": body,
            "metadata": {"response_sha256": _hash("ps")}},
    })
    result = runtime._ensure_context(
        point, SimpleNamespace(controls={}),
        lambda *_args: (_ for _ in ()).throw(AssertionError("duplicate HTTP")),
        ordinal=2, controls=controls, trigger_work=work,
        trigger_attempt_id=_hash("trigger"))
    assert result[0] == "RAW_VALID"
    assert ("context_evidence", context_id) in point.artifacts


def test_malformed_context_is_failed_before_raw_valid_and_backed_up(
        monkeypatch) -> None:
    point, work = FakePoint(), _work()
    context_id = _hash("context-malformed")
    control = {
        "control_id": context_id, "purpose": "c0b4_stage_f_candidate_context",
        "candidate_id": _hash("candidate"), "model": "qwen3.6:27b",
        "model_digest": runtime.SELECTION["model_digest"],
        "config_sha256": _hash("config"), "prompt_sha256": _hash("prompt"),
    }
    controls = {"context": SimpleNamespace(
        control=control, request_spec=RequestSpec(kind="ps"))}
    outcome, attempt_id = runtime._ensure_context(
        point, SimpleNamespace(controls={}),
        lambda *_args: FakeResponse("{}", {"response_sha256": _hash("ps")}),
        ordinal=1, controls=controls, trigger_work=work,
        trigger_attempt_id=_hash("trigger"))
    assert outcome == "FAILED_SAFETY"
    assert point.list_attempts(attempt_id)[0]["state"] == "FAILED_SAFETY"
    point.artifacts[("master_plan", "master")] = {
        "control_plan": {"context": {"control_id": context_id}}}
    backed_up = []
    monkeypatch.setattr(
        runtime, "ensure_backup_receipt",
        lambda *_args, **_kwargs: backed_up.append(True))
    result = runtime._finish_failure(
        point, SimpleNamespace(), terminal="FAILED_SAFETY",
        lane_id="F72_17", plan_sha256=_hash("plan"),
        attempt_id=attempt_id)
    assert result["state"] == "FAILED_SAFETY" and backed_up == [True]


def test_cancel_success_is_reused_when_health_resumes_as_orphan() -> None:
    point = FakePoint()
    cancel_id, health_id = _hash("cancel-resume"), _hash("health-resume")
    source = "SSN 900-12-3456"
    spec = RequestSpec(
        kind="chat", payload={}, worksheet="v2", expected_model="qwen3.6:27b",
        expected_digest=runtime.SELECTION["model_digest"])
    controls = {
        "cancellation": SimpleNamespace(
            control={"control_id": cancel_id, "candidate_id": _hash("candidate")},
            request_spec=spec),
        "health": SimpleNamespace(
            control={"control_id": health_id, "health_work_id": _hash("health-work"),
                     "prompt_sha256": _hash("prompt")},
            request_spec=spec, source_chunk=source),
    }
    resolver, calls = SimpleNamespace(controls={}), []

    def first(request, cancel):
        if request.control_id == cancel_id:
            calls.append("cancel")
            cancel.set()
            raise RetryableTransport("cancelled")
        calls.append("health-busy")
        raise RetryableTransport("busy")

    paused = runtime._run_cancel_health(
        point, resolver, first, ordinal=1, controls=controls,
        monotonic=lambda: 1.0, sleep=lambda _seconds: None,
        now=lambda: 1_800_000_000.0)
    assert paused[0] == "PAUSED_RESOURCE"

    def resumed(request, _cancel):
        calls.append("health-ok")
        finding = {"category": "pii", "quote": "900-12-3456", "offset": 4}
        return FakeResponse(_answer([finding]), _metadata(), True, "ACCEPTED")

    done = runtime._run_cancel_health(
        point, resolver, resumed, ordinal=2, controls=controls,
        monotonic=lambda: 2.0, sleep=lambda _seconds: None,
        now=lambda: 1_800_000_010.0)
    assert done[0] == "RAW_VALID"
    assert calls == ["cancel", "health-busy", "health-ok"]
    assert [row["call_class"] for row in point.attempts
            if row["owner_id"] == health_id] == [
                "preflight_control", "transport_orphan"]
    evidence = point.artifacts[("cancellation_health_evidence", cancel_id)]
    assert evidence["health_attempt_ids"] == [
        row["attempt_id"] for row in point.attempts
        if row["owner_id"] == health_id]


def test_missing_health_metric_is_failed_before_raw_valid_and_backed_up(
        monkeypatch) -> None:
    point = FakePoint()
    cancel_id, health_id = _hash("cancel-bad"), _hash("health-bad")
    source = "SSN 900-12-3456"
    spec = RequestSpec(
        kind="chat", payload={}, worksheet="v2", expected_model="qwen3.6:27b",
        expected_digest=runtime.SELECTION["model_digest"])
    controls = {
        "cancellation": SimpleNamespace(
            control={"control_id": cancel_id, "candidate_id": _hash("candidate")},
            request_spec=spec),
        "health": SimpleNamespace(
            control={"control_id": health_id,
                     "health_work_id": _hash("health-work"),
                     "prompt_sha256": _hash("prompt")},
            request_spec=spec, source_chunk=source),
    }

    def transport(request, cancel):
        if request.control_id == cancel_id:
            cancel.set()
            raise RetryableTransport("cancelled")
        finding = {"category": "pii", "quote": "900-12-3456", "offset": 4}
        metadata = _metadata()
        metadata.pop("prompt_eval_count")
        return FakeResponse(_answer([finding]), metadata, True, "ACCEPTED")

    outcome, attempt_id = runtime._run_cancel_health(
        point, SimpleNamespace(controls={}), transport, ordinal=1,
        controls=controls, monotonic=lambda: 1.0,
        sleep=lambda _seconds: None, now=lambda: point.clock)
    assert outcome == "FAILED_SAFETY"
    assert point.list_attempts(attempt_id)[0]["state"] == "FAILED_SAFETY"
    point.artifacts[("master_plan", "master")] = {"control_plan": {
        "cancellation": {"control_id": cancel_id},
        "health": {"control_id": health_id}}}
    backed_up = []
    monkeypatch.setattr(
        runtime, "ensure_backup_receipt",
        lambda *_args, **_kwargs: backed_up.append(True))
    result = runtime._finish_failure(
        point, SimpleNamespace(), terminal="FAILED_SAFETY",
        lane_id="F72_17", plan_sha256=_hash("plan"),
        attempt_id=attempt_id)
    assert result["state"] == "FAILED_SAFETY" and backed_up == [True]


def test_schema_retry_budget_is_partitioned_per_lane() -> None:
    point = FakePoint()
    first, second = _work(), {**_work(), "work_id": _hash("work-2")}
    invalid = {"answered": True, "response": "{}", "metadata": {}}
    point.attempts = [{
        "attempt_id": _hash("a1"), "owner_id": first["work_id"],
        "call_class": "schema_retry", "state": "SCHEMA_INVALID",
        "payload": invalid,
    }, {
        "attempt_id": _hash("a2"), "owner_id": second["work_id"],
        "call_class": "scored", "state": "SCHEMA_INVALID", "payload": invalid,
    }]
    same_lane = frozenset({first["work_id"], second["work_id"]})
    assert runtime._next_work_disposition(
        point, second, same_lane)[0] == "budget"
    assert runtime._next_work_disposition(
        point, second, frozenset({second["work_id"]}))[0] == "schema_retry"


def test_activation_partition_and_cursor_census_are_exact() -> None:
    point = FakePoint()
    lanes = [{"lane_id": lane, "plan_sha256": _hash(f"plan:{lane}"),
              "work": [{"work_id": _hash(f"work:{lane}")}]} for lane in runtime.LANE_ORDER]
    master = {
        "lane_plans": [{"payload": lanes[0]}, {"payload": lanes[1]}],
        "acceptance_template": {"payload": lanes[2]},
    }
    runtime._activate_lane(point, master, lanes[0], _hash("master"))
    activation = point.artifacts[("plan_activation", "F72_17")]
    assert activation["activated_work_ids"] == [lanes[0]["work"][0]["work_id"]]
    assert activation["inactive_work_ids"] == sorted([
        lanes[1]["work"][0]["work_id"], lanes[2]["work"][0]["work_id"]])
    runtime._cursor_transition(
        point, lane=lanes[0], next_lane=lanes[1],
        aggregate_sha256=_hash("aggregate"))
    cursor = point.artifacts[("cursor_transition", "F72_17")]
    assert cursor["completed_work_census_sha256"] == runtime.sha256_json({
        "lane_id": "F72_17",
        "completed_work_ids": [lanes[0]["work"][0]["work_id"]],
    })
