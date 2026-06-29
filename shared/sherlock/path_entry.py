"""
Canonical Sherlock path-entry shape and pure adapters.

A SherlockPathEntry is the only thing the matcher consumes. Adapters convert
already-in-memory snapshot data (normalized DB rows or raw snapshot dicts) into
path entries. They never read the database, filesystem, or network — callers
load the data and hand it in (MD-3). Storage reads belong to later cards.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclasses.dataclass(frozen=True)
class SherlockPathEntry:
    """A normalized path to match against.

    display_path: full normalized path, ORIGINAL casing (used for output).
    segments: individual path components, ORIGINAL casing (MD-1).
    container: share / container name when present (MD-2).
    """

    display_path: str
    segments: Tuple[str, ...]
    container: Optional[str] = None


def _normalize(raw: str) -> Tuple[str, Tuple[str, ...]]:
    """Normalize separators and split into segments.

    Returns (display_path, segments). Casing is preserved; only `\\` -> `/`
    conversion, empty-segment collapse, and `.` dropping happen here.
    """
    text = str(raw or "").replace("\\", "/")
    segments = tuple(seg for seg in text.split("/") if seg and seg != ".")
    return "/".join(segments), segments


def _join_normalize(*parts: Any) -> Tuple[str, Tuple[str, ...]]:
    """Join non-empty parts with `/`, then normalize."""
    raw = "/".join(str(p) for p in parts if p)
    return _normalize(raw)


def _row_value(row: Any, key: str) -> Any:
    """Tolerant getter supporting Mapping, sqlite3.Row, and attribute objects."""
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        pass
    return getattr(row, key, None)


def _make_entry(container: Optional[str], *path_parts: Any) -> Optional[SherlockPathEntry]:
    """Build an entry from container + path parts, or None if it normalizes empty."""
    display_path, segments = _join_normalize(*path_parts)
    if not display_path:
        return None
    container_str = str(container).strip() if container else None
    return SherlockPathEntry(
        display_path=display_path,
        segments=segments,
        container=container_str or None,
    )


def path_entries_from_rows(rows: Iterable[Any]) -> List[SherlockPathEntry]:
    """Adapt already-loaded normalized `probe_snapshot_entries` rows.

    Row shape: share_name, entry_kind, path, parent_path, is_truncated,
    metadata_json. `path` already holds the full relative path within the share,
    so display_path = share_name + path. `parent_path` is metadata and is used
    only as a fallback when `path` is blank (prepending it would duplicate the
    directory prefix). No DB reads here — rows are passed in.
    """
    entries: List[SherlockPathEntry] = []
    for row in rows or []:
        share_name = _row_value(row, "share_name")
        rel = _row_value(row, "path")
        rel_str = str(rel).strip() if rel else ""
        if not rel_str:
            parent = _row_value(row, "parent_path")
            rel_str = str(parent).strip() if parent else ""
        if not rel_str:
            continue
        entry = _make_entry(share_name, share_name, rel_str)
        if entry is not None:
            entries.append(entry)
    return entries


def _share_name(share: Dict[str, Any]) -> str:
    for key in ("share", "share_name", "name"):
        value = share.get(key)
        if value:
            return str(value)
    return ""


def _directory_name(directory: Any) -> str:
    if isinstance(directory, str):
        return directory
    if isinstance(directory, Mapping):
        for key in ("name", "path", "directory"):
            value = directory.get(key)
            if value:
                return str(value)
    return ""


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def path_entries_from_snapshot(snapshot: Any) -> List[SherlockPathEntry]:
    """Adapt a raw in-memory probe snapshot dict.

    Probe runners emit each directory as
    {"name": <top-level dir>, "subdirectories": [...], "files": [...]} where the
    subdirectories/files are paths RELATIVE to that directory (they do not include
    the directory name). The full path is therefore
    share + directory_name + nested. Root files sit directly under the share.
    Tolerant of malformed entries — they are skipped, not raised.
    """
    entries: List[SherlockPathEntry] = []
    if not isinstance(snapshot, Mapping):
        return entries

    for share in _as_list(snapshot.get("shares")):
        if not isinstance(share, Mapping):
            continue
        container = _share_name(share)

        for root_file in _as_list(share.get("root_files")):
            if not root_file:
                continue
            entry = _make_entry(container, container, root_file)
            if entry is not None:
                entries.append(entry)

        for directory in _as_list(share.get("directories")):
            dir_name = _directory_name(directory)
            if not dir_name:
                continue

            dir_entry = _make_entry(container, container, dir_name)
            if dir_entry is not None:
                entries.append(dir_entry)

            if not isinstance(directory, Mapping):
                continue

            for sub_rel in _as_list(directory.get("subdirectories")):
                if not sub_rel:
                    continue
                entry = _make_entry(container, container, dir_name, sub_rel)
                if entry is not None:
                    entries.append(entry)

            for file_rel in _as_list(directory.get("files")):
                if not file_rel:
                    continue
                entry = _make_entry(container, container, dir_name, file_rel)
                if entry is not None:
                    entries.append(entry)

    return entries
