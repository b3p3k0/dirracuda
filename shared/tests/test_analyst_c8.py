"""C8 acceptance tests for durable Analyst state and worker exclusion."""

from __future__ import annotations

from itertools import product
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import sqlite3
import stat

import pytest

from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    AnalystSchemaError,
    initialize_schema,
    validate_schema,
)
from experimental.analyst.models import FileStage, FileTerminal, ResumableState
from experimental.analyst.inventory import (
    InventoryExclusion,
    InventoryFile,
    InventoryResult,
)
from experimental.analyst.lease import (
    HEARTBEAT_FUTURE_TOLERANCE_NS,
    HEARTBEAT_MAX_AGE_NS,
    LeaseError,
    LeaseFence,
    ReconcileResult,
    acknowledge_cancel,
    claim_worker,
    current_lease,
    heartbeat,
    reconcile_lease,
    release_worker,
    request_cancel,
    signal_cancel,
)
from experimental.analyst.process_identity import (
    ProcessIdentity,
    ProcessIdentityUnavailable,
    current_process_identity,
)
from experimental.analyst.state import (
    AttemptState,
    ChunkState,
    FileWorkState,
    RunState,
    TERMINAL_ATTEMPT_STATES,
    TERMINAL_RUN_STATES,
    require_file_transition,
    require_run_transition,
    require_stage_advance,
    validate_file_state,
)
from experimental.analyst.store import (
    AnalystStoreBusy,
    AnalystStoreError,
    BUSY_TIMEOUT_MS,
    RunSpec,
    TRANSACTION_ATTEMPTS,
    abandon_run,
    create_run,
    get_db_path,
    initialize_database,
    list_active_runs,
    open_connection,
    run_immediate,
)
from shared.path_service import get_paths


_NOW = "2026-08-16T12:00:00Z"
_BOOT_ID = "00000000-0000-0000-0000-000000000001"


def _insert_run(
    db_path: Path,
    run_id: str,
    *,
    state: RunState = RunState.READY,
) -> None:
    finished = _NOW if state in {RunState.COMPLETE, RunState.ABANDONED} else None
    completion = (
        "complete" if state is RunState.COMPLETE
        else "abandoned" if state is RunState.ABANDONED
        else None
    )
    finalization_token = (
        "9" * 64
        if state in {RunState.FINALIZING, RunState.COMPLETE}
        else None
    )

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_runs("
            "run_id,state,created_at_utc,updated_at_utc,finished_at_utc,"
            "completion_code,mode,source_mode,source_root,output_root,"
            "source_identity_json,source_identity_sha256,report_label,model_tag,"
            "model_digest,worksheet_version,prompt_sha256,response_schema_sha256,"
            "detector_rules_version,detector_rules_sha256,parser_bundle_json,"
            "parser_bundle_sha256,chunk_chars,overlap_chars,num_ctx,num_predict,"
            "isolation_mode,reduced_isolation_ack,finalization_token) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, state.value, _NOW, _NOW, finished, completion,
                "fast", "unknown", "/public/source", "/public/output", "{}",
                "a" * 64, f"Public run {run_id}", "qwen3.6:27b", "b" * 64,
                "v2", "c" * 64, "d" * 64, "rules-v1", "e" * 64, "{}",
                "f" * 64, 8000, 256, 8192, 1024, "strict", 0,
                finalization_token,
            ),
        )
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
            "VALUES(?,?)",
            (run_id, _NOW),
        )

    run_immediate(operation, path=db_path)


def _run_spec(run_id: str) -> RunSpec:
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


def _inventory(*, bad_reason: str | None = None) -> InventoryResult:
    return InventoryResult(
        root_device=1,
        root_inode=2,
        root_mount_id=3,
        files=(
            InventoryFile("alpha.txt", 5, 10, 11, 1, 20, 0o100600, "1" * 64),
            InventoryFile("nested/beta.txt", 7, 12, 13, 1, 21, 0o100600, "2" * 64),
        ),
        exclusions=(
            InventoryExclusion("ignored-link", bad_reason or "symlink"),
        ),
    )


def _insert_file_work(
    db_path: Path,
    run_id: str,
    *,
    file_id: int,
    work_state: FileWorkState,
    terminal: FileTerminal | None = None,
) -> None:
    active_generation = 1 if work_state is FileWorkState.ACTIVE else None

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_files("
            "file_id,run_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,"
            "inode,mode,sha256,stage,work_state,terminal_code,active_generation,"
            "updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                file_id, run_id, file_id, f"public-{file_id}.txt", 10, 1, 1,
                1, file_id, 0o100600, "1" * 64, FileStage.DISCOVERED.value,
                work_state.value, None if terminal is None else terminal.value,
                active_generation, _NOW,
            ),
        )

    run_immediate(operation, path=db_path)


def _insert_dispatching_attempt(
    db_path: Path, *, file_id: int, chunk_id: int, attempt_id: str,
) -> None:
    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_chunks("
            "chunk_id,file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
            "VALUES(?,?,?,?,?,?,?)",
            (chunk_id, file_id, 0, 0, 5, "2" * 64, "pending"),
        )
        conn.execute(
            "INSERT INTO analyst_model_attempts("
            "attempt_id,chunk_id,attempt_no,request_sha256,state,charged_at_utc) "
            "VALUES(?,?,?,?,?,?)",
            (attempt_id, chunk_id, 1, "3" * 64, "dispatching", _NOW),
        )

    run_immediate(operation, path=db_path)


