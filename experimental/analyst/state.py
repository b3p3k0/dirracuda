"""Pure lifecycle contracts for durable Analyst runs and files."""

from __future__ import annotations

from enum import Enum

from .models import FileStage, FileTerminal, ResumableState


class RunState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED_PENDING_RESUME = "cancelled_pending_resume"
    INTERRUPTED = "interrupted"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    ABANDONED = "abandoned"


class FileWorkState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CANCELLED_PENDING_RESUME = "cancelled_pending_resume"
    TERMINAL = "terminal"


class ChunkState(str, Enum):
    PENDING = "pending"
    MODEL_RESPONSE_VALID = "model_response_valid"
    MODEL_INVALID = "model_invalid"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_TRANSPORT_ERROR = "model_transport_error"


class AttemptState(str, Enum):
    DISPATCHING = "dispatching"
    VALID = "valid"
    SCHEMA_INVALID = "schema_invalid"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_TRANSPORT_ERROR = "model_transport_error"
    ORPHANED_UNKNOWN = "orphaned_unknown"
    CANCELLED_UNVERIFIED = "cancelled_unverified"


TERMINAL_RUN_STATES = frozenset({RunState.COMPLETE, RunState.ABANDONED})
RESUMABLE_RUN_STATES = frozenset(
    {
        RunState.READY,
        RunState.CANCELLED_PENDING_RESUME,
        RunState.INTERRUPTED,
    }
)
TERMINAL_ATTEMPT_STATES = frozenset(set(AttemptState) - {AttemptState.DISPATCHING})

_RUN_TRANSITIONS = {
    RunState.READY: frozenset({RunState.RUNNING, RunState.ABANDONED}),
    RunState.RUNNING: frozenset(
        {RunState.CANCEL_REQUESTED, RunState.FINALIZING, RunState.INTERRUPTED}
    ),
    RunState.CANCEL_REQUESTED: frozenset(
        {RunState.CANCELLED_PENDING_RESUME, RunState.INTERRUPTED}
    ),
    RunState.CANCELLED_PENDING_RESUME: frozenset(
        {RunState.RUNNING, RunState.ABANDONED}
    ),
    RunState.INTERRUPTED: frozenset({RunState.RUNNING, RunState.ABANDONED}),
    RunState.FINALIZING: frozenset({RunState.COMPLETE, RunState.INTERRUPTED}),
    RunState.COMPLETE: frozenset(),
    RunState.ABANDONED: frozenset(),
}

_FILE_TRANSITIONS = {
    FileWorkState.PENDING: frozenset(
        {
            FileWorkState.ACTIVE,
            FileWorkState.CANCELLED_PENDING_RESUME,
            FileWorkState.TERMINAL,
        }
    ),
    FileWorkState.ACTIVE: frozenset(
        {
            FileWorkState.PENDING,
            FileWorkState.CANCELLED_PENDING_RESUME,
            FileWorkState.TERMINAL,
        }
    ),
    FileWorkState.CANCELLED_PENDING_RESUME: frozenset(
        {FileWorkState.PENDING, FileWorkState.TERMINAL}
    ),
    FileWorkState.TERMINAL: frozenset(),
}

_STAGE_ORDER = {stage: index for index, stage in enumerate(FileStage)}


def require_run_transition(current: RunState | str, target: RunState | str) -> None:
    """Reject every run-state edge not frozen by C8."""
    source = _run_state(current)
    destination = _run_state(target)
    if destination not in _RUN_TRANSITIONS[source]:
        raise ValueError(f"invalid Analyst run transition: {source.value}->{destination.value}")


def require_file_transition(
    current: FileWorkState | str, target: FileWorkState | str,
) -> None:
    """Reject reopening a terminal or any other invalid file-state edge."""
    source = _file_state(current)
    destination = _file_state(target)
    if destination not in _FILE_TRANSITIONS[source]:
        raise ValueError(
            f"invalid Analyst file transition: {source.value}->{destination.value}"
        )


def require_stage_advance(
    current: FileStage | str, target: FileStage | str,
) -> None:
    """Require a strictly forward, single-step durable stage checkpoint."""
    source = _file_stage(current)
    destination = _file_stage(target)
    if _STAGE_ORDER[destination] != _STAGE_ORDER[source] + 1:
        raise ValueError(
            f"invalid Analyst stage transition: {source.value}->{destination.value}"
        )


def validate_file_state(
    *,
    work_state: FileWorkState | str,
    terminal: FileTerminal | str | None,
    resumable_state: ResumableState | str | None,
) -> None:
    """Validate the durable terminal/resumable nullability invariant."""
    state = _file_state(work_state)
    terminal_value = None if terminal is None else FileTerminal(terminal)
    resumable = None if resumable_state is None else ResumableState(resumable_state)
    if state is FileWorkState.TERMINAL:
        valid = terminal_value is not None and resumable is None
    elif state is FileWorkState.CANCELLED_PENDING_RESUME:
        valid = (
            terminal_value is None
            and resumable is ResumableState.CANCELLED_PENDING_RESUME
        )
    else:
        valid = terminal_value is None and resumable is None
    if not valid:
        raise ValueError("Analyst file terminal/resumable state is inconsistent")


def _run_state(value: RunState | str) -> RunState:
    return value if isinstance(value, RunState) else RunState(value)


def _file_state(value: FileWorkState | str) -> FileWorkState:
    return value if isinstance(value, FileWorkState) else FileWorkState(value)


def _file_stage(value: FileStage | str) -> FileStage:
    return value if isinstance(value, FileStage) else FileStage(value)
