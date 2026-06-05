"""Live-output completion rollups for in-process dashboard providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional


SUMMARY_TITLE = "Dirracuda Scan Summary"
SUMMARY_DIVIDER = "=" * len(SUMMARY_TITLE)


def _count(value: Any) -> int:
    """Return a non-negative display count."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _sync_line(summary: Mapping[str, Any]) -> str:
    processed = _count(summary.get("processed"))
    inserted = _count(summary.get("inserted"))
    updated = _count(summary.get("updated"))
    skipped = _count(summary.get("skipped"))
    failed = _count(summary.get("failed"))
    cancelled = _count(summary.get("cancelled"))

    details = [
        f"{inserted} inserted",
        f"{updated} updated",
        f"{skipped} skipped",
        f"{failed} failed",
    ]
    if cancelled:
        details.append(f"{cancelled} cancelled")
    return f"🔄 Primary DB Sync: {processed} processed ({', '.join(details)})"


def format_searxng_rollup(
    result: Any,
    *,
    query: str = "",
    db_path: Path | str,
    sync_summary: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build the Shodan-style completion block for a SearXNG run."""
    fetched = _count(getattr(result, "fetched_count", 0))
    verified = _count(getattr(result, "verified_count", 0))
    retained = _count(getattr(result, "deduped_count", 0))

    lines = [SUMMARY_TITLE, SUMMARY_DIVIDER]
    if str(query or "").strip():
        lines.append(f"🌍 SearXNG Query: {str(query).strip()}")
    lines.extend(
        [
            f"📊 URLs Fetched: {fetched}",
            f"🔓 URLs Verified: {verified}",
            f"📁 Open Indexes Retained: {retained}",
        ]
    )

    if bool(getattr(result, "probe_enabled", False)):
        lines.append(
            "🧪 Probe Results: "
            f"{_count(getattr(result, 'probe_total', 0))} attempted "
            f"({_count(getattr(result, 'probe_clean', 0))} clean, "
            f"{_count(getattr(result, 'probe_issue', 0))} flagged, "
            f"{_count(getattr(result, 'probe_unprobed', 0))} unprobed)"
        )
    if isinstance(sync_summary, Mapping):
        lines.append(_sync_line(sync_summary))

    lines.append(f"💾 Results saved to: {db_path}")
    if retained:
        lines.append(f"✓ Scan completed: Found {retained} open indexes")
    else:
        lines.append("ℹ Scan completed: No open indexes found")
    return "\n".join(lines)


def format_reddit_rollup(
    result: Any,
    *,
    mode: str = "feed",
    query: str = "",
    db_path: Path | str,
    sync_summary: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build the Shodan-style completion block for a Reddit RSS run."""
    normalized_mode = str(mode or "feed").strip().lower()
    sort = str(getattr(result, "sort", "") or "new").strip()
    posts_stored = _count(getattr(result, "posts_stored", 0))
    posts_skipped = _count(getattr(result, "posts_skipped", 0))
    targets_stored = _count(getattr(result, "targets_stored", 0))
    targets_deduped = _count(getattr(result, "targets_deduped", 0))
    targets_discovered = targets_stored + targets_deduped

    lines = [SUMMARY_TITLE, SUMMARY_DIVIDER]
    if normalized_mode == "search":
        lines.append(
            f'🌍 Reddit Query: "{str(query or "").strip()}" '
            f"in r/opendirectories RSS ({sort})"
        )
    else:
        lines.append(
            f"🌍 Reddit Source: r/opendirectories RSS ({normalized_mode}, {sort})"
        )

    posts_line = f"📊 Posts Stored: {posts_stored}"
    if posts_skipped:
        posts_line += f" ({posts_skipped} skipped)"
    lines.extend(
        [
            posts_line,
            f"🔓 Targets Discovered: {targets_discovered}",
            f"📁 New Targets Stored: {targets_stored}",
        ]
    )

    if bool(getattr(result, "probe_enabled", False)):
        lines.append(
            "🧪 Probe Results: "
            f"{_count(getattr(result, 'probe_total', 0))} attempted "
            f"({_count(getattr(result, 'probe_clean', 0))} clean, "
            f"{_count(getattr(result, 'probe_issue', 0))} flagged, "
            f"{_count(getattr(result, 'probe_unprobed', 0))} unprobed, "
            f"{_count(getattr(result, 'probe_skipped', 0))} skipped)"
        )
    if isinstance(sync_summary, Mapping):
        lines.append(_sync_line(sync_summary))

    lines.append(f"💾 Results saved to: {db_path}")
    if targets_discovered:
        lines.append(
            f"✓ Scan completed: Found {targets_discovered} Reddit targets "
            f"({targets_stored} new)"
        )
    else:
        lines.append("ℹ Scan completed: No usable Reddit targets found")
    return "\n".join(lines)


__all__ = ["format_reddit_rollup", "format_searxng_rollup"]
