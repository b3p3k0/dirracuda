"""Serial, charged Ollama orchestration for Analyst Phase 2."""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .checkpoint import (
    ChunkSpec,
    build_extraction_evidence,
    finish_valid_attempt,
    terminalize_file,
    verify_chunks,
)
from .contact_contract import (
    TAGS_REQUEST_SHA256,
    VERSION_REQUEST_SHA256,
    ContactKind,
    ContactStatus,
    ScheduleState,
)
from .lease import (
    LeaseError,
    LeaseFence,
    acknowledge_cancel,
    pulse_worker,
    release_worker,
)
from .models import Assessment, FileTerminal
from .ollama_client import OllamaClient
from .ollama_contract import (
    EXPECTED_IDENTITY,
    MODEL_DIGEST,
    MODEL_TAG,
    NUM_CTX,
    NUM_PREDICT,
    WORKSHEET_VERSION,
    OllamaStatus,
    PromptKind,
    ChatResult,
    TagsCheckResult,
    VersionCheckResult,
    build_chat_request,
    build_repair_chat_request,
)
from .ollama_state import (
    ResourceWaitCancelled,
    finish_contact,
    precharge_chat_contact,
    precharge_control_contact,
    wait_until_resource_retry_due,
)
from .phase1 import Phase1Dependencies
from .phase1_state import (
    Phase1EvidenceMismatch,
    load_phase1_handoff,
    verify_detector_checkpoint,
    verify_extraction_evidence,
)
from .phase2_contract import (
    CANCELLATION_HEALTH_DELAY_SECONDS,
    HEALTH_REQUEST_SHA256,
    Phase2Handoff,
    Phase2Outcome,
    build_health_chat_request,
    derive_nonce,
)
from .phase2_state import (
    Phase2FileSnapshot,
    Phase2StateError,
    claim_next_phase2_file,
    close_exhausted_ambiguous_chunk,
    close_nonretryable_chunk,
    deduplicate_grounded_result,
    finish_phase2_file,
    load_health_obligation,
    load_phase2_snapshot,
    load_phase2_totals,
)
from .source_reopen import SourceReopenCancelled, SourceReopenError, open_inventory_file
from .state import AttemptState
from .worker_contract import (
    MAX_DETECTOR_HITS,
    Phase1Handoff,
    WorkerRunContext,
)
from .worksheet import (
    WorksheetSemanticError,
    parse_and_ground,
    prompt_template_hash,
    schema_hash,
)


HEARTBEAT_INTERVAL_SECONDS = 2.0
WAIT_PULSE_SECONDS = 1.0


class Phase2Failure(str, Enum):
    CONTRACT = "contract"
    LEASE = "lease"
    PREFLIGHT = "preflight"
    SOURCE = "source"
    RESUME_MISMATCH = "resume_mismatch"
    MODEL = "model"
    STATE = "state"


class Phase2Error(RuntimeError):
    """A content-free Phase 2 failure requiring resumable recovery."""

    def __init__(self, code: Phase2Failure) -> None:
        if type(code) is not Phase2Failure:
            raise TypeError("Phase 2 failure code is not closed")
        self.code = code
        super().__init__(code.value)


class Phase2Cancelled(Phase2Error):
    pass


class Phase2Interrupted(Phase2Error):
    pass


class Phase2PausedResource(Phase2Error):
    pass


@dataclass(frozen=True, slots=True)
class Phase2Dependencies:
    """Injectable transport/private-work functions with no durable-state seam."""

    client: Any = field(default_factory=OllamaClient, repr=False)
    phase1: Phase1Dependencies = field(default_factory=Phase1Dependencies, repr=False)
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    utc_now: Callable[[], str] = field(
        default=lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        repr=False,
    )

    def __post_init__(self) -> None:
        client_methods = ("check_version", "check_tags", "chat", "cancel_current")
        if (
            any(not callable(getattr(self.client, name, None)) for name in client_methods)
            or type(self.phase1) is not Phase1Dependencies
            or not callable(self.monotonic)
            or not callable(self.sleep)
            or not callable(self.utc_now)
        ):
            raise TypeError("Phase 2 dependencies are invalid")


class _DurableStop(Exception):
    pass


