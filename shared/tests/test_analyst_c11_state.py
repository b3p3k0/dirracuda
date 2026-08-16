"""Real-SQLite hostile tests for frozen C11 Phase 2 state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst.checkpoint import finish_valid_attempt
from experimental.analyst.contact_contract import ContactKind, ContactStatus
from experimental.analyst.db_schema import AnalystSchemaError, validate_schema
from experimental.analyst.lease import LeaseFence, claim_worker, release_worker
from experimental.analyst.models import (
    Assessment,
    Category,
    FileStage,
    FileTerminal,
    GroundedFinding,
    WorksheetResult,
)
from experimental.analyst.ollama_state import (
    OllamaStateError,
    finish_contact,
    precharge_chat_contact,
    precharge_control_contact,
)
from experimental.analyst.phase2_contract import HEALTH_REQUEST_SHA256
from experimental.analyst.phase2_state import (
    Phase2StateError,
    claim_next_phase2_file,
    close_exhausted_ambiguous_chunk,
    close_nonretryable_chunk,
    deduplicate_grounded_result,
    finish_phase2_file,
    load_health_obligation,
    load_phase2_snapshot,
)
from experimental.analyst.store import open_connection
from experimental.analyst.state import ChunkState
from shared.tests.test_analyst_c10b import _prepare_handoff


_NOW = "2026-08-16T16:00:00Z"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prepare(tmp_path: Path, text: str = "public selected text"):
    path, _spec, _inventory, fence, handoff = _prepare_handoff(tmp_path, text)
    return path, fence, handoff


def _claim(tmp_path: Path, text: str = "public selected text"):
    path, fence, handoff = _prepare(tmp_path, text)
    snapshot = claim_next_phase2_file(
        fence, expected_file_id=handoff.file_id, now_utc=_NOW, path=path,
    )
    assert snapshot is not None
    return path, fence, snapshot


def _finish_chat(
    path: Path,
    fence: LeaseFence,
    chunk_id: int,
    status: ContactStatus,
    label: str,
):
    charge = precharge_chat_contact(
        fence, chunk_id, _sha(label), now_utc=_NOW, path=path,
    )
    return finish_contact(
        fence, charge.contact_id, status, now_utc=_NOW, path=path,
    )


def _answer_health(path: Path, fence: LeaseFence) -> None:
    charge = precharge_control_contact(
        fence,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=_NOW,
        path=path,
    )
    finish_contact(
        fence, charge.contact_id, ContactStatus.SUCCESS,
        now_utc=_NOW, path=path,
    )


def _result(
    finding: GroundedFinding | None = None,
    *,
    raw_count: int | None = None,
) -> WorksheetResult:
    findings = () if finding is None else (finding,)
    return WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=(
            Assessment.NO_FINDINGS if finding is None else Assessment.FINDINGS_PRESENT
        ),
        findings=findings,
        raw_finding_count=len(findings) if raw_count is None else raw_count,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )


def test_phase2_claim_is_selected_only_ordered_and_fenced(tmp_path: Path) -> None:
    path, fence, handoff = _prepare(tmp_path)
    assert claim_next_phase2_file(
        fence, expected_file_id=handoff.file_id + 1, path=path,
    ) is None
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT work_state FROM analyst_files WHERE file_id=?",
            (handoff.file_id,),
        ).fetchone()[0] == "pending"
    finally:
        conn.close()

    snapshot = claim_next_phase2_file(
        fence, expected_file_id=handoff.file_id, now_utc=_NOW, path=path,
    )
    assert snapshot is not None
    assert snapshot.stage is FileStage.SELECTED_FOR_MODEL
    assert snapshot.file_id == handoff.file_id
    assert tuple(item.identity for item in snapshot.chunks) == handoff.chunks
    assert load_phase2_snapshot(fence, snapshot.file_id, path=path) == snapshot
    assert claim_next_phase2_file(fence, path=path) is None

    stale = LeaseFence(
        fence.generation,
        fence.run_id,
        fence.owner_token,
        fence.process,
        fence.heartbeat_monotonic_ns + 1,
    )
    with pytest.raises(Phase2StateError, match="lease"):
        load_phase2_snapshot(stale, snapshot.file_id, path=path)


def test_failed_snapshot_audit_rolls_back_claim_atomically(tmp_path: Path) -> None:
    path, fence, handoff = _prepare(tmp_path)
    noncanonical = '{"parser": "builtin_text"}'
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE analyst_files SET parser_identity_json=?,"
            "parser_identity_sha256=? WHERE file_id=?",
            (
                noncanonical,
                hashlib.sha256(noncanonical.encode()).hexdigest(),
                handoff.file_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(Phase2StateError, match="not canonical"):
        claim_next_phase2_file(fence, path=path)
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,active_generation FROM analyst_files WHERE file_id=?",
            (handoff.file_id,),
        ).fetchone()) == ("pending", None)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("sql", "value", "match"),
    [
        (
            "UPDATE analyst_files SET relative_path=? WHERE file_id=?",
            "../escape.txt",
            "inventory",
        ),
        (
            "UPDATE analyst_chunks SET state=? WHERE file_id=?",
            "model_invalid",
            "chunk state",
        ),
    ],
)
def test_snapshot_rejects_forged_inventory_and_unbacked_terminal_chunk(
    tmp_path: Path, sql: str, value: str, match: str,
) -> None:
    path, fence, handoff = _prepare(tmp_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, (value, handoff.file_id))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(Phase2StateError, match=match):
        claim_next_phase2_file(fence, path=path)
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,active_generation FROM analyst_files WHERE file_id=?",
            (handoff.file_id,),
        ).fetchone()) == ("pending", None)
    finally:
        conn.close()


def test_health_obligation_blocks_scored_chat_until_answered_health(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    first = _finish_chat(
        path, fence, chunk_id, ContactStatus.REQUEST_TIMEOUT, "timeout-one",
    )
    assert first.semantic_attempt_no == 1

    obligation = load_health_obligation(fence, path=path)
    assert obligation is not None
    assert obligation.source_contact_id == first.contact_id
    assert obligation.source_status == "request_timeout"
    conn = open_connection(path, read_only=True)
    try:
        before = conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts",
        ).fetchone()[0]
    finally:
        conn.close()
    with pytest.raises(OllamaStateError, match="recovery-health"):
        precharge_chat_contact(
            fence, chunk_id, _sha("blocked"), now_utc=_NOW, path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts",
        ).fetchone()[0] == before
    finally:
        conn.close()

    _answer_health(path, fence)
    assert load_health_obligation(fence, path=path) is None
    second = precharge_chat_contact(
        fence, chunk_id, _sha("attempt-two"), now_utc=_NOW, path=path,
    )
    assert second.semantic_attempt_no == 2


def test_forged_health_request_cannot_clear_delivery_obligation(tmp_path: Path) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    _finish_chat(
        path, fence, chunk_id, ContactStatus.TRANSPORT_UNAVAILABLE,
        "transport-one",
    )
    conn = open_connection(path, read_only=True)
    try:
        before = conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts",
        ).fetchone()[0]
    finally:
        conn.close()

    with pytest.raises(ValueError, match="frozen intent"):
        precharge_control_contact(
            fence,
            ContactKind.CANCELLATION_HEALTH,
            _sha("not-the-public-health-request"),
            now_utc=_NOW,
            path=path,
        )
    assert load_health_obligation(fence, path=path) is not None
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts",
        ).fetchone()[0] == before
    finally:
        conn.close()


def test_success_contact_orphaned_before_chunk_checkpoint_requires_health(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    finished = _finish_chat(
        path, fence, chunk_id, ContactStatus.SUCCESS, "answered-before-crash",
    )
    assert finished.attempt_id is not None
    assert release_worker(fence, path=path).value == "interrupted"
    successor = claim_worker(
        fence.run_id,
        fence.process,
        owner_token="e" * 64,
        heartbeat_monotonic_ns=fence.heartbeat_monotonic_ns + 100,
        path=path,
    )
    assert successor is not None

    obligation = load_health_obligation(successor, path=path)
    assert obligation is not None
    assert obligation.source_contact_id == finished.contact_id
    assert obligation.source_status == "orphaned_unknown"
    with pytest.raises(OllamaStateError, match="recovery-health"):
        precharge_chat_contact(
            successor, chunk_id, _sha("blocked-after-orphan"), path=path,
        )


def test_full_audit_rejects_tampered_scored_chat_bypassing_health(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    _finish_chat(
        path, fence, chunk_id, ContactStatus.REQUEST_TIMEOUT, "timeout-one",
    )
    _answer_health(path, fence)
    _finish_chat(
        path, fence, chunk_id, ContactStatus.REQUEST_TIMEOUT, "timeout-two",
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE analyst_ollama_contacts SET state='transport_unavailable' "
            "WHERE kind='cancellation_health'",
        )
        conn.commit()
        with pytest.raises(AnalystSchemaError, match="recovery-health"):
            validate_schema(conn)
    finally:
        conn.close()

@pytest.mark.parametrize(
    "status",
    [
        ContactStatus.IDENTITY_MISMATCH,
        ContactStatus.PROTOCOL_VIOLATION,
        ContactStatus.RESPONSE_LIMIT,
    ],
)
def test_nonretryable_contact_closes_pending_chunk_as_transport_error(
    tmp_path: Path, status: ContactStatus,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    _finish_chat(path, fence, chunk_id, status, status.value)

    close_nonretryable_chunk(fence, chunk_id, path=path)
    refreshed = load_phase2_snapshot(fence, snapshot.file_id, path=path)
    assert refreshed.chunks[0].state is ChunkState.MODEL_TRANSPORT_ERROR
    completion = finish_phase2_file(fence, snapshot.file_id, path=path)
    assert completion.terminal is FileTerminal.MODEL_TRANSPORT_ERROR


def test_only_second_ambiguous_attempt_can_be_closed_as_exhausted(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    chunk_id = snapshot.chunks[0].identity.chunk_id
    _finish_chat(
        path, fence, chunk_id, ContactStatus.REQUEST_TIMEOUT, "timeout-one",
    )
    with pytest.raises(Phase2StateError, match="exhausted"):
        close_exhausted_ambiguous_chunk(fence, chunk_id, path=path)
    _answer_health(path, fence)
    _finish_chat(
        path, fence, chunk_id, ContactStatus.CANCELLED_UNVERIFIED,
        "cancelled-two",
    )
    close_exhausted_ambiguous_chunk(fence, chunk_id, path=path)
    assert load_phase2_snapshot(
        fence, snapshot.file_id, path=path,
    ).chunks[0].state is ChunkState.MODEL_TRANSPORT_ERROR


def test_overlap_duplicate_is_removed_by_absolute_span_and_file_finishes(
    tmp_path: Path,
) -> None:
    quote = "public@example.test"
    body = "x" * 7800 + quote + "y" * 300
    path, fence, snapshot = _claim(tmp_path, body)
    assert len(snapshot.chunks) == 2
    first_chunk, second_chunk = snapshot.chunks
    first_offset = 7800
    second_offset = first_offset - second_chunk.identity.start

    first_finding = GroundedFinding(
        Category.CONTACT, quote, first_offset, first_offset,
        first_offset + len(quote), 1, True,
    )
    first_contact = _finish_chat(
        path, fence, first_chunk.identity.chunk_id,
        ContactStatus.SUCCESS, "first-success",
    )
    assert first_contact.attempt_id is not None
    first_result = deduplicate_grounded_result(
        fence, first_chunk.identity.chunk_id, _result(first_finding), path=path,
    )
    assert first_result.findings == (first_finding,)
    finish_valid_attempt(
        fence, first_contact.attempt_id, first_result, now_utc=_NOW, path=path,
    )

    duplicate = GroundedFinding(
        Category.CONTACT, quote, second_offset, second_offset,
        second_offset + len(quote), 1, True,
    )
    second_contact = _finish_chat(
        path, fence, second_chunk.identity.chunk_id,
        ContactStatus.SUCCESS, "second-success",
    )
    assert second_contact.attempt_id is not None
    deduplicated = deduplicate_grounded_result(
        fence, second_chunk.identity.chunk_id, _result(duplicate), path=path,
    )
    assert deduplicated.findings == ()
    assert deduplicated.model_assessment is Assessment.NO_FINDINGS
    assert deduplicated.removed_duplicate_count == 1
    finish_valid_attempt(
        fence, second_contact.attempt_id, deduplicated,
        now_utc=_NOW, path=path,
    )

    completion = finish_phase2_file(
        fence, snapshot.file_id, now_utc=_NOW, path=path,
    )
    assert completion.terminal is FileTerminal.COMPLETE_MODEL_REVIEWED
    assert completion.valid_chunk_count == 2
    assert completion.retained_finding_count == 1
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,work_state,terminal_code FROM analyst_files WHERE file_id=?",
            (snapshot.file_id,),
        ).fetchone()
        assert tuple(row) == (
            "model_response_valid", "terminal", "complete_model_reviewed",
        )
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_findings",
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_finish_rejects_pending_chunks_without_partial_stage_write(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path)
    with pytest.raises(Phase2StateError, match="pending"):
        finish_phase2_file(fence, snapshot.file_id, now_utc=_NOW, path=path)
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,work_state,terminal_code FROM analyst_files WHERE file_id=?",
            (snapshot.file_id,),
        ).fetchone()
        assert tuple(row) == ("selected_for_model", "active", None)
    finally:
        conn.close()


def test_mixed_chunk_failures_use_transport_then_timeout_then_invalid_precedence(
    tmp_path: Path,
) -> None:
    path, fence, snapshot = _claim(tmp_path, "x" * 16_000)
    assert len(snapshot.chunks) == 3
    invalid, timed_out, transport = (
        item.identity.chunk_id for item in snapshot.chunks
    )

    _finish_chat(
        path, fence, invalid, ContactStatus.MODEL_INVALID, "invalid-one",
    )
    _finish_chat(
        path, fence, invalid, ContactStatus.MODEL_INVALID, "invalid-two",
    )
    _finish_chat(
        path, fence, timed_out, ContactStatus.REQUEST_TIMEOUT, "timeout-one",
    )
    _answer_health(path, fence)
    _finish_chat(
        path, fence, timed_out, ContactStatus.REQUEST_TIMEOUT, "timeout-two",
    )
    _answer_health(path, fence)
    _finish_chat(
        path, fence, transport, ContactStatus.IDENTITY_MISMATCH,
        "identity-one",
    )
    close_nonretryable_chunk(fence, transport, path=path)

    completion = finish_phase2_file(fence, snapshot.file_id, path=path)
    assert completion.terminal is FileTerminal.MODEL_TRANSPORT_ERROR
    assert completion.valid_chunk_count == 0
    assert completion.retained_finding_count == 0
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,work_state,terminal_code FROM analyst_files WHERE file_id=?",
            (snapshot.file_id,),
        ).fetchone()
        assert tuple(row) == (
            "model_reviewed", "terminal", "model_transport_error",
        )
    finally:
        conn.close()
