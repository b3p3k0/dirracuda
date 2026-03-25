"""
Cache helpers for HTTP probe snapshots.

Snapshots are stored as JSON files under ~/.dirracuda/http_probes/.
When port is provided, filename is endpoint-specific: <ip>_<port>.json.
When port is omitted, legacy filename remains: <ip>.json.
The format mirrors the FTP probe snapshot so probe_patterns.py works unchanged.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

HTTP_CACHE_DIR = Path.home() / ".dirracuda" / "http_probes"


def _sanitize_ip(ip: str) -> str:
    """Return a filesystem-safe version of the IP string."""
    return ip.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_http_cache_path(ip: str, port: Optional[int] = None) -> Path:
    """Return the full path to the cache file for the given IP or endpoint."""
    base = _sanitize_ip(ip)
    if port is not None:
        try:
            endpoint_port = int(port)
        except (TypeError, ValueError):
            endpoint_port = None
        if endpoint_port is not None:
            return HTTP_CACHE_DIR / f"{base}_{endpoint_port}.json"
    return HTTP_CACHE_DIR / f"{base}.json"


def load_http_probe_result(ip: str, port: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Load a cached HTTP probe snapshot for ip.

    Returns the parsed dict, or None if the file doesn't exist or is unreadable.
    """
    path = get_http_cache_path(ip, port=port)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_http_probe_result(ip: str, result: Dict[str, Any], port: Optional[int] = None) -> None:
    """
    Persist a probe snapshot dict to the cache directory.

    Silently ignores write errors so cache failures are never fatal.
    """
    HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = get_http_cache_path(ip, port=port)
    try:
        path.write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def clear_http_probe_result(ip: str, port: Optional[int] = None) -> None:
    """Remove the cached snapshot for ip (no-op if absent)."""
    try:
        get_http_cache_path(ip, port=port).unlink(missing_ok=True)
    except Exception:
        pass