class _LocalStop(Exception):
    pass


class _ResourcePause(Exception):
    pass


@dataclass(slots=True)
class _RegeneratedFile:
    text: str = field(repr=False)
    format_name: str
    evidence: Any = field(repr=False)
    hits: tuple[Any, ...] = field(repr=False)
    chunks: tuple[ChunkSpec, ...] = field(repr=False)


@dataclass(slots=True)
class _FenceOwner:
    fence: LeaseFence
    stop_event: threading.Event
    dependencies: Phase2Dependencies
    path: Path | None
    last_pulse: float

    @classmethod
    def create(
        cls,
        fence: LeaseFence,
        stop_event: threading.Event,
        dependencies: Phase2Dependencies,
        path: Path | None,
    ) -> "_FenceOwner":
        now = float(dependencies.monotonic())
        if not math.isfinite(now):
            raise Phase2Error(Phase2Failure.CONTRACT)
        return cls(fence, stop_event, dependencies, path, now)

    def pulse(self, *, force: bool = False) -> LeaseFence:
        now = float(self.dependencies.monotonic())
        if not math.isfinite(now) or now < self.last_pulse:
            raise Phase2Error(Phase2Failure.CONTRACT)
        if not force and not self.stop_event.is_set() and (
            now - self.last_pulse < HEARTBEAT_INTERVAL_SECONDS
        ):
            return self.fence
        value = self.dependencies.phase1.monotonic_ns()
        if type(value) is not int or value < 0:
            raise Phase2Error(Phase2Failure.CONTRACT)
        value = max(value, self.fence.heartbeat_monotonic_ns + 1)
        try:
            observed = pulse_worker(
                self.fence, heartbeat_monotonic_ns=value, path=self.path,
            )
        except LeaseError:
            self.stop_event.set()
            raise Phase2Error(Phase2Failure.LEASE) from None
        self.fence = observed.fence
        self.last_pulse = now
        if observed.cancel_requested:
            self.stop_event.set()
            raise _DurableStop
        if self.stop_event.is_set():
            raise _LocalStop
        return self.fence

    def client_poll(self) -> None:
        self.pulse(force=self.stop_event.is_set())

    def heartbeat(self, expected: LeaseFence) -> LeaseFence:
        if expected != self.fence:
            raise Phase2Error(Phase2Failure.LEASE)
        return self.pulse(force=True)


