"""Same-process Phase 1, Phase 2, and report worker boundary for Analyst."""

from __future__ import annotations

import secrets
import signal
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence, TYPE_CHECKING

from .worker_contract import (
    WorkerContractError,
    WorkerOutcome,
    validate_worker_run_id,
)

if TYPE_CHECKING:
    from .phase1 import Phase1Dependencies
    from .phase2 import Phase2Dependencies
    from .report import ReportDependencies
    from .worker_contract import Phase1Handoff, WorkerRunContext
    from .worker_preflight import WorkerPreflightResult


EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_HELD_OR_BUSY = 3
EXIT_INTERRUPTED = 130

_USAGE = "usage: python -m experimental.analyst.worker --run-id RUN_ID\n"
_INVALID = '{"outcome":"invalid_invocation"}\n'

PreflightFn = Callable[
    ["WorkerRunContext", Callable[[], bool]], "WorkerPreflightResult"
]


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    """A closed shell outcome with a hidden handoff only at the C10 seam."""

    outcome: WorkerOutcome
    handoff: "Phase1Handoff | None" = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.outcome) is not WorkerOutcome:
            raise ValueError("worker result requires a closed outcome")
        from .worker_contract import Phase1Handoff

        if self.outcome is WorkerOutcome.PHASE1_HANDOFF:
            if type(self.handoff) is not Phase1Handoff:
                raise ValueError("successful worker result requires a handoff")
        elif self.handoff is not None:
            raise ValueError("failed worker result cannot retain a handoff")


def run_phase1_worker(
    run_id: str,
    stop_event: threading.Event,
    *,
    path: Path | None = None,
    dependencies: "Phase1Dependencies | None" = None,
    preflight: PreflightFn | None = None,
) -> WorkerRunResult:
    """Preflight, claim, and run Phase 1 while retaining a success lease."""
    try:
        canonical_run_id = validate_worker_run_id(run_id)
    except WorkerContractError:
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    if not isinstance(stop_event, threading.Event):
        raise TypeError("worker stop_event must be a threading.Event")
    if stop_event.is_set():
        return WorkerRunResult(WorkerOutcome.INTERRUPTED)

    # Runtime imports remain behind the pure invocation gate. No optional native
    # parser package is imported in the durable worker process.
    from .lease import LeaseError, claim_worker, current_lease
    from .phase1 import (
        Phase1Cancelled,
        Phase1Error,
        Phase1Interrupted,
        Phase1Dependencies,
        run_phase1,
    )
    from .process_identity import (
        current_process_identity,
    )
    from .state import RESUMABLE_RUN_STATES
    from .store import AnalystStoreError, ForkRequired, load_worker_run
    from .worker_preflight import (
        WorkerPreflightResult,
        WorkerPreflightStatus,
        preflight_worker,
    )

    if dependencies is not None and type(dependencies) is not Phase1Dependencies:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    try:
        context = load_worker_run(canonical_run_id, path=path)
    except (AnalystStoreError, ForkRequired, WorkerContractError, ValueError):
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    except Exception:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    if context.observed_state not in RESUMABLE_RUN_STATES:
        try:
            observed_lease = current_lease(path=path)
        except Exception:
            return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
        if observed_lease is not None and observed_lease.run_id == canonical_run_id:
            return WorkerRunResult(WorkerOutcome.LEASE_BUSY)
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    chosen_preflight = preflight_worker if preflight is None else preflight
    if not callable(chosen_preflight):
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    try:
        capability = chosen_preflight(context, stop_event.is_set)
    except Exception:
        return WorkerRunResult(WorkerOutcome.PREFLIGHT_FAILED)
    if type(capability) is not WorkerPreflightResult:
        return WorkerRunResult(WorkerOutcome.PREFLIGHT_FAILED)
    if capability.status is WorkerPreflightStatus.CANCELLED or stop_event.is_set():
        return WorkerRunResult(WorkerOutcome.INTERRUPTED)
    if capability.status in {
        WorkerPreflightStatus.RUN_STATE,
        WorkerPreflightStatus.PARSER_DRIFT,
    }:
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    if not capability.ok:
        return WorkerRunResult(WorkerOutcome.PREFLIGHT_FAILED)

    try:
        process = current_process_identity()
    except Exception:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    if stop_event.is_set():
        return WorkerRunResult(WorkerOutcome.INTERRUPTED)
    try:
        fence = claim_worker(
            canonical_run_id,
            process,
            owner_token=secrets.token_hex(32),
            heartbeat_monotonic_ns=time.monotonic_ns(),
            path=path,
        )
    except LeaseError:
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    except Exception:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    if fence is None:
        return WorkerRunResult(WorkerOutcome.LEASE_BUSY)
    try:
        handoff = run_phase1(
            context,
            fence,
            stop_event,
            path=path,
            dependencies=dependencies,
        )
    except Phase1Cancelled:
        return WorkerRunResult(WorkerOutcome.CANCELLED)
    except Phase1Interrupted:
        return WorkerRunResult(WorkerOutcome.INTERRUPTED)
    except Phase1Error:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    except Exception:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    return WorkerRunResult(WorkerOutcome.PHASE1_HANDOFF, handoff)


