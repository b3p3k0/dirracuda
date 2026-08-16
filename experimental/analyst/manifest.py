"""Exact extraction-summary loading and manifest-only Analyst inventory."""

from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import quote

from shared.extract_manifest import ExtractSummaryReference, ExtractSummarySource

from .inventory import InventoryResult, inventory_selected_paths


MAX_SUMMARY_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_CHOICES: Final = 100
_REQUIRED_DB_COLUMNS = frozenset({
    "id", "ip_address", "host_type", "protocol_server_id", "port",
    "summary_json", "source", "files_downloaded",
})


class ManifestError(RuntimeError):
    """An extraction manifest is unavailable or outside the frozen contract."""


@dataclass(frozen=True, slots=True)
class ExtractionManifest:
    reference: ExtractSummaryReference
    source_root: Path = field(repr=False)
    inventory: InventoryResult = field(repr=False)
    host_type: str
    ip_address: str = field(repr=False)
    protocol_server_id: int | None
    port: int | None

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not ExtractSummaryReference
            or not isinstance(self.source_root, Path)
            or not self.source_root.is_absolute()
            or type(self.inventory) is not InventoryResult
            or self.host_type not in {"S", "F", "H"}
            or type(self.ip_address) is not str
            or not self.ip_address
            or len(self.ip_address) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in self.ip_address)
            or (
                self.protocol_server_id is not None
                and (type(self.protocol_server_id) is not int or self.protocol_server_id <= 0)
            )
            or (
                self.port is not None
                and (type(self.port) is not int or not 1 <= self.port <= 65535)
            )
        ):
            raise ValueError("extraction manifest is invalid")
        try:
            ipaddress.ip_address(self.ip_address)
        except ValueError:
            raise ValueError("extraction manifest is invalid") from None


@dataclass(frozen=True, slots=True)
class ExtractionManifestChoice:
    """Content-bounded display metadata bound to one exact primary row."""

    reference: ExtractSummaryReference
    host_type: str
    ip_address: str = field(repr=False)
    created_at: str
    files_downloaded: int

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not ExtractSummaryReference
            or self.reference.source is not ExtractSummarySource.PRIMARY_DB
            or self.host_type not in {"S", "F", "H"}
            or type(self.ip_address) is not str
            or not self.ip_address
            or type(self.created_at) is not str
            or not self.created_at
            or type(self.files_downloaded) is not int
            or self.files_downloaded <= 0
        ):
            raise ValueError("extraction manifest choice is invalid")
        try:
            ipaddress.ip_address(self.ip_address)
        except ValueError:
            raise ValueError("extraction manifest choice is invalid") from None

    @property
    def display_label(self) -> str:
        return (
            f"{self.host_type} {self.ip_address} · {self.files_downloaded} file(s) · "
            f"{self.created_at} · row {self.reference.db_row_id}"
        )


