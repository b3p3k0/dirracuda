"""Bounded, fenced Phase 1 orchestration for the Analyst worker."""

from __future__ import annotations

import hashlib
import gc
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from .checkpoint import (
    MAX_DETECTOR_HITS,
    ChunkSpec,
    ExtractionCheckpointEvidence,
    advance_file_stage,
    build_extraction_evidence,
    checkpoint_detector,
    terminalize_file,
)
from .detectors import DetectorScanCancelled, scan_bounded
from .extract import ExtractionResult, extract_document
from .inventory import InventoryFile
from .lease import (
    LeaseFence,
    acknowledge_cancel,
    pulse_worker,
    release_worker,
)
from .models import DetectorHit, FileStage, FileTerminal
from .phase1_state import (
    Phase1EvidenceMismatch,
    claim_next_phase1_file,
    handoff_selected_file,
    load_phase1_handoff,
    verify_detector_checkpoint,
    verify_extraction_evidence,
)
from .source_reopen import (
    SourceReopenCancelled,
    SourceReopenError,
    open_inventory_file,
)
from .state import RunState
from .worker_contract import (
    MAX_CHUNKS_PER_FILE,
    MAX_PHASE1_TASKS,
    WORKER_POLL_SECONDS,
    FileResumeSnapshot,
    Phase1FileHandoff,
    Phase1Handoff,
    WorkerRunContext,
)


ExtractFn = Callable[
    [int, InventoryFile, Callable[[], bool]], ExtractionResult
]
DetectFn = Callable[
    [str, int, Callable[[], bool]], tuple[list[DetectorHit], bool]
]
ChunkFn = Callable[
    [str, int, int, Callable[[], bool]], Iterable[ChunkSpec]
]


class Phase1Failure(str, Enum):
    CONTRACT = "contract"
    LEASE = "lease"
    SOURCE = "source"
    EXTRACTOR = "extractor"
    RESUME_MISMATCH = "resume_mismatch"
    STATE = "state"


class Phase1Error(RuntimeError):
    """A content-free Phase 1 failure that requires operator-visible recovery."""

    def __init__(self, code: Phase1Failure) -> None:
        if type(code) is not Phase1Failure:
            raise TypeError("Phase 1 errors require a closed failure code")
        self.code = code
        super().__init__(code.value)


class Phase1Cancelled(Phase1Error):
    """Durable operator cancellation was acknowledged and released."""


class Phase1Interrupted(Phase1Error):
    """A local stop safely returned unfinished work for resume."""


def _default_extract(
    source_fd: int,
    expected: InventoryFile,
    cancel_check: Callable[[], bool],
) -> ExtractionResult:
    return extract_document(
        source_fd=source_fd,
        expected=expected,
        cancel_check=cancel_check,
    )


def _default_detect(
    text: str, limit: int, cancel_check: Callable[[], bool],
) -> tuple[list[DetectorHit], bool]:
    return scan_bounded(
        text, max_hits=limit, cancel_check=cancel_check,
    )


def _default_chunks(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
    cancel_check: Callable[[], bool],
) -> tuple[ChunkSpec, ...]:
    stride = chunk_chars - overlap_chars
    chunks: list[ChunkSpec] = []
    start = 0
    while start < len(text):
        if cancel_check():
            raise DetectorScanCancelled
        end = min(start + chunk_chars, len(text))
        digest = hashlib.sha256(
            text[start:end].encode("utf-8", errors="strict")
        ).hexdigest()
        chunks.append(ChunkSpec(len(chunks), start, end, digest))
        if end == len(text):
            break
        start += stride
    if cancel_check():
        raise DetectorScanCancelled
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class Phase1Dependencies:
    """Injectable pure/private-work functions; durable state is never injected."""

    extract: ExtractFn = field(default=_default_extract, repr=False)
    detect: DetectFn = field(default=_default_detect, repr=False)
    chunk: ChunkFn = field(default=_default_chunks, repr=False)
    monotonic_ns: Callable[[], int] = field(default=time.monotonic_ns, repr=False)

    def __post_init__(self) -> None:
        if not all(callable(item) for item in (
            self.extract, self.detect, self.chunk, self.monotonic_ns,
        )):
            raise TypeError("Phase 1 dependencies must be callable")


