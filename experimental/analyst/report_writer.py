"""Symlink-safe atomic writer and streaming renderers for Analyst reports."""

from __future__ import annotations

import csv
import errno
import hashlib
import html
import io
import os
import secrets
import stat
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Final, TypeVar

from .report_contract import (
    ArtifactIdentity,
    FindingReportRow,
    HTML_CSP,
    InventoryReportRow,
    REPORT_ARTIFACT_NAMES,
    REPORT_SCHEMA,
    ReportManifest,
    ReportSnapshot,
    canonical_json_bytes,
    csv_safe,
)


_T = TypeVar("_T")
PageFactory = Callable[[], Iterator[tuple[_T, ...]]]

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FILE_FLAGS: Final = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
MAX_REPORT_ARTIFACT_BYTES: Final = 16 * 1024 * 1024 * 1024
_CSV_FIELDS: Final = (
    "evidence_kind", "file_ordinal", "relative_path", "format_name",
    "evidence_ordinal", "source_start", "source_end", "detector_kind",
    "detector_value", "chunk_index", "category", "quote", "document_type",
    "subject", "assessment", "model_offset", "model_offset_exact",
    "match_count", "review_state", "provenance_kind", "provenance_label",
)


class ReportWriteError(RuntimeError):
    """The report target or atomic publication boundary failed closed."""


