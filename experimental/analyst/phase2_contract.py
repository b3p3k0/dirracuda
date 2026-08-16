"""Pure content-free contracts for serial Analyst Phase 2 work."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, TYPE_CHECKING

from .ollama_contract import (
    ChatRequest,
    PromptKind,
    build_chat_request,
)
from .inventory import InventoryFile
from .models import FileStage, FileTerminal
from .state import AttemptState, ChunkState
from .worker_contract import Phase1ChunkIdentity, validate_worker_run_id

if TYPE_CHECKING:
    from .lease import LeaseFence


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NONCE_ATTEMPTS: Final = 65_536

HEALTH_SOURCE: Final = (
    "Public synthetic health note: library books are arranged by topic."
)
HEALTH_NONCE: Final = "FENCE_C11A11CE00000001"
CANCELLATION_HEALTH_DELAY_SECONDS: Final = 2.0
HEALTH_REQUEST_SHA256: Final = (
    "f74a63463c1b6d14832efc8dc2a213130d1fcda2078e79b64d02d80acd995916"
)


class Phase2ContractError(ValueError):
    """A caller supplied a value outside the frozen C11 contract."""


class Phase2Outcome(str, Enum):
    HANDOFF = "phase2_handoff"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    PAUSED_RESOURCE = "paused_resource"


@dataclass(frozen=True, slots=True)
class Phase2AttemptIdentity:
    attempt_id: str
    attempt_no: int
    request_sha256: str
    state: AttemptState

    def __post_init__(self) -> None:
        if (
            type(self.attempt_id) is not str
            or _SHA256.fullmatch(self.attempt_id) is None
            or type(self.attempt_no) is not int
            or self.attempt_no not in {1, 2}
            or type(self.request_sha256) is not str
            or _SHA256.fullmatch(self.request_sha256) is None
            or type(self.state) is not AttemptState
        ):
            raise Phase2ContractError("Phase 2 attempt identity is invalid")


@dataclass(frozen=True, slots=True)
class Phase2ChunkSnapshot:
    identity: Phase1ChunkIdentity
    state: ChunkState
    attempts: tuple[Phase2AttemptIdentity, ...]

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not Phase1ChunkIdentity
            or type(self.state) is not ChunkState
            or type(self.attempts) is not tuple
            or any(type(item) is not Phase2AttemptIdentity for item in self.attempts)
            or tuple(item.attempt_no for item in self.attempts)
            != tuple(range(1, len(self.attempts) + 1))
            or len(self.attempts) > 2
        ):
            raise Phase2ContractError("Phase 2 chunk state is invalid")


@dataclass(frozen=True, slots=True)
class Phase2FileSnapshot:
    file_id: int
    ordinal: int
    inventory_file: InventoryFile = field(repr=False)
    stage: FileStage
    format_name: str
    parser_identity_json: str = field(repr=False)
    extraction_meta_json: str = field(repr=False)
    chunks: tuple[Phase2ChunkSnapshot, ...]

    def __post_init__(self) -> None:
        if (
            type(self.file_id) is not int
            or self.file_id <= 0
            or type(self.ordinal) is not int
            or self.ordinal < 0
            or type(self.inventory_file) is not InventoryFile
            or not _valid_inventory(self.inventory_file)
            or type(self.stage) is not FileStage
            or self.stage not in {
                FileStage.SELECTED_FOR_MODEL,
                FileStage.MODEL_REVIEWED,
                FileStage.MODEL_RESPONSE_VALID,
            }
            or type(self.format_name) is not str
            or self.format_name not in {
                "text", "rtf", "pdf", "docx", "xlsx", "pptx", "doc", "xls",
            }
            or type(self.parser_identity_json) is not str
            or not self.parser_identity_json
            or type(self.extraction_meta_json) is not str
            or not self.extraction_meta_json
            or type(self.chunks) is not tuple
            or not self.chunks
            or any(type(item) is not Phase2ChunkSnapshot for item in self.chunks)
            or tuple(item.identity.index for item in self.chunks)
            != tuple(range(len(self.chunks)))
        ):
            raise Phase2ContractError("Phase 2 file snapshot is invalid")


def _valid_inventory(value: InventoryFile) -> bool:
    relative = value.relative_path
    parts = relative.split("/") if type(relative) is str else ()
    return (
        type(relative) is str
        and bool(relative)
        and not relative.startswith("/")
        and "\\" not in relative
        and "\x00" not in relative
        and all(part not in {"", ".", ".."} for part in parts)
        and all(
            type(item) is int and item >= 0
            for item in (
                value.size, value.mtime_ns, value.ctime_ns,
                value.device, value.inode, value.mode,
            )
        )
        and value.inode > 0
        and value.mode <= 0o7777
        and type(value.sha256) is str
        and _SHA256.fullmatch(value.sha256) is not None
    )


@dataclass(frozen=True, slots=True)
class HealthObligation:
    """Latest ambiguous scored contact not followed by a public health answer."""

    source_contact_id: str
    source_contact_no: int
    source_status: str

    def __post_init__(self) -> None:
        if (
            type(self.source_contact_id) is not str
            or _SHA256.fullmatch(self.source_contact_id) is None
            or type(self.source_contact_no) is not int
            or self.source_contact_no <= 0
            or type(self.source_status) is not str
            or self.source_status not in {
                "request_timeout", "transport_unavailable",
                "cancelled_unverified", "orphaned_unknown",
            }
        ):
            raise Phase2ContractError("health obligation is invalid")


@dataclass(frozen=True, slots=True)
class Phase2FileCompletion:
    file_id: int
    terminal: FileTerminal
    valid_chunk_count: int
    retained_finding_count: int

    def __post_init__(self) -> None:
        if (
            type(self.file_id) is not int
            or self.file_id <= 0
            or type(self.terminal) is not FileTerminal
            or type(self.valid_chunk_count) is not int
            or self.valid_chunk_count < 0
            or type(self.retained_finding_count) is not int
            or self.retained_finding_count < 0
            or (
                self.terminal is FileTerminal.COMPLETE_MODEL_REVIEWED
                and self.valid_chunk_count == 0
            )
            or (
                self.terminal is not FileTerminal.COMPLETE_MODEL_REVIEWED
                and (self.valid_chunk_count != 0 or self.retained_finding_count != 0)
            )
        ):
            raise Phase2ContractError("Phase 2 file completion is invalid")


@dataclass(frozen=True, slots=True)
class Phase2Handoff:
    """Content-free C11 result retaining the exact live fence for C12."""

    fence: LeaseFence = field(repr=False)
    reviewed_file_count: int
    valid_chunk_count: int
    retained_finding_count: int

    def __post_init__(self) -> None:
        from .lease import LeaseFence

        if type(self.fence) is not LeaseFence or any(
            type(value) is not int or value < 0
            for value in (
                self.reviewed_file_count,
                self.valid_chunk_count,
                self.retained_finding_count,
            )
        ) or (
            self.reviewed_file_count == 0
            and (self.valid_chunk_count != 0 or self.retained_finding_count != 0)
        ) or (
            self.retained_finding_count > 0 and self.valid_chunk_count == 0
        ):
            raise Phase2ContractError("Phase 2 handoff fields are invalid")


def derive_nonce(
    run_id: str,
    chunk_id: int,
    chunk_sha256: str,
    prompt_kind: PromptKind,
    source_text: str,
) -> str:
    """Derive crash-reconstructible prompt fencing without persisting source text."""
    try:
        normalized_run_id = validate_worker_run_id(run_id)
    except (TypeError, ValueError) as exc:
        raise Phase2ContractError("run id is invalid") from exc
    if (
        type(chunk_id) is not int
        or chunk_id <= 0
        or type(chunk_sha256) is not str
        or _SHA256.fullmatch(chunk_sha256) is None
        or type(prompt_kind) is not PromptKind
        or type(source_text) is not str
    ):
        raise Phase2ContractError("nonce inputs are invalid")
    for counter in range(_NONCE_ATTEMPTS):
        preimage = (
            f"dirracuda-analyst-c11-nonce-v1\0{normalized_run_id}\0{chunk_id}\0"
            f"{chunk_sha256}\0{prompt_kind.value}\0{counter}"
        ).encode("ascii")
        nonce = f"FENCE_{hashlib.sha256(preimage).hexdigest()[:16].upper()}"
        if nonce not in source_text:
            return nonce
    raise Phase2ContractError("source exhausted the bounded nonce derivation")


def build_health_chat_request() -> ChatRequest:
    """Build the exact public generation used after uncertain cancellation/delivery."""
    request = build_chat_request(HEALTH_SOURCE, nonce=HEALTH_NONCE)
    if request.request_sha256 != HEALTH_REQUEST_SHA256:
        raise RuntimeError("C11 public health request drifted")
    return request


__all__ = [
    "HEALTH_NONCE",
    "CANCELLATION_HEALTH_DELAY_SECONDS",
    "HEALTH_REQUEST_SHA256",
    "HEALTH_SOURCE",
    "Phase2ContractError",
    "Phase2AttemptIdentity",
    "Phase2ChunkSnapshot",
    "Phase2FileSnapshot",
    "Phase2FileCompletion",
    "Phase2Handoff",
    "Phase2Outcome",
    "HealthObligation",
    "build_health_chat_request",
    "derive_nonce",
]
