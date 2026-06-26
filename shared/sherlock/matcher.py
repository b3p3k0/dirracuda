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

from .model import Severity, SherlockPattern, SherlockSettings
from .path_entry import SherlockPathEntry


@dataclasses.dataclass(frozen=True)
class SherlockHit:
    """One matched (entry, pattern) pair."""

    severity: Severity
    category: str
    label: str
    pattern: str
    display_path: str


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
                    )
                )

    highest = max((hit.severity for hit in hits), default=None)
    return MatchResult(hits=hits, highest_severity=highest, hit_count=len(hits))
