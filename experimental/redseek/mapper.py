"""
Shared row-to-prefill mapper for Reddit target rows.

Used by both the run-sync path (main_db_sync.py) and the browser manual-promotion
path (reddit_browser_window._build_prefill) so that both callers produce identical
prefill shapes. Source labels are passed explicitly by each caller.

    Sync path:    promotion_source="reddit_run_sync",  snapshot_source="reddit:run_sync"
    Browser path: promotion_source="reddit_browser",   snapshot_source="sidecar:reddit"
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse


def _parse_probe_snapshot(value) -> Optional[dict]:
    """Return probe_snapshot_json as a dict, or None if absent/invalid."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_path(raw: str) -> str:
    path = (raw or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    return path


def row_to_prefill(
    row: dict,
    *,
    promotion_source: str,
    snapshot_source: str,
) -> Optional[dict]:
    """
    Map a reddit_targets row dict to a sidecar-promotion prefill dict.

    Returns None for rows whose protocol is unsupported or whose host is empty.
    Never raises.

    Parameters
    ----------
    row:
        Dict with keys: protocol, host, target_normalized, probe_status,
        probe_indicator_matches, probe_preview, probe_checked_at, probe_error,
        probe_snapshot_json.
    promotion_source:
        Value for the ``_promotion_source`` prefill key.
    snapshot_source:
        Value for the ``_probe_snapshot_source`` prefill key.
    """
    try:
        return _row_to_prefill_inner(row, promotion_source, snapshot_source)
    except Exception:
        return None


def _row_to_prefill_inner(
    row: dict,
    promotion_source: str,
    snapshot_source: str,
) -> Optional[dict]:
    protocol = (row.get("protocol") or "").lower().strip()
    if protocol in ("http", "https"):
        host_type, scheme = "H", protocol
    elif protocol == "ftp":
        host_type, scheme = "F", None
    elif protocol == "smb":
        host_type, scheme = "S", None
    else:
        return None

    host = str(row.get("host") or "").strip()
    if not host:
        return None

    url = row.get("target_normalized") or ""
    port: Optional[int] = None
    if url:
        try:
            port = urlparse(url).port
        except Exception:
            port = None
    # Fallback: handle bare host:port form with no scheme
    if port is None and url and "://" not in url:
        segment = url.split("/")[0]
        if ":" in segment:
            try:
                port = int(segment.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                port = None

    prefill: dict = {
        "host_type": host_type,
        "host": host,
        "port": port,
        "scheme": scheme,
        "_promotion_source": promotion_source,
        "_probe_cache": {
            "status": row.get("probe_status"),
            "indicator_matches": row.get("probe_indicator_matches"),
            "preview": row.get("probe_preview"),
            "checked_at": row.get("probe_checked_at"),
            "error": row.get("probe_error"),
        },
        "_probe_snapshot_source": snapshot_source,
    }

    if host_type == "H":
        parsed = None
        try:
            parse_target = url if "://" in url else f"{scheme or 'http'}://{url}"
            parsed = urlparse(parse_target) if parse_target else None
        except Exception:
            parsed = None
        probe_host_hint = (
            (parsed.hostname if parsed is not None else None) or host
        )
        probe_path_hint = _normalize_path(
            (parsed.path if parsed is not None else "") or "/"
        )
        prefill["_probe_host_hint"] = probe_host_hint
        prefill["_probe_path_hint"] = probe_path_hint

    snapshot = _parse_probe_snapshot(row.get("probe_snapshot_json"))
    if snapshot is not None:
        prefill["_probe_snapshot"] = snapshot

    return prefill


__all__ = ["row_to_prefill"]