@dataclass(frozen=True, slots=True)
class _WorkProduct:
    snapshot: FileResumeSnapshot
    extraction: ExtractionResult | None = field(repr=False)
    evidence: ExtractionCheckpointEvidence | None = field(repr=False)
    hits: tuple[DetectorHit, ...] = field(repr=False)
    detector_overflow: bool
    selected_for_model: bool
    chunks: tuple[ChunkSpec, ...] = field(repr=False)
    source_changed: bool = False
    cancelled: bool = False


class _DurableStop(Exception):
    pass


class _LocalStop(Exception):
    pass


@dataclass(slots=True)
class _FenceOwner:
    fence: LeaseFence
    stop_event: threading.Event
    dependencies: Phase1Dependencies
    path: Path | None

    def pulse(self) -> LeaseFence:
        value = self.dependencies.monotonic_ns()
        if type(value) is not int or value < 0:
            raise Phase1Error(Phase1Failure.CONTRACT)
        value = max(value, self.fence.heartbeat_monotonic_ns + 1)
        pulse = pulse_worker(
            self.fence, heartbeat_monotonic_ns=value, path=self.path,
        )
        self.fence = pulse.fence
        if pulse.cancel_requested:
            self.stop_event.set()
            raise _DurableStop
        if self.stop_event.is_set():
            raise _LocalStop
        return self.fence


def run_phase1(
    context: WorkerRunContext,
    fence: LeaseFence,
    stop_event: threading.Event,
    *,
    path: Path | None = None,
    dependencies: Phase1Dependencies | None = None,
) -> Phase1Handoff:
    """Run all pre-model files while retaining one exact successor lease fence."""
    if type(context) is not WorkerRunContext or type(fence) is not LeaseFence:
        raise TypeError("Phase 1 requires typed worker context and lease fence")
    if not isinstance(stop_event, threading.Event):
        raise TypeError("Phase 1 requires a threading.Event stop signal")
    chosen = Phase1Dependencies() if dependencies is None else dependencies
    if type(chosen) is not Phase1Dependencies:
        raise TypeError("dependencies must be Phase1Dependencies")
    if context.run_id != fence.run_id:
        raise Phase1Error(Phase1Failure.CONTRACT)

    owner = _FenceOwner(fence, stop_event, chosen, path)
    executor = ThreadPoolExecutor(
        max_workers=MAX_PHASE1_TASKS, thread_name_prefix="analyst-phase1",
    )
    futures: dict[Future[_WorkProduct], FileResumeSnapshot] = {}
    completed_files = None
    failure: tuple[str, Phase1Failure] | None = None
    try:
        completed_files = _drive_phase1(
            context, owner, executor, futures, chosen,
        )
    except _DurableStop:
        failure = ("cancelled", Phase1Failure.STATE)
    except _LocalStop:
        failure = ("interrupted", Phase1Failure.STATE)
    except Phase1Error as exc:
        failure = ("error", exc.code)
    except Exception:
        failure = ("error", Phase1Failure.STATE)
    finally:
        if failure is not None:
            stop_event.set()
        for pending_future in futures:
            pending_future.cancel()
        if futures:
            del pending_future
        executor.shutdown(wait=True, cancel_futures=True)
        futures.clear()
        # Future tracebacks can form cycles containing bounded private results.
        # Drop them before the lease is acknowledged or released.
        gc.collect()

    if failure is None:
        assert completed_files is not None
        return Phase1Handoff(fence=owner.fence, files=completed_files)
    return _close_failure(owner, failure)


def _drive_phase1(
    context: WorkerRunContext,
    owner: _FenceOwner,
    executor: ThreadPoolExecutor,
    futures: dict[Future[_WorkProduct], FileResumeSnapshot],
    dependencies: Phase1Dependencies,
) -> tuple[Phase1FileHandoff, ...]:
    while True:
        owner.pulse()
        queue_empty = False
        while len(futures) < MAX_PHASE1_TASKS and not queue_empty:
            owner.pulse()
            snapshot = claim_next_phase1_file(owner.fence, path=owner.path)
            if snapshot is None:
                queue_empty = True
                break
            submitted = executor.submit(
                _process_file,
                context,
                snapshot,
                owner.stop_event,
                dependencies,
            )
            futures[submitted] = snapshot
            del submitted, snapshot

        if not futures:
            owner.pulse()
            return load_phase1_handoff(owner.fence, path=owner.path)

        done, _pending = wait(
            tuple(futures), timeout=WORKER_POLL_SECONDS,
            return_when=FIRST_COMPLETED,
        )
        if not done:
            continue
        owner.pulse()
        ordered = sorted(done, key=lambda item: (
            futures[item].ordinal, futures[item].file_id,
        ))
        try:
            for completed in ordered:
                futures.pop(completed)
                _commit_future(context, completed, owner)
        finally:
            # Completed futures retain their private _WorkProduct results. Drop
            # every batch reference before another wave can be submitted.
            ordered.clear()
            done.clear()
            del ordered, done
            try:
                del completed
            except UnboundLocalError:
                pass