def _run_state(db_path: Path, run_id: str) -> tuple[str, int]:
    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT state,revision FROM analyst_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row is not None
        return str(row["state"]), int(row["revision"])
    finally:
        conn.close()


def _race_claim(
    db_path: Path,
    run_id: str,
    owner_token: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    """Spawn target: race one real process/connection for the global slot."""
    try:
        if not start.wait(10):
            results.put(("error", run_id, "start timeout"))
            return
        fence = claim_worker(
            run_id,
            current_process_identity(),
            owner_token=owner_token,
            path=db_path,
        )
        results.put(("ok", run_id, fence is not None))
    except BaseException as exc:
        results.put(("error", run_id, type(exc).__name__))


_RUN_EDGES = {
    (RunState.READY, RunState.RUNNING),
    (RunState.READY, RunState.ABANDONED),
    (RunState.RUNNING, RunState.CANCEL_REQUESTED),
    (RunState.RUNNING, RunState.FINALIZING),
    (RunState.RUNNING, RunState.INTERRUPTED),
    (RunState.CANCEL_REQUESTED, RunState.CANCELLED_PENDING_RESUME),
    (RunState.CANCEL_REQUESTED, RunState.INTERRUPTED),
    (RunState.CANCELLED_PENDING_RESUME, RunState.RUNNING),
    (RunState.CANCELLED_PENDING_RESUME, RunState.ABANDONED),
    (RunState.INTERRUPTED, RunState.RUNNING),
    (RunState.INTERRUPTED, RunState.ABANDONED),
    (RunState.FINALIZING, RunState.COMPLETE),
    (RunState.FINALIZING, RunState.INTERRUPTED),
}

_FILE_EDGES = {
    (FileWorkState.PENDING, FileWorkState.ACTIVE),
    (FileWorkState.PENDING, FileWorkState.CANCELLED_PENDING_RESUME),
    (FileWorkState.PENDING, FileWorkState.TERMINAL),
    (FileWorkState.ACTIVE, FileWorkState.PENDING),
    (FileWorkState.ACTIVE, FileWorkState.CANCELLED_PENDING_RESUME),
    (FileWorkState.ACTIVE, FileWorkState.TERMINAL),
    (FileWorkState.CANCELLED_PENDING_RESUME, FileWorkState.PENDING),
    (FileWorkState.CANCELLED_PENDING_RESUME, FileWorkState.TERMINAL),
}


@pytest.mark.parametrize("source,target", list(product(RunState, repeat=2)))
def test_run_transition_matrix_is_exact(source: RunState, target: RunState) -> None:
    if (source, target) in _RUN_EDGES:
        require_run_transition(source.value, target.value)
    else:
        with pytest.raises(ValueError, match="invalid Analyst run transition"):
            require_run_transition(source, target)


def test_run_terminal_and_attempt_terminal_sets_are_exact() -> None:
    assert TERMINAL_RUN_STATES == {RunState.COMPLETE, RunState.ABANDONED}
    assert TERMINAL_ATTEMPT_STATES == set(AttemptState) - {AttemptState.DISPATCHING}
    assert {state.value for state in ChunkState} == {
        "pending",
        "model_response_valid",
        "model_invalid",
        "model_timeout",
        "model_transport_error",
    }


def test_persisted_lifecycle_vocabulary_matches_shared_enums() -> None:
    assert db_schema.RUN_STATES == tuple(state.value for state in RunState)
    assert db_schema.FILE_STAGES == tuple(stage.value for stage in FileStage)
    assert db_schema.FILE_WORK_STATES == tuple(state.value for state in FileWorkState)
    assert db_schema.FILE_TERMINALS == tuple(reason.value for reason in FileTerminal)
    assert db_schema.CHUNK_STATES == tuple(state.value for state in ChunkState)
    assert db_schema.ATTEMPT_STATES == tuple(state.value for state in AttemptState)


@pytest.mark.parametrize("source,target", list(product(FileWorkState, repeat=2)))
def test_file_transition_matrix_is_exact(
    source: FileWorkState, target: FileWorkState,
) -> None:
    if (source, target) in _FILE_EDGES:
        require_file_transition(source.value, target.value)
    else:
        with pytest.raises(ValueError, match="invalid Analyst file transition"):
            require_file_transition(source, target)


def test_stage_progression_is_strictly_one_step_and_never_reopens() -> None:
    stages = list(FileStage)
    for source, target in product(stages, repeat=2):
        if stages.index(target) == stages.index(source) + 1:
            require_stage_advance(source.value, target.value)
        else:
            with pytest.raises(ValueError, match="invalid Analyst stage transition"):
                require_stage_advance(source, target)


@pytest.mark.parametrize("state", [FileWorkState.PENDING, FileWorkState.ACTIVE])
def test_nonterminal_file_state_forbids_terminal_and_resume_markers(
    state: FileWorkState,
) -> None:
    validate_file_state(work_state=state, terminal=None, resumable_state=None)
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=state,
            terminal=FileTerminal.EMPTY,
            resumable_state=None,
        )
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=state,
            terminal=None,
            resumable_state=ResumableState.CANCELLED_PENDING_RESUME,
        )


