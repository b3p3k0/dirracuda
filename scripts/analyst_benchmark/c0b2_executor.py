"""Transport-independent durable executor primitives for C0B-2.

No network client is imported here. The bounded Ollama adapter and offline fakes both
implement the small callable interface owned by this module.

DISPOSITION: benchmark-only diagnostic; remove after C0B artifacts are accepted.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Union

from .c0b2_checkpoint import (INVOCATION_CAPS, BackoffRecord, Checkpoint,
                              CheckpointError, ImmutableViolation,
                              canonical_json, sha256_json)
from .c0b2_fsprobe import (GlobalExecutionLock, MountFingerprint,
                           quarantine_corrupt, revalidate_filesystem,
                           verify_connection)
from .c0b2_plan import (KEEP_ALIVE, MODELS, OPTIONS_C,
                        attempt_id as stable_attempt_id, planned_work_ids,
                        stable_hash)
from .c0b2_schema import (validate_stage_c_inconclusive,
                          validate_stage_c_selection)


class RetryableTransport(RuntimeError):
    """A bounded fake transport outcome that follows the resource sequence."""


class SafetyLimit(RuntimeError):
    """A non-retryable byte/object/depth/channel safety outcome."""


class ProvenanceFailure(RuntimeError):
    """An immutable endpoint, version, model, digest, or configuration mismatch."""


class SoftWallReached(RuntimeError):
    """The invocation deadline crossed inside the atomic claim."""


class InvocationCancelled(RuntimeError):
    """Cancellation won the guarded invocation claim without spending an ordinal."""


PUBLIC_ACCEPTANCE_GATES = frozenset({
    "strict_validity", "first_pass_invalid_bound", "raw_grounding",
    "retained_grounding", "category_recall", "false_positive_bound",
    "injection_robustness", "boundary_identifiers", "truncation_complete",
    "context_channel_cancellation_provenance_safety",
})
PRIVATE_OPERATIONAL_GATES = frozenset({
    "sandbox", "memfd", "source_identity", "loopback_digest",
    "cancellation_health", "leakage", "chunk_completion", "schema_validity",
    "context_headroom", "raw_grounding", "retained_grounding", "aggregate_schema",
})
SERVER_CONTROL_MODEL = "__server__"


@dataclass(frozen=True)
class WorkRequest:
    stage: str
    work_id: str
    model: str
    request_hash: str
    attempt_no: int
    call_class: str = "scored"

    @property
    def attempt_id(self) -> str:
        return stable_attempt_id(self.work_id, self.attempt_no)


@dataclass(frozen=True)
class ControlRequest:
    stage: str
    control_id: str
    model: str
    request_hash: str
    attempt_no: int
    call_class: str = "preflight_probe"

    @property
    def attempt_id(self) -> str:
        return stable_attempt_id(f"control:{self.control_id}", self.attempt_no)


@dataclass(frozen=True)
class FakeResponse:
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    accepted: bool = True
    outcome: str = "ACCEPTED"


@dataclass(frozen=True)
class ExecutionResult:
    outcome: str
    attempt_id: Optional[str] = None
    retry_not_before: float = 0.0


@dataclass(frozen=True)
class _FContextRecovery:
    context_control_id: str
    candidate_id: str
    model: str
    trigger_work_id: str
    trigger_request_hash: str


def control_id(stage: str, invocation_ordinal: int, kind: str, model: str) -> str:
    if invocation_ordinal < 1 or not all((stage, kind, model)):
        raise ValueError("control identity fields must be non-empty and ordinal positive")
    return stable_hash({"stage": stage, "invocation_ordinal": invocation_ordinal,
                        "kind": kind, "model": model})


def stage_c_context_control_id(model: str, model_digest: str) -> str:
    candidates = {tag: (digest, think) for tag, digest, think in MODELS}
    if candidates.get(model, (None,))[0] != model_digest:
        raise ValueError("model/digest is not a frozen Stage-C candidate")
    config_hash = stable_hash({"OPTIONS_C": dict(OPTIONS_C),
                               "think": candidates[model][1],
                               "keep_alive": KEEP_ALIVE})
    identity = stable_hash({"stage": "C", "purpose": "stage_c_context",
                            "model": model, "model_digest": model_digest,
                            "generation_options_sha256": config_hash})
    return identity


def resource_probe_id(stage: str, invocation_ordinal: int, model: str,
                      payload_hash: str) -> str:
    if invocation_ordinal < 1 or not all((stage, model, payload_hash)):
        raise ValueError("resource probe identity fields must be nonempty")
    return stable_hash({"stage": stage, "invocation_ordinal": invocation_ordinal,
                        "kind": "resource_probe", "model": model,
                        "probe_payload_sha256": payload_hash})


class InvocationClock:
    def __init__(self, soft_wall_seconds: float = 240 * 60, *,
                 monotonic: Callable[[], float] = time.monotonic):
        if soft_wall_seconds <= 0:
            raise ValueError("soft wall must be positive")
        self.soft_wall_seconds = float(soft_wall_seconds)
        self._monotonic = monotonic
        self.started = monotonic()

    @property
    def elapsed(self) -> float:
        return self._monotonic() - self.started

    def crossed(self) -> bool:
        return self.elapsed >= self.soft_wall_seconds


class CancellationController:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.forced = False

    def first_signal(self) -> None:
        self.event.set()

    def second_signal(self) -> None:
        self.forced = True
        self.event.set()

    def wait(self, seconds: float) -> bool:
        """Interruptible wait; True means cancellation interrupted it."""
        return self.event.wait(max(0.0, seconds))


class DurableExecutor:
    def __init__(self, checkpoint: Checkpoint, lock: GlobalExecutionLock,
                 transport: Callable[[Union[WorkRequest, ControlRequest], threading.Event], FakeResponse], *,
                 clock: Optional[InvocationClock] = None,
                 cancellation: Optional[CancellationController] = None,
                 context_request_hashes: Optional[Mapping[str, str]] = None,
                 enforce_public_budget_contract: bool = False,
                 now: Callable[[], float] = time.time):
        if not lock.held or lock.root != checkpoint.root:
            raise CheckpointError("executor requires the matching global lock")
        self.checkpoint = checkpoint
        self.lock = lock
        self.transport = transport
        self.clock = clock or InvocationClock()
        self.cancellation = cancellation or CancellationController()
        self.context_request_hashes = dict(context_request_hashes or {})
        self.enforce_public_budget_contract = enforce_public_budget_contract
        self._now = now
        self.current_attempt: Optional[str] = None
        self.invocation_stage: Optional[str] = None
        self.invocation_ordinal: Optional[int] = None

    def recover_and_start(
            self, stage: str, *,
            post_recovery_guard: Callable[[], None] | None = None,
    ) -> tuple[int, int]:
        """Recover crash windows, revalidate ownership, then claim an invocation."""
        self._require_lock()
        if self.invocation_stage is not None:
            raise CheckpointError("executor already owns an invocation")
        if self.checkpoint.state() == "PAUSED_STAGE_BOUNDARY":
            raise CheckpointError("stage boundary is frozen; successor implementation required")
        if self.cancellation.event.is_set():
            self.checkpoint.cancel()
            raise InvocationCancelled("cancelled before invocation claim")
        self._active_plan_raw(stage)
        header = self.checkpoint.header()
        self._require_budget_ledger(header)
        try:
            revalidate_filesystem(
                MountFingerprint(**header["mount"]), self.checkpoint.root,
                header["journal_mode"], header["filesystem_capability_sha256"])
        except Exception:
            if header["run_type"] == "public":
                from .c0b2_runtime import finish_public_run_failure
                finish_public_run_failure(
                    self.checkpoint, terminal="BLOCKED_FILESYSTEM")
            else:
                self.checkpoint.transition("BLOCKED_FILESYSTEM")
            raise
        verification = verify_connection(self.checkpoint.conn)
        if not verification.ok:
            path, root = self.checkpoint.path, self.checkpoint.root
            self.checkpoint.close()
            quarantine_corrupt(
                path, root / "quarantine", reason="integrity_failed", lock=self.lock)
            raise CheckpointError(f"checkpoint verification failed: {verification.errors}")
        count = self.checkpoint.recover()
        if post_recovery_guard is not None:
            post_recovery_guard()
        self.checkpoint.transition("RUNNING")
        try:
            ordinal = self.checkpoint.claim_invocation(
                stage, claim_guard=self._guard_invocation_claim,
                budget_failure_callback=self._budget_failure_callback)
        except InvocationCancelled:
            self.checkpoint.cancel()
            raise
        self.invocation_stage, self.invocation_ordinal = stage, ordinal
        return count, ordinal

    def _require_budget_ledger(self, header: Mapping[str, Any]) -> None:
        limits = header["limits"]
        if self.enforce_public_budget_contract:
            from .c0b2_runtime import PUBLIC_CUMULATIVE_CAP, PUBLIC_LIMITS
            if (header.get("run_type") != "public"
                    or canonical_json(limits) != canonical_json(PUBLIC_LIMITS)
                    or type(header.get("cumulative_cap")) is not int
                    or header["cumulative_cap"] != PUBLIC_CUMULATIVE_CAP):
                raise ImmutableViolation(
                    "public budget ledger differs from the implementation contract")
        stages = sorted((stage, sum(classes.values()))
                        for stage, classes in limits.items())
        classes = sorted((stage, kind, allowance)
                         for stage, values in limits.items()
                         for kind, allowance in values.items())
        stored_stages = self.checkpoint.conn.execute(
            "SELECT stage,hard_cap FROM stage_limits ORDER BY 1").fetchall()
        stored_classes = self.checkpoint.conn.execute(
            "SELECT stage,call_class,allowance FROM class_limits ORDER BY 1,2"
        ).fetchall()
        if (header["invocation_caps"] != INVOCATION_CAPS
                or stored_stages != stages or stored_classes != classes):
            raise ImmutableViolation("budget ledger differs from its frozen header")

    def run(self, request: WorkRequest) -> ExecutionResult:
        self._require_lock()
        self._require_invocation_stage(request.stage)
        if self.cancellation.event.is_set():
            self.checkpoint.cancel()
            return ExecutionResult("CANCELLED_PENDING_RESUME")
        self._require_work_request_identity(request)
        self._require_context_probe_configuration(request.stage, request.model)
        self._require_preflight_complete(request.stage)
        self._require_f_scored_control_order(request.stage)
        context = self.checkpoint.pending_context_obligation(request.stage)
        if context is not None:
            raise CheckpointError(f"context probe required for {context.model}")
        obligation = self._resource_obligation()
        if obligation is not None:
            raise CheckpointError(f"resource probe required for {obligation.model}")
        if self.clock.crossed():
            self.checkpoint.transition("PAUSED_SOFT_WALL")
            return ExecutionResult("PAUSED_SOFT_WALL")
        backoff = self.checkpoint.backoff(request.model)
        if backoff.retry_not_before > self._now():
            return ExecutionResult("RETRY_WAIT", retry_not_before=backoff.retry_not_before)
        attempt_id = request.attempt_id
        try:
            inserted = self.checkpoint.precharge(
                attempt_id=attempt_id, stage=request.stage, call_class=request.call_class,
                request_hash=request.request_hash, attempt_no=request.attempt_no,
                work_id=request.work_id, invocation_ordinal=self.invocation_ordinal,
                claim_guard=self._guard_soft_wall,
                budget_failure_callback=self._budget_failure_callback)
        except SoftWallReached:
            self.checkpoint.transition("PAUSED_SOFT_WALL")
            return ExecutionResult("PAUSED_SOFT_WALL")
        if not inserted:
            state = self.checkpoint.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            return ExecutionResult(f"ALREADY_{state}", attempt_id)
        self.current_attempt = attempt_id
        try:
            response = self.transport(request, self.cancellation.event)
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            self._require_returned_response(response, {"ACCEPTED", "SCHEMA_INVALID"})
            self.checkpoint.finish_attempt(
                attempt_id, outcome=response.outcome, response=response.content,
                metadata=response.metadata,
                accept_work=response.outcome == "ACCEPTED",
                before_commit=lambda: self._accepted_chat_side_effects(
                    request.stage, request.model, attempt_id))
            # Any bounded HTTP-accepted chat response proves resource recovery;
            # schema acceptance controls work state, not the resource sequence.
            return ExecutionResult(response.outcome, attempt_id)
        except RetryableTransport:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            records: list[BackoffRecord] = []
            self.checkpoint.finish_attempt(
                attempt_id, outcome="RETRYABLE_TRANSPORT", response=None,
                metadata={}, accept_work=False,
                before_commit=lambda: records.append(
                    self._advance_resource(request.model)))
            backoff = records[0]
            outcome = "PAUSED_RESOURCE" if backoff.failures >= 6 else "RETRY_WAIT"
            return ExecutionResult(outcome, attempt_id, backoff.retry_not_before)
        except SafetyLimit:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            terminal = self._safety_terminal()
            self._finish_failure_attempt(attempt_id, terminal)
            return ExecutionResult(terminal, attempt_id)
        except ProvenanceFailure:
            return self._finish_provenance(attempt_id)
        finally:
            self.current_attempt = None

    def run_resource_probe(
            self, request: ControlRequest, *,
            prioritized_obligation_model: Optional[str] = None,
            prioritized_context_control_id: Optional[str] = None,
    ) -> ExecutionResult:
        self._require_lock()
        self._require_invocation_stage(request.stage)
        if self.cancellation.event.is_set():
            self.checkpoint.cancel()
            return ExecutionResult("CANCELLED_PENDING_RESUME")
        self._require_preflight_complete(request.stage)
        f_priority = (self._f_context_recovery_owner()
                      if request.stage == "F" else None)
        if prioritized_context_control_id is not None:
            if (request.stage != "F" or prioritized_obligation_model is not None
                    or f_priority is None
                    or prioritized_context_control_id
                    != f_priority.context_control_id
                    or request.model != f_priority.model
                    or request.request_hash != f_priority.trigger_request_hash):
                raise CheckpointError(
                    "prioritized Stage-F recovery differs from its context trigger")
            exact = self.checkpoint.backoff(f_priority.model)
            obligation = exact if exact.failures >= 6 else None
        elif prioritized_obligation_model is None:
            if f_priority is not None:
                raise CheckpointError(
                    "pending Stage-F context has priority over generic recovery")
            obligation = self._resource_obligation()
        else:
            if (request.stage != "D"
                    or prioritized_obligation_model != request.model):
                raise CheckpointError(
                    "prioritized resource obligation is Stage-D candidate-specific")
            exact = self.checkpoint.backoff(prioritized_obligation_model)
            obligation = exact if exact.failures >= 6 else None
        if obligation is None or obligation.model != request.model:
            raise CheckpointError("probe does not match the persisted resource obligation")
        expected_control = resource_probe_id(
            request.stage, int(self.invocation_ordinal or 0), request.model,
            request.request_hash)
        if request.control_id != expected_control:
            raise CheckpointError("resource probe identity does not match this invocation")
        self._require_context_probe_configuration(request.stage, request.model)
        if obligation.retry_not_before > self._now():
            return ExecutionResult("RETRY_WAIT", retry_not_before=obligation.retry_not_before)
        attempt_id = request.attempt_id
        try:
            inserted = self.checkpoint.precharge(
                attempt_id=attempt_id, stage=request.stage, call_class=request.call_class,
                request_hash=request.request_hash, attempt_no=request.attempt_no,
                control_id=request.control_id, invocation_ordinal=self.invocation_ordinal,
                first_control_class="transport_orphan",
                claim_guard=self._guard_soft_wall,
                budget_failure_callback=self._budget_failure_callback)
        except SoftWallReached:
            self.checkpoint.transition("PAUSED_SOFT_WALL")
            return ExecutionResult("PAUSED_SOFT_WALL")
        if not inserted:
            state = self.checkpoint.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            return ExecutionResult(f"ALREADY_{state}", attempt_id)
        self.current_attempt = attempt_id
        try:
            response = self.transport(request, self.cancellation.event)
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            self._require_returned_response(response, {"ACCEPTED", "SCHEMA_INVALID"})
            self.checkpoint.finish_attempt(
                attempt_id, outcome=response.outcome, response=response.content,
                metadata=response.metadata, accept_work=False,
                before_commit=lambda: self._accepted_chat_side_effects(
                    request.stage, request.model, attempt_id))
            return ExecutionResult(response.outcome, attempt_id)
        except RetryableTransport:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            records: list[BackoffRecord] = []
            self.checkpoint.finish_attempt(
                attempt_id, outcome="RETRYABLE_TRANSPORT", response=None,
                metadata={}, accept_work=False,
                before_commit=lambda: records.append(self._advance_resource(request.model)))
            backoff = records[0]
            return ExecutionResult("PAUSED_RESOURCE", attempt_id, backoff.retry_not_before)
        except SafetyLimit:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
            terminal = self._safety_terminal()
            self._finish_failure_attempt(attempt_id, terminal)
            return ExecutionResult(terminal, attempt_id)
        except ProvenanceFailure:
            return self._finish_provenance(attempt_id)
        finally:
            self.current_attempt = None

    def run_context_probe(self, request: ControlRequest) -> ExecutionResult:
        priority = (self._f_context_recovery_owner()
                    if request.stage == "F" else None)
        if priority is not None and (
                request.control_id != priority.context_control_id
                or request.model != priority.model):
            raise CheckpointError(
                "pending Stage-F context has priority over another candidate control")
        from .c0b2_runtime_common import run_context_probe
        return run_context_probe(self, request)

    def run_control(self, request: ControlRequest, *, kind: str) -> ExecutionResult:
        """Run one invocation-bound version/tags/model-show preflight call."""
        self._require_lock()
        self._require_invocation_stage(request.stage)
        if kind not in {"version", "tags", "show"}:
            raise ValueError("unsupported standard control kind")
        if kind in {"version", "tags"}:
            if request.model != SERVER_CONTROL_MODEL:
                raise CheckpointError("server controls require the server identity")
        elif request.model not in self._stage_models(request.stage):
            raise CheckpointError("show control model is outside the frozen stage plan")
        expected = control_id(
            request.stage, int(self.invocation_ordinal or 0), kind, request.model)
        if request.control_id != expected:
            raise CheckpointError("control identity does not match this invocation")
        if self.cancellation.event.is_set():
            self.checkpoint.cancel()
            return ExecutionResult("CANCELLED_PENDING_RESUME")
        retry_not_before = max(
            (self.checkpoint.backoff(model).retry_not_before
             for model in self._control_resource_models(request)), default=0.0)
        if retry_not_before > self._now():
            return ExecutionResult("RETRY_WAIT", retry_not_before=retry_not_before)
        try:
            inserted = self.checkpoint.precharge(
                attempt_id=request.attempt_id, stage=request.stage,
                call_class=request.call_class, request_hash=request.request_hash,
                attempt_no=request.attempt_no, control_id=request.control_id,
                invocation_ordinal=self.invocation_ordinal,
                claim_guard=self._guard_soft_wall,
                budget_failure_callback=self._budget_failure_callback)
        except SoftWallReached:
            self.checkpoint.transition("PAUSED_SOFT_WALL")
            return ExecutionResult("PAUSED_SOFT_WALL")
        if not inserted:
            state = self.checkpoint.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id=?",
                (request.attempt_id,)).fetchone()[0]
            return ExecutionResult(f"ALREADY_{state}", request.attempt_id)
        self.current_attempt = request.attempt_id
        try:
            response = self.transport(request, self.cancellation.event)
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(request.attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
            self._require_returned_response(response, {"ACCEPTED"})
            self.checkpoint.finish_attempt(
                request.attempt_id, outcome=response.outcome, response=response.content,
                metadata=response.metadata, accept_work=False)
            return ExecutionResult(response.outcome, request.attempt_id)
        except RetryableTransport:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(request.attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
            return self._finish_retryable_control(request)
        except SafetyLimit:
            if self.cancellation.event.is_set():
                self.checkpoint.cancel(request.attempt_id)
                return ExecutionResult("CANCELLED_PENDING_RESUME", request.attempt_id)
            terminal = self._safety_terminal()
            self._finish_failure_attempt(request.attempt_id, terminal)
            return ExecutionResult(terminal, request.attempt_id)
        except ProvenanceFailure:
            return self._finish_provenance(request.attempt_id)
        finally:
            self.current_attempt = None

    def run_cancellation_probe(self, request: ControlRequest) -> ExecutionResult:
        if request.stage == "F" and self._f_context_recovery_owner() is not None:
            raise CheckpointError(
                "pending Stage-F context has priority over another candidate control")
        from .c0b2_runtime_common import run_cancellation_probe
        return run_cancellation_probe(self, request)

    def run_cancellation_health(self, request: ControlRequest,
                                *, cancelled_attempt_id: str, source: str,
                                worksheet: str, num_predict: int,
                                num_ctx: int) -> ExecutionResult:
        if request.stage == "F" and self._f_context_recovery_owner() is not None:
            raise CheckpointError(
                "pending Stage-F context has priority over another candidate control")
        from .c0b2_runtime_common import run_cancellation_health
        return run_cancellation_health(
            self, request, cancelled_attempt_id=cancelled_attempt_id,
            source=source, worksheet=worksheet,
            num_predict=num_predict, num_ctx=num_ctx)

    def cancel(self) -> None:
        self._require_lock()
        self.cancellation.first_signal()
        self.checkpoint.cancel(self.current_attempt)

    def interruptible_backoff(self, retry_not_before: float,
                              *, max_slice: float = 1.0) -> bool:
        """Wait until eligible; False when the user cancels."""
        while True:
            self._require_lock()
            if self.clock.crossed():
                self.checkpoint.transition("PAUSED_SOFT_WALL")
                return False
            remaining = retry_not_before - self._now()
            if remaining <= 0:
                return True
            if self.cancellation.wait(min(max_slice, remaining)):
                self.checkpoint.cancel(self.current_attempt)
                return False

    def abandon(self) -> None:
        self._require_lock()
        if self.checkpoint.header()["run_type"] == "public":
            from .c0b2_runtime import finish_public_run_failure
            finish_public_run_failure(self.checkpoint, terminal="ABANDONED")
        else:
            self.checkpoint.transition("ABANDONED")

    def finalize_complete(self, outcome: str, artifact: Mapping[str, Any]) -> str:
        self._require_lock()
        if outcome not in {"SELECTED", "INCONCLUSIVE", "PASS_OPERATIONAL",
                           "FAIL_OPERATIONAL", "INCOMPLETE"}:
            raise ValueError("unsupported aggregate final outcome")
        if not artifact:
            raise ValueError("final artifact must not be empty")
        raw = canonical_json(dict(artifact))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        conn = self.checkpoint.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            if self.checkpoint.state() != "RUNNING":
                raise CheckpointError("finalization requires RUNNING")
            run_type = self.checkpoint.header()["run_type"]
            allowed = ({"SELECTED", "INCONCLUSIVE"} if run_type == "public" else
                       {"PASS_OPERATIONAL", "FAIL_OPERATIONAL", "INCOMPLETE"})
            if outcome not in allowed:
                raise CheckpointError(f"{outcome} is invalid for a {run_type} run")
            required_stages = {"C", "D", "F"} if run_type == "public" else {"E"}
            configured_stages = {row[0] for row in conn.execute(
                "SELECT stage FROM stage_limits")}
            frozen_stages = {row[0] for row in conn.execute("SELECT stage FROM plans")}
            allowed_frozen = ({frozenset({"C"}), frozenset({"C", "D"}),
                               frozenset({"C", "D", "F"})}
                              if outcome == "INCONCLUSIVE" else
                              {frozenset(required_stages)})
            if (configured_stages != required_stages
                    or frozenset(frozen_stages) not in allowed_frozen):
                raise CheckpointError(
                    "aggregate incomplete: required stage limits and plans are not frozen")
            plans = {stage: self.checkpoint.load_plan(stage)
                     for stage in frozen_stages}
            final_stage = (next(stage for stage in ("F", "D", "C")
                                if stage in frozen_stages)
                           if run_type == "public" else "E")
            acceptance = self.checkpoint.load_acceptance_plan()
            if outcome == "SELECTED" and acceptance is None:
                raise CheckpointError("selection requires a frozen post-F C44 acceptance plan")
            if outcome != "SELECTED" and acceptance is not None:
                raise CheckpointError("a frozen acceptance branch requires SELECTED outcome")
            proof_parent = acceptance[1] if acceptance else plans[final_stage][1]
            if outcome == "SELECTED":
                self._validate_selection_artifact(artifact, acceptance[0])
            proof = self._completion_proof(
                outcome, digest, final_stage, proof_parent)
            proof_plans = dict(plans)
            if acceptance:
                proof_plans["F_ACCEPTANCE"] = acceptance
            self._validate_outcome_facts(outcome, proof["facts"], proof_plans)
            planned = set().union(*(planned_work_ids(
                item[2]) for item in proof_plans.values()))
            registered = {row[0] for row in conn.execute("SELECT work_id FROM work_items")}
            pending = conn.execute(
                "SELECT count(*) FROM work_items WHERE state NOT IN "
                "('SUCCEEDED','COMPLETED_INVALID')").fetchone()[0]
            dispatching = conn.execute(
                "SELECT count(*) FROM attempts WHERE state='DISPATCHING'").fetchone()[0]
            unbound = conn.execute(
                "SELECT count(*) FROM work_items w JOIN attempts a "
                "ON a.attempt_id=w.accepted_attempt_id "
                "WHERE a.invocation_ordinal IS NULL").fetchone()[0]
            if planned != registered or pending or dispatching or unbound:
                raise CheckpointError(
                    f"aggregate incomplete: plan_delta={len(planned ^ registered)}, "
                    f"{pending} work, {dispatching} dispatching, {unbound} unbound")
            self._validate_control_obligations(plans)
            detail = canonical_json({"state": outcome, "sha256": digest,
                                     "artifact": json.loads(raw)})
            conn.execute("INSERT INTO events(kind,detail_json,created) VALUES('FINAL_ARTIFACT',?,?)",
                         (detail, time.time()))
            conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                         (outcome, time.time()))
            conn.commit()
            return digest
        except Exception:
            conn.rollback()
            raise

    def finalize_stage_c_inconclusive(
            self, selection: Mapping[str, Any], artifact: Mapping[str, Any]) -> str:
        """Atomically persist the no-survivor decisions, artifact, and terminal."""
        self._require_lock()
        selected = validate_stage_c_selection(selection)
        result = validate_stage_c_inconclusive(artifact)
        plan = self.checkpoint.load_plan("C")
        aggregate = self.checkpoint.load_aggregate("C")
        from .c0b2_stage_c import build_stage_c_selection
        expected_selection = validate_stage_c_selection(
            build_stage_c_selection(json.loads(aggregate[2])))
        if (selected["survivors"] or len(json.loads(plan[2])["work"]) != 264
                or selected != expected_selection
                or selected["plan_sha256"] != plan[1]
                or selected["aggregate_sha256"] != aggregate[1]
                or result["aggregate_sha256"] != aggregate[1]
                or [(row["model"], row["model_digest"])
                    for row in selected["models"]]
                != [(model, digest) for model, digest, _think in MODELS]
                or self.checkpoint.header()["model_digests"]
                != {model: digest for model, digest, _think in MODELS}):
            raise CheckpointError("Stage-C inconclusive artifacts differ from frozen evidence")
        result_raw = canonical_json(result)
        result_hash = hashlib.sha256(result_raw.encode("utf-8")).hexdigest()
        selection_raw = canonical_json(selected)
        selection_row = ("C", plan[1], aggregate[1], "NOT_ACTIVATED", selection_raw)
        completion = {
            "outcome": "INCONCLUSIVE", "artifact_sha256": result_hash,
            "facts": {"deterministic_stop": True,
                      "reason": "no_stage_c_survivor"},
        }
        completion_row = (
            "C", plan[1], aggregate[1], "NOT_ACTIVATED", canonical_json(completion))
        conn = self.checkpoint.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            if self.checkpoint.state() != "RUNNING":
                raise CheckpointError("Stage-C inconclusive finalization requires RUNNING")
            planned = planned_work_ids(plan[2])
            states = {row[0]: row[1] for row in conn.execute(
                "SELECT work_id,state FROM work_items WHERE stage='C'")}
            contexts = {row[0]: row[1] for row in conn.execute(
                "SELECT model,state FROM context_obligations WHERE stage='C'")}
            if (not planned or planned != set(states)
                    or any(state not in {"SUCCEEDED", "COMPLETED_INVALID"}
                           for state in states.values())
                    or conn.execute(
                        "SELECT 1 FROM attempts WHERE state='DISPATCHING' LIMIT 1").fetchone()
                    or contexts != {model: "COMPLETE" for model, _digest, _think in MODELS}):
                raise CheckpointError("Stage-C inconclusive work is incomplete")
            unbound = conn.execute(
                "SELECT count(*) FROM work_items w JOIN attempts a "
                "ON a.attempt_id=w.accepted_attempt_id "
                "WHERE a.invocation_ordinal IS NULL").fetchone()[0]
            if unbound:
                raise CheckpointError("Stage-C inconclusive work has unbound acceptance")
            self._validate_control_obligations({"C": plan})
            for decision_id, row in (("stage-c-selection", selection_row),
                                     ("c0b2-completion", completion_row)):
                if conn.execute("SELECT 1 FROM decisions WHERE decision_id=?",
                                (decision_id,)).fetchone():
                    raise CheckpointError(f"decision {decision_id} already exists")
                conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                             (decision_id, *row, time.time()))
            detail = canonical_json({"state": "INCONCLUSIVE", "sha256": result_hash,
                                     "artifact": result})
            conn.execute("INSERT INTO events(kind,detail_json,created) "
                         "VALUES('FINAL_ARTIFACT',?,?)", (detail, time.time()))
            conn.execute("UPDATE run_state SET state='INCONCLUSIVE',updated=? WHERE id=1",
                         (time.time(),))
            conn.commit()
            return result_hash
        except Exception:
            conn.rollback()
            raise

    def _require_lock(self) -> None:
        if not self.lock.held or self.lock.root != self.checkpoint.root:
            raise CheckpointError("executor lost its matching global lock")

    def _budget_failure_callback(
            self, payload: Mapping[str, Optional[str]]) -> None:
        if self.checkpoint.header()["run_type"] != "public":
            return
        from .c0b2_runtime import finish_public_budget_failure
        finish_public_budget_failure(self.checkpoint, payload)

    def _finish_provenance(self, attempt_id: str) -> ExecutionResult:
        if self.cancellation.event.is_set():
            self.checkpoint.cancel(attempt_id)
            return ExecutionResult("CANCELLED_PENDING_RESUME", attempt_id)
        self._finish_failure_attempt(attempt_id, "BLOCKED_PROVENANCE")
        return ExecutionResult("BLOCKED_PROVENANCE", attempt_id)

    def _finish_failure_attempt(self, attempt_id: str, terminal: str) -> None:
        row = self.checkpoint.conn.execute(
            "SELECT stage FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row and self.checkpoint.header()["run_type"] == "public":
            from .c0b2_runtime import finish_public_failure_attempt
            finish_public_failure_attempt(
                self.checkpoint, attempt_id=attempt_id, terminal=terminal)
            return
        self.checkpoint.finish_attempt(
            attempt_id, outcome=terminal, response=None, metadata={},
            accept_work=False, terminal_state=terminal)

    def _accepted_chat_side_effects(self, stage: str, model: str,
                                    attempt_id: str) -> None:
        self._reset_resource(model)
        if stage != "C":
            return
        frozen = self.checkpoint.header()["model_digests"].get(model)
        candidates = {tag: digest for tag, digest, _think in MODELS}
        if frozen is None or candidates.get(model) != frozen:
            return
        if self.checkpoint.conn.execute(
                "SELECT 1 FROM context_obligations WHERE stage='C' AND model=?",
                (model,)).fetchone():
            return
        identity = stage_c_context_control_id(model, frozen)
        self.checkpoint.ensure_context_obligation(
            stage="C", model=model, control_id=identity,
            request_hash=self.context_request_hashes[model],
            source_attempt_id=attempt_id)

    def _require_context_probe_configuration(self, stage: str, model: str) -> None:
        if stage != "C":
            return
        frozen = self.checkpoint.header()["model_digests"].get(model)
        candidates = {tag: digest for tag, digest, _think in MODELS}
        if candidates.get(model) == frozen:
            request_hash = self.context_request_hashes.get(model)
            if not isinstance(request_hash, str) or not request_hash:
                raise CheckpointError(
                    f"Stage-C context request hash is not configured for {model}")

    def _completion_proof(self, outcome: str, artifact_hash: str,
                          stage: str, plan_hash: str) -> dict[str, Any]:
        matches: list[dict[str, Any]] = []
        for row in self.checkpoint.conn.execute(
                "SELECT parent_hash,activation,value_json FROM decisions WHERE stage=?",
                (stage,)):
            if row[0] != plan_hash:
                continue
            value = json.loads(row[2])
            if canonical_json(value) != row[2]:
                raise CheckpointError("completion decision is not canonical")
            if (isinstance(value, dict) and value.get("outcome") == outcome
                    and value.get("artifact_sha256") == artifact_hash):
                expected_activation = ("ACTIVATED" if outcome in
                                       {"SELECTED", "PASS_OPERATIONAL"}
                                       else "NOT_ACTIVATED")
                if row[1] != expected_activation:
                    raise CheckpointError("completion decision activation is inconsistent")
                matches.append(value)
        if len(matches) != 1 or set(matches[0]) != {
                "outcome", "artifact_sha256", "facts"}:
            raise CheckpointError("one exact persisted completion decision is required")
        if not isinstance(matches[0]["facts"], dict):
            raise CheckpointError("completion facts must be an object")
        return matches[0]

    def _validate_outcome_facts(self, outcome: str, facts: Mapping[str, Any],
                                plans: Mapping[str, tuple[str, str, str]]) -> None:
        if outcome == "SELECTED":
            if set(facts) != {"accepted_document_count", "gates"}:
                raise CheckpointError("selected completion facts are incomplete")
            gates = facts["gates"]
            if (facts["accepted_document_count"] != 166
                    or not isinstance(gates, dict)
                    or set(gates) != PUBLIC_ACCEPTANCE_GATES
                    or not all(value is True for value in gates.values())):
                raise CheckpointError("public acceptance predicates did not pass")
            document_ids: set[str] = set()
            stage_documents: dict[str, set[str]] = {}
            for stage, (_parent, _digest, raw) in plans.items():
                current: set[str] = set()
                for item in json.loads(raw)["work"]:
                    doc_id = item.get("doc_id")
                    if isinstance(doc_id, str) and doc_id:
                        document_ids.add(doc_id)
                        current.add(doc_id)
                stage_documents[stage] = current
            if len(document_ids) != 166:
                raise CheckpointError("selection does not cover 166 planned documents")
            acceptance_docs = {
                item.get("doc_id") for item in
                json.loads(plans["F_ACCEPTANCE"][2])["work"]
                if isinstance(item.get("doc_id"), str) and item.get("doc_id")
            }
            if len(acceptance_docs) != 44:
                raise CheckpointError("selection lacks the frozen C44 acceptance rerun")
            if ({stage: len(stage_documents.get(stage, set()))
                 for stage in ("C", "D", "F", "F_ACCEPTANCE")}
                    != {"C": 44, "D": 50, "F": 72, "F_ACCEPTANCE": 44}
                    or acceptance_docs != stage_documents["C"]
                    or len(json.loads(plans["C"][2])["work"]) != 264):
                raise CheckpointError("selection plans do not match the frozen 44/50/72 split")
            self._require_cancellation_health_event()
        elif outcome == "PASS_OPERATIONAL":
            if set(facts) != {"target", "processed", "gates"}:
                raise CheckpointError("private PASS facts are incomplete")
            gates = facts["gates"]
            if (not isinstance(facts["target"], int) or isinstance(facts["target"], bool)
                    or facts["target"] < 20 or facts["processed"] != facts["target"]
                    or not isinstance(gates, dict)
                    or set(gates) != PRIVATE_OPERATIONAL_GATES
                    or not all(value is True for value in gates.values())):
                raise CheckpointError("private operational predicates did not pass")
            document_ids = {
                item.get("doc_id") for item in json.loads(plans["E"][2])["work"]
                if isinstance(item.get("doc_id"), str) and item.get("doc_id")
            }
            if len(document_ids) != facts["target"]:
                raise CheckpointError(
                    "private target/processed counts do not match the frozen E plan")
        else:
            if (set(facts) != {"deterministic_stop", "reason"}
                    or facts["deterministic_stop"] is not True
                    or not isinstance(facts["reason"], str)
                    or not 1 <= len(facts["reason"]) <= 120):
                raise CheckpointError("terminal stop facts are incomplete")
            if outcome == "INCONCLUSIVE" and "F" in plans:
                self._require_cancellation_health_event()

    def _validate_selection_artifact(
            self, artifact: Mapping[str, Any], provisional_hash: str) -> None:
        selection = artifact.get("selection")
        if not isinstance(selection, dict) or set(selection) != {
                "model", "model_digest", "worksheet", "chunk_chars", "overlap",
                "num_ctx", "num_predict"}:
            raise CheckpointError("selection artifact lacks exact D1/D2 fields")
        if (self.checkpoint.header()["model_digests"].get(selection["model"])
                != selection["model_digest"]
                or selection["worksheet"] not in {"v1", "v2"}
                or any(isinstance(selection[field], bool)
                       or not isinstance(selection[field], int)
                       or selection[field] <= 0
                       for field in ("chunk_chars", "num_ctx", "num_predict"))
                or isinstance(selection["overlap"], bool)
                or not isinstance(selection["overlap"], int)
                or not 0 <= selection["overlap"] < selection["chunk_chars"]):
            raise CheckpointError("selection artifact D1/D2 values are invalid")
        matched = False
        for row in self.checkpoint.conn.execute(
                "SELECT decision_id,stage,parent_hash,aggregate_hash,activation,value_json "
                "FROM decisions WHERE stage='F'"):
            value = json.loads(row[5])
            if (sha256_json(tuple(row)) == provisional_hash
                    and value == {"outcome": "PROVISIONAL_SELECTED",
                                  "selection": selection}):
                matched = True
        if not matched:
            raise CheckpointError("selection artifact differs from provisional F decision")

    def _require_cancellation_health_event(self) -> None:
        ordinals = [int(row[0]) for row in self.checkpoint.conn.execute(
            "SELECT ordinal FROM invocations WHERE stage='F' ORDER BY ordinal")]
        required_models = self._stage_models("F", include_acceptance=False)
        if not required_models:
            raise CheckpointError("Stage F has no frozen cancellation candidates")
        witnessed: set[str] = set()
        for row in self.checkpoint.conn.execute(
                "SELECT detail_json FROM events WHERE kind='CANCELLATION_HEALTH_PASS'"):
            detail = json.loads(row[0])
            if not isinstance(detail, dict) or set(detail) != {
                    "cancelled_attempt_id", "health_attempt_id"}:
                continue
            cancelled = self.checkpoint.conn.execute(
                "SELECT control_id,stage,state FROM attempts WHERE attempt_id=?",
                (detail["cancelled_attempt_id"],)).fetchone()
            health = self.checkpoint.conn.execute(
                "SELECT control_id,stage,state FROM attempts WHERE attempt_id=?",
                (detail["health_attempt_id"],)).fetchone()
            if (not cancelled or not health
                    or cancelled[1:] != ("F", "CANCELLED_UNVERIFIED")
                    or health[1:] != ("F", "ACCEPTED")):
                continue
            for model in required_models:
                for first in ordinals:
                    for second in ordinals:
                        if (first < second
                                and cancelled[0] == control_id(
                                    "F", first, "cancellation_probe", model)
                                and health[0] == control_id(
                                    "F", second, "cancellation_health", model)):
                            witnessed.add(model)
        missing = required_models - witnessed
        if missing:
            raise CheckpointError(
                "Stage F cancellation/following-health proof is missing for "
                + ", ".join(sorted(missing)))

    def _validate_control_obligations(
            self, plans: Mapping[str, tuple[str, str, str]]) -> None:
        invocations = list(self.checkpoint.conn.execute(
            "SELECT DISTINCT stage,invocation_ordinal FROM attempts "
            "WHERE work_id IS NOT NULL AND invocation_ordinal IS NOT NULL "
            "ORDER BY stage,invocation_ordinal"))
        by_stage = {stage: [] for stage in plans}
        for stage, ordinal in invocations:
            if stage in by_stage:
                by_stage[stage].append(int(ordinal))
        for stage, ordinals in by_stage.items():
            for ordinal in ordinals:
                self._require_preflight_complete(stage, ordinal=ordinal)

    def _require_preflight_complete(self, stage: str, *,
                                    ordinal: Optional[int] = None) -> None:
        ordinal = int(self.invocation_ordinal or 0) if ordinal is None else ordinal
        if ordinal < 1:
            raise CheckpointError("preflight requires an active invocation ordinal")
        models = self._stage_models(stage)
        controls = (("version", SERVER_CONTROL_MODEL),
                    ("tags", SERVER_CONTROL_MODEL),
                    *(("show", model) for model in sorted(models)))
        for kind, model in controls:
            identity = control_id(stage, ordinal, kind, model)
            accepted = self.checkpoint.conn.execute(
                "SELECT 1 FROM attempts WHERE control_id=? AND stage=? "
                "AND state='ACCEPTED'", (identity, stage)).fetchone()
            if not accepted:
                raise CheckpointError(
                    f"missing accepted {stage}/{ordinal}/{kind}/{model} control")

    def _planned_items(self, stage: str, *,
                       include_acceptance: bool = True) -> list[dict[str, Any]]:
        raw, phased = self._active_plan_raw(stage)
        if stage == "F" and phased:
            return self._activated_f_items(raw)
        raws = [raw]
        acceptance = self.checkpoint.load_acceptance_plan() if not phased else None
        if include_acceptance and stage == "F" and acceptance:
            raws.append(acceptance[2])
        items: list[dict[str, Any]] = []
        for raw in raws:
            value = json.loads(raw).get("work")
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise CheckpointError(f"stage {stage} plan work must be a list of objects")
            items.extend(value)
        return items

    def _activated_f_items(self, raw: str) -> list[dict[str, Any]]:
        """Return only work authorized by the exact active F activation."""
        from .c0b2_runtime_common import runtime_position

        position = runtime_position(self.checkpoint)
        try:
            plan = json.loads(raw)
            key = str(plan["plan_key"])
            work = list(plan["work"])
            groups = list(plan.get("groups", ()))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointError("active F plan is not an exact work owner") from exc
        if (position.active_stage, position.active_plan_key) != ("F", key):
            raise CheckpointError("active F plan disagrees with the runtime cursor")
        row = self.checkpoint.conn.execute(
            "SELECT activation_hash,activation_json FROM plan_activations "
            "WHERE plan_key=?", (key,)).fetchone()
        if not row:
            raise CheckpointError("active F plan lacks an activation")
        try:
            activation = json.loads(str(row[1]))
        except json.JSONDecodeError as exc:
            raise CheckpointError("active F activation is not valid JSON") from exc
        if (not isinstance(activation, dict)
                or canonical_json(activation) != row[1]
                or sha256_json(activation) != row[0]
                or activation.get("plan_key") != key
                or activation.get("plan_sha256") != sha256_json(plan)
                or activation.get("run_id") != self.checkpoint.header()["run_id"]
                or activation.get("state") != "ACTIVATED"):
            raise CheckpointError("active F activation identity changed")
        activated = activation.get("activated_group_ids")
        if not isinstance(activated, list) or any(
                not isinstance(group, str) for group in activated):
            raise CheckpointError("active F activation groups are invalid")
        if key == "F_ACCEPTANCE":
            if activated:
                raise CheckpointError("F acceptance cannot activate groups")
            expected = work
        else:
            ordered = [group.get("group_id") for group in groups]
            if (not activated or len(set(activated)) != len(activated)
                    or any(group not in ordered for group in activated)
                    or key == "F_SEED_1" and activated != ordered):
                raise CheckpointError("active F groups changed from their authority")
            expected = [item for group in activated for item in work
                        if item.get("activation_group_id") == group]
        registry = self.checkpoint.conn.execute(
            "SELECT r.work_id,r.activation_group_id,w.stage,w.cell_id,w.request_hash "
            "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
            "WHERE r.plan_key=? ORDER BY r.rowid", (key,)).fetchall()
        expected_registry = [(item.get("work_id"),
                              item.get("activation_group_id"), "F",
                              item.get("cell_id"), item.get("request_sha256"))
                             for item in expected]
        if registry != expected_registry:
            raise CheckpointError("active F work registry changed")
        return expected

    def _active_plan_raw(self, stage: str) -> tuple[str, bool]:
        """Return the activated phase plan, with legacy C/fixture compatibility."""
        from .c0b2_runtime_common import load_phase_plan, runtime_position
        position = runtime_position(self.checkpoint)
        if position.active_stage == stage and position.active_plan_key != "C":
            return load_phase_plan(
                self.checkpoint, position.active_plan_key)[2], True
        return self.checkpoint.load_plan(stage)[2], False

    def _stage_models(self, stage: str, *,
                      include_acceptance: bool = True) -> set[str]:
        items = self._planned_items(stage, include_acceptance=include_acceptance)
        raw_models = [item.get("model") for item in items]
        if any(not isinstance(model, str) or not model for model in raw_models):
            raise CheckpointError(f"stage {stage} plan lacks an exact model identity")
        models = {str(model) for model in raw_models}
        frozen = self.checkpoint.header()["model_digests"]
        for item in items:
            model = item["model"]
            if item.get("model_digest") != frozen.get(model):
                raise CheckpointError(
                    f"stage {stage} plan model digest is not frozen for {model}")
        return models

    def _require_work_request_identity(self, request: WorkRequest) -> None:
        matches = [item for item in self._planned_items(request.stage)
                   if item.get("work_id") == request.work_id]
        if len(matches) != 1:
            raise CheckpointError("work request is not unique in the frozen stage plan")
        item = matches[0]
        registered = self.checkpoint.conn.execute(
            "SELECT cell_id,request_hash FROM work_items WHERE work_id=?",
            (request.work_id,)).fetchone()
        expected = (item.get("cell_id"), item.get("request_sha256"))
        if (registered != expected
                or request.request_hash != item.get("request_sha256")
                or request.model != item.get("model")
                or item.get("model_digest")
                != self.checkpoint.header()["model_digests"].get(request.model)):
            raise CheckpointError("work request differs from its frozen plan identity")

    def _control_resource_models(self, request: ControlRequest) -> set[str]:
        if request.model != SERVER_CONTROL_MODEL:
            return {request.model}
        models = self._stage_models(request.stage)
        return models or set(self.checkpoint.header()["model_digests"])

    def _finish_retryable_control(self, request: ControlRequest) -> ExecutionResult:
        records: list[BackoffRecord] = []

        def persist_resource_failure() -> None:
            records.extend(self._advance_resource(model)
                           for model in sorted(self._control_resource_models(request)))
            if not any(record.failures >= 6 for record in records):
                self.checkpoint.conn.execute(
                    "UPDATE run_state SET state='PAUSED_PREFLIGHT',updated=? WHERE id=1",
                    (time.time(),))

        self.checkpoint.finish_attempt(
            request.attempt_id, outcome="RETRYABLE_TRANSPORT", response=None,
            metadata={}, accept_work=False, before_commit=persist_resource_failure)
        paused_resource = any(record.failures >= 6 for record in records)
        outcome = "PAUSED_RESOURCE" if paused_resource else "PAUSED_PREFLIGHT"
        retry_at = max((record.retry_not_before for record in records), default=0.0)
        return ExecutionResult(outcome, request.attempt_id, retry_at)

    def _resource_gate(self, model: str) -> Optional[ExecutionResult]:
        priority = (self._f_context_recovery_owner()
                    if self.invocation_stage == "F" else None)
        if priority is not None:
            if model != priority.model:
                raise CheckpointError(
                    "pending Stage-F context has priority over another model")
            backoff = self.checkpoint.backoff(priority.model)
            if backoff.failures >= 6:
                raise CheckpointError(
                    "Stage-F context trigger requires exact recovery replay")
            if backoff.retry_not_before > self._now():
                return ExecutionResult(
                    "RETRY_WAIT", retry_not_before=backoff.retry_not_before)
            return None
        obligation = self._resource_obligation()
        if obligation is not None:
            raise CheckpointError(f"resource probe required for {obligation.model}")
        backoff = self.checkpoint.backoff(model)
        if backoff.retry_not_before > self._now():
            return ExecutionResult("RETRY_WAIT", retry_not_before=backoff.retry_not_before)
        return None

    @staticmethod
    def _require_returned_response(
            response: FakeResponse, allowed: set[str]) -> None:
        accepted = response.outcome == "ACCEPTED"
        if (type(response.outcome) is not str
                or type(response.accepted) is not bool
                or type(response.content) is not str
                or not isinstance(response.metadata, Mapping)
                or response.outcome not in allowed
                or response.accepted is not accepted):
            raise SafetyLimit("transport returned an invalid response outcome")

    def _safety_terminal(self) -> str:
        return ("BLOCKED_SECURITY"
                if self.checkpoint.header()["run_type"] == "private"
                else "FAILED_SAFETY")

    def _require_invocation_stage(self, stage: str) -> None:
        if self.invocation_stage is None or self.invocation_ordinal is None:
            raise CheckpointError("executor has no active invocation")
        if stage != self.invocation_stage:
            raise CheckpointError(
                f"request stage {stage} does not match invocation stage {self.invocation_stage}")

    def _f_seed1_control_groups(self) -> tuple[dict[str, Any], ...]:
        from .c0b2_runtime_common import _bound_control, load_runtime_control

        position = self.checkpoint.runtime_position()
        if (position.active_stage, position.active_plan_key) != ("F", "F_SEED_1"):
            return ()
        _parent, _digest, raw = self.checkpoint.load_phase_plan("F_SEED_1")
        plan = json.loads(raw)
        groups = []
        fields = {
            "context": ("context_control", "context_probe"),
            "cancel": ("cancellation_control", "cancellation_probe"),
            "health": ("health_control", "cancellation_health"),
        }
        for group in plan["groups"]:
            exact = {"group": group, "plan": plan}
            for name, (field, kind) in fields.items():
                value = group[field]
                record = load_runtime_control(self.checkpoint, value["control_id"])
                bound = _bound_control(self.checkpoint, "F_SEED_1", kind, value)
                if (bound != value or record.plan_key != "F_SEED_1"
                        or record.kind != kind):
                    raise CheckpointError("F seed-1 runtime control binding changed")
                exact[name] = record
            groups.append(exact)
        return tuple(groups)

    def _f_group_work_evidence(
            self, exact: Mapping[str, Any]) -> tuple[bool, bool]:
        group, plan = exact["group"], exact["plan"]
        planned = [row["work_id"] for row in plan["work"]
                   if row["activation_group_id"] == group["group_id"]]
        rows = self.checkpoint.conn.execute(
            "SELECT r.work_id,w.state,w.accepted_attempt_id FROM phase_work_registry r "
            "JOIN work_items w ON w.work_id=r.work_id WHERE r.plan_key='F_SEED_1' "
            "AND r.activation_group_id=? ORDER BY r.rowid", (group["group_id"],)
        ).fetchall()
        if [row[0] for row in rows] != planned or len(rows) != group["planned_work_count"]:
            raise CheckpointError("F seed-1 group work registry changed")
        answered, complete = False, True
        for work_id, state, accepted_id in rows:
            answered = answered or bool(self.checkpoint.conn.execute(
                "SELECT 1 FROM attempts WHERE work_id=? AND state IN "
                "('ACCEPTED','SCHEMA_INVALID') LIMIT 1", (work_id,)).fetchone())
            if state == "SUCCEEDED":
                valid = self.checkpoint.conn.execute(
                    "SELECT 1 FROM attempts WHERE attempt_id=? AND work_id=? "
                    "AND state='ACCEPTED'", (accepted_id, work_id)).fetchone()
            elif state == "COMPLETED_INVALID" and accepted_id is None:
                valid = self.checkpoint.conn.execute(
                    "SELECT count(*) FROM attempts WHERE work_id=? "
                    "AND state='SCHEMA_INVALID'", (work_id,)).fetchone()[0] >= 2
            else:
                valid = False
            complete = complete and bool(valid)
        return answered, complete

    def _f_context_recovery_owner(self) -> Optional[_FContextRecovery]:
        owners: list[_FContextRecovery] = []
        frozen_models = self.checkpoint.header()["model_digests"]
        for exact in self._f_seed1_control_groups():
            answered, _complete = self._f_group_work_evidence(exact)
            if not answered:
                continue
            context = exact["context"]
            if context.state == "COMPLETE":
                continue
            if context.state != "PENDING":
                raise CheckpointError(
                    "answered Stage-F candidate has an invalid context state")
            group, plan = exact["group"], exact["plan"]
            controls = group["context_control"]
            work = [row for row in plan["work"]
                    if row["activation_group_id"] == group["group_id"]]
            work_ids = [row["work_id"] for row in work]
            counts = dict(self.checkpoint.conn.execute(
                "SELECT work_id,count(*) FROM attempts WHERE work_id IN ("
                + ",".join("?" for _ in work_ids)
                + ") AND state IN ('ACCEPTED','SCHEMA_INVALID') GROUP BY work_id",
                work_ids,
            ))
            triggered = [row for row in work if counts.get(row["work_id"], 0)]
            answered_attempts = sum(int(value) for value in counts.values())
            if not triggered:
                raise CheckpointError(
                    "Stage-F context owner lacks its answered trigger")
            if len(triggered) != 1 or answered_attempts != 1:
                raise CheckpointError(
                    "Stage-F work crossed its pending context barrier")
            trigger = triggered[0]
            if (trigger["work_id"] != group["first_work_id"]
                    or trigger["candidate_id"] != group["candidate_id"]
                    or controls["candidate_id"] != group["candidate_id"]
                    or trigger["model"] != controls["model"]
                    or trigger["model_digest"] != controls["model_digest"]
                    or frozen_models.get(trigger["model"])
                    != trigger["model_digest"]):
                raise CheckpointError(
                    "Stage-F context trigger identity differs from its frozen owner")
            owners.append(_FContextRecovery(
                context_control_id=str(controls["control_id"]),
                candidate_id=str(group["candidate_id"]),
                model=str(trigger["model"]),
                trigger_work_id=str(trigger["work_id"]),
                trigger_request_hash=str(trigger["request_sha256"]),
            ))
        if len(owners) > 1:
            raise CheckpointError(
                "multiple Stage-F context recovery owners are pending")
        return owners[0] if owners else None

    def _require_f_scored_control_order(self, stage: str) -> None:
        if stage != "F":
            return
        groups = self._f_seed1_control_groups()
        evidence = [(row, *self._f_group_work_evidence(row)) for row in groups]
        if any(answered and row["context"].state != "COMPLETE"
               for row, answered, _complete in evidence):
            raise CheckpointError("F context probe is required before more scored work")
        if any(row["cancel"].state == "CANCELLED_UNVERIFIED"
               and row["health"].state != "COMPLETE"
               for row, _answered, _complete in evidence):
            raise CheckpointError("F cancellation health is required before scored work")
        if any(complete and row["context"].state == "COMPLETE"
               and row["cancel"].state == "PENDING"
               for row, _answered, complete in evidence):
            raise CheckpointError("F cancellation probe is required before more scored work")

    def _require_f_cancellation_ready(self, candidate_id: str) -> None:
        matches = [row for row in self._f_seed1_control_groups()
                   if row["group"]["candidate_id"] == candidate_id]
        if len(matches) != 1:
            raise CheckpointError("F cancellation candidate is not uniquely active")
        _answered, complete = self._f_group_work_evidence(matches[0])
        if matches[0]["context"].state != "COMPLETE" or not complete:
            raise CheckpointError(
                "F cancellation requires completed context and seed-1 work")

    def _guard_soft_wall(self) -> None:
        if self.clock.crossed():
            raise SoftWallReached

    def _guard_invocation_claim(self) -> None:
        if self.cancellation.event.is_set():
            raise InvocationCancelled("cancelled during invocation claim")

    def _reset_resource(self, model: str) -> None:
        self.checkpoint.conn.execute("DELETE FROM model_backoff WHERE model=?", (model,))

    def _advance_resource(self, model: str) -> BackoffRecord:
        now = self._now()
        row = self.checkpoint.conn.execute(
            "SELECT failures FROM model_backoff WHERE model=?", (model,)).fetchone()
        failures = (int(row[0]) if row else 0) + 1
        waits = (15, 30, 60, 120, 240, 300)
        retry_at = now + waits[min(failures, 6) - 1]
        self.checkpoint.conn.execute(
            "INSERT INTO model_backoff VALUES(?,?,?,?) ON CONFLICT(model) DO UPDATE SET "
            "failures=excluded.failures,retry_not_before=excluded.retry_not_before,updated=excluded.updated",
            (model, failures, retry_at, time.time()))
        if failures >= 6:
            self.checkpoint.conn.execute(
                "UPDATE run_state SET state='PAUSED_RESOURCE',updated=? WHERE id=1",
                (time.time(),))
        return BackoffRecord(model, failures, retry_at)

    def _resource_obligation(self) -> Optional[BackoffRecord]:
        if self.invocation_stage == "F":
            models = sorted(self._stage_models("F"))
            if not models:
                return None
            placeholders = ",".join("?" for _ in models)
            row = self.checkpoint.conn.execute(
                "SELECT model,failures,retry_not_before FROM model_backoff "
                f"WHERE failures>=6 AND model IN ({placeholders}) "
                "ORDER BY model LIMIT 1", models).fetchone()
        else:
            row = self.checkpoint.conn.execute(
                "SELECT model,failures,retry_not_before FROM model_backoff "
                "WHERE failures>=6 ORDER BY model LIMIT 1").fetchone()
        return BackoffRecord(str(row[0]), int(row[1]), float(row[2])) if row else None
