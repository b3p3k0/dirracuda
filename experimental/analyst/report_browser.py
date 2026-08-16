"""Verified, bounded read projections for completed Analyst reports."""

from __future__ import annotations

import csv
import io
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final

from .report import verify_completed_report
from .report_contract import (
    FindingReportRow,
    InventoryReportRow,
    MAX_REPORT_FILES,
    MAX_REPORT_FINDINGS,
    READ_PAGE_ROWS,
    canonical_json_bytes,
    csv_safe,
)
from .report_writer import ArtifactSink
from .report_state import (
    ReportStateError,
    decode_detector_report_row,
    decode_inventory_report_row,
    decode_model_report_row,
)
from .store import open_connection, run_immediate
from .worker_contract import validate_worker_run_id


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", re.ASCII)
MAX_BROWSER_RUNS: Final = 200
MAX_EXPLICIT_EXPORT_IDS: Final = 10_000
_CSV_FIELDS: Final = (
    "evidence_kind", "file_ordinal", "relative_path", "format_name",
    "evidence_ordinal", "source_start", "source_end", "detector_kind",
    "detector_value", "chunk_index", "category", "quote", "document_type",
    "subject", "assessment", "model_offset", "model_offset_exact",
    "match_count", "review_state", "provenance_kind", "provenance_label",
)


class ReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ExportFormat(str, Enum):
    JSONL = "jsonl"
    CSV = "csv"


@dataclass(frozen=True, slots=True)
class FindingExportSelection:
    """Transient explicit selection; ids are inclusions or select-all exclusions."""

    all_findings: bool
    finding_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.all_findings) is not bool
            or type(self.finding_ids) is not tuple
            or len(self.finding_ids) > MAX_EXPLICIT_EXPORT_IDS
            or any(type(value) is not int or value <= 0 for value in self.finding_ids)
            or tuple(sorted(set(self.finding_ids))) != self.finding_ids
        ):
            raise ValueError("finding export selection is invalid")


@dataclass(frozen=True, slots=True)
class CompletedReportHandle:
    """Manifest-verified immutable identity used by lazy desktop reads."""

    run_id: str
    manifest_sha256: str
    report_label: str = field(repr=False)
    mode: str
    completed_at_utc: str
    output_root: str = field(repr=False)
    discovered_files: int
    excluded_paths: int
    detector_scanned_files: int
    selected_files: int
    model_reviewed_files: int
    detector_hits: int
    model_findings: int

    def __post_init__(self) -> None:
        validate_worker_run_id(self.run_id)
        if (
            type(self.manifest_sha256) is not str
            or _SHA256.fullmatch(self.manifest_sha256) is None
            or type(self.report_label) is not str
            or not self.report_label
            or self.mode not in {"fast", "deep"}
            or type(self.completed_at_utc) is not str
            or not self.completed_at_utc
            or type(self.output_root) is not str
            or not self.output_root.startswith("/")
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.discovered_files,
                    self.excluded_paths,
                    self.detector_scanned_files,
                    self.selected_files,
                    self.model_reviewed_files,
                    self.detector_hits,
                    self.model_findings,
                )
            )
            or self.detector_scanned_files > self.discovered_files
            or self.selected_files > self.discovered_files
            or self.model_reviewed_files > self.selected_files
            or self.discovered_files > MAX_REPORT_FILES
            or self.excluded_paths > MAX_REPORT_FILES
            or self.detector_hits > MAX_REPORT_FINDINGS
            or self.model_findings > MAX_REPORT_FINDINGS
        ):
            raise ValueError("completed report handle is invalid")


def list_completed_reports(
    *, path: Path | None = None, limit: int = 100,
) -> tuple[tuple[str, str, str], ...]:
    """List compact completed run identities without touching report files."""
    if type(limit) is not int or not 1 <= limit <= MAX_BROWSER_RUNS:
        raise ValueError("completed report limit is invalid")
    conn = open_connection(path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT run_id,report_label,finished_at_utc FROM analyst_runs "
            "WHERE state='complete' AND report_manifest_sha256 IS NOT NULL "
            "ORDER BY finished_at_utc DESC,run_id LIMIT ?", (limit,),
        ).fetchall()
        return tuple(
            (str(row["run_id"]), str(row["report_label"]), str(row["finished_at_utc"]))
            for row in rows
        )
    finally:
        conn.close()


