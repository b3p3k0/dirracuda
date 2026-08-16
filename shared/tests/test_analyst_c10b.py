"""C10B durable Phase 1 state-engine acceptance tests."""

from __future__ import annotations

import hashlib
import gc
import multiprocessing
import os
import sqlite3
import threading
import time
import weakref
from types import SimpleNamespace
from pathlib import Path

import pytest

from experimental.analyst import detectors
from experimental.analyst.checkpoint import (
    CheckpointError,
    ChunkSpec,
    ExtractionCheckpointEvidence,
    ProvenanceUnit,
    advance_file_stage,
    checkpoint_detector,
    terminalize_file,
)
from experimental.analyst.chunking import chunk_text
from experimental.analyst.detectors import scan, scan_bounded
from experimental.analyst.inventory import inventory_tree
from experimental.analyst.lease import (
    LeaseError,
    claim_worker,
    pulse_worker,
    reconcile_lease,
    release_worker,
    request_cancel,
)
from experimental.analyst.models import (
    ANALYST_DEFAULTS,
    DetectorHit,
    FileStage,
    FileTerminal,
)
from experimental.analyst.phase1 import (
    Phase1Cancelled,
    Phase1Dependencies,
    Phase1Error,
    Phase1Failure,
    Phase1Interrupted,
    run_phase1,
)
from experimental.analyst.phase1_state import (
    FileResumeSnapshot,
    Phase1FileHandoff,
    claim_next_phase1_file,
    handoff_selected_file,
    load_file_resume_snapshot,
    load_phase1_handoff,
    verify_detector_checkpoint,
    verify_extraction_evidence,
)
from experimental.analyst.process_identity import ProcessIdentity
from experimental.analyst.store import (
    RunSpec,
    create_run,
    initialize_database,
    load_worker_run,
    open_connection,
)
from experimental.analyst.worker_contract import build_source_identity
from experimental.analyst.worker_contract import WorkerContractError
from experimental.analyst.worksheet import prompt_template_hash, schema_hash


_NOW = "2026-08-16T15:00:00Z"
_PROCESS = ProcessIdentity(
    8111, 9222, "12345678-1234-5678-1234-567812345678",
)


def _run(
    tmp_path: Path,
    *,
    run_id: str = "public-c10b",
    bodies: tuple[str, ...] = ("public text",),
    mode: str = "fast",
):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    for index, body in enumerate(bodies):
        (source / f"public-{index}.txt").write_text(body, encoding="utf-8")
    inventory = inventory_tree(source)
    db_path = tmp_path / "state" / "analyst.db"
    initialize_database(db_path)
    spec = RunSpec(
        run_id=run_id,
        mode=mode,
        source_mode="unknown",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        source_identity=build_source_identity(inventory),
        report_label="Public C10B",
        model_tag=ANALYST_DEFAULTS.model_tag,
        model_digest=ANALYST_DEFAULTS.model_digest,
        worksheet_version=ANALYST_DEFAULTS.worksheet_version,
        prompt_sha256=prompt_template_hash(),
        response_schema_sha256=schema_hash(),
        detector_rules_version="rules-v1",
        detector_rules_sha256="d" * 64,
        parser_bundle={"bundle": "public-c10b", "version": 1},
        chunk_chars=8000,
        overlap_chars=256,
        num_ctx=8192,
        num_predict=1024,
        isolation_mode="strict",
        reduced_isolation_ack=False,
    )
    create_run(spec, inventory, path=db_path, now_utc=_NOW)
    fence = claim_worker(
        run_id,
        _PROCESS,
        owner_token="a" * 64,
        heartbeat_monotonic_ns=10,
        now_utc=_NOW,
        path=db_path,
    )
    assert fence is not None
    return db_path, spec, inventory, fence


def _text_evidence(text: str) -> ExtractionCheckpointEvidence:
    return ExtractionCheckpointEvidence(
        encoding="utf-8",
        parser_identity={"parser": "builtin_text"},
        extraction_counts={
            "text_bytes": len(text.encode("utf-8")),
            "text_chars": len(text),
            "logical_unit_count": 1,
        },
        provenance=(
            ProvenanceUnit("output_line", "output-line-1", 0, len(text)),
        ),
    )


def _to_text_extracted(db_path: Path, fence, file_id: int, text: str) -> None:
    evidence = _text_evidence(text)
    advance_file_stage(
        fence,
        file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="text",
        path=db_path,
    )
    advance_file_stage(
        fence,
        file_id,
        FileStage.TEXT_EXTRACTED,
        encoding=evidence.encoding,
        parser_identity=evidence.parser_identity,
        extraction_meta=evidence.extraction_counts,
        provenance=evidence.provenance,
        path=db_path,
    )


def _chunk_specs(text: str) -> tuple[ChunkSpec, ...]:
    return tuple(
        ChunkSpec(
            chunk.index,
            chunk.start,
            chunk.end,
            hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        )
        for chunk in chunk_text(text, chunk_chars=8000, overlap_chars=256)
    )


def _crash_after_claim(db_path: str, fence) -> None:
    snapshot = claim_next_phase1_file(fence, path=Path(db_path), now_utc=_NOW)
    if snapshot is None:
        os._exit(91)
    os._exit(0)


def _crash_after_boundary(db_path: str, fence, text: str, boundary: str) -> None:
    path = Path(db_path)
    snapshot = claim_next_phase1_file(fence, path=path, now_utc=_NOW)
    if snapshot is None:
        os._exit(91)
    _to_text_extracted(path, fence, snapshot.file_id, text)
    if boundary in {"detector", "handoff"}:
        checkpoint_detector(
            fence,
            snapshot.file_id,
            (),
            selected_for_model=True,
            path=path,
        )
    if boundary == "handoff":
        handoff_selected_file(
            fence,
            snapshot.file_id,
            _chunk_specs(text),
            path=path,
            now_utc=_NOW,
        )
    os._exit(0)


