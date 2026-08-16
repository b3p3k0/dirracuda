"""Exact versioned SQLite schemas for durable Analyst state.

This module owns schema identity and validation only. Connection policy, file
creation, transaction retry, and state transitions belong to the store layer.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from .contact_contract import (
    ContactKind,
    ContactStatus,
    MAX_CHAT_CONTACTS_PER_CHUNK,
    MAX_CONTROL_CONTACTS_PER_RUN,
    PS_REQUEST_SHA256,
    TAGS_REQUEST_SHA256,
    VERSION_REQUEST_SHA256,
    ScheduleState,
)
from .resource_policy import RESOURCE_BACKOFF_SECONDS


APPLICATION_ID: Final = 0x44414E41  # DANA
PREVIOUS_SCHEMA_VERSION: Final = 1
SCHEMA_VERSION: Final = 2
KNOWN_SCHEMA_VERSIONS: Final = (PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION)

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
OLLAMA_CONTACT_KINDS: Final = tuple(item.value for item in ContactKind)
OLLAMA_CONTACT_STATES: Final = tuple(item.value for item in ContactStatus)
OLLAMA_SCHEDULE_STATES: Final = tuple(item.value for item in ScheduleState)


class AnalystSchemaError(RuntimeError):
    """The sidecar is empty-but-invalid, partial, unknown, or corrupt."""


def _values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


_LOWER_SHA = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"

_V1_TABLE_DDL: Final = (
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

_V1_INDEX_DDL: Final = (
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

_V2_ADDITIONAL_TABLE_DDL: Final = (
    f"""CREATE TABLE analyst_ollama_contacts (
        contact_id TEXT PRIMARY KEY CHECK({_LOWER_SHA.format('contact_id')}),
        run_id TEXT NOT NULL,
        contact_no INTEGER NOT NULL CHECK(contact_no > 0),
        kind TEXT NOT NULL CHECK(kind IN ({_values(OLLAMA_CONTACT_KINDS)})),
        chunk_id INTEGER CHECK(chunk_id IS NULL OR chunk_id > 0),
        semantic_attempt_no INTEGER
            CHECK(semantic_attempt_no IS NULL OR semantic_attempt_no BETWEEN 1 AND 2),
        request_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('request_sha256')}),
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        state TEXT NOT NULL CHECK(state IN ({_values(OLLAMA_CONTACT_STATES)})),
        charged_at_utc TEXT NOT NULL
            CHECK(length(charged_at_utc) BETWEEN 1 AND 40),
        finished_at_utc TEXT
            CHECK(finished_at_utc IS NULL OR length(finished_at_utc) BETWEEN 1 AND 40),
        attempt_id TEXT UNIQUE
            CHECK(attempt_id IS NULL OR {_LOWER_SHA.format('attempt_id')}),
        resource_failures_before INTEGER NOT NULL
            CHECK(resource_failures_before BETWEEN 0 AND 6),
        resource_failures_after INTEGER
            CHECK(resource_failures_after IS NULL
                  OR resource_failures_after BETWEEN 0 AND 6),
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        FOREIGN KEY(chunk_id) REFERENCES analyst_chunks(chunk_id) ON DELETE RESTRICT,
        FOREIGN KEY(attempt_id) REFERENCES analyst_model_attempts(attempt_id)
            ON DELETE RESTRICT,
        UNIQUE(run_id, contact_no),
        CHECK((kind='chat' AND chunk_id IS NOT NULL AND semantic_attempt_no IS NOT NULL)
              OR (kind!='chat' AND chunk_id IS NULL AND semantic_attempt_no IS NULL)),
        CHECK(state!='model_invalid' OR kind IN ('chat','cancellation_health')),
        CHECK((state='dispatching' AND finished_at_utc IS NULL
                  AND resource_failures_after IS NULL)
              OR (state!='dispatching' AND finished_at_utc IS NOT NULL
                  AND resource_failures_after IS NOT NULL)),
        CHECK((kind='chat' AND state NOT IN ('dispatching','resource_busy')
                  AND attempt_id IS NOT NULL)
              OR ((kind!='chat' OR state IN ('dispatching','resource_busy'))
                  AND attempt_id IS NULL)),
        CHECK(state='dispatching'
              OR (state='resource_busy'
                  AND resource_failures_after=min(resource_failures_before+1,6))
              OR (kind IN ('chat','cancellation_health')
                  AND state IN ('success','model_invalid')
                  AND resource_failures_after=0)
              OR ((kind NOT IN ('chat','cancellation_health')
                       OR state NOT IN ('success','model_invalid','resource_busy'))
                  AND state!='resource_busy'
                  AND resource_failures_after=resource_failures_before))
    ) STRICT""",
    f"""CREATE TABLE analyst_ollama_schedule (
        run_id TEXT PRIMARY KEY,
        state TEXT NOT NULL DEFAULT 'available'
            CHECK(state IN ({_values(OLLAMA_SCHEDULE_STATES)})),
        consecutive_failures INTEGER NOT NULL DEFAULT 0
            CHECK(consecutive_failures BETWEEN 0 AND 6),
        delay_seconds INTEGER NOT NULL DEFAULT 0
            CHECK(delay_seconds IN (0,15,30,60,120,240,300)),
        not_before_utc TEXT
            CHECK(not_before_utc IS NULL OR length(not_before_utc) BETWEEN 1 AND 40),
        resume_authorized_at_utc TEXT
            CHECK(resume_authorized_at_utc IS NULL
                  OR length(resume_authorized_at_utc) BETWEEN 1 AND 40),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        updated_at_utc TEXT NOT NULL
            CHECK(length(updated_at_utc) BETWEEN 1 AND 40),
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        CHECK((state='available' AND consecutive_failures=0
                  AND delay_seconds=0 AND not_before_utc IS NULL
                  AND resume_authorized_at_utc IS NULL)
              OR (state='backoff' AND consecutive_failures BETWEEN 1 AND 5
                  AND not_before_utc IS NOT NULL
                  AND resume_authorized_at_utc IS NULL
                  AND ((consecutive_failures=1 AND delay_seconds=15)
                    OR (consecutive_failures=2 AND delay_seconds=30)
                    OR (consecutive_failures=3 AND delay_seconds=60)
                    OR (consecutive_failures=4 AND delay_seconds=120)
                    OR (consecutive_failures=5 AND delay_seconds=240)))
              OR (state='paused_resource' AND consecutive_failures=6
                  AND delay_seconds=300 AND not_before_utc IS NOT NULL))
    ) STRICT""",
)

_V2_ADDITIONAL_INDEX_DDL: Final = (
    "CREATE INDEX idx_analyst_contacts_run ON "
    "analyst_ollama_contacts(run_id,kind,state,contact_no)",
    "CREATE INDEX idx_analyst_contacts_chunk ON "
    "analyst_ollama_contacts(chunk_id,semantic_attempt_no,contact_no)",
    "CREATE UNIQUE INDEX ux_analyst_contacts_one_dispatching ON "
    "analyst_ollama_contacts((1)) WHERE state='dispatching'",
    "CREATE UNIQUE INDEX ux_analyst_contacts_semantic_slot ON "
    "analyst_ollama_contacts(chunk_id,semantic_attempt_no) "
    "WHERE kind='chat' AND state!='resource_busy'",
    "CREATE INDEX idx_analyst_schedule_state ON "
    "analyst_ollama_schedule(state,not_before_utc,run_id)",
)

_TABLE_DDL: Final = (*_V1_TABLE_DDL, *_V2_ADDITIONAL_TABLE_DDL)
_INDEX_DDL: Final = (*_V1_INDEX_DDL, *_V2_ADDITIONAL_INDEX_DDL)

_V1_DOMAIN_TABLES: Final = (
    "analyst_runs",
    "analyst_files",
    "analyst_inventory_exclusions",
    "analyst_provenance_units",
    "analyst_chunks",
    "analyst_model_attempts",
    "analyst_detector_hits",
    "analyst_model_findings",
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
    """Create v2 or upgrade the one frozen empty-v1 development state.

    An existing exact v2 schema is accepted idempotently. Any populated v1 or
    other on-disk state is rejected and is never repaired in place.
    """
    _require_transaction_boundary(conn)
    identity = _identity(conn)
    objects = _user_objects(conn)
    if identity == (APPLICATION_ID, SCHEMA_VERSION):
        validate_schema(conn)
        return
    if identity == (APPLICATION_ID, PREVIOUS_SCHEMA_VERSION):
        validate_v1_migration_candidate(conn)
    elif identity != (0, 0) or objects:
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
        if concurrent_identity == (APPLICATION_ID, PREVIOUS_SCHEMA_VERSION):
            validate_v1_migration_candidate(conn)
            for statement in (
                *_V2_ADDITIONAL_TABLE_DDL, *_V2_ADDITIONAL_INDEX_DDL,
            ):
                conn.execute(statement)
        elif concurrent_identity == (0, 0) and not concurrent_objects:
            for statement in (*_TABLE_DDL, *_INDEX_DDL):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)"
            )
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        else:
            raise AnalystSchemaError("database changed during schema initialization")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        validate_schema(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise AnalystSchemaError("new schema failed foreign-key validation")
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]) != "ok":
            raise AnalystSchemaError("new schema failed SQLite quick_check")
        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            raise AnalystSchemaError("SQLite foreign-key enforcement was not restored")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def validate_schema(conn: sqlite3.Connection) -> None:
    """Read and exactly validate schema v2 without mutating the database."""
    _validate_schema_version(conn, SCHEMA_VERSION)


def validate_schema_v1(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v1 migration source."""
    _validate_schema_version(conn, PREVIOUS_SCHEMA_VERSION)