def run_phase2(
    context: WorkerRunContext,
    handoff: Phase1Handoff,
    stop_event: threading.Event,
    *,
    path: Path | None = None,
    dependencies: Phase2Dependencies | None = None,
) -> Phase2Handoff:
    """Consume every durable selected chunk and retain the successor fence for C12."""
    if (
        type(context) is not WorkerRunContext
        or type(handoff) is not Phase1Handoff
        or not isinstance(stop_event, threading.Event)
    ):
        raise TypeError("Phase 2 requires typed context, handoff and stop Event")
    chosen = Phase2Dependencies() if dependencies is None else dependencies
    if type(chosen) is not Phase2Dependencies:
        raise TypeError("dependencies must be Phase2Dependencies")
    if context.run_id != handoff.fence.run_id:
        raise Phase2Error(Phase2Failure.CONTRACT)
    _require_runtime_contract(context)
    owner = _FenceOwner.create(handoff.fence, stop_event, chosen, path)
    lease_owned = True
    try:
        if load_phase1_handoff(owner.pulse(force=True), path=path) != handoff.files:
            raise Phase2Error(Phase2Failure.CONTRACT)
        snapshot = claim_next_phase2_file(owner.pulse(force=True), path=path)
        if snapshot is None:
            reviewed, valid, findings = load_phase2_totals(owner.fence, path=path)
            return Phase2Handoff(owner.fence, reviewed, valid, findings)
        preflight_done = False
        while snapshot is not None:
            if _snapshot_needs_model(snapshot):
                if not preflight_done:
                    _run_identity_preflight(owner)
                    preflight_done = True
                _ensure_health(owner)
                try:
                    _process_selected_file(context, owner, snapshot)
                except SourceReopenCancelled:
                    # The opener raises this only after the cooperative Event is set.
                    # Pulse once more so durable cancellation wins over local stop.
                    owner.pulse(force=True)
                    raise Phase2Error(Phase2Failure.LEASE)
                except SourceReopenError:
                    terminalize_file(
                        owner.pulse(force=True),
                        snapshot.file_id,
                        FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY,
                        path=path,
                    )
                    snapshot = claim_next_phase2_file(
                        owner.pulse(force=True), path=path,
                    )
                    continue
            finish_phase2_file(owner.pulse(force=True), snapshot.file_id, path=path)
            snapshot = claim_next_phase2_file(owner.pulse(force=True), path=path)
        reviewed, valid, findings = load_phase2_totals(owner.fence, path=path)
        return Phase2Handoff(owner.fence, reviewed, valid, findings)
    except _ResourcePause:
        lease_owned = False
        raise Phase2PausedResource(Phase2Failure.MODEL) from None
    except _DurableStop:
        if lease_owned:
            _best_effort_acknowledge_cancel(owner)
            lease_owned = False
        raise Phase2Cancelled(Phase2Failure.LEASE) from None
    except _LocalStop:
        if lease_owned:
            state = _best_effort_release(owner)
            lease_owned = False
            if state is not None and state.value == "cancelled_pending_resume":
                raise Phase2Cancelled(Phase2Failure.LEASE) from None
        raise Phase2Interrupted(Phase2Failure.LEASE) from None
    except Phase2Error:
        if lease_owned:
            _best_effort_release(owner)
        raise
    except (Phase1EvidenceMismatch, SourceReopenCancelled):
        if lease_owned:
            _best_effort_release(owner)
        raise Phase2Error(Phase2Failure.RESUME_MISMATCH) from None
    except SourceReopenError:
        if lease_owned:
            _best_effort_release(owner)
        raise Phase2Error(Phase2Failure.SOURCE) from None
    except Exception:
        if lease_owned:
            _best_effort_release(owner)
        raise Phase2Error(Phase2Failure.STATE) from None


def _best_effort_release(owner: _FenceOwner) -> Any | None:
    try:
        return release_worker(owner.fence, path=owner.path)
    except Exception:
        return None


def _best_effort_acknowledge_cancel(owner: _FenceOwner) -> None:
    try:
        acknowledge_cancel(owner.fence, path=owner.path)
    except Exception:
        return


def _require_runtime_contract(context: WorkerRunContext) -> None:
    if (
        context.model_tag != MODEL_TAG
        or context.model_digest != MODEL_DIGEST
        or context.worksheet_version != WORKSHEET_VERSION
        or context.prompt_sha256 != prompt_template_hash()
        or context.response_schema_sha256 != schema_hash()
        or context.num_ctx != NUM_CTX
        or context.num_predict != NUM_PREDICT
    ):
        raise Phase2Error(Phase2Failure.CONTRACT)


def _snapshot_needs_model(snapshot: Phase2FileSnapshot) -> bool:
    return any(chunk.state.value == "pending" for chunk in snapshot.chunks)


def _run_identity_preflight(owner: _FenceOwner) -> None:
    version = _run_control(
        owner,
        ContactKind.VERSION,
        VERSION_REQUEST_SHA256,
        lambda: owner.dependencies.client.check_version(
            cancel=owner.stop_event.is_set, poll=owner.client_poll,
        ),
        VersionCheckResult,
    )
    if version is not OllamaStatus.SUCCESS:
        raise Phase2Error(Phase2Failure.PREFLIGHT)
    tags = _run_control(
        owner,
        ContactKind.TAGS,
        TAGS_REQUEST_SHA256,
        lambda: owner.dependencies.client.check_tags(
            EXPECTED_IDENTITY,
            cancel=owner.stop_event.is_set,
            poll=owner.client_poll,
        ),
        TagsCheckResult,
    )
    if tags is not OllamaStatus.SUCCESS:
        raise Phase2Error(Phase2Failure.PREFLIGHT)


