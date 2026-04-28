"""
FTP Shodan query helpers (Card 4).

Mirrors commands/discover/shodan_query.py but targets FTP servers.
Raises FtpDiscoveryError on API failure so the CLI boundary can exit(1)
without the success marker being emitted.
"""
from __future__ import annotations

import json
from typing import List, Optional, TYPE_CHECKING

from commands.ftp.models import FtpCandidate, FtpDiscoveryError

if TYPE_CHECKING:
    from shared.ftp_workflow import FtpWorkflow

SHODAN_PAGE_SIZE = 100
SHODAN_RESULT_FIELDS = [
    "ip_str",
    "port",
    "data",
    "location.country_name",
    "location.country_code",
    "org",
    "isp",
    "hostnames",
]


def _coerce_int(value, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _resolve_ftp_query_limits(workflow: "FtpWorkflow") -> dict:
    """Resolve FTP query limits with a hard per-scan credit budget."""
    ftp_cfg = workflow.config.get_ftp_config()
    ftp_limits = ftp_cfg.get("shodan", {}).get("query_limits", {})
    shodan_limits = workflow.config.get_shodan_config().get("query_limits", {})

    ftp_limit = ftp_limits.get("max_results")
    global_limit = shodan_limits.get("max_results")
    max_results = _coerce_int(ftp_limit if ftp_limit is not None else global_limit, 1000)
    budget = _coerce_int(shodan_limits.get("ftp_max_query_credits_per_scan"), 1)
    effective_limit = min(max_results, budget * SHODAN_PAGE_SIZE)
    max_pages = max(1, (effective_limit + SHODAN_PAGE_SIZE - 1) // SHODAN_PAGE_SIZE)

    return {
        "max_results": max_results,
        "budget": budget,
        "effective_limit": effective_limit,
        "max_pages": max_pages,
    }


def _collect_ftp_matches(api, query: str, limits: dict, out) -> List[dict]:
    """Collect FTP matches page-by-page within configured budget."""
    effective_limit = limits["effective_limit"]
    max_pages = limits["max_pages"]

    if effective_limit <= 0:
        return []

    out.info(
        "FTP Shodan budget: "
        f"requested {limits['max_results']} results, "
        f"budget {limits['budget']} credit(s), "
        f"effective limit {effective_limit} ({max_pages} page(s) max)"
    )

    matches: List[dict] = []
    pages_fetched = 0
    page = 1

    while len(matches) < effective_limit and page <= max_pages:
        try:
            response = api.search(
                query,
                page=page,
                minify=False,
                fields=SHODAN_RESULT_FIELDS,
            )
        except Exception as exc:
            err_text = str(exc).lower()
            if ("unable to parse json response" in err_text or "search cursor timed out" in err_text) and matches:
                out.warning(
                    f"Shodan API paging interrupted on page {page} ({exc}); using "
                    f"{len(matches)} results collected so far"
                )
                break
            raise

        pages_fetched = page
        page_matches = response.get("matches", [])
        if not isinstance(page_matches, list) or not page_matches:
            break

        remaining = effective_limit - len(matches)
        matches.extend(page_matches[:remaining])
        if len(page_matches) < SHODAN_PAGE_SIZE:
            break
        page += 1

    out.print_if_verbose(
        f"Shodan query fetched {len(matches)} matches over {pages_fetched} page(s)"
    )
    return matches


def query_ftp_shodan(
    workflow: "FtpWorkflow",
    country: Optional[str] = None,
    custom_filters: Optional[str] = None,
) -> List[FtpCandidate]:
    """
    Query Shodan for FTP servers and return a list of FtpCandidate objects.

    Returns an empty list when Shodan finds no matches (not an error).
    Raises FtpDiscoveryError on API or network failure.
    """
    out = workflow.output

    target_countries = workflow.config.resolve_target_countries(country)
    query = build_ftp_query(workflow, target_countries, custom_filters)

    # Emit before the blocking call so the GUI log pane doesn't appear frozen.
    if target_countries:
        out.info(f"Querying Shodan for FTP servers in: {', '.join(target_countries)}")
    else:
        out.info("Querying Shodan for FTP servers (global scan)")

    limits = _resolve_ftp_query_limits(workflow)

    try:
        import shodan
    except ImportError as exc:
        raise FtpDiscoveryError(f"shodan package not installed: {exc}") from exc

    try:
        api_key = workflow.config.get_shodan_api_key()
        api = shodan.Shodan(api_key)
        page_matches = _collect_ftp_matches(api, query, limits, out)
    except shodan.APIError as exc:
        out.error(f"Shodan API error: {exc}")
        raise FtpDiscoveryError(str(exc)) from exc
    except Exception as exc:
        out.error(f"Shodan query failed: {exc}")
        raise FtpDiscoveryError(str(exc)) from exc

    # Deduplicate by IP (last-wins; Shodan rarely returns duplicates).
    by_ip: dict = {}
    for match in page_matches:
        ip = match.get("ip_str", "")
        if not ip:
            continue
        by_ip[ip] = match

    if not by_ip:
        out.warning(f"No FTP candidates found in Shodan for query: {query}")
        return []

    out.success(f"Found {len(by_ip)} FTP candidates in Shodan database")

    candidates: List[FtpCandidate] = []
    for ip, match in by_ip.items():
        location = match.get("location", {})
        country_name = location.get("country_name") or match.get("country_name") or ""
        country_code = location.get("country_code") or match.get("country_code") or ""
        banner = match.get("data", "") or ""
        # Store a lightweight metadata dict (avoids serialising full Shodan blob).
        meta = {
            "org": match.get("org", ""),
            "isp": match.get("isp", ""),
            "country_name": country_name,
            "country_code": country_code,
            "port": match.get("port", 21),
            "hostnames": match.get("hostnames", []),
        }
        candidates.append(FtpCandidate(
            ip=ip,
            port=match.get("port", 21),
            banner=banner,
            country=country_name,
            country_code=country_code,
            shodan_data=meta,
        ))

    return candidates


def build_ftp_query(
    workflow: "FtpWorkflow",
    countries: List[str],
    custom_filters: Optional[str] = None,
) -> str:
    """
    Assemble a Shodan query string for FTP anonymous-access discovery.
    """
    ftp_cfg = workflow.config.get_ftp_config()
    query_components = ftp_cfg.get("shodan", {}).get("query_components", {})

    base_query = query_components.get("base_query", 'port:21 "230 Login successful"')
    additional_exclusions = query_components.get("additional_exclusions", [])

    parts = [base_query]

    if custom_filters:
        parts.append(custom_filters)

    if countries:
        if len(countries) == 1:
            parts.append(f"country:{countries[0]}")
        else:
            parts.append(f"country:{','.join(countries)}")

    parts.extend(additional_exclusions)

    return " ".join(parts)