def test_terminal_file_requires_exactly_one_terminal_reason() -> None:
    for reason in FileTerminal:
        validate_file_state(
            work_state=FileWorkState.TERMINAL,
            terminal=reason.value,
            resumable_state=None,
        )
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=FileWorkState.TERMINAL,
            terminal=None,
            resumable_state=None,
        )
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=FileWorkState.TERMINAL,
            terminal=FileTerminal.EMPTY,
            resumable_state=ResumableState.CANCELLED_PENDING_RESUME,
        )


def test_resumable_file_requires_only_the_frozen_resume_marker() -> None:
    validate_file_state(
        work_state=FileWorkState.CANCELLED_PENDING_RESUME.value,
        terminal=None,
        resumable_state=ResumableState.CANCELLED_PENDING_RESUME.value,
    )
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=FileWorkState.CANCELLED_PENDING_RESUME,
            terminal=None,
            resumable_state=None,
        )
    with pytest.raises(ValueError, match="state is inconsistent"):
        validate_file_state(
            work_state=FileWorkState.CANCELLED_PENDING_RESUME,
            terminal=FileTerminal.CANCELLED_ABANDONED,
            resumable_state=ResumableState.CANCELLED_PENDING_RESUME,
        )


@pytest.mark.parametrize(
    "call,args",
    [
        (require_run_transition, ("unknown", RunState.READY)),
        (require_file_transition, ("unknown", FileWorkState.PENDING)),
        (require_stage_advance, ("unknown", FileStage.DISCOVERED)),
    ],
)
def test_lifecycle_helpers_fail_closed_on_unknown_values(call, args) -> None:
    with pytest.raises(ValueError):
        call(*args)


def test_analyst_sidecar_path_is_canonical_and_separate_from_primary_db(
    tmp_path: Path,
) -> None:
    paths = get_paths(home_root=tmp_path / "home", repo_root=tmp_path / "checkout")

    assert paths.analyst_db_file == (
        paths.home_root / "data" / "experimental" / "analyst.db"
    )
    assert paths.analyst_db_file.parent == paths.experimental_dir
    assert paths.analyst_db_file != paths.main_db_file


def test_store_override_must_be_absolute(tmp_path: Path) -> None:
    assert get_db_path(tmp_path / "analyst.db") == tmp_path / "analyst.db"
    with pytest.raises(ValueError, match="must be absolute"):
        get_db_path(Path("analyst.db"))


def test_fresh_database_has_exact_identity_schema_and_owner_permissions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "private" / "analyst.db"
    assert initialize_database(db_path) == db_path

    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert db_path.stat().st_uid == os.getuid()

    conn = open_connection(db_path)
    try:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "analyst_runs",
            "analyst_files",
            "analyst_inventory_exclusions",
            "analyst_provenance_units",
            "analyst_chunks",
            "analyst_model_attempts",
            "analyst_detector_hits",
            "analyst_model_findings",
            "analyst_gpu_lease",
            "analyst_ollama_contacts",
            "analyst_ollama_schedule",
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert indexes == {
            "idx_analyst_runs_state_updated",
            "idx_analyst_runs_host",
            "idx_analyst_runs_endpoint",
            "idx_analyst_files_work",
            "idx_analyst_files_terminal",
            "idx_analyst_files_stage",
            "idx_analyst_provenance_span",
            "idx_analyst_chunks_work",
            "idx_analyst_attempts_state",
            "ux_analyst_attempts_one_valid",
            "idx_analyst_exclusions_reason",
            "idx_analyst_detector_kind",
            "idx_analyst_findings_category",
            "idx_analyst_contacts_run",
            "idx_analyst_contacts_chunk",
            "ux_analyst_contacts_one_dispatching",
            "ux_analyst_contacts_semantic_slot",
            "idx_analyst_schedule_state",
        }
        assert all(
            row[5] == 1
            for row in conn.execute("PRAGMA table_list")
            if row[1] in tables
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        lease = conn.execute(
            "SELECT slot,generation,run_id FROM analyst_gpu_lease"
        ).fetchall()
        assert [tuple(row) for row in lease] == [(1, 0, None)]
    finally:
        conn.close()


def test_database_initialization_is_idempotent_and_preserves_state(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "state" / "analyst.db")
    inode = db_path.stat().st_ino
    run_immediate(
        lambda conn: conn.execute(
            "UPDATE analyst_gpu_lease SET generation=7 WHERE slot=1"
        ),
        path=db_path,
    )

    assert initialize_database(db_path) == db_path
    assert db_path.stat().st_ino == inode
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT generation FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == 7
    finally:
        conn.close()


def test_existing_empty_version_zero_database_initializes_in_place(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "existing-empty"
    parent.mkdir(mode=0o700)
    db_path = parent / "analyst.db"
    fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    inode = db_path.stat().st_ino

    assert initialize_database(db_path) == db_path
    assert db_path.stat().st_ino == inode
    conn = open_connection(db_path, read_only=True)
    try:
        validate_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "prepare",
    [
        lambda conn: conn.execute("CREATE TABLE unexpected(value TEXT)"),
        lambda conn: conn.execute("PRAGMA user_version=2"),
        lambda conn: conn.execute("PRAGMA application_id=12345"),
    ],
    ids=("partial", "newer-version", "foreign-application"),
)
def test_unknown_partial_or_newer_database_fails_without_mutation(
    tmp_path: Path, prepare,
) -> None:
    db_path = tmp_path / "foreign" / "analyst.db"
    db_path.parent.mkdir(mode=0o700)
    conn = sqlite3.connect(db_path, autocommit=True)
    try:
        prepare(conn)
        before = tuple(conn.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ))
        identity = (
            conn.execute("PRAGMA application_id").fetchone()[0],
            conn.execute("PRAGMA user_version").fetchone()[0],
        )
    finally:
        conn.close()
    db_path.chmod(0o600)

    with pytest.raises(AnalystSchemaError):
        initialize_database(db_path)

    audit = sqlite3.connect(db_path, autocommit=True)
    try:
        after = tuple(audit.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ))
        assert after == before
        assert (
            audit.execute("PRAGMA application_id").fetchone()[0],
            audit.execute("PRAGMA user_version").fetchone()[0],
        ) == identity
    finally:
        audit.close()