def _claim_to_queue(db_path: str, fence, queue) -> None:
    snapshot = claim_next_phase1_file(fence, path=Path(db_path), now_utc=_NOW)
    queue.put(None if snapshot is None else snapshot.file_id)


def _prepare_handoff(
    tmp_path: Path, text: str = "public text", *, mode: str = "deep",
):
    db_path, spec, inventory, fence = _run(
        tmp_path, bodies=(text,), mode=mode,
    )
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        (),
        selected_for_model=True,
        path=db_path,
    )
    handoff = handoff_selected_file(
        fence, snapshot.file_id, _chunk_specs(text), path=db_path,
    )
    return db_path, spec, inventory, fence, handoff


def _unsafe_update(
    db_path: Path,
    sql: str,
    values: tuple[object, ...],
    *,
    ignore_checks: bool = False,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        if ignore_checks:
            conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(sql, values)
        conn.commit()
    finally:
        conn.close()


def _raw_immediate(operation, *, path: Path | None = None):
    assert path is not None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return operation(conn)
    finally:
        conn.close()


def _success_extract(
    source_fd: int, expected, cancel_check,
):
    assert cancel_check() is False
    body = os.pread(source_fd, expected.size, 0).decode("utf-8")
    from experimental.analyst.extract import ExtractionResult

    return ExtractionResult("success", "text", "utf-8", body)


def _exit_extract(_source_fd: int, _expected, _cancel_check):
    os._exit(0)


def _run_phase1_crash(db_path: str, run_id: str, fence) -> None:
    context = load_worker_run(run_id, path=Path(db_path))
    dependencies = Phase1Dependencies(extract=_exit_extract)
    run_phase1(
        context,
        fence,
        threading.Event(),
        path=Path(db_path),
        dependencies=dependencies,
    )


def _email_corpus(count: int) -> str:
    return " ".join(f"public-{index:05d}@example.test" for index in range(count))


def test_scan_bounded_accepts_exact_detector_limit_in_source_order() -> None:
    text = _email_corpus(10_000)

    hits, overflow = scan_bounded(text, max_hits=10_000)

    assert overflow is False
    assert len(hits) == 10_000
    assert hits[0].value == "public-00000@example.test"
    assert hits[-1].value == "public-09999@example.test"
    assert tuple((hit.start, hit.end, hit.kind) for hit in hits) == tuple(
        sorted((hit.start, hit.end, hit.kind) for hit in hits)
    )


def test_scan_bounded_rejects_limit_plus_one_without_partial_evidence() -> None:
    text = _email_corpus(10_001)

    hits, overflow = scan_bounded(text, max_hits=10_000)

    assert hits == []
    assert overflow is True


def test_scan_bounded_stops_immediately_at_unique_limit_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MustNotScanLater:
        def finditer(self, _text: str):
            raise AssertionError("detector scan continued after unique N+1")

    monkeypatch.setattr(detectors, "_PHONE", MustNotScanLater())

    assert scan_bounded(
        "first@example.test second@example.test", max_hits=1,
    ) == ([], True)


def test_scan_bounded_overlap_is_not_counted_as_a_duplicate_hit() -> None:
    text = "The public response was not hispanic or latino."

    hits, overflow = scan_bounded(text, max_hits=1)

    assert overflow is False
    assert [(hit.kind, hit.value) for hit in hits] == [
        ("demographic_term", "not hispanic or latino"),
    ]


def test_scan_bounded_restores_source_order_after_detector_order() -> None:
    text = "first@example.test then 123-45-6789 then 212-555-0119"

    hits, overflow = scan_bounded(text, max_hits=3)

    assert overflow is False
    assert [hit.kind for hit in hits] == ["email", "ssn", "phone"]
    assert hits == scan(text)


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, "1", None])
def test_scan_bounded_rejects_invalid_exact_limit_types(limit: object) -> None:
    with pytest.raises(ValueError):
        scan_bounded("public@example.test", max_hits=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [b"public", 1, True, None, SimpleNamespace()])
def test_scan_bounded_rejects_non_text_without_partial_output(text: object) -> None:
    with pytest.raises(TypeError):
        scan_bounded(text, max_hits=1)  # type: ignore[arg-type]


def test_phase1_claim_returns_exact_content_free_discovered_snapshot(
    tmp_path: Path,
) -> None:
    db_path, _spec, inventory, fence = _run(tmp_path)

    snapshot = claim_next_phase1_file(fence, path=db_path, now_utc=_NOW)

    assert isinstance(snapshot, FileResumeSnapshot)
    assert snapshot.file_id > 0
    assert snapshot.ordinal == 0
    assert snapshot.inventory_file == inventory.files[0]
    assert snapshot.stage is FileStage.DISCOVERED
    assert snapshot.format_name is None
    assert snapshot.encoding is None
    assert snapshot.selected_for_model is None
    assert snapshot.parser_identity_json is None
    assert snapshot.parser_identity_sha256 is None
    assert snapshot.extraction_meta_json is None
    assert snapshot.extraction_meta_sha256 is None
    assert snapshot.detector_hit_count == 0
    assert snapshot.chunks == ()
    assert load_file_resume_snapshot(
        fence, snapshot.file_id, path=db_path,
    ) == snapshot


def test_phase1_claim_orders_files_and_does_not_reclaim_active(
    tmp_path: Path,
) -> None:
    db_path, _spec, inventory, fence = _run(
        tmp_path, bodies=("zero", "one", "two"),
    )

    snapshots = tuple(
        claim_next_phase1_file(fence, path=db_path, now_utc=_NOW)
        for _ in range(3)
    )

    assert [item.ordinal for item in snapshots if item is not None] == [0, 1, 2]
    assert [item.inventory_file.relative_path for item in snapshots if item is not None] == [
        item.relative_path for item in inventory.files
    ]
    assert claim_next_phase1_file(fence, path=db_path, now_utc=_NOW) is None