def _run_control(
    owner: _FenceOwner,
    kind: ContactKind,
    request_sha256: str,
    invoke: Callable[[], Any],
    result_type: type,
) -> OllamaStatus:
    while True:
        charge = precharge_control_contact(
            owner.pulse(force=True), kind, request_sha256, path=owner.path,
        )
        result = invoke()
        if type(result) is not result_type:
            raise Phase2Error(Phase2Failure.CONTRACT)
        status = result.status
        if type(status) is not OllamaStatus:
            raise Phase2Error(Phase2Failure.CONTRACT)
        finished = finish_contact(
            owner.pulse(force=True), charge.contact_id,
            ContactStatus(status.value), path=owner.path,
        )
        if status is not OllamaStatus.RESOURCE_BUSY:
            return status
        if finished.lease_released:
            raise _ResourcePause
        _wait_resource(owner, finished.schedule)


def _ensure_health(owner: _FenceOwner) -> None:
    obligation = load_health_obligation(owner.pulse(force=True), path=owner.path)
    if obligation is None:
        return
    _wait_health_delay(owner)
    request = build_health_chat_request()
    status = _run_control(
        owner,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        lambda: owner.dependencies.client.chat(
            request,
            expected_sha256=request.request_sha256,
            cancel=owner.stop_event.is_set,
            poll=owner.client_poll,
        ),
        ChatResult,
    )
    if status not in {OllamaStatus.SUCCESS, OllamaStatus.MODEL_INVALID}:
        raise Phase2Error(Phase2Failure.MODEL)
    if load_health_obligation(owner.pulse(force=True), path=owner.path) is not None:
        raise Phase2Error(Phase2Failure.STATE)


def _wait_health_delay(owner: _FenceOwner) -> None:
    start = float(owner.dependencies.monotonic())
    if not math.isfinite(start):
        raise Phase2Error(Phase2Failure.CONTRACT)
    deadline = start + CANCELLATION_HEALTH_DELAY_SECONDS
    observed = start
    while observed < deadline:
        owner.pulse(force=True)
        owner.dependencies.sleep(min(WAIT_PULSE_SECONDS, deadline - observed))
        observed = float(owner.dependencies.monotonic())
        if not math.isfinite(observed) or observed < start:
            raise Phase2Error(Phase2Failure.CONTRACT)
        start = observed


def _wait_resource(owner: _FenceOwner, schedule: Any) -> None:
    now_utc = owner.dependencies.utc_now()
    try:
        successor, updated = wait_until_resource_retry_due(
            owner.fence,
            schedule,
            cancelled=owner.stop_event.is_set,
            heartbeat=owner.heartbeat,
            now_utc=now_utc,
            observed_utc=owner.dependencies.utc_now,
            path=owner.path,
            monotonic=owner.dependencies.monotonic,
            sleep=owner.dependencies.sleep,
            pulse_seconds=WAIT_PULSE_SECONDS,
        )
    except ResourceWaitCancelled:
        owner.pulse(force=True)
        raise Phase2Error(Phase2Failure.MODEL) from None
    owner.fence = successor
    if updated.state is not ScheduleState.BACKOFF:
        raise Phase2Error(Phase2Failure.STATE)


def _process_selected_file(
    context: WorkerRunContext,
    owner: _FenceOwner,
    snapshot: Phase2FileSnapshot,
) -> None:
    source_text, regenerated = _regenerate_file(context, owner, snapshot)
    try:
        while True:
            current = load_phase2_snapshot(
                owner.pulse(force=True), snapshot.file_id, path=owner.path,
            )
            pending = next(
                (chunk for chunk in current.chunks if chunk.state.value == "pending"),
                None,
            )
            if pending is None:
                return
            _ensure_health(owner)
            if len(pending.attempts) >= 2:
                close_exhausted_ambiguous_chunk(
                    owner.pulse(force=True), pending.identity.chunk_id, path=owner.path,
                )
                continue
            if pending.attempts and pending.attempts[-1].state is AttemptState.MODEL_TRANSPORT_ERROR:
                try:
                    close_nonretryable_chunk(
                        owner.pulse(force=True), pending.identity.chunk_id,
                        path=owner.path,
                    )
                except Phase2StateError:
                    pass
                else:
                    continue
            chunk_text = source_text[pending.identity.start:pending.identity.end]
            _validate_attempt_request_history(
                context.run_id, pending, chunk_text,
            )
            prompt_kind = (
                PromptKind.MODEL_INVALID_REPAIR
                if pending.attempts
                and pending.attempts[-1].state is AttemptState.SCHEMA_INVALID
                else PromptKind.PRIMARY
            )
            request = _build_request(
                context.run_id, pending.identity, prompt_kind, chunk_text,
            )
            try:
                _dispatch_chunk(
                    owner, pending.identity.chunk_id, request, chunk_text,
                )
            finally:
                chunk_text = ""
                request = None
    finally:
        source_text = ""
        regenerated = ()


