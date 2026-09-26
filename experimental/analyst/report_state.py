"""Bounded read-only projections for finalizing Analyst reports."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import detectors, report_json
from .lease import LeaseFence
from .models import FileStage, FileTerminal
from .report_contract import (
    CoverageSummary,
    EvidenceKind,
    FindingReportRow,
    InventoryReportRow,
    MAX_REPORT_FILES,
    MAX_REPORT_FINDINGS,
    MAX_REPORT_JSON_FACTS,
    READ_PAGE_ROWS,
    ReportRun,
    ReportSnapshot,
    frozen_counts,
)
from .store import open_connection


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FENCE_WHERE = (
    "generation=? AND run_id=? AND owner_token=? AND pid=? AND start_ticks=? "
    "AND boot_id=? AND heartbeat_monotonic_ns=?"
)


class ReportStateError(RuntimeError):
    """The durable report projection is unavailable or contradictory."""


def load_report_snapshot(
    fence: LeaseFence,
    finalization_token: str,
    *,
    path: Path | None = None,
) -> ReportSnapshot:
    """Load the compact finalizing run identity and coverage in one short read."""
    _require_inputs(fence, finalization_token)
    conn = open_connection(path, read_only=True)
    try:
        row = _require_finalizing(conn, fence, finalization_token)
        discovered = _scalar_count(
            conn, "SELECT count(*) FROM analyst_files WHERE run_id=?", fence.run_id,
            maximum=MAX_REPORT_FILES,
        )
        excluded = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_inventory_exclusions WHERE run_id=?",
            fence.run_id,
            maximum=MAX_REPORT_FILES,
        )
        detector_scanned = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_files WHERE run_id=? AND stage IN "
            "('detector_scanned','selected_for_model','model_reviewed',"
            "'model_response_valid')",
            fence.run_id,
            maximum=MAX_REPORT_FILES,
        )
        selected = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_files WHERE run_id=? "
            "AND selected_for_model=1",
            fence.run_id,
            maximum=MAX_REPORT_FILES,
        )
        model_reviewed = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_files WHERE run_id=? "
            "AND stage IN ('model_reviewed','model_response_valid')",
            fence.run_id,
            maximum=MAX_REPORT_FILES,
        )
        valid_model_chunks = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_chunks c JOIN analyst_files f "
            "ON f.file_id=c.file_id WHERE f.run_id=? "
            "AND f.terminal_code='complete_model_reviewed' "
            "AND c.state='model_response_valid'",
            fence.run_id,
            maximum=MAX_REPORT_FINDINGS,
        )
        detector_hits = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_detector_hits h JOIN analyst_files f "
            "ON f.file_id=h.file_id WHERE f.run_id=?",
            fence.run_id,
            maximum=MAX_REPORT_FINDINGS,
        )
        model_findings = _scalar_count(
            conn,
            "SELECT count(*) FROM analyst_model_findings m "
            "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=? AND f.work_state='terminal' "
            "AND f.terminal_code='complete_model_reviewed'",
            fence.run_id,
            maximum=MAX_REPORT_FINDINGS,
        )
        terminal_counts = _group_counts(
            conn,
            "SELECT terminal_code,count(*) AS n FROM analyst_files "
            "WHERE run_id=? GROUP BY terminal_code ORDER BY terminal_code",
            fence.run_id,
        )
        format_counts = _group_counts(
            conn,
            "SELECT coalesce(format_name,'unidentified') AS name,count(*) AS n "
            "FROM analyst_files WHERE run_id=? GROUP BY name ORDER BY name",
            fence.run_id,
        )
        exclusion_counts = _group_counts(
            conn,
            "SELECT reason AS name,count(*) AS n FROM analyst_inventory_exclusions "
            "WHERE run_id=? GROUP BY reason ORDER BY reason",
            fence.run_id,
        )
        coverage = CoverageSummary(
            discovered, excluded, detector_scanned, selected, model_reviewed,
            valid_model_chunks, detector_hits, model_findings, frozen_counts(terminal_counts),
            frozen_counts(format_counts), frozen_counts(exclusion_counts),
        )
        run = ReportRun(
            run_id=str(row["run_id"]),
            report_label=str(row["report_label"]),
            mode=str(row["mode"]),
            source_mode=str(row["source_mode"]),
            source_root=str(row["source_root"]),
            report_built_at_utc=str(row["report_built_at_utc"] or ""),
            created_at_utc=str(row["created_at_utc"]),
            model_tag=str(row["model_tag"]),
            model_digest=(
                None if row["model_digest"] is None else str(row["model_digest"])
            ),
            worksheet_version=str(row["worksheet_version"]),
            prompt_sha256=str(row["prompt_sha256"]),
            response_schema_sha256=str(row["response_schema_sha256"]),
            detector_rules_version=str(row["detector_rules_version"]),
            detector_rules_sha256=str(row["detector_rules_sha256"]),
            parser_bundle_sha256=str(row["parser_bundle_sha256"]),
            chunk_chars=int(row["chunk_chars"]),
            overlap_chars=int(row["overlap_chars"]),
            num_ctx=int(row["num_ctx"]),
            num_predict=int(row["num_predict"]),
            isolation_mode=str(row["isolation_mode"]),
            reduced_isolation_ack=_strict_bool(row["reduced_isolation_ack"]),
            host_type=_optional_text(row["host_type"]),
            protocol_server_id=_optional_int(row["protocol_server_id"]),
            ip_address=_optional_text(row["ip_address"]),
            port=_optional_int(row["port"]),
            identity_kind=row["identity_kind"] or "digest",
            server_fingerprint=(
                None if row["server_fingerprint"] is None
                else str(row["server_fingerprint"])
            ),
            extract_summary_row_id=_optional_int(row["extract_summary_row_id"]),
        )
        return ReportSnapshot(run, coverage, str(row["output_root"]))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable report state is invalid") from exc
    finally:
        conn.close()


def build_report_json_payload(
    fence: LeaseFence,
    finalization_token: str,
    snapshot: ReportSnapshot,
    *,
    path: Path | None = None,
) -> dict[str, object]:
    """Build the bounded report.json projection from durable finalizing state."""
    _require_inputs(fence, finalization_token)
    if type(snapshot) is not ReportSnapshot:
        raise TypeError("report JSON requires a ReportSnapshot")
    if snapshot.run.run_id != fence.run_id:
        raise ReportStateError("report snapshot does not match the finalizing run")
    conn = open_connection(path, read_only=True)
    try:
        _require_finalizing(conn, fence, finalization_token)
        facts = _load_ranked_facts(conn, fence.run_id)
        flagged_files = _scalar_count(
            conn,
            "SELECT count(*) FROM ("
            "SELECT f.file_id FROM analyst_detector_hits h JOIN analyst_files f "
            "ON f.file_id=h.file_id WHERE f.run_id=? UNION "
            "SELECT f.file_id FROM analyst_model_findings m "
            "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=? "
            "AND f.work_state='terminal' "
            "AND f.terminal_code='complete_model_reviewed')",
            (fence.run_id, fence.run_id),
            maximum=MAX_REPORT_FILES,
        )
        run = snapshot.run
        coverage_summary = snapshot.coverage
        run_meta = report_json.RunMeta(
            run_id=run.run_id,
            report_label=run.report_label,
            read_mode={"fast": "quick", "deep": "full"}[run.mode],
            model_tag=run.model_tag,
            model_digest=run.model_digest,
            identity_kind=run.identity_kind,
            server_fingerprint=run.server_fingerprint,
            created_at_utc=run.created_at_utc,
            files_read=coverage_summary.model_reviewed_files,
            files_total=coverage_summary.discovered_files,
            flagged_files=flagged_files,
            source_root=run.source_root,
            output_root=snapshot.output_root,
            # The run is not complete yet -- the report is written first, so
            # finished_at_utc does not exist. This is the instant finalization
            # began, moments before this payload. It comes from the row and
            # never from a clock: a crashed finalization that resumes has to
            # rebuild this payload byte for byte, because the manifest digest
            # is durable and is compared on resume.
            report_written_at_utc=run.report_built_at_utc,
            detector_rules_version=run.detector_rules_version,
        )
        read = _load_host_read(conn, fence.run_id)
        if read is None:
            read = report_json.build_fallback_read(
                facts,
                files_read=run_meta.files_read,
                files_total=run_meta.files_total,
                flagged_files=run_meta.flagged_files,
            )
        terminal_counts = {
            item.name: item.count for item in coverage_summary.terminal_counts
        }
        coverage = report_json.Coverage(
            discovered=coverage_summary.discovered_files,
            terminal=sum(terminal_counts.values()),
            no_text_layer=terminal_counts.get("no_text_layer", 0),
            parse_failed=sum(
                terminal_counts.get(name, 0)
                for name in (
                    "parse_timeout", "parse_oom", "parse_signal", "parse_error",
                    "parser_output_limit",
                )
            ),
            unsupported=terminal_counts.get("unsupported_format", 0),
        )
        organizations, toll_free, toll_free_total = _load_affiliations(
            conn, fence.run_id,
        )
        return report_json.build_report_json(
            run_meta, read, facts, coverage,
            affiliations=organizations,
            toll_free=toll_free,
            toll_free_total=toll_free_total,
        )
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable report JSON state is invalid") from exc
    finally:
        conn.close()


def load_inventory_page(
    fence: LeaseFence,
    finalization_token: str,
    *,
    after_ordinal: int = -1,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[InventoryReportRow, ...]:
    """Load one stable inventory page ordered by the frozen file ordinal."""
    _require_inputs(fence, finalization_token)
    _require_page(after_ordinal, limit)
    conn = open_connection(path, read_only=True)
    try:
        _require_finalizing(conn, fence, finalization_token)
        rows = conn.execute(
            "SELECT f.file_id,f.ordinal,f.relative_path,f.size,f.sha256,f.stage,"
            "f.terminal_code,f.terminal_detail,f.format_name,f.selected_for_model,"
            "(SELECT count(*) FROM analyst_detector_hits h WHERE h.file_id=f.file_id) "
            "AS detector_hit_count,"
            "(SELECT count(*) FROM analyst_chunks c WHERE c.file_id=f.file_id) "
            "AS chunk_count,"
            "CASE WHEN f.terminal_code='complete_model_reviewed' THEN "
            "(SELECT count(*) FROM analyst_model_findings m JOIN analyst_chunks c "
            "ON c.chunk_id=m.chunk_id WHERE c.file_id=f.file_id) ELSE 0 END "
            "AS model_finding_count FROM analyst_files f "
            "WHERE f.run_id=? AND f.ordinal>? ORDER BY f.ordinal LIMIT ?",
            (fence.run_id, after_ordinal, limit),
        ).fetchall()
        return tuple(decode_inventory_report_row(row) for row in rows)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable inventory report row is invalid") from exc
    finally:
        conn.close()


def load_detector_finding_page(
    fence: LeaseFence,
    finalization_token: str,
    *,
    after_id: int = 0,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[tuple[int, FindingReportRow], ...]:
    """Load one canonical detector-evidence page, retaining the private cursor id."""
    _require_inputs(fence, finalization_token)
    _require_page(after_id, limit, allow_zero=True)
    conn = open_connection(path, read_only=True)
    try:
        _require_finalizing(conn, fence, finalization_token)
        rows = conn.execute(
            "SELECT h.hit_id,h.ordinal,h.kind,h.value,h.start_char,h.end_char,"
            "f.file_id,f.ordinal AS file_ordinal,f.relative_path,f.format_name "
            "FROM analyst_detector_hits h JOIN analyst_files f ON f.file_id=h.file_id "
            "WHERE f.run_id=? AND h.hit_id>? ORDER BY h.hit_id LIMIT ?",
            (fence.run_id, after_id, limit),
        ).fetchall()
        return tuple(
            (int(row["hit_id"]), decode_detector_report_row(row)) for row in rows
        )
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable detector report row is invalid") from exc
    finally:
        conn.close()


def load_model_finding_page(
    fence: LeaseFence,
    finalization_token: str,
    *,
    after_id: int = 0,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[tuple[int, FindingReportRow], ...]:
    """Load one reportable model-evidence page from complete reviewed files only."""
    _require_inputs(fence, finalization_token)
    _require_page(after_id, limit, allow_zero=True)
    conn = open_connection(path, read_only=True)
    try:
        _require_finalizing(conn, fence, finalization_token)
        rows = conn.execute(
            "SELECT m.finding_id,m.ordinal,m.category,m.quote,m.model_offset,"
            "m.canonical_offset,m.canonical_end,m.match_count,m.model_offset_exact,"
            "m.review_state,c.chunk_index,c.start_char,c.document_type,c.subject,"
            "c.assessment,f.file_id,f.ordinal AS file_ordinal,f.relative_path,"
            "f.format_name,(SELECT p.kind FROM analyst_provenance_units p "
            "WHERE p.file_id=f.file_id AND p.start_char<=c.start_char+m.canonical_offset "
            "AND p.end_char>=c.start_char+m.canonical_end ORDER BY p.ordinal LIMIT 1) "
            "AS provenance_kind,(SELECT p.label FROM analyst_provenance_units p "
            "WHERE p.file_id=f.file_id AND p.start_char<=c.start_char+m.canonical_offset "
            "AND p.end_char>=c.start_char+m.canonical_end ORDER BY p.ordinal LIMIT 1) "
            "AS provenance_label FROM analyst_model_findings m "
            "JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
            "JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=? AND f.work_state='terminal' "
            "AND f.terminal_code='complete_model_reviewed' AND m.finding_id>? "
            "ORDER BY m.finding_id LIMIT ?",
            (fence.run_id, after_id, limit),
        ).fetchall()
        return tuple(
            (int(row["finding_id"]), decode_model_report_row(row)) for row in rows
        )
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable model report row is invalid") from exc
    finally:
        conn.close()


def _require_finalizing(
    conn: sqlite3.Connection, fence: LeaseFence, token: str,
) -> sqlite3.Row:
    lease = conn.execute(
        "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND " + _FENCE_WHERE,
        _fence_values(fence),
    ).fetchone()
    row = conn.execute(
        "SELECT * FROM analyst_runs WHERE run_id=? AND state='finalizing' "
        "AND finalization_token=?",
        (fence.run_id, token),
    ).fetchone()
    if lease is None or row is None:
        raise ReportStateError("report lease or finalization token no longer matches")
    return row


def decode_inventory_report_row(row: sqlite3.Row) -> InventoryReportRow:
    selected = row["selected_for_model"]
    return InventoryReportRow(
        int(row["file_id"]), int(row["ordinal"]), str(row["relative_path"]),
        int(row["size"]), str(row["sha256"]), FileStage(str(row["stage"])),
        FileTerminal(str(row["terminal_code"])),
        _optional_text(row["terminal_detail"]), _optional_text(row["format_name"]),
        None if selected is None else _strict_bool(selected),
        int(row["detector_hit_count"]), int(row["chunk_count"]),
        int(row["model_finding_count"]),
    )


def decode_detector_report_row(row: sqlite3.Row) -> FindingReportRow:
    return FindingReportRow(
        EvidenceKind.DETECTOR, int(row["file_id"]), int(row["file_ordinal"]),
        str(row["relative_path"]), str(row["format_name"]), int(row["ordinal"]),
        int(row["start_char"]), int(row["end_char"]),
        detector_kind=str(row["kind"]), detector_value=str(row["value"]),
    )


def decode_model_report_row(row: sqlite3.Row) -> FindingReportRow:
    source_start = int(row["start_char"]) + int(row["canonical_offset"])
    source_end = int(row["start_char"]) + int(row["canonical_end"])
    return FindingReportRow(
        EvidenceKind.MODEL, int(row["file_id"]), int(row["file_ordinal"]),
        str(row["relative_path"]), str(row["format_name"]),
        int(row["chunk_index"]) * 16 + int(row["ordinal"]), source_start, source_end,
        chunk_index=int(row["chunk_index"]), category=str(row["category"]),
        quote=str(row["quote"]), document_type=str(row["document_type"]),
        subject=str(row["subject"]), assessment=str(row["assessment"]),
        model_offset=int(row["model_offset"]),
        model_offset_exact=_strict_bool(row["model_offset_exact"]),
        match_count=int(row["match_count"]), review_state=str(row["review_state"]),
        provenance_kind=_optional_text(row["provenance_kind"]),
        provenance_label=_optional_text(row["provenance_label"]),
    )


def _scalar_count(
    conn: sqlite3.Connection,
    sql: str,
    parameters: str | tuple[object, ...],
    *,
    maximum: int,
) -> int:
    values = (parameters,) if type(parameters) is str else parameters
    row = conn.execute(sql, values).fetchone()
    if row is None:
        raise ReportStateError("report count query returned no row")
    value = int(row[0])
    if value < 0 or value > maximum:
        raise ReportStateError("report count exceeds the frozen bound")
    return value


def _load_host_read(
    conn: sqlite3.Connection, run_id: str,
) -> report_json.HostRead | None:
    row = conn.execute(
        "SELECT report_schema_version,risk_level,host_summary,likely_owner,"
        "contacts_json FROM analyst_read WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    if (
        int(row["report_schema_version"])
        not in report_json.SUPPORTED_REPORT_SCHEMA_VERSIONS
    ):
        raise ReportStateError("durable host read version is unsupported")
    contact_values = json.loads(str(row["contacts_json"]))
    if type(contact_values) is not list:
        raise ReportStateError("durable host read contacts are invalid")
    exposure_rows = conn.execute(
        "SELECT ordinal,severity,text FROM analyst_read_exposures "
        "WHERE run_id=? ORDER BY ordinal",
        (run_id,),
    ).fetchall()
    if tuple(int(item["ordinal"]) for item in exposure_rows) != tuple(
        range(1, len(exposure_rows) + 1)
    ):
        raise ReportStateError("durable host read exposures are not canonical")
    return report_json.HostRead(
        host_summary=str(row["host_summary"]),
        likely_owner=_optional_text(row["likely_owner"]),
        contacts=tuple(contact_values),
        risk_level=str(row["risk_level"]),
        top_exposures=tuple(
            report_json.TopExposure(
                rank=int(item["ordinal"]),
                severity=str(item["severity"]),
                text=str(item["text"]),
            )
            for item in exposure_rows
        ),
    )


#: One notch down. A screened-down fact stays visible and says why.
_DEMOTED = {"HIGH": "MED", "MED": "low", "low": "low"}

#: Where a machine-shaped identifier is usually not an identifier at all.
#: Source code and markup are where a label word sits next to another word --
#: 125 of one corpus's 130 "passport" hits were the word "Number" inside
#: individualApplication.js.
_CODE_SUFFIXES = (".js", ".css", ".json", ".htm", ".html", ".map", ".xml")
#: Paths that announce the file is not real data.
_SPECIMEN_WORDS = (
    "sample", "template", "example", "demo", "dummy", "blank", "practice",
    "worksheet", "placeholder", "mock", "lorem",
)
#: Kinds whose value alone cannot survive a bad context. A demographic term or
#: an email in a template is still a demographic term or an email.
_CONTEXT_SENSITIVE = frozenset({
    "ssn", "card", "routing", "bank_account", "iban", "passport", "dob",
    "phone",
})


def _context_is_unreliable(
    kind: str, relative_path: str, format_name: object,
) -> bool:
    """Return whether where this value was found argues against it.

    Deliberately not "it came from a spreadsheet". A real payroll workbook full
    of real card numbers is exactly the find that matters, and demoting every
    numeric cell would bury it. The spreadsheet problem was long floats, and
    the value rules already refuse those on their digits.
    """
    if kind not in _CONTEXT_SENSITIVE:
        return False
    lowered = relative_path.casefold()
    if lowered.endswith(_CODE_SUFFIXES):
        return True
    return any(word in lowered for word in _SPECIMEN_WORDS)


def _load_affiliations(
    conn: sqlite3.Connection, run_id: str,
) -> tuple[tuple[report_json.Affiliation, ...],
           tuple[report_json.TollFreeContact, ...], int]:
    """Return the organisations this host's documents keep referring to.

    Read straight from the hit table, never from the ranked facts. Contact
    facts rank ``low`` and the fact budget is spent long before it reaches
    them -- a real 1,244-file run put zero emails into report.json -- so
    deriving this from the facts list would silently return nothing.

    An organisation is an email domain that is not a mailbox provider. The
    ranking signal is how many DISTINCT FILES mention it, not how often: one
    product manual in a downloads folder mentions its vendor several times,
    and that is not a relationship. In one real corpus 63 of 77 candidate
    domains appeared in exactly one file.
    """
    domain_files: dict[str, set[int]] = {}
    domain_hits: dict[str, int] = {}
    toll_free_files: dict[str, set[int]] = {}
    toll_free_example: dict[str, str] = {}
    rows = conn.execute(
        "SELECT h.kind,h.value,f.file_id,f.relative_path "
        "FROM analyst_detector_hits h JOIN analyst_files f ON f.file_id=h.file_id "
        "WHERE f.run_id=? AND h.kind IN ('email','phone') "
        "ORDER BY f.ordinal,h.hit_id",
        (run_id,),
    )
    for row in rows:
        kind = str(row["kind"])
        value = str(row["value"])
        screen = detectors.screen_identifier(kind, value)
        if screen.impossible or screen.plausibility != "valid":
            continue
        file_id = int(row["file_id"])
        if kind == "email":
            domain = report_json.email_domain(value)
            if not report_json.is_organizational_domain(domain):
                continue
            domain_files.setdefault(domain, set()).add(file_id)
            domain_hits[domain] = domain_hits.get(domain, 0) + 1
        elif screen.subject == "organizational":
            toll_free_files.setdefault(value, set()).add(file_id)
            toll_free_example.setdefault(value, str(row["relative_path"]))

    organizations = tuple(
        report_json.Affiliation(
            domain=domain, files=len(files), occurrences=domain_hits[domain],
        )
        for domain, files in sorted(
            domain_files.items(), key=lambda item: (-len(item[1]), item[0]),
        )
        if len(files) >= report_json.MIN_AFFILIATION_FILES
    )[:report_json.MAX_AFFILIATIONS]

    numbers = tuple(
        report_json.TollFreeContact(
            value=value, files=len(files), example_file=toll_free_example[value],
        )
        for value, files in sorted(
            toll_free_files.items(), key=lambda item: (-len(item[1]), item[0]),
        )
    )
    return organizations, numbers[:report_json.MAX_TOLL_FREE], len(numbers)


def _load_ranked_facts(
    conn: sqlite3.Connection, run_id: str,
) -> tuple[report_json.GroundedFact, ...]:
    """Return the report's facts, one row per value per file.

    Occurrences of the same value in the same file differ only in provenance,
    which no report surface has a column for, so they rendered as identical
    rows -- and they spent the fact budget that model findings never reached.
    They now fold into one fact carrying a count, keeping the first occurrence
    so ``provenance`` still names a real span.
    """
    # key -> [sort key, field mapping, occurrences]
    collapsed: dict[tuple[str, ...], list] = {}
    counts: dict[tuple[str, str], int] = {}
    rank_order = {"HIGH": 0, "MED": 1, "low": 2}

    def collect(sort_key: tuple[int, int, int, int], **fields: str) -> None:
        key = (
            fields["source"], fields["kind"], fields["category"],
            fields["quote"], fields["file"],
        )
        group = collapsed.get(key)
        if group is not None:
            group[2] += 1
            if sort_key < group[0]:
                group[0] = sort_key
                group[1] = fields
            return
        bucket = (fields["rank"], fields["source"])
        counts[bucket] = counts.get(bucket, 0) + 1
        if counts[bucket] > MAX_REPORT_JSON_FACTS:
            return
        collapsed[key] = [sort_key, fields, 1]
    detector_rows = conn.execute(
        "SELECT h.hit_id,h.kind,h.value,h.start_char,h.end_char,h.labeled,"
        "f.ordinal AS file_ordinal,f.relative_path,f.format_name,"
        "(SELECT p.kind FROM analyst_provenance_units p WHERE p.file_id=f.file_id "
        "AND p.start_char<=h.start_char AND p.end_char>=h.end_char "
        "ORDER BY p.ordinal LIMIT 1) AS provenance_kind,"
        "(SELECT p.label FROM analyst_provenance_units p WHERE p.file_id=f.file_id "
        "AND p.start_char<=h.start_char AND p.end_char>=h.end_char "
        "ORDER BY p.ordinal LIMIT 1) AS provenance_label "
        "FROM analyst_detector_hits h JOIN analyst_files f ON f.file_id=h.file_id "
        "WHERE f.run_id=? ORDER BY f.ordinal,h.hit_id",
        (run_id,),
    )
    for row in detector_rows:
        kind = str(row["kind"])
        category = _detector_category(kind)
        rank = report_json.rank_fact(kind, category, "detector")
        screen = detectors.screen_identifier(
            kind,
            str(row["value"]),
            labeled=None if row["labeled"] is None else bool(row["labeled"]),
        )
        # A hit already on disk may have been written under older detector
        # rules that would refuse it today. It is still recorded evidence, so
        # it is reported as suspect rather than crashing the report or being
        # deleted from it.
        plausibility = "suspect" if screen.impossible else screen.plausibility
        if plausibility == "valid" and _context_is_unreliable(
            kind, str(row["relative_path"]), row["format_name"],
        ):
            plausibility = "suspect"
        if plausibility == "suspect":
            rank = _DEMOTED[rank]
        collect(
            (rank_order[rank], int(row["file_ordinal"]), 0, int(row["hit_id"])),
            kind=kind,
            category=category,
            quote=str(row["value"]),
            file=str(row["relative_path"]),
            provenance=_fact_provenance(
                row, int(row["start_char"]), int(row["end_char"]),
            ),
            rank=rank,
            source="detector",
            plausibility=plausibility,
            subject=screen.subject,
        )
    model_rows = conn.execute(
        "SELECT m.finding_id,m.category,m.quote,m.canonical_offset,m.canonical_end,"
        "c.start_char,f.ordinal AS file_ordinal,f.relative_path,"
        "(SELECT p.kind FROM analyst_provenance_units p WHERE p.file_id=f.file_id "
        "AND p.start_char<=c.start_char+m.canonical_offset "
        "AND p.end_char>=c.start_char+m.canonical_end "
        "ORDER BY p.ordinal LIMIT 1) AS provenance_kind,"
        "(SELECT p.label FROM analyst_provenance_units p WHERE p.file_id=f.file_id "
        "AND p.start_char<=c.start_char+m.canonical_offset "
        "AND p.end_char>=c.start_char+m.canonical_end "
        "ORDER BY p.ordinal LIMIT 1) AS provenance_label "
        "FROM analyst_model_findings m JOIN analyst_chunks c ON c.chunk_id=m.chunk_id "
        "JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=? "
        "AND f.work_state='terminal' "
        "AND f.terminal_code='complete_model_reviewed' "
        "ORDER BY f.ordinal,m.finding_id",
        (run_id,),
    )
    for row in model_rows:
        category = str(row["category"])
        rank = report_json.rank_fact("model", category, "model")
        start = int(row["start_char"]) + int(row["canonical_offset"])
        end = int(row["start_char"]) + int(row["canonical_end"])
        collect(
            (rank_order[rank], int(row["file_ordinal"]), 1, int(row["finding_id"])),
            kind="model",
            category=category,
            quote=str(row["quote"]),
            file=str(row["relative_path"]),
            provenance=_fact_provenance(row, start, end),
            rank=rank,
            source="model",
        )
    candidates = [
        (
            group[0],
            report_json.GroundedFact(**group[1], occurrences=group[2]),
        )
        for group in collapsed.values()
    ]
    candidates.sort(key=lambda item: item[0])
    return tuple(item[1] for item in candidates[:MAX_REPORT_JSON_FACTS])


def _detector_category(kind: str) -> str:
    if kind in {"ssn", "dob", "passport"}:
        return "pii"
    if kind in {"card", "routing", "bank_account", "iban"}:
        return "financial"
    if kind in {"email", "phone"}:
        return "contact"
    if kind == "demographic_term":
        return "demographic"
    raise ReportStateError("durable detector kind is invalid")


def _fact_provenance(row: sqlite3.Row, start: int, end: int) -> str:
    kind = _optional_text(row["provenance_kind"])
    label = _optional_text(row["provenance_label"])
    if kind is not None and label is not None:
        return f"{kind} {label}"
    if kind is not None or label is not None:
        raise ReportStateError("durable fact provenance is incomplete")
    return f"characters {start}-{end}"


def _group_counts(
    conn: sqlite3.Connection, sql: str, run_id: str,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in conn.execute(sql, (run_id,)).fetchall():
        name = row["name"] if "name" in row.keys() else row[0]
        if name is None or type(name) is not str:
            raise ReportStateError("report group identity is invalid")
        count = int(row["n"])
        if count < 0 or count > MAX_REPORT_FINDINGS or name in result:
            raise ReportStateError("report group count is invalid")
        result[name] = count
    return result


def _require_inputs(fence: LeaseFence, token: str) -> None:
    if type(fence) is not LeaseFence:
        raise TypeError("report fence must be a LeaseFence")
    if type(token) is not str or _SHA256.fullmatch(token) is None:
        raise ValueError("finalization token must be a lowercase sha256")


def _require_page(value: int, limit: int, *, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else -1
    if type(value) is not int or value < minimum:
        raise ValueError("report page cursor is invalid")
    if type(limit) is not int or not 1 <= limit <= READ_PAGE_ROWS:
        raise ValueError("report page limit is invalid")


def _fence_values(fence: LeaseFence) -> tuple[object, ...]:
    return (
        fence.generation, fence.run_id, fence.owner_token, fence.process.pid,
        fence.process.start_ticks, fence.process.boot_id,
        fence.heartbeat_monotonic_ns,
    )


def _strict_bool(value: object) -> bool:
    if type(value) is not int or value not in {0, 1}:
        raise ReportStateError("durable boolean is invalid")
    return bool(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ReportStateError("durable text is invalid")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ReportStateError("durable integer is invalid")
    return value


__all__ = [
    "ReportStateError",
    "decode_detector_report_row",
    "decode_inventory_report_row",
    "decode_model_report_row",
    "build_report_json_payload",
    "load_detector_finding_page",
    "load_inventory_page",
    "load_model_finding_page",
    "load_report_snapshot",
]
