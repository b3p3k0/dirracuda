"""Pure content contracts for coverage-first Analyst reports."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping

from .models import FileStage, FileTerminal


REPORT_SCHEMA: Final = "dirracuda-analyst-report-v1"
REPORT_ARTIFACT_NAMES: Final = (
    "findings.csv",
    "findings.jsonl",
    "report.html",
    "run.json",
)
REPORT_PAGE_ROWS: Final = 500
READ_PAGE_ROWS: Final = 500
MAX_REPORT_FILES: Final = 1_000_000
MAX_REPORT_FINDINGS: Final = 10_000_000
HTML_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
    "script-src 'none'; connect-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_CSV_PREFIXES: Final = frozenset("=+-@\t\r\n\"',;") - {"'"}
_CSV_UNICODE_PREFIXES: Final = frozenset({"−", "➕", "➖"})


class ReportContractError(ValueError):
    """A value is outside the frozen report contract."""


class EvidenceKind(str, Enum):
    DETECTOR = "detector"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class CountEntry:
    name: str
    count: int

    def __post_init__(self) -> None:
        if not _closed_text(self.name, 80) or not _count(self.count):
            raise ReportContractError("report count entry is invalid")


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    discovered_files: int
    excluded_paths: int
    detector_scanned_files: int
    selected_files: int
    model_reviewed_files: int
    valid_model_chunks: int
    detector_hits: int
    retained_model_findings: int
    terminal_counts: tuple[CountEntry, ...]
    format_counts: tuple[CountEntry, ...]
    exclusion_counts: tuple[CountEntry, ...]

    def __post_init__(self) -> None:
        scalar = (
            self.discovered_files,
            self.excluded_paths,
            self.detector_scanned_files,
            self.selected_files,
            self.model_reviewed_files,
            self.valid_model_chunks,
            self.detector_hits,
            self.retained_model_findings,
        )
        if any(not _count(value) for value in scalar):
            raise ReportContractError("report coverage count is invalid")
        if not (
            self.detector_scanned_files <= self.discovered_files
            and self.selected_files <= self.detector_scanned_files
            and self.model_reviewed_files <= self.selected_files
        ):
            raise ReportContractError("report coverage hierarchy is inconsistent")
        for values in (
            self.terminal_counts, self.format_counts, self.exclusion_counts,
        ):
            if (
                type(values) is not tuple
                or any(type(item) is not CountEntry for item in values)
                or tuple(item.name for item in values)
                != tuple(sorted(item.name for item in values))
                or len({item.name for item in values}) != len(values)
            ):
                raise ReportContractError("report coverage entries are not canonical")
        if sum(item.count for item in self.terminal_counts) != self.discovered_files:
            raise ReportContractError("report terminal counts do not cover inventory")
        if sum(item.count for item in self.exclusion_counts) != self.excluded_paths:
            raise ReportContractError("report exclusion counts are inconsistent")

    def as_json(self) -> dict[str, object]:
        return {
            "detector_hits": self.detector_hits,
            "detector_scanned_files": self.detector_scanned_files,
            "discovered_files": self.discovered_files,
            "excluded_paths": self.excluded_paths,
            "exclusion_counts": _counts_json(self.exclusion_counts),
            "format_counts": _counts_json(self.format_counts),
            "model_reviewed_files": self.model_reviewed_files,
            "valid_model_chunks": self.valid_model_chunks,
            "retained_model_findings": self.retained_model_findings,
            "selected_files": self.selected_files,
            "terminal_counts": _counts_json(self.terminal_counts),
        }


@dataclass(frozen=True, slots=True)
class ReportRun:
    run_id: str
    report_label: str = field(repr=False)
    mode: str
    source_mode: str
    created_at_utc: str
    model_tag: str
    model_digest: str
    worksheet_version: str
    prompt_sha256: str
    response_schema_sha256: str
    detector_rules_version: str
    detector_rules_sha256: str
    parser_bundle_sha256: str
    chunk_chars: int
    overlap_chars: int
    num_ctx: int
    num_predict: int
    isolation_mode: str
    reduced_isolation_ack: bool
    host_type: str | None
    protocol_server_id: int | None
    ip_address: str | None = field(repr=False)
    port: int | None
    extract_summary_row_id: int | None

    def __post_init__(self) -> None:
        texts = (
            self.run_id, self.report_label, self.mode, self.source_mode,
            self.created_at_utc, self.model_tag, self.worksheet_version,
            self.detector_rules_version, self.isolation_mode,
        )
        shas = (
            self.model_digest, self.prompt_sha256, self.response_schema_sha256,
            self.detector_rules_sha256, self.parser_bundle_sha256,
        )
        numbers = (self.chunk_chars, self.num_ctx, self.num_predict)
        optional_numbers = (
            self.protocol_server_id, self.port, self.extract_summary_row_id,
        )
        if (
            any(not _closed_text(value, 1024) for value in texts)
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in shas)
            or any(type(value) is not int or value <= 0 for value in numbers)
            or type(self.overlap_chars) is not int
            or not 0 <= self.overlap_chars < self.chunk_chars
            or type(self.reduced_isolation_ack) is not bool
            or self.mode not in {"fast", "deep"}
            or self.source_mode not in {
                "extraction_manifest", "single_host", "multi_host", "unknown",
            }
            or self.isolation_mode not in {"strict", "reduced"}
            or self.reduced_isolation_ack != (self.isolation_mode == "reduced")
            or self.host_type not in {None, "S", "F", "H"}
            or any(value is not None and (type(value) is not int or value <= 0)
                   for value in optional_numbers)
            or (self.port is not None and self.port > 65535)
            or any(value is not None and not _closed_text(value, 256)
                   for value in (self.host_type, self.ip_address))
        ):
            raise ReportContractError("report run identity is invalid")

    def as_json(self) -> dict[str, object]:
        return {
            "chunk_chars": self.chunk_chars,
            "created_at_utc": self.created_at_utc,
            "detector_rules_sha256": self.detector_rules_sha256,
            "detector_rules_version": self.detector_rules_version,
            "extract_summary_row_id": self.extract_summary_row_id,
            "host_type": self.host_type,
            "ip_address": self.ip_address,
            "isolation_mode": self.isolation_mode,
            "mode": self.mode,
            "model_digest": self.model_digest,
            "model_tag": self.model_tag,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "overlap_chars": self.overlap_chars,
            "parser_bundle_sha256": self.parser_bundle_sha256,
            "port": self.port,
            "prompt_sha256": self.prompt_sha256,
            "protocol_server_id": self.protocol_server_id,
            "reduced_isolation_ack": self.reduced_isolation_ack,
            "report_label": self.report_label,
            "response_schema_sha256": self.response_schema_sha256,
            "run_id": self.run_id,
            "source_mode": self.source_mode,
            "worksheet_version": self.worksheet_version,
        }


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    run: ReportRun
    coverage: CoverageSummary
    output_root: str = field(repr=False)

    def __post_init__(self) -> None:
        components = (
            self.output_root.split("/")[1:]
            if type(self.output_root) is str and self.output_root.startswith("/")
            else ()
        )
        if (
            type(self.run) is not ReportRun
            or type(self.coverage) is not CoverageSummary
            or type(self.output_root) is not str
            or not self.output_root.startswith("/")
            or "\\" in self.output_root
            or "\x00" in self.output_root
            or not components
            or any(part in {"", ".", ".."} for part in components)
            or len(self.output_root) > 4096
        ):
            raise ReportContractError("report snapshot target is invalid")


@dataclass(frozen=True, slots=True)
class InventoryReportRow:
    file_id: int
    ordinal: int
    relative_path: str = field(repr=False)
    size: int
    sha256: str
    stage: FileStage
    terminal: FileTerminal
    terminal_detail: str | None
    format_name: str | None
    selected_for_model: bool | None
    detector_hit_count: int
    chunk_count: int
    retained_model_finding_count: int

    def __post_init__(self) -> None:
        if (
            type(self.file_id) is not int or self.file_id <= 0
            or type(self.ordinal) is not int or self.ordinal < 0
            or not _relative_path(self.relative_path)
            or type(self.size) is not int or self.size < 0
            or type(self.sha256) is not str or _SHA256.fullmatch(self.sha256) is None
            or type(self.stage) is not FileStage
            or type(self.terminal) is not FileTerminal
            or (self.terminal_detail is not None and not _closed_text(self.terminal_detail, 64))
            or (self.format_name is not None and not _closed_text(self.format_name, 32))
            or self.selected_for_model not in {None, False, True}
            or any(not _count(value) for value in (
                self.detector_hit_count, self.chunk_count,
                self.retained_model_finding_count,
            ))
        ):
            raise ReportContractError("inventory report row is invalid")


@dataclass(frozen=True, slots=True)
class FindingReportRow:
    evidence_kind: EvidenceKind
    file_id: int
    file_ordinal: int
    relative_path: str = field(repr=False)
    format_name: str
    evidence_ordinal: int
    source_start: int
    source_end: int
    detector_kind: str | None = None
    detector_value: str | None = field(default=None, repr=False)
    chunk_index: int | None = None
    category: str | None = None
    quote: str | None = field(default=None, repr=False)
    document_type: str | None = None
    subject: str | None = field(default=None, repr=False)
    assessment: str | None = None
    model_offset: int | None = None
    model_offset_exact: bool | None = None
    match_count: int | None = None
    review_state: str | None = None
    provenance_kind: str | None = None
    provenance_label: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        common = (
            type(self.evidence_kind) is EvidenceKind
            and type(self.file_id) is int and self.file_id > 0
            and type(self.file_ordinal) is int and self.file_ordinal >= 0
            and _relative_path(self.relative_path)
            and _closed_text(self.format_name, 32)
            and type(self.evidence_ordinal) is int and self.evidence_ordinal >= 0
            and type(self.source_start) is int and self.source_start >= 0
            and type(self.source_end) is int and self.source_end > self.source_start
        )
        if not common:
            raise ReportContractError("finding report row is invalid")
        if self.evidence_kind is EvidenceKind.DETECTOR:
            valid = (
                _closed_text(self.detector_kind, 64)
                and isinstance(self.detector_value, str)
                and bool(self.detector_value)
                and self.source_end - self.source_start == len(self.detector_value)
                and all(value is None for value in self._model_values())
            )
        else:
            valid = (
                self.detector_kind is None and self.detector_value is None
                and type(self.chunk_index) is int and self.chunk_index >= 0
                and _closed_text(self.category, 64)
                and isinstance(self.quote, str) and bool(self.quote)
                and self.source_end - self.source_start == len(self.quote)
                and _closed_text(self.document_type, 80)
                and isinstance(self.subject, str) and len(self.subject) <= 160
                and _closed_text(self.assessment, 64)
                and type(self.model_offset) is int and self.model_offset >= 0
                and type(self.model_offset_exact) is bool
                and type(self.match_count) is int and self.match_count > 0
                and _closed_text(self.review_state, 32)
                and (self.provenance_kind is None or _closed_text(self.provenance_kind, 32))
                and (self.provenance_label is None or _closed_text(self.provenance_label, 256))
                and ((self.provenance_kind is None) == (self.provenance_label is None))
            )
        if not valid:
            raise ReportContractError("finding evidence shape is inconsistent")

    def _model_values(self) -> tuple[object, ...]:
        return (
            self.chunk_index, self.category, self.quote, self.document_type,
            self.subject, self.assessment, self.model_offset,
            self.model_offset_exact, self.match_count, self.review_state,
            self.provenance_kind, self.provenance_label,
        )

    def as_json(self) -> dict[str, object]:
        return {
            "assessment": self.assessment,
            "category": self.category,
            "chunk_index": self.chunk_index,
            "detector_kind": self.detector_kind,
            "detector_value": self.detector_value,
            "document_type": self.document_type,
            "evidence_kind": self.evidence_kind.value,
            "evidence_ordinal": self.evidence_ordinal,
            "file_id": self.file_id,
            "file_ordinal": self.file_ordinal,
            "format_name": self.format_name,
            "match_count": self.match_count,
            "model_offset": self.model_offset,
            "model_offset_exact": self.model_offset_exact,
            "provenance_kind": self.provenance_kind,
            "provenance_label": self.provenance_label,
            "quote": self.quote,
            "relative_path": self.relative_path,
            "review_state": self.review_state,
            "source_end": self.source_end,
            "source_start": self.source_start,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    name: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str or self.name not in REPORT_ARTIFACT_NAMES
            or type(self.size) is not int or self.size < 0
            or type(self.sha256) is not str or _SHA256.fullmatch(self.sha256) is None
        ):
            raise ReportContractError("report artifact identity is invalid")


@dataclass(frozen=True, slots=True)
class ReportManifest:
    artifacts: tuple[ArtifactIdentity, ...]

    def __post_init__(self) -> None:
        if (
            type(self.artifacts) is not tuple
            or any(type(item) is not ArtifactIdentity for item in self.artifacts)
            or tuple(item.name for item in self.artifacts) != REPORT_ARTIFACT_NAMES
        ):
            raise ReportContractError("report manifest is not canonical")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({
            "artifacts": [
                {"name": item.name, "sha256": item.sha256, "size": item.size}
                for item in self.artifacts
            ],
            "schema": REPORT_SCHEMA,
        })

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class ReportFinalizationResult:
    run_id: str
    manifest: ReportManifest

    def __post_init__(self) -> None:
        if (
            not _closed_text(self.run_id, 128)
            or type(self.manifest) is not ReportManifest
        ):
            raise ReportContractError("report finalization result is invalid")


def canonical_json_bytes(value: object) -> bytes:
    """Return the single canonical UTF-8 JSON representation used by C12."""
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def csv_safe(value: object) -> str:
    """Return a spreadsheet-safe display cell without changing canonical evidence."""
    if value is None:
        return ""
    if type(value) is bool:
        text = "true" if value else "false"
    elif type(value) in {int, float}:
        text = str(value)
    elif type(value) is str:
        text = value
    else:
        raise TypeError("CSV cells require scalar report values")
    if not text:
        return text
    normalized = unicodedata.normalize("NFKC", text[0])
    if (
        text[0] in _CSV_PREFIXES
        or text[0] in _CSV_UNICODE_PREFIXES
        or any(char in _CSV_PREFIXES for char in normalized)
    ):
        return "'" + text
    return text


def frozen_counts(values: Mapping[str, int]) -> tuple[CountEntry, ...]:
    """Convert an exact count mapping to deterministic immutable entries."""
    if not isinstance(values, Mapping):
        raise TypeError("report counts require a mapping")
    return tuple(CountEntry(name, values[name]) for name in sorted(values))


def readonly_json(value: Mapping[str, object]) -> Mapping[str, object]:
    """Expose a shallow read-only mapping for presentation adapters."""
    return MappingProxyType(dict(value))


def _counts_json(values: tuple[CountEntry, ...]) -> dict[str, int]:
    return {item.name: item.count for item in values}


def _closed_text(value: object, maximum: int) -> bool:
    return (
        type(value) is str and 0 < len(value) <= maximum
        and _CONTROL.search(value) is None
    )


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_REPORT_FINDINGS


def _relative_path(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 4096:
        return False
    parts = value.split("/")
    return (
        not value.startswith("/") and "\\" not in value and "\x00" not in value
        and all(part not in {"", ".", ".."} for part in parts)
    )


__all__ = [
    "ArtifactIdentity",
    "CountEntry",
    "CoverageSummary",
    "EvidenceKind",
    "FindingReportRow",
    "HTML_CSP",
    "InventoryReportRow",
    "MAX_REPORT_FILES",
    "MAX_REPORT_FINDINGS",
    "READ_PAGE_ROWS",
    "REPORT_ARTIFACT_NAMES",
    "REPORT_PAGE_ROWS",
    "REPORT_SCHEMA",
    "ReportContractError",
    "ReportManifest",
    "ReportFinalizationResult",
    "ReportRun",
    "ReportSnapshot",
    "canonical_json_bytes",
    "csv_safe",
    "frozen_counts",
    "readonly_json",
]
