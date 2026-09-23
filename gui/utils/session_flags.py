"""
In-process session flags.

Module-level dict; resets on every app restart. No persistence, no GUI coupling.
"""

from __future__ import annotations

_flags: dict = {}

CLAMAV_MUTE_KEY = "clamav_results_dialog_muted"
REDDIT_PROMOTION_NOTICE_MUTE_KEY = "reddit_promotion_notice_muted"
DORKBOOK_DELETE_CONFIRM_MUTE_KEY = "dorkbook_delete_confirm_muted"
#: Mutes the Analyst remote-egress confirmation for this session only.
#: Resets on restart, like every flag here (remote-backends 10.2).
ANALYST_REMOTE_EGRESS_MUTE_KEY = "analyst_remote_egress_muted"


def set_flag(key: str, value: bool = True) -> None:
    """Set a session flag."""
    _flags[key] = value


def get_flag(key: str, default: bool = False) -> bool:
    """Return a session flag value, or *default* if not set."""
    return _flags.get(key, default)


def clear_flag(key: str) -> None:
    """Remove a session flag (no-op if not set)."""
    _flags.pop(key, None)
