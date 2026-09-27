"""Reachability and explicit JSON preflight for SearXNG and DeGoog."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from experimental.se_dork.backends import instance_base, is_degoog_endpoint, search_url
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
    Detect the aggregator, then validate its JSON search response.

    instance_url: base URL or search endpoint of a SearXNG or DeGoog instance
    timeout:      seconds per HTTP request

    Returns a PreflightResult with ok=True on full success, or ok=False with a
    reason_code and human-readable message describing the failure.
    """
    reachability = run_reachability_check(instance_url, timeout)
    if not reachability.ok:
        return reachability

    # Explicit Test issues one upstream search after detection.
    result = _check_search(reachability.search_endpoint or instance_url, timeout)
    result.search_endpoint = reachability.search_endpoint
    return result


def run_reachability_check(instance_url: str, timeout: int = 10) -> PreflightResult:
    """Check instance reachability without issuing an upstream search."""
    try:
        base = instance_base(instance_url)
    except ValueError as exc:
        return PreflightResult(False, INSTANCE_UNREACHABLE, str(exc))
    degoog = is_degoog_endpoint(instance_url)
    if not degoog:
        try:
            with urllib.request.urlopen(f"{base}/config", timeout=timeout) as resp:
                if resp.status != 200:
                    return PreflightResult(False, INSTANCE_UNREACHABLE,
                                           f"Instance /config returned HTTP {resp.status}.")
            return PreflightResult(True, None, "Instance reachable.", base + "/search")
        except urllib.error.HTTPError as exc:
            # Missing SearXNG route: try DeGoog's metadata, not an upstream search.
            if exc.code != 404:
                return PreflightResult(False, INSTANCE_UNREACHABLE,
                                       f"Instance /config returned HTTP {exc.code}.")
        except (urllib.error.URLError, OSError) as exc:
            return PreflightResult(False, INSTANCE_UNREACHABLE,
                                   f"Cannot reach instance: {exc}.")
    try:
        with urllib.request.urlopen(f"{base}/api/search-tabs", timeout=timeout) as resp:
            payload = json.loads(resp.read())
        if not isinstance(payload, dict) or not isinstance(payload.get("tabs"), list):
            raise ValueError("missing valid 'tabs' list")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return PreflightResult(False, INSTANCE_UNREACHABLE,
                               f"Cannot identify DeGoog at /api/search-tabs: {exc}.")
    return PreflightResult(True, None, "DeGoog instance reachable.", base + "/api/search")


def _check_search(base: str, timeout: int) -> PreflightResult:
    """
    Query the resolved search endpoint.
    Maps HTTP/parse failures to explicit reason codes.
    """
    degoog = is_degoog_endpoint(base)
    url = search_url(base, "hello", 1)

    # Fetch
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and not degoog:
            return PreflightResult(
                ok=False,
                reason_code=INSTANCE_FORMAT_FORBIDDEN,
                message=f"Format=json not allowed (HTTP 403). {_FORMAT_FORBIDDEN_HINT}",
            )
        return PreflightResult(
            ok=False,
            reason_code=SEARCH_HTTP_ERROR,
            message=(f"Search endpoint returned HTTP {exc.code}."
                     + (" Check DeGoog access controls; API-key authentication is not supported."
                        if degoog and exc.code in (401, 403) else "")),
        )
    except (urllib.error.URLError, OSError) as exc:
        return PreflightResult(
            ok=False,
            reason_code=INSTANCE_UNREACHABLE,
            message=f"Cannot reach instance: {exc}.",
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
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return PreflightResult(
            ok=False,
            reason_code=SEARCH_PARSE_ERROR,
            message="Search response is missing a valid 'results' list.",
        )

    return PreflightResult(ok=True, reason_code=None, message="DeGoog instance OK." if degoog else "Instance OK.")