def run_worker(
    run_id: str,
    stop_event: threading.Event,
    *,
    path: Path | None = None,
    phase1_dependencies: "Phase1Dependencies | None" = None,
    phase2_dependencies: "Phase2Dependencies | None" = None,
    report_dependencies: "ReportDependencies | None" = None,
    preflight: PreflightFn | None = None,
) -> WorkerRunResult:
    """Run the complete worker pipeline without releasing either phase handoff."""
    try:
        validate_worker_run_id(run_id)
    except WorkerContractError:
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    if not isinstance(stop_event, threading.Event):
        raise TypeError("worker stop_event must be a threading.Event")

    from .phase1 import Phase1Dependencies
    from .phase2 import (
        Phase2Cancelled,
        Phase2Dependencies,
        Phase2Error,
        Phase2Interrupted,
        Phase2PausedResource,
        run_phase2,
    )
    from .report import (
        ReportDependencies,
        ReportFinalizationError,
        finalize_report,
    )
    from .store import AnalystStoreError, load_worker_run

    if (
        phase1_dependencies is not None
        and type(phase1_dependencies) is not Phase1Dependencies
        or phase2_dependencies is not None
        and type(phase2_dependencies) is not Phase2Dependencies
        or report_dependencies is not None
        and type(report_dependencies) is not ReportDependencies
    ):
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    phase1_result = run_phase1_worker(
        run_id,
        stop_event,
        path=path,
        dependencies=phase1_dependencies,
        preflight=preflight,
    )
    if phase1_result.outcome is not WorkerOutcome.PHASE1_HANDOFF:
        return phase1_result
    assert phase1_result.handoff is not None
    phase1_handoff = phase1_result.handoff
    try:
        context = load_worker_run(run_id, path=path)
    except (AnalystStoreError, ValueError):
        _release_handoff(phase1_handoff.fence, path)
        return WorkerRunResult(WorkerOutcome.RUN_INVALID)
    except Exception:
        _release_handoff(phase1_handoff.fence, path)
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    try:
        phase2_handoff = run_phase2(
            context,
            phase1_handoff,
            stop_event,
            path=path,
            dependencies=phase2_dependencies,
        )
    except Phase2Cancelled:
        return WorkerRunResult(WorkerOutcome.CANCELLED)
    except Phase2Interrupted:
        return WorkerRunResult(WorkerOutcome.INTERRUPTED)
    except Phase2PausedResource:
        return WorkerRunResult(WorkerOutcome.PAUSED_RESOURCE)
    except Phase2Error:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    except Exception:
        _release_handoff(phase1_handoff.fence, path)
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    try:
        finalize_report(
            phase2_handoff, path=path, dependencies=report_dependencies,
        )
    except ReportFinalizationError:
        return WorkerRunResult(WorkerOutcome.REPORT_FAILED)
    except Exception:
        return WorkerRunResult(WorkerOutcome.INTERNAL_ERROR)
    return WorkerRunResult(WorkerOutcome.COMPLETE)


def _release_handoff(fence: object, path: Path | None) -> None:
    try:
        from .lease import LeaseFence, release_worker

        if type(fence) is LeaseFence:
            release_worker(fence, path=path)
    except BaseException:
        pass


@contextmanager
def worker_signal_handlers(stop_event: threading.Event) -> Iterator[None]:
    """Install Event-only SIGINT/SIGTERM handlers on the main thread."""
    if not isinstance(stop_event, threading.Event):
        raise TypeError("worker stop_event must be a threading.Event")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("worker signal handlers require the main thread")

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    previous = {
        number: signal.getsignal(number)
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for number in previous:
            signal.signal(number, request_stop)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the exact worker CLI and emit one fixed content-free outcome."""
    source = sys.argv[1:] if argv is None else argv
    try:
        arguments = list(source)
    except (TypeError, ValueError):
        sys.stderr.write(_INVALID)
        return EXIT_USAGE
    if any(type(item) is not str for item in arguments):
        sys.stderr.write(_INVALID)
        return EXIT_USAGE
    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(_USAGE)
        return EXIT_SUCCESS
    if len(arguments) != 2 or arguments[0] != "--run-id":
        sys.stderr.write(_INVALID)
        return EXIT_USAGE
    try:
        validate_worker_run_id(arguments[1])
    except WorkerContractError:
        sys.stderr.write(_INVALID)
        return EXIT_USAGE
    stop_event = threading.Event()
    with worker_signal_handlers(stop_event):
        result = run_worker(arguments[1], stop_event)
    sys.stdout.write('{"outcome":"' + result.outcome.value + '"}\n')
    if result.outcome is WorkerOutcome.COMPLETE:
        return EXIT_SUCCESS
    if result.outcome in {WorkerOutcome.LEASE_BUSY, WorkerOutcome.PAUSED_RESOURCE}:
        return EXIT_HELD_OR_BUSY
    if result.outcome in {WorkerOutcome.CANCELLED, WorkerOutcome.INTERRUPTED}:
        return EXIT_INTERRUPTED
    return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_FAILURE",
    "EXIT_HELD_OR_BUSY",
    "EXIT_INTERRUPTED",
    "EXIT_SUCCESS",
    "EXIT_USAGE",
    "WorkerRunResult",
    "main",
    "run_phase1_worker",
    "run_worker",
    "worker_signal_handlers",
]
