"""
Semantic coloring for the Live Scan Output panel (C11C).

Applies ANSI colors at display time inside append_log_line; log_history stores
the original input so C11C adds no escapes to Copy All and Shodan stays unchanged.
"""

from __future__ import annotations

import re
from typing import List, Tuple

try:
    from gui.components.dashboard_scan_rollup import SUMMARY_TITLE
except Exception:
    SUMMARY_TITLE = "Dirracuda Scan Summary"

LEVEL_CODES: dict[str, str] = {
    "blue": "94",
    "green": "92",
    "yellow": "93",
    "red": "91",
}

_STATUS_PREFIX = re.compile(r"^\[status \d{2}:\d{2}:\d{2}\] ")

# Ordered (pattern, level) — red checked before yellow before green before blue.
# Only exact SearXNG/Reddit/provider-queue messages are matched; everything else
# (including Shodan _log_status_event calls) returns "" (normal).
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Red — terminal failures
    (re.compile(r"^Reachability (?:error|failed):"), "red"),
    (re.compile(r"^(?:Database|Run setup) (?:error|failed):"), "red"),
    (re.compile(r"^(?:Processing|Fetch) error:"), "red"),
    (re.compile(r"^Cancellation finalization failed:"), "red"),
    (re.compile(r"^Provider queue failed:"), "red"),
    (re.compile(r"^(?:SearXNG search|Reddit (?:ingest|Grab)) failed:"), "red"),
    # Yellow — warnings, retries, cancellation, nonterminal errors
    (re.compile(r"^Upstream retry \d+/\d+:"), "yellow"),
    (re.compile(r"^(?:Upstream throttling persisted|Temporary upstream engine failures)"), "yellow"),
    (re.compile(r"^Page \d+: \d+ upstream engine warning"), "yellow"),
    (re.compile(r"^Page \d+: probe results could not be saved:"), "yellow"),
    (re.compile(r"^SearXNG pagination stopped"), "yellow"),
    (re.compile(r"^Probe indicator setup warning:"), "yellow"),
    (re.compile(r"^Cancelled\.$"), "yellow"),
    (re.compile(r"^SearXNG search cancelled\.$"), "yellow"),
    (re.compile(r"^Unified provider queue cancelled\.$"), "yellow"),
    # Yellow — provider queue finished with at least one failure (count >= 1)
    (re.compile(r"^Provider queue finished:.*\([1-9]\d* failed\)"), "yellow"),
    # Green — successful checkpoints and completions
    (re.compile(r"^Instance reachable$"), "green"),
    (re.compile(r"^Page \d+: classified \d+; retained"), "green"),
    (re.compile(r"^Page \d+: probed \d+"), "green"),
    (re.compile(r"^Run complete:"), "green"),
    (re.compile(r"^Upstream availability recovered"), "green"),
    (re.compile(r"^Provider queue completed:"), "green"),
    (re.compile(r"^Provider queue finished:"), "green"),  # fallback (no failures)
    # Blue — starts, requests, headings
    (re.compile(r"^Reachability: checking "), "blue"),
    (re.compile(r"^Run registered \(#\d+\)\. Starting fetch"), "blue"),
    (re.compile(r"^Querying SearXNG page \d+"), "blue"),
    (re.compile(r"^SearXNG search started:"), "blue"),
    (re.compile(r"^Reddit (?:feed|search) ingest started"), "blue"),
    (re.compile(r"^Fetching Reddit posts"), "blue"),
    (re.compile(r"^Reddit Grab started"), "blue"),
    (re.compile(r"^Provider queue: "), "blue"),
    (re.compile(r"^Provider queue starting:"), "blue"),
]

_SYNC_FAILED = re.compile(r"(\d+) failed")
_SYNC_CANCELLED = re.compile(r"(\d+) cancelled")


def wrap(text: str, level: str) -> str:
    """Wrap text in ANSI SGR codes for the given semantic level."""
    if not text:
        return text
    code = LEVEL_CODES.get(level, "")
    if not code:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def classify_status_message(msg: str) -> str:
    """Return semantic level for a known SearXNG/Reddit/queue message, or ''."""
    for pattern, level in _PATTERNS:
        if pattern.match(msg):
            return level
    return ""


def _classify_sync_line(line: str) -> str:
    """Classify a 🔄 sync rollup line. Green requires explicit failed==0."""
    failed_m = _SYNC_FAILED.search(line)
    if failed_m is None:
        return ""  # unknown format — render plain, do not assume success
    if int(failed_m.group(1)) > 0:
        return "red"
    cancelled_m = _SYNC_CANCELLED.search(line)
    if cancelled_m is not None and int(cancelled_m.group(1)) > 0:
        return "yellow"
    return "green"  # confirmed failed==0, cancelled==0 or absent


def classify_rollup_line(line: str) -> str:
    """Return semantic level for a single rollup line."""
    stripped = line.strip()
    if not stripped:
        return ""
    if stripped == SUMMARY_TITLE:
        return "blue"
    if stripped.startswith("="):
        return "blue"
    if stripped.startswith("🌍"):
        return "blue"
    if stripped.startswith("⚠"):
        return "yellow"
    if stripped.startswith("⏳"):
        return "yellow"
    if stripped.startswith("🔄"):
        return _classify_sync_line(stripped)
    if stripped.startswith("✓"):
        return "green"
    return ""


def _color_status_line(line: str) -> str:
    """Apply semantic color to the message part of a [status HH:MM:SS] line."""
    m = _STATUS_PREFIX.match(line)
    if not m:
        return line
    prefix = m.group(0)
    message = line[m.end():]
    level = classify_status_message(message)
    return prefix + wrap(message, level) if level else line


def _color_rollup_block(rollup: str) -> str:
    """Apply per-line semantic color to a multiline rollup string."""
    parts = rollup.split("\n")
    colored = []
    for part in parts:
        level = classify_rollup_line(part)
        colored.append(wrap(part, level) if level else part)
    return "\n".join(colored)


def colorize_for_display(line: str) -> str:
    """Return a display-ready version of line with semantic ANSI color applied.

    Routes [status ...] lines through the status classifier and multiline
    rollup blocks through per-line rollup coloring. All other lines (Shodan
    backend output, plain GUI lines) are returned unchanged.
    """
    if _STATUS_PREFIX.match(line):
        return _color_status_line(line)
    # Guard on "\n": a standalone SUMMARY_TITLE line from Shodan (CLI colors
    # disabled) must not enter the rollup path.
    if "\n" in line and line.startswith(SUMMARY_TITLE):
        return _color_rollup_block(line)
    return line
