"""Batch export for already-validated Analyst reports."""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import report_json, report_render


_FORMAT_ORDER = ("md", "json", "txt", "csv")
_FORMATS = frozenset(_FORMAT_ORDER)
_LAYOUTS = frozenset({"per_report", "combined"})
_INCLUDES = frozenset({"read", "facts"})
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Counts and paths produced by one completed batch export."""

    report_count: int
    file_count: int
    written_paths: tuple[Path, ...]

    @property
    def reports_exported(self) -> int:
        return self.report_count

    @property
    def files_written(self) -> int:
        return self.file_count

    @property
    def paths(self) -> tuple[Path, ...]:
        return self.written_paths


def export_reports(
    reports: Sequence[tuple[str, dict]],
    *,
    formats,
    layout: str,
    include,
    dest_dir,
) -> ExportResult:
    """Render and write one batch of validated reports as owner-only files."""
    report_values = tuple(reports)
    format_values = _option_set(formats, _FORMATS, "formats", allow_empty=False)
    include_values = _option_set(include, _INCLUDES, "include", allow_empty=False)
    if type(layout) is not str or layout not in _LAYOUTS:
        raise ValueError("layout must be per_report or combined")

    destination = Path(dest_dir).expanduser()
    if not destination.is_dir():
        raise ValueError("destination must be an existing directory")

    normalized: list[tuple[str, dict]] = []
    for item in report_values:
        if type(item) not in {tuple, list} or len(item) != 2:
            raise TypeError("reports must contain (label, report) pairs")
        label, report = item
        if type(label) is not str or not label.strip() or type(report) is not dict:
            raise TypeError("report labels and payloads must use the expected types")
        report_json.validate_report_json(report)
        normalized.append((label, report))

    ordered_formats = tuple(fmt for fmt in _FORMAT_ORDER if fmt in format_values)
    written: list[Path] = []
    if layout == "per_report":
        used_names = _existing_names(destination)
        for label, report in normalized:
            stem = _deduplicated_stem(
                _sanitize_label(label), ordered_formats, used_names,
            )
            for fmt in ordered_formats:
                target = destination / f"{stem}.{fmt}"
                _write_owner_only(
                    target, _render_one(report, fmt, include_values), exclusive=True,
                )
                written.append(target)
    else:
        for fmt in ordered_formats:
            target = destination / f"analyst-reports.{fmt}"
            content = _render_combined(normalized, fmt, include_values)
            _write_owner_only(target, content, exclusive=False)
            written.append(target)

    return ExportResult(len(normalized), len(written), tuple(written))


def _option_set(values, allowed: frozenset[str], name: str, *, allow_empty: bool):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a set of options")
    try:
        selected = frozenset(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be a set of options") from exc
    if (not selected and not allow_empty) or not selected <= allowed:
        raise ValueError(f"invalid {name} selection")
    return selected


def _render_one(report: dict, fmt: str, include: frozenset[str]) -> str:
    if fmt == "json":
        rendered = report_json.dumps_report(report)
        if include == _INCLUDES:
            return rendered
        payload = json.loads(rendered)
        if "read" not in include:
            payload.pop("read")
        if "facts" not in include:
            payload.pop("facts")
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    rendered = report_render.render(report, fmt)
    if fmt == "csv":
        return rendered if "facts" in include else ""
    marker = "\n## FACTS\n" if fmt == "md" else "\nFACTS\n"
    read_block, facts_block = rendered.split(marker, 1)
    if include == frozenset({"read"}):
        return read_block.rstrip() + "\n"
    if include == frozenset({"facts"}):
        return marker.lstrip("\n") + facts_block
    return rendered


def _render_combined(
    reports: Sequence[tuple[str, dict]], fmt: str, include: frozenset[str],
) -> str:
    rendered = [
        (_sanitize_label(label), _render_one(report, fmt, include))
        for label, report in reports
    ]
    if fmt == "json":
        return "[" + ",".join(content for _label, content in rendered) + "]"
    if fmt == "md":
        sections = [
            f"# Analyst report: {label}\n\n{content.rstrip()}"
            for label, content in rendered
        ]
        return "\n\n---\n\n".join(sections) + ("\n" if sections else "")
    if fmt == "txt":
        sections = [
            f"===== ANALYST REPORT: {label} =====\n{content.rstrip()}"
            for label, content in rendered
        ]
        return "\n\n".join(sections) + ("\n" if sections else "")
    sections = [
        f"Analyst report,{index},{label}\n{content.rstrip()}"
        for index, (label, content) in enumerate(rendered, start=1)
    ]
    return "\n\n".join(sections) + ("\n" if sections else "")


def _sanitize_label(label: str) -> str:
    ascii_label = unicodedata.normalize("NFKD", label).encode(
        "ascii", "ignore",
    ).decode("ascii")
    safe = _UNSAFE_FILENAME.sub("-", ascii_label).strip(" .-_")
    safe = safe[:120].rstrip(" .-_")
    return safe or "report"


def _existing_names(destination: Path) -> set[str]:
    return {entry.name.casefold() for entry in destination.iterdir()}


def _deduplicated_stem(
    initial: str, formats: Sequence[str], used_names: set[str],
) -> str:
    stem = initial
    counter = 2
    while any(f"{stem}.{fmt}".casefold() in used_names for fmt in formats):
        stem = f"{initial}-{counter}"
        counter += 1
    used_names.update(f"{stem}.{fmt}".casefold() for fmt in formats)
    return stem


def _write_owner_only(path: Path, content: str, *, exclusive: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        payload = content.encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("export write made no progress")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PermissionError("exported reports must be owner-only")


__all__ = ["ExportResult", "export_reports"]
