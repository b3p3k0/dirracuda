"""Analyst read-projection schema v4 and additive migration regressions."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    V2_SCHEMA_VERSION,
    V3_SCHEMA_VERSION,
    AnalystSchemaError,
    initialize_schema,
    validate_schema,
)


_NOW = "2026-09-18T14:22:00Z"
_RUN_ID = "r" * 32


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
        "VALUES(?, 'ready', ?, ?, 'fast', 'unknown', '/source', '/output', "
        "'{}', ?, 'Existing run', 'qwen3.6:27b', ?, 'v2', ?, ?, 'rules-v1', "
        "?, '{}', ?, 8000, 256, 8192, 1024, 'strict', 0)",
        (
            _RUN_ID, _NOW, _NOW, _sha("identity"), _sha("model"),
            _sha("prompt"), _sha("response"), _sha("rules"), _sha("parser"),
        ),
    )
    conn.execute(
        "INSERT INTO analyst_files("
        "file_id,run_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,"
        "inode,mode,sha256,stage,work_state,terminal_code,updated_at_utc) "
        "VALUES(1,?,0,'existing.txt',4,1,1,1,1,384,?,'model_response_valid',"
        "'terminal','complete_model_reviewed',?)",
        (_RUN_ID, _sha("file"), _NOW),
    )
    conn.execute(
        "INSERT INTO analyst_chunks("
        "chunk_id,file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
        "VALUES(1,1,0,0,4,?,'pending')",
        (_sha("chunk"),),
    )
    request_sha = _sha("request")
    attempt_id = hashlib.sha256(f"1\0{1}\0{request_sha}".encode("ascii")).hexdigest()
    conn.execute(
        "INSERT INTO analyst_model_attempts("
        "attempt_id,chunk_id,attempt_no,request_sha256,state,charged_at_utc,"
        "finished_at_utc) VALUES(?,1,1,?,'valid',?,?)",
        (attempt_id, request_sha, _NOW, _NOW),
    )
    conn.execute(
        "UPDATE analyst_chunks SET state='model_response_valid',"
        "accepted_attempt_id=?,document_type='text',subject='Existing',"
        "assessment='findings_present',raw_finding_count=1,"
        "removed_duplicate_count=0,dropped_ungrounded_count=0 WHERE chunk_id=1",
        (attempt_id,),
    )
    conn.execute(
        "INSERT INTO analyst_detector_hits("
        "hit_id,file_id,ordinal,kind,value,start_char,end_char) "
        "VALUES(1,1,0,'phone','123',0,3)"
    )
    conn.execute(
        "INSERT INTO analyst_model_findings("
        "finding_id,chunk_id,ordinal,category,quote,model_offset,"
        "canonical_offset,canonical_end,match_count,model_offset_exact) "
        "VALUES(1,1,0,'contact','123',0,0,3,1,1)"
    )
    if version >= V2_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
            "VALUES(?,?)",
            (_RUN_ID, _NOW),
        )


def _create_version(path: Path, version: int, *, populated: bool = True) -> None:
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
        conn.execute("INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)")
        if populated:
            _insert_existing_rows(conn, version)
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_xinfo({table})")}


def test_fresh_v4_create_has_read_tables_rank_columns_and_index() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        } >= {"analyst_read", "analyst_read_exposures"}
        assert all(
            conn.execute(
                "SELECT strict FROM pragma_table_list WHERE name=?", (table,),
            ).fetchone()[0] == 1
            for table in ("analyst_read", "analyst_read_exposures")
        )
        assert _table_columns(conn, "analyst_read") == {
            "run_id", "report_schema_version", "read_mode", "risk_level",
            "host_summary", "likely_owner", "contacts_json", "files_read",
            "files_total", "flagged_files", "created_at_utc",
        }
        assert "fact_rank" in _table_columns(conn, "analyst_detector_hits")
        assert "fact_rank" in _table_columns(conn, "analyst_model_findings")
        assert conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='index' "
            "AND name='idx_analyst_read_risk'"
        ).fetchone() is not None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "version", (V2_SCHEMA_VERSION, V3_SCHEMA_VERSION),
)
def test_v2_v3_migrate_additively_with_rows_preserved(
    tmp_path: Path, version: int,
) -> None:
    path = tmp_path / f"v{version}.db"
    _create_version(path, version)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        before = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "analyst_runs", "analyst_files", "analyst_chunks",
                "analyst_model_attempts", "analyst_detector_hits",
                "analyst_model_findings",
            )
        }
        initialize_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in before
        } == before
        assert conn.execute(
            "SELECT value,fact_rank FROM analyst_detector_hits WHERE hit_id=1"
        ).fetchone() == ("123", None)
        assert conn.execute(
            "SELECT quote,fact_rank FROM analyst_model_findings WHERE finding_id=1"
        ).fetchone() == ("123", None)
        assert conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (_RUN_ID,),
        ).fetchone()[0] == "ready"
        validate_schema(conn)
    finally:
        conn.close()


def test_v4_reinitialization_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "idempotent.db"
    conn = sqlite3.connect(path, isolation_level=None)
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
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / f"{kind}.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        if kind == "foreign":
            conn.execute("PRAGMA application_id=12345")
        else:
            conn.execute("CREATE TABLE analyst_runs(run_id TEXT PRIMARY KEY) STRICT")
        before = (
            conn.execute("PRAGMA application_id").fetchone()[0],
            conn.execute("PRAGMA user_version").fetchone()[0],
            tuple(conn.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )),
        )

        with pytest.raises(AnalystSchemaError):
            initialize_schema(conn)

        after = (
            conn.execute("PRAGMA application_id").fetchone()[0],
            conn.execute("PRAGMA user_version").fetchone()[0],
            tuple(conn.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )),
        )
        assert after == before
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_v4_snapshot_verifies() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        initialize_schema(conn)
        validate_schema(conn)
        assert db_schema._schema_snapshot(conn) == db_schema._expected_snapshot(
            SCHEMA_VERSION
        )
        for version in (1, 2, 3, 4, 5):
            assert db_schema._expected_snapshot(version)
    finally:
        conn.close()


@pytest.mark.parametrize("fact_rank", ("HIGH", "MED", "low", None))
def test_fact_rank_accepts_frozen_values_and_null(
    tmp_path: Path, fact_rank: str | None,
) -> None:
    path = tmp_path / "accepted.db"
    _create_version(path, V3_SCHEMA_VERSION)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_schema(conn)
        conn.execute(
            "UPDATE analyst_detector_hits SET fact_rank=? WHERE hit_id=1",
            (fact_rank,),
        )
        conn.execute(
            "UPDATE analyst_model_findings SET fact_rank=? WHERE finding_id=1",
            (fact_rank,),
        )
        assert conn.execute(
            "SELECT fact_rank FROM analyst_detector_hits"
        ).fetchone()[0] == fact_rank
        assert conn.execute(
            "SELECT fact_rank FROM analyst_model_findings"
        ).fetchone()[0] == fact_rank
    finally:
        conn.close()


@pytest.mark.parametrize(
    "table", ("analyst_detector_hits", "analyst_model_findings"),
)
def test_fact_rank_rejects_values_outside_frozen_domain(
    tmp_path: Path, table: str,
) -> None:
    path = tmp_path / f"rejected-{table}.db"
    _create_version(path, V3_SCHEMA_VERSION)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_schema(conn)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(f"UPDATE {table} SET fact_rank='LOW'")
    finally:
        conn.close()