@pytest.mark.parametrize("stage", [FileStage.TEXT_EXTRACTED, FileStage.DETECTOR_SCANNED])
def test_phase1_resume_snapshot_reports_exact_durable_counts(
    tmp_path: Path, stage: FileStage,
) -> None:
    text = "public@example.test"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    first = claim_next_phase1_file(fence, path=db_path)
    assert first is not None
    _to_text_extracted(db_path, fence, first.file_id, text)
    if stage is FileStage.DETECTOR_SCANNED:
        checkpoint_detector(
            fence,
            first.file_id,
            scan(text),
            selected_for_model=True,
            path=db_path,
        )

    snapshot = load_file_resume_snapshot(fence, first.file_id, path=db_path)

    assert snapshot.stage is stage
    assert snapshot.format_name == "text"
    assert snapshot.encoding == "utf-8"
    assert snapshot.parser_identity_json == '{"parser":"builtin_text"}'
    assert snapshot.parser_identity_sha256 == hashlib.sha256(
        snapshot.parser_identity_json.encode("utf-8")
    ).hexdigest()
    assert snapshot.extraction_meta_json is not None
    assert snapshot.extraction_meta_sha256 == hashlib.sha256(
        snapshot.extraction_meta_json.encode("utf-8")
    ).hexdigest()
    assert snapshot.detector_hit_count == (1 if stage is FileStage.DETECTOR_SCANNED else 0)
    assert snapshot.selected_for_model is (
        True if stage is FileStage.DETECTOR_SCANNED else None
    )
    assert snapshot.chunks == ()


def test_verify_regenerated_extraction_evidence_exact_and_read_only(
    tmp_path: Path,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    evidence = _text_evidence(text)
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    before = db_path.read_bytes()

    verify_extraction_evidence(
        fence,
        snapshot.file_id,
        evidence,
        authenticated_format_name="text",
        path=db_path,
    )

    assert db_path.read_bytes() == before


@pytest.mark.parametrize("drift", ["encoding", "parser", "counts", "provenance"])
def test_verify_regenerated_extraction_rejects_every_identity_drift(
    tmp_path: Path, drift: str,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    expected = _text_evidence(text)
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    values = {
        "encoding": expected.encoding,
        "parser_identity": expected.parser_identity,
        "extraction_counts": expected.extraction_counts,
        "provenance": expected.provenance,
    }
    if drift == "encoding":
        values["encoding"] = "windows-1252"
    elif drift == "parser":
        values["parser_identity"] = {"parser": "builtin_rtf"}
    elif drift == "counts":
        values["extraction_counts"] = {
            **expected.extraction_counts,
            "text_chars": len(text) + 1,
        }
    else:
        values["provenance"] = (
            ProvenanceUnit("output_line", "output-line-2", 0, len(text)),
        )
    forged = ExtractionCheckpointEvidence(**values)
    before = db_path.read_bytes()

    with pytest.raises(CheckpointError, match="does not match"):
        verify_extraction_evidence(
            fence,
            snapshot.file_id,
            forged,
            authenticated_format_name="text",
            path=db_path,
        )

    assert db_path.read_bytes() == before


def test_verify_detector_checkpoint_exact_order_value_and_selection(
    tmp_path: Path,
) -> None:
    text = "first@example.test then 123-45-6789"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    hits = scan(text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        hits,
        selected_for_model=True,
        path=db_path,
    )
    before = db_path.read_bytes()

    verify_detector_checkpoint(
        fence,
        snapshot.file_id,
        hits,
        selected_for_model=True,
        path=db_path,
    )

    assert db_path.read_bytes() == before
    cases = (
        (tuple(reversed(hits)), True),
        ((DetectorHit("email", "other@example.test", 0, 18), *hits[1:]), True),
        (hits, False),
    )
    for forged, selected in cases:
        with pytest.raises(CheckpointError, match="does not match"):
            verify_detector_checkpoint(
                fence,
                snapshot.file_id,
                forged,
                selected_for_model=selected,
                path=db_path,
            )


def test_verify_detector_checkpoint_rejects_limit_plus_one_before_database(
    tmp_path: Path,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        (),
        selected_for_model=False,
        path=db_path,
    )
    hits = (
        DetectorHit("email", "x@example.test", 0, 1)
        for _ in range(10_001)
    )

    with pytest.raises(ValueError, match="cap"):
        verify_detector_checkpoint(
            fence,
            snapshot.file_id,
            hits,
            selected_for_model=False,
            path=db_path,
        )


def test_selected_file_handoff_is_atomic_pending_and_not_reclaimable(
    tmp_path: Path,
) -> None:
    text = "A" * 8200
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    file_id = snapshot.file_id
    _to_text_extracted(db_path, fence, file_id, text)
    checkpoint_detector(
        fence, file_id, (), selected_for_model=True, path=db_path,
    )
    chunks = _chunk_specs(text)

    handoff = handoff_selected_file(
        fence, file_id, chunks, path=db_path, now_utc=_NOW,
    )

    assert isinstance(handoff, Phase1FileHandoff)
    assert handoff.file_id == file_id
    assert handoff.ordinal == snapshot.ordinal
    assert len(handoff.chunks) == len(chunks) == 2
    assert len({item.chunk_id for item in handoff.chunks}) == len(chunks)
    assert tuple(
        (item.index, item.start, item.end, item.sha256) for item in handoff.chunks
    ) == tuple(
        (item.index, item.start, item.end, item.sha256) for item in chunks
    )
    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,work_state,active_generation FROM analyst_files "
            "WHERE file_id=?", (file_id,),
        ).fetchone()
        assert tuple(row) == ("selected_for_model", "pending", None)
        stored = conn.execute(
            "SELECT chunk_index,start_char,end_char,chunk_sha256,state "
            "FROM analyst_chunks WHERE file_id=? ORDER BY chunk_index", (file_id,),
        ).fetchall()
        assert tuple(tuple(item) for item in stored) == tuple(
            (item.index, item.start, item.end, item.sha256, "pending")
            for item in chunks
        )
    finally:
        conn.close()
    assert claim_next_phase1_file(fence, path=db_path) is None
    with pytest.raises(CheckpointError):
        load_file_resume_snapshot(fence, file_id, path=db_path)


@pytest.mark.parametrize("drift", ["empty", "gap", "stride", "hash_type"])
def test_selected_handoff_rejects_bad_chunks_with_zero_partial_mutation(
    tmp_path: Path, drift: str,
) -> None:
    text = "A" * 8200
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    file_id = snapshot.file_id
    _to_text_extracted(db_path, fence, file_id, text)
    checkpoint_detector(
        fence, file_id, (), selected_for_model=True, path=db_path,
    )
    valid = _chunk_specs(text)
    if drift == "empty":
        forged = ()
    elif drift == "gap":
        forged = (ChunkSpec(0, 1, valid[0].end, valid[0].sha256), valid[1])
    elif drift == "stride":
        forged = (valid[0], ChunkSpec(1, valid[1].start + 1, valid[1].end, valid[1].sha256))
    else:
        forged = (object(),)

    with pytest.raises((TypeError, ValueError)):
        handoff_selected_file(fence, file_id, forged, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_chunks WHERE file_id=?", (file_id,),
        ).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT stage,work_state,active_generation FROM analyst_files "
            "WHERE file_id=?", (file_id,),
        ).fetchone()) == ("detector_scanned", "active", fence.generation)
    finally:
        conn.close()


