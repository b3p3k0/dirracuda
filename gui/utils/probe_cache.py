"""
Probe result caching utilities.

Stores probe snapshots per IP under ~/.dirracuda/data/cache/probes/smb so the GUI can
reuse previous runs without talking to the backend again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from shared.path_service import get_paths, get_legacy_paths

_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)
CACHE_DIR = _PATHS.cache_probe_smb_dir
_LEGACY_CACHE_DIRS = [
    _LEGACY.flat_probe_smb_dir,
    _LEGACY.legacy_home_root / "probes",
]


def _sanitize_ip(ip_address: str) -> str:
    """Return filesystem-safe token for an IP or hostname."""
    return ip_address.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_cache_path(ip_address: str, *, create_dir: bool = True) -> Path:
    """Return cache file path for the given IP."""
    safe_name = _sanitize_ip(ip_address)
    if create_dir:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{safe_name}.json"


def load_probe_result(ip_address: str) -> Optional[Dict[str, Any]]:
    """Load cached probe result for an IP (returns None if missing)."""
    cache_path = get_cache_path(ip_address, create_dir=False)
    if not cache_path.exists():
        safe_name = _sanitize_ip(ip_address)
        for legacy_dir in _LEGACY_CACHE_DIRS:
            legacy_path = legacy_dir / f"{safe_name}.json"
            if legacy_path.exists():
                cache_path = legacy_path
                break
        else:
            return None
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            result = json.load(handle)

            # Ensure backward compatibility with older cache files
            # that don't have RCE analysis data
            if result and "rce_analysis" not in result:
                result["rce_analysis"] = None

            return result
    except Exception:
        return None


def save_probe_result(ip_address: str, result: Dict[str, Any]) -> None:
    """Persist probe result for later reuse."""
    cache_path = get_cache_path(ip_address, create_dir=True)
    try:
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    except Exception:
        pass


def clear_probe_result(ip_address: str) -> None:
    """Delete cached probe result (if present)."""
    safe_name = _sanitize_ip(ip_address)
    targets = [CACHE_DIR / f"{safe_name}.json"]
    targets.extend(p / f"{safe_name}.json" for p in _LEGACY_CACHE_DIRS)
    try:
        for cache_path in targets:
            if cache_path.exists():
                cache_path.unlink()
    except Exception:
        pass
