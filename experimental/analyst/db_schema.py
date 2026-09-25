"""Exact versioned SQLite schemas for durable Analyst state.

This module owns schema identity and validation only. Connection policy, file
creation, transaction retry, and state transitions belong to the store layer.
"""

from __future__ import annotations

import hashlib
import json
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
V1_SCHEMA_VERSION: Final = 1
V2_SCHEMA_VERSION: Final = 2
V3_SCHEMA_VERSION: Final = 3
V4_SCHEMA_VERSION: Final = 4
V5_SCHEMA_VERSION: Final = 5
V6_SCHEMA_VERSION: Final = 6
V7_SCHEMA_VERSION: Final = 7
V8_SCHEMA_VERSION: Final = 8
V9_SCHEMA_VERSION: Final = 9
PREVIOUS_SCHEMA_VERSION: Final = V9_SCHEMA_VERSION
SCHEMA_VERSION: Final = 10
KNOWN_SCHEMA_VERSIONS: Final = (
    V1_SCHEMA_VERSION, V2_SCHEMA_VERSION, V3_SCHEMA_VERSION, V4_SCHEMA_VERSION,
    V5_SCHEMA_VERSION, V6_SCHEMA_VERSION, V7_SCHEMA_VERSION, V8_SCHEMA_VERSION,
    V9_SCHEMA_VERSION, SCHEMA_VERSION,
)

RUN_STATES: Final = (
    "ready", "running", "cancel_requested", "cancelled_pending_resume",
    "interrupted", "finalizing", "complete", "abandoned",
)
RUN_MODES: Final = ("fast", "deep")
# N1: the two transports of the remote-backends contract section 3.  Only
# "ollama" is reachable until N2 ships the adapter and N3 the policy.
BACKEND_KINDS: Final = ("ollama", "openai")
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
#: The contact states as frozen into the v2, v5 and v6 table DDL. Those literals
#: must never move when ContactStatus grows, or every historical schema snapshot
#: would change underneath us. v9's rebuilt tables use OLLAMA_CONTACT_STATES.
_LEGACY_CONTACT_STATES: Final = (
    "dispatching", "success", "model_invalid", "cancelled_unverified",
    "request_timeout", "resource_busy", "transport_unavailable",
    "protocol_violation", "response_limit", "identity_mismatch",
    "orphaned_unknown",
)
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
        state TEXT NOT NULL CHECK(state IN ({_values(_LEGACY_CONTACT_STATES)})),
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
_V3_ADDITIONAL_DDL: Final = (
    "ALTER TABLE analyst_files ADD COLUMN device_high_bit INTEGER "
    "NOT NULL DEFAULT 0 CHECK(device_high_bit IN (0,1))",
    "ALTER TABLE analyst_files ADD COLUMN inode_high_bit INTEGER "
    "NOT NULL DEFAULT 0 CHECK(inode_high_bit IN (0,1))",
)

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

_V4_ADDITIONAL_DDL: Final = (
    """CREATE TABLE analyst_read (
        run_id TEXT PRIMARY KEY REFERENCES analyst_runs(run_id),
        report_schema_version INTEGER NOT NULL CHECK(report_schema_version >= 1),
        read_mode TEXT NOT NULL CHECK(read_mode IN ('quick','full')),
        risk_level TEXT NOT NULL CHECK(risk_level IN ('HIGH','MED','LOW')),
        host_summary TEXT NOT NULL,
        likely_owner TEXT,
        contacts_json TEXT NOT NULL,
        files_read INTEGER NOT NULL CHECK(files_read >= 0),
        files_total INTEGER NOT NULL CHECK(files_total >= 0),
        flagged_files INTEGER NOT NULL CHECK(flagged_files >= 0),
        created_at_utc TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE analyst_read_exposures (
        run_id TEXT NOT NULL REFERENCES analyst_runs(run_id),
        ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
        severity TEXT NOT NULL CHECK(severity IN ('HIGH','MED','LOW')),
        text TEXT NOT NULL,
        PRIMARY KEY (run_id, ordinal)
    ) STRICT""",
    "ALTER TABLE analyst_detector_hits ADD COLUMN fact_rank TEXT "
    "CHECK(fact_rank IS NULL OR fact_rank IN ('HIGH','MED','low'))",
    "ALTER TABLE analyst_model_findings ADD COLUMN fact_rank TEXT "
    "CHECK(fact_rank IS NULL OR fact_rank IN ('HIGH','MED','low'))",
    "CREATE INDEX idx_analyst_read_risk ON analyst_read(risk_level)",
)

