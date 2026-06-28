"""
Pure Sherlock matching engine.

Matches in-memory SherlockPathEntry objects against enabled patterns. No DB,
filesystem, network, or protocol access (R1/R11) — it only inspects the strings
on the entries handed to it.
"""

from __future__ import annotations

import dataclasses
import fnmatch
from typing import Iterable, List, Optional

from .model import (
    COLOR_TAG_NONE,
    USER_COLOR_KEYS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    normalize_color_tag,
)
from .path_entry import SherlockPathEntry


@dataclasses.dataclass(frozen=True)
class SherlockHit:
    """One matched (entry, pattern) pair."""

    severity: Severity
    category: str
    label: str
    pattern: str
    display_path: str
    color_tag: str = COLOR_TAG_NONE


@dataclasses.dataclass(frozen=True)
class MatchResult:
    """Aggregate result of matching a set of entries."""

    hits: List[SherlockHit]
    highest_severity: Optional[Severity]
    hit_count: int


def _escape_brackets(pattern: str) -> str:
    """Neutralize fnmatch bracket sets so only `*`/`?` are wildcards (V1 scope).

    Single-pass scan: chained str.replace would reprocess inserted brackets and
    corrupt the pattern. glob.escape is unusable because it also escapes `*`/`?`.
    """
    out: List[str] = []
    for char in pattern:
        if char == "[":
            out.append("[[]")
        elif char == "]":
            out.append("[]]")
        else:
            out.append(char)
    return "".join(out)


def _candidates(entry: SherlockPathEntry) -> List[str]:
    """Build the match-candidate set: full path, each segment, container (MD-1/2)."""
    candidates: List[str] = [entry.display_path]
    candidates.extend(entry.segments)
    if entry.container and entry.container not in entry.segments:
        candidates.append(entry.container)
    return candidates


def _matches_any(pattern: str, candidates: Iterable[str], ignore_case: bool) -> bool:
    """Return True if pattern matches any candidate under the case mode."""
    cmp_pattern = pattern.lower() if ignore_case else pattern
    if "*" in pattern or "?" in pattern:
        escaped = _escape_brackets(cmp_pattern)
        return any(fnmatch.fnmatchcase(candidate, escaped) for candidate in candidates)
    return any(cmp_pattern in candidate for candidate in candidates)


def match_entries(
    entries: Iterable[SherlockPathEntry],
    settings: SherlockSettings,
) -> MatchResult:
    """Match entries against enabled patterns and aggregate hits.

    Hit count follows SPEC: a pattern matching any candidate of an entry counts
    as one hit for that (entry, pattern) pair. Highest severity wins for display.
    """
    enabled: List[SherlockPattern] = [
        p for p in settings.patterns if p.enabled and p.pattern
    ]
    ignore_case = settings.ignore_case

    hits: List[SherlockHit] = []
    for entry in entries:
        raw_candidates = _candidates(entry)
        cmp_candidates = (
            [c.lower() for c in raw_candidates] if ignore_case else raw_candidates
        )
        for pattern in enabled:
            if _matches_any(pattern.pattern, cmp_candidates, ignore_case):
                hits.append(
                    SherlockHit(
                        severity=pattern.severity,
                        category=pattern.category,
                        label=pattern.label,
                        pattern=pattern.pattern,
                        display_path=entry.display_path,
                        color_tag=normalize_color_tag(pattern.color_tag),
                    )
                )

    highest = max((hit.severity for hit in hits), default=None)
    return MatchResult(hits=hits, highest_severity=highest, hit_count=len(hits))


def select_display_color_tag(hits: Iterable[SherlockHit]) -> Optional[str]:
    """Pick the result-level display tag from matched hits (SPEC C9 / V2).

    The token of the highest-severity user-tagged hit (color_tag in
    USER_COLOR_KEYS); ties preserve matcher hit order (first hit wins). Returns
    None when no hit carries a user tag. The token is independent of whether that
    user color is currently configured — display surfaces resolve the color later.
    """
    best: Optional[tuple] = None  # (severity, tag)
    for hit in hits:
        tag = normalize_color_tag(hit.color_tag)
        if tag in USER_COLOR_KEYS and (best is None or hit.severity > best[0]):
            best = (hit.severity, tag)
    return best[1] if best else None
