"""Optional post-probe Sherlock hook (C5).

Fires Sherlock immediately after a successful probe snapshot is persisted, but
only when the user enabled `Sherlock -> Run after probe`. Reuses the C4
scan-and-persist helper `gui.utils.sherlock_scan.run_sherlock_scan` so post-probe
and standalone scans cannot drift (R12). Snapshot-only and fail-closed: it never
downloads files, reads file contents, authenticates, or performs network/probe
work, and a Sherlock failure must never turn a successful probe into a failed one.
"""

from __future__ import annotations

from typing import Any, Optional

from gui.utils.sherlock_scan import SherlockScanSummary, run_sherlock_scan
from shared.sherlock import (
    SHERLOCK_SETTINGS_KEY,
    SherlockSettings,
    default_settings,
    settings_from_dict,
    settings_to_dict,
)


def load_sherlock_settings(settings_manager: Any) -> SherlockSettings:
    """Load validated Sherlock settings, or safe defaults (run_after_probe=False).

    Any missing manager or read/parse failure degrades to default_settings(),
    which has run_after_probe disabled - so the hook fails closed.
    """
    if settings_manager is None:
        return default_settings()
    try:
        return settings_from_dict(settings_manager.get_setting(SHERLOCK_SETTINGS_KEY, {}))
    except Exception:
        return default_settings()


def set_run_after_probe(settings_manager: Any, enabled: bool) -> bool:
    """Persist sherlock.run_after_probe, preserving all other Sherlock settings.

    Round-trips through the current settings so colors, patterns, and ignore_case
    are never clobbered. Returns True on success; fail-safe (False) when the
    manager is missing or anything raises - callers treat it as a best-effort write.
    """
    if settings_manager is None:
        return False
    try:
        settings = load_sherlock_settings(settings_manager)
        settings.run_after_probe = bool(enabled)
        return bool(
            settings_manager.set_setting(SHERLOCK_SETTINGS_KEY, settings_to_dict(settings))
        )
    except Exception:
        return False


def run_sherlock_after_probe(
    settings_manager: Any,
    db_reader: Any,
    ip_address: str,
    host_type: str,
    *,
    protocol_server_id: Any = None,
    port: Any = None,
) -> Optional[SherlockScanSummary]:
    """Re-run Sherlock for one just-probed host when run_after_probe is enabled.

    No-ops silently when the reader is missing, the setting is disabled, settings
    fail to load, or anything raises - the probe outcome is never affected.
    Display-only: delegates to run_sherlock_scan (DB read/write + the pure
    matcher), never any probe/network/content work. Callers gate on a real
    snapshot_id before calling; the returned summary exists only for tests.
    """
    try:
        if db_reader is None:
            return None
        settings = load_sherlock_settings(settings_manager)
        if not settings.run_after_probe:
            return None
        return run_sherlock_scan(
            db_reader,
            settings,
            [
                {
                    "ip_address": ip_address,
                    "host_type": host_type,
                    "protocol_server_id": protocol_server_id,
                    "port": port,
                }
            ],
        )
    except Exception:
        return None
