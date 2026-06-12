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

    pages_fetched = _count(getattr(result, "pages_fetched", 0))
    pacing_seconds = _count(round(getattr(result, "pacing_delay_seconds", 0.0)))
    if pages_fetched:
        lines.append(
            f"⏱ Fetch Pacing: {pages_fetched} pages, "
            f"{pacing_seconds}s delayed"
        )
    throttled_pages = _count(getattr(result, "throttled_page_count", 0))
    engines = tuple(getattr(result, "throttle_engines", ()) or ())
    if throttled_pages:
        lines.append(
            "⚠ Upstream Engine Warnings: "
            f"{len(engines)} engines across {throttled_pages} pages; "
            "continued with soft backoff"
        )
    hard_retry_count = _count(getattr(result, "hard_retry_count", 0))
    if hard_retry_count:
        retry_seconds = _count(
            round(getattr(result, "hard_retry_delay_seconds", 0.0))
        )
        outcome = (
            "pagination stopped early"
            if bool(getattr(result, "stopped_early", False))
            else "recovered"
        )
        lines.append(
            f"⏳ Upstream Retry: {retry_seconds}s across "
            f"{hard_retry_count} retries; {outcome}"
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


def format_searxng_popup_summary(
    result: Any,
    *,
    query: str = "",
    sync_summary: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build the standalone SearXNG completion-dialog message."""
    lines = [
        f"SearXNG dork search complete. {_count(getattr(result, 'fetched_count', 0))} "
        f"URLs fetched, {_count(getattr(result, 'deduped_count', 0))} retained as "
        "open-index results.",
    ]
    if query:
        lines.extend(("", f"Query: {query}"))
    lines.extend(
        (
            "",
            "Retained results were written to the primary Dirracuda database "
            "during this run.",
        )
    )
    if isinstance(sync_summary, Mapping):
        sync_text = _sync_line(sync_summary).replace(
            "🔄 Primary DB Sync",
            "Primary DB sync",
            1,
        )
        lines.extend(("", f"{sync_text}."))
    if bool(getattr(result, "probe_enabled", False)):
        lines.extend(
            (
                "",
                f"Probe: {_count(getattr(result, 'probe_total', 0))} attempted — "
                f"{_count(getattr(result, 'probe_clean', 0))} clean, "
                f"{_count(getattr(result, 'probe_issue', 0))} flagged, "
                f"{_count(getattr(result, 'probe_unprobed', 0))} unprobed.",
            )
        )
    warning = str(getattr(result, "fetch_warning", "") or "").strip()
    if warning:
        lines.extend(("", f"Upstream warning: {warning}"))
    return "\n".join(lines)


def format_searxng_cancelled_rollup(
    result: Any,
    *,
    query: str = "",
    db_path: Path | str,
    sync_summary: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build the completion block for a cancelled SearXNG run.

    Omits URLs Verified (classification may be partial). Terminal line uses ⚠
    (warning class) to distinguish cancellation from failure; C11C will color
    it yellow.
    """
    fetched = _count(getattr(result, "fetched_count", 0))
    retained = _count(getattr(result, "deduped_count", 0))

    lines = [SUMMARY_TITLE, SUMMARY_DIVIDER]
    if str(query or "").strip():
        lines.append(f"🌍 SearXNG Query: {str(query).strip()}")
    lines.extend(
        [
            f"📊 URLs Fetched: {fetched}",
            f"📁 Open Indexes Retained: {retained}",
        ]
    )

    pages_fetched = _count(getattr(result, "pages_fetched", 0))
    pacing_seconds = _count(round(getattr(result, "pacing_delay_seconds", 0.0)))
    if pages_fetched:
        lines.append(
            f"⏱ Fetch Pacing: {pages_fetched} pages, {pacing_seconds}s delayed"
        )
    throttled_pages = _count(getattr(result, "throttled_page_count", 0))
    engines = tuple(getattr(result, "throttle_engines", ()) or ())
    if throttled_pages:
        lines.append(
            "⚠ Upstream Engine Warnings: "
            f"{len(engines)} engines across {throttled_pages} pages; "
            "continued with soft backoff"
        )
    hard_retry_count = _count(getattr(result, "hard_retry_count", 0))
    if hard_retry_count:
        retry_seconds = _count(round(getattr(result, "hard_retry_delay_seconds", 0.0)))
        lines.append(
            f"⏳ Upstream Retry: {retry_seconds}s across "
            f"{hard_retry_count} retries; pagination stopped early"
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
        lines.append(f"⚠ Scan cancelled: {retained} open indexes retained")
    else:
        lines.append("⚠ Scan cancelled: no open indexes retained")
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


__all__ = [
    "format_reddit_rollup",
    "format_searxng_cancelled_rollup",
    "format_searxng_popup_summary",
    "format_searxng_rollup",
]