def validate_v1_migration_candidate(conn: sqlite3.Connection) -> None:
    """Require exact v1, zero domain rows and the pristine singleton lease."""
    validate_schema_v1(conn)
    for table in _V1_DOMAIN_TABLES:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise AnalystSchemaError(
                "populated Analyst v1 requires an explicit later migration"
            )
    rows = conn.execute(
        "SELECT slot,generation,run_id,owner_token,pid,start_ticks,boot_id,"
        "heartbeat_monotonic_ns,claimed_at_utc,heartbeat_at_utc "
        "FROM analyst_gpu_lease"
    ).fetchall()
    expected = (1, 0, None, None, None, None, None, None, None, None)
    if len(rows) != 1 or tuple(rows[0]) != expected:
        raise AnalystSchemaError(
            "Analyst v1 migration requires the unowned generation-zero lease"
        )


def _validate_schema_version(conn: sqlite3.Connection, version: int) -> None:
    if _identity(conn) != (APPLICATION_ID, version):
        raise AnalystSchemaError(
            f"Analyst database identity or schema version is not v{version}"
        )
    actual = _schema_snapshot(conn)
    expected = _expected_snapshot(version)
    if actual != expected:
        raise AnalystSchemaError(f"Analyst v{version} schema signature does not match")
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
    if version == SCHEMA_VERSION:
        _validate_v2_rows(conn)


