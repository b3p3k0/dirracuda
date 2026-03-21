"""
Cache helpers for HTTP probe snapshots.

Snapshots are stored as JSON files under ~/.smbseek/http_probes/<ip>.json.
The format mirrors the FTP probe snapshot so probe_patterns.py works unchanged.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

HTTP_CACHE_DIR = Path.home() / ".smbseek" / "http_probes"


def _sanitize_ip(ip: str) -> str:
    """Return a filesystem-safe version of the IP string."""
    return ip.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_http_cache_path(ip: str) -> Path:
    """Return the full path to the cache file for the given IP."""
    return HTTP_CACHE_DIR / f"{_sanitize_ip(ip)}.json"


def load_http_probe_result(ip: str) -> Optional[Dict[str, Any]]:
    """
    Load a cached HTTP probe snapshot for ip.

    Returns the parsed dict, or None if the file doesn't exist or is unreadable.
    """
    path = get_http_cache_path(ip)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_http_probe_result(ip: str, result: Dict[str, Any]) -> None:
    """
    Persist a probe snapshot dict to the cache directory.

    Silently ignores write errors so cache failures are never fatal.
    """
    HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = get_http_cache_path(ip)
    try:
        path.write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def clear_http_probe_result(ip: str) -> None:
    """Remove the cached snapshot for ip (no-op if absent)."""
    try:
        get_http_cache_path(ip).unlink(missing_ok=True)
    except Exception:
        pass
