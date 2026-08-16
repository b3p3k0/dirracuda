"""Fenced durable file, chunk, attempt, and finalization checkpoints."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .lease import LeaseFence
from .models import (
    Assessment,
    Category,
    DetectorHit,
    FileStage,
    FileTerminal,
    GroundedFinding,
    WorksheetResult,
)
from .state import AttemptState, require_stage_advance
from .store import run_immediate


_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_VALUE_RE = re.compile(r"[A-Za-z0-9_.:+-]{1,128}\Z")
_PROVENANCE_LABEL_RE = re.compile(r"[A-Za-z0-9._/!#:+-]{1,256}\Z")
_FORMAT_NAMES = frozenset({"text", "rtf", "pdf", "docx", "xlsx", "pptx", "doc", "xls"})
_FORMAT_CANDIDATES = frozenset({"ooxml", "legacy_office"})
_ENCODINGS = frozenset({
    "rtf", "utf-8", "utf-8-bom", "utf-16-le-bom", "utf-16-be-bom",
    "utf-32-le-bom", "utf-32-be-bom", "windows-1252",
})
_PARSER_IDENTITY_KEYS = frozenset({
    "parser", "parser_version", "embedded_version", "package_revision",
})
_EXTRACTION_COUNT_KEYS = frozenset({
    "text_bytes", "text_chars", "page_count", "text_page_count",
    "logical_unit_count", "primary_unit_count", "member_count",
    "expanded_bytes", "worksheet_count", "skipped_sheet_count",
    "dense_cell_count",
})
MAX_DETECTOR_HITS = 10_000
MAX_PROVENANCE_UNITS = 50_000
_PROVENANCE_KINDS = frozenset({
    "page", "paragraph", "cell", "slide", "notes", "comments", "output_line",
})
_DETECTOR_KINDS = frozenset({
    "bank_account", "card", "demographic_term", "dob", "email", "iban",
    "passport", "phone", "routing", "ssn",
})
_DISCOVERED_TERMINALS = frozenset({
    FileTerminal.UNSUPPORTED_FORMAT,
    FileTerminal.OVERSIZE,
    FileTerminal.EMPTY,
    FileTerminal.SKIPPED_ANALYST_OUTPUT,
    FileTerminal.SKIPPED_KNOWN_BAD,
})
_FORMAT_FAILURE_TERMINALS = frozenset({
    FileTerminal.NO_TEXT_LAYER,
    FileTerminal.PARSE_TIMEOUT,
    FileTerminal.PARSE_OOM,
    FileTerminal.PARSE_SIGNAL,
    FileTerminal.PARSE_ERROR,
    FileTerminal.PARSER_OUTPUT_LIMIT,
    FileTerminal.ENCRYPTED,
    FileTerminal.SANDBOX_UNAVAILABLE,
    FileTerminal.SANDBOX_ERROR,
})
_TERMINAL_DETAILS: dict[FileTerminal, frozenset[str | None]] = {
    FileTerminal.COMPLETE_DETECTOR_ONLY: frozenset({None}),
    FileTerminal.COMPLETE_MODEL_REVIEWED: frozenset({None}),
    FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT: frozenset({None}),
    FileTerminal.UNSUPPORTED_FORMAT: frozenset({
        None, "compression_method", "macro_enabled", "not_ooxml",
        "not_word_binary", "not_xls", "strict_ooxml",
        "unsupported_format", "unsupported_word_variant",
    }),
    FileTerminal.NO_TEXT_LAYER: frozenset({"no_text_layer"}),
    FileTerminal.PARSE_TIMEOUT: frozenset({None}),
    FileTerminal.PARSE_OOM: frozenset({None, "memory_limit"}),
    FileTerminal.PARSE_SIGNAL: frozenset({None}),
    FileTerminal.PARSE_ERROR: frozenset({
        None, "antiword_failed", "archive_corrupt", "archive_duplicate",
        "archive_encrypted", "archive_path", "attribute_limit",
        "binary_length", "calamine_failed", "cell_reference",
        "content_types", "control_character", "control_parameter",
        "control_word", "encryption_state", "font_id", "formula_value",
        "format_mismatch", "group_depth", "hex_escape", "input_alias",
        "input_io", "main_relationship", "page_count", "pdf_parse",
        "relationship", "rtf_header", "scalar_type", "shared_string",
        "sheet_metadata", "sheet_shape", "text_decode", "text_type",
        "trailing_content", "trailing_escape", "unbalanced_group",
        "unicode_fallback", "unicode_surrogate", "unicode_value",
        "unsupported_codepage", "xml_parse",
    }),
    FileTerminal.PARSER_OUTPUT_LIMIT: frozenset({
        None, "aggregate_ratio", "cell_limit", "cell_text_limit",
        "dimension_limit", "expanded_limit", "member_limit", "member_ratio",
        "member_size", "page_limit", "semantic_unit_limit", "sheet_limit",
        "slide_limit", "stderr_limit", "text_limit", "xml_depth",
        "xml_element_limit", "xml_package_limit", "xml_size",
    }),
    FileTerminal.DETECTOR_OUTPUT_LIMIT: frozenset({"detector_hit_limit"}),
    FileTerminal.OVERSIZE: frozenset({None, "source_limit"}),
    FileTerminal.EMPTY: frozenset({None}),
    FileTerminal.ENCRYPTED: frozenset({"password_required"}),
    FileTerminal.SANDBOX_UNAVAILABLE: frozenset({
        None, "dependency_missing", "dependency_version",
    }),
    FileTerminal.SANDBOX_ERROR: frozenset({None}),
    FileTerminal.MODEL_INVALID: frozenset({None, "model_invalid"}),
    FileTerminal.MODEL_TIMEOUT: frozenset({None, "model_timeout"}),
    FileTerminal.MODEL_TRANSPORT_ERROR: frozenset({
        None, "model_transport_error",
    }),
    FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY: frozenset({None}),
    FileTerminal.CANCELLED_ABANDONED: frozenset({"operator_abandon"}),
    FileTerminal.SKIPPED_ANALYST_OUTPUT: frozenset({None}),
    FileTerminal.SKIPPED_KNOWN_BAD: frozenset({None}),
}
_FENCE_WHERE = (
    "generation=? AND run_id=? AND owner_token=? AND pid=? AND start_ticks=? "
    "AND boot_id=? AND heartbeat_monotonic_ns=?"
)


class CheckpointError(RuntimeError):
    """A checkpoint violated lifecycle, ownership, or immutable evidence."""


class DetectorHitLimit(CheckpointError):
    """The deterministic hit set exceeded its durable per-file cap."""


@dataclass(frozen=True, slots=True)
class FileClaim:
    file_id: int
    ordinal: int
    relative_path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    mode: int
    sha256: str
    stage: FileStage


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    index: int
    start: int
    end: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("chunk index must be a nonnegative integer")
        if (type(self.start) is not int or type(self.end) is not int
                or self.start < 0 or self.end <= self.start):
            raise ValueError("chunk bounds are invalid")
        _require_sha(self.sha256, "chunk sha256")


@dataclass(frozen=True, slots=True)
class ProvenanceUnit:
    """Content-free source unit boundaries retained for report grounding."""

    kind: str
    label: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.kind not in _PROVENANCE_KINDS:
            raise ValueError("provenance kind is not in the closed vocabulary")
        if _PROVENANCE_LABEL_RE.fullmatch(self.label) is None:
            raise ValueError("provenance label is not canonical content-free text")
        if (type(self.start) is not int or type(self.end) is not int
                or self.start < 0 or self.end < self.start):
            raise ValueError("provenance bounds are invalid")


@dataclass(frozen=True, slots=True)
class ExtractionCheckpointEvidence:
    """Typed, content-free durable projection of one successful extraction."""

    encoding: str | None
    parser_identity: Mapping[str, object]
    extraction_counts: Mapping[str, object]
    provenance: tuple[ProvenanceUnit, ...]


def build_extraction_evidence(result: object) -> ExtractionCheckpointEvidence:
    """Project a validated ExtractionResult without retaining its text."""
    from .extract import ExtractionResult

    if not isinstance(result, ExtractionResult) or not result.ok:
        raise ValueError("durable extraction evidence requires a successful result")
    if not isinstance(result.text, str) or result.format_name not in _FORMAT_NAMES:
        raise ValueError("successful extraction result is incomplete")
    format_name = result.format_name
    parser_name = {
        "text": "builtin_text",
        "rtf": "builtin_rtf",
        "pdf": "pymupdf",
        "docx": "defusedxml",
        "xlsx": "defusedxml",
        "pptx": "defusedxml",
        "doc": "antiword",
        "xls": "python_calamine",
    }[format_name]
    parser: dict[str, object] = {"parser": parser_name}
    if result.parser_version is not None:
        parser["parser_version"] = result.parser_version
    if result.embedded_version is not None:
        parser["embedded_version"] = result.embedded_version
    if result.package_revision is not None:
        parser["package_revision"] = result.package_revision

    counts: dict[str, object] = {
        "text_bytes": len(result.text.encode("utf-8", errors="strict")),
        "text_chars": len(result.text),
    }
    units: tuple[ProvenanceUnit, ...] = ()
    if format_name == "pdf":
        counts.update(
            page_count=len(result.page_char_counts),
            text_page_count=result.text_page_count,
        )
        units = _units_from_counts(
            (("page", f"page-{index}", count)
             for index, count in enumerate(result.page_char_counts, start=1))
        )
    elif format_name in {"docx", "xlsx", "pptx"}:
        counts.update(
            logical_unit_count=result.logical_unit_count,
            primary_unit_count=result.primary_unit_count,
            member_count=result.member_count,
            expanded_bytes=result.expanded_bytes,
        )
        units = _units_from_counts(
            ((unit.kind, unit.label, unit.char_count) for unit in result.ooxml_units)
        )
    elif format_name == "doc":
        counts["logical_unit_count"] = result.logical_unit_count
        units = _units_from_counts(
            ((unit.kind, unit.label, unit.char_count) for unit in result.legacy_units)
        )
    elif format_name == "xls":
        counts.update(
            logical_unit_count=result.logical_unit_count,
            primary_unit_count=result.primary_unit_count,
            worksheet_count=result.worksheet_count,
            skipped_sheet_count=result.skipped_sheet_count,
            dense_cell_count=result.dense_cell_count,
        )
        units = _units_from_counts(
            ((unit.kind, unit.label, unit.char_count) for unit in result.xls_units)
        )
    evidence = ExtractionCheckpointEvidence(
        encoding=result.encoding,
        parser_identity=parser,
        extraction_counts=counts,
        provenance=units,
    )
    _parser_identity(evidence.parser_identity)
    _extraction_counts(evidence.extraction_counts)
    _require_provenance_format(format_name, evidence.provenance)
    _require_provenance_counts(
        format_name, evidence.provenance, evidence.extraction_counts,
    )
    if evidence.provenance and evidence.provenance[-1].end != len(result.text):
        raise ValueError("extraction unit counts do not match validated text")
    return evidence


def claim_next_file(
    fence: LeaseFence, *, now_utc: str | None = None, path: Path | None = None,
) -> FileClaim | None:
    """Claim the next pending file under the exact worker generation."""
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> FileClaim | None:
        _require_running_fence(conn, fence)
        row = conn.execute(
            "SELECT file_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,"
            "inode,mode,sha256,stage FROM analyst_files WHERE run_id=? "
            "AND work_state='pending' ORDER BY ordinal LIMIT 1",
            (fence.run_id,),
        ).fetchone()
        if row is None:
            return None
        cursor = conn.execute(
            "UPDATE analyst_files SET work_state='active',active_generation=?,"
            "updated_at_utc=?,revision=revision+1 WHERE file_id=? AND run_id=? "
            "AND work_state='pending'",
            (fence.generation, timestamp, int(row["file_id"]), fence.run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("Analyst file claim lost its compare-and-set")
        return FileClaim(
            file_id=int(row["file_id"]), ordinal=int(row["ordinal"]),
            relative_path=str(row["relative_path"]), size=int(row["size"]),
            mtime_ns=int(row["mtime_ns"]), ctime_ns=int(row["ctime_ns"]),
            device=int(row["device"]), inode=int(row["inode"]),
            mode=int(row["mode"]), sha256=str(row["sha256"]),
            stage=FileStage(str(row["stage"])),
        )

    return run_immediate(operation, path=path)


def advance_file_stage(
    fence: LeaseFence,
    file_id: int,
    target: FileStage,
    *,
    format_name: str | None = None,
    authenticated_format_name: str | None = None,
    encoding: str | None = None,
    parser_identity: Mapping[str, object] | None = None,
    extraction_meta: Mapping[str, object] | None = None,
    provenance: Iterable[ProvenanceUnit] = (),
    selected_for_model: bool | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Advance exactly one durable stage without persisting extracted text."""
    if type(file_id) is not int or file_id <= 0 or not isinstance(target, FileStage):
        raise ValueError("file checkpoint identity or stage is invalid")
    timestamp = _timestamp(now_utc)
    parser_json: str | None = None
    parser_sha: str | None = None
    extraction_json: str | None = None
    provenance_units = _bounded_provenance(provenance)
    if target is FileStage.FORMAT_IDENTIFIED:
        if format_name not in _FORMAT_NAMES | _FORMAT_CANDIDATES:
            raise ValueError("format checkpoint requires a closed format name")
        if any(value is not None for value in (
            authenticated_format_name, encoding, parser_identity, extraction_meta,
            selected_for_model,
        )) or provenance_units:
            raise ValueError("format checkpoint received later-stage evidence")
    elif target is FileStage.TEXT_EXTRACTED:
        if format_name is not None or selected_for_model is not None:
            raise ValueError("text checkpoint received another stage's evidence")
        if (
            authenticated_format_name is not None
            and authenticated_format_name not in _FORMAT_NAMES
        ):
            raise ValueError("authenticated format is not in the closed vocabulary")
        if encoding is not None and encoding not in _ENCODINGS:
            raise ValueError("text checkpoint encoding is not in the closed vocabulary")
        parser_json, parser_sha = _parser_identity(parser_identity)
        extraction_json = _extraction_counts(extraction_meta)
        text_chars = int(extraction_meta["text_chars"])
        if provenance_units and (
            provenance_units[0].start != 0 or provenance_units[-1].end != text_chars
        ):
            raise ValueError("provenance does not cover the exact extracted text span")
    elif target in {
        FileStage.SELECTED_FOR_MODEL,
        FileStage.MODEL_REVIEWED,
        FileStage.MODEL_RESPONSE_VALID,
    }:
        if any(value is not None for value in (
            format_name, encoding, parser_identity, extraction_meta,
            selected_for_model,
        )) or provenance_units:
            raise ValueError("stage checkpoint received evidence owned by another stage")
    else:
        raise ValueError("this stage requires its dedicated atomic checkpoint")

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        source = FileStage(str(row["stage"]))
        require_stage_advance(source, target)
        if target is FileStage.FORMAT_IDENTIFIED:
            cursor = conn.execute(
                "UPDATE analyst_files SET stage=?,format_name=?,"
                "updated_at_utc=?,revision=revision+1 "
                "WHERE file_id=? AND run_id=? AND work_state='active' "
                "AND active_generation=? AND stage=? AND format_name IS NULL",
                (
                    target.value, format_name, timestamp, file_id, fence.run_id,
                    fence.generation, source.value,
                ),
            )
        elif target is FileStage.TEXT_EXTRACTED:
            resolved_format = _resolved_format(
                str(row["format_name"]), authenticated_format_name,
            )
            _require_provenance_format(resolved_format, provenance_units)
            _require_provenance_counts(
                resolved_format, provenance_units, extraction_meta,
            )
            conn.executemany(
                "INSERT INTO analyst_provenance_units("
                "file_id,ordinal,kind,label,start_char,end_char) VALUES(?,?,?,?,?,?)",
                (
                    (file_id, ordinal, unit.kind, unit.label, unit.start, unit.end)
                    for ordinal, unit in enumerate(provenance_units)
                ),
            )
            cursor = conn.execute(
                "UPDATE analyst_files SET stage=?,format_name=?,encoding=?,parser_identity_json=?,"
                "parser_identity_sha256=?,extraction_meta_json=?,"
                "updated_at_utc=?,revision=revision+1 "
                "WHERE file_id=? AND run_id=? AND work_state='active' "
                "AND active_generation=? AND stage=? AND parser_identity_json IS NULL "
                "AND parser_identity_sha256 IS NULL AND extraction_meta_json IS NULL",
                (
                    target.value, resolved_format, encoding, parser_json, parser_sha,
                    extraction_json,
                    timestamp, file_id, fence.run_id, fence.generation, source.value,
                ),
            )
        else:
            if target is FileStage.SELECTED_FOR_MODEL and row["selected_for_model"] != 1:
                raise CheckpointError("file was not selected for model review")
            if target is FileStage.MODEL_RESPONSE_VALID:
                _require_all_chunks_valid(conn, file_id)
            cursor = conn.execute(
                "UPDATE analyst_files SET stage=?,updated_at_utc=?,revision=revision+1 "
                "WHERE file_id=? AND run_id=? AND work_state='active' "
                "AND active_generation=? AND stage=?",
                (
                    target.value, timestamp, file_id, fence.run_id,
                    fence.generation, source.value,
                ),
            )
        if cursor.rowcount != 1:
            raise CheckpointError("Analyst file stage compare-and-set failed")

    run_immediate(operation, path=path)


