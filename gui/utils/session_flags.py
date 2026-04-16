"""
In-process session flags.

Module-level dict; resets on every app restart. No persistence, no GUI coupling.
"""

from __future__ import annotations

_flags: dict = {}

CLAMAV_MUTE_KEY = "clamav_results_dialog_muted"
REDDIT_PROMOTION_NOTICE_MUTE_KEY = "reddit_promotion_notice_muted"


def set_flag(key: str, value: bool = True) -> None:
    """Set a session flag."""
    _flags[key] = value


def get_flag(key: str, default: bool = False) -> bool:
    """Return a session flag value, or *default* if not set."""
    return _flags.get(key, default)


def clear_flag(key: str) -> None:
    """Remove a session flag (no-op if not set)."""
    _flags.pop(key, None)
