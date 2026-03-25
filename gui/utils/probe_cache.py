"""
Probe result caching utilities.

Stores probe snapshots per IP under ~/.dirracuda/probes so the GUI can
reuse previous runs without talking to the backend again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_DIR = Path.home() / ".dirracuda" / "probes"


def _sanitize_ip(ip_address: str) -> str:
    """Return filesystem-safe token for an IP or hostname."""
    return ip_address.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_cache_path(ip_address: str) -> Path:
    """Return cache file path for the given IP."""
    safe_name = _sanitize_ip(ip_address)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{safe_name}.json"


def load_probe_result(ip_address: str) -> Optional[Dict[str, Any]]:
    """Load cached probe result for an IP (returns None if missing)."""
    cache_path = get_cache_path(ip_address)
    if not cache_path.exists():
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
    cache_path = get_cache_path(ip_address)
    try:
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    except Exception:
        pass


def clear_probe_result(ip_address: str) -> None:
    """Delete cached probe result (if present)."""
    cache_path = get_cache_path(ip_address)
    try:
        if cache_path.exists():
            cache_path.unlink()
    except Exception:
        pass