def test_worker_pulse_returns_strict_successor_and_stales_prior_fence(
    tmp_path: Path,
) -> None:
    db_path, _spec, _inventory_result, fence = _run(tmp_path)

    pulse = pulse_worker(
        fence,
        heartbeat_monotonic_ns=11,
        now_utc="2026-08-16T15:00:01Z",
        path=db_path,
    )

    assert pulse.cancel_requested is False
    assert pulse.fence.heartbeat_monotonic_ns == 11
    with pytest.raises((CheckpointError, LeaseError)):
        claim_next_phase1_file(fence, path=db_path)
    assert claim_next_phase1_file(pulse.fence, path=db_path) is not None
    before = db_path.read_bytes()
    for value in (11, 10):
        with pytest.raises(LeaseError, match="advance"):
            pulse_worker(
                pulse.fence, heartbeat_monotonic_ns=value, path=db_path,
            )
    assert db_path.read_bytes() == before


def test_worker_pulse_reads_durable_cancel_with_successor_fence(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory_result, fence = _run(tmp_path)
    returned = request_cancel(spec.run_id, path=db_path, now_utc=_NOW)
    assert returned == fence

    pulse = pulse_worker(
        fence, heartbeat_monotonic_ns=12, path=db_path, now_utc=_NOW,
    )

    assert pulse.cancel_requested is True
    assert pulse.fence.heartbeat_monotonic_ns == 12


def test_real_crash_after_phase1_claim_reconciles_and_resumes(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory_result, fence = _run(tmp_path)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_claim,
        args=(str(db_path), fence),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 0

    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=20,
        now_utc="2026-08-16T15:00:02Z",
        identity_reader=lambda _pid: None,
    ).value == "cleared_interrupted"
    successor = claim_worker(
        spec.run_id,
        ProcessIdentity(8112, 9223, "12345678-1234-5678-1234-567812345678"),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=21,
        now_utc="2026-08-16T15:00:03Z",
        path=db_path,
    )
    assert successor is not None
    resumed = claim_next_phase1_file(successor, path=db_path)
    assert resumed is not None
    assert resumed.stage is FileStage.DISCOVERED
    assert resumed.file_id > 0
    with pytest.raises(CheckpointError):
        load_file_resume_snapshot(fence, resumed.file_id, path=db_path)


def test_verify_extraction_requires_exact_authenticated_format(
    tmp_path: Path,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    evidence = _text_evidence(text)
    _to_text_extracted(db_path, fence, snapshot.file_id, text)

    for forged in ("rtf", "ooxml", "legacy_office", "", True, None):
        with pytest.raises((CheckpointError, TypeError, ValueError)):
            verify_extraction_evidence(
                fence,
                snapshot.file_id,
                evidence,
                authenticated_format_name=forged,  # type: ignore[arg-type]
                path=db_path,
            )


def test_load_phase1_handoff_round_trips_ordered_chunk_identities(
    tmp_path: Path,
) -> None:
    bodies = ("A" * 8200, "B" * 100)
    db_path, _spec, _inventory_result, fence = _run(
        tmp_path, bodies=bodies, mode="deep",
    )
    created = []
    for text in bodies:
        snapshot = claim_next_phase1_file(fence, path=db_path)
        assert snapshot is not None
        _to_text_extracted(db_path, fence, snapshot.file_id, text)
        checkpoint_detector(
            fence,
            snapshot.file_id,
            (),
            selected_for_model=True,
            path=db_path,
        )
        created.append(handoff_selected_file(
            fence,
            snapshot.file_id,
            _chunk_specs(text),
            path=db_path,
        ))

    loaded = load_phase1_handoff(fence, path=db_path)

    assert loaded == tuple(created)
    assert [(item.ordinal, item.file_id) for item in loaded] == sorted(
        (item.ordinal, item.file_id) for item in loaded
    )
    assert sum(len(item.chunks) for item in loaded) == 3
    assert len({chunk.chunk_id for item in loaded for chunk in item.chunks}) == 3
    assert "A" * 16 not in repr(loaded)
    assert "B" * 16 not in repr(loaded)


def test_handoff_rejects_unselected_and_duplicate_without_mutation(
    tmp_path: Path,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        (),
        selected_for_model=False,
        path=db_path,
    )
    before = db_path.read_bytes()

    with pytest.raises(CheckpointError, match="selected"):
        handoff_selected_file(
            fence,
            snapshot.file_id,
            _chunk_specs(text),
            path=db_path,
        )
    assert db_path.read_bytes() == before


def test_snapshot_repr_excludes_path_and_checkpoint_json(tmp_path: Path) -> None:
    marker = "PUBLIC_C10B_PRIVATE_PATH_MARKER"
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(
        tmp_path / marker, bodies=(text,),
    )
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    resumed = load_file_resume_snapshot(fence, snapshot.file_id, path=db_path)
    rendered = repr(resumed)

    assert marker not in rendered
    assert "builtin_text" not in rendered
    assert "logical_unit_count" not in rendered


@pytest.mark.parametrize(
    ("boundary", "expected_stage"),
    [
        ("extraction", FileStage.TEXT_EXTRACTED),
        ("detector", FileStage.DETECTOR_SCANNED),
        ("handoff", FileStage.SELECTED_FOR_MODEL),
    ],
)
def test_real_crash_resume_preserves_each_phase1_checkpoint(
    tmp_path: Path, boundary: str, expected_stage: FileStage,
) -> None:
    text = "A" * 8200
    db_path, spec, _inventory_result, fence = _run(
        tmp_path, bodies=(text,), mode="deep",
    )
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_boundary,
        args=(str(db_path), fence, text, boundary),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 0
    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=20,
        now_utc="2026-08-16T15:00:02Z",
        identity_reader=lambda _pid: None,
    ).value == "cleared_interrupted"
    successor = claim_worker(
        spec.run_id,
        ProcessIdentity(8112, 9223, "12345678-1234-5678-1234-567812345678"),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=21,
        now_utc="2026-08-16T15:00:03Z",
        path=db_path,
    )
    assert successor is not None

    if boundary == "handoff":
        assert claim_next_phase1_file(successor, path=db_path) is None
        handoff = load_phase1_handoff(successor, path=db_path)
        assert len(handoff) == 1
        assert len(handoff[0].chunks) == 2
    else:
        resumed = claim_next_phase1_file(successor, path=db_path)
        assert resumed is not None
        assert resumed.stage is expected_stage
        if boundary == "extraction":
            verify_extraction_evidence(
                successor,
                resumed.file_id,
                _text_evidence(text),
                authenticated_format_name="text",
                path=db_path,
            )
        else:
            verify_detector_checkpoint(
                successor,
                resumed.file_id,
                (),
                selected_for_model=True,
                path=db_path,
            )


def test_two_process_phase1_claim_race_has_one_winner(
    tmp_path: Path,
) -> None:
    db_path, _spec, _inventory_result, fence = _run(tmp_path)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_claim_to_queue, args=(str(db_path), fence, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    results = sorted((queue.get(timeout=2) for _ in processes), key=lambda x: x is None)

    assert sum(value is not None for value in results) == 1
    assert sum(value is None for value in results) == 1


def test_snapshot_and_handoff_dataclasses_reject_counterfeit_types(
    tmp_path: Path,
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None

    from dataclasses import replace

    for field, value in (
        ("file_id", True),
        ("ordinal", True),
        ("stage", FileStage.MODEL_REVIEWED),
        ("detector_hit_count", True),
        ("selected_for_model", 1),
        ("chunks", []),
    ):
        with pytest.raises(WorkerContractError):
            replace(snapshot, **{field: value})


@pytest.mark.parametrize("bounds", [(0, 12), (11, 12), (1, 11)])
def test_handoff_rejects_chunk_bounds_outside_exact_extracted_text(
    tmp_path: Path, bounds: tuple[int, int],
) -> None:
    text = "public text"
    db_path, _spec, _inventory_result, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence, snapshot.file_id, (), selected_for_model=True, path=db_path,
    )
    forged = ChunkSpec(
        0,
        bounds[0],
        bounds[1],
        hashlib.sha256(b"forged").hexdigest(),
    )

    with pytest.raises(ValueError):
        handoff_selected_file(
            fence, snapshot.file_id, (forged,), path=db_path,
        )


@pytest.mark.parametrize("drift", ["stage", "active", "generation"])
def test_load_handoff_rejects_any_nonterminal_outside_exact_handoff_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from experimental.analyst import phase1_state

    db_path, _spec, _inventory_result, fence, handoff = _prepare_handoff(tmp_path)
    if drift == "stage":
        sql = "UPDATE analyst_files SET stage='detector_scanned' WHERE file_id=?"
        values = (handoff.file_id,)
    elif drift == "active":
        sql = (
            "UPDATE analyst_files SET work_state='active',active_generation=? "
            "WHERE file_id=?"
        )
        values = (fence.generation, handoff.file_id)
    else:
        sql = "UPDATE analyst_files SET active_generation=? WHERE file_id=?"
        values = (fence.generation, handoff.file_id)
    _unsafe_update(db_path, sql, values, ignore_checks=drift == "generation")
    monkeypatch.setattr(phase1_state, "run_immediate", _raw_immediate)

    with pytest.raises(CheckpointError, match="unfinished"):
        load_phase1_handoff(fence, path=db_path)


@pytest.mark.parametrize(
    ("drift", "sql", "values", "ignore_checks"),
    [
        (
            "candidate format",
            "UPDATE analyst_files SET format_name='ooxml' WHERE file_id=?",
            (),
            False,
        ),
        (
            "unknown format",
            "UPDATE analyst_files SET format_name='unknown' WHERE file_id=?",
            (),
            True,
        ),
        (
            "noncanonical parser",
            "UPDATE analyst_files SET parser_identity_json=? WHERE file_id=?",
            ('{"parser": "builtin_text"}',),
            False,
        ),
        (
            "parser hash",
            "UPDATE analyst_files SET parser_identity_sha256=? WHERE file_id=?",
            ("0" * 64,),
            False,
        ),
        (
            "invalid extraction",
            "UPDATE analyst_files SET extraction_meta_json=? WHERE file_id=?",
            ('{"text_bytes":11}',),
            False,
        ),
        (
            "noncanonical extraction",
            "UPDATE analyst_files SET extraction_meta_json=? WHERE file_id=?",
            ('{"logical_unit_count": 1, "text_bytes": 11, "text_chars": 11}',),
            False,
        ),
        (
            "empty selected text",
            "UPDATE analyst_files SET extraction_meta_json=? WHERE file_id=?",
            ('{"logical_unit_count":1,"text_bytes":0,"text_chars":0}',),
            False,
        ),
    ],
)
def test_load_handoff_revalidates_exact_extraction_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    sql: str,
    values: tuple[object, ...],
    ignore_checks: bool,
) -> None:
    from experimental.analyst import phase1_state

    db_path, _spec, _inventory_result, fence, handoff = _prepare_handoff(tmp_path)
    _unsafe_update(
        db_path,
        sql,
        (*values, handoff.file_id),
        ignore_checks=ignore_checks,
    )
    monkeypatch.setattr(phase1_state, "run_immediate", _raw_immediate)

    with pytest.raises(
        (CheckpointError, WorkerContractError),
        match="(?:format|extraction|extracted text)",
    ):
        load_phase1_handoff(fence, path=db_path)


@pytest.mark.parametrize("drift", ["start", "end", "coverage"])
def test_load_handoff_rejects_noncanonical_durable_chunk_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from experimental.analyst import phase1_state

    text = "A" * 8200
    db_path, _spec, _inventory_result, fence, handoff = _prepare_handoff(
        tmp_path, text,
    )
    first, second = handoff.chunks
    if drift == "start":
        sql = "UPDATE analyst_chunks SET start_char=1 WHERE chunk_id=?"
        target = first.chunk_id
    elif drift == "end":
        sql = "UPDATE analyst_chunks SET end_char=? WHERE chunk_id=?"
        _unsafe_update(db_path, sql, (len(text) + 1, second.chunk_id))
        sql = ""
        target = second.chunk_id
    else:
        sql = "UPDATE analyst_chunks SET start_char=start_char+1 WHERE chunk_id=?"
        target = second.chunk_id
    if sql:
        _unsafe_update(db_path, sql, (target,))
    monkeypatch.setattr(phase1_state, "run_immediate", _raw_immediate)

    with pytest.raises((CheckpointError, WorkerContractError, ValueError)):
        load_phase1_handoff(fence, path=db_path)


def test_fast_handoff_rejects_selection_without_durable_detector_hit(
    tmp_path: Path,
) -> None:
    db_path, _spec, _inventory, fence, _handoff = _prepare_handoff(
        tmp_path, mode="fast",
    )

    with pytest.raises(CheckpointError, match="fast model selection"):
        load_phase1_handoff(fence, path=db_path)


def test_deep_handoff_accepts_selection_without_detector_hit(tmp_path: Path) -> None:
    db_path, _spec, _inventory, fence, created = _prepare_handoff(
        tmp_path, mode="deep",
    )

    assert load_phase1_handoff(fence, path=db_path) == (created,)


def test_deep_handoff_rejects_detector_only_terminal(tmp_path: Path) -> None:
    text = "public text"
    db_path, _spec, _inventory, fence = _run(
        tmp_path, bodies=(text,), mode="deep",
    )
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        (),
        selected_for_model=False,
        path=db_path,
    )
    terminalize_file(
        fence,
        snapshot.file_id,
        FileTerminal.COMPLETE_DETECTOR_ONLY,
        path=db_path,
    )

    with pytest.raises(CheckpointError, match="deep run"):
        load_phase1_handoff(fence, path=db_path)