def list_extraction_manifests(
    main_db_path: Path,
    *,
    limit: int = 20,
) -> tuple[ExtractionManifestChoice, ...]:
    """List bounded eligible rows while retaining exact row-id references."""
    if type(limit) is not int or not 1 <= limit <= MAX_MANIFEST_CHOICES:
        raise ValueError("manifest choice limit is invalid")
    if not isinstance(main_db_path, Path) or not main_db_path.is_absolute():
        raise ManifestError("primary manifest requires an absolute database path")
    raw = os.fspath(main_db_path)
    if "\\" in raw or "\x00" in raw:
        raise ManifestError("primary database path is invalid")
    uri = "file:" + quote(os.fspath(main_db_path.absolute()), safe="/") + "?mode=ro&cache=private"
    try:
        conn = sqlite3.connect(uri, uri=True, autocommit=True)
    except sqlite3.Error:
        raise ManifestError("primary extraction-summary database is unavailable") from None
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row["name"]) for row in conn.execute(
                "PRAGMA table_xinfo(extract_run_summaries)"
            ).fetchall()
        }
        required = _REQUIRED_DB_COLUMNS | {"created_at", "files_downloaded"}
        if not required.issubset(columns):
            raise ManifestError("primary extraction-summary schema is unavailable")
        rows = conn.execute(
            "SELECT id,ip_address,host_type,protocol_server_id,port,summary_json,"
            "created_at,files_downloaded FROM extract_run_summaries "
            "WHERE files_downloaded>0 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        choices: list[ExtractionManifestChoice] = []
        for row in rows:
            identity = _identity(
                row["host_type"], row["ip_address"],
                row["protocol_server_id"], row["port"],
            )
            summary = _decode_summary(row["summary_json"])
            _require_summary_identity(summary, identity)
            count = row["files_downloaded"]
            created_at = row["created_at"]
            if (
                type(row["id"]) is not int
                or row["id"] <= 0
                or type(count) is not int
                or count <= 0
                or count != _summary_file_count(summary)
                or type(created_at) is not str
                or not created_at
            ):
                raise ManifestError("primary extraction-summary row is invalid")
            choices.append(ExtractionManifestChoice(
                ExtractSummaryReference(
                    row["id"], None, ExtractSummarySource.PRIMARY_DB,
                ),
                identity[0], identity[1], created_at, count,
            ))
        return tuple(choices)
    except (sqlite3.Error, ValueError, TypeError):
        raise ManifestError("primary extraction-summary list failed closed") from None
    finally:
        conn.close()


def load_extraction_manifest(
    reference: ExtractSummaryReference,
    *,
    main_db_path: Path | None = None,
    cancel_check=None,
) -> ExtractionManifest:
    """Load one exact row/file and inventory only its final saved paths."""
    if type(reference) is not ExtractSummaryReference:
        raise TypeError("manifest load requires an ExtractSummaryReference")
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")
    try:
        if reference.source is ExtractSummarySource.PRIMARY_DB:
            if not isinstance(main_db_path, Path) or not main_db_path.is_absolute():
                raise ManifestError("primary manifest requires an absolute database path")
            identity, summary = _load_database(reference, main_db_path)
        else:
            identity, summary = _load_fallback(reference)
        saved = _saved_paths(summary)
        root, relatives = _common_root(saved)
        inventory = inventory_selected_paths(
            root, relatives, cancel_check=cancel_check,
        )
        return ExtractionManifest(
            reference,
            root,
            inventory,
            identity[0],
            identity[1],
            identity[2],
            identity[3],
        )
    except ManifestError:
        raise
    except Exception:
        raise ManifestError("extraction manifest failed closed") from None


def _load_database(
    reference: ExtractSummaryReference, path: Path,
) -> tuple[tuple[str, str, int | None, int | None], dict[str, object]]:
    raw = os.fspath(path)
    if "\\" in raw or "\x00" in raw:
        raise ManifestError("primary database path is invalid")
    uri = "file:" + quote(os.fspath(path.absolute()), safe="/") + "?mode=ro&cache=private"
    conn = sqlite3.connect(uri, uri=True, autocommit=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row["name"]) for row in conn.execute(
                "PRAGMA table_xinfo(extract_run_summaries)"
            ).fetchall()
        }
        if not _REQUIRED_DB_COLUMNS.issubset(columns):
            raise ManifestError("primary extraction-summary schema is unavailable")
        row = conn.execute(
            "SELECT id,ip_address,host_type,protocol_server_id,port,summary_json,"
            "files_downloaded "
            "FROM extract_run_summaries WHERE id=?",
            (reference.db_row_id,),
        ).fetchone()
        if (
            row is None
            or type(row["id"]) is not int
            or row["id"] != reference.db_row_id
        ):
            raise ManifestError("extraction-summary row does not exist")
        identity = _identity(
            row["host_type"], row["ip_address"],
            row["protocol_server_id"], row["port"],
        )
        summary = _decode_summary(row["summary_json"])
        _require_summary_identity(summary, identity)
        if (
            type(row["files_downloaded"]) is not int
            or row["files_downloaded"] != _summary_file_count(summary)
        ):
            raise ManifestError("extraction-summary row count is contradictory")
        return identity, summary
    finally:
        conn.close()


def _load_fallback(
    reference: ExtractSummaryReference,
) -> tuple[tuple[str, str, int | None, int | None], dict[str, object]]:
    path = reference.fallback_log_path
    assert path is not None
    fd = _open_fallback_no_follow(path)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size <= 0
            or info.st_size > MAX_SUMMARY_BYTES
        ):
            raise ManifestError("fallback extraction summary is unsafe")
        payload = _read_bounded(fd, info.st_size)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        ):
            raise ManifestError("fallback extraction summary changed during read")
    finally:
        os.close(fd)
    envelope = _decode_json(payload)
    if set(envelope) != {"host", "schema", "summary"}:
        raise ManifestError("fallback extraction summary is not an eligible envelope")
    if envelope["schema"] != "dirracuda-extract-summary-v1":
        raise ManifestError("fallback extraction summary version is unsupported")
    host = envelope["host"]
    if type(host) is not dict or set(host) != {
        "host_type", "ip_address", "port", "protocol_server_id",
    }:
        raise ManifestError("fallback host identity is invalid")
    identity = _identity(
        host["host_type"], host["ip_address"],
        host["protocol_server_id"], host["port"],
    )
    summary = envelope["summary"]
    if type(summary) is not dict:
        raise ManifestError("fallback extraction summary is invalid")
    _require_summary_identity(summary, identity)
    return identity, summary


