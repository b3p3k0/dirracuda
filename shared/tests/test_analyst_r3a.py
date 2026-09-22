"""Analyst host-read contact schema v5 and additive migration regressions."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    V1_SCHEMA_VERSION,
    V2_SCHEMA_VERSION,
    V3_SCHEMA_VERSION,
    V4_SCHEMA_VERSION,
    AnalystSchemaError,
    initialize_schema,
    validate_schema,
)


_NOW = "2026-09-18T16:00:00Z"
_RUN_ID = "r" * 32


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _insert_run(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO analyst_runs("
        "run_id,state,created_at_utc,updated_at_utc,mode,source_mode,"
        "source_root,output_root,source_identity_json,source_identity_sha256,"
        "report_label,model_tag,model_digest,worksheet_version,prompt_sha256,"
        "response_schema_sha256,detector_rules_version,detector_rules_sha256,"
        "parser_bundle_json,parser_bundle_sha256,chunk_chars,overlap_chars,"
        "num_ctx,num_predict,isolation_mode,reduced_isolation_ack) "
        "VALUES(?,'ready',?,?,'fast','unknown','/source','/output','{}',?,"
        "'Existing run','qwen3.6:27b',?,'v2',?,?,'rules-v1',?,'{}',?,"
        "8000,256,8192,1024,'strict',0)",
        (
            _RUN_ID,
            _NOW,
            _NOW,
            _sha("identity"),
            _sha("model"),
            _sha("prompt"),
            _sha("response"),
            _sha("rules"),
            _sha("parser"),
        ),
    )
    if version >= V2_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
            "VALUES(?,?)",
            (_RUN_ID, _NOW),
        )
    if version >= V4_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO analyst_read("
            "run_id,report_schema_version,read_mode,risk_level,host_summary,"
            "contacts_json,files_read,files_total,flagged_files,created_at_utc) "
            "VALUES(?,1,'quick','LOW','Existing read','[]',0,0,0,?)",
            (_RUN_ID, _NOW),
        )
        conn.execute(
            "INSERT INTO analyst_read_exposures(run_id,ordinal,severity,text) "
            "VALUES(?,1,'LOW','Existing exposure')",
            (_RUN_ID,),
        )


def _create_version(path: Path, version: int, *, populated: bool) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for statement in (*db_schema._V1_TABLE_DDL, *db_schema._V1_INDEX_DDL):
            conn.execute(statement)
        if version >= V2_SCHEMA_VERSION:
            for statement in (
                *db_schema._V2_ADDITIONAL_TABLE_DDL,
                *db_schema._V2_ADDITIONAL_INDEX_DDL,
            ):
                conn.execute(statement)
        if version >= V3_SCHEMA_VERSION:
            for statement in db_schema._V3_ADDITIONAL_DDL:
                conn.execute(statement)
        if version >= V4_SCHEMA_VERSION:
            for statement in db_schema._V4_ADDITIONAL_DDL:
                conn.execute(statement)
        conn.execute("INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)")
        if populated:
            _insert_run(conn, version)
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _identity_and_objects(
    conn: sqlite3.Connection,
) -> tuple[int, int, tuple[tuple[object, ...], ...]]:
    return (
        int(conn.execute("PRAGMA application_id").fetchone()[0]),
        int(conn.execute("PRAGMA user_version").fetchone()[0]),
        tuple(
            tuple(row)
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ),
    )


def _insert_read_contact(
    conn: sqlite3.Connection,
    *,
    attempt_no: int,
    state: str,
    finished_at_utc: str | None,
) -> None:
    conn.execute(
        "INSERT INTO analyst_read_contact("
        "contact_id,run_id,attempt_no,request_sha256,lease_generation,state,"
        "charged_at_utc,finished_at_utc,resource_failures_before,"
        "resource_failures_after) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            _sha(f"contact-{attempt_no}-{state}"),
            _RUN_ID,
            attempt_no,
            _sha("request"),
            1,
            state,
            _NOW,
            finished_at_utc,
            0,
            None,
        ),
    )


def test_fresh_schema_has_read_contact_table_index_and_v6_identity() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)

        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 7
        assert conn.execute(
            "SELECT strict FROM pragma_table_list "
            "WHERE name='analyst_read_contact'"
        ).fetchone()[0] == 1
        assert {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_read_contact)")
        } == {
            "contact_id",
            "run_id",
            "attempt_no",
            "request_sha256",
            "lease_generation",
            "state",
            "charged_at_utc",
            "finished_at_utc",
            "resource_failures_before",
            "resource_failures_after",
        }
        assert conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='index' "
            "AND name='idx_analyst_read_contact_run'"
        ).fetchone() is not None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "version",
    (V1_SCHEMA_VERSION, V2_SCHEMA_VERSION, V3_SCHEMA_VERSION, V4_SCHEMA_VERSION),
)
def test_v1_through_v4_migrate_additively_and_preserve_rows(
    tmp_path: Path, version: int,
) -> None:
    path = tmp_path / f"v{version}.db"
    _create_version(path, version, populated=version != V1_SCHEMA_VERSION)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        existing_tables = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        counts_before = {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in existing_tables
        }

        initialize_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in existing_tables
        } == counts_before
        if version != V1_SCHEMA_VERSION:
            assert conn.execute(
                "SELECT state FROM analyst_runs WHERE run_id=?", (_RUN_ID,),
            ).fetchone()[0] == "ready"
        if version == V4_SCHEMA_VERSION:
            assert conn.execute(
                "SELECT host_summary FROM analyst_read WHERE run_id=?", (_RUN_ID,),
            ).fetchone()[0] == "Existing read"
        assert conn.execute(
            "SELECT count(*) FROM analyst_read_contact"
        ).fetchone()[0] == 0
        validate_schema(conn)
    finally:
        conn.close()


def test_current_schema_reinitialization_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)
        before = db_schema._schema_snapshot(conn)

        initialize_schema(conn)

        assert db_schema._schema_snapshot(conn) == before
        assert conn.execute(
            "SELECT count(*) FROM analyst_gpu_lease"
        ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ("foreign", "partial"))
def test_foreign_or_partial_database_is_refused_without_mutation(
    kind: str,
) -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        if kind == "foreign":
            conn.execute("PRAGMA application_id=12345")
        else:
            conn.execute("CREATE TABLE analyst_runs(run_id TEXT PRIMARY KEY) STRICT")
        before = _identity_and_objects(conn)

        with pytest.raises(AnalystSchemaError):
            initialize_schema(conn)

        assert _identity_and_objects(conn) == before
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_v6_snapshot_verifies() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)

        validate_schema(conn)
        assert db_schema._schema_snapshot(conn) == db_schema._expected_snapshot(
            db_schema.SCHEMA_VERSION
        )
        for version in db_schema.KNOWN_SCHEMA_VERSIONS:
            assert db_schema._expected_snapshot(version)
    finally:
        conn.close()


@pytest.mark.parametrize("attempt_no", (0, 3))
def test_read_contact_rejects_attempt_number_outside_one_or_two(
    attempt_no: int,
) -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)
        _insert_run(conn, SCHEMA_VERSION)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_read_contact(
                conn,
                attempt_no=attempt_no,
                state="dispatching",
                finished_at_utc=None,
            )
    finally:
        conn.close()


def test_read_contact_rejects_dispatching_row_with_finished_timestamp() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)
        _insert_run(conn, SCHEMA_VERSION)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_read_contact(
                conn,
                attempt_no=1,
                state="dispatching",
                finished_at_utc=_NOW,
            )
    finally:
        conn.close()


def test_owned_v4_lease_is_refused_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "owned-v4.db"
    _create_version(path, V4_SCHEMA_VERSION, populated=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "UPDATE analyst_gpu_lease SET generation=1,run_id=?,owner_token=?,"
            "pid=1,start_ticks=0,boot_id='boot',heartbeat_monotonic_ns=0,"
            "claimed_at_utc=?,heartbeat_at_utc=? WHERE slot=1",
            (_RUN_ID, "a" * 64, _NOW, _NOW),
        )
        before = _identity_and_objects(conn)

        with pytest.raises(AnalystSchemaError, match="idle durable state"):
            initialize_schema(conn)

        assert _identity_and_objects(conn) == before
        assert conn.execute(
            "SELECT generation,run_id FROM analyst_gpu_lease WHERE slot=1"
        ).fetchone() == (1, _RUN_ID)
        assert conn.in_transaction is False
    finally:
        conn.close()