def test_schema_creation_rolls_back_all_ddl_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "rollback.db"
    conn = sqlite3.connect(db_path, autocommit=True)
    original = db_schema._TABLE_DDL
    monkeypatch.setattr(
        db_schema,
        "_TABLE_DDL",
        (original[0], "CREATE TABLE broken("),
    )
    try:
        with pytest.raises(sqlite3.Error):
            initialize_schema(conn)
        assert conn.in_transaction is False
        assert tuple(conn.execute(
            "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        )) == ()
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 0
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()


def test_schema_validation_rejects_extra_objects_and_active_initialization(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "strict" / "analyst.db")
    conn = sqlite3.connect(db_path, autocommit=True)
    try:
        conn.execute("CREATE TABLE unexpected(value TEXT) STRICT")
        with pytest.raises(AnalystSchemaError, match="signature"):
            validate_schema(conn)
    finally:
        conn.close()

    empty = sqlite3.connect(tmp_path / "active.db")
    try:
        empty.execute("BEGIN")
        with pytest.raises(AnalystSchemaError, match="no active transaction"):
            initialize_schema(empty)
    finally:
        empty.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_every_connection_enforces_e13_and_safety_pragmas(
    tmp_path: Path, read_only: bool,
) -> None:
    db_path = initialize_database(tmp_path / "pragmas" / "analyst.db")
    conn = open_connection(db_path, read_only=read_only)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert conn.execute("PRAGMA mmap_size").fetchone()[0] == 0
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert conn.execute("PRAGMA query_only").fetchone()[0] == int(read_only)
    finally:
        conn.close()


def test_read_only_connection_rejects_writes(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "readonly" / "analyst.db")
    conn = open_connection(db_path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "UPDATE analyst_gpu_lease SET generation=1 WHERE slot=1"
            )
    finally:
        conn.close()


def test_store_rejects_symlinks_and_non_owner_only_database_modes(
    tmp_path: Path,
) -> None:
    real = initialize_database(tmp_path / "real" / "analyst.db")
    link = tmp_path / "linked.db"
    link.symlink_to(real)
    with pytest.raises(PermissionError, match="regular file"):
        open_connection(link)

    real.chmod(0o640)
    with pytest.raises(PermissionError, match="owner-only"):
        open_connection(real)


def test_store_rejects_symlink_sidecar_directory(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PermissionError, match="real directory"):
        initialize_database(linked_parent / "analyst.db")


def test_foreign_keys_are_enforced_on_real_store_connections(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "foreign-key" / "analyst.db")

    def forge_lease(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE analyst_gpu_lease SET generation=1,run_id='missing',"
            "owner_token=?,pid=1,start_ticks=0,boot_id=?,"
            "heartbeat_monotonic_ns=1,claimed_at_utc=?,heartbeat_at_utc=? "
            "WHERE slot=1",
            ("a" * 64, "00000000-0000-0000-0000-000000000001", "now", "now"),
        )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        run_immediate(forge_lease, path=db_path)


def test_immediate_transaction_rolls_back_callback_failure(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "callback" / "analyst.db")

    def fail_after_write(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE analyst_gpu_lease SET generation=9 WHERE slot=1")
        raise RuntimeError("synthetic crash")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        run_immediate(fail_after_write, path=db_path)
    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT generation FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_immediate_transaction_uses_bounded_busy_retry(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "busy" / "analyst.db")
    blocker = open_connection(db_path)
    calls = 0

    def should_not_enter(conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1

    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(AnalystStoreBusy, match="bounded retry"):
            run_immediate(should_not_enter, path=db_path)
        assert calls == 0
        assert TRANSACTION_ATTEMPTS == 4
    finally:
        blocker.rollback()
        blocker.close()


def test_create_run_persists_complete_inventory_atomically_and_canonically(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "create-run" / "analyst.db")
    create_run(_run_spec("run"), _inventory(), now_utc=_NOW, path=db_path)

    conn = open_connection(db_path, read_only=True)
    try:
        run = conn.execute(
            "SELECT state,source_identity_json,parser_bundle_json,created_at_utc "
            "FROM analyst_runs WHERE run_id='run'"
        ).fetchone()
        assert tuple(run) == (
            RunState.READY.value,
            '{"kind":"public-synthetic","version":1}',
            '{"bundle":"public-test"}',
            _NOW,
        )
        files = [tuple(row) for row in conn.execute(
            "SELECT ordinal,relative_path,work_state,stage FROM analyst_files "
            "WHERE run_id='run' ORDER BY ordinal"
        )]
        assert files == [
            (0, "alpha.txt", "pending", FileStage.DISCOVERED.value),
            (1, "nested/beta.txt", "pending", FileStage.DISCOVERED.value),
        ]
        assert [tuple(row) for row in conn.execute(
            "SELECT ordinal,relative_path,reason "
            "FROM analyst_inventory_exclusions WHERE run_id='run'"
        )] == [(0, "ignored-link", "symlink")]
    finally:
        conn.close()


def test_create_run_rolls_back_run_and_files_on_invalid_inventory(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "create-rollback" / "analyst.db")
    with pytest.raises(sqlite3.IntegrityError):
        create_run(
            _run_spec("bad"), _inventory(bad_reason="not-frozen"),
            now_utc=_NOW, path=db_path,
        )

    conn = open_connection(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_runs WHERE run_id='bad'"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analyst_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_active_run_listing_is_bounded_content_free_and_excludes_terminals(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "listing" / "analyst.db")
    _insert_run(db_path, "ready")
    _insert_run(db_path, "running", state=RunState.RUNNING)
    _insert_run(db_path, "complete", state=RunState.COMPLETE)
    _insert_run(db_path, "abandoned", state=RunState.ABANDONED)

    rows = list_active_runs(path=db_path)
    assert {row["run_id"] for row in rows} == {"ready", "running"}
    assert set(rows[0].keys()) == {
        "run_id", "state", "revision", "created_at_utc", "updated_at_utc",
        "mode", "source_mode", "report_label", "host_type",
        "protocol_server_id", "ip_address", "port", "cancel_requested_at_utc",
    }
    with pytest.raises(ValueError, match="between 1 and 1000"):
        list_active_runs(path=db_path, limit=0)


def test_abandon_terminalizes_only_remaining_files_and_is_immutable(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "abandon" / "analyst.db")
    _insert_run(db_path, "run")
    _insert_file_work(
        db_path, "run", file_id=1, work_state=FileWorkState.PENDING,
    )
    _insert_file_work(
        db_path, "run", file_id=2, work_state=FileWorkState.TERMINAL,
        terminal=FileTerminal.EMPTY,
    )
    _insert_dispatching_attempt(
        db_path, file_id=1, chunk_id=1, attempt_id="attempt-1",
    )

    abandon_run("run", now_utc="abandon-time", path=db_path)
    assert _run_state(db_path, "run")[0] == RunState.ABANDONED.value
    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT file_id,work_state,terminal_code,terminal_detail "
            "FROM analyst_files ORDER BY file_id"
        )] == [
            (1, "terminal", FileTerminal.CANCELLED_ABANDONED.value, "operator_abandon"),
            (2, "terminal", FileTerminal.EMPTY.value, None),
        ]
        assert tuple(conn.execute(
            "SELECT state,failure_code FROM analyst_model_attempts"
        ).fetchone()) == ("cancelled_unverified", "cancelled_unverified")
    finally:
        conn.close()
    with pytest.raises(AnalystStoreError, match="not abandonable"):
        abandon_run("run", now_utc="again", path=db_path)


def test_abandon_refuses_active_lease_without_partial_changes(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "abandon-lease" / "analyst.db")
    _insert_run(db_path, "run")
    _insert_file_work(
        db_path, "run", file_id=1, work_state=FileWorkState.PENDING,
    )
    fence = claim_worker(
        "run", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=1, path=db_path,
    )
    assert fence is not None

    with pytest.raises(AnalystStoreError, match="active worker lease"):
        abandon_run("run", now_utc="abandon-time", path=db_path)
    assert _run_state(db_path, "run")[0] == RunState.RUNNING.value
    assert current_lease(path=db_path) == fence

    assert release_worker(fence, now_utc="release-time", path=db_path) is RunState.INTERRUPTED
    abandon_run("run", now_utc="abandon-time", path=db_path)
    assert _run_state(db_path, "run")[0] == RunState.ABANDONED.value


def test_claim_is_atomic_idempotent_and_transitions_only_the_winner(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "claim" / "analyst.db")
    _insert_run(db_path, "run-a")
    _insert_run(db_path, "run-b")
    process = ProcessIdentity(101, 202, _BOOT_ID)
    token = "a" * 64

    fence = claim_worker(
        "run-a", process, owner_token=token,
        heartbeat_monotonic_ns=300, now_utc=_NOW, path=db_path,
    )
    assert fence == LeaseFence(1, "run-a", token, process, 300)
    assert current_lease(path=db_path) == fence
    assert _run_state(db_path, "run-a") == (RunState.RUNNING.value, 1)
    assert _run_state(db_path, "run-b") == (RunState.READY.value, 0)

    retry = claim_worker(
        "run-a", process, owner_token=token,
        heartbeat_monotonic_ns=999, now_utc="later", path=db_path,
    )
    assert retry == fence
    assert claim_worker(
        "run-b", ProcessIdentity(102, 203, _BOOT_ID),
        owner_token="b" * 64, path=db_path,
    ) is None
    assert _run_state(db_path, "run-b") == (RunState.READY.value, 0)


def test_claim_rejects_missing_terminal_and_invalid_owner_inputs(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "claim-invalid" / "analyst.db")
    process = ProcessIdentity(101, 202, _BOOT_ID)
    with pytest.raises(LeaseError, match="does not exist"):
        claim_worker("missing", process, owner_token="a" * 64, path=db_path)

    _insert_run(db_path, "complete", state=RunState.COMPLETE)
    with pytest.raises(LeaseError, match="not claimable"):
        claim_worker("complete", process, owner_token="a" * 64, path=db_path)
    with pytest.raises(ValueError, match="64 lowercase hex"):
        claim_worker("complete", process, owner_token="A" * 64, path=db_path)


def test_heartbeat_and_release_are_fenced_against_old_owner(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "fence" / "analyst.db")
    _insert_run(db_path, "first")
    _insert_run(db_path, "second")
    first = claim_worker(
        "first", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="1" * 64, heartbeat_monotonic_ns=10, path=db_path,
    )
    assert first is not None
    advanced = heartbeat(first, heartbeat_monotonic_ns=11, path=db_path)
    assert advanced.heartbeat_monotonic_ns == 11
    with pytest.raises(LeaseError, match="cannot move backwards"):
        heartbeat(advanced, heartbeat_monotonic_ns=10, path=db_path)
    with pytest.raises(LeaseError, match="no longer matches"):
        heartbeat(first, heartbeat_monotonic_ns=12, path=db_path)

    assert release_worker(advanced, path=db_path) is RunState.INTERRUPTED
    assert _run_state(db_path, "first")[0] == RunState.INTERRUPTED.value
    second = claim_worker(
        "second", ProcessIdentity(202, 2, _BOOT_ID),
        owner_token="2" * 64, heartbeat_monotonic_ns=20, path=db_path,
    )
    assert second is not None
    assert second.generation == advanced.generation + 2
    with pytest.raises(LeaseError, match="no longer matches"):
        release_worker(advanced, path=db_path)
    assert current_lease(path=db_path) == second


def test_two_spawned_workers_race_and_exactly_one_claims_global_slot(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "race" / "analyst.db")
    _insert_run(db_path, "race-a")
    _insert_run(db_path, "race-b")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_claim,
            args=(db_path, run_id, token, start, results),
        )
        for run_id, token in (("race-a", "a" * 64), ("race-b", "b" * 64))
    ]
    for process in processes:
        process.start()
    start.set()
    try:
        observed = [results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
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
    winners = [run_id for _, run_id, won in observed if won]
    assert len(winners) == 1
    loser = ({"race-a", "race-b"} - set(winners)).pop()
    assert current_lease(path=db_path).run_id == winners[0]
    assert _run_state(db_path, winners[0])[0] == RunState.RUNNING.value
    assert _run_state(db_path, loser) == (RunState.READY.value, 0)


@pytest.mark.parametrize(
    "expected,identity_reader,now",
    [
        (
            ReconcileResult.REATTACHED,
            lambda identity: lambda _pid: identity,
            100,
        ),
        (
            ReconcileResult.BLOCKED_STALE_LIVE,
            lambda identity: lambda _pid: identity,
            100 + HEARTBEAT_MAX_AGE_NS + 1,
        ),
        (
            ReconcileResult.BLOCKED_INVALID_HEARTBEAT,
            lambda identity: lambda _pid: identity,
            0,
        ),
        (
            ReconcileResult.BLOCKED_UNVERIFIABLE,
            lambda _identity: lambda _pid: (_ for _ in ()).throw(
                ProcessIdentityUnavailable("synthetic unreadable")
            ),
            100,
        ),
    ],
)
def test_reconcile_blocks_or_reattaches_live_evidence_without_mutation(
    tmp_path: Path, expected: ReconcileResult, identity_reader, now: int,
) -> None:
    db_path = initialize_database(tmp_path / expected.value / "analyst.db")
    _insert_run(db_path, "run")
    identity = ProcessIdentity(101, 202, _BOOT_ID)
    persisted_heartbeat = (
        HEARTBEAT_FUTURE_TOLERANCE_NS + 1
        if expected is ReconcileResult.BLOCKED_INVALID_HEARTBEAT
        else 100
    )
    fence = claim_worker(
        "run", identity, owner_token="a" * 64,
        heartbeat_monotonic_ns=persisted_heartbeat, path=db_path,
    )
    assert fence is not None

    assert reconcile_lease(
        path=db_path,
        now_monotonic_ns=now,
        identity_reader=identity_reader(identity),
    ) is expected
    assert current_lease(path=db_path) == fence
    assert _run_state(db_path, "run")[0] == RunState.RUNNING.value


def test_dead_worker_reconcile_is_atomic_and_resume_preserves_terminals(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "dead" / "analyst.db")
    _insert_run(db_path, "run")
    _insert_file_work(
        db_path, "run", file_id=1, work_state=FileWorkState.ACTIVE,
    )
    _insert_file_work(
        db_path, "run", file_id=2, work_state=FileWorkState.TERMINAL,
        terminal=FileTerminal.EMPTY,
    )
    _insert_dispatching_attempt(
        db_path, file_id=1, chunk_id=1, attempt_id="attempt-1",
    )
    fence = claim_worker(
        "run", ProcessIdentity(101, 202, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=100, path=db_path,
    )
    assert fence is not None

    assert reconcile_lease(
        path=db_path, now_monotonic_ns=100, now_utc=_NOW,
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    assert current_lease(path=db_path) is None
    assert _run_state(db_path, "run")[0] == RunState.INTERRUPTED.value
    conn = open_connection(db_path, read_only=True)
    try:
        files = [tuple(row) for row in conn.execute(
            "SELECT file_id,work_state,terminal_code,active_generation "
            "FROM analyst_files ORDER BY file_id"
        )]
        assert files == [
            (1, "pending", None, None),
            (2, "terminal", FileTerminal.EMPTY.value, None),
        ]
        assert tuple(conn.execute(
            "SELECT state,failure_code,finished_at_utc "
            "FROM analyst_model_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()) == ("orphaned_unknown", "orphaned_unknown", _NOW)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "observed",
    [
        ProcessIdentity(101, 203, _BOOT_ID),
        ProcessIdentity(101, 202, "00000000-0000-0000-0000-000000000002"),
    ],
    ids=("pid-reused", "host-rebooted"),
)
def test_reconcile_clears_pid_reuse_or_reboot_identity(
    tmp_path: Path, observed: ProcessIdentity,
) -> None:
    db_path = initialize_database(tmp_path / str(observed.start_ticks) / "analyst.db")
    _insert_run(db_path, "run")
    fence = claim_worker(
        "run", ProcessIdentity(101, 202, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=100, path=db_path,
    )
    assert fence is not None

    assert reconcile_lease(
        path=db_path, now_monotonic_ns=100, now_utc=_NOW,
        identity_reader=lambda _pid: observed,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    assert current_lease(path=db_path) is None
    assert _run_state(db_path, "run")[0] == RunState.INTERRUPTED.value


def test_cancel_intent_is_durable_before_ack_and_resume_reopens_only_work(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "cancel" / "analyst.db")
    _insert_run(db_path, "run")
    _insert_file_work(
        db_path, "run", file_id=1, work_state=FileWorkState.ACTIVE,
    )
    _insert_file_work(
        db_path, "run", file_id=2, work_state=FileWorkState.TERMINAL,
        terminal=FileTerminal.EMPTY,
    )
    _insert_dispatching_attempt(
        db_path, file_id=1, chunk_id=1, attempt_id="attempt-1",
    )
    identity = ProcessIdentity(101, 202, _BOOT_ID)
    fence = claim_worker(
        "run", identity, owner_token="a" * 64,
        heartbeat_monotonic_ns=100, path=db_path,
    )
    assert fence is not None

    returned = request_cancel("run", now_utc="cancel-time", path=db_path)
    assert returned == fence
    assert _run_state(db_path, "run")[0] == RunState.CANCEL_REQUESTED.value
    acknowledge_cancel(fence, now_utc="ack-time", path=db_path)
    assert current_lease(path=db_path) is None
    assert _run_state(db_path, "run")[0] == RunState.CANCELLED_PENDING_RESUME.value

    resumed = claim_worker(
        "run", ProcessIdentity(202, 303, _BOOT_ID),
        owner_token="b" * 64, heartbeat_monotonic_ns=200, path=db_path,
    )
    assert resumed is not None
    conn = open_connection(db_path, read_only=True)
    try:
        files = [tuple(row) for row in conn.execute(
            "SELECT file_id,work_state,terminal_code FROM analyst_files ORDER BY file_id"
        )]
        assert files == [
            (1, "pending", None),
            (2, "terminal", FileTerminal.EMPTY.value),
        ]
        assert tuple(conn.execute(
            "SELECT state,failure_code,finished_at_utc "
            "FROM analyst_model_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()) == ("cancelled_unverified", "cancelled_unverified", "ack-time")
    finally:
        conn.close()


def test_cancel_without_lease_atomically_becomes_resumable_and_cancels_work(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "cancel-no-lease" / "analyst.db")
    _insert_run(db_path, "run", state=RunState.RUNNING)
    _insert_file_work(
        db_path, "run", file_id=1, work_state=FileWorkState.PENDING,
    )
    _insert_file_work(
        db_path, "run", file_id=2, work_state=FileWorkState.ACTIVE,
    )
    _insert_file_work(
        db_path, "run", file_id=3, work_state=FileWorkState.TERMINAL,
        terminal=FileTerminal.EMPTY,
    )
    _insert_dispatching_attempt(
        db_path, file_id=2, chunk_id=1, attempt_id="attempt-1",
    )

    assert request_cancel("run", now_utc="cancel-time", path=db_path) is None
    assert _run_state(db_path, "run") == (
        RunState.CANCELLED_PENDING_RESUME.value,
        2,
    )
    assert current_lease(path=db_path) is None
    conn = open_connection(db_path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT file_id,work_state,terminal_code,active_generation "
            "FROM analyst_files ORDER BY file_id"
        )] == [
            (1, FileWorkState.CANCELLED_PENDING_RESUME.value, None, None),
            (2, FileWorkState.CANCELLED_PENDING_RESUME.value, None, None),
            (3, FileWorkState.TERMINAL.value, FileTerminal.EMPTY.value, None),
        ]
        assert tuple(conn.execute(
            "SELECT state,failure_code,finished_at_utc "
            "FROM analyst_model_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()) == (
            AttemptState.CANCELLED_UNVERIFIED.value,
            AttemptState.CANCELLED_UNVERIFIED.value,
            "cancel-time",
        )
    finally:
        conn.close()


def test_exact_idempotent_claim_is_rejected_after_cancel_requested(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "claim-after-cancel" / "analyst.db")
    _insert_run(db_path, "run")
    identity = ProcessIdentity(101, 1, _BOOT_ID)
    token = "a" * 64
    fence = claim_worker(
        "run", identity, owner_token=token,
        heartbeat_monotonic_ns=1, path=db_path,
    )
    assert fence is not None
    assert request_cancel("run", now_utc="cancel-time", path=db_path) == fence

    with pytest.raises(LeaseError, match="no longer permits an idempotent claim"):
        claim_worker(
            "run", identity, owner_token=token,
            heartbeat_monotonic_ns=1, path=db_path,
        )
    assert _run_state(db_path, "run")[0] == RunState.CANCEL_REQUESTED.value
    assert current_lease(path=db_path) == fence


def test_cancel_for_nonowner_rolls_back_intent(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "cancel-other" / "analyst.db")
    _insert_run(db_path, "owner")
    _insert_run(db_path, "other", state=RunState.RUNNING)
    fence = claim_worker(
        "owner", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=1, path=db_path,
    )
    assert fence is not None

    with pytest.raises(LeaseError, match="does not own"):
        request_cancel("other", now_utc="cancel-time", path=db_path)
    assert _run_state(db_path, "other") == (RunState.RUNNING.value, 0)
    assert current_lease(path=db_path) == fence


def test_dead_cancelled_worker_reconciles_to_resumable_state(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "cancel-dead" / "analyst.db")
    _insert_run(db_path, "run")
    fence = claim_worker(
        "run", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=1, path=db_path,
    )
    assert fence is not None
    request_cancel("run", now_utc="cancel-time", path=db_path)

    assert reconcile_lease(
        path=db_path, now_monotonic_ns=1, now_utc="reconcile-time",
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_CANCELLED
    assert current_lease(path=db_path) is None
    assert _run_state(db_path, "run")[0] == RunState.CANCELLED_PENDING_RESUME.value


def test_signal_cancel_requires_exact_identity_and_uses_pidfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = ProcessIdentity(101, 202, _BOOT_ID)
    fence = LeaseFence(1, "run", "a" * 64, identity, 100)
    sent: list[tuple[int, int]] = []

    def fake_pidfd_open(pid: int, flags: int) -> int:
        assert (pid, flags) == (101, 0)
        return os.open("/dev/null", os.O_RDONLY)

    def fake_send(pidfd: int, sig: int) -> None:
        os.fstat(pidfd)
        sent.append((pidfd, sig))

    monkeypatch.setattr(os, "pidfd_open", fake_pidfd_open)
    monkeypatch.setattr(signal, "pidfd_send_signal", fake_send, raising=False)

    assert signal_cancel(fence, identity_reader=lambda _pid: identity) is True
    assert len(sent) == 1
    assert sent[0][1] == signal.SIGTERM
    sent.clear()
    mismatch = ProcessIdentity(identity.pid, identity.start_ticks + 1, identity.boot_id)
    assert signal_cancel(fence, identity_reader=lambda _pid: mismatch) is False
    assert sent == []
    assert signal_cancel(
        fence,
        identity_reader=lambda _pid: (_ for _ in ()).throw(
            ProcessIdentityUnavailable("synthetic unreadable")
        ),
    ) is False
    assert sent == []


def test_reconcile_compare_and_set_cannot_clear_successor_lease(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "reconcile-race" / "analyst.db")
    _insert_run(db_path, "old")
    _insert_run(db_path, "successor")
    old = claim_worker(
        "old", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=100, path=db_path,
    )
    assert old is not None
    successor_box: list[LeaseFence] = []

    def replace_before_recovery(_pid: int) -> None:
        release_worker(old, path=db_path)
        successor = claim_worker(
            "successor", ProcessIdentity(202, 2, _BOOT_ID),
            owner_token="b" * 64, heartbeat_monotonic_ns=200, path=db_path,
        )
        assert successor is not None
        successor_box.append(successor)
        return None

    assert reconcile_lease(
        path=db_path, now_monotonic_ns=100,
        identity_reader=replace_before_recovery,
    ) is ReconcileResult.RACE_LOST
    assert current_lease(path=db_path) == successor_box[0]
    assert _run_state(db_path, "successor")[0] == RunState.RUNNING.value


def test_crash_during_finalization_becomes_resumable_and_clears_token(
    tmp_path: Path,
) -> None:
    db_path = initialize_database(tmp_path / "finalize-crash" / "analyst.db")
    _insert_run(db_path, "run")
    fence = claim_worker(
        "run", ProcessIdentity(101, 1, _BOOT_ID),
        owner_token="a" * 64, heartbeat_monotonic_ns=100, path=db_path,
    )
    assert fence is not None
    run_immediate(
        lambda conn: conn.execute(
            "UPDATE analyst_runs SET state='finalizing',finalization_token=? "
            "WHERE run_id='run'",
            ("9" * 64,),
        ),
        path=db_path,
    )

    assert reconcile_lease(
        path=db_path, now_monotonic_ns=100,
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    conn = open_connection(db_path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,finalization_token FROM analyst_runs WHERE run_id='run'"
        ).fetchone()) == (RunState.INTERRUPTED.value, None)
    finally:
        conn.close()
