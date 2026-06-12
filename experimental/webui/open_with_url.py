"""Helpers for server-derived "open with system" URLs on Web UI results rows."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _format_root_file_name(item: object) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return _clean_text(
            item.get("name")
            or item.get("file_name")
            or item.get("file")
        )
    return _clean_text(item)


def split_csv_items(value: object) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [item for item in (_clean_text(part) for part in text.split(",")) if item]


def snapshot_open_path_candidates(
    snapshot: Optional[dict],
    *,
    include_share_names: bool = False,
) -> list[str]:
    if not isinstance(snapshot, dict):
        return []

    out: list[str] = []
    for share in _safe_list(snapshot.get("shares")):
        if not isinstance(share, dict):
            continue

        share_name = _clean_text(share.get("share"))
        if include_share_names and share_name:
            out.append(share_name)

        for directory in _safe_list(share.get("directories")):
            if not isinstance(directory, dict):
                continue
            dir_name = _clean_text(directory.get("name"))
            if dir_name:
                out.append(dir_name)
            for subdir in _safe_list(directory.get("subdirectories")):
                subdir_text = _clean_text(subdir)
                if subdir_text:
                    out.append(subdir_text)
            for file_name in _safe_list(directory.get("files")):
                file_text = _clean_text(file_name)
                if file_text:
                    out.append(file_text)

        for root_item in _safe_list(share.get("root_files")):
            root_name = _format_root_file_name(root_item)
            if root_name:
                out.append(root_name)

    return out


def _normalize_open_path_candidate(path_value: object) -> str:
    text = _clean_text(path_value).replace("\\", "/")
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"(none)", "(unnamed)"}:
        return ""
    if text in {"/", "."}:
        return "/"

    normalized = "/" + text.lstrip("/")
    normalized = re.sub(r"/{2,}", "/", normalized)
    if normalized.startswith("/./"):
        normalized = normalized[2:]
    if normalized == "/.":
        normalized = "/"
    return normalized


def _choose_best_open_path(
    candidates: list[str],
    *,
    explicit_base_path: object = None,
    prefer_non_root: bool = False,
    prefer_root_when_ambiguous: bool = False,
) -> str:
    explicit = _normalize_open_path_candidate(explicit_base_path)
    if explicit:
        if explicit != "/":
            return explicit
        if prefer_root_when_ambiguous:
            return "/"

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = _normalize_open_path_candidate(candidate)
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)

    if not normalized:
        return "/"
    if prefer_root_when_ambiguous:
        return "/"
    if prefer_non_root:
        for path in normalized:
            if path != "/":
                return path
    return normalized[0]


def snapshot_explicit_base_path(snapshot: Optional[dict]) -> str:
    if not isinstance(snapshot, dict):
        return ""
    return _normalize_open_path_candidate(snapshot.get("start_path"))


def safe_port(value: object, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if 1 <= port <= 65535:
        return port
    return default


def build_open_with_url(
    *,
    scheme: str,
    host: object,
    path_candidates: list[str],
    explicit_base_path: object = None,
    default_port: Optional[int] = None,
    prefer_non_root: bool = False,
    prefer_root_when_ambiguous: bool = False,
) -> str:
    scheme_text = _clean_text(scheme).lower()
    host_text = _clean_text(host)
    if not scheme_text or not host_text:
        return ""
    path = _choose_best_open_path(
        path_candidates,
        explicit_base_path=explicit_base_path,
        prefer_non_root=prefer_non_root,
        prefer_root_when_ambiguous=prefer_root_when_ambiguous,
    )
    encoded_path = quote(path, safe="/")
    if default_port is None:
        return f"{scheme_text}://{host_text}{encoded_path}"
    return f"{scheme_text}://{host_text}:{default_port}{encoded_path}"