_V5_ADDITIONAL_DDL: Final = (
    f"""CREATE TABLE analyst_read_contact (
        contact_id TEXT PRIMARY KEY CHECK({_LOWER_SHA.format('contact_id')}),
        run_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 2),
        request_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('request_sha256')}),
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        state TEXT NOT NULL CHECK(state IN ({_values(_LEGACY_CONTACT_STATES)})),
        charged_at_utc TEXT NOT NULL
            CHECK(length(charged_at_utc) BETWEEN 1 AND 40),
        finished_at_utc TEXT
            CHECK(finished_at_utc IS NULL OR length(finished_at_utc) BETWEEN 1 AND 40),
        resource_failures_before INTEGER NOT NULL
            CHECK(resource_failures_before BETWEEN 0 AND 6),
        resource_failures_after INTEGER
            CHECK(resource_failures_after IS NULL
                  OR resource_failures_after BETWEEN 0 AND 6),
        FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
        UNIQUE(run_id, attempt_no),
        CHECK((state='dispatching' AND finished_at_utc IS NULL)
              OR (state!='dispatching' AND finished_at_utc IS NOT NULL))
    ) STRICT""",
    "CREATE INDEX idx_analyst_read_contact_run ON analyst_read_contact(run_id)",
)

_V6_ADDITIONAL_DDL: Final = (
    f"""CREATE TABLE analyst_discovery_contact (
        contact_id TEXT PRIMARY KEY CHECK({_LOWER_SHA.format('contact_id')}),
        contact_no INTEGER NOT NULL CHECK(contact_no > 0),
        endpoint TEXT NOT NULL,
        request_sha256 TEXT NOT NULL CHECK({_LOWER_SHA.format('request_sha256')}),
        state TEXT NOT NULL CHECK(state IN ({_values(_LEGACY_CONTACT_STATES)})),
        models_found INTEGER CHECK(models_found IS NULL OR models_found >= 0),
        charged_at_utc TEXT NOT NULL,
        finished_at_utc TEXT,
        UNIQUE(contact_no),
        CHECK((state='dispatching' AND finished_at_utc IS NULL)
              OR (state!='dispatching' AND finished_at_utc IS NOT NULL))
    ) STRICT""",
    f"""CREATE TABLE analyst_discovered_model (
        endpoint TEXT NOT NULL,
        model_tag TEXT NOT NULL CHECK(length(model_tag) > 0),
        model_digest TEXT NOT NULL CHECK({_LOWER_SHA.format('model_digest')}),
        first_seen_utc TEXT NOT NULL,
        last_seen_utc TEXT NOT NULL,
        PRIMARY KEY(endpoint, model_tag)
    ) STRICT""",
    "CREATE INDEX idx_analyst_discovery_contact_endpoint ON "
    "analyst_discovery_contact(endpoint,contact_no)",
    "CREATE INDEX idx_analyst_discovered_model_endpoint ON "
    "analyst_discovered_model(endpoint)",
)

