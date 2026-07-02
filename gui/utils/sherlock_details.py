"""Format the Sherlock section for the desktop Server Detail popup (C6).

Display-only: takes the dict already read from
`DatabaseReader.get_sherlock_result_for_host` and renders explanatory text. The
detail surface is allowed to explain stale / no-snapshot / zero-hit states
(unlike the alert-only Server List Risk column, which stays blank). No DB,
network, content, or auth work happens here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from shared.sherlock import Severity, normalize_color_tag
from shared.sherlock.model import COLOR_TAG_NONE

_HEADER = "Sherlock Findings:"

_SEVERITY_BY_TOKEN = {
    "high": Severity.HIGH,
    "med": Severity.MED,
    "low": Severity.LOW,
}


def _severity_label(token: object) -> Optional[Severity]:
    """Map a stored severity token to a Severity, or None when unrecognized."""
    if not isinstance(token, str):
        return None
    return _SEVERITY_BY_TOKEN.get(token.strip().lower())


def _user_tag_label(color_tag: object) -> str:
    """Return a ` [User1]` style suffix for a user-tagged hit, else ''.

    The popup is a plain Text widget (no color), so the user tag is surfaced as a
    text label. 'none'/unknown/missing tags add nothing.
    """
    token = normalize_color_tag(color_tag)
    if token == COLOR_TAG_NONE:
        return ""
    return " [{0}]".format(token.capitalize())


def _hit_line(hit: Dict[str, Any]) -> str:
    sev = _severity_label(hit.get("severity"))
    sev_text = sev.display_name if sev is not None else "?"
    category = str(hit.get("category") or "").strip() or "?"
    label = str(hit.get("label") or "").strip() or "?"
    pattern = str(hit.get("pattern") or "").strip()
    path = str(hit.get("display_path") or "").strip() or "(no path)"
    label_part = "{0} ({1})".format(label, pattern) if pattern else label
    tag_part = _user_tag_label(hit.get("color_tag"))
    return "   • {0} · {1} · {2}{3} — {4}".format(sev_text, category, label_part, tag_part, path)


def format_sherlock_section(
    result: Optional[Dict[str, Any]],
    settings: Any = None,
) -> str:
    """Return the Sherlock detail section text for a host.

    `result` is the dict from get_sherlock_result_for_host (or None when the host
    was never scanned / the Sherlock tables are absent). `settings` is accepted
    for parity with other formatters but is unused: the popup is a plain Text
    widget, so the severity label text carries the signal, not color.
    """
    if not isinstance(result, dict):
        return "{0}\n   No Sherlock scan recorded for this host.".format(_HEADER)

    if result.get("stale"):
        return (
            "{0}\n   Result is stale (host snapshot changed since the last "
            "Sherlock scan).\n   Re-scan to refresh.".format(_HEADER)
        )

    try:
        total = int(result.get("total_hit_count") or 0)
    except (TypeError, ValueError):
        total = 0

    if total <= 0:
        return "{0}\n   No risky names matched.".format(_HEADER)

    severity = _severity_label(result.get("highest_severity"))
    if severity is None:
        # Defensive: a positive count with an unrecognized token. Report the
        # count without mislabeling severity.
        risk_line = "   Risk: {0} hit(s)".format(total)
    else:
        risk_line = "   Risk: {0}".format(severity.display_text(total))

    lines = ["{0}".format(_HEADER), risk_line]

    hits = result.get("hits")
    if isinstance(hits, list) and hits:
        for hit in hits:
            if isinstance(hit, dict):
                lines.append(_hit_line(hit))
        if result.get("truncated"):
            try:
                shown = int(result.get("detail_count") or len([h for h in hits if isinstance(h, dict)]))
            except (TypeError, ValueError):
                shown = len([h for h in hits if isinstance(h, dict)])
            more = total - shown
            if more > 0:
                lines.append("   … +{0} more not shown".format(more))

    return "\n".join(lines)
