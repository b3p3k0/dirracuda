"""
Target extraction and normalization for the redseek module.

Extraction pipeline:
  raw text
    -> _clean_text (html unescape, markdown link strip, backtick strip)
    -> regex extraction (priority-ordered, span-aware)
    -> _cleanup_candidate (strip trailing/leading punctuation)
    -> _normalize (validate + normalize by kind)
    -> _classify (protocol, host, confidence)
    -> make_dedupe_key
    -> RedditTarget

Extraction priority (higher-priority patterns claim char spans first):
  1. Full URLs with explicit scheme (http, https, ftp)  — confidence: high
  2. host:port (IPv4 or hostname)                        — confidence: medium
  3. Raw IPv4 (no port)                                  — confidence: low
  4. Bare domain                                         — confidence: medium

All Reddit data is untrusted. No eval, no execution.
"""

import hashlib
import html
import re
import urllib.parse
from typing import Optional

from experimental.redseek.models import RedditTarget

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_MAX_BYTES = 102_400   # 100 KB — truncate source before parse
_URL_MAX_LEN = 2048           # drop individual URL candidates over this length
_SELFTEXT_EMPTY = frozenset({"", "[deleted]", "[removed]"})

_STRIP_TRAILING = r'.,)]\>"\''
_STRIP_LEADING = '([<'

# Port → protocol inference for host:port targets
_PORT_PROTOCOL = {80: "http", 443: "https", 21: "ftp"}

# Confidence by extraction kind
_CONFIDENCE = {
    "url":         "high",
    "host_port":   "medium",
    "bare_domain": "medium",
    "ipv4":        "low",
}

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level; compiled once)
# ---------------------------------------------------------------------------

# Priority 1: full URLs with explicit scheme (case-insensitive: HTTP:// and http:// both match)
_RE_URL = re.compile(
    r'(?:https?|ftp)://[^\s<>"\')\]\}]+',
    re.IGNORECASE,
)

# Priority 2: host:port — IPv4 or hostname label, TLD up to 63 chars (DNS label max)
_RE_HOST_PORT = re.compile(
    r'\b'
    r'(?:\d{1,3}(?:\.\d{1,3}){3}'
    r'|(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63})'
    r':(\d{1,5})'
    r'\b'
)

# Priority 3: raw IPv4 (no port)
_RE_IPV4 = re.compile(
    r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b'
)

# Priority 4: bare domain
# Negative lookbehind on [a-zA-Z0-9@] suppresses:
#   - email domains (user@example.com → @example.com skipped)
#   - extensions of a longer already-matched token
_RE_BARE_DOMAIN = re.compile(
    r'(?<![a-zA-Z0-9@])(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\b'
)

# Markdown link [text](url) — captures URL in group 2 (http, https, ftp)
_RE_MD_LINK = re.compile(
    r'\[([^\]]*)\]\(((?:https?|ftp)://[^)]+)\)'
)

# Inline backtick `content`
_RE_BACKTICK = re.compile(r'`([^`]+)`')

# Ordered list of (pattern, kind) for span-aware extraction
_PATTERNS = [
    (_RE_URL,         "url"),
    (_RE_HOST_PORT,   "host_port"),
    (_RE_IPV4,        "ipv4"),
    (_RE_BARE_DOMAIN, "bare_domain"),
]

# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """Unescape HTML entities, strip markdown link wrappers and backticks."""
    text = html.unescape(raw)
    text = _RE_MD_LINK.sub(r'\2', text)    # [label](url) → url
    text = _RE_BACKTICK.sub(r'\1', text)   # `content` → content
    return text


def _cleanup_candidate(s: str) -> str:
    """Strip trailing and leading punctuation characters."""
    return s.rstrip(_STRIP_TRAILING).lstrip(_STRIP_LEADING)


# ---------------------------------------------------------------------------
# Normalization (per-kind)
# ---------------------------------------------------------------------------

def _normalize_url(candidate: str) -> Optional[str]:
    if len(candidate) > _URL_MAX_LEN:
        return None
    try:
        p = urllib.parse.urlparse(candidate)
    except Exception:
        return None
    scheme = p.scheme.lower()
    if scheme not in ("http", "https", "ftp"):
        return None
    host = p.hostname
    if not host:
        return None
    host = host.lower()
    try:
        port = p.port
    except ValueError:
        return None
    port_part = f":{port}" if port else ""
    path = p.path if p.path != "/" else ""
    normalized = f"{scheme}://{host}{port_part}{path}"
    if p.query:
        normalized += f"?{p.query}"
    if p.fragment:
        normalized += f"#{p.fragment}"
    return normalized


