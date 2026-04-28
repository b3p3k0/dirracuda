"""
SearXNG preflight client for the se_dork module.

Checks whether a SearXNG instance is reachable and has JSON search enabled.
Uses urllib.request only (stdlib, no external deps).

Preflight sequence:
  1. GET /config       — reachability probe
  2. GET /search?q=hello&format=json — JSON format capability check

Failure reason codes:
  instance_unreachable      — cannot reach the instance at all
  instance_format_forbidden — 403 on format=json (json not enabled)
  instance_non_json         — search response body is not valid JSON
  search_http_error         — non-200, non-403 HTTP status on search
  search_parse_error        — JSON parsed but missing/invalid 'results' key
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from experimental.se_dork.models import (
    INSTANCE_FORMAT_FORBIDDEN,
    INSTANCE_NON_JSON,
    INSTANCE_UNREACHABLE,
    SEARCH_HTTP_ERROR,
    SEARCH_PARSE_ERROR,
    PreflightResult,
)

_FORMAT_FORBIDDEN_HINT = (
    "Enable JSON in SearXNG settings.yml: "
    "search.formats: [html, json, csv, rss]"
)


def run_preflight(instance_url: str, timeout: int = 10) -> PreflightResult:
    """
    Run a two-step preflight check against a SearXNG instance.

    instance_url: base URL of the SearXNG instance (trailing slash stripped)
    timeout:      seconds per HTTP request

    Returns a PreflightResult with ok=True on full success, or ok=False with a
    reason_code and human-readable message describing the failure.
    """
    base = instance_url.rstrip("/")

    # Step 1: reachability via /config
    config_result = _check_reachable(base, timeout)
    if config_result is not None:
        return config_result

    # Step 2: JSON format capability via /search?q=hello&format=json
    return _check_search(base, timeout)


def _check_reachable(base: str, timeout: int) -> Optional[PreflightResult]:
    """
    GET {base}/config.  Returns a failure PreflightResult if unreachable,
    None if the instance responded with HTTP 200.
    """
    url = f"{base}/config"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return PreflightResult(
                    ok=False,
                    reason_code=INSTANCE_UNREACHABLE,
                    message=f"Instance /config returned HTTP {resp.status}.",
                )
    except urllib.error.HTTPError as exc:
        return PreflightResult(
            ok=False,
            reason_code=INSTANCE_UNREACHABLE,
            message=f"Instance /config returned HTTP {exc.code}.",
        )
    except urllib.error.URLError as exc:
        return PreflightResult(
            ok=False,
            reason_code=INSTANCE_UNREACHABLE,
            message=f"Cannot reach instance: {exc.reason}.",
        )
    return None


def _check_search(base: str, timeout: int) -> PreflightResult:
    """
    GET {base}/search?q=hello&format=json.
    Maps HTTP/parse failures to explicit reason codes.
    """
    url = f"{base}/search?q=hello&format=json"

    # Fetch
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return PreflightResult(
                ok=False,
                reason_code=INSTANCE_FORMAT_FORBIDDEN,
                message=f"Format=json not allowed (HTTP 403). {_FORMAT_FORBIDDEN_HINT}",
            )
        return PreflightResult(
            ok=False,
            reason_code=SEARCH_HTTP_ERROR,
            message=f"Search endpoint returned HTTP {exc.code}.",
        )
    except urllib.error.URLError as exc:
        return PreflightResult(
            ok=False,
            reason_code=INSTANCE_UNREACHABLE,
            message=f"Cannot reach instance: {exc.reason}.",
        )

    # Parse JSON
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return PreflightResult(
            ok=False,
            reason_code=INSTANCE_NON_JSON,
            message="Search response is not valid JSON.",
        )

    # Validate shape: results key must exist and be a list
    results = payload.get("results")
    if not isinstance(results, list):
        return PreflightResult(
            ok=False,
            reason_code=SEARCH_PARSE_ERROR,
            message="Search response is missing a valid 'results' list.",
        )

    return PreflightResult(ok=True, reason_code=None, message="Instance OK.")