def _validate_v2_rows(conn: sqlite3.Connection) -> None:
    missing_schedule = conn.execute(
        "SELECT 1 FROM analyst_runs r LEFT JOIN analyst_ollama_schedule s "
        "ON s.run_id=r.run_id WHERE s.run_id IS NULL LIMIT 1"
    ).fetchone()
    if missing_schedule is not None:
        raise AnalystSchemaError("Analyst run is missing its Ollama schedule")
    wrong_chunk = conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts o JOIN analyst_chunks c "
        "ON c.chunk_id=o.chunk_id JOIN analyst_files f ON f.file_id=c.file_id "
        "WHERE f.run_id!=o.run_id LIMIT 1"
    ).fetchone()
    if wrong_chunk is not None:
        raise AnalystSchemaError("Ollama contact chunk belongs to another run")
    wrong_attempt = conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts o "
        "JOIN analyst_model_attempts a ON a.attempt_id=o.attempt_id "
        "WHERE a.chunk_id!=o.chunk_id OR a.attempt_no!=o.semantic_attempt_no LIMIT 1"
    ).fetchone()
    if wrong_attempt is not None:
        raise AnalystSchemaError("Ollama contact attempt does not match its slot")
    wrong_attempt_state = conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts o "
        "JOIN analyst_model_attempts a ON a.attempt_id=o.attempt_id WHERE "
        "(o.state='success' AND a.state NOT IN "
        "('dispatching','valid','orphaned_unknown','cancelled_unverified')) OR "
        "(o.state='model_invalid' AND a.state!='schema_invalid') OR "
        "(o.state='request_timeout' AND a.state!='model_timeout') OR "
        "(o.state IN ('transport_unavailable','protocol_violation',"
        "'response_limit','identity_mismatch') "
        "AND a.state!='model_transport_error') OR "
        "(o.state='cancelled_unverified' AND a.state!='cancelled_unverified') OR "
        "(o.state='orphaned_unknown' AND a.state!='orphaned_unknown') LIMIT 1"
    ).fetchone()
    if wrong_attempt_state is not None:
        raise AnalystSchemaError("Ollama contact outcome contradicts its attempt")
    excess_controls = conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts WHERE kind!='chat' "
        "GROUP BY run_id HAVING count(*)>? LIMIT 1",
        (MAX_CONTROL_CONTACTS_PER_RUN,),
    ).fetchone()
    excess_chats = conn.execute(
        "SELECT 1 FROM analyst_ollama_contacts WHERE kind='chat' "
        "GROUP BY chunk_id HAVING count(*)>? LIMIT 1",
        (MAX_CHAT_CONTACTS_PER_CHUNK,),
    ).fetchone()
    if excess_controls is not None or excess_chats is not None:
        raise AnalystSchemaError("Ollama contact evidence exceeds its frozen bound")
    _validate_contact_schedule_history(conn)
    _validate_contact_and_attempt_ids(conn)


