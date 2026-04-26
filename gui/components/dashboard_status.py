"""Pure helpers for dashboard runtime status composition (C6 extraction)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def coerce_bool_dashboard(value: Any) -> bool:
    """DashboardWidget._coerce_bool semantics.

    Diverges from gui.utils.coercion._coerce_bool:
    - No int/float branch: int(2) -> str("2") not in truthy set -> False
    - No `default` parameter
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_clamav_backend(value: Any) -> str:
    """Normalize backend mode to one of auto/clamdscan/clamscan."""
    backend = str(value or "auto").strip().lower()
    return backend if backend in {"auto", "clamdscan", "clamscan"} else "auto"


def compose_runtime_status_lines(
    clamav_cfg: Dict[str, Any],
    tmpfs_state: Dict[str, Any],
) -> tuple[str, str]:
    """Build ClamAV/tmpfs status lines from pre-loaded config dicts.

    Args:
        clamav_cfg:   dict with keys 'enabled' and 'backend' (already resolved)
        tmpfs_state:  dict with keys 'tmpfs_active' and 'mountpoint' (already resolved)

    Returns:
        (clamav_line, tmpfs_line) -- formatted status strings
    """
    clamav_enabled = coerce_bool_dashboard(clamav_cfg.get("enabled", False))
    clamav_icon = "\u2714" if clamav_enabled else "\u2716"
    clamav_line = f"{clamav_icon} ClamAV Integration"

    tmpfs_active = bool(tmpfs_state.get("tmpfs_active", False))
    mountpoint = str(
        tmpfs_state.get("mountpoint")
        or (Path.home() / ".dirracuda" / "quarantine_tmpfs")
    )
    tmpfs_icon = "\u2714" if tmpfs_active else "\u2716"
    tmpfs_line = f"{tmpfs_icon} tmpfs <{mountpoint}>"
    return clamav_line, tmpfs_line