def _commit_future(
    context: WorkerRunContext,
    completed: Future[_WorkProduct],
    owner: _FenceOwner,
) -> None:
    product = completed.result()
    _commit_product(context, product, owner)


def _process_file(
    context: WorkerRunContext,
    snapshot: FileResumeSnapshot,
    stop_event: threading.Event,
    dependencies: Phase1Dependencies,
) -> _WorkProduct:
    try:
        with open_inventory_file(
            Path(context.source_root),
            context.root_identity,
            snapshot.inventory_file,
            cancel_check=stop_event.is_set,
        ) as opened:
            result = dependencies.extract(
                opened.fileno(), snapshot.inventory_file, stop_event.is_set,
            )
    except SourceReopenCancelled:
        return _empty_product(snapshot, cancelled=True)
    except SourceReopenError:
        return _empty_product(snapshot, source_changed=True)

    if type(result) is not ExtractionResult:
        raise Phase1Error(Phase1Failure.EXTRACTOR)
    if result.reason == "cancelled":
        return _empty_product(snapshot, cancelled=True)
    if not result.ok:
        return _WorkProduct(snapshot, result, None, (), False, False, ())
    if type(result.text) is not str:
        raise Phase1Error(Phase1Failure.EXTRACTOR)
    evidence = build_extraction_evidence(result)
    if not result.text:
        return _WorkProduct(snapshot, result, evidence, (), False, False, ())
    if stop_event.is_set():
        return _empty_product(snapshot, cancelled=True)
    try:
        hits, overflow = dependencies.detect(
            result.text, MAX_DETECTOR_HITS, stop_event.is_set,
        )
    except DetectorScanCancelled:
        return _empty_product(snapshot, cancelled=True)
    if stop_event.is_set():
        return _empty_product(snapshot, cancelled=True)
    if type(hits) is not list or type(overflow) is not bool:
        raise Phase1Error(Phase1Failure.CONTRACT)
    if any(type(hit) is not DetectorHit for hit in hits):
        raise Phase1Error(Phase1Failure.CONTRACT)
    if overflow and hits:
        raise Phase1Error(Phase1Failure.CONTRACT)
    selected = context.mode == "deep" or bool(hits)
    chunks = ()
    if selected and not overflow:
        try:
            chunks = _bounded_chunks(dependencies.chunk(
                result.text,
                context.chunk_chars,
                context.overlap_chars,
                stop_event.is_set,
            ), cancel_check=stop_event.is_set)
        except DetectorScanCancelled:
            return _empty_product(snapshot, cancelled=True)
        if stop_event.is_set():
            return _empty_product(snapshot, cancelled=True)
    return _WorkProduct(
        snapshot, result, evidence, tuple(hits), overflow, selected, chunks,
    )


def _empty_product(
    snapshot: FileResumeSnapshot,
    *,
    source_changed: bool = False,
    cancelled: bool = False,
) -> _WorkProduct:
    return _WorkProduct(
        snapshot, None, None, (), False, False, (),
        source_changed=source_changed, cancelled=cancelled,
    )


def _bounded_chunks(
    chunks: Iterable[ChunkSpec],
    *,
    cancel_check: Callable[[], bool],
) -> tuple[ChunkSpec, ...]:
    materialized: list[ChunkSpec] = []
    for chunk in chunks:
        if cancel_check():
            raise DetectorScanCancelled
        if type(chunk) is not ChunkSpec:
            raise Phase1Error(Phase1Failure.CONTRACT)
        if len(materialized) >= MAX_CHUNKS_PER_FILE:
            raise Phase1Error(Phase1Failure.CONTRACT)
        materialized.append(chunk)
    if cancel_check():
        raise DetectorScanCancelled
    return tuple(materialized)


