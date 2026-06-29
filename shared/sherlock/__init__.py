"""
Sherlock: pure exposure-triage matcher and settings model (C1).

Display-only keyword/wildcard matching over probe snapshot paths. This package
never downloads files, reads file contents, authenticates, or probes — it only
matches in-memory path strings (R1/R11). Persistence and GUI live in later cards.
"""

from __future__ import annotations

from .matcher import (
    MatchResult,
    SherlockHit,
    match_entries,
    select_display_color_tag,
)
from .model import (
    COLOR_TAG_NONE,
    DEFAULT_COLORS,
    DEFAULT_USER_COLORS,
    USER_COLOR_KEYS,
    VALID_COLOR_TAGS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    builtin_patterns,
    category_choices,
    default_settings,
    is_valid_color,
    is_valid_user_color,
    normalize_color_tag,
    validate_color,
    validate_user_color,
)
from .path_entry import (
    SherlockPathEntry,
    path_entries_from_rows,
    path_entries_from_snapshot,
)
from .serialize import (
    SHERLOCK_SETTINGS_KEY,
    settings_from_dict,
    settings_to_dict,
    severity_from_str,
    severity_to_str,
)

__all__ = [
    "COLOR_TAG_NONE",
    "DEFAULT_COLORS",
    "DEFAULT_USER_COLORS",
    "MatchResult",
    "SHERLOCK_SETTINGS_KEY",
    "Severity",
    "SherlockHit",
    "SherlockPathEntry",
    "SherlockPattern",
    "SherlockSettings",
    "USER_COLOR_KEYS",
    "VALID_COLOR_TAGS",
    "builtin_patterns",
    "category_choices",
    "default_settings",
    "is_valid_color",
    "is_valid_user_color",
    "match_entries",
    "normalize_color_tag",
    "path_entries_from_rows",
    "path_entries_from_snapshot",
    "select_display_color_tag",
    "settings_from_dict",
    "settings_to_dict",
    "severity_from_str",
    "severity_to_str",
    "validate_color",
    "validate_user_color",
]
