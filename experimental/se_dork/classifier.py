"""
URL classifier for se_dork results.

Reuses commands.http.verifier.try_http_request and validate_index_page to
triage each candidate URL into one of four deterministic verdicts.

Public API:
    classify_url(url, timeout=10.0) -> ClassifyResult
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Optional

from commands.http.verifier import try_http_request, validate_index_page

# ---------------------------------------------------------------------------
# Verdict constants
# ---------------------------------------------------------------------------

VERDICT_OPEN_INDEX = "OPEN_INDEX"
VERDICT_MAYBE      = "MAYBE"
VERDICT_NOISE      = "NOISE"
VERDICT_ERROR      = "ERROR"

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    """Outcome of a single URL classification."""

    verdict: str            # VERDICT_* constant
    reason_code: Optional[str]  # None only for OPEN_INDEX; failure/detail code otherwise
    http_status: Optional[int]  # None on network-level failure (reason != "")


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------

def classify_url(url: str, timeout: float = 10.0) -> ClassifyResult:
    """
    Classify a URL using the existing HTTP verifier path.

    Returns a deterministic ClassifyResult. Never raises.

    Verdict mapping:
      OPEN_INDEX — HTTP 200 + validate_index_page() passes
      MAYBE      — HTTP 200 but no index tag; or redirect (3xx)
      NOISE      — non-http/https scheme; or HTTP 4xx/5xx
      ERROR      — non-str input; parse failure; network failure
    """
    # Guard: non-string input cannot be parsed
    if not isinstance(url, str):
        return ClassifyResult(verdict=VERDICT_ERROR, reason_code="parse_error", http_status=None)

    # Parse URL
    try:
        parsed = urllib.parse.urlparse(url)
        scheme   = (parsed.scheme or "").lower()
        hostname = parsed.hostname or ""
        port     = parsed.port or _DEFAULT_PORTS.get(scheme, 80)
        path     = parsed.path or "/"
        if not path:
            path = "/"
    except Exception:
        return ClassifyResult(verdict=VERDICT_ERROR, reason_code="parse_error", http_status=None)

    # Reject unsupported schemes before any network I/O
    if scheme not in ("http", "https"):
        return ClassifyResult(verdict=VERDICT_NOISE, reason_code="unsupported_scheme", http_status=None)

    # Reject empty hostname (e.g. http:///path)
    if not hostname:
        return ClassifyResult(verdict=VERDICT_ERROR, reason_code="no_host", http_status=None)

    # Run HTTP request via existing verifier
    status_code, body, _tls_verified, reason = try_http_request(
        ip=hostname,
        port=port,
        scheme=scheme,
        allow_insecure_tls=True,
        timeout=timeout,
        path=path,
        request_host=hostname,
    )

    # Network-level failure
    if reason:
        return ClassifyResult(verdict=VERDICT_ERROR, reason_code=reason, http_status=None)

    # Open directory index
    if validate_index_page(body, status_code):
        return ClassifyResult(verdict=VERDICT_OPEN_INDEX, reason_code=None, http_status=status_code)

    # Responsive but not an index
    if status_code == 200:
        return ClassifyResult(verdict=VERDICT_MAYBE, reason_code="no_index_tag", http_status=status_code)

    # Client/server errors
    if status_code >= 400:
        return ClassifyResult(
            verdict=VERDICT_NOISE,
            reason_code=f"http_{status_code}",
            http_status=status_code,
        )

    # Redirects and other 1xx/2xx/3xx without matching index
    return ClassifyResult(
        verdict=VERDICT_MAYBE,
        reason_code=f"http_{status_code}",
        http_status=status_code,
    )