def _open_fallback_no_follow(path: Path) -> int:
    raw = os.fspath(path)
    parts = tuple(raw.split("/")[1:])
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current = os.open("/", directory_flags)
    try:
        for component in parts[:-1]:
            child = os.open(component, directory_flags, dir_fd=current)
            os.close(current)
            current = child
        return os.open(
            parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=current,
        )
    except OSError:
        raise ManifestError("fallback extraction summary is unsafe") from None
    finally:
        os.close(current)


def _decode_summary(value: object) -> dict[str, object]:
    if type(value) is not str or not value or len(value.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise ManifestError("database extraction summary is invalid")
    return _decode_json(value.encode("utf-8"))


def _decode_json(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("nonfinite JSON")
            ),
        )
    except (UnicodeError, ValueError, TypeError):
        raise ManifestError("extraction summary JSON is invalid") from None
    if type(value) is not dict:
        raise ManifestError("extraction summary JSON must be an object")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _identity(host_type, ip_address, server_id, port):
    if type(host_type) is not str or host_type.upper() not in {"S", "F", "H"}:
        raise ManifestError("manifest host type is invalid")
    if (
        type(ip_address) is not str
        or not ip_address.strip()
        or ip_address != ip_address.strip()
        or len(ip_address) > 255
        or any(ord(char) < 32 or ord(char) == 127 for char in ip_address)
    ):
        raise ManifestError("manifest address is invalid")
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        raise ManifestError("manifest address is invalid") from None
    if server_id is not None and (type(server_id) is not int or server_id <= 0):
        raise ManifestError("manifest server id is invalid")
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ManifestError("manifest port is invalid")
    return host_type.upper(), ip_address, server_id, port


def _require_summary_identity(summary: dict[str, object], identity) -> None:
    for key, expected in (
        ("host_type", identity[0]),
        ("ip_address", identity[1]),
        ("protocol_server_id", identity[2]),
        ("port", identity[3]),
    ):
        value = summary.get(key)
        if value is not None and value != expected:
            raise ManifestError("summary contradicts its durable host identity")


def _summary_file_count(summary: dict[str, object]) -> int:
    files = summary.get("files")
    if type(files) is not list:
        raise ManifestError("extraction summary has no saved-file list")
    return len(files)


def _saved_paths(summary: dict[str, object]) -> tuple[Path, ...]:
    files = summary.get("files")
    if type(files) is not list or not files:
        raise ManifestError("extraction summary has no saved files")
    result: list[Path] = []
    seen: set[str] = set()
    for item in files:
        if type(item) is not dict or type(item.get("saved_to")) is not str:
            raise ManifestError("extraction summary saved path is invalid")
        raw = item["saved_to"]
        path = Path(raw)
        if (
            not path.is_absolute()
            or "\\" in raw
            or "\x00" in raw
            or any(part in {"", ".", ".."} for part in raw.split("/")[1:])
            or raw in seen
        ):
            raise ManifestError("extraction summary saved path is not canonical")
        seen.add(raw)
        result.append(path)
    totals = summary.get("totals")
    if type(totals) is dict and "files_downloaded" in totals:
        count = totals["files_downloaded"]
        if type(count) is not int or count != len(result):
            raise ManifestError("extraction summary file count is contradictory")
    return tuple(result)


def _common_root(paths: tuple[Path, ...]) -> tuple[Path, tuple[str, ...]]:
    try:
        root = Path(os.path.commonpath(tuple(str(path.parent) for path in paths)))
    except ValueError:
        raise ManifestError("manifest paths have no common root") from None
    if root == Path("/"):
        raise ManifestError("manifest common root is too broad")
    relatives = tuple(path.relative_to(root).as_posix() for path in paths)
    return root, relatives


def _read_bounded(fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            raise ManifestError("fallback extraction summary was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise ManifestError("fallback extraction summary exceeded its bound")
    return b"".join(chunks)


__all__ = [
    "ExtractionManifest",
    "ExtractionManifestChoice",
    "MAX_MANIFEST_CHOICES",
    "MAX_SUMMARY_BYTES",
    "ManifestError",
    "list_extraction_manifests",
    "load_extraction_manifest",
]