def _commit_product(
    context: WorkerRunContext,
    product: _WorkProduct,
    owner: _FenceOwner,
) -> None:
    snapshot = product.snapshot
    if product.cancelled:
        owner.pulse()
        raise Phase1Error(Phase1Failure.EXTRACTOR)
    if product.source_changed:
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id,
            FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY,
            path=owner.path,
        )
        return
    result = product.extraction
    if result is None:
        raise Phase1Error(Phase1Failure.EXTRACTOR)
    if not result.ok:
        _commit_extraction_failure(product, owner)
        return
    _commit_success(context, product, owner)


def _commit_extraction_failure(
    product: _WorkProduct, owner: _FenceOwner,
) -> None:
    snapshot = product.snapshot
    result = product.extraction
    assert result is not None
    if snapshot.stage in {FileStage.TEXT_EXTRACTED, FileStage.DETECTOR_SCANNED}:
        raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
    try:
        terminal = FileTerminal(result.reason)
    except ValueError:
        raise Phase1Error(Phase1Failure.EXTRACTOR) from None
    if terminal is FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY:
        owner.pulse()
        terminalize_file(owner.fence, snapshot.file_id, terminal, path=owner.path)
        return

    direct = {
        FileTerminal.EMPTY,
        FileTerminal.OVERSIZE,
        FileTerminal.SKIPPED_ANALYST_OUTPUT,
        FileTerminal.SKIPPED_KNOWN_BAD,
    }
    parser_failures = {
        FileTerminal.NO_TEXT_LAYER,
        FileTerminal.PARSE_TIMEOUT,
        FileTerminal.PARSE_OOM,
        FileTerminal.PARSE_SIGNAL,
        FileTerminal.PARSE_ERROR,
        FileTerminal.PARSER_OUTPUT_LIMIT,
        FileTerminal.ENCRYPTED,
        FileTerminal.SANDBOX_UNAVAILABLE,
        FileTerminal.SANDBOX_ERROR,
    }
    if terminal in direct:
        if snapshot.stage is not FileStage.DISCOVERED or result.format_name is not None:
            raise Phase1Error(Phase1Failure.EXTRACTOR)
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id, terminal, detail=result.detail,
            path=owner.path,
        )
        return
    if terminal is FileTerminal.UNSUPPORTED_FORMAT:
        if snapshot.stage is FileStage.DISCOVERED:
            if result.format_name in {"ooxml", "legacy_office"}:
                owner.pulse()
                advance_file_stage(
                    owner.fence, snapshot.file_id, FileStage.FORMAT_IDENTIFIED,
                    format_name=result.format_name, path=owner.path,
                )
            elif result.format_name is not None:
                owner.pulse()
                terminalize_file(
                    owner.fence, snapshot.file_id, terminal, detail=result.detail,
                    path=owner.path,
                )
                return
        elif not _format_compatible(snapshot.format_name, result.format_name):
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id, terminal, detail=result.detail,
            path=owner.path,
        )
        return
    if terminal not in parser_failures or result.format_name is None:
        raise Phase1Error(Phase1Failure.EXTRACTOR)
    if snapshot.stage is FileStage.DISCOVERED:
        owner.pulse()
        advance_file_stage(
            owner.fence, snapshot.file_id, FileStage.FORMAT_IDENTIFIED,
            format_name=result.format_name, path=owner.path,
        )
    elif not _format_compatible(snapshot.format_name, result.format_name):
        raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
    owner.pulse()
    terminalize_file(
        owner.fence, snapshot.file_id, terminal, detail=result.detail,
        path=owner.path,
    )