def _contact_count(db_path: Path) -> int:
    conn = open_connection(db_path, read_only=True)
    try:
        return int(conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts",
        ).fetchone()[0])
    finally:
        conn.close()


def _file_outcomes(db_path: Path) -> tuple[tuple[object, ...], ...]:
    conn = open_connection(db_path, read_only=True)
    try:
        return tuple(tuple(row) for row in conn.execute(
            "SELECT f.ordinal,f.stage,f.work_state,f.terminal_code,"
            "(SELECT count(*) FROM analyst_detector_hits h "
            "WHERE h.file_id=f.file_id) FROM analyst_files f ORDER BY f.ordinal",
        ).fetchall())
    finally:
        conn.close()


def test_phase1_engine_fast_happy_path_persists_exact_handoff_without_contacts(
    tmp_path: Path,
) -> None:
    text = "public@example.test"
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=(text,), mode="fast",
    )
    context = load_worker_run(spec.run_id, path=db_path)

    result = run_phase1(
        context,
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )

    assert result.file_count == 1
    assert result.chunk_count == 1
    assert result.fence.heartbeat_monotonic_ns > fence.heartbeat_monotonic_ns
    assert load_phase1_handoff(result.fence, path=db_path) == result.files
    assert _file_outcomes(db_path) == (
        (0, "selected_for_model", "pending", None, 1),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_fast_no_hit_terminalizes_detector_only(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=("public text",), mode="fast",
    )

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )

    assert result.files == ()
    assert _file_outcomes(db_path) == (
        (0, "detector_scanned", "terminal", "complete_detector_only", 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_deep_selects_nonempty_file_without_hit(tmp_path: Path) -> None:
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=("public text",), mode="deep",
    )

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )

    assert result.file_count == 1
    assert _file_outcomes(db_path) == (
        (0, "selected_for_model", "pending", None, 0),
    )
    assert load_phase1_handoff(result.fence, path=db_path) == result.files
    assert _contact_count(db_path) == 0


