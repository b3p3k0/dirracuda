"""Fenced durable resume and Phase-1-to-Phase-2 handoff state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .checkpoint import (
    MAX_PROVENANCE_UNITS,
    CheckpointError,
    ChunkSpec,
    ExtractionCheckpointEvidence,
    ProvenanceUnit,
)
from .inventory import InventoryFile
from .lease import LeaseFence
from .models import DetectorHit, FileStage
from .store import run_immediate
from .worker_contract import (
    FileResumeSnapshot,
    MAX_CHUNKS_PER_FILE,
    MAX_DETECTOR_HITS,
    MAX_PHASE1_FILES,
    Phase1ChunkIdentity,
    Phase1FileHandoff,
)


_FENCE_WHERE = (
    "generation=? AND run_id=? AND owner_token=? AND pid=? AND start_ticks=? "
    "AND boot_id=? AND heartbeat_monotonic_ns=?"
)
_PRE_MODEL_STAGES = (
    FileStage.DISCOVERED,
    FileStage.FORMAT_IDENTIFIED,
    FileStage.TEXT_EXTRACTED,
    FileStage.DETECTOR_SCANNED,
)
_SNAPSHOT_STAGES = (*_PRE_MODEL_STAGES, FileStage.SELECTED_FOR_MODEL)
_PARSER_IDENTITY_KEYS = frozenset({
    "parser", "parser_version", "embedded_version", "package_revision",
})
_EXTRACTION_COUNT_KEYS = frozenset({
    "text_bytes", "text_chars", "page_count", "text_page_count",
    "logical_unit_count", "primary_unit_count", "member_count",
    "expanded_bytes", "worksheet_count", "skipped_sheet_count",
    "dense_cell_count",
})
_IDENTITY_VALUE = re.compile(r"[A-Za-z0-9_.:+-]{1,128}\Z", re.ASCII)
_EXACT_FORMATS = frozenset({
    "text", "rtf", "pdf", "docx", "xlsx", "pptx", "doc", "xls",
})
_ENCODINGS = frozenset({
    "rtf", "utf-8", "utf-8-bom", "utf-16-le-bom", "utf-16-be-bom",
    "utf-32-le-bom", "utf-32-be-bom", "windows-1252",
})


class Phase1EvidenceMismatch(CheckpointError):
    """Regenerated private evidence differs from its durable checkpoint."""


def claim_next_phase1_file(
    fence: LeaseFence,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> FileResumeSnapshot | None:
    """Claim only pending work that has not reached the model handoff."""
    _require_fence_type(fence)
    timestamp = _timestamp(now_utc)
    stage_values = tuple(stage.value for stage in _PRE_MODEL_STAGES)
    placeholders = ",".join("?" for _ in stage_values)

    def operation(conn: sqlite3.Connection) -> FileResumeSnapshot | None:
        _require_running_fence(conn, fence)
        row = conn.execute(
            "SELECT file_id FROM analyst_files WHERE run_id=? "
            "AND work_state='pending' "
            f"AND stage IN ({placeholders}) ORDER BY ordinal LIMIT 1",
            (fence.run_id, *stage_values),
        ).fetchone()
        if row is None:
            return None
        file_id = int(row[0])
        cursor = conn.execute(
            "UPDATE analyst_files SET work_state='active',active_generation=?,"
            "updated_at_utc=?,revision=revision+1 WHERE file_id=? AND run_id=? "
            "AND work_state='pending' "
            f"AND stage IN ({placeholders})",
            (
                fence.generation, timestamp, file_id, fence.run_id,
                *stage_values,
            ),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("Phase 1 file claim lost its compare-and-set")
        return _load_snapshot(conn, fence, file_id)

    return run_immediate(operation, path=path)


def load_file_resume_snapshot(
    fence: LeaseFence, file_id: int, *, path: Path | None = None,
) -> FileResumeSnapshot:
    """Load exact content-free resume facts for one active Phase 1 claim."""
    _require_fence_type(fence)
    _require_file_id(file_id)

    def operation(conn: sqlite3.Connection) -> FileResumeSnapshot:
        _require_running_fence(conn, fence)
        return _load_snapshot(conn, fence, file_id)

    return run_immediate(operation, path=path)


def load_phase1_handoff(
    fence: LeaseFence, *, path: Path | None = None,
) -> tuple[Phase1FileHandoff, ...]:
    """Load the ordered pending files already handed off to the model phase."""
    _require_fence_type(fence)

    def operation(conn: sqlite3.Connection) -> tuple[Phase1FileHandoff, ...]:
        _require_running_fence(conn, fence)
        config = conn.execute(
            "SELECT mode,chunk_chars,overlap_chars FROM analyst_runs WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if config is None:
            raise CheckpointError("Analyst run disappeared during handoff load")
        mode = str(config["mode"])
        if mode not in {"fast", "deep"}:
            raise CheckpointError("Analyst run has an invalid Phase 1 mode")
        if mode == "deep" and conn.execute(
            "SELECT 1 FROM analyst_files WHERE run_id=? "
            "AND terminal_code='complete_detector_only' LIMIT 1",
            (fence.run_id,),
        ).fetchone() is not None:
            raise CheckpointError("deep run contains a detector-only success")
        rows = conn.execute(
            "SELECT * FROM analyst_files WHERE run_id=? AND work_state!='terminal' "
            "ORDER BY ordinal,file_id LIMIT ?",
            (fence.run_id, MAX_PHASE1_FILES + 1),
        ).fetchall()
        if len(rows) > MAX_PHASE1_FILES:
            raise CheckpointError("Phase 1 handoff exceeds its file bound")
        handoffs: list[Phase1FileHandoff] = []
        for row in rows:
            if not (
                row["stage"] == FileStage.SELECTED_FOR_MODEL.value
                and row["selected_for_model"] == 1
                and row["work_state"] == "pending"
                and row["active_generation"] is None
            ):
                raise CheckpointError("Phase 1 contains unfinished pre-handoff work")
            text_chars = _validate_handoff_extraction(row)
            detector_count = int(conn.execute(
                "SELECT count(*) FROM analyst_detector_hits WHERE file_id=?",
                (int(row["file_id"]),),
            ).fetchone()[0])
            if detector_count > MAX_DETECTOR_HITS:
                raise CheckpointError("durable detector evidence exceeds its bound")
            if mode == "fast" and detector_count == 0:
                raise CheckpointError("fast model selection lacks a detector hit")
            chunks = _chunk_identities(conn, int(row["file_id"]))
            _validate_chunk_shape(
                tuple(
                    ChunkSpec(
                        chunk.index, chunk.start, chunk.end, chunk.sha256,
                    )
                    for chunk in chunks
                ),
                int(config["chunk_chars"]),
                int(config["overlap_chars"]),
                text_chars,
            )
            handoffs.append(Phase1FileHandoff(
                file_id=int(row["file_id"]),
                ordinal=int(row["ordinal"]),
                chunks=chunks,
            ))
        return tuple(handoffs)

    return run_immediate(operation, path=path)


def verify_extraction_evidence(
    fence: LeaseFence,
    file_id: int,
    evidence: ExtractionCheckpointEvidence,
    *,
    authenticated_format_name: str,
    path: Path | None = None,
) -> None:
    """Require regenerated extraction metadata to equal its durable checkpoint."""
    _require_fence_type(fence)
    _require_file_id(file_id)
    if not isinstance(evidence, ExtractionCheckpointEvidence):
        raise TypeError("evidence must be ExtractionCheckpointEvidence")
    if authenticated_format_name not in _EXACT_FORMATS:
        raise ValueError("authenticated format name is outside its closed vocabulary")
    parser_json, parser_sha = _parser_identity(evidence.parser_identity)
    extraction_json = _extraction_counts(evidence.extraction_counts)
    provenance = _provenance_rows(evidence.provenance)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        if FileStage(str(row["stage"])) not in {
            FileStage.TEXT_EXTRACTED, FileStage.DETECTOR_SCANNED,
        }:
            raise CheckpointError(
                "extraction verification requires a completed text checkpoint"
            )
        if row["format_name"] != authenticated_format_name:
            raise Phase1EvidenceMismatch(
                "regenerated authenticated format does not match"
            )
        observed = (
            row["encoding"], row["parser_identity_json"],
            row["parser_identity_sha256"], row["extraction_meta_json"],
        )
        expected = (
            evidence.encoding, parser_json, parser_sha, extraction_json,
        )
        if tuple(observed) != expected:
            raise Phase1EvidenceMismatch(
                "regenerated extraction evidence does not match"
            )
        stored_rows = conn.execute(
            "SELECT ordinal,kind,label,start_char,end_char "
            "FROM analyst_provenance_units WHERE file_id=? ORDER BY ordinal LIMIT ?",
            (file_id, MAX_PROVENANCE_UNITS + 1),
        ).fetchall()
        if len(stored_rows) > MAX_PROVENANCE_UNITS:
            raise CheckpointError("durable provenance exceeds its bound")
        stored = tuple(
            (int(item[0]), str(item[1]), str(item[2]), int(item[3]), int(item[4]))
            for item in stored_rows
        )
        if stored != provenance:
            raise Phase1EvidenceMismatch(
                "regenerated extraction provenance does not match"
            )

    run_immediate(operation, path=path)


def verify_detector_checkpoint(
    fence: LeaseFence,
    file_id: int,
    hits: Iterable[DetectorHit],
    *,
    selected_for_model: bool,
    path: Path | None = None,
) -> None:
    """Require regenerated detector output and selection to match exactly."""
    _require_fence_type(fence)
    _require_file_id(file_id)
    if type(selected_for_model) is not bool:
        raise TypeError("selected_for_model must be bool")
    materialized = _detector_rows(hits)

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        if FileStage(str(row["stage"])) is not FileStage.DETECTOR_SCANNED:
            raise CheckpointError(
                "detector verification requires detector-scanned stage"
            )
        if row["selected_for_model"] != int(selected_for_model):
            raise Phase1EvidenceMismatch(
                "regenerated model selection does not match"
            )
        stored_rows = conn.execute(
            "SELECT ordinal,kind,value,start_char,end_char "
            "FROM analyst_detector_hits WHERE file_id=? ORDER BY ordinal LIMIT ?",
            (file_id, MAX_DETECTOR_HITS + 1),
        ).fetchall()
        if len(stored_rows) > MAX_DETECTOR_HITS:
            raise CheckpointError("durable detector evidence exceeds its bound")
        stored = tuple(
            (int(item[0]), str(item[1]), str(item[2]), int(item[3]), int(item[4]))
            for item in stored_rows
        )
        if stored != materialized:
            raise Phase1EvidenceMismatch(
                "regenerated detector evidence does not match"
            )

    run_immediate(operation, path=path)


def handoff_selected_file(
    fence: LeaseFence,
    file_id: int,
    chunks: Iterable[ChunkSpec],
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> Phase1FileHandoff:
    """Atomically persist chunks and make one selected file pending for C11."""
    _require_fence_type(fence)
    _require_file_id(file_id)
    materialized = _materialize_chunks(chunks)
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> Phase1FileHandoff:
        _require_running_fence(conn, fence)
        row = _active_file(conn, fence, file_id)
        if (
            FileStage(str(row["stage"])) is not FileStage.DETECTOR_SCANNED
            or row["selected_for_model"] != 1
        ):
            raise CheckpointError(
                "model handoff requires a selected detector checkpoint"
            )
        if conn.execute(
            "SELECT 1 FROM analyst_chunks WHERE file_id=? LIMIT 1", (file_id,),
        ).fetchone() is not None:
            raise CheckpointError("model chunk identities are already durable")
        config = conn.execute(
            "SELECT chunk_chars,overlap_chars FROM analyst_runs WHERE run_id=?",
            (fence.run_id,),
        ).fetchone()
        if config is None:
            raise CheckpointError("Analyst run disappeared during model handoff")
        text_chars = _text_chars(row)
        _validate_chunk_shape(
            materialized, int(config[0]), int(config[1]), text_chars,
        )
        conn.executemany(
            "INSERT INTO analyst_chunks("
            "file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
            "VALUES(?,?,?,?,?,'pending')",
            (
                (file_id, item.index, item.start, item.end, item.sha256)
                for item in materialized
            ),
        )
        durable_chunks = _chunk_identities(conn, file_id)
        if len(durable_chunks) != len(materialized):
            raise CheckpointError("model handoff did not persist every chunk")
        cursor = conn.execute(
            "UPDATE analyst_files SET stage='selected_for_model',"
            "work_state='pending',active_generation=NULL,updated_at_utc=?,"
            "revision=revision+1 WHERE file_id=? AND run_id=? "
            "AND stage='detector_scanned' AND selected_for_model=1 "
            "AND work_state='active' AND active_generation=?",
            (timestamp, file_id, fence.run_id, fence.generation),
        )
        if cursor.rowcount != 1:
            raise CheckpointError("model handoff lost its compare-and-set")
        return Phase1FileHandoff(
            file_id=file_id,
            ordinal=int(row["ordinal"]),
            chunks=durable_chunks,
        )

    return run_immediate(operation, path=path)


def _load_snapshot(
    conn: sqlite3.Connection, fence: LeaseFence, file_id: int,
) -> FileResumeSnapshot:
    row = _active_file(conn, fence, file_id)
    stage = FileStage(str(row["stage"]))
    if stage not in _SNAPSHOT_STAGES:
        raise CheckpointError("file is outside the Phase 1 resume contract")
    provenance_count = int(conn.execute(
        "SELECT count(*) FROM analyst_provenance_units WHERE file_id=?", (file_id,),
    ).fetchone()[0])
    detector_count = int(conn.execute(
        "SELECT count(*) FROM analyst_detector_hits WHERE file_id=?", (file_id,),
    ).fetchone()[0])
    chunks = _chunk_identities(conn, file_id)
    parser_json = (
        None if row["parser_identity_json"] is None
        else str(row["parser_identity_json"])
    )
    extraction_json = (
        None if row["extraction_meta_json"] is None
        else str(row["extraction_meta_json"])
    )
    if stage in {FileStage.DISCOVERED, FileStage.FORMAT_IDENTIFIED} and (
        provenance_count or detector_count
    ):
        raise CheckpointError("early-stage file contains later durable evidence")
    if stage is FileStage.TEXT_EXTRACTED and detector_count:
        raise CheckpointError("text-stage file already contains detector evidence")
    selected = row["selected_for_model"]
    return FileResumeSnapshot(
        file_id=int(row["file_id"]),
        ordinal=int(row["ordinal"]),
        inventory_file=InventoryFile(
            relative_path=str(row["relative_path"]), size=int(row["size"]),
            mtime_ns=int(row["mtime_ns"]), ctime_ns=int(row["ctime_ns"]),
            device=int(row["device"]), inode=int(row["inode"]),
            mode=int(row["mode"]), sha256=str(row["sha256"]),
        ),
        stage=stage,
        format_name=None if row["format_name"] is None else str(row["format_name"]),
        encoding=None if row["encoding"] is None else str(row["encoding"]),
        parser_identity_json=parser_json,
        parser_identity_sha256=(
            None if row["parser_identity_sha256"] is None
            else str(row["parser_identity_sha256"])
        ),
        extraction_meta_json=extraction_json,
        extraction_meta_sha256=(
            None if extraction_json is None
            else hashlib.sha256(extraction_json.encode("utf-8")).hexdigest()
        ),
        detector_hit_count=detector_count,
        selected_for_model=(None if selected is None else bool(selected)),
        chunks=chunks,
    )


def _chunk_identities(
    conn: sqlite3.Connection, file_id: int,
) -> tuple[Phase1ChunkIdentity, ...]:
    rows = conn.execute(
        "SELECT chunk_id,chunk_index,start_char,end_char,chunk_sha256 "
        "FROM analyst_chunks WHERE file_id=? ORDER BY chunk_index LIMIT ?",
        (file_id, MAX_CHUNKS_PER_FILE + 1),
    ).fetchall()
    if len(rows) > MAX_CHUNKS_PER_FILE:
        raise CheckpointError("durable chunks exceed the per-file bound")
    return tuple(
        Phase1ChunkIdentity(
            chunk_id=int(row["chunk_id"]),
            index=int(row["chunk_index"]),
            start=int(row["start_char"]),
            end=int(row["end_char"]),
            sha256=str(row["chunk_sha256"]),
        )
        for row in rows
    )


def _validate_handoff_extraction(row: sqlite3.Row) -> int:
    if row["format_name"] not in _EXACT_FORMATS:
        raise CheckpointError("model handoff lacks an exact authenticated format")
    if row["encoding"] is not None and row["encoding"] not in _ENCODINGS:
        raise CheckpointError("model handoff has an invalid extraction encoding")
    try:
        parser = json.loads(str(row["parser_identity_json"]))
        counts = json.loads(str(row["extraction_meta_json"]))
        parser_json, parser_sha = _parser_identity(parser)
        extraction_json = _extraction_counts(counts)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointError("model handoff has invalid extraction evidence") from None
    if (
        parser_json != row["parser_identity_json"]
        or parser_sha != row["parser_identity_sha256"]
        or extraction_json != row["extraction_meta_json"]
    ):
        raise CheckpointError("model handoff extraction identity does not match")
    text_chars = counts["text_chars"]
    if type(text_chars) is not int or text_chars <= 0:
        raise CheckpointError("selected model handoff requires extracted text")
    return text_chars


def _require_running_fence(conn: sqlite3.Connection, fence: LeaseFence) -> None:
    if conn.execute(
        "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND " + _FENCE_WHERE,
        _fence_values(fence),
    ).fetchone() is None or conn.execute(
        "SELECT 1 FROM analyst_runs WHERE run_id=? AND state='running'",
        (fence.run_id,),
    ).fetchone() is None:
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


def _parser_identity(value: Mapping[str, object]) -> tuple[str, str]:
    if (
        not isinstance(value, Mapping)
        or not value
        or not set(value).issubset(_PARSER_IDENTITY_KEYS)
        or "parser" not in value
        or any(
            not isinstance(item, str) or _IDENTITY_VALUE.fullmatch(item) is None
            for item in value.values()
        )
    ):
        raise ValueError("parser identity is outside its closed contract")
    body = _canonical_json(value)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _extraction_counts(value: Mapping[str, object]) -> str:
    if (
        not isinstance(value, Mapping)
        or not value
        or not set(value).issubset(_EXTRACTION_COUNT_KEYS)
        or not {"text_chars", "text_bytes"}.issubset(value)
        or any(type(item) is not int or item < 0 for item in value.values())
    ):
        raise ValueError("extraction counts are outside their closed contract")
    return _canonical_json(value)


def _canonical_json(value: Mapping[str, object]) -> str:
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


def _provenance_rows(
    units: Iterable[ProvenanceUnit],
) -> tuple[tuple[int, str, str, int, int], ...]:
    materialized: list[ProvenanceUnit] = []
    for unit in units:
        if not isinstance(unit, ProvenanceUnit):
            raise TypeError("provenance contains an invalid unit")
        if len(materialized) >= MAX_PROVENANCE_UNITS:
            raise ValueError("provenance exceeds its durable cap")
        materialized.append(unit)
    return tuple(
        (ordinal, unit.kind, unit.label, unit.start, unit.end)
        for ordinal, unit in enumerate(materialized)
    )


def _detector_rows(
    hits: Iterable[DetectorHit],
) -> tuple[tuple[int, str, str, int, int], ...]:
    materialized: list[DetectorHit] = []
    for hit in hits:
        if not isinstance(hit, DetectorHit):
            raise TypeError("detector checkpoint contains an invalid hit")
        if len(materialized) >= MAX_DETECTOR_HITS:
            raise ValueError("detector evidence exceeds its durable cap")
        materialized.append(hit)
    return tuple(
        (ordinal, hit.kind, hit.value, hit.start, hit.end)
        for ordinal, hit in enumerate(materialized)
    )


def _materialize_chunks(chunks: Iterable[ChunkSpec]) -> tuple[ChunkSpec, ...]:
    materialized: list[ChunkSpec] = []
    for item in chunks:
        if not isinstance(item, ChunkSpec):
            raise TypeError("model handoff contains an invalid chunk")
        if len(materialized) >= MAX_CHUNKS_PER_FILE:
            raise ValueError("model handoff exceeds the per-file chunk bound")
        materialized.append(item)
    result = tuple(materialized)
    _validate_chunk_indexes(result)
    return result


def _validate_chunk_indexes(chunks: tuple[ChunkSpec, ...]) -> None:
    if any(not isinstance(item, ChunkSpec) for item in chunks):
        raise TypeError("model handoff contains an invalid chunk")
    if tuple(item.index for item in chunks) != tuple(range(len(chunks))):
        raise ValueError("chunk indexes must be canonical and contiguous")


def _validate_chunk_shape(
    chunks: tuple[ChunkSpec, ...],
    chunk_chars: int,
    overlap_chars: int,
    text_chars: int,
) -> None:
    if text_chars <= 0 or not chunks:
        raise ValueError("selected model handoff requires nonempty extracted text")
    if chunks[0].start != 0 or chunks[-1].end != text_chars:
        raise ValueError("chunks do not cover the exact extracted text span")
    stride = chunk_chars - overlap_chars
    for position, item in enumerate(chunks):
        if not 0 <= item.start < item.end <= text_chars:
            raise ValueError("chunk bounds exceed the extracted text span")
        length = item.end - item.start
        if length > chunk_chars:
            raise ValueError("chunk exceeds the frozen character window")
        if position < len(chunks) - 1:
            following = chunks[position + 1]
            if length != chunk_chars or following.start != item.start + stride:
                raise ValueError("chunk overlap does not match the frozen window")


def _text_chars(row: sqlite3.Row) -> int:
    try:
        value = json.loads(str(row["extraction_meta_json"]))["text_chars"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointError("file lacks valid extracted-text evidence") from None
    if type(value) is not int or value < 0:
        raise CheckpointError("file has invalid extracted-text length evidence")
    return value


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation, fence.run_id, fence.owner_token, fence.process.pid,
        fence.process.start_ticks, fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _require_fence_type(fence: LeaseFence) -> None:
    if not isinstance(fence, LeaseFence):
        raise TypeError("fence must be a LeaseFence")


def _require_file_id(file_id: int) -> None:
    if type(file_id) is not int or file_id <= 0:
        raise ValueError("file id must be a positive integer")


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z",
        )
    if not isinstance(value, str) or not value:
        raise ValueError("now_utc must be nonempty text")
    return value


__all__ = [
    "FileResumeSnapshot",
    "Phase1FileHandoff",
    "claim_next_phase1_file",
    "handoff_selected_file",
    "load_file_resume_snapshot",
    "load_phase1_handoff",
    "verify_detector_checkpoint",
    "verify_extraction_evidence",
]
