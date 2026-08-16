"""Hostile C9B tests for durable contacts, resource pause, and v1 migration."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import queue
import sqlite3

import pytest

from experimental.analyst.contact_contract import (
    MAX_CHAT_CONTACTS_PER_CHUNK,
    MAX_CONTROL_CONTACTS_PER_RUN,
    PS_REQUEST_SHA256,
    TAGS_REQUEST_SHA256,
    VERSION_REQUEST_SHA256,
    ContactCharge,
    ContactContractError,
    ContactFinish,
    ContactKind,
    ContactStatus,
    ScheduleSnapshot,
    ScheduleState,
    resets_resource_streak,
    semantic_attempt_state,
)
from experimental.analyst.ollama_contract import (
    OLLAMA_PS_URL,
    OLLAMA_TAGS_URL,
    OLLAMA_VERSION_URL,
)
from experimental.analyst.phase2_contract import HEALTH_REQUEST_SHA256
from experimental.analyst.state import AttemptState
from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    AnalystSchemaError,
    validate_schema,
    validate_schema_v1,
)
from experimental.analyst.checkpoint import (
    CheckpointError,
    ChunkSpec,
    advance_file_stage,
    begin_finalization,
    checkpoint_detector,
    claim_next_file,
    finish_attempt_failure,
    finish_valid_attempt,
    precharge_attempt,
    store_chunks,
)
from experimental.analyst.inventory import InventoryFile, InventoryResult
from experimental.analyst.lease import (
    LeaseError,
    LeaseFence,
    acknowledge_cancel,
    claim_worker,
    heartbeat as persist_heartbeat,
    release_worker,
    request_cancel,
)
from experimental.analyst.models import Assessment, FileStage, WorksheetResult
from experimental.analyst.ollama_state import (
    OllamaStateError,
    ResourceWaitCancelled,
    authorize_resource_resume,
    finish_contact,
    get_schedule,
    precharge_chat_contact,
    precharge_control_contact,
    reconcile_dispatching_contacts,
    remaining_resource_wait,
    wait_for_resource_retry,
    wait_until_resource_resume_authorized,
    wait_until_resource_retry_due,
)
from experimental.analyst import ollama_state
from experimental.analyst.process_identity import ProcessIdentity
from experimental.analyst.store import (
    RunSpec,
    abandon_run,
    create_run,
    initialize_database,
    open_connection,
    run_immediate,
)


def _sha(character: str) -> str:
    return character * 64


_V2_TABLES = {"analyst_ollama_contacts", "analyst_ollama_schedule"}
_V2_INDEXES = {
    "idx_analyst_contacts_run",
    "idx_analyst_contacts_chunk",
    "ux_analyst_contacts_one_dispatching",
    "ux_analyst_contacts_semantic_slot",
    "idx_analyst_schedule_state",
}
_NOW = "2026-08-16T12:00:00Z"
_BOOT_ID = "00000000-0000-0000-0000-000000000001"


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
        model_digest=_sha("b"),
        worksheet_version="v2",
        prompt_sha256=_sha("c"),
        response_schema_sha256=_sha("d"),
        detector_rules_version="rules-v1",
        detector_rules_sha256=_sha("e"),
        parser_bundle={"bundle": "public-test"},
        chunk_chars=8000,
        overlap_chars=256,
        num_ctx=8192,
        num_predict=1024,
        isolation_mode="strict",
        reduced_isolation_ack=False,
    )


def _inventory() -> InventoryResult:
    return InventoryResult(
        root_device=1,
        root_inode=2,
        root_mount_id=3,
        files=(
            InventoryFile(
                "public.txt", 20, 10, 20, 1, 100, 0o100600, _sha("1"),
            ),
        ),
        exclusions=(),
    )


def _setup_runtime(
    tmp_path: Path, *, run_id: str = "run", with_chunk: bool = True,
) -> tuple[Path, LeaseFence, int | None]:
    path = initialize_database(tmp_path / run_id / "analyst.db")
    create_run(_run_spec(run_id), _inventory(), now_utc=_NOW, path=path)
    fence = claim_worker(
        run_id,
        ProcessIdentity(101, 202, _BOOT_ID),
        owner_token=_sha("a"),
        heartbeat_monotonic_ns=100,
        now_utc=_NOW,
        path=path,
    )
    assert fence is not None
    if not with_chunk:
        return path, fence, None
    claim = claim_next_file(fence, now_utc=_NOW, path=path)
    assert claim is not None
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="text",
        now_utc=_NOW,
        path=path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        encoding="utf-8",
        parser_identity={"parser": "public-test"},
        extraction_meta={"text_bytes": 20, "text_chars": 20},
        now_utc=_NOW,
        path=path,
    )
    checkpoint_detector(
        fence,
        claim.file_id,
        (),
        selected_for_model=True,
        now_utc=_NOW,
        path=path,
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.SELECTED_FOR_MODEL,
        now_utc=_NOW,
        path=path,
    )
    store_chunks(
        fence,
        claim.file_id,
        (ChunkSpec(0, 0, 20, _sha("4")),),
        now_utc=_NOW,
        path=path,
    )
    conn = open_connection(path, read_only=True)
    try:
        chunk_id = int(conn.execute(
            "SELECT chunk_id FROM analyst_chunks WHERE file_id=?", (claim.file_id,),
        ).fetchone()[0])
    finally:
        conn.close()
    return path, fence, chunk_id


def _timestamp(seconds: int) -> str:
    value = datetime(2026, 8, 16, 12, tzinfo=timezone.utc) + timedelta(
        seconds=seconds,
    )
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _race_precharge_control(
    path: Path,
    fence: LeaseFence,
    request_sha: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    start.wait()
    try:
        charge = precharge_control_contact(
            fence,
            ContactKind.CANCELLATION_HEALTH,
            request_sha,
            now_utc=_NOW,
            path=path,
        )
        results.put(("ok", charge.contact_id))
    except BaseException as exc:  # pragma: no cover - reported in parent process
        results.put((type(exc).__name__, str(exc)))


def _downgrade_empty_database_to_exact_v1(path: Path) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for name in sorted(_V2_INDEXES):
            conn.execute(f"DROP INDEX {name}")
        for name in ("analyst_ollama_contacts", "analyst_ollama_schedule"):
            conn.execute(f"DROP TABLE {name}")
        conn.execute(f"PRAGMA user_version={PREVIOUS_SCHEMA_VERSION}")
        conn.execute("COMMIT")
        validate_schema_v1(conn)
    finally:
        conn.close()


def _insert_v1_run(conn: sqlite3.Connection, run_id: str = "run") -> None:
    conn.execute(
        "INSERT INTO analyst_runs("
        "run_id,state,created_at_utc,updated_at_utc,mode,source_mode,"
        "source_root,output_root,source_identity_json,source_identity_sha256,"
        "report_label,model_tag,model_digest,worksheet_version,prompt_sha256,"
        "response_schema_sha256,detector_rules_version,detector_rules_sha256,"
        "parser_bundle_json,parser_bundle_sha256,chunk_chars,overlap_chars,"
        "num_ctx,num_predict,isolation_mode,reduced_isolation_ack) "
        "VALUES(?, 'ready', ?, ?, 'fast', 'unknown', ?, ?, '{}', ?, ?, ?, ?, ?,"
        " ?, ?, ?, ?, '{}', ?, 8000, 256, 8192, 1024, 'strict', 0)",
        (
            run_id, "2026-08-16T12:00:00Z", "2026-08-16T12:00:00Z",
            "/public/source", "/public/output", _sha("a"), "Public run",
            "qwen3.6:27b", _sha("b"), "v2", _sha("c"), _sha("d"),
            "rules-v1", _sha("e"), _sha("f"),
        ),
    )


def _initialize_in_process(
    path: Path,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    start.wait()
    try:
        initialize_database(path)
        conn = open_connection(path, read_only=True)
        try:
            identity = (
                int(conn.execute("PRAGMA application_id").fetchone()[0]),
                int(conn.execute("PRAGMA user_version").fetchone()[0]),
            )
        finally:
            conn.close()
        results.put(("ok", identity))
    except BaseException as exc:  # pragma: no cover - reported in parent process
        results.put((type(exc).__name__, str(exc)))


def _crash_during_v1_migration(path: Path, boundary: int) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=EXTRA")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    statements = (
        *db_schema._V2_ADDITIONAL_TABLE_DDL,
        *db_schema._V2_ADDITIONAL_INDEX_DDL,
    )
    for statement in statements[:boundary]:
        conn.execute(statement)
    if boundary > len(statements):
        conn.execute("PRAGMA user_version=2")
    os._exit(73)


def _run_crash(target: object, path: Path, *args: object) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=target, args=(path, *args))
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("crash subprocess did not exit")
    assert process.exitcode == 73


def _control_hash(kind: ContactKind, url: str) -> str:
    body = json.dumps(
        {
            "accept": "application/json",
            "accept_encoding": "identity",
            "kind": kind.value,
            "method": "GET",
            "url": url,
            "version": 1,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(body).hexdigest()


def _schedule(
    state: ScheduleState = ScheduleState.AVAILABLE,
    failures: int = 0,
    delay: int = 0,
    not_before: str | None = None,
    resume: str | None = None,
    revision: int = 0,
) -> ScheduleSnapshot:
    return ScheduleSnapshot(state, failures, delay, not_before, resume, revision)


def _charge(
    *,
    kind: ContactKind = ContactKind.CHAT,
    chunk_id: int | None = 1,
    attempt_no: int | None = 1,
) -> ContactCharge:
    return ContactCharge(
        _sha("1"), "run", 1, kind, chunk_id, attempt_no, _sha("2"), 1, 0,
    )


def test_contact_schedule_vocabularies_and_caps_are_exact() -> None:
    assert {item.value for item in ContactKind} == {
        "version", "tags", "ps", "chat", "cancellation_health",
    }
    assert {item.value for item in ContactStatus} == {
        "dispatching", "success", "model_invalid", "cancelled_unverified",
        "request_timeout", "resource_busy", "transport_unavailable",
        "protocol_violation", "response_limit", "identity_mismatch",
        "orphaned_unknown",
    }
    assert {item.value for item in ScheduleState} == {
        "available", "backoff", "paused_resource",
    }
    assert (MAX_CONTROL_CONTACTS_PER_RUN, MAX_CHAT_CONTACTS_PER_CHUNK) == (64, 16)


def test_control_request_hashes_bind_exact_intents_independently() -> None:
    assert VERSION_REQUEST_SHA256 == _control_hash(
        ContactKind.VERSION, OLLAMA_VERSION_URL,
    )
    assert TAGS_REQUEST_SHA256 == _control_hash(ContactKind.TAGS, OLLAMA_TAGS_URL)
    assert PS_REQUEST_SHA256 == _control_hash(ContactKind.PS, OLLAMA_PS_URL)
    assert len({VERSION_REQUEST_SHA256, TAGS_REQUEST_SHA256, PS_REQUEST_SHA256}) == 3


@pytest.mark.parametrize(
    "snapshot",
    [
        _schedule(),
        _schedule(ScheduleState.BACKOFF, 1, 15, "2026-08-16T12:00:15Z"),
        _schedule(ScheduleState.BACKOFF, 2, 30, "2026-08-16T12:00:30Z"),
        _schedule(ScheduleState.BACKOFF, 3, 60, "2026-08-16T12:01:00Z"),
        _schedule(ScheduleState.BACKOFF, 4, 120, "2026-08-16T12:02:00Z"),
        _schedule(ScheduleState.BACKOFF, 5, 240, "2026-08-16T12:04:00Z"),
        _schedule(
            ScheduleState.PAUSED_RESOURCE,
            6,
            300,
            "2026-08-16T12:05:00Z",
            None,
            6,
        ),
        _schedule(
            ScheduleState.PAUSED_RESOURCE,
            6,
            300,
            "2026-08-16T12:05:00Z",
            "2026-08-16T12:05:01Z",
            7,
        ),
    ],
)
def test_schedule_snapshot_accepts_only_frozen_state_shapes(
    snapshot: ScheduleSnapshot,
) -> None:
    assert isinstance(snapshot.state, ScheduleState)


@pytest.mark.parametrize(
    "args",
    [
        ("available", 0, 0, None, None, 0),
        (ScheduleState.AVAILABLE, 1, 0, None, None, 0),
        (ScheduleState.AVAILABLE, 0, 15, None, None, 0),
        (ScheduleState.AVAILABLE, 0, 0, "future", None, 0),
        (ScheduleState.BACKOFF, 0, 0, "future", None, 0),
        (ScheduleState.BACKOFF, 1, 30, "future", None, 0),
        (ScheduleState.BACKOFF, 1, 15, None, None, 0),
        (ScheduleState.BACKOFF, 1, 15, "future", "resume", 0),
        (ScheduleState.PAUSED_RESOURCE, 5, 300, "future", None, 0),
        (ScheduleState.PAUSED_RESOURCE, 6, 240, "future", None, 0),
        (ScheduleState.PAUSED_RESOURCE, 6, 300, None, None, 0),
        (ScheduleState.PAUSED_RESOURCE, 6, 300, "future", None, True),
    ],
)
def test_forged_schedule_snapshot_is_rejected(args: tuple[object, ...]) -> None:
    with pytest.raises(ContactContractError):
        ScheduleSnapshot(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "chunk_id", "attempt_no"),
    [
        (ContactKind.CHAT, 1, 1),
        (ContactKind.CHAT, 1, 2),
        (ContactKind.VERSION, None, None),
        (ContactKind.TAGS, None, None),
        (ContactKind.PS, None, None),
        (ContactKind.CANCELLATION_HEALTH, None, None),
    ],
)
def test_contact_charge_ownership_matches_kind_exactly(
    kind: ContactKind, chunk_id: int | None, attempt_no: int | None,
) -> None:
    charge = _charge(kind=kind, chunk_id=chunk_id, attempt_no=attempt_no)
    assert (charge.kind, charge.chunk_id, charge.semantic_attempt_no) == (
        kind, chunk_id, attempt_no,
    )


@pytest.mark.parametrize(
    ("kind", "chunk_id", "attempt_no"),
    [
        (ContactKind.CHAT, None, 1),
        (ContactKind.CHAT, 1, None),
        (ContactKind.CHAT, 1, 0),
        (ContactKind.CHAT, 1, 3),
        (ContactKind.VERSION, 1, None),
        (ContactKind.TAGS, None, 1),
        (ContactKind.CANCELLATION_HEALTH, 1, 1),
    ],
)
def test_contact_charge_rejects_wrong_semantic_ownership(
    kind: ContactKind, chunk_id: int | None, attempt_no: int | None,
) -> None:
    with pytest.raises(ContactContractError, match="ownership"):
        _charge(kind=kind, chunk_id=chunk_id, attempt_no=attempt_no)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_id", "A" * 64),
        ("run_id", ""),
        ("contact_no", True),
        ("kind", "chat"),
        ("request_sha256", "x" * 64),
        ("lease_generation", 0),
        ("resource_failures_before", 7),
    ],
)
def test_contact_charge_revalidates_forged_exact_types(
    field: str, value: object,
) -> None:
    values: dict[str, object] = {
        "contact_id": _sha("1"),
        "run_id": "run",
        "contact_no": 1,
        "kind": ContactKind.CHAT,
        "chunk_id": 1,
        "semantic_attempt_no": 1,
        "request_sha256": _sha("2"),
        "lease_generation": 1,
        "resource_failures_before": 0,
    }
    values[field] = value
    with pytest.raises(ContactContractError):
        ContactCharge(**values)  # type: ignore[arg-type]


def test_semantic_attempt_mapping_is_closed_and_resource_never_consumes() -> None:
    assert {
        status: semantic_attempt_state(status)
        for status in set(ContactStatus) - {ContactStatus.DISPATCHING}
    } == {
        ContactStatus.SUCCESS: AttemptState.DISPATCHING,
        ContactStatus.MODEL_INVALID: AttemptState.SCHEMA_INVALID,
        ContactStatus.CANCELLED_UNVERIFIED: AttemptState.CANCELLED_UNVERIFIED,
        ContactStatus.REQUEST_TIMEOUT: AttemptState.MODEL_TIMEOUT,
        ContactStatus.RESOURCE_BUSY: None,
        ContactStatus.TRANSPORT_UNAVAILABLE: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.PROTOCOL_VIOLATION: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.RESPONSE_LIMIT: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.IDENTITY_MISMATCH: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.ORPHANED_UNKNOWN: AttemptState.ORPHANED_UNKNOWN,
    }
    with pytest.raises(ContactContractError):
        semantic_attempt_state(ContactStatus.DISPATCHING)
    with pytest.raises(ContactContractError):
        semantic_attempt_state("success")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kind",
    [ContactKind.CHAT, ContactKind.CANCELLATION_HEALTH],
)
def test_answered_generation_outcomes_reset_resource_streak(kind: ContactKind) -> None:
    reset = {
        ContactStatus.SUCCESS,
        ContactStatus.MODEL_INVALID,
    }
    for status in set(ContactStatus) - {ContactStatus.DISPATCHING}:
        assert resets_resource_streak(kind, status) is (status in reset)


@pytest.mark.parametrize(
    "kind",
    [ContactKind.VERSION, ContactKind.TAGS, ContactKind.PS],
)
def test_cheap_controls_never_reset_resource_streak(kind: ContactKind) -> None:
    for status in set(ContactStatus) - {ContactStatus.DISPATCHING}:
        assert resets_resource_streak(kind, status) is False


def test_contact_finish_requires_attempt_for_nonresource_chat_only() -> None:
    available = _schedule()
    resource = ContactFinish(
        _sha("1"), ContactKind.CHAT, ContactStatus.RESOURCE_BUSY,
        1, None,
        _schedule(ScheduleState.BACKOFF, 1, 15, "2026-08-16T12:00:15Z", revision=1),
        False,
    )
    semantic = ContactFinish(
        _sha("2"), ContactKind.CHAT, ContactStatus.SUCCESS,
        1, _sha("3"), available, False,
    )
    control = ContactFinish(
        _sha("4"), ContactKind.VERSION, ContactStatus.SUCCESS,
        None, None, available, False,
    )
    assert resource.attempt_id is None
    assert semantic.attempt_id == _sha("3")
    assert control.semantic_attempt_no is None


def test_only_sixth_resource_failure_releases_lease() -> None:
    paused = ContactFinish(
        _sha("1"), ContactKind.CHAT, ContactStatus.RESOURCE_BUSY,
        1, None,
        _schedule(
            ScheduleState.PAUSED_RESOURCE,
            6,
            300,
            "2026-08-16T12:05:00Z",
            revision=6,
        ),
        True,
    )
    assert paused.lease_released is True

    with pytest.raises(ContactContractError, match="lease"):
        ContactFinish(
            _sha("2"), ContactKind.CHAT, ContactStatus.RESOURCE_BUSY,
            1, None, _schedule(), True,
        )


def test_fresh_v2_schema_has_only_frozen_additions_and_schedule_constraints(
    tmp_path: Path,
) -> None:
    path = initialize_database(tmp_path / "fresh-v2" / "analyst.db")
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        validate_schema(conn)
        objects = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT type,name FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        assert {("table", name) for name in _V2_TABLES} <= objects
        assert {("index", name) for name in _V2_INDEXES} <= objects
        for table in _V2_TABLES:
            row = conn.execute(
                "SELECT strict FROM pragma_table_list WHERE name=?", (table,),
            ).fetchone()
            assert row is not None and int(row[0]) == 1
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_ollama_contacts)")
        }
        assert columns == {
            "contact_id", "run_id", "contact_no", "kind", "chunk_id",
            "semantic_attempt_no", "request_sha256", "lease_generation",
            "state", "charged_at_utc", "finished_at_utc", "attempt_id",
            "resource_failures_before", "resource_failures_after",
        }
        schedule_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_ollama_schedule)")
        }
        assert schedule_columns == {
            "run_id", "state", "consecutive_failures", "delay_seconds",
            "not_before_utc", "resume_authorized_at_utc", "revision",
            "updated_at_utc",
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_exact_empty_v1_migrates_additively_and_reopen_is_idempotent(
    tmp_path: Path,
) -> None:
    path = initialize_database(tmp_path / "empty-v1" / "analyst.db")
    _downgrade_empty_database_to_exact_v1(path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        before = tuple(conn.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ))
    finally:
        conn.close()

    assert initialize_database(path) == path
    conn = open_connection(path, read_only=True)
    try:
        validate_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_schedule"
        ).fetchone()[0] == 0
        after_v1 = tuple(
            tuple(row) for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
            if str(row[1]) not in _V2_TABLES | _V2_INDEXES
        )
        assert after_v1 == before
    finally:
        conn.close()

    first_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    assert initialize_database(path) == path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first_hash


@pytest.mark.parametrize("mutation", ["domain_row", "lease_generation"])
def test_populated_or_active_v1_is_refused_without_mutation(
    tmp_path: Path, mutation: str,
) -> None:
    path = initialize_database(tmp_path / mutation / "analyst.db")
    _downgrade_empty_database_to_exact_v1(path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        if mutation == "domain_row":
            _insert_v1_run(conn)
            conn.execute(
                "INSERT INTO analyst_inventory_exclusions("
                "run_id,ordinal,relative_path,reason) VALUES(?,?,?,?)",
                ("run", 0, "public.txt", "symlink"),
            )
        else:
            conn.execute(
                "UPDATE analyst_gpu_lease SET generation=1 WHERE slot=1"
            )
    finally:
        conn.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(AnalystSchemaError, match="populated|generation-zero"):
        initialize_database(path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not Path(os.fspath(path) + "-journal").exists()


@pytest.mark.parametrize(
    "boundary",
    range(
        1,
        len(db_schema._V2_ADDITIONAL_TABLE_DDL)
        + len(db_schema._V2_ADDITIONAL_INDEX_DDL)
        + 2,
    ),
    ids=lambda boundary: f"boundary-{boundary}",
)
def test_crash_at_each_v1_migration_boundary_rolls_back_then_migrates_cleanly(
    tmp_path: Path, boundary: int,
) -> None:
    path = initialize_database(
        tmp_path / f"migration-crash-{boundary}" / "analyst.db"
    )
    _downgrade_empty_database_to_exact_v1(path)

    _run_crash(_crash_during_v1_migration, path, boundary)

    journal = Path(os.fspath(path) + "-journal")
    assert journal.is_file()
    assert initialize_database(path) == path
    assert not journal.exists()
    conn = open_connection(path, read_only=True)
    try:
        validate_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_two_processes_racing_exact_v1_migration_converge_on_one_v2(
    tmp_path: Path,
) -> None:
    path = initialize_database(tmp_path / "migration-race" / "analyst.db")
    _downgrade_empty_database_to_exact_v1(path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_initialize_in_process, args=(path, start, results))
        for _ in range(2)
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

    assert observed == [("ok", (APPLICATION_ID, 2))] * 2
    conn = open_connection(path, read_only=True)
    try:
        validate_schema(conn)
        assert conn.execute(
            "SELECT count(*) FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_create_run_seeds_exact_available_schedule_and_reopens(
    tmp_path: Path,
) -> None:
    path, _fence, _ = _setup_runtime(tmp_path, with_chunk=False)

    assert get_schedule("run", path=path) == ScheduleSnapshot(
        ScheduleState.AVAILABLE, 0, 0, None, None, 0,
    )
    assert get_schedule("run", path=path) == get_schedule("run", path=path)


@pytest.mark.parametrize(
    ("kind", "request_sha"),
    [
        (ContactKind.VERSION, VERSION_REQUEST_SHA256),
        (ContactKind.TAGS, TAGS_REQUEST_SHA256),
        (ContactKind.PS, PS_REQUEST_SHA256),
        (ContactKind.CANCELLATION_HEALTH, HEALTH_REQUEST_SHA256),
    ],
)
def test_control_contact_precharge_and_finish_are_exact_and_attempt_free(
    tmp_path: Path, kind: ContactKind, request_sha: str,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)

    charge = precharge_control_contact(
        fence, kind, request_sha, now_utc=_NOW, path=path,
    )
    assert (
        charge.contact_no,
        charge.kind,
        charge.chunk_id,
        charge.semantic_attempt_no,
        charge.request_sha256,
    ) == (1, kind, None, None, request_sha)
    finish = finish_contact(
        fence, charge.contact_id, ContactStatus.SUCCESS,
        now_utc=_NOW, path=path,
    )
    assert finish.attempt_id is None
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts"
        ).fetchone()[0] == 0
        row = conn.execute(
            "SELECT state,finished_at_utc,resource_failures_before,"
            "resource_failures_after FROM analyst_ollama_contacts"
        ).fetchone()
        assert tuple(row) == ("success", _NOW, 0, 0)
    finally:
        conn.close()


def test_control_precharge_revalidates_intent_and_stale_fence_without_mutation(
    tmp_path: Path,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    stale = LeaseFence(
        fence.generation,
        fence.run_id,
        fence.owner_token,
        fence.process,
        fence.heartbeat_monotonic_ns + 1,
    )
    with pytest.raises(ValueError, match="frozen intent"):
        precharge_control_contact(
            fence, ContactKind.VERSION, _sha("9"), path=path,
        )
    with pytest.raises(OllamaStateError, match="fence"):
        precharge_control_contact(
            stale, ContactKind.CANCELLATION_HEALTH, HEALTH_REQUEST_SHA256, path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        (ContactStatus.SUCCESS, AttemptState.DISPATCHING),
        (ContactStatus.MODEL_INVALID, AttemptState.SCHEMA_INVALID),
        (ContactStatus.REQUEST_TIMEOUT, AttemptState.MODEL_TIMEOUT),
        (ContactStatus.TRANSPORT_UNAVAILABLE, AttemptState.MODEL_TRANSPORT_ERROR),
        (ContactStatus.PROTOCOL_VIOLATION, AttemptState.MODEL_TRANSPORT_ERROR),
        (ContactStatus.RESPONSE_LIMIT, AttemptState.MODEL_TRANSPORT_ERROR),
        (ContactStatus.IDENTITY_MISMATCH, AttemptState.MODEL_TRANSPORT_ERROR),
        (ContactStatus.CANCELLED_UNVERIFIED, AttemptState.CANCELLED_UNVERIFIED),
    ],
)
def test_nonresource_chat_outcomes_atomically_consume_one_semantic_attempt(
    tmp_path: Path, status: ContactStatus, expected_state: AttemptState,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )

    finish = finish_contact(
        fence, charge.contact_id, status, now_utc=_NOW, path=path,
    )

    assert (finish.semantic_attempt_no, finish.attempt_id) == (
        1, hashlib.sha256(f"{chunk_id}\0{1}\0{_sha('5')}".encode("ascii")).hexdigest(),
    )
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT attempt_no,state,request_sha256,finished_at_utc,failure_code "
            "FROM analyst_model_attempts"
        ).fetchone()
        assert tuple(row[:3]) == (1, expected_state.value, _sha("5"))
        if expected_state is AttemptState.DISPATCHING:
            assert tuple(row[3:]) == (None, None)
        else:
            assert tuple(row[3:]) == (_NOW, expected_state.value)
        linked = conn.execute(
            "SELECT state,attempt_id FROM analyst_ollama_contacts"
        ).fetchone()
        assert tuple(linked) == (status.value, finish.attempt_id)
    finally:
        conn.close()


def test_six_resource_contacts_pause_without_consuming_semantic_attempt(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    expected_delays = (15, 30, 60, 120, 240, 300)

    for failure, delay in enumerate(expected_delays, start=1):
        timestamp = _timestamp(elapsed)
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=timestamp, path=path,
        )
        assert charge.semantic_attempt_no == 1
        result = finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=timestamp,
            path=path,
        )
        assert result.attempt_id is None
        assert result.schedule.consecutive_failures == failure
        assert result.schedule.delay_seconds == delay
        assert result.lease_released is (failure == 6)
        elapsed += delay

    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts "
            "WHERE state='resource_busy'"
        ).fetchone()[0] == 6
        assert tuple(conn.execute(
            "SELECT state,consecutive_failures,delay_seconds,not_before_utc,"
            "resume_authorized_at_utc FROM analyst_ollama_schedule"
        ).fetchone()) == (
            "paused_resource", 6, 300, _timestamp(elapsed), None,
        )
        assert conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id='run'"
        ).fetchone()[0] == "interrupted"
        assert tuple(conn.execute(
            "SELECT work_state,active_generation FROM analyst_files"
        ).fetchone()) == ("pending", None)
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_resource_wait_is_clamped_and_resume_requires_elapsed_cooldown(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240, 300):
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )
        elapsed += delay
    paused = get_schedule("run", path=path)
    assert remaining_resource_wait(paused, now_utc=_timestamp(elapsed - 1)) == 1
    assert remaining_resource_wait(paused, now_utc=_timestamp(elapsed - 1000)) == 300
    assert remaining_resource_wait(paused, now_utc=_timestamp(elapsed + 1)) == 0
    with pytest.raises(OllamaStateError, match="cooldown"):
        authorize_resource_resume(
            "run", now_utc=_timestamp(elapsed - 1), path=path,
        )
    authorized = authorize_resource_resume(
        "run", now_utc=_timestamp(elapsed), path=path,
    )
    assert authorized.resume_authorized_at_utc == _timestamp(elapsed)
    assert authorized.consecutive_failures == 6
    assert authorize_resource_resume(
        "run", now_utc=_timestamp(elapsed + 1), path=path,
    ) == authorized


@pytest.mark.parametrize(
    ("kind", "cancelled", "expected_status", "attempt_state"),
    [
        (
            ContactKind.CHAT,
            False,
            ContactStatus.ORPHANED_UNKNOWN,
            AttemptState.ORPHANED_UNKNOWN,
        ),
        (
            ContactKind.CHAT,
            True,
            ContactStatus.CANCELLED_UNVERIFIED,
            AttemptState.CANCELLED_UNVERIFIED,
        ),
        (ContactKind.VERSION, False, ContactStatus.ORPHANED_UNKNOWN, None),
    ],
)
def test_reconciliation_closes_contact_and_only_chat_consumes_attempt(
    tmp_path: Path,
    kind: ContactKind,
    cancelled: bool,
    expected_status: ContactStatus,
    attempt_state: AttemptState | None,
) -> None:
    path, fence, chunk_id = _setup_runtime(
        tmp_path, with_chunk=kind is ContactKind.CHAT,
    )
    if kind is ContactKind.CHAT:
        assert chunk_id is not None
        precharge_chat_contact(fence, chunk_id, _sha("5"), path=path)
    else:
        precharge_control_contact(
            fence, kind, VERSION_REQUEST_SHA256, path=path,
        )

    run_immediate(
        lambda conn: reconcile_dispatching_contacts(
            conn, "run", "reconcile", cancelled=cancelled,
        ),
        path=path,
    )

    conn = open_connection(path, read_only=True)
    try:
        contact = conn.execute(
            "SELECT state,attempt_id FROM analyst_ollama_contacts"
        ).fetchone()
        assert contact[0] == expected_status.value
        attempts = conn.execute(
            "SELECT state FROM analyst_model_attempts"
        ).fetchall()
        if attempt_state is None:
            assert attempts == []
            assert contact[1] is None
        else:
            assert [row[0] for row in attempts] == [attempt_state.value]
            assert contact[1] is not None
    finally:
        conn.close()


def test_global_dispatch_contact_gate_is_atomic_across_processes(
    tmp_path: Path,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_precharge_control,
            args=(path, fence, HEALTH_REQUEST_SHA256, start, results),
        )
        for _index in range(2)
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

    assert sorted(result[0] for result in observed) == ["OllamaStateError", "ok"]
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts "
            "WHERE state='dispatching'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("status", "resets"),
    [
        (ContactStatus.SUCCESS, True),
        (ContactStatus.MODEL_INVALID, True),
        (ContactStatus.REQUEST_TIMEOUT, False),
        (ContactStatus.TRANSPORT_UNAVAILABLE, False),
        (ContactStatus.PROTOCOL_VIOLATION, False),
        (ContactStatus.RESPONSE_LIMIT, False),
        (ContactStatus.IDENTITY_MISMATCH, False),
        (ContactStatus.CANCELLED_UNVERIFIED, False),
    ],
)
def test_only_proven_terminal_generation_resets_persisted_resource_streak(
    tmp_path: Path, status: ContactStatus, resets: bool,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    resource = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_timestamp(0), path=path,
    )
    finish_contact(
        fence,
        resource.contact_id,
        ContactStatus.RESOURCE_BUSY,
        now_utc=_timestamp(0),
        path=path,
    )
    semantic = precharge_chat_contact(
        fence, chunk_id, _sha("6"), now_utc=_timestamp(15), path=path,
    )

    result = finish_contact(
        fence, semantic.contact_id, status, now_utc=_timestamp(15), path=path,
    )

    if resets:
        assert result.schedule == ScheduleSnapshot(
            ScheduleState.AVAILABLE, 0, 0, None, None, 2,
        )
    else:
        assert result.schedule.state is ScheduleState.BACKOFF
        assert result.schedule.consecutive_failures == 1
        assert result.schedule.not_before_utc == _timestamp(15)


def test_paused_resume_claim_then_exactly_two_real_outcomes_and_no_third(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240, 300):
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )
        elapsed += delay
    with pytest.raises(LeaseError, match="retry is not due"):
        claim_worker(
            "run",
            ProcessIdentity(202, 303, _BOOT_ID),
            owner_token=_sha("b"),
            heartbeat_monotonic_ns=200,
            now_utc=_timestamp(elapsed - 1),
            path=path,
        )
    with pytest.raises(LeaseError, match="authorization"):
        claim_worker(
            "run",
            ProcessIdentity(202, 303, _BOOT_ID),
            owner_token=_sha("b"),
            heartbeat_monotonic_ns=200,
            now_utc=_timestamp(elapsed),
            path=path,
        )
    authorize_resource_resume("run", now_utc=_timestamp(elapsed), path=path)
    resumed = claim_worker(
        "run",
        ProcessIdentity(202, 303, _BOOT_ID),
        owner_token=_sha("b"),
        heartbeat_monotonic_ns=200,
        now_utc=_timestamp(elapsed),
        path=path,
    )
    assert resumed is not None and resumed.generation > fence.generation
    claim = claim_next_file(resumed, now_utc=_timestamp(elapsed), path=path)
    assert claim is not None
    first = precharge_chat_contact(
        resumed, chunk_id, _sha("6"), now_utc=_timestamp(elapsed), path=path,
    )
    assert first.semantic_attempt_no == 1
    finish_contact(
        resumed,
        first.contact_id,
        ContactStatus.MODEL_INVALID,
        now_utc=_timestamp(elapsed),
        path=path,
    )
    second = precharge_chat_contact(
        resumed, chunk_id, _sha("7"), now_utc=_timestamp(elapsed), path=path,
    )
    assert second.semantic_attempt_no == 2
    finish_contact(
        resumed,
        second.contact_id,
        ContactStatus.REQUEST_TIMEOUT,
        now_utc=_timestamp(elapsed),
        path=path,
    )
    with pytest.raises(OllamaStateError, match="two-attempt"):
        precharge_chat_contact(
            resumed, chunk_id, _sha("8"), now_utc=_timestamp(elapsed), path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert [tuple(row) for row in conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts ORDER BY attempt_no"
        )] == [(1, "schema_invalid"), (2, "model_timeout")]
        assert conn.execute(
            "SELECT state FROM analyst_chunks WHERE chunk_id=?", (chunk_id,),
        ).fetchone()[0] == "model_timeout"
    finally:
        conn.close()


def test_cancel_resource_paused_run_without_lease_is_immediately_resumable(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240, 300):
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )
        elapsed += delay

    assert request_cancel(
        "run", now_utc=_timestamp(elapsed), path=path,
    ) is None
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,cancel_requested_at_utc FROM analyst_runs"
        ).fetchone()) == ("cancelled_pending_resume", _timestamp(elapsed))
        assert conn.execute(
            "SELECT state FROM analyst_ollama_schedule"
        ).fetchone()[0] == "paused_resource"
    finally:
        conn.close()


def test_control_contact_cap_accepts_64_and_refuses_65_without_mutation(
    tmp_path: Path,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    for ordinal in range(1, MAX_CONTROL_CONTACTS_PER_RUN + 1):
        charge = precharge_control_contact(
            fence,
            ContactKind.CANCELLATION_HEALTH,
            HEALTH_REQUEST_SHA256,
            now_utc=_NOW,
            path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.REQUEST_TIMEOUT,
            now_utc=_NOW,
            path=path,
        )
    with pytest.raises(OllamaStateError, match="control contact evidence"):
        precharge_control_contact(
            fence,
            ContactKind.CANCELLATION_HEALTH,
            HEALTH_REQUEST_SHA256,
            now_utc=_NOW,
            path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts"
        ).fetchone()[0] == 64
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts "
            "WHERE state='dispatching'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_chat_contact_cap_accepts_16_and_refuses_17_without_mutation(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None

    def seed(conn: sqlite3.Connection) -> None:
        conn.executemany(
            "INSERT INTO analyst_ollama_contacts("
            "contact_id,run_id,contact_no,kind,chunk_id,semantic_attempt_no,"
            "request_sha256,lease_generation,state,charged_at_utc,"
            "finished_at_utc,resource_failures_before,resource_failures_after) "
            "VALUES(?,'run',?,'chat',?,1,?,?,'resource_busy',?,?,0,1)",
            (
                (
                    f"{ordinal:064x}",
                    ordinal,
                    chunk_id,
                    f"{ordinal + 100:064x}",
                    fence.generation,
                    _NOW,
                    _NOW,
                )
                for ordinal in range(1, MAX_CHAT_CONTACTS_PER_CHUNK)
            ),
        )

    run_immediate(seed, path=path)
    sixteenth = precharge_chat_contact(
        fence, chunk_id, _sha("f"), now_utc=_NOW, path=path,
    )
    assert sixteenth.contact_no == 16
    finish_contact(
        fence,
        sixteenth.contact_id,
        ContactStatus.MODEL_INVALID,
        now_utc=_NOW,
        path=path,
    )
    with pytest.raises(OllamaStateError, match="chat contact evidence"):
        precharge_chat_contact(
            fence, chunk_id, _sha("e"), now_utc=_NOW, path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()[0] == 16
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_terminal_contact_cannot_be_finished_or_overwritten_twice(
    tmp_path: Path,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    charge = precharge_control_contact(
        fence,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=_NOW,
        path=path,
    )
    finish_contact(
        fence,
        charge.contact_id,
        ContactStatus.REQUEST_TIMEOUT,
        now_utc=_NOW,
        path=path,
    )
    conn = open_connection(path, read_only=True)
    try:
        before = tuple(conn.execute(
            "SELECT * FROM analyst_ollama_contacts WHERE contact_id=?",
            (charge.contact_id,),
        ).fetchone())
    finally:
        conn.close()
    with pytest.raises(OllamaStateError, match="already terminal"):
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.SUCCESS,
            now_utc=_timestamp(1),
            path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT * FROM analyst_ollama_contacts WHERE contact_id=?",
            (charge.contact_id,),
        ).fetchone()) == before
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("contact_no", 0),
        ("contact_no", 1.5),
        ("kind", "unknown"),
        ("semantic_attempt_no", 1),
        ("request_sha256", "secret_account_123"),
        ("lease_generation", 0),
        ("resource_failures_before", 7),
    ],
)
def test_contact_table_rejects_strict_or_contract_invalid_rows(
    tmp_path: Path, column: str, value: object,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    values: dict[str, object] = {
        "contact_id": _sha("1"),
        "run_id": "run",
        "contact_no": 1,
        "kind": "version",
        "chunk_id": None,
        "semantic_attempt_no": None,
        "request_sha256": VERSION_REQUEST_SHA256,
        "lease_generation": fence.generation,
        "state": "dispatching",
        "charged_at_utc": _NOW,
        "finished_at_utc": None,
        "attempt_id": None,
        "resource_failures_before": 0,
        "resource_failures_after": None,
    }
    values[column] = value

    def inject(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_ollama_contacts("
            + ",".join(values)
            + ") VALUES("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )

    with pytest.raises(sqlite3.IntegrityError):
        run_immediate(inject, path=path)
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_acknowledged_cancel_closes_dispatching_chat_as_cancelled_attempt(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )

    assert request_cancel("run", now_utc=_NOW, path=path) == fence
    acknowledge_cancel(fence, now_utc=_timestamp(1), path=path)

    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,attempt_id FROM analyst_ollama_contacts "
            "WHERE contact_id=?", (charge.contact_id,),
        ).fetchone())[0] == "cancelled_unverified"
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts"
        ).fetchone()[0] == "cancelled_unverified"
        assert conn.execute(
            "SELECT state FROM analyst_runs"
        ).fetchone()[0] == "cancelled_pending_resume"
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_sixth_resource_pause_failure_rolls_back_every_partial_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240):
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )
        elapsed += delay
    sixth = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
    )
    monkeypatch.setattr(ollama_state, "_clear_lease", lambda _conn, _fence: 0)

    with pytest.raises(OllamaStateError, match="fence changed"):
        finish_contact(
            fence,
            sixth.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )

    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,resource_failures_after FROM analyst_ollama_contacts "
            "WHERE contact_id=?", (sixth.contact_id,),
        ).fetchone()) == ("dispatching", None)
        assert tuple(conn.execute(
            "SELECT state,consecutive_failures FROM analyst_ollama_schedule"
        ).fetchone()) == ("backoff", 5)
        assert conn.execute(
            "SELECT state FROM analyst_runs"
        ).fetchone()[0] == "running"
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == "run"
    finally:
        conn.close()


def test_resource_wait_uses_bounded_pulses_heartbeats_and_successor_fence() -> None:
    fence = LeaseFence(
        1, "run", _sha("a"), ProcessIdentity(101, 202, _BOOT_ID), 100,
    )
    schedule = ScheduleSnapshot(
        ScheduleState.BACKOFF,
        1,
        15,
        _timestamp(3),
        None,
        1,
    )
    clock = [0.0]
    sleeps: list[float] = []
    beats: list[int] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    def beat(current: LeaseFence) -> LeaseFence:
        beats.append(current.heartbeat_monotonic_ns)
        return LeaseFence(
            current.generation,
            current.run_id,
            current.owner_token,
            current.process,
            current.heartbeat_monotonic_ns + 1,
        )

    result = wait_for_resource_retry(
        fence,
        schedule,
        cancelled=lambda: False,
        heartbeat=beat,
        now_utc=_NOW,
        monotonic=lambda: clock[0],
        sleep=sleep,
        pulse_seconds=2,
    )

    assert sleeps == [2.0, 1.0]
    assert beats == [100, 101]
    assert result.heartbeat_monotonic_ns == 102


def test_resource_wait_cancellation_wins_before_sleep() -> None:
    fence = LeaseFence(
        1, "run", _sha("a"), ProcessIdentity(101, 202, _BOOT_ID), 100,
    )
    schedule = ScheduleSnapshot(
        ScheduleState.BACKOFF, 1, 15, _timestamp(15), None, 1,
    )
    polls = [False, True]
    slept: list[float] = []

    with pytest.raises(ResourceWaitCancelled, match="cancelled"):
        wait_for_resource_retry(
            fence,
            schedule,
            cancelled=lambda: polls.pop(0),
            heartbeat=lambda _value: pytest.fail("cancelled wait heartbeated"),
            now_utc=_NOW,
            monotonic=lambda: 0.0,
            sleep=slept.append,
        )
    assert slept == []


@pytest.mark.parametrize("failure", ["owner", "clock"])
def test_resource_wait_fails_closed_on_fence_loss_or_monotonic_rollback(
    failure: str,
) -> None:
    fence = LeaseFence(
        1, "run", _sha("a"), ProcessIdentity(101, 202, _BOOT_ID), 100,
    )
    schedule = ScheduleSnapshot(
        ScheduleState.BACKOFF, 1, 15, _timestamp(1), None, 1,
    )
    clock = [0.0]

    def beat(value: LeaseFence) -> LeaseFence:
        if failure == "owner":
            return LeaseFence(
                value.generation,
                value.run_id,
                _sha("b"),
                value.process,
                value.heartbeat_monotonic_ns + 1,
            )
        return LeaseFence(
            value.generation,
            value.run_id,
            value.owner_token,
            value.process,
            value.heartbeat_monotonic_ns + 1,
        )

    def sleep(_seconds: float) -> None:
        clock[0] = -1.0 if failure == "clock" else 1.0

    with pytest.raises(OllamaStateError, match="exact lease|backwards"):
        wait_for_resource_retry(
            fence,
            schedule,
            cancelled=lambda: False,
            heartbeat=beat,
            now_utc=_NOW,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )


def test_resource_wait_rejects_noop_heartbeat_before_sleep() -> None:
    fence = LeaseFence(
        1, "run", _sha("a"), ProcessIdentity(101, 202, _BOOT_ID), 100,
    )
    schedule = ScheduleSnapshot(
        ScheduleState.BACKOFF, 1, 15, _timestamp(1), None, 1,
    )
    slept: list[float] = []

    with pytest.raises(OllamaStateError, match="did not advance"):
        wait_for_resource_retry(
            fence,
            schedule,
            cancelled=lambda: False,
            heartbeat=lambda value: value,
            now_utc=_NOW,
            monotonic=lambda: 0.0,
            sleep=slept.append,
        )
    assert slept == []


def test_resource_paused_run_can_be_abandoned_without_resetting_schedule(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240, 300):
        charge = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence,
            charge.contact_id,
            ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed),
            path=path,
        )
        elapsed += delay

    abandon_run("run", now_utc=_timestamp(elapsed), path=path)

    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,completion_code FROM analyst_runs"
        ).fetchone()) == ("abandoned", "abandoned")
        assert tuple(conn.execute(
            "SELECT work_state,terminal_code FROM analyst_files"
        ).fetchone()) == ("terminal", "cancelled_abandoned")
        assert conn.execute(
            "SELECT state FROM analyst_ollama_schedule"
        ).fetchone()[0] == "paused_resource"
    finally:
        conn.close()


def test_finalization_rejects_nonavailable_resource_schedule(
    tmp_path: Path,
) -> None:
    path = initialize_database(tmp_path / "finalize-schedule" / "analyst.db")
    empty_inventory = InventoryResult(1, 2, 3, (), ())
    create_run(_run_spec("run"), empty_inventory, now_utc=_NOW, path=path)
    fence = claim_worker(
        "run",
        ProcessIdentity(101, 202, _BOOT_ID),
        owner_token=_sha("a"),
        heartbeat_monotonic_ns=100,
        now_utc=_NOW,
        path=path,
    )
    assert fence is not None

    def backoff(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE analyst_ollama_schedule SET state='backoff',"
            "consecutive_failures=1,delay_seconds=15,not_before_utc=?,"
            "revision=revision+1,updated_at_utc=? WHERE run_id='run'",
            (_timestamp(15), _NOW),
        )

    run_immediate(backoff, path=path)
    with pytest.raises(CheckpointError, match="incomplete work"):
        begin_finalization(
            fence, _sha("f"), now_utc=_timestamp(15), path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,finalization_token FROM analyst_runs"
        ).fetchone()) == ("running", None)
    finally:
        conn.close()


def test_schema_audit_rejects_contact_chunk_owned_by_another_run(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    create_run(_run_spec("other"), _inventory(), now_utc=_NOW, path=path)

    def forge(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_ollama_contacts("
            "contact_id,run_id,contact_no,kind,chunk_id,semantic_attempt_no,"
            "request_sha256,lease_generation,state,charged_at_utc,finished_at_utc,"
            "resource_failures_before,resource_failures_after) "
            "VALUES(?,'other',1,'chat',?,1,?,?,'resource_busy',?,?,0,1)",
            (_sha("9"), chunk_id, _sha("8"), fence.generation, _NOW, _NOW),
        )

    run_immediate(forge, path=path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        with pytest.raises(AnalystSchemaError, match="another run"):
            validate_schema(conn)
    finally:
        conn.close()


def test_contact_and_schedule_rows_never_retain_private_content_marker(
    tmp_path: Path,
) -> None:
    marker = "PRIVATE_SOURCE_MARKER_DO_NOT_PERSIST"
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence,
        chunk_id,
        hashlib.sha256(marker.encode("ascii")).hexdigest(),
        now_utc=_NOW,
        path=path,
    )
    finish_contact(
        fence,
        charge.contact_id,
        ContactStatus.MODEL_INVALID,
        now_utc=_NOW,
        path=path,
    )

    conn = open_connection(path, read_only=True)
    try:
        contact_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_ollama_contacts)")
        }
        schedule_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_ollama_schedule)")
        }
        forbidden = {
            "url", "prompt", "source_text", "response", "content", "thinking",
            "error", "exception", "detail", "raw",
        }
        assert contact_columns.isdisjoint(forbidden)
        assert schedule_columns.isdisjoint(forbidden)
        values = tuple(conn.execute(
            "SELECT * FROM analyst_ollama_contacts"
        ).fetchone()) + tuple(conn.execute(
            "SELECT * FROM analyst_ollama_schedule"
        ).fetchone())
        assert marker not in "\n".join(
            value for value in values if isinstance(value, str)
        )
    finally:
        conn.close()


def test_success_attempt_must_checkpoint_before_any_later_http_contact(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    success = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finish = finish_contact(
        fence,
        success.contact_id,
        ContactStatus.SUCCESS,
        now_utc=_NOW,
        path=path,
    )
    assert finish.attempt_id is not None

    for dispatch in (
        lambda: precharge_control_contact(
            fence,
            ContactKind.CANCELLATION_HEALTH,
            HEALTH_REQUEST_SHA256,
            now_utc=_timestamp(1),
            path=path,
        ),
        lambda: precharge_chat_contact(
            fence,
            chunk_id,
            _sha("9"),
            now_utc=_timestamp(1),
            path=path,
        ),
    ):
        with pytest.raises(
            OllamaStateError, match="durable checkpoint|live semantic attempt",
        ):
            dispatch()

    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_ollama_contacts"
        ).fetchone()[0] == 1
        assert tuple(conn.execute(
            "SELECT attempt_id,state FROM analyst_model_attempts"
        ).fetchone()) == (finish.attempt_id, "dispatching")
    finally:
        conn.close()


def test_legacy_precharge_rejects_dispatching_contact_without_mutation(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )

    with pytest.raises(CheckpointError, match="charged Ollama contact"):
        precharge_attempt(
            fence, chunk_id, _sha("6"), now_utc=_timestamp(1), path=path,
        )

    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts"
        ).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT contact_id,state,finished_at_utc FROM analyst_ollama_contacts"
        ).fetchone()) == (charge.contact_id, "dispatching", None)
    finally:
        conn.close()


def test_success_linked_attempt_rejects_legacy_failure_then_accepts_valid(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finished = finish_contact(
        fence, charge.contact_id, ContactStatus.SUCCESS,
        now_utc=_NOW, path=path,
    )
    assert finished.attempt_id is not None
    conn = open_connection(path, read_only=True)
    try:
        before = tuple(conn.execute(
            "SELECT * FROM analyst_model_attempts WHERE attempt_id=?",
            (finished.attempt_id,),
        ).fetchone())
    finally:
        conn.close()

    with pytest.raises(CheckpointError, match="valid checkpoint or recovery"):
        finish_attempt_failure(
            fence,
            finished.attempt_id,
            AttemptState.SCHEMA_INVALID,
            now_utc=_timestamp(1),
            path=path,
        )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT * FROM analyst_model_attempts WHERE attempt_id=?",
            (finished.attempt_id,),
        ).fetchone()) == before
    finally:
        conn.close()

    finish_valid_attempt(
        fence,
        finished.attempt_id,
        WorksheetResult(
            "Public note", "Synthetic", Assessment.NO_FINDINGS, (), 0, 0, 0,
        ),
        now_utc=_timestamp(2),
        path=path,
    )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,finished_at_utc FROM analyst_model_attempts"
        ).fetchone()) == ("valid", _timestamp(2))
        validate_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("recovery", "expected"),
    [("cancel", "cancelled_unverified"), ("orphan", "orphaned_unknown")],
)
def test_success_linked_attempt_allows_cancel_or_orphan_recovery(
    tmp_path: Path, recovery: str, expected: str,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finish = finish_contact(
        fence, charge.contact_id, ContactStatus.SUCCESS,
        now_utc=_NOW, path=path,
    )
    assert finish.attempt_id is not None

    if recovery == "cancel":
        assert request_cancel("run", now_utc=_timestamp(1), path=path) == fence
        acknowledge_cancel(fence, now_utc=_timestamp(2), path=path)
    else:
        release_worker(fence, now_utc=_timestamp(2), path=path)

    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT state FROM analyst_model_attempts WHERE attempt_id=?",
            (finish.attempt_id,),
        ).fetchone()[0] == expected
        assert conn.execute(
            "SELECT state FROM analyst_ollama_contacts WHERE contact_id=?",
            (charge.contact_id,),
        ).fetchone()[0] == "success"
        validate_schema(conn)
    finally:
        conn.close()


def test_full_audit_rejects_success_contact_linked_to_schema_invalid_attempt(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    charge = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finish = finish_contact(
        fence, charge.contact_id, ContactStatus.SUCCESS,
        now_utc=_NOW, path=path,
    )
    assert finish.attempt_id is not None

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "UPDATE analyst_model_attempts SET state='schema_invalid',"
            "finished_at_utc=?,failure_code='schema_invalid' WHERE attempt_id=?",
            (_timestamp(1), finish.attempt_id),
        )
        with pytest.raises(AnalystSchemaError, match="outcome contradicts"):
            validate_schema(conn)
    finally:
        conn.close()


def test_full_audit_rejects_backoff_schedule_without_contact_history(
    tmp_path: Path,
) -> None:
    path, _fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "UPDATE analyst_ollama_schedule SET state='backoff',"
            "consecutive_failures=1,delay_seconds=15,not_before_utc=?,"
            "revision=revision+1,updated_at_utc=? WHERE run_id='run'",
            (_timestamp(15), _NOW),
        )
        with pytest.raises(AnalystSchemaError, match="derived from contact history"):
            validate_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("drift", ["ordinal", "counters"])
def test_full_audit_rejects_contact_ordinal_or_counter_history_drift(
    tmp_path: Path, drift: str,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    first = precharge_control_contact(
        fence,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=_NOW,
        path=path,
    )
    finish_contact(
        fence, first.contact_id, ContactStatus.RESOURCE_BUSY,
        now_utc=_NOW, path=path,
    )
    second = precharge_control_contact(
        fence,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=_timestamp(15),
        path=path,
    )
    finish_contact(
        fence, second.contact_id, ContactStatus.REQUEST_TIMEOUT,
        now_utc=_timestamp(15), path=path,
    )

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        if drift == "ordinal":
            conn.execute(
                "UPDATE analyst_ollama_contacts SET contact_no=3 "
                "WHERE contact_id=?", (second.contact_id,),
            )
        else:
            conn.execute(
                "UPDATE analyst_ollama_contacts SET resource_failures_before=0,"
                "resource_failures_after=0 WHERE contact_id=?", (second.contact_id,),
            )
        with pytest.raises(AnalystSchemaError, match="contiguous or replayable"):
            validate_schema(conn)
    finally:
        conn.close()


def test_retry_handoff_closes_wall_rollback_then_allows_precharge(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    contact = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finish_contact(
        fence, contact.contact_id, ContactStatus.RESOURCE_BUSY,
        now_utc=_NOW, path=path,
    )
    schedule = get_schedule("run", path=path)
    clock = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    def beat(current: LeaseFence) -> LeaseFence:
        return persist_heartbeat(
            current,
            heartbeat_monotonic_ns=current.heartbeat_monotonic_ns + 1,
            now_utc=rollback_wall,
            path=path,
        )

    rollback_wall = _timestamp(-1000)
    successor, due = wait_until_resource_retry_due(
        fence,
        schedule,
        cancelled=lambda: False,
        heartbeat=beat,
        now_utc=rollback_wall,
        observed_utc=lambda: rollback_wall,
        path=path,
        monotonic=lambda: clock[0],
        sleep=sleep,
        pulse_seconds=2,
    )

    assert successor.heartbeat_monotonic_ns > fence.heartbeat_monotonic_ns
    assert sum(sleeps) == 15
    assert all(0 < pulse <= 2 for pulse in sleeps)
    assert due.not_before_utc == rollback_wall
    assert due.revision == schedule.revision + 1
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT heartbeat_monotonic_ns FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone()[0] == successor.heartbeat_monotonic_ns
    finally:
        conn.close()
    next_contact = precharge_control_contact(
        successor,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=rollback_wall,
        path=path,
    )
    assert next_contact.resource_failures_before == 1


def test_resume_handoff_closes_wall_rollback_then_allows_claim(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    elapsed = 0
    for delay in (15, 30, 60, 120, 240, 300):
        contact = precharge_chat_contact(
            fence, chunk_id, _sha("5"), now_utc=_timestamp(elapsed), path=path,
        )
        finish_contact(
            fence, contact.contact_id, ContactStatus.RESOURCE_BUSY,
            now_utc=_timestamp(elapsed), path=path,
        )
        elapsed += delay
    schedule = get_schedule("run", path=path)
    clock = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    rollback_wall = _timestamp(-1000)
    authorized = wait_until_resource_resume_authorized(
        "run",
        schedule,
        cancelled=lambda: False,
        now_utc=rollback_wall,
        observed_utc=lambda: rollback_wall,
        path=path,
        monotonic=lambda: clock[0],
        sleep=sleep,
        pulse_seconds=2,
    )

    assert sum(sleeps) == 300
    assert all(0 < pulse <= 2 for pulse in sleeps)
    assert authorized.not_before_utc == rollback_wall
    assert authorized.resume_authorized_at_utc == rollback_wall
    resumed = claim_worker(
        "run",
        ProcessIdentity(202, 303, _BOOT_ID),
        owner_token=_sha("b"),
        heartbeat_monotonic_ns=200,
        now_utc=rollback_wall,
        path=path,
    )
    assert resumed is not None and resumed.generation > fence.generation


def test_retry_handoff_schedule_revision_race_preserves_external_state(
    tmp_path: Path,
) -> None:
    path, fence, chunk_id = _setup_runtime(tmp_path)
    assert chunk_id is not None
    contact = precharge_chat_contact(
        fence, chunk_id, _sha("5"), now_utc=_NOW, path=path,
    )
    finish_contact(
        fence, contact.contact_id, ContactStatus.RESOURCE_BUSY,
        now_utc=_NOW, path=path,
    )
    schedule = get_schedule("run", path=path)
    clock = [0.0]
    raced = [False]

    def beat(current: LeaseFence) -> LeaseFence:
        return persist_heartbeat(
            current,
            heartbeat_monotonic_ns=current.heartbeat_monotonic_ns + 1,
            now_utc=_timestamp(-1000),
            path=path,
        )

    def sleep(seconds: float) -> None:
        if not raced[0]:
            run_immediate(
                lambda conn: conn.execute(
                    "UPDATE analyst_ollama_schedule SET revision=revision+1,"
                    "updated_at_utc=? WHERE run_id='run'",
                    (_timestamp(1),),
                ),
                path=path,
            )
            raced[0] = True
        clock[0] += seconds

    with pytest.raises(OllamaStateError, match="schedule changed"):
        wait_until_resource_retry_due(
            fence,
            schedule,
            cancelled=lambda: False,
            heartbeat=beat,
            now_utc=_timestamp(-1000),
            observed_utc=lambda: _timestamp(-1000),
            path=path,
            monotonic=lambda: clock[0],
            sleep=sleep,
            pulse_seconds=2,
        )
    current = get_schedule("run", path=path)
    assert current.revision == schedule.revision + 1
    assert current.not_before_utc == schedule.not_before_utc


def test_full_audit_rejects_terminal_contact_after_dispatching_contact(
    tmp_path: Path,
) -> None:
    path, fence, _ = _setup_runtime(tmp_path, with_chunk=False)
    dispatching = precharge_control_contact(
        fence,
        ContactKind.CANCELLATION_HEALTH,
        HEALTH_REQUEST_SHA256,
        now_utc=_NOW,
        path=path,
    )

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO analyst_ollama_contacts("
            "contact_id,run_id,contact_no,kind,request_sha256,lease_generation,"
            "state,charged_at_utc,finished_at_utc,resource_failures_before,"
            "resource_failures_after) "
            "VALUES(?,'run',2,'cancellation_health',?,?,'request_timeout',"
            "?,?,0,0)",
            (
                _sha("9"), HEALTH_REQUEST_SHA256,
                fence.generation, _NOW, _timestamp(1),
            ),
        )
        with pytest.raises(
            AnalystSchemaError, match="dispatching Ollama contact is not the final",
        ):
            validate_schema(conn)
        assert tuple(conn.execute(
            "SELECT contact_no,state FROM analyst_ollama_contacts "
            "ORDER BY contact_no"
        )) == ((1, "dispatching"), (2, "request_timeout"))
        assert dispatching.contact_no == 1
    finally:
        conn.close()