def test_phase1_engine_empty_success_and_detector_overflow_are_terminal(
    tmp_path: Path,
) -> None:
    from experimental.analyst.extract import ExtractionResult

    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=("empty", "overflow"), mode="fast",
    )

    def extract(source_fd: int, expected, cancel_check):
        body = os.pread(source_fd, expected.size, 0).decode("utf-8")
        return ExtractionResult(
            "success", "text", "utf-8", "" if body == "empty" else body,
        )

    def detect(_text: str, _limit: int, _cancel_check):
        return [], True

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract, detect=detect),
    )

    assert result.files == ()
    assert _file_outcomes(db_path) == (
        (0, "text_extracted", "terminal", "complete_no_supported_content", 0),
        (1, "text_extracted", "terminal", "detector_output_limit", 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_parser_failure_checkpoints_format_then_terminalizes(
    tmp_path: Path,
) -> None:
    from experimental.analyst.extract import ExtractionResult

    db_path, spec, _inventory, fence = _run(tmp_path)

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(
            extract=lambda _fd, _expected, _cancel: ExtractionResult(
                "parse_timeout", "text",
            ),
        ),
    )

    assert result.files == ()
    assert _file_outcomes(db_path) == (
        (0, "format_identified", "terminal", "parse_timeout", 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_source_drift_is_terminal_and_never_extracted(
    tmp_path: Path,
) -> None:
    db_path, spec, inventory, fence = _run(
        tmp_path, bodies=("public text",), mode="fast",
    )
    source_file = Path(spec.source_root) / inventory.files[0].relative_path
    source_file.write_text("changed text", encoding="utf-8")
    called = False

    def extract(_source_fd: int, _expected, _cancel_check):
        nonlocal called
        called = True
        raise AssertionError("changed source reached extraction")

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract),
    )

    assert result.files == ()
    assert called is False
    assert _file_outcomes(db_path) == (
        (0, "discovered", "terminal", "source_changed_since_inventory", 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_bounds_parallel_extraction_and_orders_handoff(
    tmp_path: Path,
) -> None:
    bodies = tuple(f"public body {index}" for index in range(5))
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=bodies, mode="deep",
    )
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def extract(source_fd: int, expected, cancel_check):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        if expected.relative_path != "public-4.txt":
            barrier.wait(timeout=3)
        if expected.relative_path == "public-0.txt":
            time.sleep(0.05)
        result = _success_extract(source_fd, expected, cancel_check)
        with lock:
            active -= 1
        return result

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract),
    )

    assert maximum == 4
    assert result.file_count == 5
    assert tuple(item.ordinal for item in result.files) == (0, 1, 2, 3, 4)
    assert _contact_count(db_path) == 0