def _regenerate_file(
    context: WorkerRunContext,
    owner: _FenceOwner,
    snapshot: Phase2FileSnapshot,
) -> tuple[str, tuple[ChunkSpec, ...]]:
    owner.pulse(force=True)
    future = None
    product = None
    executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="analyst-phase2-regenerate",
    )
    try:
        future = executor.submit(
            _regenerate_product,
            context,
            owner.dependencies.phase1,
            owner.stop_event,
            snapshot,
        )
        while True:
            try:
                owner.pulse()
            except BaseException:
                owner.stop_event.set()
                raise
            try:
                product = future.result(timeout=WAIT_PULSE_SECONDS)
                break
            except FutureTimeout:
                continue
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        future = None
    if type(product) is not _RegeneratedFile:
        raise Phase2Error(Phase2Failure.CONTRACT)
    try:
        owner.pulse(force=True)
        verify_extraction_evidence(
            owner.fence,
            snapshot.file_id,
            product.evidence,
            authenticated_format_name=product.format_name,
            path=owner.path,
        )
        verify_detector_checkpoint(
            owner.pulse(force=True), snapshot.file_id, product.hits,
            selected_for_model=True, path=owner.path,
        )
        verify_chunks(
            owner.pulse(force=True), snapshot.file_id, product.chunks,
            path=owner.path,
        )
        return product.text, product.chunks
    finally:
        product = None


def _regenerate_product(
    context: WorkerRunContext,
    dependencies: Phase1Dependencies,
    stop_event: threading.Event,
    snapshot: Phase2FileSnapshot,
) -> _RegeneratedFile:
    extraction = None
    evidence = None
    hits: tuple[Any, ...] = ()
    chunks: tuple[ChunkSpec, ...] = ()
    try:
        with open_inventory_file(
            Path(context.source_root),
            context.root_identity,
            snapshot.inventory_file,
            cancel_check=stop_event.is_set,
        ) as source:
            extraction = dependencies.extract(
                source.fileno(), snapshot.inventory_file, stop_event.is_set,
            )
        if stop_event.is_set():
            raise SourceReopenCancelled("Phase 2 regeneration was cancelled")
        if not extraction.ok or type(extraction.text) is not str or not extraction.text:
            raise Phase2Error(Phase2Failure.RESUME_MISMATCH)
        evidence = build_extraction_evidence(extraction)
        hits, overflow = dependencies.detect(
            extraction.text, MAX_DETECTOR_HITS, stop_event.is_set,
        )
        if stop_event.is_set():
            raise SourceReopenCancelled("Phase 2 detection was cancelled")
        if overflow:
            raise Phase2Error(Phase2Failure.RESUME_MISMATCH)
        chunks = tuple(dependencies.chunk(
            extraction.text,
            context.chunk_chars,
            context.overlap_chars,
            stop_event.is_set,
        ))
        if stop_event.is_set():
            raise SourceReopenCancelled("Phase 2 chunking was cancelled")
        return _RegeneratedFile(
            extraction.text, str(extraction.format_name), evidence,
            tuple(hits), chunks,
        )
    finally:
        extraction = None
        evidence = None
        hits = ()
        chunks = ()


def _build_request(
    run_id: str,
    identity: Any,
    prompt_kind: PromptKind,
    chunk_text: str,
) -> Any:
    nonce = derive_nonce(
        run_id, identity.chunk_id, identity.sha256, prompt_kind, chunk_text,
    )
    return (
        build_repair_chat_request(chunk_text, nonce=nonce)
        if prompt_kind is PromptKind.MODEL_INVALID_REPAIR
        else build_chat_request(chunk_text, nonce=nonce)
    )


