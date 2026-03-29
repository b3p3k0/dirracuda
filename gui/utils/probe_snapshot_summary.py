"""
Helpers for summarizing probe snapshots into server-list display fields.

Contract for protocol implementations:
- Emit snapshot["shares"] as a list of share-like dicts.
- Each share dict may include:
  - "directories": list of dicts with optional "name" and "files"
  - "root_files": list of file names at the root level

If a protocol follows this shape, these helpers can be reused without
protocol-specific branching.
"""

from __future__ import annotations

from typing import Any, Dict, List


LOOSE_FILES_DISPLAY_TOKEN = "[[loose files]]"


def _safe_list(value: Any) -> List[Any]:
    """Return value as list-like for tolerant snapshot parsing."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return []


def summarize_probe_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Summarize protocol probe snapshot data for UI list/count usage.

    Returns:
      {
        "directory_names": [str, ...],     # deduplicated, order-preserving
        "display_entries": [str, ...],     # directory_names + loose-file token
        "has_loose_root_files": bool,
        "root_file_count": int,            # sampled/root files in snapshot
        "nested_file_count": int,          # sampled files inside directories
        "total_file_count": int,           # root + nested sampled files
      }
    """
    shares = _safe_list((snapshot or {}).get("shares"))
    directory_names: List[str] = []
    seen_dirs = set()
    root_file_count = 0
    nested_file_count = 0

    for share in shares:
        if not isinstance(share, dict):
            continue

        root_files = _safe_list(share.get("root_files"))
        root_file_count += len(root_files)

        directories = _safe_list(share.get("directories"))
        for directory in directories:
            if not isinstance(directory, dict):
                continue

            name = str(directory.get("name") or "").strip()
            if name and name not in seen_dirs:
                seen_dirs.add(name)
                directory_names.append(name)

            nested_file_count += len(_safe_list(directory.get("files")))

    has_loose_root_files = root_file_count > 0
    display_entries = list(directory_names)
    if has_loose_root_files and LOOSE_FILES_DISPLAY_TOKEN not in display_entries:
        display_entries.append(LOOSE_FILES_DISPLAY_TOKEN)

    return {
        "directory_names": directory_names,
        "display_entries": display_entries,
        "has_loose_root_files": has_loose_root_files,
        "root_file_count": root_file_count,
        "nested_file_count": nested_file_count,
        "total_file_count": root_file_count + nested_file_count,
    }