def _normalize_host_port(candidate: str) -> Optional[str]:
    idx = candidate.rfind(":")
    if idx == -1:
        return None
    host_part = candidate[:idx].lower()
    port_str = candidate[idx + 1:]
    try:
        port = int(port_str)
    except ValueError:
        return None
    if not (1 <= port <= 65535):
        return None
    # If host looks like IPv4, validate octets
    if re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', host_part):
        try:
            if any(not (0 <= int(o) <= 255) for o in host_part.split(".")):
                return None
        except ValueError:
            return None
    return f"{host_part}:{port}"


def _normalize_ipv4(candidate: str) -> Optional[str]:
    parts = candidate.split(".")
    if len(parts) != 4:
        return None
    try:
        if any(not (0 <= int(o) <= 255) for o in parts):
            return None
    except ValueError:
        return None
    return candidate


def _normalize_bare_domain(candidate: str) -> Optional[str]:
    lower = candidate.lower()
    labels = lower.split(".")
    if len(labels) < 2:
        return None
    tld = labels[-1]
    if not tld.isalpha():
        return None
    # Reject labels that are all-digit (guards against IP fragments and version strings)
    if any(label.isdigit() for label in labels):
        return None
    return lower


_NORMALIZERS = {
    "url":         _normalize_url,
    "host_port":   _normalize_host_port,
    "ipv4":        _normalize_ipv4,
    "bare_domain": _normalize_bare_domain,
}


def _normalize(candidate: str, kind: str) -> Optional[str]:
    fn = _NORMALIZERS.get(kind)
    return fn(candidate) if fn else None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(normalized: str, kind: str) -> tuple:
    """Return (protocol, host, confidence)."""
    confidence = _CONFIDENCE[kind]

    if kind == "url":
        p = urllib.parse.urlparse(normalized)
        return p.scheme, (p.hostname or ""), confidence

    if kind == "host_port":
        idx = normalized.rfind(":")
        host = normalized[:idx]
        port = int(normalized[idx + 1:])
        protocol = _PORT_PROTOCOL.get(port, "unknown")
        return protocol, host, confidence

    # bare_domain and ipv4
    return "unknown", normalized, confidence


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------

def make_dedupe_key(post_id: str, target_normalized: str) -> str:
    """sha1(post_id + "|" + target_normalized) — deterministic, stable."""
    return hashlib.sha1(f"{post_id}|{target_normalized}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Span-aware multi-pattern extraction
# ---------------------------------------------------------------------------

def _extract_candidates(text: str) -> list:
    """
    Return list of (raw_match, kind) in document order.

    Higher-priority patterns claim char spans first. Any lower-priority match
    whose span overlaps a claimed span is skipped.
    """
    claimed: list = []  # list of (start, end)

    def _overlaps(start: int, end: int) -> bool:
        return any(s < end and start < e for s, e in claimed)

    results = []
    for pattern, kind in _PATTERNS:
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            if _overlaps(s, e):
                continue
            claimed.append((s, e))
            results.append((m.group(0), kind))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_targets(
    post_id: str,
    title: str,
    selftext: Optional[str],
    parse_body: bool,
    created_at: str,
) -> list:
    """
    Extract and normalize targets from a Reddit post.

    post_id:    Reddit post ID (used in dedupe key)
    title:      post title — always parsed
    selftext:   post body — parsed only if parse_body=True and not empty/deleted
    parse_body: whether to include selftext in extraction
    created_at: UTC datetime string ("YYYY-MM-DD HH:MM:SS") for RedditTarget.created_at

    Returns a list of RedditTarget objects (may be empty).
    Output is deterministic: same input always produces the same results.
    """
    parts = [title]
    if (
        parse_body
        and selftext is not None
        and selftext.strip() not in _SELFTEXT_EMPTY
    ):
        parts.append(selftext)

    source = "\n".join(parts)
    truncated = False
    encoded = source.encode("utf-8")
    if len(encoded) > _SOURCE_MAX_BYTES:
        source = encoded[:_SOURCE_MAX_BYTES].decode("utf-8", errors="ignore")
        truncated = True

    source = _clean_text(source)
    candidates = _extract_candidates(source)

    seen: dict = {}  # dedupe_key -> RedditTarget (keeps first occurrence)

    for raw_candidate, kind in candidates:
        cleaned = _cleanup_candidate(raw_candidate)
        if not cleaned:
            continue
        normalized = _normalize(cleaned, kind)
        if normalized is None:
            continue

        protocol, host, confidence = _classify(normalized, kind)
        dedupe_key = make_dedupe_key(post_id, normalized)

        if dedupe_key in seen:
            continue

        seen[dedupe_key] = RedditTarget(
            id=None,
            post_id=post_id,
            target_raw=raw_candidate,
            target_normalized=normalized,
            host=host,
            protocol=protocol,
            notes="truncated" if truncated else None,
            parse_confidence=confidence,
            created_at=created_at,
            dedupe_key=dedupe_key,
        )

    return list(seen.values())
