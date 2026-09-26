"""Analyst model-discovery schema v6 and additive migration regressions."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import db_schema
from experimental.analyst.contact_contract import TAGS_REQUEST_SHA256
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    V1_SCHEMA_VERSION,
    V2_SCHEMA_VERSION,
    V3_SCHEMA_VERSION,
    V4_SCHEMA_VERSION,
    V5_SCHEMA_VERSION,
    AnalystSchemaError,
    initialize_schema,
    validate_schema,
)
from experimental.analyst.store import initialize_database, open_connection


_NOW = "2026-09-18T18:00:00Z"
_RUN_ID = "r" * 32
_ENDPOINT = "127.0.0.1:11434"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _insert_existing_rows(conn: sqlite3.Connection, version: int) -> None:
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
    if version >= V5_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO analyst_read_contact("
            "contact_id,run_id,attempt_no,request_sha256,lease_generation,state,"
            "charged_at_utc,finished_at_utc,resource_failures_before,"
            "resource_failures_after) VALUES(?,?,1,?,1,'success',?,?,0,0)",
            (_sha("read-contact"), _RUN_ID, _sha("read-request"), _NOW, _NOW),
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
        if version >= V5_SCHEMA_VERSION:
            for statement in db_schema._V5_ADDITIONAL_DDL:
                conn.execute(statement)
        conn.execute("INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)")
        if populated:
            _insert_existing_rows(conn, version)
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


def _insert_discovery_contact(
    conn: sqlite3.Connection,
    *,
    contact_no: int,
    state: str,
    finished_at_utc: str | None,
) -> None:
    conn.execute(
        "INSERT INTO analyst_discovery_contact("
        "contact_id,contact_no,endpoint,request_sha256,state,models_found,"
        "charged_at_utc,finished_at_utc) VALUES(?,?,?,?,?,?,?,?)",
        (
            _sha(f"discovery-{contact_no}-{state}"),
            contact_no,
            _ENDPOINT,
            TAGS_REQUEST_SHA256,
            state,
            None if state == "dispatching" else 1,
            _NOW,
            finished_at_utc,
        ),
    )


def test_fresh_v6_has_discovery_tables_indexes_and_identity() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)

        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 11
        assert {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        } >= {"analyst_discovery_contact", "analyst_discovered_model"}
        assert all(
            conn.execute(
                "SELECT strict FROM pragma_table_list WHERE name=?", (table,),
            ).fetchone()[0] == 1
            for table in ("analyst_discovery_contact", "analyst_discovered_model")
        )
        assert {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_discovery_contact)")
        } == {
            "contact_id",
            "contact_no",
            "endpoint",
            "request_sha256",
            "state",
            "models_found",
            "charged_at_utc",
            "finished_at_utc",
        }
        assert {
            str(row[1])
            for row in conn.execute("PRAGMA table_xinfo(analyst_discovered_model)")
        } == {
            "endpoint",
            "model_tag",
            "model_digest",
            "first_seen_utc",
            "last_seen_utc",
        }
        assert {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='index'"
            )
        } >= {
            "idx_analyst_discovery_contact_endpoint",
            "idx_analyst_discovered_model_endpoint",
        }
    finally:
        conn.close()


@pytest.mark.parametrize(
    "version",
    (
        V1_SCHEMA_VERSION,
        V2_SCHEMA_VERSION,
        V3_SCHEMA_VERSION,
        V4_SCHEMA_VERSION,
        V5_SCHEMA_VERSION,
    ),
)
def test_v1_through_v5_migrate_additively_and_preserve_rows(
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
        if version >= V4_SCHEMA_VERSION:
            assert conn.execute(
                "SELECT host_summary FROM analyst_read WHERE run_id=?", (_RUN_ID,),
            ).fetchone()[0] == "Existing read"
        if version >= V5_SCHEMA_VERSION:
            assert conn.execute(
                "SELECT state FROM analyst_read_contact WHERE run_id=?", (_RUN_ID,),
            ).fetchone()[0] == "success"
        assert conn.execute(
            "SELECT count(*) FROM analyst_discovery_contact"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM analyst_discovered_model"
        ).fetchone()[0] == 0
        validate_schema(conn)
    finally:
        conn.close()


def test_store_audit_accepts_exact_idle_v5_before_migration(tmp_path: Path) -> None:
    path = tmp_path / "analyst.db"
    _create_version(path, V5_SCHEMA_VERSION, populated=True)
    path.chmod(0o600)

    assert initialize_database(path) == path

    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute(
            "SELECT state FROM analyst_read_contact WHERE run_id=?", (_RUN_ID,),
        ).fetchone()[0] == "success"
    finally:
        conn.close()


def test_v6_reinitialization_is_idempotent() -> None:
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
def test_foreign_or_partial_database_is_refused_without_mutation(kind: str) -> None:
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


def test_v6_snapshot_verifies_full_version_chain() -> None:
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


@pytest.mark.parametrize(
    ("state", "finished_at_utc"),
    (("dispatching", _NOW), ("success", None)),
)
def test_discovery_contact_enforces_dispatching_finished_check(
    state: str, finished_at_utc: str | None,
) -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_discovery_contact(
                conn,
                contact_no=1,
                state=state,
                finished_at_utc=finished_at_utc,
            )
    finally:
        conn.close()


def test_discovered_model_primary_key_deduplicates_endpoint_and_tag() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)
        conn.execute(
            "INSERT INTO analyst_discovered_model("
            "endpoint,model_tag,model_digest,first_seen_utc,last_seen_utc) "
            "VALUES(?,?,?,?,?)",
            (_ENDPOINT, "qwen3.6:27b", _sha("digest-1"), _NOW, _NOW),
        )

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute(
                "INSERT INTO analyst_discovered_model("
                "endpoint,model_tag,model_digest,first_seen_utc,last_seen_utc) "
                "VALUES(?,?,?,?,?)",
                (_ENDPOINT, "qwen3.6:27b", _sha("digest-2"), _NOW, _NOW),
            )
    finally:
        conn.close()


def test_v5_dispatching_read_contact_is_refused_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "active-v5.db"
    _create_version(path, V5_SCHEMA_VERSION, populated=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "UPDATE analyst_read_contact SET state='dispatching',"
            "finished_at_utc=NULL,resource_failures_after=NULL"
        )
        before = _identity_and_objects(conn)

        with pytest.raises(AnalystSchemaError, match="idle durable state"):
            initialize_schema(conn)

        assert _identity_and_objects(conn) == before
        assert conn.execute(
            "SELECT state FROM analyst_read_contact"
        ).fetchone()[0] == "dispatching"
        assert conn.in_transaction is False
    finally:
        conn.close()
