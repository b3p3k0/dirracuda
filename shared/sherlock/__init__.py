"""
Sherlock: pure exposure-triage matcher and settings model (C1).

Display-only keyword/wildcard matching over probe snapshot paths. This package
never downloads files, reads file contents, authenticates, or probes — it only
matches in-memory path strings (R1/R11). Persistence and GUI live in later cards.
"""

from __future__ import annotations

from .matcher import MatchResult, SherlockHit, match_entries
from .model import (
    DEFAULT_COLORS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    builtin_patterns,
    default_settings,
    is_valid_color,
    validate_color,
)
from .path_entry import (
    SherlockPathEntry,
    path_entries_from_rows,
    path_entries_from_snapshot,
)

__all__ = [
    "DEFAULT_COLORS",
    "MatchResult",
    "Severity",
    "SherlockHit",
    "SherlockPathEntry",
    "SherlockPattern",
    "SherlockSettings",
    "builtin_patterns",
    "default_settings",
    "is_valid_color",
    "match_entries",
    "path_entries_from_rows",
    "path_entries_from_snapshot",
    "validate_color",
]
