"""
Pure JSON-export payload builder for the Sherlock pattern catalog.

Turns a list of SherlockPattern rows into a plain, JSON-serializable dict with a
small metadata header. Like serialize.py this module performs no file, DB, or
network I/O, so the Sherlock package stays pure (see test_sherlock_purity). The
caller owns the Save As dialog and the actual file write.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .model import SherlockPattern, normalize_color_tag
from .serialize import severity_to_str

EXPORT_SCHEMA_NAME = "dirracuda.sherlock.patterns"
EXPORT_SCHEMA_VERSION = 1


def build_export_payload(
    patterns: Iterable[SherlockPattern], *, exported_at: str
) -> Dict[str, Any]:
    """Build the export dict for *patterns* (read-only; preserves input order).

    Severities use the stable lowercase tokens from serialize.severity_to_str;
    color tags are normalized via model.normalize_color_tag. The caller supplies
    *exported_at* (an ISO-8601 string) so timestamping stays out of this pure
    module.
    """
    rows: List[Dict[str, Any]] = [
        {
            "key": p.key,
            "type": "builtin" if p.builtin else "custom",
            "enabled": bool(p.enabled),
            "severity": severity_to_str(p.severity),
            "category": p.category,
            "label": p.label,
            "pattern": p.pattern,
            "color_tag": normalize_color_tag(p.color_tag),
        }
        for p in patterns
    ]
    return {
        "schema": EXPORT_SCHEMA_NAME,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "count": len(rows),
        "patterns": rows,
    }