def _validate_contact_and_attempt_ids(conn: sqlite3.Connection) -> None:
    for row in conn.execute(
        "SELECT contact_id,run_id,contact_no,kind,chunk_id,semantic_attempt_no,"
        "request_sha256,lease_generation FROM analyst_ollama_contacts"
    ).fetchall():
        expected = hashlib.sha256("\0".join((
            str(row[1]),
            str(int(row[2])),
            str(row[3]),
            "" if row[4] is None else str(int(row[4])),
            "" if row[5] is None else str(int(row[5])),
            str(row[6]),
            str(int(row[7])),
        )).encode("utf-8")).hexdigest()
        if str(row[0]) != expected:
            raise AnalystSchemaError("Ollama contact id is not deterministic")
    for row in conn.execute(
        "SELECT attempt_id,chunk_id,attempt_no,request_sha256 "
        "FROM analyst_model_attempts"
    ).fetchall():
        expected = hashlib.sha256(
            f"{int(row[1])}\0{int(row[2])}\0{str(row[3])}".encode("ascii")
        ).hexdigest()
        if str(row[0]) != expected:
            raise AnalystSchemaError("model attempt id is not deterministic")


def _validate_contact_schedule_history(conn: sqlite3.Connection) -> None:
    from .phase2_contract import HEALTH_REQUEST_SHA256

    expected_control_hashes = {
        "version": VERSION_REQUEST_SHA256,
        "tags": TAGS_REQUEST_SHA256,
        "ps": PS_REQUEST_SHA256,
        "cancellation_health": HEALTH_REQUEST_SHA256,
    }
    controls = conn.execute(
        "SELECT kind,request_sha256 FROM analyst_ollama_contacts WHERE kind!='chat'"
    ).fetchall()
    if any(
        str(row[1]) != expected_control_hashes.get(str(row[0]))
        for row in controls
    ):
        raise AnalystSchemaError("Ollama control request identity is invalid")
    schedules = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT run_id,consecutive_failures FROM analyst_ollama_schedule"
        ).fetchall()
    }
    histories: dict[str, list[tuple[object, ...]]] = {}
    for row in conn.execute(
        "SELECT run_id,contact_no,kind,state,resource_failures_before,"
        "resource_failures_after FROM analyst_ollama_contacts "
        "ORDER BY run_id,contact_no"
    ).fetchall():
        histories.setdefault(str(row[0]), []).append(tuple(row[1:]))
    for run_id, expected_final in schedules.items():
        prior = 0
        rows = histories.get(run_id, [])
        for expected_no, row in enumerate(rows, start=1):
            contact_no, kind, state, before, after = row
            if int(contact_no) != expected_no or int(before) != prior:
                raise AnalystSchemaError(
                    "Ollama contact history is not contiguous or replayable"
                )
            if str(state) == ContactStatus.DISPATCHING.value:
                if after is not None:
                    raise AnalystSchemaError("dispatching contact has terminal counters")
                if expected_no != len(rows):
                    raise AnalystSchemaError(
                        "dispatching Ollama contact is not the final contact"
                    )
                continue
            if str(state) == ContactStatus.RESOURCE_BUSY.value:
                expected_after = min(prior + 1, 6)
            elif (
                str(kind) in {
                    ContactKind.CHAT.value,
                    ContactKind.CANCELLATION_HEALTH.value,
                }
                and str(state) in {
                    ContactStatus.SUCCESS.value,
                    ContactStatus.MODEL_INVALID.value,
                }
            ):
                expected_after = 0
            else:
                expected_after = prior
            if after is None or int(after) != expected_after:
                raise AnalystSchemaError("Ollama contact resource history is invalid")
            prior = expected_after
        if prior != expected_final:
            raise AnalystSchemaError("Ollama schedule is not derived from contact history")
    _validate_health_barrier_history(conn)


