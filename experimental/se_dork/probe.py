"""
Probe helpers for se_dork retained results.

This module adapts the existing probe stack used by the main app:
- dispatch_probe_run
- probe_patterns.attach_indicator_analysis
- summarize_probe_snapshot
"""

from __future__ import annotations

import datetime
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from gui.utils.probe_cache_dispatch import dispatch_probe_run
from gui.utils import probe_patterns
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot

PROBE_STATUS_UNPROBED = "unprobed"
PROBE_STATUS_CLEAN = "clean"
PROBE_STATUS_ISSUE = "issue"


@dataclass
class ProbeOutcome:
    """Persistable probe outcome for one URL."""

    probe_status: str
    probe_indicator_matches: int
    probe_preview: Optional[str]
    probe_checked_at: str
    probe_error: Optional[str]


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _default_port_for_scheme(scheme: str) -> int:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return 21


def _parse_probe_target(url: str) -> tuple[str, str, int, str, Optional[str]]:
    """
    Parse URL into probe target fields.

    Returns: (host_type, host, port, path, scheme_or_none)
    Raises ValueError on unsupported/invalid inputs.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if scheme not in ("http", "https", "ftp"):
        raise ValueError("unsupported_scheme")
    if not host:
        raise ValueError("missing_hostname")

    try:
        port = parsed.port or _default_port_for_scheme(scheme)
    except ValueError as exc:
        raise ValueError("invalid_port") from exc

    start_path = parsed.path or "/"
    if not start_path.startswith("/"):
        start_path = "/" + start_path

    if scheme == "ftp":
        return ("F", host, port, start_path, None)
    return ("H", host, port, start_path, scheme)


def build_indicator_patterns(config_path: Optional[str]) -> list[tuple[str, object]]:
    indicators = probe_patterns.load_ransomware_indicators(config_path)
    return probe_patterns.compile_indicator_patterns(indicators)


def probe_url(
    url: str,
    *,
    config_path: Optional[str] = None,
    max_directories: int = 3,
    max_files: int = 5,
    timeout_seconds: int = 10,
    indicator_patterns: Optional[list[tuple[str, object]]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ProbeOutcome:
    """
    Probe one URL and return normalized probe fields.

    Status convention:
    - clean: no indicator matches
    - issue: indicator matches > 0
    - unprobed: unsupported/invalid URL or probe failure
    """
    checked_at = _utcnow()

    try:
        host_type, host, port, start_path, scheme = _parse_probe_target(url)
    except Exception as exc:
        return ProbeOutcome(
            probe_status=PROBE_STATUS_UNPROBED,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at=checked_at,
            probe_error=str(exc),
        )

    try:
        snapshot = dispatch_probe_run(
            host,
            host_type,
            max_directories=max(1, int(max_directories)),
            max_files=max(1, int(max_files)),
            timeout_seconds=max(1, int(timeout_seconds)),
            cancel_event=cancel_event or threading.Event(),
            port=port,
            scheme=scheme,
            request_host=host,
            start_path=start_path,
        )
    except Exception as exc:
        return ProbeOutcome(
            probe_status=PROBE_STATUS_UNPROBED,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at=checked_at,
            probe_error=str(exc),
        )

    if not isinstance(snapshot, dict):
        return ProbeOutcome(
            probe_status=PROBE_STATUS_UNPROBED,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at=checked_at,
            probe_error="invalid_probe_result",
        )

    errors = snapshot.get("errors")
    shares = snapshot.get("shares") or []
    if errors and not shares:
        error_text = "; ".join(str(e) for e in errors if e)
        return ProbeOutcome(
            probe_status=PROBE_STATUS_UNPROBED,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at=checked_at,
            probe_error=error_text or "probe_failed",
        )

    patterns = indicator_patterns
    if patterns is None:
        patterns = build_indicator_patterns(config_path)

    analysis = probe_patterns.attach_indicator_analysis(snapshot, patterns)
    matches = len(analysis.get("matches") or [])

    summary = summarize_probe_snapshot(snapshot)
    entries = [str(entry).strip() for entry in (summary.get("display_entries") or []) if str(entry).strip()]
    preview = ",".join(entries) if entries else None

    return ProbeOutcome(
        probe_status=PROBE_STATUS_ISSUE if matches > 0 else PROBE_STATUS_CLEAN,
        probe_indicator_matches=matches,
        probe_preview=preview,
        probe_checked_at=checked_at,
        probe_error=None,
    )