class ArtifactSink:
    """A bounded-interface binary sink that hashes exactly what reaches disk."""

    __slots__ = ("_fd", "_hash", "_size", "_closed")

    def __init__(self, fd: int) -> None:
        if type(fd) is not int or fd < 0:
            raise TypeError("artifact sink requires an owned file descriptor")
        self._fd = fd
        self._hash = hashlib.sha256()
        self._size = 0
        self._closed = False

    def write_bytes(self, value: bytes) -> None:
        if type(value) is not bytes:
            raise TypeError("artifact bytes must use exact bytes")
        if self._closed:
            raise ReportWriteError("artifact sink is closed")
        view = memoryview(value)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:
                raise ReportWriteError("artifact write made no progress")
            view = view[written:]
        self._hash.update(value)
        self._size += len(value)

    def write_text(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("artifact text must use exact str")
        self.write_bytes(value.encode("utf-8"))

    @property
    def size(self) -> int:
        return self._size

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    def close(self) -> None:
        if not self._closed:
            os.fsync(self._fd)
            os.close(self._fd)
            self._closed = True


class SecureReportDirectory:
    """Owned descriptor for one exact no-symlink report directory."""

    __slots__ = ("_path", "_components", "_fd", "_identity", "_closed")

    def __init__(self, path: Path) -> None:
        absolute, components = _validate_output_path(path)
        self._path = absolute
        self._components = components
        self._fd = _open_or_create_output(components)
        try:
            info = os.fstat(self._fd)
            if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
                raise ReportWriteError("report directory ownership or type is unsafe")
            os.fchmod(self._fd, 0o700)
        except ReportWriteError:
            os.close(self._fd)
            raise
        except OSError as exc:
            os.close(self._fd)
            raise ReportWriteError("report directory policy could not be applied") from exc
        self._identity = (info.st_dev, info.st_ino)
        self._closed = False

    def __enter__(self) -> SecureReportDirectory:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            os.close(self._fd)
            self._closed = True

    def publish(
        self, name: str, render: Callable[[ArtifactSink], None],
    ) -> ArtifactIdentity:
        """Render, fsync, and atomically replace one fixed report artifact."""
        if type(name) is not str or name not in REPORT_ARTIFACT_NAMES:
            raise ValueError("report artifact name is not allowed")
        if not callable(render):
            raise TypeError("report renderer must be callable")
        self._require_open()
        temporary = f".analyst-{secrets.token_hex(16)}.tmp"
        fd = -1
        sink: ArtifactSink | None = None
        try:
            self._verify_binding()
            _require_safe_existing(self._fd, name)
            fd = os.open(temporary, _FILE_FLAGS, 0o600, dir_fd=self._fd)
            os.set_inheritable(fd, False)
            sink = ArtifactSink(fd)
            fd = -1
            render(sink)
            sink.close()
            identity = ArtifactIdentity(name, sink.size, sink.sha256)
            sink = None
            self._verify_binding()
            _require_safe_existing(self._fd, name)
            os.rename(
                temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd,
            )
            os.fsync(self._fd)
            return identity
        except ReportWriteError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ReportWriteError("atomic report publication failed") from exc
        finally:
            if sink is not None:
                try:
                    sink.close()
                except OSError:
                    pass
            elif fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(temporary, dir_fd=self._fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _require_open(self) -> None:
        if self._closed:
            raise ReportWriteError("report directory is closed")

    def _verify_binding(self) -> None:
        try:
            check_fd = _walk_existing(self._components)
        except OSError as exc:
            raise ReportWriteError("report directory binding is unavailable") from exc
        try:
            info = os.fstat(check_fd)
            if (info.st_dev, info.st_ino) != self._identity:
                raise ReportWriteError("report directory binding changed")
        finally:
            os.close(check_fd)


def publish_report(
    snapshot: ReportSnapshot,
    *,
    inventory_pages: PageFactory[InventoryReportRow],
    finding_pages: PageFactory[FindingReportRow],
    progress: Callable[[], None] | None = None,
) -> ReportManifest:
    """Stream and atomically publish the frozen four-artifact report."""
    if type(snapshot) is not ReportSnapshot:
        raise TypeError("report publication requires a ReportSnapshot")
    if (
        not callable(inventory_pages)
        or not callable(finding_pages)
        or progress is not None and not callable(progress)
    ):
        raise TypeError("report page sources must be callable")
    pulse = (lambda: None) if progress is None else progress
    identities: dict[str, ArtifactIdentity] = {}
    with SecureReportDirectory(Path(snapshot.output_root)) as target:
        pulse()
        identities["run.json"] = target.publish(
            "run.json", lambda sink: _render_run_json(sink, snapshot),
        )
        pulse()
        identities["findings.jsonl"] = target.publish(
            "findings.jsonl", lambda sink: _render_jsonl(sink, finding_pages()),
        )
        pulse()
        identities["findings.csv"] = target.publish(
            "findings.csv", lambda sink: _render_csv(sink, finding_pages()),
        )
        pulse()
        identities["report.html"] = target.publish(
            "report.html",
            lambda sink: _render_html(
                sink, snapshot, inventory_pages(), finding_pages(),
            ),
        )
        pulse()
    return ReportManifest(tuple(identities[name] for name in REPORT_ARTIFACT_NAMES))


def inspect_report_manifest(output_root: Path) -> ReportManifest:
    """Recompute identities from an existing owner-only fixed report set."""
    _absolute, components = _validate_output_path(output_root)
    try:
        directory_fd = _walk_existing(components)
    except OSError as exc:
        raise ReportWriteError("published report directory is unavailable") from exc
    try:
        directory = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise ReportWriteError("published report directory is unsafe")
        identity = (directory.st_dev, directory.st_ino)
        artifacts = tuple(
            _inspect_artifact(directory_fd, name) for name in REPORT_ARTIFACT_NAMES
        )
        check_fd = _walk_existing(components)
        try:
            check = os.fstat(check_fd)
            if (check.st_dev, check.st_ino) != identity:
                raise ReportWriteError("published report directory binding changed")
        finally:
            os.close(check_fd)
        return ReportManifest(artifacts)
    except ReportWriteError:
        raise
    except OSError as exc:
        raise ReportWriteError("published report inspection failed") from exc
    finally:
        os.close(directory_fd)


def _render_run_json(sink: ArtifactSink, snapshot: ReportSnapshot) -> None:
    payload = {
        "coverage": snapshot.coverage.as_json(),
        "run": snapshot.run.as_json(),
        "schema": REPORT_SCHEMA,
    }
    sink.write_bytes(canonical_json_bytes(payload) + b"\n")


def _render_jsonl(
    sink: ArtifactSink, pages: Iterable[tuple[FindingReportRow, ...]],
) -> None:
    for page in pages:
        _require_page(page, FindingReportRow)
        for row in page:
            sink.write_bytes(canonical_json_bytes(row.as_json()) + b"\n")


def _render_csv(
    sink: ArtifactSink, pages: Iterable[tuple[FindingReportRow, ...]],
) -> None:
    _write_csv_row(sink, _CSV_FIELDS)
    for page in pages:
        _require_page(page, FindingReportRow)
        for row in page:
            values = row.as_json()
            _write_csv_row(sink, tuple(csv_safe(values[name]) for name in _CSV_FIELDS))


def _write_csv_row(sink: ArtifactSink, values: tuple[object, ...]) -> None:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL).writerow(values)
    sink.write_text(buffer.getvalue())


def _render_html(
    sink: ArtifactSink,
    snapshot: ReportSnapshot,
    inventory_pages: Iterable[tuple[InventoryReportRow, ...]],
    finding_pages: Iterable[tuple[FindingReportRow, ...]],
) -> None:
    title = _escape(snapshot.run.report_label)
    sink.write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{_escape(HTML_CSP)}\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Analyst report — {title}</title><style>"
        "body{font:14px system-ui,sans-serif;margin:2rem;color:#18212b;background:#fff}"
        "h1,h2{color:#102a43}table{border-collapse:collapse;width:100%;margin:1rem 0}"
        "th,td{border:1px solid #bcccdc;padding:.35rem;text-align:left;vertical-align:top}"
        "th{background:#eef2f6}code{overflow-wrap:anywhere}details{margin:.7rem 0}"
        ".metric{display:inline-block;padding:.6rem;margin:.2rem;background:#eef2f6}"
        "</style></head><body>"
        f"<h1>Analyst report — {title}</h1>"
    )
    _render_coverage_html(sink, snapshot)
    sink.write_text("<h2>Findings</h2>")
    finding_count = 0
    for number, page in enumerate(finding_pages, 1):
        _require_page(page, FindingReportRow)
        sink.write_text(
            f"<details><summary>Findings page {number} ({len(page)} rows)</summary>"
            "<table><thead><tr><th>Kind</th><th>File</th><th>Type/category</th>"
            "<th>Evidence</th><th>Span</th><th>Provenance</th></tr></thead><tbody>"
        )
        for row in page:
            finding_count += 1
            kind = row.detector_kind if row.detector_kind is not None else row.category
            evidence = row.detector_value if row.detector_value is not None else row.quote
            provenance = " / ".join(
                value for value in (row.provenance_kind, row.provenance_label) if value
            )
            sink.write_text(
                "<tr>" + _cells(
                    row.evidence_kind.value, row.relative_path, kind or "",
                    evidence or "", f"{row.source_start}:{row.source_end}", provenance,
                ) + "</tr>"
            )
        sink.write_text("</tbody></table></details>")
    if finding_count == 0:
        sink.write_text("<p>No retained detector or model findings.</p>")
    sink.write_text("<h2>Document inventory</h2>")
    inventory_count = 0
    for number, page in enumerate(inventory_pages, 1):
        _require_page(page, InventoryReportRow)
        sink.write_text(
            f"<details><summary>Inventory page {number} ({len(page)} rows)</summary>"
            "<table><thead><tr><th>#</th><th>Path</th><th>Format</th><th>Stage</th>"
            "<th>Terminal</th><th>Detail</th><th>Selected</th>"
            "<th>Detector hits</th><th>Model findings</th>"
            "</tr></thead><tbody>"
        )
        for row in page:
            inventory_count += 1
            sink.write_text(
                "<tr>" + _cells(
                    row.ordinal, row.relative_path, row.format_name or "unidentified",
                    row.stage.value, row.terminal.value, row.terminal_detail or "",
                    "yes" if row.selected_for_model else "no",
                    row.detector_hit_count,
                    row.retained_model_finding_count,
                ) + "</tr>"
            )
        sink.write_text("</tbody></table></details>")
    if inventory_count == 0:
        sink.write_text("<p>No discovered files.</p>")
    sink.write_text("</body></html>\n")


