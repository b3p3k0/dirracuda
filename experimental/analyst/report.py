"""Heartbeat-safe streaming report publication and run finalization."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

from .checkpoint import CheckpointError, begin_finalization, finish_finalization
from .lease import LeaseError, LeaseFence, pulse_worker, release_worker
from .phase2_contract import Phase2Handoff
from .report_contract import (
    FindingReportRow,
    InventoryReportRow,
    READ_PAGE_ROWS,
    ReportFinalizationResult,
    ReportManifest,
)
from .report_state import (
    ReportStateError,
    load_detector_finding_page,
    load_inventory_page,
    load_model_finding_page,
    load_report_snapshot,
)
from .report_writer import (
    ReportWriteError,
    inspect_report_manifest,
    publish_report,
)
from .store import open_connection
from .worker_contract import WorkerContractError, validate_worker_run_id


HEARTBEAT_INTERVAL_NS = 2_000_000_000


class ReportFailure(str, Enum):
    CONTRACT = "contract"
    STATE = "state"
    OUTPUT = "output"
    LEASE = "lease"
    INTERNAL = "internal"


class ReportFinalizationError(RuntimeError):
    """A content-free closed C12 failure."""

    def __init__(self, code: ReportFailure) -> None:
        if type(code) is not ReportFailure:
            raise TypeError("report failure code must use the closed enum")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ReportDependencies:
    """Injectable time/token/writer seams; none may read document content."""

    monotonic_ns: Callable[[], int] = field(default=time.monotonic_ns, repr=False)
    token_factory: Callable[[], str] = field(
        default=lambda: secrets.token_hex(32), repr=False,
    )
    publisher: Callable[..., Any] = field(default=publish_report, repr=False)

    def __post_init__(self) -> None:
        if not all(callable(value) for value in (
            self.monotonic_ns, self.token_factory, self.publisher,
        )):
            raise TypeError("report dependencies are invalid")


@dataclass(slots=True)
class _ReportOwner:
    fence: LeaseFence
    finalization_token: str
    path: Path | None
    dependencies: ReportDependencies
    last_pulse_clock_ns: int

    def progress(self, *, force: bool = False) -> None:
        observed = self.dependencies.monotonic_ns()
        if type(observed) is not int or observed < 0:
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        if observed < self.last_pulse_clock_ns:
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        if not force and observed - self.last_pulse_clock_ns < HEARTBEAT_INTERVAL_NS:
            return
        heartbeat = max(observed, self.fence.heartbeat_monotonic_ns + 1)
        pulse = pulse_worker(
            self.fence, heartbeat_monotonic_ns=heartbeat, path=self.path,
        )
        if pulse.cancel_requested:
            raise ReportFinalizationError(ReportFailure.STATE)
        self.fence = pulse.fence
        self.last_pulse_clock_ns = observed

    def inventory_pages(self) -> Iterator[tuple[InventoryReportRow, ...]]:
        cursor = -1
        while True:
            page = load_inventory_page(
                self.fence, self.finalization_token, after_ordinal=cursor,
                limit=READ_PAGE_ROWS, path=self.path,
            )
            if not page:
                return
            yield page
            cursor = page[-1].ordinal
            self.progress()

    def finding_pages(self) -> Iterator[tuple[FindingReportRow, ...]]:
        detector_cursor = 0
        while True:
            page = load_detector_finding_page(
                self.fence, self.finalization_token, after_id=detector_cursor,
                limit=READ_PAGE_ROWS, path=self.path,
            )
            if not page:
                break
            yield tuple(item[1] for item in page)
            detector_cursor = page[-1][0]
            self.progress()
        model_cursor = 0
        while True:
            page = load_model_finding_page(
                self.fence, self.finalization_token, after_id=model_cursor,
                limit=READ_PAGE_ROWS, path=self.path,
            )
            if not page:
                return
            yield tuple(item[1] for item in page)
            model_cursor = page[-1][0]
            self.progress()


def finalize_report(
    handoff: Phase2Handoff,
    *,
    path: Path | None = None,
    dependencies: ReportDependencies | None = None,
) -> ReportFinalizationResult:
    """Publish the final report, commit its manifest, and clear the exact lease."""
    if type(handoff) is not Phase2Handoff:
        raise TypeError("report finalization requires a Phase2Handoff")
    chosen = ReportDependencies() if dependencies is None else dependencies
    if type(chosen) is not ReportDependencies:
        raise TypeError("dependencies must be ReportDependencies")
    fence = handoff.fence
    token: str
    owner: _ReportOwner | None = None
    try:
        token = chosen.token_factory()
        if type(token) is not str:
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        begin_finalization(fence, token, path=path)
    except ReportFinalizationError:
        _best_effort_release(fence, path)
        raise
    except CheckpointError:
        _best_effort_release(fence, path)
        raise ReportFinalizationError(ReportFailure.STATE) from None
    except (TypeError, ValueError):
        _best_effort_release(fence, path)
        raise ReportFinalizationError(ReportFailure.CONTRACT) from None
    except BaseException:
        _best_effort_release(fence, path)
        raise ReportFinalizationError(ReportFailure.INTERNAL) from None

    try:
        observed = chosen.monotonic_ns()
        if type(observed) is not int or observed < 0:
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        owner = _ReportOwner(fence, token, path, chosen, observed)
        owner.progress(force=True)
        snapshot = load_report_snapshot(owner.fence, token, path=path)
        coverage = snapshot.coverage
        if (
            snapshot.run.run_id != handoff.fence.run_id
            or coverage.model_reviewed_files != handoff.reviewed_file_count
            or coverage.valid_model_chunks != handoff.valid_chunk_count
            or coverage.retained_model_findings != handoff.retained_finding_count
        ):
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        manifest = chosen.publisher(
            snapshot,
            inventory_pages=owner.inventory_pages,
            finding_pages=owner.finding_pages,
            progress=owner.progress,
        )
        if type(manifest) is not ReportManifest:
            raise ReportFinalizationError(ReportFailure.CONTRACT)
        owner.progress(force=True)
        result = ReportFinalizationResult(snapshot.run.run_id, manifest)
        finish_finalization(
            owner.fence, token, manifest.sha256, path=path,
        )
        return result
    except ReportFinalizationError:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise
    except LeaseError:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.LEASE) from None
    except CheckpointError:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.STATE) from None
    except ReportStateError:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.STATE) from None
    except ReportWriteError:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.OUTPUT) from None
    except (TypeError, ValueError):
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.CONTRACT) from None
    except BaseException:
        _best_effort_release(owner.fence if owner is not None else fence, path)
        raise ReportFinalizationError(ReportFailure.INTERNAL) from None


def verify_completed_report(
    run_id: str, *, path: Path | None = None,
) -> ReportManifest:
    """Fail closed unless a complete run's fixed artifacts match its manifest SHA."""
    try:
        canonical_run_id = validate_worker_run_id(run_id)
    except (TypeError, WorkerContractError):
        raise ReportFinalizationError(ReportFailure.CONTRACT) from None
    try:
        conn = open_connection(path, read_only=True)
        try:
            row = conn.execute(
                "SELECT state,output_root,report_manifest_sha256 FROM analyst_runs "
                "WHERE run_id=?", (canonical_run_id,),
            ).fetchone()
            if (
                row is None
                or str(row["state"]) != "complete"
                or type(row["output_root"]) is not str
                or type(row["report_manifest_sha256"]) is not str
            ):
                raise ReportFinalizationError(ReportFailure.STATE)
            output_root = Path(str(row["output_root"]))
            expected_sha256 = str(row["report_manifest_sha256"])
        finally:
            conn.close()
        manifest = inspect_report_manifest(output_root)
        if manifest.sha256 != expected_sha256:
            raise ReportFinalizationError(ReportFailure.OUTPUT)
        return manifest
    except ReportFinalizationError:
        raise
    except ReportWriteError:
        raise ReportFinalizationError(ReportFailure.OUTPUT) from None
    except BaseException:
        raise ReportFinalizationError(ReportFailure.STATE) from None


def _best_effort_release(fence: LeaseFence, path: Path | None) -> None:
    try:
        release_worker(fence, path=path)
    except BaseException:
        pass


__all__ = [
    "HEARTBEAT_INTERVAL_NS",
    "ReportDependencies",
    "ReportFailure",
    "ReportFinalizationError",
    "finalize_report",
    "verify_completed_report",
]
