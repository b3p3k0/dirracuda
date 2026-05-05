"""Shared probe adapter for experimental sidecar rows.

Sidecars should keep full probe snapshots when they expect promotion or detail
views to show the same probe tree as the main Server List Browser.
"""

from __future__ import annotations

import datetime
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from gui.utils import probe_patterns
from gui.utils.probe_cache_dispatch import dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot

PROBE_STATUS_UNPROBED = "unprobed"
PROBE_STATUS_CLEAN = "clean"
PROBE_STATUS_ISSUE = "issue"


class SidecarProbeUnsupported(ValueError):
    """Raised when a sidecar row cannot be probed safely."""


@dataclass(frozen=True)
class SidecarProbeTarget:
    """Normalized concrete target for the shared probe runner."""

    host_type: str
    host: str
    port: int
    scheme: Optional[str] = None
    request_host: Optional[str] = None
    start_path: str = "/"


@dataclass
class SidecarProbeOutcome:
    """Persistable probe outcome shared by sidecar stores."""

    probe_status: str
    probe_indicator_matches: int
    probe_preview: Optional[str]
    probe_checked_at: str
    probe_error: Optional[str]
    probe_snapshot_payload: Optional[dict[str, Any]] = None


def utcnow_iso() -> str:
    """Return a timezone-neutral UTC ISO timestamp for sidecar DB fields."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def build_indicator_patterns(config_path: Optional[str]) -> list[tuple[str, object]]:
    """Load and compile ransomware indicator patterns from active config."""
    indicators = probe_patterns.load_ransomware_indicators(config_path)
    return probe_patterns.compile_indicator_patterns(indicators)


def build_probe_target_from_sidecar_row(row: Mapping[str, Any]) -> SidecarProbeTarget:
    """Build a concrete probe target from a generic sidecar row mapping.

    Expected row keys mirror Reddit/SearXNG sidecar conventions:
    ``protocol``, ``target_normalized`` or ``url``, and ``host``.
    """
    protocol = str(row.get("protocol") or "").strip().lower()
    raw_target = str(row.get("target_normalized") or row.get("url") or "").strip()
    host_hint = str(row.get("host") or "").strip()

    if protocol in {"http", "https"}:
        return _build_http_target(raw_target, host_hint, protocol)
    if protocol == "ftp":
        return _build_ftp_target(raw_target, host_hint)
    if protocol in {"", "unknown"}:
        raise SidecarProbeUnsupported("protocol info is unavailable")
    if protocol == "smb":
        raise SidecarProbeUnsupported("SMB sidecar rows need share context before probing")
    raise SidecarProbeUnsupported(f"protocol '{protocol}' is not supported for sidecar probing")


def run_sidecar_probe(
    target: SidecarProbeTarget,
    *,
    config_path: Optional[str] = None,
    max_directories: int = 3,
    max_files: int = 5,
    timeout_seconds: int = 10,
    max_depth: int = 1,
    indicator_patterns: Optional[list[tuple[str, object]]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> SidecarProbeOutcome:
    """Run a sidecar probe and return summary fields plus full snapshot."""
    checked_at = utcnow_iso()
    try:
        snapshot = dispatch_probe_run(
            target.host,
            target.host_type,
            max_directories=max(1, int(max_directories)),
            max_files=max(1, int(max_files)),
            timeout_seconds=max(1, int(timeout_seconds)),
            max_depth=max(1, int(max_depth)),
            cancel_event=cancel_event or threading.Event(),
            port=target.port,
            scheme=target.scheme,
            request_host=target.request_host or target.host,
            start_path=target.start_path,
        )
    except Exception as exc:
        return SidecarProbeOutcome(
            probe_status=PROBE_STATUS_UNPROBED,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at=checked_at,
            probe_error=str(exc),
        )

    if not isinstance(snapshot, dict):
        return SidecarProbeOutcome(
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
        return SidecarProbeOutcome(
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
    entries = [
        str(entry).strip()
        for entry in (summary.get("display_entries") or [])
        if str(entry).strip()
    ]

    return SidecarProbeOutcome(
        probe_status=PROBE_STATUS_ISSUE if matches > 0 else PROBE_STATUS_CLEAN,
        probe_indicator_matches=matches,
        probe_preview=",".join(entries) if entries else None,
        probe_checked_at=checked_at,
        probe_error=None,
        probe_snapshot_payload=snapshot,
    )


def skipped_probe_outcome(reason: str) -> SidecarProbeOutcome:
    """Return an unprobed outcome for explicit unsupported/skip cases."""
    return SidecarProbeOutcome(
        probe_status=PROBE_STATUS_UNPROBED,
        probe_indicator_matches=0,
        probe_preview=None,
        probe_checked_at=utcnow_iso(),
        probe_error=reason,
    )


def _build_http_target(raw_target: str, host_hint: str, scheme: str) -> SidecarProbeTarget:
    parse_target = raw_target if "://" in raw_target else f"{scheme}://{raw_target or host_hint}"
    parsed = _parse(parse_target)
    host = parsed.hostname or host_hint
    if not host:
        raise SidecarProbeUnsupported("HTTP target is missing a hostname")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SidecarProbeUnsupported("HTTP target has an invalid port") from exc
    return SidecarProbeTarget(
        host_type="H",
        host=host,
        port=port,
        scheme=scheme,
        request_host=host,
        start_path=_normalize_start_path(parsed.path),
    )


def _build_ftp_target(raw_target: str, host_hint: str) -> SidecarProbeTarget:
    parse_target = raw_target if "://" in raw_target else f"ftp://{raw_target or host_hint}"
    parsed = _parse(parse_target)
    host = parsed.hostname or host_hint
    if not host:
        raise SidecarProbeUnsupported("FTP target is missing a hostname")
    try:
        port = parsed.port or 21
    except ValueError as exc:
        raise SidecarProbeUnsupported("FTP target has an invalid port") from exc
    return SidecarProbeTarget(
        host_type="F",
        host=host,
        port=port,
        scheme=None,
        request_host=None,
        start_path=_normalize_start_path(parsed.path),
    )


def _parse(value: str):
    try:
        return urlparse(value)
    except Exception as exc:
        raise SidecarProbeUnsupported("target URL is invalid") from exc


def _normalize_start_path(value: Any) -> str:
    text = str(value or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not text.startswith("/"):
        text = "/" + text.lstrip("/")
    return text
