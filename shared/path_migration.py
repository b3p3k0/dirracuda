"""One-time user data migration helpers for ~/.smbseek -> ~/.dirracuda.

Deprecated for primary startup flow (layout-v2 uses shared.path_service), but
kept for backward compatibility with older callers/tests.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

_logger = logging.getLogger(__name__)

_LEGACY_DIR = Path.home() / ".smbseek"
_CANONICAL_DIR = Path.home() / ".dirracuda"


def legacy_user_data_needs_migration() -> bool:
    """Return True when legacy data exists and canonical dir does not."""
    return _LEGACY_DIR.exists() and not _CANONICAL_DIR.exists()


def migrate_user_data_root() -> bool:
    """
    One-time migration from ~/.smbseek to ~/.dirracuda.

    Returns:
        True on success (including no-op success), False on migration failure.
    """
    if _CANONICAL_DIR.exists():
        return True

    if not _LEGACY_DIR.exists():
        _CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
        return True

    try:
        shutil.move(str(_LEGACY_DIR), str(_CANONICAL_DIR))
        _logger.info("Migrated user data from %s to %s", _LEGACY_DIR, _CANONICAL_DIR)
        return True
    except Exception as exc:
        _logger.warning("Failed to migrate user data directory: %s", exc)
        return False
