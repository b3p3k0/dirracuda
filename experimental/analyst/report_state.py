"""Bounded read-only projections for finalizing Analyst reports."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .lease import LeaseFence
from .models import FileStage, FileTerminal
from .report_contract import (
    CoverageSummary,
    EvidenceKind,
    FindingReportRow,
    InventoryReportRow,
    MAX_REPORT_FILES,
    MAX_REPORT_FINDINGS,
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
            created_at_utc=str(row["created_at_utc"]),
            model_tag=str(row["model_tag"]),
            model_digest=str(row["model_digest"]),
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
            extract_summary_row_id=_optional_int(row["extract_summary_row_id"]),
        )
        return ReportSnapshot(run, coverage, str(row["output_root"]))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("durable report state is invalid") from exc
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
        return tuple(_inventory_row(row) for row in rows)
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
        return tuple((int(row["hit_id"]), _detector_row(row)) for row in rows)
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
        return tuple((int(row["finding_id"]), _model_row(row)) for row in rows)
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


def _inventory_row(row: sqlite3.Row) -> InventoryReportRow:
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


def _detector_row(row: sqlite3.Row) -> FindingReportRow:
    return FindingReportRow(
        EvidenceKind.DETECTOR, int(row["file_id"]), int(row["file_ordinal"]),
        str(row["relative_path"]), str(row["format_name"]), int(row["ordinal"]),
        int(row["start_char"]), int(row["end_char"]),
        detector_kind=str(row["kind"]), detector_value=str(row["value"]),
    )


def _model_row(row: sqlite3.Row) -> FindingReportRow:
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
    conn: sqlite3.Connection, sql: str, run_id: str, *, maximum: int,
) -> int:
    row = conn.execute(sql, (run_id,)).fetchone()
    if row is None:
        raise ReportStateError("report count query returned no row")
    value = int(row[0])
    if value < 0 or value > maximum:
        raise ReportStateError("report count exceeds the frozen bound")
    return value


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
    "load_detector_finding_page",
    "load_inventory_page",
    "load_model_finding_page",
    "load_report_snapshot",
]
