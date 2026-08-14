"""Pure, immutable data contracts shared by later Analyst cards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    PII = "pii"
    FINANCIAL = "financial"
    CONTACT = "contact"
    DEMOGRAPHIC = "demographic"


class Assessment(str, Enum):
    FINDINGS_PRESENT = "findings_present"
    NO_FINDINGS = "no_findings"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FileStage(str, Enum):
    DISCOVERED = "discovered"
    FORMAT_IDENTIFIED = "format_identified"
    TEXT_EXTRACTED = "text_extracted"
    DETECTOR_SCANNED = "detector_scanned"
    SELECTED_FOR_MODEL = "selected_for_model"
    MODEL_REVIEWED = "model_reviewed"
    MODEL_RESPONSE_VALID = "model_response_valid"


class FileTerminal(str, Enum):
    COMPLETE_DETECTOR_ONLY = "complete_detector_only"
    COMPLETE_MODEL_REVIEWED = "complete_model_reviewed"
    COMPLETE_NO_SUPPORTED_CONTENT = "complete_no_supported_content"
    UNSUPPORTED_FORMAT = "unsupported_format"
    NO_TEXT_LAYER = "no_text_layer"
    PARSE_TIMEOUT = "parse_timeout"
    PARSE_OOM = "parse_oom"
    PARSE_SIGNAL = "parse_signal"
    PARSE_ERROR = "parse_error"
    PARSER_OUTPUT_LIMIT = "parser_output_limit"
    OVERSIZE = "oversize"
    EMPTY = "empty"
    ENCRYPTED = "encrypted"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    SANDBOX_ERROR = "sandbox_error"
    MODEL_INVALID = "model_invalid"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_TRANSPORT_ERROR = "model_transport_error"
    SOURCE_CHANGED_SINCE_INVENTORY = "source_changed_since_inventory"
    CANCELLED_ABANDONED = "cancelled_abandoned"
    SKIPPED_ANALYST_OUTPUT = "skipped_analyst_output"
    SKIPPED_KNOWN_BAD = "skipped_known_bad"


class ResumableState(str, Enum):
    CANCELLED_PENDING_RESUME = "cancelled_pending_resume"


@dataclass(frozen=True, slots=True)
class AnalystDefaults:
    model_tag: str
    model_digest: str
    worksheet_version: str
    chunk_chars: int
    overlap_chars: int
    num_ctx: int
    num_predict: int


ANALYST_DEFAULTS = AnalystDefaults(
    model_tag="qwen3.6:27b",
    model_digest=(
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"
    ),
    worksheet_version="v2",
    chunk_chars=8000,
    overlap_chars=256,
    num_ctx=8192,
    num_predict=1024,
)


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class DetectorHit:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class GroundedFinding:
    category: Category
    quote: str
    model_offset: int
    canonical_offset: int
    canonical_end: int
    match_count: int
    model_offset_exact: bool


@dataclass(frozen=True, slots=True)
class WorksheetResult:
    document_type: str
    subject: str
    model_assessment: Assessment
    findings: tuple[GroundedFinding, ...]
    raw_finding_count: int
    removed_duplicate_count: int
    dropped_ungrounded_count: int

    @property
    def model_offset_mismatch_count(self) -> int:
        return sum(not finding.model_offset_exact for finding in self.findings)