def test_phase1_engine_local_stop_releases_without_claim_or_contact(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(tmp_path)
    stop = threading.Event()
    stop.set()

    with pytest.raises(Phase1Interrupted) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            fence,
            stop,
            path=db_path,
            dependencies=Phase1Dependencies(extract=_success_extract),
        )

    assert raised.value.code is Phase1Failure.STATE
    assert _file_outcomes(db_path) == (
        (0, "discovered", "pending", None, 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_durable_cancel_acknowledges_and_releases(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(tmp_path)
    started = threading.Event()
    outcome: list[BaseException] = []

    def extract(_source_fd: int, _expected, cancel_check):
        started.set()
        deadline = time.monotonic() + 5
        while not cancel_check() and time.monotonic() < deadline:
            time.sleep(0.01)
        from experimental.analyst.extract import ExtractionResult

        return ExtractionResult("cancelled")

    def target() -> None:
        try:
            run_phase1(
                load_worker_run(spec.run_id, path=db_path),
                fence,
                threading.Event(),
                path=db_path,
                dependencies=Phase1Dependencies(extract=extract),
            )
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    assert started.wait(3)
    assert request_cancel(spec.run_id, path=db_path) is not None
    worker.join(5)

    assert not worker.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], Phase1Cancelled)
    assert _contact_count(db_path) == 0
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (spec.run_id,),
        ).fetchone()[0] == "cancelled_pending_resume"
        assert conn.execute(
            "SELECT count(*) FROM analyst_gpu_lease WHERE run_id IS NOT NULL",
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_phase1_engine_durable_cancel_interrupts_cooperative_detector(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=("public text",), mode="deep",
    )
    started = threading.Event()
    outcome: list[BaseException] = []

    def detect(_text: str, _limit: int, cancel_check):
        started.set()
        deadline = time.monotonic() + 5
        while not cancel_check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return [], False

    def target() -> None:
        try:
            run_phase1(
                load_worker_run(spec.run_id, path=db_path),
                fence,
                threading.Event(),
                path=db_path,
                dependencies=Phase1Dependencies(
                    extract=_success_extract, detect=detect,
                ),
            )
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    assert started.wait(3)
    assert request_cancel(spec.run_id, path=db_path) is not None
    worker.join(5)

    assert not worker.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], Phase1Cancelled)
    assert _file_outcomes(db_path) == (
        (0, "discovered", "cancelled_pending_resume", None, 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_drops_completed_private_results_before_second_wave(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import phase1

    bodies = tuple(f"public wave {index}" for index in range(5))
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=bodies, mode="deep",
    )
    first_wave = threading.Barrier(4)
    completed_refs: list[weakref.ReferenceType] = []
    second_wave_observed = threading.Event()
    original_commit = phase1._commit_future

    def recording_commit(context, completed, owner):
        completed_refs.append(weakref.ref(completed))
        return original_commit(context, completed, owner)

    def extract(source_fd: int, expected, cancel_check):
        if expected.relative_path != "public-4.txt":
            first_wave.wait(timeout=3)
        else:
            gc.collect()
            assert completed_refs
            assert all(reference() is None for reference in completed_refs)
            second_wave_observed.set()
        return _success_extract(source_fd, expected, cancel_check)

    monkeypatch.setattr(phase1, "_commit_future", recording_commit)
    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract),
    )

    assert second_wave_observed.is_set()
    assert result.file_count == 5
    assert _contact_count(db_path) == 0