def _validate_attempt_request_history(
    run_id: str,
    chunk: Any,
    chunk_text: str,
) -> None:
    """Bind every durable semantic slot to its reconstructible request bytes."""
    attempts = chunk.attempts
    if not attempts:
        return
    primary = _build_request(
        run_id, chunk.identity, PromptKind.PRIMARY, chunk_text,
    )
    try:
        if attempts[0].request_sha256 != primary.request_sha256:
            raise Phase2Error(Phase2Failure.RESUME_MISMATCH)
        if len(attempts) == 1:
            return
        first_state = attempts[0].state
        if first_state is AttemptState.SCHEMA_INVALID:
            expected = _build_request(
                run_id,
                chunk.identity,
                PromptKind.MODEL_INVALID_REPAIR,
                chunk_text,
            ).request_sha256
        elif first_state in {
            AttemptState.MODEL_TIMEOUT,
            AttemptState.MODEL_TRANSPORT_ERROR,
            AttemptState.ORPHANED_UNKNOWN,
            AttemptState.CANCELLED_UNVERIFIED,
        }:
            expected = primary.request_sha256
        else:
            raise Phase2Error(Phase2Failure.RESUME_MISMATCH)
        if attempts[1].request_sha256 != expected:
            raise Phase2Error(Phase2Failure.RESUME_MISMATCH)
    finally:
        primary = None


def _dispatch_chunk(
    owner: _FenceOwner,
    chunk_id: int,
    request: Any,
    chunk_text: str,
) -> None:
    response = None
    grounded = None
    finished = None
    charge = None
    try:
        while True:
            charge = precharge_chat_contact(
                owner.pulse(force=True), chunk_id, request.request_sha256,
                path=owner.path,
            )
            response = owner.dependencies.client.chat(
                request,
                expected_sha256=request.request_sha256,
                cancel=owner.stop_event.is_set,
                poll=owner.client_poll,
            )
            if type(response) is not ChatResult:
                raise Phase2Error(Phase2Failure.CONTRACT)
            status = response.status
            if type(status) is not OllamaStatus:
                raise Phase2Error(Phase2Failure.CONTRACT)
            grounded = None
            durable_status = status
            if status is OllamaStatus.SUCCESS:
                try:
                    grounded = parse_and_ground(response.content, chunk_text)
                    if (
                        not grounded.findings
                        and grounded.model_assessment is Assessment.FINDINGS_PRESENT
                    ):
                        raise WorksheetSemanticError(
                            "findings-present answer retained no grounded evidence"
                        )
                    grounded = deduplicate_grounded_result(
                        owner.pulse(force=True), chunk_id, grounded, path=owner.path,
                    )
                except (TypeError, ValueError, WorksheetSemanticError):
                    grounded = None
                    durable_status = OllamaStatus.MODEL_INVALID
            finished = finish_contact(
                owner.pulse(force=True), charge.contact_id,
                ContactStatus(durable_status.value), path=owner.path,
            )
            if durable_status is OllamaStatus.RESOURCE_BUSY:
                if finished.lease_released:
                    raise _ResourcePause
                schedule = finished.schedule
                response = None
                finished = None
                charge = None
                _wait_resource(owner, schedule)
                schedule = None
                continue
            if durable_status is OllamaStatus.SUCCESS:
                if grounded is None or finished.attempt_id is None:
                    raise Phase2Error(Phase2Failure.STATE)
                finish_valid_attempt(
                    owner.pulse(force=True), finished.attempt_id, grounded,
                    path=owner.path,
                )
            elif durable_status in {
                OllamaStatus.IDENTITY_MISMATCH,
                OllamaStatus.PROTOCOL_VIOLATION,
                OllamaStatus.RESPONSE_LIMIT,
            }:
                close_nonretryable_chunk(
                    owner.pulse(force=True), chunk_id, path=owner.path,
                )
            return
    finally:
        response = None
        grounded = None
        finished = None
        charge = None
        request = None
        chunk_text = ""


__all__ = [
    "Phase2Cancelled",
    "Phase2Dependencies",
    "Phase2Error",
    "Phase2Failure",
    "Phase2Interrupted",
    "Phase2Outcome",
    "Phase2PausedResource",
    "run_phase2",
]