_V7_ADDITIONAL_DDL: Final = (
    f"""CREATE TABLE analyst_llm_profile (
        profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 1 AND 64),
        scheme TEXT NOT NULL CHECK(scheme IN ('http','https')),
        host TEXT NOT NULL CHECK(length(host) BETWEEN 1 AND 253),
        port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
        backend_kind TEXT NOT NULL
            CHECK(backend_kind IN ({_values(BACKEND_KINDS)})),
        backend_detected INTEGER NOT NULL DEFAULT 0
            CHECK(backend_detected IN (0,1)),
        keymaster_key_id INTEGER
            CHECK(keymaster_key_id IS NULL OR keymaster_key_id > 0),
        cert_fingerprint TEXT
            CHECK(cert_fingerprint IS NULL
                  OR ({_LOWER_SHA.format('cert_fingerprint')})),
        plaintext_ack INTEGER NOT NULL DEFAULT 0 CHECK(plaintext_ack IN (0,1)),
        consent_muted INTEGER NOT NULL DEFAULT 0 CHECK(consent_muted IN (0,1)),
        created_at_utc TEXT NOT NULL
            CHECK(length(created_at_utc) BETWEEN 1 AND 40),
        last_used_utc TEXT
            CHECK(last_used_utc IS NULL OR length(last_used_utc) BETWEEN 1 AND 40),
        UNIQUE(scheme,host,port)
    ) STRICT""",
    "ALTER TABLE analyst_runs ADD COLUMN profile_id INTEGER "
    "REFERENCES analyst_llm_profile(profile_id)",
    f"ALTER TABLE analyst_runs ADD COLUMN backend_kind TEXT "
    f"CHECK(backend_kind IS NULL OR backend_kind IN ({_values(BACKEND_KINDS)}))",
    "CREATE INDEX idx_analyst_runs_profile ON analyst_runs(profile_id)",
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
    """Create the current schema, or narrowly upgrade an exact known one.

    Every accepted upgrade source is named by ``MIGRATABLE_VERSIONS`` and the
    DDL comes from ``_LADDER``, so a new schema version needs no change here.

    V1 remains restricted to the frozen pristine development state. Later
    migration sources must be exact and idle.
    """
    _require_transaction_boundary(conn)
    identity = _identity(conn)
    objects = _user_objects(conn)
    if identity == (APPLICATION_ID, SCHEMA_VERSION):
        validate_schema(conn)
        return
    try:
        if identity[0] == APPLICATION_ID and identity[1] in MIGRATABLE_VERSIONS:
            validate_migration_candidate(conn, identity[1])
        elif identity != (0, 0) or objects:
            raise AnalystSchemaError(
                "refusing to initialize a nonempty, partial, foreign, or "
                "versioned database"
            )
    except AnalystSchemaError:
        # Another initializer may atomically promote the schema between the
        # identity read and the exact read-only snapshot audit above.
        if _identity(conn) == (APPLICATION_ID, SCHEMA_VERSION):
            validate_schema(conn)
            return
        raise

    # A rebuild step drops a table other rows reference, which SQLite permits
    # only with enforcement off. The pragma is a no-op inside a transaction, so
    # the mode is chosen here and restored after COMMIT. foreign_key_check still
    # gates the COMMIT in both modes.
    source = identity[1] if identity[0] == APPLICATION_ID else None
    rebuilding = _requires_foreign_keys_off(source)
    wanted = 0 if rebuilding else 1
    conn.execute(f"PRAGMA foreign_keys={'OFF' if rebuilding else 'ON'}")
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != wanted:
        raise AnalystSchemaError("SQLite foreign-key enforcement is unavailable")
    try:
        conn.execute("BEGIN IMMEDIATE")
        concurrent_identity = _identity(conn)
        concurrent_objects = _user_objects(conn)
        if concurrent_identity == (APPLICATION_ID, SCHEMA_VERSION):
            validate_schema(conn)
            conn.execute("COMMIT")
            return
        if (
            concurrent_identity[0] == APPLICATION_ID
            and concurrent_identity[1] in MIGRATABLE_VERSIONS
        ):
            source = concurrent_identity[1]
            validate_migration_candidate(conn, source)
            for statement in _ddl_after(source):
                conn.execute(statement)
        elif concurrent_identity == (0, 0) and not concurrent_objects:
            # _TABLE_DDL and _INDEX_DDL are read here at call time so tests can
            # monkeypatch them to force a mid-DDL failure.
            for statement in (*_TABLE_DDL, *_INDEX_DDL):
                conn.execute(statement)
            for statement in _ddl_after(V2_SCHEMA_VERSION):
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
        if rebuilding:
            # A rebuild drops a table, leaving its pages on the freelist. Reclaim
            # them so a database built through the ladder is byte-for-byte what a
            # database built directly would be. VACUUM cannot run in a
            # transaction, so it goes here, after COMMIT.
            conn.execute("VACUUM")
        conn.execute("PRAGMA foreign_keys=ON")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            raise AnalystSchemaError("SQLite foreign-key enforcement was not restored")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def validate_schema(conn: sqlite3.Connection) -> None:
    """Read and exactly validate schema v7 without mutating the database."""
    _validate_schema_version(conn, SCHEMA_VERSION)


def validate_schema_v1(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v1 migration source."""
    _validate_schema_version(conn, V1_SCHEMA_VERSION)


def validate_schema_v2(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v2 migration source."""
    _validate_schema_version(conn, V2_SCHEMA_VERSION)


def validate_schema_v3(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v3 migration source."""
    _validate_schema_version(conn, V3_SCHEMA_VERSION)


def validate_schema_v4(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v4 migration source."""
    _validate_schema_version(conn, V4_SCHEMA_VERSION)


def validate_schema_v5(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v5 migration source."""
    _validate_schema_version(conn, V5_SCHEMA_VERSION)


def validate_schema_v6(conn: sqlite3.Connection) -> None:
    """Read and exactly validate the frozen v6 migration source."""
    _validate_schema_version(conn, V6_SCHEMA_VERSION)


def _validate_v1_pristine(conn: sqlite3.Connection) -> None:
    """Require zero domain rows and the pristine singleton lease.

    v1 is the one version admitted only when it is untouched: the frozen
    development state, not merely an idle one.
    """
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


def validate_migration_candidate(conn: sqlite3.Connection, version: int) -> None:
    """Require an exact known version with no in-flight durable work.

    One entry point for every upgrade source, so adding a schema version needs
    no new validator here and no new branch in any caller.
    """
    if version not in MIGRATABLE_VERSIONS:
        raise AnalystSchemaError(
            f"Analyst v{version} is not a known migration source"
        )
    _validate_schema_version(conn, version)
    if version == V1_SCHEMA_VERSION:
        _validate_v1_pristine(conn)
        return
    for query in _idle_queries_through(version):
        if conn.execute(query).fetchone() is not None:
            raise AnalystSchemaError(
                f"Analyst v{version} migration requires idle durable state"
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
    for step in _steps_through(version):
        if step.row_validator is not None:
            step.row_validator(conn)


def _validate_v4_rows(conn: sqlite3.Connection) -> None:
    invalid = conn.execute(
        "SELECT 1 FROM analyst_read WHERE report_schema_version < 1 "
        "OR read_mode NOT IN ('quick','full') "
        "OR risk_level NOT IN ('HIGH','MED','LOW') "
        "OR files_read < 0 OR files_total < 0 OR flagged_files < 0 LIMIT 1"
    ).fetchone()
    if invalid is not None:
        raise AnalystSchemaError("Analyst read projection domains are invalid")
    invalid_exposure = conn.execute(
        "SELECT 1 FROM analyst_read_exposures "
        "WHERE ordinal NOT BETWEEN 1 AND 5 "
        "OR severity NOT IN ('HIGH','MED','LOW') LIMIT 1"
    ).fetchone()
    if invalid_exposure is not None:
        raise AnalystSchemaError("Analyst read exposure severity is invalid")
    invalid_rank = conn.execute(
        "SELECT 1 FROM analyst_detector_hits WHERE fact_rank IS NOT NULL "
        "AND fact_rank NOT IN ('HIGH','MED','low') UNION ALL "
        "SELECT 1 FROM analyst_model_findings WHERE fact_rank IS NOT NULL "
        "AND fact_rank NOT IN ('HIGH','MED','low') LIMIT 1"
    ).fetchone()
    if invalid_rank is not None:
        raise AnalystSchemaError("Analyst fact rank is invalid")
    for row in conn.execute("SELECT contacts_json FROM analyst_read").fetchall():
        body = str(row[0])
        try:
            contacts = json.loads(body)
        except (TypeError, ValueError):
            contacts = None
        if not isinstance(contacts, list) or any(
            not isinstance(contact, str) for contact in contacts
        ):
            raise AnalystSchemaError("Analyst read contacts are not a JSON string array")
        canonical = json.dumps(
            contacts, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        )
        if body != canonical:
            raise AnalystSchemaError("Analyst read contacts are not canonical JSON")


def _validate_v3_rows(conn: sqlite3.Connection) -> None:
    invalid = conn.execute(
        "SELECT 1 FROM analyst_files WHERE device_high_bit NOT IN (0,1) "
        "OR inode_high_bit NOT IN (0,1) LIMIT 1"
    ).fetchone()
    if invalid is not None:
        raise AnalystSchemaError("Analyst file identity high bits are invalid")


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


# ---------------------------------------------------------------------------
# The schema ladder.
#
# One ordered record per version: what that version adds, the in-flight check
# its new tables need, and its row-level validator.  Every version-aware site
# in this module and in store.py derives from this tuple, so adding a schema
# version is one appended step plus its DDL constant.
#
# It lives here rather than beside the DDL constants only because the row
# validators above must be defined first.
# ---------------------------------------------------------------------------


#: Identity kinds a run may record (contract 6.1).
IDENTITY_KINDS: Final = ("digest", "reported")

#: Columns v7 added to analyst_runs by ALTER, which a rebuild must carry.
_V7_RUN_COLUMNS: Final = (
    "profile_id INTEGER REFERENCES analyst_llm_profile(profile_id)",
    f"backend_kind TEXT "
    f"CHECK(backend_kind IS NULL OR backend_kind IN ({_values(BACKEND_KINDS)}))",
)

#: The reported-identity record of contract 6.1, plus the kind discriminator.
_V8_RUN_COLUMNS: Final = (
    f"identity_kind TEXT CHECK(identity_kind IS NULL "
    f"OR identity_kind IN ({_values(IDENTITY_KINDS)}))",
    "model_path TEXT",
    "model_n_params INTEGER CHECK(model_n_params IS NULL OR model_n_params > 0)",
    "model_size_bytes INTEGER CHECK(model_size_bytes IS NULL OR model_size_bytes > 0)",
    "model_ftype TEXT",
    "model_n_vocab INTEGER CHECK(model_n_vocab IS NULL OR model_n_vocab > 0)",
    "model_n_ctx INTEGER CHECK(model_n_ctx IS NULL OR model_n_ctx > 0)",
    "model_n_ctx_train INTEGER "
    "CHECK(model_n_ctx_train IS NULL OR model_n_ctx_train > 0)",
    "server_fingerprint TEXT",
)

#: A reported identity has no cryptographic digest; a digest identity must have one.
#: Rows written before v8 carry a NULL kind and keep their digest.
_V8_IDENTITY_CHECK: Final = (
    "CHECK((identity_kind IS NULL AND model_digest IS NOT NULL)"
    " OR (identity_kind='digest' AND model_digest IS NOT NULL)"
    " OR (identity_kind='reported' AND model_digest IS NULL))"
)


def _rebuilt_analyst_runs_ddl(table: str) -> str:
    """Return the post-v8 analyst_runs definition under a supplied table name.

    Derived from the frozen v1 literal so the column bodies have a single
    source. The v8 snapshot digest in the ladder guardrail pins the result.
    """
    base = _V1_TABLE_DDL[0]
    old_digest = f"model_digest TEXT NOT NULL CHECK({_LOWER_SHA.format('model_digest')})"
    new_digest = (
        "model_digest TEXT "
        f"CHECK(model_digest IS NULL OR ({_LOWER_SHA.format('model_digest')}))"
    )
    if base.count(old_digest) != 1:
        raise AnalystSchemaError("v1 analyst_runs digest column moved")
    body = base.replace(old_digest, new_digest)
    body = body.replace("CREATE TABLE analyst_runs (", f"CREATE TABLE {table} (", 1)
    # Column definitions must precede table-level constraints, so the new
    # columns go in before the first CHECK and the identity rule goes last.
    added = ",\n        ".join((*_V7_RUN_COLUMNS, *_V8_RUN_COLUMNS))
    first_check = "        CHECK((isolation_mode='strict'"
    tail = "\n    ) STRICT"
    if body.count(first_check) != 1 or body.count(tail) != 1:
        raise AnalystSchemaError("v1 analyst_runs layout moved")
    body = body.replace(first_check, f"        {added},\n{first_check}", 1)
    return body.replace(tail, f",\n        {_V8_IDENTITY_CHECK}{tail}", 1)


#: Columns carried across the rebuild: everything analyst_runs held at v7.
_V7_RUN_COLUMN_NAMES: Final = tuple(
    line.split()[0]
    for line in (
        "run_id", "state", "revision", "created_at_utc", "updated_at_utc",
        "finished_at_utc", "completion_code", "mode", "source_mode", "source_root",
        "output_root", "source_identity_json", "source_identity_sha256",
        "report_label", "host_type", "protocol_server_id", "ip_address", "port",
        "extract_summary_row_id", "model_tag", "model_digest", "worksheet_version",
        "prompt_sha256", "response_schema_sha256", "detector_rules_version",
        "detector_rules_sha256", "parser_bundle_json", "parser_bundle_sha256",
        "chunk_chars", "overlap_chars", "num_ctx", "num_predict", "isolation_mode",
        "reduced_isolation_ack", "cancel_requested_at_utc", "finalization_token",
        "report_manifest_sha256", "profile_id", "backend_kind",
    )
)

# v8 rebuilds analyst_runs: SQLite cannot relax NOT NULL or a CHECK in place.
# This is the first non-additive step, so it runs with foreign_keys OFF, per
# SQLite's documented table-rebuild procedure. initialize_schema still runs
# PRAGMA foreign_key_check and quick_check before COMMIT.
_V8_ADDITIONAL_DDL: Final = (
    _rebuilt_analyst_runs_ddl("analyst_runs_v8"),
    f"INSERT INTO analyst_runs_v8({','.join(_V7_RUN_COLUMN_NAMES)}) "
    f"SELECT {','.join(_V7_RUN_COLUMN_NAMES)} FROM analyst_runs",
    "DROP TABLE analyst_runs",
    "ALTER TABLE analyst_runs_v8 RENAME TO analyst_runs",
    # Dropping the table dropped its indexes; recreate every one, v1 and v7.
    "CREATE INDEX idx_analyst_runs_state_updated ON analyst_runs(state,updated_at_utc,run_id)",
    "CREATE INDEX idx_analyst_runs_host ON analyst_runs(host_type,protocol_server_id,created_at_utc,run_id)",
    "CREATE INDEX idx_analyst_runs_endpoint ON analyst_runs(ip_address,port,created_at_utc,run_id)",
    "CREATE INDEX idx_analyst_runs_profile ON analyst_runs(profile_id)",
)


def _widen_contact_states(ddl: str, table: str) -> str:
    """Return one contact table's DDL with the current state set, under a temp name."""
    legacy = f"CHECK(state IN ({_values(_LEGACY_CONTACT_STATES)}))"
    current = f"CHECK(state IN ({_values(OLLAMA_CONTACT_STATES)}))"
    if ddl.count(legacy) != 1:
        raise AnalystSchemaError(f"{table} state CHECK moved")
    return ddl.replace(legacy, current).replace(
        f"CREATE TABLE {table} (", f"CREATE TABLE {table}_v9 (", 1
    )


def _rebuild_contact_table(ddl: str, table: str, indexes: tuple[str, ...]):
    """Return the statements that rebuild one contact table in place.

    Only the state CHECK changes, so the column list is identical and
    ``SELECT *`` copies faithfully.
    """
    return (
        _widen_contact_states(ddl, table),
        f"INSERT INTO {table}_v9 SELECT * FROM {table}",
        f"DROP TABLE {table}",
        f"ALTER TABLE {table}_v9 RENAME TO {table}",
        *indexes,
    )


# v9 widens the contact state CHECK on three tables so context_exceeded and
# configuration_failure can be recorded (decision D20). SQLite cannot widen a
# CHECK in place, so each table is rebuilt; every column is unchanged.
_V9_ADDITIONAL_DDL: Final = (
    *_rebuild_contact_table(
        _V2_ADDITIONAL_TABLE_DDL[0],
        "analyst_ollama_contacts",
        tuple(
            statement for statement in _V2_ADDITIONAL_INDEX_DDL
            if "analyst_ollama_contacts" in statement
        ),
    ),
    *_rebuild_contact_table(
        _V5_ADDITIONAL_DDL[0],
        "analyst_read_contact",
        tuple(
            statement for statement in _V5_ADDITIONAL_DDL
            if statement.lstrip().upper().startswith("CREATE INDEX")
            and "analyst_read_contact" in statement
        ),
    ),
    *_rebuild_contact_table(
        _V6_ADDITIONAL_DDL[0],
        "analyst_discovery_contact",
        tuple(
            statement for statement in _V6_ADDITIONAL_DDL
            if statement.lstrip().upper().startswith("CREATE INDEX")
            and "analyst_discovery_contact" in statement
        ),
    ),
)


@dataclass(frozen=True)
class _SchemaStep:
    """One version of the Analyst sidecar schema."""

    version: int
    #: DDL this version adds on top of the previous one.
    ddl: tuple[str, ...]
    #: "Is durable work in flight?" probe for the table this version introduces.
    idle_query: str | None
    #: Row-level invariants this version introduces.
    row_validator: object | None
    #: True when this step rebuilds a table. SQLite cannot relax NOT NULL or a
    #: CHECK in place, and its documented rebuild procedure requires
    #: foreign_keys OFF for the transaction. Default False: every additive step.
    rebuilds_a_table: bool = False


# v10 records whether a kind-appropriate label sat just before a detected
# value. Only the scanner can know that -- it holds the document text, and the
# report layer holds only the hit -- so the answer has to be written down. It
# is the ONE screening input that cannot be recomputed from (kind, value);
# everything else the screening rules need is in the value itself, which keeps
# those rules free to change without a migration. NULL means "not recorded",
# which is never held against a value.
_V10_ADDITIONAL_DDL: Final = (
    "ALTER TABLE analyst_detector_hits ADD COLUMN labeled INTEGER "
    "CHECK(labeled IS NULL OR labeled IN (0,1))",
)


_LADDER: Final = (
    _SchemaStep(V1_SCHEMA_VERSION, (), None, None),
    _SchemaStep(
        V2_SCHEMA_VERSION,
        (*_V2_ADDITIONAL_TABLE_DDL, *_V2_ADDITIONAL_INDEX_DDL),
        "SELECT 1 FROM analyst_ollama_contacts WHERE state='dispatching' LIMIT 1",
        _validate_v2_rows,
    ),
    _SchemaStep(V3_SCHEMA_VERSION, _V3_ADDITIONAL_DDL, None, _validate_v3_rows),
    _SchemaStep(V4_SCHEMA_VERSION, _V4_ADDITIONAL_DDL, None, _validate_v4_rows),
    _SchemaStep(
        V5_SCHEMA_VERSION,
        _V5_ADDITIONAL_DDL,
        "SELECT 1 FROM analyst_read_contact WHERE state='dispatching' LIMIT 1",
        None,
    ),
    _SchemaStep(
        V6_SCHEMA_VERSION,
        _V6_ADDITIONAL_DDL,
        "SELECT 1 FROM analyst_discovery_contact WHERE state='dispatching' LIMIT 1",
        None,
    ),
    # v7 adds analyst_llm_profile, which holds no in-flight state.
    _SchemaStep(V7_SCHEMA_VERSION, _V7_ADDITIONAL_DDL, None, None),
    # v8 rebuilds analyst_runs so a reported identity can carry no digest.
    _SchemaStep(
        V8_SCHEMA_VERSION, _V8_ADDITIONAL_DDL, None, None, rebuilds_a_table=True,
    ),
    # v9 widens the contact state CHECK on the three contact tables (D20).
    _SchemaStep(
        V9_SCHEMA_VERSION, _V9_ADDITIONAL_DDL, None, None, rebuilds_a_table=True,
    ),
    # v10 records detector label proximity for the screening rules (F2).
    _SchemaStep(SCHEMA_VERSION, _V10_ADDITIONAL_DDL, None, None),
)


def _requires_foreign_keys_off(source_version: int | None) -> bool:
    """Return whether the pending upgrade includes a table rebuild.

    A rebuild drops and recreates a table other rows reference, which SQLite
    only permits with foreign_keys OFF. Everything else migrates with
    enforcement on, and PRAGMA foreign_key_check gates the COMMIT either way.
    """
    floor = -1 if source_version is None else source_version
    return any(
        step.rebuilds_a_table for step in _LADDER if step.version > floor
    )

#: In-flight probes that exist at every version from v2 onward.
_BASE_IDLE_QUERIES: Final = (
    "SELECT 1 FROM analyst_gpu_lease WHERE run_id IS NOT NULL LIMIT 1",
    "SELECT 1 FROM analyst_runs WHERE state IN "
    "('running','cancel_requested','finalizing') LIMIT 1",
    "SELECT 1 FROM analyst_files WHERE work_state='active' LIMIT 1",
    "SELECT 1 FROM analyst_model_attempts WHERE state='dispatching' LIMIT 1",
)

#: Known versions a database may be upgraded *from*.
MIGRATABLE_VERSIONS: Final = tuple(
    step.version for step in _LADDER if step.version != SCHEMA_VERSION
)


def _steps_through(version: int) -> tuple[_SchemaStep, ...]:
    """Return every ladder step up to and including one version."""
    steps = tuple(step for step in _LADDER if step.version <= version)
    if not steps or steps[-1].version != version:
        raise ValueError("unsupported Analyst schema snapshot version")
    return steps


def _ddl_through(version: int) -> tuple[str, ...]:
    """Return the DDL that builds one version from empty, minus the v1 base."""
    return tuple(
        statement
        for step in _steps_through(version)
        for statement in step.ddl
    )


def _ddl_after(version: int) -> tuple[str, ...]:
    """Return the DDL that upgrades one version to the current schema."""
    _steps_through(version)
    return tuple(
        statement
        for step in _LADDER
        if step.version > version
        for statement in step.ddl
    )


def _idle_queries_through(version: int) -> tuple[str, ...]:
    """Return every in-flight probe that applies at one version."""
    return (
        *_BASE_IDLE_QUERIES,
        *(
            step.idle_query
            for step in _steps_through(version)
            if step.idle_query is not None
        ),
    )


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
        raise AnalystSchemaError("Analyst database identity or schema version is not v6")
    objects = tuple(
        (kind, name, _normalize_sql(sql))
        for kind, name, sql in _user_objects(conn)
    )
    if objects != _expected_snapshot(SCHEMA_VERSION).objects:
        raise AnalystSchemaError("Analyst v6 runtime schema signature does not match")
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


@lru_cache(maxsize=len(_LADDER))
def _expected_snapshot(version: int = SCHEMA_VERSION) -> _SchemaSnapshot:
    additional_ddl = _ddl_through(version)
    table_ddl, index_ddl = _V1_TABLE_DDL, _V1_INDEX_DDL
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in (*table_ddl, *index_ddl):
            conn.execute(statement)
        for statement in additional_ddl:
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
    "BACKEND_KINDS",
    "RESOURCE_BACKOFF_SECONDS",
    "SCHEMA_VERSION",
    "V1_SCHEMA_VERSION",
    "V2_SCHEMA_VERSION",
    "V3_SCHEMA_VERSION",
    "V4_SCHEMA_VERSION",
    "V5_SCHEMA_VERSION",
    "V6_SCHEMA_VERSION",
    "V7_SCHEMA_VERSION",
    "V8_SCHEMA_VERSION",
    "AnalystSchemaError",
    "initialize_schema",
    "validate_runtime_schema",
    "validate_schema",
    "validate_schema_v1",
    "validate_schema_v2",
    "validate_schema_v3",
    "validate_schema_v4",
    "validate_schema_v5",
    "validate_schema_v6",
    "validate_migration_candidate",
]