def _validate_health_barrier_history(conn: sqlite3.Connection) -> None:
    histories: dict[str, list[tuple[str, str, str | None]]] = {}
    for row in conn.execute(
        "SELECT o.run_id,o.kind,o.state,a.state FROM analyst_ollama_contacts o "
        "LEFT JOIN analyst_model_attempts a ON a.attempt_id=o.attempt_id "
        "ORDER BY o.run_id,o.contact_no"
    ).fetchall():
        histories.setdefault(str(row[0]), []).append((
            str(row[1]),
            str(row[2]),
            None if row[3] is None else str(row[3]),
        ))
    ambiguous_contacts = {
        ContactStatus.REQUEST_TIMEOUT.value,
        ContactStatus.TRANSPORT_UNAVAILABLE.value,
        ContactStatus.CANCELLED_UNVERIFIED.value,
        ContactStatus.ORPHANED_UNKNOWN.value,
    }
    ambiguous_attempts = {"orphaned_unknown", "cancelled_unverified"}
    answered = {
        ContactStatus.SUCCESS.value,
        ContactStatus.MODEL_INVALID.value,
    }
    for rows in histories.values():
        unresolved = False
        for kind, status, attempt_state in rows:
            if kind == ContactKind.CHAT.value:
                if unresolved:
                    raise AnalystSchemaError(
                        "scored chat bypassed its recovery-health barrier"
                    )
                unresolved = (
                    status in ambiguous_contacts
                    or (
                        status == ContactStatus.SUCCESS.value
                        and attempt_state in ambiguous_attempts
                    )
                )
            elif (
                kind == ContactKind.CANCELLATION_HEALTH.value
                and status in answered
            ):
                unresolved = False


def validate_runtime_schema(conn: sqlite3.Connection) -> None:
    """Validate constant-cost schema identity for an already audited sidecar."""
    if _identity(conn) != (APPLICATION_ID, SCHEMA_VERSION):
        raise AnalystSchemaError("Analyst database identity or schema version is not v2")
    objects = tuple(
        (kind, name, _normalize_sql(sql))
        for kind, name, sql in _user_objects(conn)
    )
    if objects != _expected_snapshot(SCHEMA_VERSION).objects:
        raise AnalystSchemaError("Analyst v2 runtime schema signature does not match")
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


@lru_cache(maxsize=2)
def _expected_snapshot(version: int = SCHEMA_VERSION) -> _SchemaSnapshot:
    if version == PREVIOUS_SCHEMA_VERSION:
        table_ddl, index_ddl = _V1_TABLE_DDL, _V1_INDEX_DDL
    elif version == SCHEMA_VERSION:
        table_ddl, index_ddl = _TABLE_DDL, _INDEX_DDL
    else:
        raise ValueError("unsupported Analyst schema snapshot version")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in (*table_ddl, *index_ddl):
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
    "KNOWN_SCHEMA_VERSIONS",
    "OLLAMA_CONTACT_KINDS",
    "OLLAMA_CONTACT_STATES",
    "OLLAMA_SCHEDULE_STATES",
    "PREVIOUS_SCHEMA_VERSION",
    "RESOURCE_BACKOFF_SECONDS",
    "SCHEMA_VERSION",
    "AnalystSchemaError",
    "initialize_schema",
    "validate_runtime_schema",
    "validate_schema",
    "validate_schema_v1",
    "validate_v1_migration_candidate",
]
