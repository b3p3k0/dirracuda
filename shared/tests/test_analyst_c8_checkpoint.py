"""Hostile C8 tests for fenced checkpoints and crash/resume boundaries."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import queue
import sqlite3

import pytest

from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    AnalystSchemaError,
)
from experimental.analyst.checkpoint import (
    CheckpointError,
    ChunkSpec,
    DetectorHitLimit,
    ExtractionCheckpointEvidence,
    MAX_DETECTOR_HITS,
    MAX_PROVENANCE_UNITS,
    ProvenanceUnit,
    advance_file_stage,
    begin_finalization,
    build_extraction_evidence,
    checkpoint_detector,
    claim_next_file,
    finish_attempt_failure,
    finish_finalization,
    finish_valid_attempt,
    precharge_attempt,
    store_chunks,
    terminalize_file,
)
from experimental.analyst.extract import ExtractionResult
from experimental.analyst.inventory import InventoryFile, InventoryResult
from experimental.analyst.lease import (
    LeaseFence,
    ReconcileResult,
    claim_worker,
    current_lease,
    heartbeat,
    reconcile_lease,
    release_worker,
)
from experimental.analyst.legacy_frame import LegacyUnit
from experimental.analyst.models import (
    Assessment,
    Category,
    DetectorHit,
    FileStage,
    FileTerminal,
    GroundedFinding,
    WorksheetResult,
)
from experimental.analyst.process_identity import (
    ProcessIdentity,
    current_process_identity,
)
from experimental.analyst.ooxml_frame import OoxmlUnit
from experimental.analyst.state import AttemptState, RunState
from experimental.analyst.store import (
    RunSpec,
    create_run,
    initialize_database,
    open_connection,
)
from experimental.analyst.xls_frame import XlsUnit


_NOW = "2026-08-16T12:00:00Z"
_BOOT_ID = "00000000-0000-0000-0000-000000000001"
_CRASH_EXIT_CODE = 73


def _crash_mid_transaction(db_path: Path) -> None:
    conn = open_connection(db_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE analyst_gpu_lease SET generation=99 WHERE slot=1")
    os._exit(_CRASH_EXIT_CODE)


def _crash_hot_analyst_journal(db_path: Path) -> None:
    conn = open_connection(db_path)
    conn.execute("PRAGMA cache_size=10")
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany(
        "INSERT INTO analyst_inventory_exclusions("
        "run_id,ordinal,relative_path,reason) VALUES('run',?,?, 'symlink')",
        ((index, f"generated/path-{index:05d}") for index in range(20_000)),
    )
    journal = Path(os.fspath(db_path) + "-journal")
    if not journal.exists() or journal.stat().st_size <= 512:
        os._exit(70)
    os._exit(_CRASH_EXIT_CODE)


def _crash_hot_untrusted_journal(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, autocommit=True)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA cache_size=10")
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany(
        "INSERT INTO unexpected(value) VALUES(?)",
        ((f"generated-{index:05d}",) for index in range(20_000)),
    )
    journal = Path(os.fspath(db_path) + "-journal")
    if not journal.exists() or journal.stat().st_size <= 512:
        os._exit(70)
    os._exit(_CRASH_EXIT_CODE)


def _crash_after_claim(db_path: Path) -> None:
    fence = claim_worker(
        "run", current_process_identity(), owner_token="a" * 64,
        heartbeat_monotonic_ns=100, now_utc="child-claim", path=db_path,
    )
    if fence is None:
        os._exit(72)
    os._exit(_CRASH_EXIT_CODE)


def _crash_after_precharge(db_path: Path) -> None:
    fence = claim_worker(
        "run", current_process_identity(), owner_token="a" * 64,
        heartbeat_monotonic_ns=100, now_utc="child-claim", path=db_path,
    )
    if fence is None:
        os._exit(72)
    claim = claim_next_file(fence, now_utc="child-file", path=db_path)
    if claim is None:
        os._exit(71)
    _advance_to_selected(db_path, fence, claim.file_id)
    store_chunks(
        fence, claim.file_id, (ChunkSpec(0, 0, 20, "4" * 64),),
        now_utc="child-chunks", path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        chunk_id = int(conn.execute(
            "SELECT chunk_id FROM analyst_chunks WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0])
    finally:
        conn.close()
    precharge_attempt(
        fence, chunk_id, "5" * 64, now_utc="child-precharge", path=db_path,
    )
    os._exit(_CRASH_EXIT_CODE)


def _crash_after_begin_finalization(db_path: Path) -> None:
    fence = claim_worker(
        "run", current_process_identity(), owner_token="a" * 64,
        heartbeat_monotonic_ns=100, now_utc="child-claim", path=db_path,
    )
    if fence is None:
        os._exit(72)
    claim = claim_next_file(fence, now_utc="child-file", path=db_path)
    if claim is None:
        os._exit(71)
    terminalize_file(
        fence, claim.file_id, FileTerminal.EMPTY,
        detail=None, now_utc="child-terminal", path=db_path,
    )
    begin_finalization(
        fence, "8" * 64, now_utc="child-finalizing", path=db_path,
    )
    os._exit(_CRASH_EXIT_CODE)


def _initialize_fresh_concurrently(db_path: Path, start, results) -> None:
    try:
        if not start.wait(10):
            results.put(("error", "start_timeout"))
            return
        initialized = initialize_database(db_path)
        conn = open_connection(initialized, read_only=True)
        try:
            identity = (
                conn.execute("PRAGMA application_id").fetchone()[0],
                conn.execute("PRAGMA user_version").fetchone()[0],
            )
        finally:
            conn.close()
        results.put(("ok", initialized.stat().st_ino, identity))
    except BaseException as exc:
        results.put(("error", type(exc).__name__))


def _spec(run_id: str) -> RunSpec:
    return RunSpec(
        run_id=run_id,
        mode="fast",
        source_mode="unknown",
        source_root="/public/source",
        output_root="/public/output",
        source_identity={"kind": "public-synthetic", "version": 1},
        report_label=f"Public run {run_id}",
        model_tag="qwen3.6:27b",
        model_digest="b" * 64,
        worksheet_version="v2",
        prompt_sha256="c" * 64,
        response_schema_sha256="d" * 64,
        detector_rules_version="rules-v1",
        detector_rules_sha256="e" * 64,
        parser_bundle={"bundle": "public-test"},
        chunk_chars=8000,
        overlap_chars=256,
        num_ctx=8192,
        num_predict=1024,
        isolation_mode="strict",
        reduced_isolation_ack=False,
    )


def _inventory(file_count: int) -> InventoryResult:
    return InventoryResult(
        root_device=1,
        root_inode=2,
        root_mount_id=3,
        files=tuple(
            InventoryFile(
                f"public-{index}.txt", 20, 10 + index, 20 + index, 1,
                100 + index, 0o100600, f"{index + 1:x}" * 64,
            )
            for index in range(file_count)
        ),
        exclusions=(),
    )


def _extraction_projection_cases():
    return (
        (
            ExtractionResult(
                "success", format_name="pdf", text="abcd\nxyz",
                page_char_counts=(4, 3), text_page_count=2,
                parser_version="1.28.0", embedded_version="1.29.0",
            ),
            (("page", "page-1", 0, 4), ("page", "page-2", 5, 8)),
            {"page_count": 2, "text_page_count": 2},
        ),
        (
            ExtractionResult(
                "success", format_name="docx", encoding="utf-8",
                text="abcd\nxyz", parser_version="0.7.1",
                ooxml_units=(
                    OoxmlUnit("paragraph", "main#p1", 4),
                    OoxmlUnit("paragraph", "main#p2", 3),
                ),
                logical_unit_count=2, primary_unit_count=2,
                member_count=3, expanded_bytes=100,
            ),
            (("paragraph", "main#p1", 0, 4),
             ("paragraph", "main#p2", 5, 8)),
            {"logical_unit_count": 2, "primary_unit_count": 2},
        ),
        (
            ExtractionResult(
                "success", format_name="xlsx", encoding="utf-8",
                text="abcd\nxyz", parser_version="0.7.1",
                ooxml_units=(
                    OoxmlUnit("cell", "sheet-1!A1", 4),
                    OoxmlUnit("cell", "sheet-1!A2", 3),
                ),
                logical_unit_count=2, primary_unit_count=1,
                member_count=3, expanded_bytes=100,
            ),
            (("cell", "sheet-1!A1", 0, 4),
             ("cell", "sheet-1!A2", 5, 8)),
            {"logical_unit_count": 2, "primary_unit_count": 1},
        ),
        (
            ExtractionResult(
                "success", format_name="pptx", encoding="utf-8",
                text="abcd\nxyz\nhi", parser_version="0.7.1",
                ooxml_units=(
                    OoxmlUnit("slide", "slide-1", 4),
                    OoxmlUnit("notes", "slide-1-notes", 3),
                    OoxmlUnit("comments", "slide-1-comments", 2),
                ),
                logical_unit_count=3, primary_unit_count=1,
                member_count=5, expanded_bytes=150,
            ),
            (("slide", "slide-1", 0, 4),
             ("notes", "slide-1-notes", 5, 8),
             ("comments", "slide-1-comments", 9, 11)),
            {"logical_unit_count": 3, "primary_unit_count": 1},
        ),
        (
            ExtractionResult(
                "success", format_name="doc", encoding="utf-8",
                text="abcd\nxyz", parser_version="0.37",
                package_revision="1:0.37-16",
                legacy_units=(
                    LegacyUnit("output_line", "output-line-1", 4),
                    LegacyUnit("output_line", "output-line-2", 3),
                ),
                logical_unit_count=2,
            ),
            (("output_line", "output-line-1", 0, 4),
             ("output_line", "output-line-2", 5, 8)),
            {"logical_unit_count": 2},
        ),
        (
            ExtractionResult(
                "success", format_name="xls", encoding="utf-8",
                text="abcd\nxyz", parser_version="0.8.2",
                embedded_version="0.36.0",
                xls_units=(
                    XlsUnit("cell", "sheet-1!A1", "string", 4),
                    XlsUnit("cell", "sheet-1!A2", "string", 3),
                ),
                logical_unit_count=2, primary_unit_count=1, worksheet_count=1,
                skipped_sheet_count=0, dense_cell_count=2,
            ),
            (("cell", "sheet-1!A1", 0, 4),
             ("cell", "sheet-1!A2", 5, 8)),
            {"logical_unit_count": 2, "worksheet_count": 1},
        ),
    )


def _setup(
    tmp_path: Path, *, file_count: int = 1, run_id: str = "run",
) -> tuple[Path, LeaseFence]:
    db_path = initialize_database(tmp_path / "state" / "analyst.db")
    create_run(_spec(run_id), _inventory(file_count), now_utc=_NOW, path=db_path)
    fence = claim_worker(
        run_id,
        ProcessIdentity(101, 202, _BOOT_ID),
        owner_token="a" * 64,
        heartbeat_monotonic_ns=100,
        now_utc=_NOW,
        path=db_path,
    )
    assert fence is not None
    return db_path, fence


def _run_crash(target, db_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=target, args=(db_path,))
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("crash-boundary child did not exit")
    assert process.exitcode == _CRASH_EXIT_CODE


def _run_row(db_path: Path) -> tuple[object, ...]:
    conn = open_connection(db_path, read_only=True)
    try:
        return tuple(conn.execute(
            "SELECT state,completion_code,finalization_token,"
            "report_manifest_sha256 FROM analyst_runs WHERE run_id='run'"
        ).fetchone())
    finally:
        conn.close()


def _advance_to_selected(
    db_path: Path,
    fence: LeaseFence,
    file_id: int,
    *,
    text_chars: int = 20,
) -> None:
    advance_file_stage(
        fence, file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", now_utc="format", path=db_path,
    )
    advance_file_stage(
        fence, file_id, FileStage.TEXT_EXTRACTED,
        encoding="utf-8", parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": text_chars, "text_chars": text_chars},
        now_utc="extract", path=db_path,
    )
    checkpoint_detector(
        fence,
        file_id,
        (DetectorHit("email", "a@b.c", 0, 5),),
        selected_for_model=True,
        now_utc="detector",
        path=db_path,
    )
    advance_file_stage(
        fence, file_id, FileStage.SELECTED_FOR_MODEL,
        now_utc="selected", path=db_path,
    )


def _prepare_text_stage(tmp_path: Path) -> tuple[Path, LeaseFence, int]:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 20, "text_chars": 20},
        path=db_path,
    )
    return db_path, fence, claim.file_id


def _prepare_model_attempt(
    tmp_path: Path,
) -> tuple[Path, LeaseFence, int, str]:
    db_path, fence, file_id = _prepare_text_stage(tmp_path)
    checkpoint_detector(
        fence, file_id, (), selected_for_model=True, path=db_path,
    )
    advance_file_stage(
        fence, file_id, FileStage.SELECTED_FOR_MODEL, path=db_path,
    )
    store_chunks(
        fence, file_id, (ChunkSpec(0, 0, 20, "4" * 64),), path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        chunk_id = int(conn.execute(
            "SELECT chunk_id FROM analyst_chunks WHERE file_id=?", (file_id,),
        ).fetchone()[0])
    finally:
        conn.close()
    attempt_id, attempt_no = precharge_attempt(
        fence, chunk_id, "5" * 64, path=db_path,
    )
    assert attempt_no == 1
    return db_path, fence, chunk_id, attempt_id


def test_process_exit_mid_immediate_transaction_rolls_back_cleanly(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "transaction-crash" / "analyst.db")

    _run_crash(_crash_mid_transaction, db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT generation FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_hot_delete_journal_spill_recovers_exact_analyst_v1_state(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "hot-v1" / "analyst.db")
    create_run(_spec("run"), _inventory(0), now_utc=_NOW, path=db_path)
    before_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()

    _run_crash(_crash_hot_analyst_journal, db_path)

    journal = Path(os.fspath(db_path) + "-journal")
    assert journal.is_file()
    assert journal.stat().st_size > 512
    assert initialize_database(db_path) == db_path
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_sha
    assert not journal.exists()
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_inventory_exclusions"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


@pytest.mark.parametrize("application_id", [0, 12345], ids=("partial", "foreign"))
def test_untrusted_hot_journal_is_rejected_without_mutating_either_file(
    tmp_path: Path, application_id: int,
) -> None:
    parent = tmp_path / f"untrusted-{application_id}"
    parent.mkdir(mode=0o700)
    db_path = parent / "analyst.db"
    conn = sqlite3.connect(db_path, autocommit=True)
    try:
        conn.execute("CREATE TABLE unexpected(value TEXT)")
        conn.execute("INSERT INTO unexpected(value) VALUES('sentinel')")
        if application_id:
            conn.execute(f"PRAGMA application_id={application_id}")
    finally:
        conn.close()
    db_path.chmod(0o600)

    _run_crash(_crash_hot_untrusted_journal, db_path)

    journal = Path(os.fspath(db_path) + "-journal")
    assert journal.is_file()
    before = (
        hashlib.sha256(db_path.read_bytes()).hexdigest(),
        hashlib.sha256(journal.read_bytes()).hexdigest(),
    )
    with pytest.raises(AnalystSchemaError, match="untrusted database identity"):
        initialize_database(db_path)
    after = (
        hashlib.sha256(db_path.read_bytes()).hexdigest(),
        hashlib.sha256(journal.read_bytes()).hexdigest(),
    )
    assert after == before


def test_four_processes_can_initialize_one_fresh_database(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh-race" / "analyst.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_initialize_fresh_concurrently,
            args=(db_path, start, results),
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    try:
        observed = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
    except (AssertionError, queue.Empty):
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        raise
    finally:
        results.close()
        results.join_thread()

    assert all(item[0] == "ok" for item in observed), observed
    assert len({item[1] for item in observed}) == 1
    assert {item[2] for item in observed} == {(APPLICATION_ID, SCHEMA_VERSION)}
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT count(*) FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_process_exit_after_claim_reconciles_to_interrupted(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "claim-crash" / "analyst.db")
    create_run(_spec("run"), _inventory(1), now_utc=_NOW, path=db_path)

    _run_crash(_crash_after_claim, db_path)

    crashed = current_lease(path=db_path)
    assert crashed is not None
    assert crashed.process.pid > 0
    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=100,
        now_utc="reconcile",
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    assert current_lease(path=db_path) is None
    assert _run_row(db_path)[0] == RunState.INTERRUPTED.value


def test_process_exit_after_precharge_orphans_attempt_and_allows_only_attempt_two(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "precharge-crash" / "analyst.db")
    create_run(_spec("run"), _inventory(1), now_utc=_NOW, path=db_path)

    _run_crash(_crash_after_precharge, db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        chunk_id = int(conn.execute("SELECT chunk_id FROM analyst_chunks").fetchone()[0])
        assert tuple(conn.execute(
            "SELECT attempt_no,state,finished_at_utc "
            "FROM analyst_model_attempts"
        ).fetchone()) == (1, AttemptState.DISPATCHING.value, None)
    finally:
        conn.close()
    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=100,
        now_utc="reconcile",
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED

    resumed = claim_worker(
        "run", current_process_identity(), owner_token="b" * 64,
        heartbeat_monotonic_ns=200, now_utc="resume", path=db_path,
    )
    assert resumed is not None
    claim = claim_next_file(resumed, now_utc="reclaim", path=db_path)
    assert claim is not None
    second_id, second_no = precharge_attempt(
        resumed, chunk_id, "6" * 64, now_utc="attempt-2", path=db_path,
    )
    assert second_no == 2
    with pytest.raises(CheckpointError, match="charged live attempt"):
        precharge_attempt(resumed, chunk_id, "7" * 64, path=db_path)
    finish_attempt_failure(
        resumed,
        second_id,
        AttemptState.MODEL_TIMEOUT,
        now_utc="attempt-2-failed",
        path=db_path,
    )
    with pytest.raises(CheckpointError, match="not dispatchable"):
        precharge_attempt(resumed, chunk_id, "7" * 64, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts ORDER BY attempt_no"
        )] == [
            (1, AttemptState.ORPHANED_UNKNOWN.value),
            (2, AttemptState.MODEL_TIMEOUT.value),
        ]
    finally:
        conn.close()


def test_process_exit_after_begin_finalization_preserves_terminals_and_clears_token(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "finalization-crash" / "analyst.db")
    create_run(_spec("run"), _inventory(1), now_utc=_NOW, path=db_path)

    _run_crash(_crash_after_begin_finalization, db_path)

    assert _run_row(db_path) == (
        RunState.FINALIZING.value,
        None,
        "8" * 64,
        None,
    )
    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=100,
        now_utc="reconcile",
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    assert _run_row(db_path) == (RunState.INTERRUPTED.value, None, None, None)
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code,terminal_detail FROM analyst_files"
        ).fetchone()) == (
            "terminal",
            FileTerminal.EMPTY.value,
            None,
        )
    finally:
        conn.close()


def test_stale_fence_cannot_claim_or_checkpoint_file(tmp_path: Path) -> None:
    db_path, old = _setup(tmp_path)
    current = heartbeat(old, heartbeat_monotonic_ns=101, path=db_path)

    with pytest.raises(CheckpointError, match="no longer authorizes"):
        claim_next_file(old, now_utc="stale", path=db_path)
    claim = claim_next_file(current, now_utc="current", path=db_path)
    assert claim is not None
    assert claim.stage is FileStage.DISCOVERED

    with pytest.raises(CheckpointError, match="no longer authorizes"):
        advance_file_stage(
            old, claim.file_id, FileStage.FORMAT_IDENTIFIED,
            format_name="text", path=db_path,
        )


def test_stage_evidence_cannot_be_injected_early_or_overwritten(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    with pytest.raises(ValueError, match="later-stage evidence"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.FORMAT_IDENTIFIED,
            format_name="text",
            parser_identity={"parser": "public-test"},
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT stage,format_name,parser_identity_json,extraction_meta_json "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()) == (FileStage.DISCOVERED.value, None, None, None)
    finally:
        conn.close()

    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    with pytest.raises(ValueError, match="another stage's evidence"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            format_name="text",
            parser_identity={"parser": "public-test"},
            extraction_meta={"text_bytes": 20, "text_chars": 20},
            path=db_path,
        )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        encoding="utf-8",
        parser_identity={"parser": "public-test", "parser_version": "1.0"},
        extraction_meta={"text_bytes": 20, "text_chars": 20},
        path=db_path,
    )
    with pytest.raises(ValueError, match="invalid Analyst stage transition"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            encoding="windows-1252",
            parser_identity={"parser": "replacement"},
            extraction_meta={"text_bytes": 999, "text_chars": 999},
            path=db_path,
        )
    with pytest.raises(ValueError, match="invalid Analyst stage transition"):
        advance_file_stage(
            fence, claim.file_id, FileStage.SELECTED_FOR_MODEL, path=db_path,
        )

    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT stage,encoding,parser_identity_json,extraction_meta_json "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()) == (
            FileStage.TEXT_EXTRACTED.value,
            "utf-8",
            '{"parser":"public-test","parser_version":"1.0"}',
            '{"text_bytes":20,"text_chars":20}',
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "parser_identity,extraction_meta",
    [
        ({"parser": {"secret": "raw"}}, {"text_bytes": 20, "text_chars": 20}),
        (
            {"parser": "public-test", "token": "secret"},
            {"text_bytes": 20, "text_chars": 20},
        ),
        (
            {"parser": "public-test"},
            {"text_bytes": 20, "text_chars": {"secret": "raw"}},
        ),
        (
            {"parser": "public-test"},
            {"text_bytes": 20, "text_chars": 20, "raw_text": "secret"},
        ),
    ],
)
def test_raw_or_nested_secret_metadata_is_rejected_without_persistence(
    tmp_path: Path, parser_identity, extraction_meta,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )

    with pytest.raises(ValueError):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            parser_identity=parser_identity,
            extraction_meta=extraction_meta,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT stage,parser_identity_json,extraction_meta_json "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()) == (FileStage.FORMAT_IDENTIFIED.value, None, None)
        assert "secret" not in "\n".join(conn.iterdump())
    finally:
        conn.close()


def test_terminal_detail_rejects_raw_secret_shaped_text(tmp_path: Path) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    for detail in (
        "password=secret",
        "raw secret text",
        "SecretValue",
        {"secret": "raw"},
    ):
        with pytest.raises((TypeError, ValueError)):
            terminalize_file(
                fence, claim.file_id, FileTerminal.EMPTY,
                detail=detail, path=db_path,
            )
    with pytest.raises(ValueError, match="not allowed for this terminal"):
        terminalize_file(
            fence,
            claim.file_id,
            FileTerminal.EMPTY,
            detail="secret_account_123",
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code,terminal_detail "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()) == ("active", None, None)
        assert "secret" not in "\n".join(conn.iterdump()).lower()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "format_name,kind,labels,counts",
    [
        ("pdf", "page", ("page-1", "page-2"), {"page_count": 2}),
        (
            "docx", "paragraph", ("main#p1", "main#p2"),
            {"logical_unit_count": 2},
        ),
        (
            "xlsx", "cell", ("sheet-1!A1", "sheet-1!A2"),
            {"logical_unit_count": 2, "primary_unit_count": 1},
        ),
        (
            "text", "output_line", ("output-line-1", "output-line-2"),
            {"logical_unit_count": 2},
        ),
    ],
)
def test_provenance_round_trips_across_restart_without_source_text(
    tmp_path: Path,
    format_name: str,
    kind: str,
    labels: tuple[str, str],
    counts: dict[str, int],
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name=format_name,
        path=db_path,
    )
    units = (
        ProvenanceUnit(kind, labels[0], 0, 4),
        ProvenanceUnit(kind, labels[1], 5, 10),
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 10, "text_chars": 10, **counts},
        provenance=units,
        path=db_path,
    )
    assert release_worker(fence, now_utc="restart", path=db_path) is RunState.INTERRUPTED

    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT ordinal,kind,label,start_char,end_char "
            "FROM analyst_provenance_units ORDER BY ordinal"
        )] == [
            (0, kind, labels[0], 0, 4),
            (1, kind, labels[1], 5, 10),
        ]
        assert [str(row[1]) for row in conn.execute(
            "PRAGMA table_xinfo('analyst_provenance_units')"
        )] == [
            "provenance_id", "file_id", "ordinal", "kind", "label",
            "start_char", "end_char",
        ]
    finally:
        conn.close()

    resumed = claim_worker(
        "run", current_process_identity(), owner_token="b" * 64,
        heartbeat_monotonic_ns=200, now_utc="resume", path=db_path,
    )
    assert resumed is not None
    resumed_claim = claim_next_file(resumed, path=db_path)
    assert resumed_claim is not None
    assert resumed_claim.stage is FileStage.TEXT_EXTRACTED


@pytest.mark.parametrize(
    "result,expected_units,expected_counts",
    _extraction_projection_cases(),
    ids=("pdf", "docx", "xlsx", "pptx", "doc", "xls"),
)
def test_real_extraction_contracts_project_and_persist_typed_evidence(
    tmp_path: Path,
    result: ExtractionResult,
    expected_units: tuple[tuple[object, ...], ...],
    expected_counts: dict[str, int],
) -> None:
    evidence = build_extraction_evidence(result)
    assert isinstance(evidence, ExtractionCheckpointEvidence)
    assert set(type(evidence).__dataclass_fields__) == {
        "encoding", "parser_identity", "extraction_counts", "provenance",
    }
    assert tuple(
        (unit.kind, unit.label, unit.start, unit.end)
        for unit in evidence.provenance
    ) == expected_units
    assert evidence.extraction_counts["text_chars"] == len(result.text)
    assert evidence.extraction_counts["text_bytes"] == len(result.text.encode("utf-8"))
    for key, value in expected_counts.items():
        assert evidence.extraction_counts[key] == value

    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name=result.format_name,
        path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        encoding=evidence.encoding,
        parser_identity=evidence.parser_identity,
        extraction_meta=evidence.extraction_counts,
        provenance=evidence.provenance,
        path=db_path,
    )
    assert release_worker(fence, now_utc="restart", path=db_path) is RunState.INTERRUPTED

    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT kind,label,start_char,end_char "
            "FROM analyst_provenance_units ORDER BY ordinal"
        )] == list(expected_units)
        file_row = conn.execute(
            "SELECT stage,parser_identity_json,extraction_meta_json "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()
        assert file_row[0] == FileStage.TEXT_EXTRACTED.value
        assert result.text not in str(file_row[1])
        assert result.text not in str(file_row[2])
    finally:
        conn.close()


def test_xls_projection_preserves_all_sheet_positions_when_leading_sheet_is_skipped(
    tmp_path: Path,
) -> None:
    result = ExtractionResult(
        "success",
        format_name="xls",
        encoding="utf-8",
        text="abcd\nxyz",
        parser_version="0.8.2",
        embedded_version="0.36.0",
        xls_units=(
            XlsUnit("cell", "sheet-2!A1", "string", 4),
            XlsUnit("cell", "sheet-3!A1", "string", 3),
        ),
        logical_unit_count=2,
        primary_unit_count=3,
        worksheet_count=2,
        skipped_sheet_count=1,
        dense_cell_count=2,
    )
    evidence = build_extraction_evidence(result)
    assert evidence.extraction_counts == {
        "text_bytes": 8,
        "text_chars": 8,
        "logical_unit_count": 2,
        "primary_unit_count": 3,
        "worksheet_count": 2,
        "skipped_sheet_count": 1,
        "dense_cell_count": 2,
    }
    assert tuple(
        (unit.kind, unit.label, unit.start, unit.end)
        for unit in evidence.provenance
    ) == (
        ("cell", "sheet-2!A1", 0, 4),
        ("cell", "sheet-3!A1", 5, 8),
    )

    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="xls", path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        encoding=evidence.encoding,
        parser_identity=evidence.parser_identity,
        extraction_meta=evidence.extraction_counts,
        provenance=evidence.provenance,
        path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT label,start_char,end_char FROM analyst_provenance_units "
            "ORDER BY ordinal"
        )] == [
            ("sheet-2!A1", 0, 4),
            ("sheet-3!A1", 5, 8),
        ]
        assert '"primary_unit_count":3' in conn.execute(
            "SELECT extraction_meta_json FROM analyst_files WHERE file_id=?",
            (claim.file_id,),
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "label",
    ["raw secret label", "alice@example.test", "line[private]", "line-1\nsecret"],
)
def test_provenance_label_rejects_unsafe_or_raw_shaped_text(label: str) -> None:
    with pytest.raises(ValueError, match="content-free"):
        ProvenanceUnit("output_line", label, 0, 1)


def test_provenance_format_mismatch_and_plus_one_cap_leave_no_rows(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path / "format-mismatch")
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="pdf", path=db_path,
    )
    with pytest.raises(ValueError, match="does not match authenticated format"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            parser_identity={"parser": "public-test"},
            extraction_meta={"text_bytes": 1, "text_chars": 1},
            provenance=(ProvenanceUnit("cell", "sheet-1!A1", 0, 1),),
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_provenance_units"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT stage FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0] == FileStage.FORMAT_IDENTIFIED.value
    finally:
        conn.close()

    db_path, fence = _setup(tmp_path / "over-cap")
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    units = (
        ProvenanceUnit("output_line", f"line-{index}", index, index)
        for index in range(MAX_PROVENANCE_UNITS + 1)
    )
    with pytest.raises(ValueError, match="durable cap"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            parser_identity={"parser": "public-test"},
            extraction_meta={
                "text_bytes": MAX_PROVENANCE_UNITS + 1,
                "text_chars": MAX_PROVENANCE_UNITS + 1,
            },
            provenance=units,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_provenance_units"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT stage FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0] == FileStage.FORMAT_IDENTIFIED.value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "units,error",
    [
        (
            (ProvenanceUnit("output_line", "line-1", 1, 5),
             ProvenanceUnit("output_line", "line-2", 6, 10)),
            "source order",
        ),
        (
            (ProvenanceUnit("output_line", "line-1", 0, 4),
             ProvenanceUnit("output_line", "line-2", 5, 9)),
            "exact extracted text span",
        ),
        (
            (ProvenanceUnit("output_line", "line-1", 0, 6),
             ProvenanceUnit("output_line", "line-2", 5, 10)),
            "source order",
        ),
    ],
)
def test_provenance_requires_exact_ordered_nonoverlapping_span(
    tmp_path: Path, units: tuple[ProvenanceUnit, ...], error: str,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    with pytest.raises(ValueError, match=error):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            parser_identity={"parser": "public-test"},
            extraction_meta={"text_bytes": 10, "text_chars": 10},
            provenance=units,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_provenance_units"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_parse_crash_resumes_from_last_complete_stage_without_partial_text(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, now_utc="claim", path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", now_utc="format", path=db_path,
    )

    assert release_worker(fence, now_utc="crash", path=db_path) is RunState.INTERRUPTED
    resumed = claim_worker(
        "run", ProcessIdentity(102, 203, _BOOT_ID),
        owner_token="b" * 64, heartbeat_monotonic_ns=200,
        now_utc="resume", path=db_path,
    )
    assert resumed is not None
    resumed_claim = claim_next_file(resumed, now_utc="reclaim", path=db_path)
    assert resumed_claim is not None
    assert resumed_claim.file_id == claim.file_id
    assert resumed_claim.stage is FileStage.FORMAT_IDENTIFIED

    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT parser_identity_json,extraction_meta_json,encoding "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()
        assert tuple(row) == (None, None, None)
    finally:
        conn.close()


def test_detector_checkpoint_is_atomic_and_immutable(tmp_path: Path) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    advance_file_stage(
        fence, claim.file_id, FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 20, "text_chars": 20}, path=db_path,
    )
    bad_hits = (
        DetectorHit("email", "a@b.c", 0, 5),
        DetectorHit("not-frozen", "x", 6, 7),
    )

    with pytest.raises((ValueError, CheckpointError)):
        checkpoint_detector(
            fence, claim.file_id, bad_hits,
            selected_for_model=True, path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_detector_hits"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT stage FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0] == FileStage.TEXT_EXTRACTED.value
    finally:
        conn.close()

    checkpoint_detector(
        fence, claim.file_id, bad_hits[:1],
        selected_for_model=True, path=db_path,
    )
    with pytest.raises(CheckpointError, match="requires extracted-text stage"):
        checkpoint_detector(
            fence, claim.file_id, (), selected_for_model=False, path=db_path,
        )


def test_detector_hit_cap_accepts_exact_maximum_and_rejects_one_more(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path / "over-limit")
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={
            "text_bytes": MAX_DETECTOR_HITS + 1,
            "text_chars": MAX_DETECTOR_HITS + 1,
        },
        path=db_path,
    )

    def hits(count: int):
        return (
            DetectorHit("email", "x", index, index + 1)
            for index in range(count)
        )

    with pytest.raises(DetectorHitLimit, match="durable cap"):
        checkpoint_detector(
            fence,
            claim.file_id,
            hits(MAX_DETECTOR_HITS + 1),
            selected_for_model=False,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_detector_hits"
        ).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code,terminal_detail "
            "FROM analyst_files WHERE file_id=?", (claim.file_id,),
        ).fetchone()) == (
            "terminal",
            FileTerminal.DETECTOR_OUTPUT_LIMIT.value,
            "detector_hit_limit",
        )
    finally:
        conn.close()

    db_path, fence = _setup(tmp_path / "exact-limit")
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={
            "text_bytes": MAX_DETECTOR_HITS,
            "text_chars": MAX_DETECTOR_HITS,
        },
        path=db_path,
    )
    checkpoint_detector(
        fence,
        claim.file_id,
        hits(MAX_DETECTOR_HITS),
        selected_for_model=False,
        path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_detector_hits"
        ).fetchone()[0] == MAX_DETECTOR_HITS
    finally:
        conn.close()


def test_detector_span_accepts_exact_extracted_text_boundary_n(
    tmp_path: Path,
) -> None:
    db_path, fence, file_id = _prepare_text_stage(tmp_path)

    checkpoint_detector(
        fence,
        file_id,
        (DetectorHit("email", "x", 19, 20),),
        selected_for_model=False,
        path=db_path,
    )

    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT kind,value,start_char,end_char FROM analyst_detector_hits"
        ).fetchone()) == ("email", "x", 19, 20)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "hit",
    [
        DetectorHit("email", "xx", 19, 21),
        DetectorHit("email", "x", -1, 0),
        DetectorHit("email", "", 0, 0),
        DetectorHit("not_frozen", "x", 0, 1),
        DetectorHit("email", "x", 0, 2),
        DetectorHit("email", "\n", 0, 1),
    ],
    ids=(
        "n-plus-one", "negative-start", "empty-value", "unknown-kind",
        "value-span-mismatch", "control-value",
    ),
)
def test_detector_rejects_forged_kind_value_or_span_atomically(
    tmp_path: Path, hit: DetectorHit,
) -> None:
    db_path, fence, file_id = _prepare_text_stage(tmp_path)

    with pytest.raises((ValueError, CheckpointError), match="detector|span|value|kind"):
        checkpoint_detector(
            fence, file_id, (hit,), selected_for_model=False, path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_detector_hits WHERE file_id=?", (file_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT stage FROM analyst_files WHERE file_id=?", (file_id,),
        ).fetchone()[0] == FileStage.TEXT_EXTRACTED.value
    finally:
        conn.close()


def test_no_raw_text_prompt_response_or_reasoning_columns_exist(tmp_path: Path) -> None:
    db_path, _fence = _setup(tmp_path)
    conn = open_connection(db_path, read_only=True)
    try:
        columns = {
            str(row[1])
            for table in (
                "analyst_files", "analyst_provenance_units", "analyst_chunks",
                "analyst_model_attempts", "analyst_model_findings",
            )
            for row in conn.execute(f"PRAGMA table_xinfo('{table}')")
        }
        assert columns.isdisjoint(
            {"text", "raw_text", "prompt", "raw_response", "response", "reasoning"}
        )
    finally:
        conn.close()


def test_selected_8000_chunks_accept_exact_256_character_overlap(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    _advance_to_selected(db_path, fence, claim.file_id, text_chars=16000)
    chunks = (
        ChunkSpec(0, 0, 8000, "4" * 64),
        ChunkSpec(1, 7744, 15744, "5" * 64),
        ChunkSpec(2, 15488, 16000, "6" * 64),
    )

    store_chunks(fence, claim.file_id, chunks, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT chunk_index,start_char,end_char FROM analyst_chunks "
            "ORDER BY chunk_index"
        )] == [(0, 0, 8000), (1, 7744, 15744), (2, 15488, 16000)]
    finally:
        conn.close()


def test_chunks_must_cover_exact_extracted_text_length_n(tmp_path: Path) -> None:
    db_path, fence, file_id = _prepare_text_stage(tmp_path)
    checkpoint_detector(
        fence, file_id, (), selected_for_model=True, path=db_path,
    )
    advance_file_stage(
        fence, file_id, FileStage.SELECTED_FOR_MODEL, path=db_path,
    )

    store_chunks(
        fence, file_id, (ChunkSpec(0, 0, 20, "4" * 64),), path=db_path,
    )

    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT start_char,end_char FROM analyst_chunks WHERE file_id=?",
            (file_id,),
        ).fetchone()) == (0, 20)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "chunks",
    [
        (ChunkSpec(0, 0, 21, "4" * 64),),
        (ChunkSpec(0, 0, 19, "4" * 64),),
        (),
    ],
    ids=("n-plus-one", "n-minus-one", "empty"),
)
def test_chunks_reject_coverage_other_than_exact_text_length(
    tmp_path: Path, chunks: tuple[ChunkSpec, ...],
) -> None:
    db_path, fence, file_id = _prepare_text_stage(tmp_path)
    checkpoint_detector(
        fence, file_id, (), selected_for_model=True, path=db_path,
    )
    advance_file_stage(
        fence, file_id, FileStage.SELECTED_FOR_MODEL, path=db_path,
    )

    with pytest.raises((ValueError, CheckpointError), match="text|span|cover"):
        store_chunks(fence, file_id, chunks, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_chunks WHERE file_id=?", (file_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("second_start", [7743, 7745])
def test_selected_chunks_reject_wrong_overlap_stride(
    tmp_path: Path, second_start: int,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    _advance_to_selected(db_path, fence, claim.file_id)

    with pytest.raises(ValueError, match="overlap|stride|canonical|boundaries"):
        store_chunks(
            fence,
            claim.file_id,
            (
                ChunkSpec(0, 0, 8000, "4" * 64),
                ChunkSpec(1, second_start, 15000, "5" * 64),
            ),
            path=db_path,
        )


def test_inference_crash_charges_attempt_and_only_one_retry_remains(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    _advance_to_selected(db_path, fence, claim.file_id)
    store_chunks(
        fence, claim.file_id, (ChunkSpec(0, 0, 20, "4" * 64),), path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        chunk_id = int(conn.execute(
            "SELECT chunk_id FROM analyst_chunks WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0])
    finally:
        conn.close()
    first_id, first_no = precharge_attempt(
        fence, chunk_id, "5" * 64, now_utc="charge-1", path=db_path,
    )
    assert first_no == 1

    assert release_worker(fence, now_utc="crash", path=db_path) is RunState.INTERRUPTED
    resumed = claim_worker(
        "run", ProcessIdentity(102, 203, _BOOT_ID),
        owner_token="b" * 64, heartbeat_monotonic_ns=200, path=db_path,
    )
    assert resumed is not None
    assert claim_next_file(resumed, path=db_path).file_id == claim.file_id
    second_id, second_no = precharge_attempt(
        resumed, chunk_id, "6" * 64, now_utc="charge-2", path=db_path,
    )
    assert second_no == 2
    finish_attempt_failure(
        resumed, second_id, AttemptState.MODEL_TIMEOUT,
        now_utc="finish-2", path=db_path,
    )
    with pytest.raises(CheckpointError, match="not dispatchable"):
        precharge_attempt(resumed, chunk_id, "7" * 64, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT attempt_id,attempt_no,state,failure_code "
            "FROM analyst_model_attempts ORDER BY attempt_no"
        )] == [
            (first_id, 1, "orphaned_unknown", "orphaned_unknown"),
            (second_id, 2, "model_timeout", "model_timeout"),
        ]
        assert conn.execute(
            "SELECT state FROM analyst_chunks WHERE chunk_id=?", (chunk_id,),
        ).fetchone()[0] == "model_timeout"
    finally:
        conn.close()


def test_valid_result_checkpoint_rolls_back_partial_invalid_findings(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    _advance_to_selected(db_path, fence, claim.file_id)
    store_chunks(
        fence, claim.file_id, (ChunkSpec(0, 0, 20, "4" * 64),), path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        chunk_id = int(conn.execute("SELECT chunk_id FROM analyst_chunks").fetchone()[0])
    finally:
        conn.close()
    attempt_id, _ = precharge_attempt(fence, chunk_id, "5" * 64, path=db_path)
    long_quote = "x" * 241
    invalid = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=Assessment.FINDINGS_PRESENT,
        findings=(GroundedFinding(
            Category.PII, long_quote, 0, 0, len(long_quote), 1, True,
        ),),
        raw_finding_count=1,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )
    with pytest.raises((ValueError, CheckpointError)):
        finish_valid_attempt(fence, attempt_id, invalid, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_findings"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == "dispatching"
    finally:
        conn.close()

    quote = "alice@example.test"
    valid = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=Assessment.FINDINGS_PRESENT,
        findings=(GroundedFinding(
            Category.CONTACT, quote, 0, 0, len(quote), 1, True,
        ),),
        raw_finding_count=1,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )
    finish_valid_attempt(
        fence, attempt_id, valid, now_utc="valid", path=db_path,
    )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,accepted_attempt_id,assessment FROM analyst_chunks"
        ).fetchone()) == (
            "model_response_valid", attempt_id, Assessment.FINDINGS_PRESENT.value,
        )
        assert tuple(conn.execute(
            "SELECT category,quote,canonical_end FROM analyst_model_findings"
        ).fetchone()) == (Category.CONTACT.value, quote, len(quote))
    finally:
        conn.close()


def test_finding_span_accepts_exact_chunk_boundary_n(tmp_path: Path) -> None:
    db_path, fence, _chunk_id, attempt_id = _prepare_model_attempt(tmp_path)
    finding = GroundedFinding(
        Category.CONTACT, "x", 19, 19, 20, 1, True,
    )
    result = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=Assessment.FINDINGS_PRESENT,
        findings=(finding,),
        raw_finding_count=1,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )

    finish_valid_attempt(fence, attempt_id, result, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT canonical_offset,canonical_end,quote "
            "FROM analyst_model_findings"
        ).fetchone()) == (19, 20, "x")
    finally:
        conn.close()


def test_finding_span_rejects_chunk_boundary_n_plus_one_atomically(
    tmp_path: Path,
) -> None:
    db_path, fence, _chunk_id, attempt_id = _prepare_model_attempt(tmp_path)
    result = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=Assessment.FINDINGS_PRESENT,
        findings=(GroundedFinding(
            Category.CONTACT, "xx", 19, 19, 21, 1, True,
        ),),
        raw_finding_count=1,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )

    with pytest.raises((ValueError, CheckpointError), match="finding|span|chunk"):
        finish_valid_attempt(fence, attempt_id, result, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_findings"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == AttemptState.DISPATCHING.value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "raw,removed,dropped",
    [
        (2, 0, 0),
        (1, 1, 0),
        (1, 0, 1),
        (True, 0, 0),
    ],
    ids=("retained-too-few", "retained-after-duplicate", "retained-after-drop", "bool"),
)
def test_forged_worksheet_counters_must_equal_retained_findings(
    tmp_path: Path, raw, removed: int, dropped: int,
) -> None:
    db_path, fence, _chunk_id, attempt_id = _prepare_model_attempt(tmp_path)
    result = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=Assessment.FINDINGS_PRESENT,
        findings=(GroundedFinding(
            Category.CONTACT, "x", 0, 0, 1, 1, True,
        ),),
        raw_finding_count=raw,
        removed_duplicate_count=removed,
        dropped_ungrounded_count=dropped,
    )

    with pytest.raises((ValueError, CheckpointError), match="count|retained|finding"):
        finish_valid_attempt(fence, attempt_id, result, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == AttemptState.DISPATCHING.value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "assessment,findings",
    [
        (Assessment.FINDINGS_PRESENT, ()),
        (
            Assessment.NO_FINDINGS,
            (GroundedFinding(Category.CONTACT, "x", 0, 0, 1, 1, True),),
        ),
        (
            Assessment.INSUFFICIENT_EVIDENCE,
            (GroundedFinding(Category.CONTACT, "x", 0, 0, 1, 1, True),),
        ),
    ],
    ids=("present-empty", "none-nonempty", "insufficient-nonempty"),
)
def test_forged_assessment_must_match_finding_presence(
    tmp_path: Path,
    assessment: Assessment,
    findings: tuple[GroundedFinding, ...],
) -> None:
    db_path, fence, _chunk_id, attempt_id = _prepare_model_attempt(tmp_path)
    result = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=assessment,
        findings=findings,
        raw_finding_count=len(findings),
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )

    with pytest.raises((ValueError, CheckpointError), match="assessment|finding"):
        finish_valid_attempt(fence, attempt_id, result, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == AttemptState.DISPATCHING.value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "assessment",
    [Assessment.NO_FINDINGS, Assessment.INSUFFICIENT_EVIDENCE],
)
def test_empty_finding_assessments_are_accepted(
    tmp_path: Path, assessment: Assessment,
) -> None:
    db_path, fence, _chunk_id, attempt_id = _prepare_model_attempt(tmp_path)
    result = WorksheetResult(
        document_type="Public note",
        subject="Synthetic",
        model_assessment=assessment,
        findings=(),
        raw_finding_count=0,
        removed_duplicate_count=0,
        dropped_ungrounded_count=0,
    )

    finish_valid_attempt(fence, attempt_id, result, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT assessment FROM analyst_chunks"
        ).fetchone()[0] == assessment.value
    finally:
        conn.close()


def test_discovered_file_cannot_claim_model_review_success(tmp_path: Path) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None

    with pytest.raises(CheckpointError, match="model-reviewed terminal contradicts"):
        terminalize_file(
            fence,
            claim.file_id,
            FileTerminal.COMPLETE_MODEL_REVIEWED,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT stage,work_state,terminal_code FROM analyst_files"
        ).fetchone()) == (FileStage.DISCOVERED.value, "active", None)
    finally:
        conn.close()


def test_success_terminals_require_matching_stage_selection_and_chunks(
    tmp_path: Path,
) -> None:
    detector_db, detector_fence = _setup(tmp_path / "detector")
    detector_claim = claim_next_file(detector_fence, path=detector_db)
    assert detector_claim is not None
    advance_file_stage(
        detector_fence,
        detector_claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="text",
        path=detector_db,
    )
    advance_file_stage(
        detector_fence,
        detector_claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 20, "text_chars": 20},
        path=detector_db,
    )
    checkpoint_detector(
        detector_fence,
        detector_claim.file_id,
        (),
        selected_for_model=False,
        path=detector_db,
    )
    terminalize_file(
        detector_fence,
        detector_claim.file_id,
        FileTerminal.COMPLETE_DETECTOR_ONLY,
        path=detector_db,
    )

    guard_db, guard_fence = _setup(tmp_path / "incomplete-model")
    guard_claim = claim_next_file(guard_fence, path=guard_db)
    assert guard_claim is not None
    _advance_to_selected(guard_db, guard_fence, guard_claim.file_id)
    store_chunks(
        guard_fence,
        guard_claim.file_id,
        (ChunkSpec(0, 0, 20, "4" * 64),),
        path=guard_db,
    )
    advance_file_stage(
        guard_fence,
        guard_claim.file_id,
        FileStage.MODEL_REVIEWED,
        path=guard_db,
    )
    with pytest.raises(CheckpointError, match="complete valid chunk set"):
        advance_file_stage(
            guard_fence,
            guard_claim.file_id,
            FileStage.MODEL_RESPONSE_VALID,
            path=guard_db,
        )

    model_db, model_fence = _setup(tmp_path / "valid-model")
    model_claim = claim_next_file(model_fence, path=model_db)
    assert model_claim is not None
    _advance_to_selected(model_db, model_fence, model_claim.file_id)
    store_chunks(
        model_fence,
        model_claim.file_id,
        (ChunkSpec(0, 0, 20, "4" * 64),),
        path=model_db,
    )
    with pytest.raises(CheckpointError, match="model-reviewed terminal contradicts"):
        terminalize_file(
            model_fence,
            model_claim.file_id,
            FileTerminal.COMPLETE_MODEL_REVIEWED,
            path=model_db,
        )
    conn = open_connection(model_db, read_only=True)
    try:
        chunk_id = int(conn.execute("SELECT chunk_id FROM analyst_chunks").fetchone()[0])
    finally:
        conn.close()
    attempt_id, _ = precharge_attempt(
        model_fence, chunk_id, "5" * 64, path=model_db,
    )
    finish_valid_attempt(
        model_fence,
        attempt_id,
        WorksheetResult(
            document_type="Public note",
            subject="Synthetic",
            model_assessment=Assessment.NO_FINDINGS,
            findings=(),
            raw_finding_count=0,
            removed_duplicate_count=0,
            dropped_ungrounded_count=0,
        ),
        path=model_db,
    )
    advance_file_stage(
        model_fence,
        model_claim.file_id,
        FileStage.MODEL_REVIEWED,
        path=model_db,
    )
    advance_file_stage(
        model_fence,
        model_claim.file_id,
        FileStage.MODEL_RESPONSE_VALID,
        path=model_db,
    )
    terminalize_file(
        model_fence,
        model_claim.file_id,
        FileTerminal.COMPLETE_MODEL_REVIEWED,
        path=model_db,
    )


def test_success_terminal_guards_reject_forged_cross_stage_evidence(
    tmp_path: Path,
) -> None:
    detector_db, fence = _setup(tmp_path / "detector-with-chunk")
    claim = claim_next_file(fence, path=detector_db)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=detector_db,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 1, "text_chars": 1},
        path=detector_db,
    )
    checkpoint_detector(
        fence, claim.file_id, (), selected_for_model=False, path=detector_db,
    )
    conn = open_connection(detector_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO analyst_chunks("
            "file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
            "VALUES(?,0,0,1,?,'pending')",
            (claim.file_id, "4" * 64),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    with pytest.raises(CheckpointError, match="cannot have model chunks"):
        terminalize_file(
            fence,
            claim.file_id,
            FileTerminal.COMPLETE_DETECTOR_ONLY,
            path=detector_db,
        )

    content_db, fence = _setup(tmp_path / "no-content-with-hit")
    claim = claim_next_file(fence, path=content_db)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=content_db,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 0, "text_chars": 0},
        path=content_db,
    )
    conn = open_connection(content_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO analyst_detector_hits("
            "file_id,ordinal,kind,value,start_char,end_char) "
            "VALUES(?,0,'email','x',0,1)",
            (claim.file_id,),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    with pytest.raises(CheckpointError, match="cannot retain detector hits"):
        terminalize_file(
            fence,
            claim.file_id,
            FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT,
            path=content_db,
        )


def test_completion_code_is_derived_from_terminal_coverage(tmp_path: Path) -> None:
    no_content_db, no_content_fence = _setup(tmp_path / "no-content")
    claim = claim_next_file(no_content_fence, path=no_content_db)
    assert claim is not None
    advance_file_stage(
        no_content_fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="text",
        path=no_content_db,
    )
    advance_file_stage(
        no_content_fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 0, "text_chars": 0},
        path=no_content_db,
    )
    terminalize_file(
        no_content_fence,
        claim.file_id,
        FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT,
        path=no_content_db,
    )
    begin_finalization(no_content_fence, "8" * 64, path=no_content_db)
    finish_finalization(
        no_content_fence, "8" * 64, "9" * 64, path=no_content_db,
    )
    conn = open_connection(no_content_db, read_only=True)
    try:
        assert conn.execute(
            "SELECT completion_code FROM analyst_runs WHERE run_id='run'"
        ).fetchone()[0] == "complete_no_supported_content"
    finally:
        conn.close()

    supported_db, supported_fence = _setup(tmp_path / "supported")
    claim = claim_next_file(supported_fence, path=supported_db)
    assert claim is not None
    advance_file_stage(
        supported_fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="text",
        path=supported_db,
    )
    advance_file_stage(
        supported_fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 1, "text_chars": 1},
        path=supported_db,
    )
    checkpoint_detector(
        supported_fence,
        claim.file_id,
        (),
        selected_for_model=False,
        path=supported_db,
    )
    terminalize_file(
        supported_fence,
        claim.file_id,
        FileTerminal.COMPLETE_DETECTOR_ONLY,
        path=supported_db,
    )
    begin_finalization(supported_fence, "8" * 64, path=supported_db)
    finish_finalization(
        supported_fence, "8" * 64, "9" * 64, path=supported_db,
    )
    conn = open_connection(supported_db, read_only=True)
    try:
        assert conn.execute(
            "SELECT completion_code FROM analyst_runs WHERE run_id='run'"
        ).fetchone()[0] == "complete"
    finally:
        conn.close()


def test_no_content_terminal_rejects_nonzero_extraction_counts(tmp_path: Path) -> None:
    db_path, fence = _setup(tmp_path)
    claim = claim_next_file(fence, path=db_path)
    assert claim is not None
    advance_file_stage(
        fence, claim.file_id, FileStage.FORMAT_IDENTIFIED,
        format_name="text", path=db_path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 1, "text_chars": 1},
        path=db_path,
    )

    with pytest.raises(CheckpointError, match="has extracted content"):
        terminalize_file(
            fence,
            claim.file_id,
            FileTerminal.COMPLETE_NO_SUPPORTED_CONTENT,
            path=db_path,
        )
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code FROM analyst_files WHERE file_id=?",
            (claim.file_id,),
        ).fetchone()) == ("active", None)
    finally:
        conn.close()


def test_file_terminal_is_immutable_and_partial_run_cannot_finalize(
    tmp_path: Path,
) -> None:
    db_path, fence = _setup(tmp_path, file_count=2)
    first = claim_next_file(fence, path=db_path)
    assert first is not None
    terminalize_file(
        fence, first.file_id, FileTerminal.EMPTY,
        detail=None, path=db_path,
    )
    with pytest.raises(CheckpointError, match="not active"):
        terminalize_file(fence, first.file_id, FileTerminal.PARSE_ERROR, path=db_path)
    with pytest.raises(CheckpointError, match="incomplete work"):
        begin_finalization(fence, "8" * 64, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code,terminal_detail "
            "FROM analyst_files WHERE file_id=?", (first.file_id,),
        ).fetchone()) == ("terminal", FileTerminal.EMPTY.value, None)
        assert conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id='run'"
        ).fetchone()[0] == RunState.RUNNING.value
    finally:
        conn.close()


def test_zero_file_finalization_is_atomic_and_token_fenced(tmp_path: Path) -> None:
    db_path, fence = _setup(tmp_path, file_count=0)
    token = "8" * 64
    manifest = "9" * 64
    begin_finalization(fence, token, now_utc="begin", path=db_path)
    assert _run_row(db_path) == (RunState.FINALIZING.value, None, token, None)

    with pytest.raises(CheckpointError, match="token or run state"):
        finish_finalization(
            fence, "7" * 64, manifest, now_utc="wrong", path=db_path,
        )
    assert _run_row(db_path) == (RunState.FINALIZING.value, None, token, None)
    assert current_lease(path=db_path) == fence

    finish_finalization(
        fence, token, manifest, now_utc="finish", path=db_path,
    )
    assert _run_row(db_path) == (
        RunState.COMPLETE.value,
        "complete_no_supported_content",
        token,
        manifest,
    )
    assert current_lease(path=db_path) is None
