"""Exact version-one SQLite schema for durable Analyst state.

This module owns schema identity and validation only. Connection policy, file
creation, transaction retry, and state transitions belong to the store layer.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Final


APPLICATION_ID: Final = 0x44414E41  # DANA
SCHEMA_VERSION: Final = 1

RUN_STATES: Final = (
    "ready", "running", "cancel_requested", "cancelled_pending_resume",
    "interrupted", "finalizing", "complete", "abandoned",
)
RUN_MODES: Final = ("fast", "deep")
SOURCE_MODES: Final = (
    "extraction_manifest", "single_host", "multi_host", "unknown",
)
FILE_STAGES: Final = (
    "discovered", "format_identified", "text_extracted", "detector_scanned",
    "selected_for_model", "model_reviewed", "model_response_valid",
)
FILE_WORK_STATES: Final = (
    "pending", "active", "cancelled_pending_resume", "terminal",
)
FILE_TERMINALS: Final = (
    "complete_detector_only", "complete_model_reviewed",
    "complete_no_supported_content", "unsupported_format", "no_text_layer",
    "parse_timeout", "parse_oom", "parse_signal", "parse_error",
    "parser_output_limit", "detector_output_limit", "oversize", "empty", "encrypted",
    "sandbox_unavailable", "sandbox_error", "model_invalid", "model_timeout",
    "model_transport_error", "source_changed_since_inventory",
    "cancelled_abandoned", "skipped_analyst_output", "skipped_known_bad",
)
PROVENANCE_KINDS: Final = (
    "page", "paragraph", "cell", "slide", "notes", "comments", "output_line",
)
EXCLUSION_REASONS: Final = (
    "analyst_output", "changed_during_inventory", "entry_unreadable",
    "mount_boundary", "special_file", "symlink",
)
CHUNK_STATES: Final = (
    "pending", "model_response_valid", "model_invalid", "model_timeout",
    "model_transport_error",
)
ATTEMPT_STATES: Final = (
    "dispatching", "valid", "schema_invalid", "model_timeout",
    "model_transport_error", "orphaned_unknown", "cancelled_unverified",
)
ATTEMPT_FAILURES: Final = ATTEMPT_STATES[2:]
DETECTOR_KINDS: Final = (
    "ssn", "dob", "passport", "card", "routing", "bank_account", "iban",
    "email", "phone", "demographic_term",
)
FINDING_CATEGORIES: Final = ("pii", "financial", "contact", "demographic")
ASSESSMENTS: Final = (
    "findings_present", "no_findings", "insufficient_evidence",
)
REVIEW_STATES: Final = ("unreviewed", "accepted", "rejected")


class AnalystSchemaError(RuntimeError):
    """The sidecar is empty-but-invalid, partial, unknown, or corrupt."""


def _values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


_LOWER_SHA = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"

_TABLE_DDL: Final = (
    f"""CREATE TABLE analyst_runs (
        run_id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state IN ({_values(RUN_STATES)})),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        finished_at_utc TEXT,
        completion_code TEXT,
        mode TEXT NOT NULL CHECK(mode IN ({_values(RUN_MODES)})),
        source_mode TEXT NOT NULL CHECK(source_mode IN ({_values(SOURCE_MODES)})),
        source_root TEXT NOT NULL CHECK(length(source_root) > 0),
        output_root TEXT NOT NULL CHECK(length(output_root) > 0),
        source_identity_json TEXT NOT NULL
            CHECK(length(source_identity_json) BETWEEN 1 AND 65536),
        source_identity_sha256 TEXT NOT NULL
            CHECK({_LOWER_SHA.format('source_identity_sha256')}),
        report_label TEXT NOT NULL CHECK(length(report_label) > 0),
        host_type TEXT CHECK(host_type IS NULL OR host_type IN ('S','F','H')),
        protocol_server_id INTEGER CHECK(protocol_server_id IS NULL OR protocol_server_id > 0),
        ip_address TEXT,
        port INTEGER CHECK(port IS NULL OR port BETWEEN 1 AND 65535),
        extract_summary_row_id INTEGER
            CHECK(extract_summary_row_id IS NULL OR extract_summary_row_id > 0),
        model_tag TEXT NOT NULL CHECK(length(model_tag) > 0),
        model_digest TEXT NOT NULL CHECK({_LOWER_SHA.format('model_digest')}),
        worksheet_version TEXT NOT NULL CHECK(length(worksheet_version) > 0),
        prompt_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('prompt_sha256')}),
        response_schema_sha256 TEXT NOT NULL
            CHECK({_LOWER_SHA.format('response_schema_sha256')}),
        detector_rules_version TEXT NOT NULL CHECK(length(detector_rules_version) > 0),
        detector_rules_sha256 TEXT NOT NULL
            CHECK({_LOWER_SHA.format('detector_rules_sha256')}),
        parser_bundle_json TEXT NOT NULL
            CHECK(length(parser_bundle_json) BETWEEN 1 AND 65536),
        parser_bundle_sha256 TEXT NOT NULL
            CHECK({_LOWER_SHA.format('parser_bundle_sha256')}),
        chunk_chars INTEGER NOT NULL CHECK(chunk_chars > 0),
        overlap_chars INTEGER NOT NULL CHECK(overlap_chars >= 0 AND overlap_chars < chunk_chars),
        num_ctx INTEGER NOT NULL CHECK(num_ctx > 0),
        num_predict INTEGER NOT NULL CHECK(num_predict > 0),
        isolation_mode TEXT NOT NULL CHECK(isolation_mode IN ('strict','reduced')),
        reduced_isolation_ack INTEGER NOT NULL CHECK(reduced_isolation_ack IN (0,1)),
        cancel_requested_at_utc TEXT,
        finalization_token TEXT,
        report_manifest_sha256 TEXT,
        CHECK((isolation_mode='strict' AND reduced_isolation_ack=0)
              OR (isolation_mode='reduced' AND reduced_isolation_ack=1)),
        CHECK((state='complete' AND completion_code IS NOT NULL AND completion_code IN
                    ('complete','complete_no_supported_content') AND finished_at_utc IS NOT NULL)
              OR (state='abandoned' AND completion_code IS NOT NULL
                  AND completion_code='abandoned' AND finished_at_utc IS NOT NULL)
              OR (state NOT IN ('complete','abandoned') AND completion_code IS NULL
                  AND finished_at_utc IS NULL)),
        CHECK((state IN ('finalizing','complete') AND finalization_token IS NOT NULL
                  AND {_LOWER_SHA.format('finalization_token')})
              OR (state NOT IN ('finalizing','complete') AND finalization_token IS NULL)),
        CHECK(report_manifest_sha256 IS NULL
              OR (state='complete' AND {_LOWER_SHA.format('report_manifest_sha256')}))
    ) STRICT""",
    f"""CREATE TABLE analyst_files (
        file_id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        relative_path TEXT NOT NULL CHECK(length(relative_path) > 0),
        size INTEGER NOT NULL CHECK(size >= 0),
        mtime_ns INTEGER NOT NULL CHECK(mtime_ns >= 0),
        ctime_ns INTEGER NOT NULL CHECK(ctime_ns >= 0),
        device INTEGER NOT NULL CHECK(device >= 0),
        inode INTEGER NOT NULL CHECK(inode >= 0),
        mode INTEGER NOT NULL CHECK(mode >= 0),
        sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('sha256')}),
        stage TEXT NOT NULL CHECK(stage IN ({_values(FILE_STAGES)})),
        work_state TEXT NOT NULL CHECK(work_state IN ({_values(FILE_WORK_STATES)})),
        terminal_code TEXT CHECK(terminal_code IS NULL OR terminal_code IN ({_values(FILE_TERMINALS)})),
        terminal_detail TEXT,
        format_name TEXT,
        encoding TEXT,
        parser_identity_json TEXT
            CHECK(parser_identity_json IS NULL OR length(parser_identity_json) BETWEEN 1 AND 65536),
        parser_identity_sha256 TEXT
            CHECK(parser_identity_sha256 IS NULL OR {_LOWER_SHA.format('parser_identity_sha256')}),
        extraction_meta_json TEXT
            CHECK(extraction_meta_json IS NULL OR length(extraction_meta_json) BETWEEN 1 AND 65536),
        selected_for_model INTEGER CHECK(selected_for_model IS NULL OR selected_for_model IN (0,1)),
        active_generation INTEGER
            CHECK(active_generation IS NULL OR active_generation >= 0),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        updated_at_utc TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        UNIQUE(run_id, ordinal),
        UNIQUE(run_id, relative_path),
        CHECK((work_state='terminal') = (terminal_code IS NOT NULL)),
        CHECK((work_state='active') = (active_generation IS NOT NULL))
    ) STRICT""",
    f"""CREATE TABLE analyst_inventory_exclusions (
        exclusion_id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        relative_path TEXT NOT NULL CHECK(length(relative_path) > 0),
        reason TEXT NOT NULL CHECK(reason IN ({_values(EXCLUSION_REASONS)})),
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        UNIQUE(run_id, ordinal),
        UNIQUE(run_id, relative_path)
    ) STRICT""",
    f"""CREATE TABLE analyst_provenance_units (
        provenance_id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        kind TEXT NOT NULL CHECK(kind IN ({_values(PROVENANCE_KINDS)})),
        label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 256),
        start_char INTEGER NOT NULL CHECK(start_char >= 0),
        end_char INTEGER NOT NULL CHECK(end_char >= start_char),
        FOREIGN KEY(file_id) REFERENCES analyst_files(file_id) ON DELETE RESTRICT,
        UNIQUE(file_id, ordinal),
        UNIQUE(file_id, kind, label)
    ) STRICT""",
    f"""CREATE TABLE analyst_chunks (
        chunk_id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL,
        chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
        start_char INTEGER NOT NULL CHECK(start_char >= 0),
        end_char INTEGER NOT NULL CHECK(end_char > start_char),
        chunk_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('chunk_sha256')}),
        state TEXT NOT NULL CHECK(state IN ({_values(CHUNK_STATES)})),
        accepted_attempt_id TEXT,
        document_type TEXT CHECK(document_type IS NULL OR length(document_type) BETWEEN 1 AND 80),
        subject TEXT CHECK(subject IS NULL OR length(subject) <= 160),
        assessment TEXT CHECK(assessment IS NULL OR assessment IN ({_values(ASSESSMENTS)})),
        raw_finding_count INTEGER CHECK(raw_finding_count IS NULL OR raw_finding_count BETWEEN 0 AND 16),
        removed_duplicate_count INTEGER CHECK(removed_duplicate_count IS NULL OR removed_duplicate_count >= 0),
        dropped_ungrounded_count INTEGER CHECK(dropped_ungrounded_count IS NULL OR dropped_ungrounded_count >= 0),
        FOREIGN KEY(file_id) REFERENCES analyst_files(file_id) ON DELETE RESTRICT,
        FOREIGN KEY(accepted_attempt_id) REFERENCES analyst_model_attempts(attempt_id) ON DELETE RESTRICT,
        UNIQUE(file_id, chunk_index),
        CHECK((state='model_response_valid' AND accepted_attempt_id IS NOT NULL
                  AND document_type IS NOT NULL AND subject IS NOT NULL AND assessment IS NOT NULL
                  AND raw_finding_count IS NOT NULL AND removed_duplicate_count IS NOT NULL
                  AND dropped_ungrounded_count IS NOT NULL
                  AND removed_duplicate_count + dropped_ungrounded_count <= raw_finding_count)
              OR (state!='model_response_valid' AND accepted_attempt_id IS NULL
                  AND document_type IS NULL AND subject IS NULL AND assessment IS NULL
                  AND raw_finding_count IS NULL AND removed_duplicate_count IS NULL
                  AND dropped_ungrounded_count IS NULL))
    ) STRICT""",
    f"""CREATE TABLE analyst_model_attempts (
        attempt_id TEXT PRIMARY KEY CHECK(length(attempt_id) > 0),
        chunk_id INTEGER NOT NULL,
        attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 2),
        request_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('request_sha256')}),
        state TEXT NOT NULL CHECK(state IN ({_values(ATTEMPT_STATES)})),
        charged_at_utc TEXT NOT NULL,
        finished_at_utc TEXT,
        failure_code TEXT,
        FOREIGN KEY(chunk_id) REFERENCES analyst_chunks(chunk_id) ON DELETE RESTRICT,
        UNIQUE(chunk_id, attempt_no),
        CHECK((state='dispatching' AND finished_at_utc IS NULL AND failure_code IS NULL)
              OR (state='valid' AND finished_at_utc IS NOT NULL AND failure_code IS NULL)
              OR (state IN ({_values(ATTEMPT_FAILURES)}) AND finished_at_utc IS NOT NULL
                  AND failure_code IS NOT NULL
                  AND failure_code IN ({_values(ATTEMPT_FAILURES)})))
    ) STRICT""",
    f"""CREATE TABLE analyst_detector_hits (
        hit_id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        kind TEXT NOT NULL CHECK(kind IN ({_values(DETECTOR_KINDS)})),
        value TEXT NOT NULL CHECK(length(value) > 0),
        start_char INTEGER NOT NULL CHECK(start_char >= 0),
        end_char INTEGER NOT NULL CHECK(end_char > start_char),
        FOREIGN KEY(file_id) REFERENCES analyst_files(file_id) ON DELETE RESTRICT,
        UNIQUE(file_id, ordinal),
        CHECK(end_char - start_char = length(value))
    ) STRICT""",
    f"""CREATE TABLE analyst_model_findings (
        finding_id INTEGER PRIMARY KEY,
        chunk_id INTEGER NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        category TEXT NOT NULL CHECK(category IN ({_values(FINDING_CATEGORIES)})),
        quote TEXT NOT NULL CHECK(length(quote) BETWEEN 1 AND 240),
        model_offset INTEGER NOT NULL CHECK(model_offset >= 0),
        canonical_offset INTEGER NOT NULL CHECK(canonical_offset >= 0),
        canonical_end INTEGER NOT NULL,
        match_count INTEGER NOT NULL CHECK(match_count > 0),
        model_offset_exact INTEGER NOT NULL CHECK(model_offset_exact IN (0,1)),
        review_state TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(review_state IN ({_values(REVIEW_STATES)})),
        reviewed_at_utc TEXT,
        FOREIGN KEY(chunk_id) REFERENCES analyst_chunks(chunk_id) ON DELETE RESTRICT,
        UNIQUE(chunk_id, ordinal),
        CHECK(canonical_end = canonical_offset + length(quote)),
        CHECK((review_state='unreviewed') = (reviewed_at_utc IS NULL))
    ) STRICT""",
    """CREATE TABLE analyst_gpu_lease (
        slot INTEGER PRIMARY KEY CHECK(slot=1),
        generation INTEGER NOT NULL CHECK(generation >= 0),
        run_id TEXT,
        owner_token TEXT,
        pid INTEGER,
        start_ticks INTEGER,
        boot_id TEXT,
        heartbeat_monotonic_ns INTEGER,
        claimed_at_utc TEXT,
        heartbeat_at_utc TEXT,
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        UNIQUE(run_id),
        CHECK((run_id IS NULL AND owner_token IS NULL AND pid IS NULL
                  AND start_ticks IS NULL AND boot_id IS NULL
                  AND heartbeat_monotonic_ns IS NULL AND claimed_at_utc IS NULL
                  AND heartbeat_at_utc IS NULL)
              OR (run_id IS NOT NULL AND owner_token IS NOT NULL
                  AND length(owner_token)=64 AND owner_token NOT GLOB '*[^0-9a-f]*'
                  AND pid IS NOT NULL AND pid > 0
                  AND start_ticks IS NOT NULL AND start_ticks >= 0
                  AND boot_id IS NOT NULL
                  AND heartbeat_monotonic_ns IS NOT NULL AND heartbeat_monotonic_ns >= 0
                  AND claimed_at_utc IS NOT NULL
                  AND heartbeat_at_utc IS NOT NULL))
    ) STRICT""",
)

_INDEX_DDL: Final = (
    "CREATE INDEX idx_analyst_runs_state_updated ON analyst_runs(state,updated_at_utc,run_id)",
    "CREATE INDEX idx_analyst_runs_host ON analyst_runs(host_type,protocol_server_id,created_at_utc,run_id)",
    "CREATE INDEX idx_analyst_runs_endpoint ON analyst_runs(ip_address,port,created_at_utc,run_id)",
    "CREATE INDEX idx_analyst_files_work ON analyst_files(run_id,work_state,ordinal)",
    "CREATE INDEX idx_analyst_files_terminal ON analyst_files(run_id,terminal_code,ordinal)",
    "CREATE INDEX idx_analyst_files_stage ON analyst_files(run_id,stage,ordinal)",
    "CREATE INDEX idx_analyst_provenance_span ON analyst_provenance_units(file_id,start_char,end_char,ordinal)",
    "CREATE INDEX idx_analyst_chunks_work ON analyst_chunks(file_id,state,chunk_index)",
    "CREATE INDEX idx_analyst_attempts_state ON analyst_model_attempts(chunk_id,state,attempt_no)",
    "CREATE UNIQUE INDEX ux_analyst_attempts_one_valid ON analyst_model_attempts(chunk_id) WHERE state='valid'",
    "CREATE INDEX idx_analyst_exclusions_reason ON analyst_inventory_exclusions(run_id,reason,ordinal)",
    "CREATE INDEX idx_analyst_detector_kind ON analyst_detector_hits(file_id,kind,ordinal)",
    "CREATE INDEX idx_analyst_findings_category ON analyst_model_findings(category,review_state,finding_id)",
)


@dataclass(frozen=True)
class _SchemaSnapshot:
    objects: tuple[tuple[str, str, str], ...]
    table_list: tuple[tuple[object, ...], ...]
    columns: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]
    indexes: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]
    index_columns: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]
    foreign_keys: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create v1 only over a truly empty, version-zero main database.

    An existing exact v1 schema is accepted idempotently. Any other on-disk
    state is rejected before DDL and is never repaired in place.
    """
    _require_transaction_boundary(conn)
    identity = _identity(conn)
    objects = _user_objects(conn)
    if identity == (APPLICATION_ID, SCHEMA_VERSION):
        validate_schema(conn)
        return
    if identity != (0, 0) or objects:
        raise AnalystSchemaError(
            "refusing to initialize a nonempty, partial, foreign, or versioned database"
        )

    conn.execute("PRAGMA foreign_keys=ON")
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != 1:
        raise AnalystSchemaError("SQLite foreign-key enforcement is unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        concurrent_identity = _identity(conn)
        concurrent_objects = _user_objects(conn)
        if concurrent_identity == (APPLICATION_ID, SCHEMA_VERSION):
            validate_schema(conn)
            conn.execute("COMMIT")
            return
        if concurrent_identity != (0, 0) or concurrent_objects:
            raise AnalystSchemaError("database changed during schema initialization")
        for statement in (*_TABLE_DDL, *_INDEX_DDL):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)"
        )
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        validate_schema(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise AnalystSchemaError("new schema failed foreign-key validation")
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]) != "ok":
            raise AnalystSchemaError("new schema failed SQLite quick_check")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def validate_schema(conn: sqlite3.Connection) -> None:
    """Read and exactly validate schema v1 without mutating the connection DB."""
    if _identity(conn) != (APPLICATION_ID, SCHEMA_VERSION):
        raise AnalystSchemaError("Analyst database identity or schema version is not v1")
    actual = _schema_snapshot(conn)
    expected = _expected_snapshot()
    if actual != expected:
        raise AnalystSchemaError("Analyst v1 schema signature does not match")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise AnalystSchemaError("Analyst database has foreign-key violations")
    rows = conn.execute(
        "SELECT slot,generation,run_id,owner_token,pid,start_ticks,boot_id,"
        "heartbeat_monotonic_ns,claimed_at_utc,heartbeat_at_utc "
        "FROM analyst_gpu_lease"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise AnalystSchemaError("Analyst GPU lease singleton is missing or duplicated")
    invalid_accepted = conn.execute(
        "SELECT 1 FROM analyst_chunks AS c "
        "JOIN analyst_model_attempts AS a ON a.attempt_id=c.accepted_attempt_id "
        "WHERE a.chunk_id!=c.chunk_id OR a.state!='valid' LIMIT 1"
    ).fetchone()
    if invalid_accepted is not None:
        raise AnalystSchemaError("accepted model attempt is not valid for its chunk")


def validate_runtime_schema(conn: sqlite3.Connection) -> None:
    """Validate constant-cost schema identity for an already audited sidecar."""
    if _identity(conn) != (APPLICATION_ID, SCHEMA_VERSION):
        raise AnalystSchemaError("Analyst database identity or schema version is not v1")
    objects = tuple(
        (kind, name, _normalize_sql(sql))
        for kind, name, sql in _user_objects(conn)
    )
    if objects != _expected_snapshot().objects:
        raise AnalystSchemaError("Analyst v1 runtime schema signature does not match")
    rows = conn.execute(
        "SELECT slot,generation,run_id FROM analyst_gpu_lease"
    ).fetchall()
    if len(rows) != 1 or int(rows[0][0]) != 1:
        raise AnalystSchemaError("Analyst GPU lease singleton is missing or duplicated")


def _identity(conn: sqlite3.Connection) -> tuple[int, int]:
    app = conn.execute("PRAGMA application_id").fetchone()
    version = conn.execute("PRAGMA user_version").fetchone()
    if app is None or version is None:
        raise AnalystSchemaError("SQLite did not return database identity PRAGMAs")
    return int(app[0]), int(version[0])


def _require_transaction_boundary(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise AnalystSchemaError("schema initialization requires no active transaction")


def _user_objects(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    rows = conn.execute(
        "SELECT type,name,coalesce(sql,'') FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)


def _schema_snapshot(conn: sqlite3.Connection) -> _SchemaSnapshot:
    objects = tuple(
        (kind, name, _normalize_sql(sql))
        for kind, name, sql in _user_objects(conn)
    )
    table_names = tuple(
        name for kind, name, _ in objects if kind == "table"
    )
    table_rows = conn.execute("PRAGMA table_list").fetchall()
    table_list = tuple(sorted(
        tuple(row[1:]) for row in table_rows
        if row[0] == "main" and not str(row[1]).startswith("sqlite_")
    ))
    columns = tuple(
        (table, tuple(tuple(row) for row in conn.execute(
            f"PRAGMA table_xinfo({_sql_string(table)})"
        ).fetchall()))
        for table in table_names
    )
    indexes: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    index_columns: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    foreign_keys = []
    for table in table_names:
        index_rows = tuple(tuple(row) for row in conn.execute(
            f"PRAGMA index_list({_sql_string(table)})"
        ).fetchall())
        indexes.append((table, index_rows))
        for row in index_rows:
            index_columns.append((str(row[1]), tuple(tuple(value) for value in conn.execute(
                f"PRAGMA index_xinfo({_sql_string(str(row[1]))})"
            ).fetchall())))
        foreign_keys.append((table, tuple(tuple(row) for row in conn.execute(
            f"PRAGMA foreign_key_list({_sql_string(table)})"
        ).fetchall())))
    return _SchemaSnapshot(
        objects=objects,
        table_list=table_list,
        columns=columns,
        indexes=tuple(indexes),
        index_columns=tuple(index_columns),
        foreign_keys=tuple(foreign_keys),
    )


@lru_cache(maxsize=1)
def _expected_snapshot() -> _SchemaSnapshot:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in (*_TABLE_DDL, *_INDEX_DDL):
            conn.execute(statement)
        return _schema_snapshot(conn)
    finally:
        conn.close()


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = [
    "APPLICATION_ID",
    "SCHEMA_VERSION",
    "AnalystSchemaError",
    "initialize_schema",
    "validate_runtime_schema",
    "validate_schema",
]