def checkpoint_detector(
    fence: LeaseFence,
    file_id: int,
    hits: Iterable[DetectorHit],
    *,
    selected_for_model: bool,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Commit the complete deterministic hit set and selection together."""
    timestamp = _timestamp(now_utc)
    materialized_list: list[DetectorHit] = []
    for hit in hits:
        if not isinstance(hit, DetectorHit):
            raise TypeError("detector checkpoint contains an invalid hit")
        if len(materialized_list) >= MAX_DETECTOR_HITS:
            terminalize_file(
                fence,
                file_id,
                FileTerminal.DETECTOR_OUTPUT_LIMIT,
                detail="detector_hit_limit",
                now_utc=timestamp,
                path=path,
            )
            raise DetectorHitLimit("detector hit count exceeded its durable cap")
        materialized_list.append(hit)
    materialized = tuple(materialized_list)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        if FileStage(str(row["stage"])) is not FileStage.TEXT_EXTRACTED:
            raise CheckpointError("detector checkpoint requires extracted-text stage")
        _validate_detector_spans(materialized, _text_chars(row))
        existing = conn.execute(
            "SELECT 1 FROM analyst_detector_hits WHERE file_id=? LIMIT 1", (file_id,)
        ).fetchone()
        if existing is not None:
            raise CheckpointError("detector hits are immutable once written")
        conn.executemany(
            "INSERT INTO analyst_detector_hits("
            "file_id,ordinal,kind,value,start_char,end_char) VALUES(?,?,?,?,?,?)",
            (
                (file_id, ordinal, hit.kind, hit.value, hit.start, hit.end)
                for ordinal, hit in enumerate(materialized)
            ),
        )
        cursor = conn.execute(
            "UPDATE analyst_files SET stage='detector_scanned',selected_for_model=?,"
            "updated_at_utc=?,revision=revision+1 WHERE file_id=? AND run_id=? "
            "AND work_state='active' AND active_generation=? "
            "AND stage='text_extracted'",
            (int(selected_for_model), timestamp, file_id, fence.run_id, fence.generation),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("detector checkpoint compare-and-set failed")

    run_immediate(operation, path=path)


def store_chunks(
    fence: LeaseFence,
    file_id: int,
    chunks: Iterable[ChunkSpec],
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Persist only deterministic chunk boundaries and hashes, never text."""
    materialized = tuple(chunks)
    if tuple(item.index for item in materialized) != tuple(range(len(materialized))):
        raise ValueError("chunk indexes must be canonical and contiguous")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        if FileStage(str(row["stage"])) is not FileStage.SELECTED_FOR_MODEL:
            raise CheckpointError("chunk persistence requires selected-model stage")
        if conn.execute(
            "SELECT 1 FROM analyst_chunks WHERE file_id=? LIMIT 1", (file_id,)
        ).fetchone() is not None:
            raise CheckpointError("durable chunk identities are immutable")
        config = conn.execute(
            "SELECT chunk_chars,overlap_chars FROM analyst_runs WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if config is None:
            raise CheckpointError("Analyst run disappeared during chunk persistence")
        _validate_chunk_shape(materialized, int(config[0]), int(config[1]))
        _require_chunk_coverage(materialized, _text_chars(row))
        conn.executemany(
            "INSERT INTO analyst_chunks("
            "file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
            "VALUES(?,?,?,?,?,'pending')",
            (
                (file_id, item.index, item.start, item.end, item.sha256)
                for item in materialized
            ),
        )
        conn.execute(
            "UPDATE analyst_files SET updated_at_utc=?,revision=revision+1 "
            "WHERE file_id=?", (timestamp, file_id),
        )

    run_immediate(operation, path=path)


def verify_chunks(
    fence: LeaseFence,
    file_id: int,
    chunks: Iterable[ChunkSpec],
    *,
    path: Path | None = None,
) -> None:
    """Require regenerated resume chunks to match every durable identity."""
    materialized = tuple(chunks)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        config = conn.execute(
            "SELECT chunk_chars,overlap_chars FROM analyst_runs WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if config is None:
            raise CheckpointError("Analyst run disappeared during chunk verification")
        _validate_chunk_shape(materialized, int(config[0]), int(config[1]))
        _require_chunk_coverage(materialized, _text_chars(row))
        rows = conn.execute(
            "SELECT chunk_index,start_char,end_char,chunk_sha256 "
            "FROM analyst_chunks WHERE file_id=? ORDER BY chunk_index",
            (file_id,),
        ).fetchall()
        expected = tuple(
            (item.index, item.start, item.end, item.sha256) for item in materialized
        )
        observed = tuple(
            (int(row[0]), int(row[1]), int(row[2]), str(row[3])) for row in rows
        )
        if observed != expected:
            raise CheckpointError("regenerated chunks do not match durable identities")

    run_immediate(operation, path=path)


def precharge_attempt(
    fence: LeaseFence,
    chunk_id: int,
    request_sha256: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> tuple[str, int]:
    """Durably charge one of at most two attempts before any HTTP contact."""
    _require_sha(request_sha256, "request sha256")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> tuple[str, int]:
        _require_running_fence(conn, fence)
        if conn.execute(
            "SELECT 1 FROM analyst_ollama_contacts "
            "WHERE run_id=? AND chunk_id=? AND state='dispatching' LIMIT 1",
            (fence.run_id, chunk_id),
        ).fetchone() is not None:
            raise CheckpointError("chunk already has a charged Ollama contact")
        row = conn.execute(
            "SELECT c.state,f.work_state,f.active_generation FROM analyst_chunks c "
            "JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE c.chunk_id=? AND f.run_id=?",
            (chunk_id, fence.run_id),
        ).fetchone()
        if (row is None or str(row["state"]) != "pending"
                or str(row["work_state"]) != "active"
                or int(row["active_generation"]) != fence.generation):
            raise CheckpointError("chunk is not dispatchable by this worker")
        attempts = conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts "
            "WHERE chunk_id=? ORDER BY attempt_no", (chunk_id,),
        ).fetchall()
        if attempts and str(attempts[-1]["state"]) == "dispatching":
            raise CheckpointError("chunk already has a charged live attempt")
        attempt_no = len(attempts) + 1
        if attempt_no > 2:
            raise CheckpointError("chunk exhausted its two-attempt budget")
        attempt_id = hashlib.sha256(
            f"{chunk_id}\0{attempt_no}\0{request_sha256}".encode("ascii")
        ).hexdigest()
        conn.execute(
            "INSERT INTO analyst_model_attempts("
            "attempt_id,chunk_id,attempt_no,request_sha256,state,charged_at_utc) "
            "VALUES(?,?,?,?,'dispatching',?)",
            (attempt_id, chunk_id, attempt_no, request_sha256, timestamp),
        )
        return attempt_id, attempt_no

    return run_immediate(operation, path=path)


def finish_attempt_failure(
    fence: LeaseFence,
    attempt_id: str,
    state: AttemptState,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Close a charged failure; terminalize the chunk only after attempt two."""
    allowed = {
        AttemptState.SCHEMA_INVALID,
        AttemptState.MODEL_TIMEOUT,
        AttemptState.MODEL_TRANSPORT_ERROR,
    }
    if state not in allowed:
        raise ValueError("attempt failure state is not worker-reportable")
    timestamp = _timestamp(now_utc)
    chunk_state = {
        AttemptState.SCHEMA_INVALID: "model_invalid",
        AttemptState.MODEL_TIMEOUT: "model_timeout",
        AttemptState.MODEL_TRANSPORT_ERROR: "model_transport_error",
    }[state]

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _attempt_owned_by_fence(conn, fence, attempt_id)
        if conn.execute(
            "SELECT 1 FROM analyst_ollama_contacts "
            "WHERE attempt_id=? AND state='success' LIMIT 1", (attempt_id,),
        ).fetchone() is not None:
            raise CheckpointError(
                "successful Ollama contact requires valid checkpoint or recovery"
            )
        cursor = conn.execute(
            "UPDATE analyst_model_attempts SET state=?,finished_at_utc=?,failure_code=? "
            "WHERE attempt_id=? AND state='dispatching'",
            (state.value, timestamp, state.value, attempt_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("attempt is no longer dispatching")
        if int(row["attempt_no"]) == 2:
            conn.execute(
                "UPDATE analyst_chunks SET state=? WHERE chunk_id=? AND state='pending'",
                (chunk_state, int(row["chunk_id"])),
            )

    run_immediate(operation, path=path)


def finish_valid_attempt(
    fence: LeaseFence,
    attempt_id: str,
    result: WorksheetResult,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Commit only normalized, grounded model evidence as one checkpoint."""
    if not isinstance(result, WorksheetResult):
        raise TypeError("valid attempt requires a normalized worksheet result")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _attempt_owned_by_fence(conn, fence, attempt_id)
        chunk_id = int(row["chunk_id"])
        _validate_worksheet_result(
            result, int(row["end_char"]) - int(row["start_char"]),
        )
        conn.executemany(
            "INSERT INTO analyst_model_findings("
            "chunk_id,ordinal,category,quote,model_offset,canonical_offset,"
            "canonical_end,match_count,model_offset_exact) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                (
                    chunk_id, ordinal, finding.category.value, finding.quote,
                    finding.model_offset, finding.canonical_offset,
                    finding.canonical_end, finding.match_count,
                    int(finding.model_offset_exact),
                )
                for ordinal, finding in enumerate(result.findings)
            ),
        )
        cursor = conn.execute(
            "UPDATE analyst_model_attempts SET state='valid',finished_at_utc=? "
            "WHERE attempt_id=? AND state='dispatching'",
            (timestamp, attempt_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("attempt is no longer dispatching")
        cursor = conn.execute(
            "UPDATE analyst_chunks SET state='model_response_valid',"
            "accepted_attempt_id=?,document_type=?,subject=?,assessment=?,"
            "raw_finding_count=?,removed_duplicate_count=?,"
            "dropped_ungrounded_count=? WHERE chunk_id=? AND state='pending'",
            (
                attempt_id, result.document_type, result.subject,
                result.model_assessment.value, result.raw_finding_count,
                result.removed_duplicate_count, result.dropped_ungrounded_count,
                chunk_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("chunk is no longer pending")

    run_immediate(operation, path=path)


def terminalize_file(
    fence: LeaseFence,
    file_id: int,
    terminal: FileTerminal,
    *,
    detail: str | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Write one immutable per-file terminal under the exact worker fence."""
    if not isinstance(terminal, FileTerminal):
        raise TypeError("file terminal must use the closed enum")
    if detail not in _TERMINAL_DETAILS[terminal]:
        raise ValueError("file terminal detail is not allowed for this terminal")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence, allow_finalizing=True)
        row = _active_file(conn, fence, file_id)
        _require_terminal_semantics(conn, row, terminal)
        cursor = conn.execute(
            "UPDATE analyst_files SET work_state='terminal',terminal_code=?,"
            "terminal_detail=?,active_generation=NULL,updated_at_utc=?,"
            "revision=revision+1 WHERE file_id=? AND run_id=? "
            "AND work_state='active' AND active_generation=?",
            (terminal.value, detail, timestamp, file_id, fence.run_id, fence.generation),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("file terminal compare-and-set failed")

    run_immediate(operation, path=path)


def begin_finalization(
    fence: LeaseFence,
    finalization_token: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Enter finalizing only after every discovered file is terminal."""
    _require_sha(finalization_token, "finalization token")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        incomplete = conn.execute(
            "SELECT 1 FROM analyst_files WHERE run_id=? AND work_state!='terminal' LIMIT 1",
            (fence.run_id,),
        ).fetchone()
        dispatching = conn.execute(
            "SELECT 1 FROM analyst_model_attempts a JOIN analyst_chunks c "
            "ON c.chunk_id=a.chunk_id JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=? AND a.state='dispatching' LIMIT 1",
            (fence.run_id,),
        ).fetchone()
        dispatching_contact = conn.execute(
            "SELECT 1 FROM analyst_ollama_contacts "
            "WHERE run_id=? AND state='dispatching' LIMIT 1",
            (fence.run_id,),
        ).fetchone()
        schedule = conn.execute(
            "SELECT state FROM analyst_ollama_schedule WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if (
            incomplete is not None
            or dispatching is not None
            or dispatching_contact is not None
            or schedule is None
            or str(schedule["state"]) != "available"
        ):
            raise CheckpointError("run cannot finalize with incomplete work")
        _require_run_terminal_semantics(conn, fence.run_id)
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='finalizing',finalization_token=?,"
            "updated_at_utc=?,revision=revision+1 WHERE run_id=? AND state='running'",
            (finalization_token, timestamp, fence.run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("run finalization compare-and-set failed")

    run_immediate(operation, path=path)


def finish_finalization(
    fence: LeaseFence,
    finalization_token: str,
    report_manifest_sha256: str,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Atomically publish durable completion evidence and clear the lease."""
    _require_sha(finalization_token, "finalization token")
    _require_sha(report_manifest_sha256, "report manifest sha256")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence, allow_finalizing=True)
        no_supported_content = _require_run_terminal_semantics(conn, fence.run_id)
        completion = (
            "complete_no_supported_content" if no_supported_content else "complete"
        )
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='complete',completion_code=?,"
            "finished_at_utc=?,updated_at_utc=?,report_manifest_sha256=?,"
            "revision=revision+1 WHERE run_id=? AND state='finalizing' "
            "AND finalization_token=?",
            (
                completion, timestamp, timestamp, report_manifest_sha256,
                fence.run_id, finalization_token,
            ),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("finalization token or run state no longer matches")
        cursor = conn.execute(
            "UPDATE analyst_gpu_lease SET generation=generation+1,run_id=NULL,"
            "owner_token=NULL,pid=NULL,start_ticks=NULL,boot_id=NULL,"
            "heartbeat_monotonic_ns=NULL,claimed_at_utc=NULL,heartbeat_at_utc=NULL "
            "WHERE slot=1 AND " + _FENCE_WHERE,
            _fence_values(fence),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("lease changed during finalization")

    run_immediate(operation, path=path)


def _require_running_fence(
    conn: sqlite3.Connection, fence: LeaseFence, *, allow_finalizing: bool = False,
) -> None:
    lease = conn.execute(
        "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND " + _FENCE_WHERE,
        _fence_values(fence),
    ).fetchone()
    allowed = ("running", "finalizing") if allow_finalizing else ("running",)
    placeholders = ",".join("?" for _ in allowed)
    run = conn.execute(
        f"SELECT 1 FROM analyst_runs WHERE run_id=? AND state IN ({placeholders})",
        (fence.run_id, *allowed),
    ).fetchone()
    if lease is None or run is None:
        raise CheckpointError("worker lease or run state no longer authorizes writes")


def _active_file(
    conn: sqlite3.Connection, fence: LeaseFence, file_id: int,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM analyst_files WHERE file_id=? AND run_id=? "
        "AND work_state='active' AND active_generation=?",
        (file_id, fence.run_id, fence.generation),
    ).fetchone()
    if row is None:
        raise CheckpointError("file is not active under this worker generation")
    return row


def _attempt_owned_by_fence(
    conn: sqlite3.Connection, fence: LeaseFence, attempt_id: str,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT a.chunk_id,a.attempt_no,c.start_char,c.end_char "
        "FROM analyst_model_attempts a "
        "JOIN analyst_chunks c ON c.chunk_id=a.chunk_id "
        "JOIN analyst_files f ON f.file_id=c.file_id "
        "WHERE a.attempt_id=? AND a.state='dispatching' AND f.run_id=? "
        "AND f.work_state='active' AND f.active_generation=?",
        (attempt_id, fence.run_id, fence.generation),
    ).fetchone()
    if row is None:
        raise CheckpointError("attempt is not dispatching under this worker fence")
    return row


def _require_all_chunks_valid(conn: sqlite3.Connection, file_id: int) -> None:
    row = conn.execute(
        "SELECT count(*),sum(state='model_response_valid') "
        "FROM analyst_chunks WHERE file_id=?", (file_id,),
    ).fetchone()
    total = 0 if row is None else int(row[0])
    valid = 0 if row is None or row[1] is None else int(row[1])
    if total == 0 or valid != total:
        raise CheckpointError("file does not have a complete valid chunk set")


def _require_terminal_semantics(
    conn: sqlite3.Connection, row: sqlite3.Row, terminal: FileTerminal,
) -> None:
    stage = FileStage(str(row["stage"]))
    selected = row["selected_for_model"]
    file_id = int(row["file_id"])
    if terminal is FileTerminal.COMPLETE_DETECTOR_ONLY:
        if stage is not FileStage.DETECTOR_SCANNED or selected != 0:
            raise CheckpointError("detector-only terminal contradicts file coverage")
        if conn.execute(
            "SELECT 1 FROM analyst_chunks WHERE file_id=? LIMIT 1", (file_id,),
        ).fetchone() is not None:
            raise CheckpointError("detector-only terminal cannot have model chunks")
    elif terminal is FileTerminal.COMPLETE_MODEL_REVIEWED:
        if stage is not FileStage.MODEL_RESPONSE_VALID or selected != 1:
            raise CheckpointError("model-reviewed terminal contradicts file coverage")
        _require_all_chunks_valid(conn, file_id)
    elif terminal is FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT:
        if stage is not FileStage.TEXT_EXTRACTED or selected is not None:
            raise CheckpointError("no-content terminal contradicts file coverage")
        try:
            counts = json.loads(str(row["extraction_meta_json"]))
        except (TypeError, ValueError):
            raise CheckpointError("no-content terminal lacks extraction evidence") from None
        if counts.get("text_chars") != 0 or counts.get("text_bytes") != 0:
            raise CheckpointError("no-content terminal has extracted content")
        if conn.execute(
            "SELECT 1 FROM analyst_detector_hits WHERE file_id=? LIMIT 1", (file_id,),
        ).fetchone() is not None:
            raise CheckpointError("no-content terminal cannot retain detector hits")
        if conn.execute(
            "SELECT 1 FROM analyst_provenance_units WHERE file_id=? LIMIT 1", (file_id,),
        ).fetchone() is not None:
            raise CheckpointError("no-content terminal cannot retain provenance units")
        if conn.execute(
            "SELECT 1 FROM analyst_chunks WHERE file_id=? LIMIT 1", (file_id,),
        ).fetchone() is not None:
            raise CheckpointError("no-content terminal cannot retain model chunks")
    elif terminal in {
        FileTerminal.MODEL_INVALID,
        FileTerminal.MODEL_TIMEOUT,
        FileTerminal.MODEL_TRANSPORT_ERROR,
    }:
        if stage is not FileStage.MODEL_REVIEWED or selected != 1:
            raise CheckpointError("model failure terminal contradicts file coverage")
        pending = conn.execute(
            "SELECT 1 FROM analyst_chunks WHERE file_id=? AND state='pending' LIMIT 1",
            (file_id,),
        ).fetchone()
        if pending is not None:
            raise CheckpointError("model failure terminal has unfinished chunks")
    elif terminal in _DISCOVERED_TERMINALS:
        if stage is not FileStage.DISCOVERED:
            raise CheckpointError("pre-extraction terminal contradicts file coverage")
    elif terminal in _FORMAT_FAILURE_TERMINALS:
        if stage is not FileStage.FORMAT_IDENTIFIED:
            raise CheckpointError("parser terminal contradicts file coverage")
    elif terminal is FileTerminal.DETECTOR_OUTPUT_LIMIT:
        if stage is not FileStage.TEXT_EXTRACTED:
            raise CheckpointError("detector-limit terminal contradicts file coverage")
    elif terminal is FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY:
        pass
    elif terminal is FileTerminal.CANCELLED_ABANDONED:
        raise CheckpointError("only explicit abandon may write this terminal")
    else:
        raise CheckpointError("file terminal has no frozen stage contract")


def _require_run_terminal_semantics(
    conn: sqlite3.Connection, run_id: str,
) -> bool:
    rows = conn.execute(
        "SELECT * FROM analyst_files WHERE run_id=? ORDER BY ordinal", (run_id,),
    ).fetchall()
    for row in rows:
        if str(row["work_state"]) != "terminal" or row["terminal_code"] is None:
            raise CheckpointError("run contains a nonterminal discovered file")
        _require_terminal_semantics(
            conn, row, FileTerminal(str(row["terminal_code"])),
        )
    return not rows or all(
        str(row["terminal_code"]) == FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT.value
        for row in rows
    )


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation, fence.run_id, fence.owner_token, fence.process.pid,
        fence.process.start_ticks, fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _parser_identity(
    value: Mapping[str, object] | None,
) -> tuple[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("text checkpoint requires parser identity")
    if not set(value).issubset(_PARSER_IDENTITY_KEYS) or "parser" not in value:
        raise ValueError("parser identity contains an unknown or missing field")
    if any(
        not isinstance(item, str) or _IDENTITY_VALUE_RE.fullmatch(item) is None
        for item in value.values()
    ):
        raise ValueError("parser identity contains an unsafe value")
    body = _canonical_json(value)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _extraction_counts(value: Mapping[str, object] | None) -> str:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("text checkpoint requires bounded extraction counts")
    if not set(value).issubset(_EXTRACTION_COUNT_KEYS):
        raise ValueError("extraction metadata contains a non-count field")
    if "text_chars" not in value or "text_bytes" not in value:
        raise ValueError("extraction metadata is missing exact text counts")
    if any(type(item) is not int or item < 0 for item in value.values()):
        raise ValueError("extraction metadata counts must be nonnegative integers")
    return _canonical_json(value)


def _canonical_json(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise TypeError("checkpoint metadata must be a mapping")
    try:
        body = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("checkpoint metadata is not canonical JSON data") from exc
    if not body or len(body) > 65_536:
        raise ValueError("checkpoint metadata exceeds its JSON bound")
    return body


def _resolved_format(stored: str, authenticated: str | None) -> str:
    """Return one exact format, permitting only candidate-to-subtype refinement."""
    if stored in _FORMAT_NAMES:
        if authenticated is not None and authenticated != stored:
            raise CheckpointError("authenticated format contradicts the durable format")
        return stored
    allowed = {
        "ooxml": frozenset({"docx", "xlsx", "pptx"}),
        "legacy_office": frozenset({"doc", "xls"}),
    }.get(stored)
    if allowed is None or authenticated not in allowed:
        raise CheckpointError("format candidate was not authentically refined")
    assert authenticated is not None
    return authenticated


def _require_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hex characters")


def _validate_chunk_shape(
    chunks: tuple[ChunkSpec, ...], chunk_chars: int, overlap_chars: int,
) -> None:
    if not chunks:
        return
    if chunks[0].start != 0:
        raise ValueError("first chunk must begin at source offset zero")
    stride = chunk_chars - overlap_chars
    for position, item in enumerate(chunks):
        length = item.end - item.start
        if length > chunk_chars:
            raise ValueError("chunk exceeds the frozen character window")
        if position < len(chunks) - 1:
            following = chunks[position + 1]
            if length != chunk_chars or following.start != item.start + stride:
                raise ValueError("chunk overlap does not match the frozen window")


def _require_chunk_coverage(
    chunks: tuple[ChunkSpec, ...], text_chars: int,
) -> None:
    if text_chars == 0:
        if chunks:
            raise ValueError("empty extracted text cannot have model chunks")
        return
    if not chunks or chunks[-1].end != text_chars:
        raise ValueError("chunks do not cover the exact extracted text span")


def _text_chars(row: sqlite3.Row) -> int:
    try:
        metadata = json.loads(str(row["extraction_meta_json"]))
        value = metadata["text_chars"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointError("file lacks valid extracted-text evidence") from None
    if type(value) is not int or value < 0:
        raise CheckpointError("file has invalid extracted-text length evidence")
    return value


def _validate_detector_spans(
    hits: tuple[DetectorHit, ...], text_chars: int,
) -> None:
    for hit in hits:
        if (
            not isinstance(hit.kind, str)
            or hit.kind not in _DETECTOR_KINDS
            or not isinstance(hit.value, str)
            or not hit.value
            or _has_unsafe_controls(hit.value)
            or type(hit.start) is not int
            or type(hit.end) is not int
            or hit.start < 0
            or hit.end <= hit.start
            or hit.end > text_chars
            or len(hit.value) != hit.end - hit.start
        ):
            raise ValueError("detector hit is not valid extracted-text evidence")


def _validate_worksheet_result(result: WorksheetResult, chunk_chars: int) -> None:
    if (
        not isinstance(result.document_type, str)
        or not 1 <= len(result.document_type) <= 80
        or _has_unsafe_controls(result.document_type)
        or not isinstance(result.subject, str)
        or len(result.subject) > 160
        or _has_unsafe_controls(result.subject)
    ):
        raise ValueError("worksheet classification text is invalid")
    counts = (
        result.raw_finding_count,
        result.removed_duplicate_count,
        result.dropped_ungrounded_count,
    )
    if (
        any(type(value) is not int or value < 0 for value in counts)
        or result.raw_finding_count > 16
        or len(result.findings)
        != result.raw_finding_count
        - result.removed_duplicate_count
        - result.dropped_ungrounded_count
    ):
        raise ValueError("worksheet finding counts are inconsistent")
    if not isinstance(result.model_assessment, Assessment):
        raise ValueError("worksheet assessment is not in the closed vocabulary")
    if bool(result.findings) != (
        result.model_assessment is Assessment.FINDINGS_PRESENT
    ):
        raise ValueError("worksheet assessment contradicts retained findings")
    if type(result.findings) is not tuple:
        raise TypeError("worksheet findings must be an immutable tuple")
    identities: set[tuple[Category, str]] = set()
    for finding in result.findings:
        if not isinstance(finding, GroundedFinding):
            raise TypeError("worksheet contains an invalid grounded finding")
        if (
            not isinstance(finding.category, Category)
            or not isinstance(finding.quote, str)
            or not 1 <= len(finding.quote) <= 240
            or _has_unsafe_controls(finding.quote, allow_whitespace=True)
            or type(finding.canonical_offset) is not int
            or type(finding.canonical_end) is not int
            or finding.canonical_offset < 0
            or finding.canonical_end <= finding.canonical_offset
            or finding.canonical_end > chunk_chars
            or finding.canonical_end - finding.canonical_offset != len(finding.quote)
            or type(finding.match_count) is not int
            or finding.match_count < 1
            or type(finding.model_offset) is not int
            or finding.model_offset < 0
            or type(finding.model_offset_exact) is not bool
            or finding.model_offset_exact
            and finding.model_offset + len(finding.quote) > chunk_chars
        ):
            raise ValueError("worksheet grounded finding is inconsistent")
        identity = (finding.category, finding.quote)
        if identity in identities:
            raise ValueError("worksheet grounded finding is inconsistent")
        identities.add(identity)


def _has_unsafe_controls(value: str, *, allow_whitespace: bool = False) -> bool:
    allowed = "\t\n\r" if allow_whitespace else ""
    return any(
        char == "\x00"
        or ord(char) < 32 and char not in allowed
        or 127 <= ord(char) < 160
        for char in value
    )


def _bounded_provenance(
    values: Iterable[ProvenanceUnit],
) -> tuple[ProvenanceUnit, ...]:
    units: list[ProvenanceUnit] = []
    identities: set[tuple[str, str]] = set()
    prior_end = 0
    for position, unit in enumerate(values):
        if not isinstance(unit, ProvenanceUnit):
            raise TypeError("provenance contains an invalid unit")
        if len(units) >= MAX_PROVENANCE_UNITS:
            raise ValueError("provenance unit count exceeded its durable cap")
        identity = (unit.kind, unit.label)
        expected_start = 0 if position == 0 else prior_end + 1
        if identity in identities or unit.start != expected_start:
            raise ValueError("provenance identities or source order are not canonical")
        identities.add(identity)
        prior_end = unit.end
        units.append(unit)
    return tuple(units)


def _units_from_counts(
    values: Iterable[tuple[str, str, int]],
) -> tuple[ProvenanceUnit, ...]:
    units: list[ProvenanceUnit] = []
    cursor = 0
    for kind, label, count in values:
        if type(count) is not int or count < 0:
            raise ValueError("extraction unit character count is invalid")
        units.append(ProvenanceUnit(kind, label, cursor, cursor + count))
        cursor += count + 1
    return _bounded_provenance(units)


def _require_provenance_format(
    format_name: str, units: tuple[ProvenanceUnit, ...],
) -> None:
    allowed = {
        "pdf": {"page"},
        "docx": {"paragraph"},
        "xlsx": {"cell"},
        "pptx": {"slide", "notes", "comments"},
        "doc": {"output_line"},
        "xls": {"cell"},
        "text": {"output_line"},
        "rtf": {"output_line"},
    }[format_name]
    if any(unit.kind not in allowed for unit in units):
        raise ValueError("provenance kind does not match authenticated format")
    if any(not _canonical_provenance_label(format_name, unit) for unit in units):
        raise ValueError("provenance label is not canonical for its format")


def _canonical_provenance_label(
    format_name: str, unit: ProvenanceUnit,
) -> bool:
    if format_name == "pdf":
        match = re.fullmatch(r"page-([1-9][0-9]{0,4})", unit.label)
        return unit.kind == "page" and match is not None and int(match.group(1)) <= 10_000
    if format_name == "docx":
        prefix, marker, number = unit.label.rpartition("#p")
        segments = prefix.split("/")
        return bool(
            unit.kind == "paragraph"
            and marker
            and number.isascii()
            and number.isdigit()
            and number[0] != "0"
            and int(number) <= 100_000
            and (
                prefix == "main"
                or (
                    prefix.startswith("word/")
                    and prefix.endswith(".xml")
                    and all(segment not in {"", ".", ".."} for segment in segments)
                )
            )
        )
    if format_name in {"xlsx", "xls"}:
        match = re.fullmatch(
            r"sheet-([1-9][0-9]{0,2})!([A-Z]{1,3})([1-9][0-9]{0,6})",
            unit.label,
        )
        if unit.kind != "cell" or match is None:
            return False
        sheet, column, row = match.groups()
        column_number = 0
        for char in column:
            column_number = column_number * 26 + ord(char) - 64
        column_cap, row_cap = (16_384, 1_048_576) if format_name == "xlsx" else (256, 65_536)
        return int(sheet) <= 256 and column_number <= column_cap and int(row) <= row_cap
    if format_name == "pptx":
        suffix = {"slide": "", "notes": "-notes", "comments": "-comments"}[unit.kind]
        match = re.fullmatch(r"slide-([1-9][0-9]{0,3})" + suffix, unit.label)
        return match is not None and int(match.group(1)) <= 1_000
    if format_name in {"doc", "text", "rtf"}:
        match = re.fullmatch(r"output-line-([1-9][0-9]{0,7})", unit.label)
        return unit.kind == "output_line" and match is not None
    return False


def _require_provenance_counts(
    format_name: str,
    units: tuple[ProvenanceUnit, ...],
    metadata: Mapping[str, object] | None,
) -> None:
    if metadata is None:
        raise ValueError("provenance is missing extraction counts")
    if (
        format_name in {"pdf", "docx", "xlsx", "pptx", "doc", "xls"}
        and int(metadata["text_chars"]) > 0
        and not units
    ):
        raise ValueError("text-bearing extraction is missing provenance units")
    if format_name == "pdf":
        if metadata.get("page_count") != len(units):
            raise ValueError("PDF page provenance count does not match")
    elif format_name in {"docx", "xlsx", "pptx", "doc", "xls"}:
        if metadata.get("logical_unit_count") != len(units):
            raise ValueError("logical provenance count does not match")
    elif "logical_unit_count" in metadata:
        if metadata["logical_unit_count"] != len(units):
            raise ValueError("logical provenance count does not match")
    if format_name in {"xlsx", "xls"}:
        sheet_count = metadata.get("primary_unit_count")
        if type(sheet_count) is not int or any(
            int(unit.label.split("!", 1)[0][6:]) > sheet_count for unit in units
        ):
            raise ValueError("cell provenance exceeds the persisted sheet count")
    if format_name == "pptx":
        slide_count = metadata.get("primary_unit_count")
        if type(slide_count) is not int or any(
            int(unit.label.split("-", 2)[1]) > slide_count for unit in units
        ):
            raise ValueError("presentation provenance exceeds the persisted slide count")


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if not isinstance(value, str) or not value:
        raise ValueError("now_utc must be nonempty text")
    return value


__all__ = [
    "CheckpointError",
    "ChunkSpec",
    "DetectorHitLimit",
    "ExtractionCheckpointEvidence",
    "FileClaim",
    "MAX_DETECTOR_HITS",
    "MAX_PROVENANCE_UNITS",
    "ProvenanceUnit",
    "advance_file_stage",
    "begin_finalization",
    "build_extraction_evidence",
    "checkpoint_detector",
    "claim_next_file",
    "finish_attempt_failure",
    "finish_finalization",
    "finish_valid_attempt",
    "precharge_attempt",
    "store_chunks",
    "terminalize_file",
    "verify_chunks",
]
