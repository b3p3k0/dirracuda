"""Pure, content-free contracts for the Analyst Phase 1 worker.

The detached worker starts with only a run id.  These values are the immutable,
strictly validated snapshot it may load before opening a private source file.
Filesystem, SQLite, parser and network behavior belong to later modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath
from typing import TYPE_CHECKING, Final, Mapping

from .inventory import InventoryFile, InventoryResult
from .models import ANALYST_DEFAULTS, FileStage
from .source_reopen import SourceRootIdentity
from .state import RunState

if TYPE_CHECKING:
    from .lease import LeaseFence


SOURCE_IDENTITY_KIND: Final = "analyst-source-root"
SOURCE_IDENTITY_VERSION: Final = 1
MAX_PHASE1_TASKS: Final = 4
WORKER_POLL_SECONDS: Final = 1.0
HEARTBEAT_INTERVAL_SECONDS: Final = 2.0

_SOURCE_IDENTITY_KEYS: Final = frozenset(
    {"kind", "version", "root_device", "root_inode", "root_mount_id"}
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SOURCE_MODES: Final = frozenset(
    {"extraction_manifest", "single_host", "multi_host", "unknown"}
)
_EXACT_FORMATS: Final = frozenset(
    {"text", "rtf", "pdf", "docx", "xlsx", "pptx", "doc", "xls"}
)
_FORMAT_CANDIDATES: Final = frozenset({"ooxml", "legacy_office"})
MAX_DETECTOR_HITS: Final = 10_000
MAX_PHASE1_FILES: Final = 100_000
MAX_CHUNKS_PER_FILE: Final = 1_034
_ENCODINGS: Final = frozenset(
    {
        "rtf",
        "utf-8",
        "utf-8-bom",
        "utf-16-le-bom",
        "utf-16-be-bom",
        "utf-32-le-bom",
        "utf-32-be-bom",
        "windows-1252",
    }
)
_PRE_MODEL_STAGES: Final = frozenset(
    {
        FileStage.DISCOVERED,
        FileStage.FORMAT_IDENTIFIED,
        FileStage.TEXT_EXTRACTED,
        FileStage.DETECTOR_SCANNED,
        FileStage.SELECTED_FOR_MODEL,
    }
)


class WorkerContractError(ValueError):
    """Persisted worker inputs do not satisfy the frozen C10 contract."""


class WorkerOutcome(str, Enum):
    """Closed, privacy-safe outcomes for the later worker shell."""

    PHASE1_HANDOFF = "phase1_handoff"
    CANCELLED = "cancelled"
    LEASE_BUSY = "lease_busy"
    PREFLIGHT_FAILED = "preflight_failed"
    RUN_INVALID = "run_invalid"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class WorkerRunContext:
    """Strict, content-free snapshot loaded by a run-id-only worker."""

    run_id: str
    observed_state: RunState
    observed_revision: int
    mode: str
    source_mode: str
    source_root: str = field(repr=False)
    output_root: str = field(repr=False)
    root_identity: SourceRootIdentity
    source_identity_sha256: str
    report_label: str = field(repr=False)
    model_tag: str
    model_digest: str
    worksheet_version: str
    prompt_sha256: str
    response_schema_sha256: str
    detector_rules_version: str
    detector_rules_sha256: str
    parser_bundle_json: str = field(repr=False)
    parser_bundle_sha256: str
    chunk_chars: int
    overlap_chars: int
    num_ctx: int
    num_predict: int
    isolation_mode: str
    reduced_isolation_ack: bool
    host_type: str | None = None
    protocol_server_id: int | None = None
    ip_address: str | None = field(default=None, repr=False)
    port: int | None = None
    extract_summary_row_id: int | None = None

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or _RUN_ID.fullmatch(self.run_id) is None:
            raise WorkerContractError("run id is outside the worker contract")
        if type(self.observed_state) is not RunState:
            raise WorkerContractError("run state is outside the worker contract")
        _nonnegative_int(self.observed_revision, "run revision")
        if type(self.mode) is not str or self.mode not in {"fast", "deep"}:
            raise WorkerContractError("run mode is outside the worker contract")
        if type(self.source_mode) is not str or self.source_mode not in _SOURCE_MODES:
            raise WorkerContractError("source mode is outside the worker contract")
        _absolute_path(self.source_root, "source root")
        _absolute_path(self.output_root, "output root")
        if type(self.root_identity) is not SourceRootIdentity:
            raise WorkerContractError("source identity is not typed")
        _sha256(self.source_identity_sha256, "source identity hash")
        source_json = _canonical_json({
            "kind": SOURCE_IDENTITY_KIND,
            "root_device": self.root_identity.device,
            "root_inode": self.root_identity.inode,
            "root_mount_id": self.root_identity.mount_id,
            "version": SOURCE_IDENTITY_VERSION,
        })
        if (
            hashlib.sha256(source_json.encode("utf-8")).hexdigest()
            != self.source_identity_sha256
        ):
            raise WorkerContractError("source identity hash does not match its value")
        _bounded_text(self.report_label, "report label", 1024)
        if self.model_tag != ANALYST_DEFAULTS.model_tag:
            raise WorkerContractError("model tag differs from the frozen default")
        if self.model_digest != ANALYST_DEFAULTS.model_digest:
            raise WorkerContractError("model digest differs from the frozen default")
        if self.worksheet_version != ANALYST_DEFAULTS.worksheet_version:
            raise WorkerContractError("worksheet version differs from the frozen default")
        for value, label in (
            (self.prompt_sha256, "prompt hash"),
            (self.response_schema_sha256, "response schema hash"),
            (self.detector_rules_sha256, "detector rules hash"),
            (self.parser_bundle_sha256, "parser bundle hash"),
        ):
            _sha256(value, label)
        _bounded_text(self.detector_rules_version, "detector rules version", 128)
        _validate_json_identity(
            self.parser_bundle_json, self.parser_bundle_sha256, "parser bundle",
        )
        expected_numbers = (
            (self.chunk_chars, ANALYST_DEFAULTS.chunk_chars, "chunk size"),
            (self.overlap_chars, ANALYST_DEFAULTS.overlap_chars, "chunk overlap"),
            (self.num_ctx, ANALYST_DEFAULTS.num_ctx, "context size"),
            (self.num_predict, ANALYST_DEFAULTS.num_predict, "prediction size"),
        )
        if any(type(value) is not int or value != expected for value, expected, _ in expected_numbers):
            label = next(
                label for value, expected, label in expected_numbers
                if type(value) is not int or value != expected
            )
            raise WorkerContractError(f"{label} differs from the frozen default")
        if self.isolation_mode != "strict" or type(self.isolation_mode) is not str:
            raise WorkerContractError("C10 supports strict isolation only")
        if type(self.reduced_isolation_ack) is not bool or self.reduced_isolation_ack:
            raise WorkerContractError("C10 does not accept reduced isolation")
        _optional_host_identity(
            self.host_type,
            self.protocol_server_id,
            self.ip_address,
            self.port,
            self.extract_summary_row_id,
        )


@dataclass(frozen=True, slots=True)
class Phase1ChunkIdentity:
    """One durable, content-free chunk identity handed to C11."""

    chunk_id: int
    index: int
    start: int
    end: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.chunk_id) is not int or self.chunk_id <= 0:
            raise WorkerContractError("chunk id must be a positive integer")
        if type(self.index) is not int or self.index < 0:
            raise WorkerContractError("chunk index must be nonnegative")
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or self.start < 0
            or self.end <= self.start
        ):
            raise WorkerContractError("chunk bounds are invalid")
        _sha256(self.sha256, "chunk hash")


@dataclass(frozen=True, slots=True)
class Phase1FileHandoff:
    """Ordered durable chunks for one selected file."""

    file_id: int
    ordinal: int
    chunks: tuple[Phase1ChunkIdentity, ...]

    def __post_init__(self) -> None:
        if type(self.file_id) is not int or self.file_id <= 0:
            raise WorkerContractError("file id must be a positive integer")
        _nonnegative_int(self.ordinal, "file ordinal")
        if type(self.chunks) is not tuple or not self.chunks:
            raise WorkerContractError("selected file requires durable chunks")
        if len(self.chunks) > MAX_CHUNKS_PER_FILE:
            raise WorkerContractError("selected file exceeds the chunk bound")
        if any(type(chunk) is not Phase1ChunkIdentity for chunk in self.chunks):
            raise WorkerContractError("file handoff contains an invalid chunk")
        _validate_chunk_sequence(self.chunks)


@dataclass(frozen=True, slots=True)
class FileResumeSnapshot:
    """Bounded, content-free durable evidence needed to resume Phase 1."""

    file_id: int
    ordinal: int
    inventory_file: InventoryFile = field(repr=False)
    stage: FileStage
    format_name: str | None
    encoding: str | None
    parser_identity_json: str | None = field(repr=False)
    parser_identity_sha256: str | None
    extraction_meta_json: str | None = field(repr=False)
    extraction_meta_sha256: str | None
    detector_hit_count: int
    selected_for_model: bool | None
    chunks: tuple[Phase1ChunkIdentity, ...]

    def __post_init__(self) -> None:
        if type(self.file_id) is not int or self.file_id <= 0:
            raise WorkerContractError("resume file id must be positive")
        _nonnegative_int(self.ordinal, "resume file ordinal")
        if type(self.inventory_file) is not InventoryFile:
            raise WorkerContractError("resume inventory identity is not typed")
        _validate_inventory_file(self.inventory_file)
        if type(self.stage) is not FileStage or self.stage not in _PRE_MODEL_STAGES:
            raise WorkerContractError("resume stage is outside Phase 1")
        _nonnegative_int(self.detector_hit_count, "detector hit count")
        if self.detector_hit_count > MAX_DETECTOR_HITS:
            raise WorkerContractError("resume detector hit count exceeds its bound")
        if type(self.chunks) is not tuple or any(
            type(chunk) is not Phase1ChunkIdentity for chunk in self.chunks
        ):
            raise WorkerContractError("resume chunk evidence is invalid")
        if self.chunks:
            _validate_chunk_sequence(self.chunks)
        self._validate_stage_evidence()

    def _validate_stage_evidence(self) -> None:
        has_extraction = all(value is not None for value in (
            self.parser_identity_json,
            self.parser_identity_sha256,
            self.extraction_meta_json,
            self.extraction_meta_sha256,
        ))
        partial_extraction = any(value is not None for value in (
            self.parser_identity_json,
            self.parser_identity_sha256,
            self.extraction_meta_json,
            self.extraction_meta_sha256,
        )) and not has_extraction
        if partial_extraction:
            raise WorkerContractError("resume extraction evidence is partial")
        if has_extraction:
            assert self.parser_identity_json is not None
            assert self.parser_identity_sha256 is not None
            assert self.extraction_meta_json is not None
            assert self.extraction_meta_sha256 is not None
            _validate_json_identity(
                self.parser_identity_json,
                self.parser_identity_sha256,
                "parser identity",
            )
            _validate_json_identity(
                self.extraction_meta_json,
                self.extraction_meta_sha256,
                "extraction metadata",
            )
        if self.encoding is not None and (
            type(self.encoding) is not str or self.encoding not in _ENCODINGS
        ):
            raise WorkerContractError("extraction encoding is not supported")

        if self.stage is FileStage.DISCOVERED:
            valid = (
                self.format_name is None
                and self.encoding is None
                and not has_extraction
                and self.detector_hit_count == 0
                and self.selected_for_model is None
                and not self.chunks
            )
        elif self.stage is FileStage.FORMAT_IDENTIFIED:
            valid = (
                self.format_name in _EXACT_FORMATS | _FORMAT_CANDIDATES
                and self.encoding is None
                and not has_extraction
                and self.detector_hit_count == 0
                and self.selected_for_model is None
                and not self.chunks
            )
        else:
            valid = self.format_name in _EXACT_FORMATS and has_extraction
            if self.stage is FileStage.TEXT_EXTRACTED:
                valid = (
                    valid
                    and self.detector_hit_count == 0
                    and self.selected_for_model is None
                    and not self.chunks
                )
            elif self.stage is FileStage.DETECTOR_SCANNED:
                valid = valid and type(self.selected_for_model) is bool and not self.chunks
            else:
                valid = valid and self.selected_for_model is True and bool(self.chunks)
        if not valid:
            raise WorkerContractError("resume evidence contradicts its durable stage")


@dataclass(frozen=True, slots=True)
class Phase1Handoff:
    """Current fence and ordered content-free work passed to C11."""

    fence: LeaseFence = field(repr=False)
    files: tuple[Phase1FileHandoff, ...]

    def __post_init__(self) -> None:
        from .lease import LeaseFence

        if type(self.fence) is not LeaseFence:
            raise WorkerContractError("Phase 1 handoff fence is not typed")
        if type(self.files) is not tuple or any(
            type(item) is not Phase1FileHandoff for item in self.files
        ):
            raise WorkerContractError("Phase 1 handoff files are invalid")
        if len(self.files) > MAX_PHASE1_FILES:
            raise WorkerContractError("Phase 1 handoff exceeds the file bound")
        order = tuple((item.ordinal, item.file_id) for item in self.files)
        if (
            order != tuple(sorted(order))
            or len({item.file_id for item in self.files}) != len(self.files)
            or len({item.ordinal for item in self.files}) != len(self.files)
            or len({chunk.chunk_id for item in self.files for chunk in item.chunks})
            != sum(len(item.chunks) for item in self.files)
        ):
            raise WorkerContractError("Phase 1 handoff file order is not canonical")

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def chunk_count(self) -> int:
        return sum(len(item.chunks) for item in self.files)


def build_source_identity(inventory: InventoryResult) -> dict[str, object]:
    """Build the exact runnable source identity from a completed inventory."""
    if type(inventory) is not InventoryResult:
        raise TypeError("source identity requires an InventoryResult")
    try:
        root = SourceRootIdentity.from_inventory(inventory)
    except ValueError as exc:
        raise WorkerContractError("source root identity is not runnable") from exc
    return {
        "kind": SOURCE_IDENTITY_KIND,
        "root_device": root.device,
        "root_inode": root.inode,
        "root_mount_id": root.mount_id,
        "version": SOURCE_IDENTITY_VERSION,
    }


def parse_source_identity(value: Mapping[str, object]) -> SourceRootIdentity:
    """Parse only the exact canonical source-root v1 object shape."""
    if type(value) is not dict or set(value) != _SOURCE_IDENTITY_KEYS:
        raise WorkerContractError("source identity has an unknown or missing field")
    if value["kind"] != SOURCE_IDENTITY_KIND:
        raise WorkerContractError("source identity kind is unsupported")
    if type(value["version"]) is not int or value["version"] != SOURCE_IDENTITY_VERSION:
        raise WorkerContractError("source identity version is unsupported")
    device = value["root_device"]
    inode = value["root_inode"]
    mount_id = value["root_mount_id"]
    if (
        type(device) is not int
        or device < 0
        or type(inode) is not int
        or inode <= 0
        or type(mount_id) is not int
        or mount_id <= 0
    ):
        raise WorkerContractError("source root identity is invalid")
    return SourceRootIdentity(device=device, inode=inode, mount_id=mount_id)


def _validate_chunk_sequence(chunks: tuple[Phase1ChunkIdentity, ...]) -> None:
    if tuple(chunk.index for chunk in chunks) != tuple(range(len(chunks))):
        raise WorkerContractError("chunk indexes are not canonical")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise WorkerContractError("chunk ids are not unique")
    if chunks[0].start != 0 or any(
        chunk.end - chunk.start > ANALYST_DEFAULTS.chunk_chars for chunk in chunks
    ):
        raise WorkerContractError("chunk window bounds are invalid")
    if any(
        prior.end - prior.start != ANALYST_DEFAULTS.chunk_chars
        or current.start != prior.end - ANALYST_DEFAULTS.overlap_chars
        or current.end <= prior.end
        for prior, current in zip(chunks, chunks[1:])
    ):
        raise WorkerContractError("chunk overlap or order is invalid")


def _validate_json_identity(body: str, digest: str, label: str) -> None:
    _bounded_text(body, f"{label} JSON", 65_536)
    _sha256(digest, f"{label} hash")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError) as exc:
        raise WorkerContractError(f"{label} JSON cannot be decoded") from exc
    if type(value) is not dict or _canonical_json(value) != body:
        raise WorkerContractError(f"{label} JSON is not a canonical object")
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != digest:
        raise WorkerContractError(f"{label} hash does not match its JSON")


def _validate_inventory_file(value: InventoryFile) -> None:
    relative = value.relative_path
    if type(relative) is not str:
        raise WorkerContractError("inventory path is not text")
    _bounded_text(relative, "inventory path", 4096)
    parts = relative.split("/")
    if (
        relative.startswith("/")
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise WorkerContractError("inventory path is not canonical relative text")
    numbers = (
        value.size,
        value.mtime_ns,
        value.ctime_ns,
        value.device,
        value.inode,
        value.mode,
    )
    if (
        any(type(item) is not int or item < 0 for item in numbers)
        or value.inode <= 0
        or value.mode > 0o7777
    ):
        raise WorkerContractError("inventory file metadata is invalid")
    _sha256(value.sha256, "inventory file hash")


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise WorkerContractError("identity is not canonical JSON data") from exc


def _absolute_path(value: object, label: str) -> None:
    _bounded_text(value, label, 4096)
    assert isinstance(value, str)
    path = PurePath(value)
    if (
        not path.is_absolute()
        or value.startswith("//")
        or "\\" in value
        or any(part in {".", ".."} for part in value.split("/"))
        or ".." in path.parts
        or "." in path.parts
        or os.fspath(path) != value
    ):
        raise WorkerContractError(f"{label} is not a canonical absolute path")


def _bounded_text(value: object, label: str, maximum: int) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise WorkerContractError(f"{label} must be nonempty text")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise WorkerContractError(f"{label} is not Unicode scalar text") from exc
    if size > maximum:
        raise WorkerContractError(f"{label} exceeds its bound")


def _sha256(value: object, label: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise WorkerContractError(f"{label} is invalid")


def _nonnegative_int(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise WorkerContractError(f"{label} must be a nonnegative integer")


def _optional_host_identity(
    host_type: object,
    protocol_server_id: object,
    ip_address: object,
    port: object,
    extract_summary_row_id: object,
) -> None:
    if host_type is not None and (type(host_type) is not str or host_type not in {"S", "F", "H"}):
        raise WorkerContractError("host type is invalid")
    for value, label in (
        (protocol_server_id, "protocol server id"),
        (extract_summary_row_id, "extract summary row id"),
    ):
        if value is not None and (type(value) is not int or value <= 0):
            raise WorkerContractError(f"{label} is invalid")
    if ip_address is not None:
        _bounded_text(ip_address, "IP address", 64)
    if port is not None and (type(port) is not int or not 1 <= port <= 65_535):
        raise WorkerContractError("port is invalid")


__all__ = [
    "FileResumeSnapshot",
    "HEARTBEAT_INTERVAL_SECONDS",
    "MAX_CHUNKS_PER_FILE",
    "MAX_DETECTOR_HITS",
    "MAX_PHASE1_FILES",
    "MAX_PHASE1_TASKS",
    "Phase1ChunkIdentity",
    "Phase1FileHandoff",
    "Phase1Handoff",
    "SOURCE_IDENTITY_KIND",
    "SOURCE_IDENTITY_VERSION",
    "SourceRootIdentity",
    "WORKER_POLL_SECONDS",
    "WorkerContractError",
    "WorkerOutcome",
    "WorkerRunContext",
    "build_source_identity",
    "parse_source_identity",
]
