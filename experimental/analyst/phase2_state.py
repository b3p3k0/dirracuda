"""Fenced durable state for serial Analyst Phase 2 work."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .checkpoint import CheckpointError
from .inventory import InventoryFile
from .lease import LeaseFence
from .models import Assessment, FileStage, FileTerminal, WorksheetResult
from .phase2_contract import (
    HealthObligation,
    Phase2AttemptIdentity,
    Phase2ChunkSnapshot,
    Phase2FileCompletion,
    Phase2FileSnapshot,
    Phase2ContractError,
)
from .state import AttemptState, ChunkState
from .store import run_immediate
from .worker_contract import MAX_CHUNKS_PER_FILE, Phase1ChunkIdentity


_FENCE_WHERE = (
    "generation=? AND run_id=? AND owner_token=? AND pid=? AND start_ticks=? "
    "AND boot_id=? AND heartbeat_monotonic_ns=?"
)
_PHASE2_STAGES = (
    FileStage.SELECTED_FOR_MODEL,
    FileStage.MODEL_REVIEWED,
    FileStage.MODEL_RESPONSE_VALID,
)
_AMBIGUOUS_CONTACTS = frozenset({
    "request_timeout", "transport_unavailable",
    "cancelled_unverified", "orphaned_unknown",
})
_NONRETRYABLE_CONTACTS = frozenset({
    "identity_mismatch", "protocol_violation", "response_limit",
})


class Phase2StateError(CheckpointError):
    """Durable C11 state contradicts the frozen Phase 2 contract."""


def claim_next_phase2_file(
    fence: LeaseFence,
    *,
    expected_file_id: int | None = None,
    now_utc: str | None = None,
    path: Path | None = None,
) -> Phase2FileSnapshot | None:
    """Claim only a pending selected/model-stage file in canonical order."""
    _require_fence(fence)
    if expected_file_id is not None and (
        type(expected_file_id) is not int or expected_file_id <= 0
    ):
        raise ValueError("expected file id must be positive")
    timestamp = _timestamp(now_utc)
    stages = tuple(item.value for item in _PHASE2_STAGES)
    placeholders = ",".join("?" for _ in stages)

    def operation(conn: sqlite3.Connection) -> Phase2FileSnapshot | None:
        _require_running_fence(conn, fence)
        params: tuple[object, ...] = (fence.run_id, *stages)
        clause = ""
        if expected_file_id is not None:
            clause = " AND file_id=?"
            params = (*params, expected_file_id)
        row = conn.execute(
            "SELECT file_id FROM analyst_files WHERE run_id=? AND work_state='pending' "
            f"AND stage IN ({placeholders}){clause} ORDER BY ordinal LIMIT 1",
            params,
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
                fence.generation, timestamp, file_id, fence.run_id, *stages,
            ),
        )
        if cursor.rowcount != 1:
            raise Phase2StateError("Phase 2 file claim lost its compare-and-set")
        return _load_snapshot(conn, fence, file_id)

    return run_immediate(operation, path=path)


def load_phase2_snapshot(
    fence: LeaseFence, file_id: int, *, path: Path | None = None,
) -> Phase2FileSnapshot:
    """Load the bounded exact state of one active Phase 2 file."""
    _require_fence(fence)
    _require_id(file_id, "file id")

    def operation(conn: sqlite3.Connection) -> Phase2FileSnapshot:
        _require_running_fence(conn, fence)
        return _load_snapshot(conn, fence, file_id)

    return run_immediate(operation, path=path)


def load_health_obligation(
    fence: LeaseFence, *, path: Path | None = None,
) -> HealthObligation | None:
    """Derive the latest ambiguous chat not followed by an answered health contact."""
    _require_fence(fence)

    def operation(conn: sqlite3.Connection) -> HealthObligation | None:
        _require_running_fence(conn, fence)
        placeholders = ",".join("?" for _ in _AMBIGUOUS_CONTACTS)
        row = conn.execute(
            "SELECT o.contact_id,o.contact_no,"
            "CASE WHEN a.state IN ('orphaned_unknown','cancelled_unverified') "
            "THEN a.state ELSE o.state END AS source_status "
            "FROM analyst_ollama_contacts o "
            "LEFT JOIN analyst_model_attempts a ON a.attempt_id=o.attempt_id "
            "WHERE o.run_id=? AND o.kind='chat' AND ("
            f"o.state IN ({placeholders}) OR (o.state='success' AND "
            "a.state IN ('orphaned_unknown','cancelled_unverified'))) "
            "ORDER BY o.contact_no DESC LIMIT 1",
            (fence.run_id, *sorted(_AMBIGUOUS_CONTACTS)),
        ).fetchone()
        if row is None:
            return None
        answered = conn.execute(
            "SELECT 1 FROM analyst_ollama_contacts WHERE run_id=? "
            "AND contact_no>? AND kind='cancellation_health' "
            "AND state IN ('success','model_invalid') LIMIT 1",
            (fence.run_id, int(row["contact_no"])),
        ).fetchone()
        if answered is not None:
            return None
        return HealthObligation(
            source_contact_id=str(row["contact_id"]),
            source_contact_no=int(row["contact_no"]),
            source_status=str(row["source_status"]),
        )

    return run_immediate(operation, path=path)


def close_nonretryable_chunk(
    fence: LeaseFence,
    chunk_id: int,
    *,
    path: Path | None = None,
) -> None:
    """Close an identity/protocol/limit failure without spending attempt two."""
    _require_fence(fence)
    _require_id(chunk_id, "chunk id")

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_chunk(conn, fence, chunk_id)
        if row["state"] != ChunkState.PENDING.value:
            raise Phase2StateError("nonretryable chunk is no longer pending")
        attempt = conn.execute(
            "SELECT a.attempt_id,o.state FROM analyst_model_attempts a "
            "JOIN analyst_ollama_contacts o ON o.attempt_id=a.attempt_id "
            "WHERE a.chunk_id=? ORDER BY a.attempt_no DESC LIMIT 1",
            (chunk_id,),
        ).fetchone()
        if attempt is None or str(attempt["state"]) not in _NONRETRYABLE_CONTACTS:
            raise Phase2StateError("chunk does not have a nonretryable contact")
        cursor = conn.execute(
            "UPDATE analyst_chunks SET state='model_transport_error' "
            "WHERE chunk_id=? AND state='pending'", (chunk_id,),
        )
        if cursor.rowcount != 1:
            raise Phase2StateError("nonretryable chunk close lost its compare-and-set")

    run_immediate(operation, path=path)


def close_exhausted_ambiguous_chunk(
    fence: LeaseFence,
    chunk_id: int,
    *,
    path: Path | None = None,
) -> None:
    """Close attempt-two orphan/cancel uncertainty as transport-limited coverage."""
    _require_fence(fence)
    _require_id(chunk_id, "chunk id")

    def operation(conn: sqlite3.Connection) -> None:
        _require_running_fence(conn, fence)
        row = _active_chunk(conn, fence, chunk_id)
        if row["state"] != ChunkState.PENDING.value:
            raise Phase2StateError("ambiguous chunk is no longer pending")
        attempts = conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts WHERE chunk_id=? "
            "ORDER BY attempt_no", (chunk_id,),
        ).fetchall()
        if (
            len(attempts) != 2
            or tuple(int(item["attempt_no"]) for item in attempts) != (1, 2)
            or str(attempts[-1]["state"])
            not in {"orphaned_unknown", "cancelled_unverified"}
        ):
            raise Phase2StateError("chunk is not an exhausted ambiguous attempt")
        cursor = conn.execute(
            "UPDATE analyst_chunks SET state='model_transport_error' "
            "WHERE chunk_id=? AND state='pending'", (chunk_id,),
        )
        if cursor.rowcount != 1:
            raise Phase2StateError("ambiguous chunk close lost its compare-and-set")

    run_immediate(operation, path=path)


def deduplicate_grounded_result(
    fence: LeaseFence,
    chunk_id: int,
    result: WorksheetResult,
    *,
    path: Path | None = None,
) -> WorksheetResult:
    """Drop exact overlap duplicates already retained by an earlier file chunk."""
    _require_fence(fence)
    _require_id(chunk_id, "chunk id")
    if type(result) is not WorksheetResult:
        raise TypeError("grounded result must use WorksheetResult")
    if not result.findings and result.model_assessment is Assessment.FINDINGS_PRESENT:
        raise Phase2StateError("findings-present answer retained no grounded evidence")

    def operation(conn: sqlite3.Connection) -> WorksheetResult:
        _require_running_fence(conn, fence)
        current = _active_chunk(conn, fence, chunk_id)
        earlier = conn.execute(
            "SELECT f.category,f.quote,c.start_char+f.canonical_offset AS absolute_start,"
            "c.start_char+f.canonical_end AS absolute_end "
            "FROM analyst_model_findings f JOIN analyst_chunks c ON c.chunk_id=f.chunk_id "
            "WHERE c.file_id=? AND c.chunk_index<? ORDER BY c.chunk_index,f.ordinal",
            (int(current["file_id"]), int(current["chunk_index"])),
        ).fetchall()
        seen = {
            (str(row["category"]), str(row["quote"]), int(row["absolute_start"]),
             int(row["absolute_end"]))
            for row in earlier
        }
        start = int(current["start_char"])
        retained = tuple(
            finding for finding in result.findings
            if (
                finding.category.value,
                finding.quote,
                start + finding.canonical_offset,
                start + finding.canonical_end,
            ) not in seen
        )
        removed = len(result.findings) - len(retained)
        assessment = result.model_assessment
        if removed and not retained and assessment is Assessment.FINDINGS_PRESENT:
            assessment = Assessment.NO_FINDINGS
        return WorksheetResult(
            document_type=result.document_type,
            subject=result.subject,
            model_assessment=assessment,
            findings=retained,
            raw_finding_count=result.raw_finding_count,
            removed_duplicate_count=result.removed_duplicate_count + removed,
            dropped_ungrounded_count=result.dropped_ungrounded_count,
        )

    return run_immediate(operation, path=path)


def finish_phase2_file(
    fence: LeaseFence,
    file_id: int,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> Phase2FileCompletion:
    """Atomically derive model coverage, stage progression and the file terminal."""
    _require_fence(fence)
    _require_id(file_id, "file id")
    timestamp = _timestamp(now_utc)

    def operation(conn: sqlite3.Connection) -> Phase2FileCompletion:
        _require_running_fence(conn, fence)
        file_row = _active_file(conn, fence, file_id)
        stage = FileStage(str(file_row["stage"]))
        if stage not in _PHASE2_STAGES or file_row["selected_for_model"] != 1:
            raise Phase2StateError("file is outside Phase 2 completion")
        if conn.execute(
            "SELECT 1 FROM analyst_model_attempts a JOIN analyst_chunks c "
            "ON c.chunk_id=a.chunk_id WHERE c.file_id=? AND a.state='dispatching' LIMIT 1",
            (file_id,),
        ).fetchone() is not None or conn.execute(
            "SELECT 1 FROM analyst_ollama_contacts o JOIN analyst_chunks c "
            "ON c.chunk_id=o.chunk_id WHERE c.file_id=? AND o.state='dispatching' LIMIT 1",
            (file_id,),
        ).fetchone() is not None:
            raise Phase2StateError("file retains dispatching model work")
        rows = conn.execute(
            "SELECT state FROM analyst_chunks WHERE file_id=? ORDER BY chunk_index LIMIT ?",
            (file_id, MAX_CHUNKS_PER_FILE + 1),
        ).fetchall()
        if not rows or len(rows) > MAX_CHUNKS_PER_FILE:
            raise Phase2StateError("file has an invalid chunk set")
        states = tuple(ChunkState(str(row["state"])) for row in rows)
        if ChunkState.PENDING in states:
            raise Phase2StateError("file still has pending chunks")
        if all(state is ChunkState.MODEL_RESPONSE_VALID for state in states):
            terminal = FileTerminal.COMPLETE_MODEL_REVIEWED
            target_stage = FileStage.MODEL_RESPONSE_VALID
            valid_count = len(states)
            finding_count = int(conn.execute(
                "SELECT count(*) FROM analyst_model_findings f JOIN analyst_chunks c "
                "ON c.chunk_id=f.chunk_id WHERE c.file_id=?", (file_id,),
            ).fetchone()[0])
        else:
            precedence = (
                (ChunkState.MODEL_TRANSPORT_ERROR, FileTerminal.MODEL_TRANSPORT_ERROR),
                (ChunkState.MODEL_TIMEOUT, FileTerminal.MODEL_TIMEOUT),
                (ChunkState.MODEL_INVALID, FileTerminal.MODEL_INVALID),
            )
            terminal = next(
                terminal_value for chunk_state, terminal_value in precedence
                if chunk_state in states
            )
            target_stage = FileStage.MODEL_REVIEWED
            valid_count = 0
            finding_count = 0
        if stage is FileStage.SELECTED_FOR_MODEL:
            conn.execute(
                "UPDATE analyst_files SET stage='model_reviewed',revision=revision+1,"
                "updated_at_utc=? WHERE file_id=?", (timestamp, file_id),
            )
            stage = FileStage.MODEL_REVIEWED
        if target_stage is FileStage.MODEL_RESPONSE_VALID and stage is FileStage.MODEL_REVIEWED:
            conn.execute(
                "UPDATE analyst_files SET stage='model_response_valid',revision=revision+1,"
                "updated_at_utc=? WHERE file_id=?", (timestamp, file_id),
            )
            stage = FileStage.MODEL_RESPONSE_VALID
        if stage is not target_stage:
            raise Phase2StateError("file stage contradicts derived model coverage")
        cursor = conn.execute(
            "UPDATE analyst_files SET work_state='terminal',terminal_code=?,"
            "terminal_detail=NULL,active_generation=NULL,updated_at_utc=?,"
            "revision=revision+1 WHERE file_id=? AND run_id=? AND work_state='active' "
            "AND active_generation=?",
            (terminal.value, timestamp, file_id, fence.run_id, fence.generation),
        )
        if cursor.rowcount != 1:
            raise Phase2StateError("file terminalization lost its compare-and-set")
        return Phase2FileCompletion(
            file_id=file_id,
            terminal=terminal,
            valid_chunk_count=valid_count,
            retained_finding_count=finding_count,
        )

    return run_immediate(operation, path=path)


def load_phase2_totals(
    fence: LeaseFence, *, path: Path | None = None,
) -> tuple[int, int, int]:
    """Return complete run-wide C12 handoff counts after all selected work closes."""
    _require_fence(fence)

    def operation(conn: sqlite3.Connection) -> tuple[int, int, int]:
        _require_running_fence(conn, fence)
        if conn.execute(
            "SELECT 1 FROM analyst_files WHERE run_id=? AND selected_for_model=1 "
            "AND work_state!='terminal' LIMIT 1", (fence.run_id,),
        ).fetchone() is not None:
            raise Phase2StateError("selected model work is not complete")
        reviewed = int(conn.execute(
            "SELECT count(*) FROM analyst_files WHERE run_id=? "
            "AND selected_for_model=1 AND work_state='terminal' "
            "AND stage IN ('model_reviewed','model_response_valid')",
            (fence.run_id,),
        ).fetchone()[0])
        valid = int(conn.execute(
            "SELECT count(*) FROM analyst_chunks c JOIN analyst_files f "
            "ON f.file_id=c.file_id WHERE f.run_id=? "
            "AND f.terminal_code='complete_model_reviewed' "
            "AND c.state='model_response_valid'", (fence.run_id,),
        ).fetchone()[0])
        findings = int(conn.execute(
            "SELECT count(*) FROM analyst_model_findings m "
            "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=? "
            "AND f.terminal_code='complete_model_reviewed'", (fence.run_id,),
        ).fetchone()[0])
        return reviewed, valid, findings

    return run_immediate(operation, path=path)


def _load_snapshot(
    conn: sqlite3.Connection, fence: LeaseFence, file_id: int,
) -> Phase2FileSnapshot:
    row = _active_file(conn, fence, file_id)
    stage = FileStage(str(row["stage"]))
    if stage not in _PHASE2_STAGES or row["selected_for_model"] != 1:
        raise Phase2StateError("file is outside the Phase 2 resume contract")
    parser_json = _canonical_object(str(row["parser_identity_json"]), "parser")
    extraction_json = _canonical_object(str(row["extraction_meta_json"]), "extraction")
    if hashlib.sha256(parser_json.encode()).hexdigest() != row["parser_identity_sha256"]:
        raise Phase2StateError("parser identity hash does not match")
    counts = json.loads(extraction_json)
    if type(counts.get("text_chars")) is not int or counts["text_chars"] <= 0:
        raise Phase2StateError("selected file lacks extracted text")
    chunks = _load_chunks(conn, file_id)
    try:
        return Phase2FileSnapshot(
            file_id=file_id,
            ordinal=int(row["ordinal"]),
            inventory_file=InventoryFile(
                relative_path=str(row["relative_path"]), size=int(row["size"]),
                mtime_ns=int(row["mtime_ns"]), ctime_ns=int(row["ctime_ns"]),
                device=int(row["device"]), inode=int(row["inode"]),
                mode=int(row["mode"]), sha256=str(row["sha256"]),
            ),
            stage=stage,
            format_name=str(row["format_name"]),
            parser_identity_json=parser_json,
            extraction_meta_json=extraction_json,
            chunks=chunks,
        )
    except (Phase2ContractError, TypeError, ValueError):
        raise Phase2StateError("inventory or Phase 2 snapshot evidence is invalid") from None


def _load_chunks(
    conn: sqlite3.Connection, file_id: int,
) -> tuple[Phase2ChunkSnapshot, ...]:
    rows = conn.execute(
        "SELECT chunk_id,chunk_index,start_char,end_char,chunk_sha256,state "
        "FROM analyst_chunks WHERE file_id=? ORDER BY chunk_index LIMIT ?",
        (file_id, MAX_CHUNKS_PER_FILE + 1),
    ).fetchall()
    if not rows or len(rows) > MAX_CHUNKS_PER_FILE:
        raise Phase2StateError("Phase 2 chunks exceed their bound")
    result: list[Phase2ChunkSnapshot] = []
    for row in rows:
        attempts = conn.execute(
            "SELECT attempt_id,attempt_no,request_sha256,state "
            "FROM analyst_model_attempts WHERE chunk_id=? ORDER BY attempt_no LIMIT 3",
            (int(row["chunk_id"]),),
        ).fetchall()
        if len(attempts) > 2:
            raise Phase2StateError("chunk exceeds its semantic attempt bound")
        snapshot = Phase2ChunkSnapshot(
            identity=Phase1ChunkIdentity(
                chunk_id=int(row["chunk_id"]), index=int(row["chunk_index"]),
                start=int(row["start_char"]), end=int(row["end_char"]),
                sha256=str(row["chunk_sha256"]),
            ),
            state=ChunkState(str(row["state"])),
            attempts=tuple(
                Phase2AttemptIdentity(
                    attempt_id=str(item["attempt_id"]),
                    attempt_no=int(item["attempt_no"]),
                    request_sha256=str(item["request_sha256"]),
                    state=AttemptState(str(item["state"])),
                )
                for item in attempts
            ),
        )
        _validate_chunk_history(conn, int(row["chunk_id"]), snapshot)
        result.append(snapshot)
    _validate_chunk_windows(conn, file_id, result)
    return tuple(result)


def _validate_chunk_history(
    conn: sqlite3.Connection,
    chunk_id: int,
    snapshot: Phase2ChunkSnapshot,
) -> None:
    attempts = snapshot.attempts
    state = snapshot.state
    row = conn.execute(
        "SELECT accepted_attempt_id FROM analyst_chunks WHERE chunk_id=?",
        (chunk_id,),
    ).fetchone()
    accepted = None if row is None else row["accepted_attempt_id"]
    if state is ChunkState.PENDING:
        valid = (
            accepted is None
            and not any(item.state is AttemptState.VALID for item in attempts)
            and not (
                len(attempts) == 2
                and attempts[-1].state in {
                    AttemptState.SCHEMA_INVALID,
                    AttemptState.MODEL_TIMEOUT,
                    AttemptState.MODEL_TRANSPORT_ERROR,
                }
            )
        )
    elif state is ChunkState.MODEL_RESPONSE_VALID:
        valid = (
            bool(attempts)
            and attempts[-1].state is AttemptState.VALID
            and accepted == attempts[-1].attempt_id
        )
    elif state is ChunkState.MODEL_INVALID:
        valid = (
            accepted is None
            and len(attempts) == 2
            and attempts[-1].state is AttemptState.SCHEMA_INVALID
        )
    elif state is ChunkState.MODEL_TIMEOUT:
        valid = (
            accepted is None
            and len(attempts) == 2
            and attempts[-1].state is AttemptState.MODEL_TIMEOUT
        )
    else:
        valid = (
            accepted is None
            and bool(attempts)
            and attempts[-1].state in {
                AttemptState.MODEL_TRANSPORT_ERROR,
                AttemptState.ORPHANED_UNKNOWN,
                AttemptState.CANCELLED_UNVERIFIED,
            }
        )
    if not valid:
        raise Phase2StateError("chunk state contradicts semantic attempt history")


def _validate_chunk_windows(
    conn: sqlite3.Connection, file_id: int, chunks: list[Phase2ChunkSnapshot],
) -> None:
    config = conn.execute(
        "SELECT r.chunk_chars,r.overlap_chars,f.extraction_meta_json "
        "FROM analyst_files f JOIN analyst_runs r ON r.run_id=f.run_id "
        "WHERE f.file_id=?", (file_id,),
    ).fetchone()
    if config is None:
        raise Phase2StateError("chunk owner disappeared")
    counts = json.loads(str(config["extraction_meta_json"]))
    text_chars = counts.get("text_chars")
    chunk_chars = int(config["chunk_chars"])
    overlap = int(config["overlap_chars"])
    identities = [item.identity for item in chunks]
    if (
        type(text_chars) is not int
        or identities[0].start != 0
        or identities[-1].end != text_chars
        or any(item.end > text_chars or item.end - item.start > chunk_chars for item in identities)
        or any(
            prior.end - prior.start != chunk_chars
            or current.start != prior.end - overlap
            or current.end <= prior.end
            for prior, current in zip(identities, identities[1:])
        )
    ):
        raise Phase2StateError("durable chunk coverage is not canonical")


def _active_file(
    conn: sqlite3.Connection, fence: LeaseFence, file_id: int,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM analyst_files WHERE file_id=? AND run_id=? "
        "AND work_state='active' AND active_generation=?",
        (file_id, fence.run_id, fence.generation),
    ).fetchone()
    if row is None:
        raise Phase2StateError("file is not active under this worker fence")
    return row


def _active_chunk(
    conn: sqlite3.Connection, fence: LeaseFence, chunk_id: int,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT c.*,f.run_id,f.work_state,f.active_generation "
        "FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id "
        "WHERE c.chunk_id=? AND f.run_id=? AND f.work_state='active' "
        "AND f.active_generation=?",
        (chunk_id, fence.run_id, fence.generation),
    ).fetchone()
    if row is None:
        raise Phase2StateError("chunk is not active under this worker fence")
    return row


def _require_running_fence(conn: sqlite3.Connection, fence: LeaseFence) -> None:
    if conn.execute(
        "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND " + _FENCE_WHERE,
        _fence_values(fence),
    ).fetchone() is None or conn.execute(
        "SELECT 1 FROM analyst_runs WHERE run_id=? AND state='running'",
        (fence.run_id,),
    ).fetchone() is None:
        raise Phase2StateError("worker lease no longer authorizes Phase 2")


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation, fence.run_id, fence.owner_token, fence.process.pid,
        fence.process.start_ticks, fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _canonical_object(value: str, label: str) -> str:
    try:
        parsed = json.loads(value)
        canonical = json.dumps(
            parsed, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        raise Phase2StateError(f"{label} evidence is invalid") from None
    if type(parsed) is not dict or canonical != value:
        raise Phase2StateError(f"{label} evidence is not canonical")
    return canonical


def _require_fence(value: object) -> None:
    if type(value) is not LeaseFence:
        raise TypeError("Phase 2 state requires a LeaseFence")


def _require_id(value: object, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be positive")


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError("timestamp is invalid")
    return value


__all__ = [
    "Phase2StateError",
    "claim_next_phase2_file",
    "close_exhausted_ambiguous_chunk",
    "close_nonretryable_chunk",
    "deduplicate_grounded_result",
    "finish_phase2_file",
    "load_health_obligation",
    "load_phase2_totals",
    "load_phase2_snapshot",
]