@pytest.mark.parametrize("invalid", [object(), None, "text"])
def test_phase1_engine_rejects_untyped_extractor_result_and_releases(
    tmp_path: Path, invalid: object,
) -> None:
    db_path, spec, _inventory, fence = _run(tmp_path)

    with pytest.raises(Phase1Error) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            fence,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(
                extract=lambda _fd, _expected, _cancel: invalid,  # type: ignore[arg-type]
            ),
        )

    assert raised.value.code is Phase1Failure.EXTRACTOR
    assert _contact_count(db_path) == 0


def test_phase1_engine_rejects_partial_detector_overflow_without_checkpoint(
    tmp_path: Path,
) -> None:
    text = "public@example.test"
    db_path, spec, _inventory, fence = _run(tmp_path, bodies=(text,))

    with pytest.raises(Phase1Error) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            fence,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(
                extract=_success_extract,
                detect=lambda body, _limit, _cancel: (scan(body), True),
            ),
        )

    assert raised.value.code is Phase1Failure.CONTRACT
    assert _file_outcomes(db_path) == (
        (0, "discovered", "pending", None, 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_rejects_untyped_chunk_before_durable_checkpoint(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=("public text",), mode="deep",
    )

    with pytest.raises(Phase1Error) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            fence,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(
                extract=_success_extract,
                chunk=lambda _text, _size, _overlap, _cancel: (object(),),
            ),
        )

    assert raised.value.code is Phase1Failure.CONTRACT
    assert _file_outcomes(db_path) == (
        (0, "discovered", "pending", None, 0),
    )
    assert _contact_count(db_path) == 0


def test_phase1_engine_resume_rejects_changed_extraction_identity(
    tmp_path: Path,
) -> None:
    old_text = "public text"
    db_path, spec, _inventory, fence = _run(tmp_path, bodies=(old_text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, old_text)
    assert release_worker(fence, path=db_path).value == "interrupted"
    successor = claim_worker(
        spec.run_id,
        ProcessIdentity(8112, 9223, "12345678-1234-5678-1234-567812345678"),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=20,
        path=db_path,
    )
    assert successor is not None

    def changed(_source_fd: int, _expected, _cancel_check):
        from experimental.analyst.extract import ExtractionResult

        return ExtractionResult("success", "text", "utf-8", "changed text")

    with pytest.raises(Phase1Error) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            successor,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(extract=changed),
        )

    assert raised.value.code is Phase1Failure.RESUME_MISMATCH
    assert _contact_count(db_path) == 0


def test_phase1_engine_resume_rejects_changed_detector_checkpoint(
    tmp_path: Path,
) -> None:
    text = "public@example.test"
    db_path, spec, _inventory, fence = _run(tmp_path, bodies=(text,))
    snapshot = claim_next_phase1_file(fence, path=db_path)
    assert snapshot is not None
    _to_text_extracted(db_path, fence, snapshot.file_id, text)
    checkpoint_detector(
        fence,
        snapshot.file_id,
        scan(text),
        selected_for_model=True,
        path=db_path,
    )
    assert release_worker(fence, path=db_path).value == "interrupted"
    successor = claim_worker(
        spec.run_id,
        ProcessIdentity(8112, 9223, "12345678-1234-5678-1234-567812345678"),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=20,
        path=db_path,
    )
    assert successor is not None

    with pytest.raises(Phase1Error) as raised:
        run_phase1(
            load_worker_run(spec.run_id, path=db_path),
            successor,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(
                extract=_success_extract,
                detect=lambda _text, _limit, _cancel: ([], False),
            ),
        )

    assert raised.value.code is Phase1Failure.RESUME_MISMATCH
    assert _contact_count(db_path) == 0


def test_phase1_engine_never_persists_extracted_source_text(tmp_path: Path) -> None:
    marker = "PUBLIC_C10B_EXTRACTED_TEXT_MARKER_93A7D2"
    db_path, spec, _inventory, fence = _run(
        tmp_path, bodies=(marker,), mode="deep",
    )

    result = run_phase1(
        load_worker_run(spec.run_id, path=db_path),
        fence,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )

    assert result.file_count == 1
    assert marker.encode("utf-8") not in db_path.read_bytes()
    assert _contact_count(db_path) == 0


def test_real_crash_inside_phase1_extractor_reconciles_to_discovered_resume(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory, fence = _run(tmp_path)
    process = multiprocessing.get_context("spawn").Process(
        target=_run_phase1_crash,
        args=(str(db_path), spec.run_id, fence),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 0
    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=20,
        now_utc="2026-08-16T15:00:02Z",
        identity_reader=lambda _pid: None,
    ).value == "cleared_interrupted"
    successor = claim_worker(
        spec.run_id,
        ProcessIdentity(8112, 9223, "12345678-1234-5678-1234-567812345678"),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=21,
        path=db_path,
    )
    assert successor is not None
    resumed = claim_next_phase1_file(successor, path=db_path)
    assert resumed is not None
    assert resumed.stage is FileStage.DISCOVERED
    assert _contact_count(db_path) == 0
