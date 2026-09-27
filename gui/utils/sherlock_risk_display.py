"""Shared Sherlock Risk display helpers (C12).

Single source for the alert-only resolve contract, the composite Treeview tint
tag name, the server -> row_key derivation, and bulk risk enrichment, so the
Server List table and the batch summary dialog cannot drift on tint precedence
or the blank/stale rules (C11 lesson). Display-only: a single DB read of
already-persisted `sherlock_results`; never any network/probe/content work.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from shared.sherlock import Severity, normalize_color_tag, validate_color

# Persisted severity tokens -> Severity. Explicit (not severity_from_str, which
# defaults unknowns to MED) so malformed tokens render blank instead of mislabeled.
_SHERLOCK_SEVERITY_BY_TOKEN = {
    "high": Severity.HIGH,
    "med": Severity.MED,
    "low": Severity.LOW,
}


def sherlock_foreground(background: str) -> str:
    """Choose readable text for a resolved Sherlock #RRGGBB background.

    Uses W3C G18 relative luminance and contrast (4.5:1 minimum). Near-black
    and white cover most colors; pure black covers the small midtone gap.
    Invalid inputs raise ValueError via the existing settings color validator.
    Call once per configured tag, not once per row. No theme state is needed.
    https://www.w3.org/WAI/WCAG21/Techniques/general/G18
    """
    background = validate_color(background)

    def luminance(color: str) -> float:
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    background_luminance = luminance(background)

    def contrast(color: str) -> float:
        low, high = sorted((background_luminance, luminance(color)))
        return (high + 0.05) / (low + 0.05)

    foreground = max(("#111111", "#ffffff"), key=contrast)
    return foreground if contrast(foreground) >= 4.5 else "#000000"


def resolve_sherlock_risk(risk: Any) -> Optional[Tuple[Severity, int, str]]:
    """Return (Severity, count, color_tag) for a displayable finding, else None.

    Applies the alert-only blank contract: absent / stale / zero-hit / malformed
    severity all yield None (blank cell). Only a fresh, non-zero, recognized
    severity is displayed. `color_tag` is the normalized user tag token
    (none/user1/user2/user3) used to pick the row tint.
    """
    if not isinstance(risk, dict):
        return None
    if risk.get("stale"):
        return None
    count = risk.get("count") or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    severity = _SHERLOCK_SEVERITY_BY_TOKEN.get(risk.get("severity"))
    if severity is None:
        return None
    color_tag = normalize_color_tag(risk.get("display_color_tag"))
    return severity, count, color_tag


def sherlock_row_tag(severity: Severity, color_tag: str) -> str:
    """Composite Treeview tag name keyed by (severity, normalized color tag).

    User-tagged rows get a distinct tag so the row background can carry the
    user color; untagged rows keep a severity-only tag.
    """
    return "sherlock_{0}_{1}".format(severity.name.lower(), color_tag)


def row_key_for_server(server: Dict[str, Any]) -> Optional[str]:
    """Derive the risk-map row_key ("<host_type>:<protocol_server_id>") for a server.

    Prefers an existing `row_key` (Server List passes one). Otherwise builds it
    only when both `host_type` (S/F/H) and a positive integer `protocol_server_id`
    are present. Returns None when neither is possible, so the row degrades to a
    blank Risk cell rather than matching the wrong host.
    """
    existing = server.get("row_key")
    if existing:
        return existing
    host_type = str(server.get("host_type") or "").strip().upper()
    if host_type not in ("S", "F", "H"):
        return None
    try:
        psid = int(server.get("protocol_server_id"))
    except (TypeError, ValueError):
        return None
    if psid <= 0:
        return None
    return "{0}:{1}".format(host_type, psid)


def attach_sherlock_risk_to_results(db_reader: Any, results: List[Dict[str, Any]]) -> None:
    """Attach per-row `sherlock_risk` summaries to result dicts by `row_key`.

    Reads the persisted risk map once (pure DB read; no network/probe/content).
    Only **successful** probe rows are enriched: a failed/cancelled probe writes
    no new snapshot, so an older Sherlock result for that host may still report
    stale=False and would otherwise surface in this run even though Sherlock did
    not run after the failure. Rows that did not succeed therefore stay blank.
    Degrades silently: a missing method or read failure leaves rows without a
    `sherlock_risk` key, so the Risk column simply stays blank. Rows with no /
    None `row_key` or no matching map entry are left untouched.
    """
    getter = getattr(db_reader, "get_sherlock_risk_summary_map", None)
    if getter is None:
        return
    try:
        summary = getter() or {}
    except Exception:
        return
    for entry in results:
        if str(entry.get("status", "")).lower() != "success":
            continue
        row_key = entry.get("row_key")
        if not row_key:
            continue
        risk = summary.get(row_key)
        if risk is not None:
            entry["sherlock_risk"] = risk
