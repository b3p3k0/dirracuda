"""
Pure grouped-value model for the Sherlock Pattern Manager (C22).

The future two-pane Pattern Manager (C23-C25) displays grouped value rows like::

    Tax docs | MED | User2 | *w2*, *w4*, *tax*

while the stored schema stays exactly one SherlockPattern per pattern string.
This module supplies the pure (Tk-free, I/O-free) helpers that bridge the two:
group individual patterns for display, parse comma-separated Add/Edit input into
tokens, build the individual rows back from those tokens, and flatten groups for
saving. Grouping and comma input are display/parse conveniences only -- nothing
here changes the settings schema or wire format; patterns persist individually
exactly as before (see shared.sherlock.serialize).
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

from .model import Severity, SherlockPattern, normalize_color_tag


# The fields that define one visible value row. Two patterns share a group only
# when all of these match; pattern string and key are the per-row varying fields.
_GroupKey = Tuple[str, str, Severity, bool, bool, str]


@dataclasses.dataclass(frozen=True)
class PatternValueGroup:
    """One visible value row: the shared fields plus the rows behind it.

    ``members`` holds the original SherlockPattern rows in first-occurrence order,
    so the grouping is a faithful, reversible view -- no schema/wire change.
    """

    category: str
    label: str
    severity: Severity
    enabled: bool
    builtin: bool
    color_tag: str
    members: Tuple[SherlockPattern, ...]

    @property
    def patterns(self) -> Tuple[str, ...]:
        """Member pattern strings, in order -- the value row's display tokens."""
        return tuple(member.pattern for member in self.members)

    @property
    def keys(self) -> Tuple[str, ...]:
        """Member keys, in order."""
        return tuple(member.key for member in self.members)


def _group_key(pattern: SherlockPattern) -> _GroupKey:
    return (
        pattern.category,
        pattern.label,
        pattern.severity,
        pattern.enabled,
        pattern.builtin,
        normalize_color_tag(pattern.color_tag),
    )


def group_patterns(patterns: Iterable[SherlockPattern]) -> List[PatternValueGroup]:
    """Group patterns into visible value rows, preserving first occurrence.

    Patterns merge into one group only when category, label, severity, enabled
    state, builtin/custom type, and color tag all match. ``builtin`` is part of
    the key, so built-in and custom rows never share a group even when every
    other field is identical. Group order and within-group member order both
    follow first occurrence. Members are kept verbatim (no de-duplication) so the
    grouping stays a faithful, reversible view of the underlying rows.
    """
    order: List[_GroupKey] = []
    buckets: Dict[_GroupKey, List[SherlockPattern]] = {}
    for pattern in patterns:
        key = _group_key(pattern)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = bucket = []
            order.append(key)
        bucket.append(pattern)
    groups: List[PatternValueGroup] = []
    for key in order:
        category, label, severity, enabled, builtin, color_tag = key
        groups.append(
            PatternValueGroup(
                category=category,
                label=label,
                severity=severity,
                enabled=enabled,
                builtin=builtin,
                color_tag=color_tag,
                members=tuple(buckets[key]),
            )
        )
    return groups


def split_pattern_input(text: str) -> List[str]:
    """Split a comma-separated Add/Edit pattern field into individual tokens.

    Splits on literal commas, strips surrounding whitespace from each token,
    drops empty tokens, and removes duplicate strings while preserving first
    occurrence.

    Limitation (this pass): literal commas *inside* a pattern string are not
    supported -- a pattern containing a comma is split into separate tokens, so
    ``"*a,b*"`` becomes ``["*a", "b*"]``. There is no escape mechanism yet.
    """
    seen = set()
    tokens: List[str] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def build_group_patterns(
    *,
    category: str,
    label: str,
    severity: Severity,
    enabled: bool,
    builtin: bool,
    color_tag: str,
    patterns: Sequence[str],
    key_for: Callable[[str], str],
) -> List[SherlockPattern]:
    """Build individual SherlockPattern rows for one grouped value row.

    ``patterns`` are cleaned pattern tokens (typically from split_pattern_input);
    each becomes one row sharing the group's fields, in order. ``key_for(pattern)``
    supplies each row's key, so key *generation* stays a runtime concern -- this
    function mints no keys and performs no I/O. ``color_tag`` is normalized via
    model.normalize_color_tag. Produces ordinary rows: no schema/wire change.
    """
    return [
        SherlockPattern(
            key=key_for(pattern),
            category=category,
            label=label,
            pattern=pattern,
            severity=severity,
            enabled=enabled,
            builtin=builtin,
            color_tag=normalize_color_tag(color_tag),
        )
        for pattern in patterns
    ]


def reuse_keys(
    existing: Iterable[SherlockPattern], mint: Callable[[], str]
) -> Callable[[str], str]:
    """Return a ``key_for`` that preserves keys across a group edit.

    The returned callable reuses an existing member's key when the pattern string
    matches (first occurrence wins) and calls ``mint()`` for any new pattern
    string. This keeps key *identity* stable across edits -- so serialize's custom
    keys / builtin_disabled keys don't churn on a no-op or partial edit -- while
    leaving key *generation* (uuid/counter, a runtime concern) to the injected
    ``mint``. ``mint`` must return keys unique within the catalog; the caller's
    existing generator already guarantees this. Pattern string is the row identity
    (the only per-row varying field within a group); since split_pattern_input
    de-duplicates tokens, no key is handed out twice.
    """
    by_pattern: Dict[str, str] = {}
    for pattern in existing:
        by_pattern.setdefault(pattern.pattern, pattern.key)

    def key_for(pattern: str) -> str:
        existing_key = by_pattern.get(pattern)
        return existing_key if existing_key is not None else mint()

    return key_for


def expand_groups(groups: Iterable[PatternValueGroup]) -> List[SherlockPattern]:
    """Flatten value-row groups back into individual rows, in group order.

    The reversible inverse of group_patterns for the save-without-edit path:
    ``expand_groups(group_patterns(rows)) == rows`` whenever same-group rows are
    contiguous in the input. Members are returned as-is, so no key factory is
    needed and there is no schema/wire change.
    """
    return [member for group in groups for member in group.members]