def _render_coverage_html(sink: ArtifactSink, snapshot: ReportSnapshot) -> None:
    coverage = snapshot.coverage
    sink.write_text("<h2>Coverage</h2><div>")
    for name, value in (
        ("Discovered", coverage.discovered_files),
        ("Detector-scanned", coverage.detector_scanned_files),
        ("Selected", coverage.selected_files),
        ("Model-reviewed", coverage.model_reviewed_files),
        ("Valid model chunks", coverage.valid_model_chunks),
        ("Detector hits", coverage.detector_hits),
        ("Retained model findings", coverage.retained_model_findings),
        ("Inventory exclusions", coverage.excluded_paths),
    ):
        sink.write_text(f"<span class=\"metric\"><strong>{_escape(name)}</strong> {value}</span>")
    sink.write_text("</div><h3>Terminal outcomes</h3><table><thead><tr>"
                    "<th>Outcome</th><th>Files</th></tr></thead><tbody>")
    for item in coverage.terminal_counts:
        sink.write_text("<tr>" + _cells(item.name, item.count) + "</tr>")
    sink.write_text("</tbody></table><h3>Authenticated formats</h3><table><thead><tr>"
                    "<th>Format</th><th>Files</th></tr></thead><tbody>")
    for item in coverage.format_counts:
        sink.write_text("<tr>" + _cells(item.name, item.count) + "</tr>")
    sink.write_text("</tbody></table><h3>Inventory exclusions</h3><table><thead><tr>"
                    "<th>Reason</th><th>Paths</th></tr></thead><tbody>")
    for item in coverage.exclusion_counts:
        sink.write_text("<tr>" + _cells(item.name, item.count) + "</tr>")
    sink.write_text("</tbody></table>")