def open_completed_report(
    run_id: str, *, path: Path | None = None,
) -> CompletedReportHandle:
    """Verify fixed artifacts, then return a bounded durable coverage handle."""
    canonical = validate_worker_run_id(run_id)
    manifest = verify_completed_report(canonical, path=path)
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT run_id,report_manifest_sha256,report_label,mode,finished_at_utc,"
            "output_root,(SELECT count(*) FROM analyst_files f WHERE f.run_id=r.run_id) "
            "discovered,(SELECT count(*) FROM analyst_inventory_exclusions e "
            "WHERE e.run_id=r.run_id) excluded,"
            "(SELECT count(*) FROM analyst_files f WHERE f.run_id=r.run_id AND "
            "f.stage IN ('detector_scanned','selected_for_model','model_reviewed',"
            "'model_response_valid')) detector_scanned,"
            "(SELECT count(*) FROM analyst_files f WHERE f.run_id=r.run_id AND "
            "f.selected_for_model=1) selected,"
            "(SELECT count(*) FROM analyst_files f WHERE f.run_id=r.run_id AND "
            "f.stage IN ('model_reviewed','model_response_valid')) model_reviewed,"
            "(SELECT count(*) FROM analyst_detector_hits h JOIN analyst_files f "
            "ON f.file_id=h.file_id WHERE f.run_id=r.run_id) detector_hits,"
            "(SELECT count(*) FROM analyst_model_findings m JOIN analyst_chunks c "
            "ON c.chunk_id=m.chunk_id JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=r.run_id AND f.terminal_code='complete_model_reviewed') "
            "model_findings FROM analyst_runs r WHERE run_id=? AND state='complete'",
            (canonical,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or str(row["report_manifest_sha256"]) != manifest.sha256:
        raise ReportStateError("completed report identity changed after verification")
    return CompletedReportHandle(
        str(row["run_id"]), str(row["report_manifest_sha256"]),
        str(row["report_label"]), str(row["mode"]), str(row["finished_at_utc"]),
        str(row["output_root"]), int(row["discovered"]), int(row["excluded"]),
        int(row["detector_scanned"]), int(row["selected"]),
        int(row["model_reviewed"]), int(row["detector_hits"]),
        int(row["model_findings"]),
    )


def load_completed_inventory_page(
    handle: CompletedReportHandle,
    *,
    after_ordinal: int = -1,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[InventoryReportRow, ...]:
    """Load one ordinal page after rechecking the immutable DB identity."""
    _require_handle_page(handle, after_ordinal, limit, allow_zero=False)
    conn = open_connection(path, read_only=True)
    try:
        _require_completed_identity(conn, handle)
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
            (handle.run_id, after_ordinal, limit),
        ).fetchall()
        return tuple(decode_inventory_report_row(row) for row in rows)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("completed inventory row is invalid") from exc
    finally:
        conn.close()


def load_completed_detector_page(
    handle: CompletedReportHandle,
    *,
    after_id: int = 0,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[tuple[int, FindingReportRow], ...]:
    """Load one deterministic-evidence page with a private durable cursor."""
    _require_handle_page(handle, after_id, limit, allow_zero=True)
    conn = open_connection(path, read_only=True)
    try:
        _require_completed_identity(conn, handle)
        rows = conn.execute(
            "SELECT h.hit_id,h.ordinal,h.kind,h.value,h.start_char,h.end_char,"
            "f.file_id,f.ordinal AS file_ordinal,f.relative_path,f.format_name "
            "FROM analyst_detector_hits h JOIN analyst_files f ON f.file_id=h.file_id "
            "WHERE f.run_id=? AND h.hit_id>? ORDER BY h.hit_id LIMIT ?",
            (handle.run_id, after_id, limit),
        ).fetchall()
        return tuple(
            (int(row["hit_id"]), decode_detector_report_row(row)) for row in rows
        )
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("completed detector row is invalid") from exc
    finally:
        conn.close()


def load_completed_model_page(
    handle: CompletedReportHandle,
    *,
    after_id: int = 0,
    limit: int = READ_PAGE_ROWS,
    path: Path | None = None,
) -> tuple[tuple[int, FindingReportRow], ...]:
    """Load suggested model evidence only from fully reviewed terminal files."""
    _require_handle_page(handle, after_id, limit, allow_zero=True)
    conn = open_connection(path, read_only=True)
    try:
        _require_completed_identity(conn, handle)
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
            (handle.run_id, after_id, limit),
        ).fetchall()
        return tuple(
            (int(row["finding_id"]), decode_model_report_row(row)) for row in rows
        )
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportStateError("completed model row is invalid") from exc
    finally:
        conn.close()


def review_model_finding(
    handle: CompletedReportHandle,
    finding_id: int,
    decision: ReviewDecision,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Persist one exact human adjudication on a completed model suggestion."""
    if type(handle) is not CompletedReportHandle:
        raise TypeError("review requires a verified completed report handle")
    if type(finding_id) is not int or finding_id <= 0:
        raise ValueError("finding id is invalid")
    if type(decision) is not ReviewDecision:
        raise ValueError("review decision is invalid")
    timestamp = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if now_utc is None else now_utc
    )
    if type(timestamp) is not str or _UTC.fullmatch(timestamp) is None:
        raise ValueError("review timestamp is invalid")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("review timestamp is invalid") from None

    def operation(conn) -> None:
        _require_completed_identity(conn, handle)
        cursor = conn.execute(
            "UPDATE analyst_model_findings SET review_state=?,reviewed_at_utc=? "
            "WHERE finding_id=? AND chunk_id IN (SELECT c.chunk_id "
            "FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id "
            "WHERE f.run_id=? AND f.terminal_code='complete_model_reviewed')",
            (decision.value, timestamp, finding_id, handle.run_id),
        )
        if cursor.rowcount != 1:
            raise ReportStateError("model finding is not reviewable in this report")

    run_immediate(operation, path=path)


def export_model_findings(
    handle: CompletedReportHandle,
    selection: FindingExportSelection,
    destination: Path,
    output_format: ExportFormat,
    *,
    path: Path | None = None,
) -> int:
    """Atomically export only the transiently selected model suggestions."""
    if type(handle) is not CompletedReportHandle:
        raise TypeError("export requires a verified completed report handle")
    if type(selection) is not FindingExportSelection:
        raise TypeError("export requires a typed finding selection")
    if type(output_format) is not ExportFormat:
        raise ValueError("export format is invalid")
    if not isinstance(destination, Path) or not destination.is_absolute():
        raise ValueError("export destination must be an absolute Path")
    raw = os.fspath(destination)
    if "\\" in raw or "\x00" in raw or destination.name in {"", ".", ".."}:
        raise ValueError("export destination is not canonical")
    directory_fd = _open_export_parent(destination.parent)
    temporary = f".analyst-export-{secrets.token_hex(16)}.tmp"
    sink: ArtifactSink | None = None
    count = 0
    try:
        _require_safe_export_target(directory_fd, destination.name)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        sink = ArtifactSink(fd)
        if output_format is ExportFormat.CSV:
            _write_csv_row(sink, _CSV_FIELDS)
        excluded_or_included = set(selection.finding_ids)
        cursor = 0
        while True:
            page = load_completed_model_page(
                handle, after_id=cursor, limit=READ_PAGE_ROWS, path=path,
            )
            if not page:
                break
            for finding_id, row in page:
                cursor = finding_id
                chosen = (
                    finding_id not in excluded_or_included
                    if selection.all_findings else finding_id in excluded_or_included
                )
                if not chosen:
                    continue
                values = row.as_json()
                if output_format is ExportFormat.JSONL:
                    sink.write_bytes(canonical_json_bytes(values) + b"\n")
                else:
                    _write_csv_row(
                        sink, tuple(csv_safe(values[name]) for name in _CSV_FIELDS),
                    )
                count += 1
            if len(page) < READ_PAGE_ROWS:
                break
        if not selection.all_findings and count != len(selection.finding_ids):
            raise ReportStateError("explicit export selection changed or is foreign")
        sink.close()
        sink = None
        _require_safe_export_target(directory_fd, destination.name)
        os.replace(
            temporary,
            destination.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
        return count
    except BaseException:
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(directory_fd)


def _write_csv_row(sink: ArtifactSink, values: tuple[object, ...]) -> None:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL).writerow(values)
    sink.write_text(buffer.getvalue())


def _open_export_parent(path: Path) -> int:
    raw = os.fspath(path)
    if not path.is_absolute() or "\\" in raw or "\x00" in raw:
        raise ValueError("export parent is invalid")
    components = tuple(raw.split("/")[1:])
    if not components or any(item in {"", ".", ".."} for item in components):
        raise ValueError("export parent is not canonical")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for component in components:
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _require_safe_export_target(directory_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ReportStateError("existing export target is unsafe")


def _require_completed_identity(conn, handle: CompletedReportHandle) -> None:
    row = conn.execute(
        "SELECT 1 FROM analyst_runs WHERE run_id=? AND state='complete' "
        "AND report_manifest_sha256=?",
        (handle.run_id, handle.manifest_sha256),
    ).fetchone()
    if row is None:
        raise ReportStateError("completed report identity changed")


def _require_handle_page(
    handle: CompletedReportHandle, cursor: int, limit: int, *, allow_zero: bool,
) -> None:
    if type(handle) is not CompletedReportHandle:
        raise TypeError("completed report page requires a verified handle")
    minimum = 0 if allow_zero else -1
    if type(cursor) is not int or cursor < minimum:
        raise ValueError("completed report cursor is invalid")
    if type(limit) is not int or not 1 <= limit <= READ_PAGE_ROWS:
        raise ValueError("completed report page limit is invalid")


__all__ = [
    "CompletedReportHandle",
    "ExportFormat",
    "FindingExportSelection",
    "MAX_BROWSER_RUNS",
    "MAX_EXPLICIT_EXPORT_IDS",
    "ReviewDecision",
    "export_model_findings",
    "list_completed_reports",
    "load_completed_detector_page",
    "load_completed_inventory_page",
    "load_completed_model_page",
    "open_completed_report",
    "review_model_finding",
]