def _commit_success(
    context: WorkerRunContext,
    product: _WorkProduct,
    owner: _FenceOwner,
) -> None:
    snapshot = product.snapshot
    result = product.extraction
    evidence = product.evidence
    assert result is not None and evidence is not None and result.text is not None
    if result.format_name not in {
        "text", "rtf", "pdf", "docx", "xlsx", "pptx", "doc", "xls",
    }:
        raise Phase1Error(Phase1Failure.EXTRACTOR)

    if snapshot.stage is FileStage.DISCOVERED:
        owner.pulse()
        advance_file_stage(
            owner.fence, snapshot.file_id, FileStage.FORMAT_IDENTIFIED,
            format_name=result.format_name, path=owner.path,
        )
        owner.pulse()
        _checkpoint_text(owner, snapshot.file_id, result, evidence)
    elif snapshot.stage is FileStage.FORMAT_IDENTIFIED:
        if not _format_compatible(snapshot.format_name, result.format_name):
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
        owner.pulse()
        _checkpoint_text(owner, snapshot.file_id, result, evidence)
    else:
        owner.pulse()
        try:
            verify_extraction_evidence(
                owner.fence, snapshot.file_id, evidence,
                authenticated_format_name=result.format_name, path=owner.path,
            )
        except Phase1EvidenceMismatch:
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH) from None

    if not result.text:
        if snapshot.stage is FileStage.DETECTOR_SCANNED:
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id,
            FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT,
            path=owner.path,
        )
        return
    if product.detector_overflow:
        if snapshot.stage is FileStage.DETECTOR_SCANNED:
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH)
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id, FileTerminal.DETECTOR_OUTPUT_LIMIT,
            detail="detector_hit_limit", path=owner.path,
        )
        return

    if snapshot.stage is FileStage.DETECTOR_SCANNED:
        owner.pulse()
        try:
            verify_detector_checkpoint(
                owner.fence, snapshot.file_id, product.hits,
                selected_for_model=product.selected_for_model, path=owner.path,
            )
        except Phase1EvidenceMismatch:
            raise Phase1Error(Phase1Failure.RESUME_MISMATCH) from None
    else:
        owner.pulse()
        checkpoint_detector(
            owner.fence, snapshot.file_id, product.hits,
            selected_for_model=product.selected_for_model, path=owner.path,
        )
    if not product.selected_for_model:
        owner.pulse()
        terminalize_file(
            owner.fence, snapshot.file_id,
            FileTerminal.COMPLETE_DETECTOR_ONLY, path=owner.path,
        )
        return
    owner.pulse()
    handoff_selected_file(
        owner.fence, snapshot.file_id, product.chunks, path=owner.path,
    )


def _checkpoint_text(
    owner: _FenceOwner,
    file_id: int,
    result: ExtractionResult,
    evidence: ExtractionCheckpointEvidence,
) -> None:
    advance_file_stage(
        owner.fence, file_id, FileStage.TEXT_EXTRACTED,
        authenticated_format_name=result.format_name,
        encoding=evidence.encoding,
        parser_identity=evidence.parser_identity,
        extraction_meta=evidence.extraction_counts,
        provenance=evidence.provenance,
        path=owner.path,
    )


def _format_compatible(stored: str | None, observed: str | None) -> bool:
    if stored == observed and stored is not None:
        return True
    families = {
        "ooxml": {"docx", "xlsx", "pptx", "ooxml"},
        "legacy_office": {"doc", "xls", "legacy_office"},
    }
    return stored in families and observed in families[stored]


def _close_failure(
    owner: _FenceOwner,
    failure: tuple[str, Phase1Failure],
) -> Phase1Handoff:
    kind, code = failure
    try:
        if kind == "cancelled":
            acknowledge_cancel(owner.fence, path=owner.path)
            raise Phase1Cancelled(code)
        pulse = pulse_worker(
            owner.fence,
            heartbeat_monotonic_ns=max(
                owner.dependencies.monotonic_ns(),
                owner.fence.heartbeat_monotonic_ns + 1,
            ),
            path=owner.path,
        )
        owner.fence = pulse.fence
        if pulse.cancel_requested:
            acknowledge_cancel(owner.fence, path=owner.path)
            raise Phase1Cancelled(Phase1Failure.STATE)
        target = release_worker(owner.fence, path=owner.path)
        if target is RunState.CANCELLED_PENDING_RESUME:
            raise Phase1Cancelled(Phase1Failure.STATE)
    except (Phase1Cancelled, Phase1Interrupted, Phase1Error):
        raise
    except Exception:
        raise Phase1Error(Phase1Failure.LEASE) from None
    if kind == "interrupted":
        raise Phase1Interrupted(code)
    raise Phase1Error(code)


__all__ = [
    "Phase1Cancelled",
    "Phase1Dependencies",
    "Phase1Error",
    "Phase1Failure",
    "Phase1Interrupted",
    "run_phase1",
]