def _require_page(page: object, item_type: type[_T]) -> None:
    if type(page) is not tuple or any(type(item) is not item_type for item in page):
        raise ReportWriteError("report page source violated its typed contract")


def _cells(*values: object) -> str:
    return "".join(f"<td>{_escape(value)}</td>" for value in values)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _validate_output_path(path: Path) -> tuple[Path, tuple[str, ...]]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("report output path must be an absolute Path")
    raw = os.fspath(path)
    if "\\" in raw or "\x00" in raw:
        raise ValueError("report output path is not canonical")
    components = tuple(raw.split("/")[1:])
    if not components or any(part in {"", ".", ".."} for part in components):
        raise ValueError("report output path is not canonical")
    return path, components


def _open_or_create_output(components: tuple[str, ...]) -> int:
    current = os.open("/", _DIRECTORY_FLAGS)
    try:
        for index, component in enumerate(components):
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise ReportWriteError("report path component is not a directory")
            if index == len(components) - 1 and info.st_uid != os.getuid():
                os.close(child)
                raise ReportWriteError("report directory is not owner-controlled")
            os.close(current)
            current = child
        return current
    except OSError as exc:
        os.close(current)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ReportWriteError("report path contains an unsafe component") from exc
        raise ReportWriteError("report directory could not be opened") from exc
    except BaseException:
        os.close(current)
        raise


def _walk_existing(components: tuple[str, ...]) -> int:
    current = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _require_safe_existing(directory_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ReportWriteError("existing report artifact is unsafe")


def _inspect_artifact(directory_fd: int, name: str) -> ArtifactIdentity:
    fd = os.open(name, _READ_FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 <= before.st_size <= MAX_REPORT_ARTIFACT_BYTES
        ):
            raise ReportWriteError("published report artifact is unsafe")
        digest = hashlib.sha256()
        size = 0
        while size <= MAX_REPORT_ARTIFACT_BYTES:
            chunk = os.read(fd, min(1024 * 1024, MAX_REPORT_ARTIFACT_BYTES + 1 - size))
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        before_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_uid,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode, after.st_uid,
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        )
        live = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        live_identity = (
            live.st_dev, live.st_ino, live.st_mode, live.st_uid,
            live.st_size, live.st_mtime_ns, live.st_ctime_ns,
        )
        if (
            size != before.st_size
            or before_identity != after_identity
            or after_identity != live_identity
        ):
            raise ReportWriteError("published report artifact changed during inspection")
        return ArtifactIdentity(name, size, digest.hexdigest())
    finally:
        os.close(fd)


__all__ = [
    "ArtifactSink",
    "MAX_REPORT_ARTIFACT_BYTES",
    "PageFactory",
    "ReportWriteError",
    "SecureReportDirectory",
    "inspect_report_manifest",
    "publish_report",
]
