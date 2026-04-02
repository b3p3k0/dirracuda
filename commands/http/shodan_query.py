"""
HTTP Shodan query helpers (Card 4).

Mirrors commands/ftp/shodan_query.py but targets HTTP open-directory servers.
Raises HttpDiscoveryError on API failure so the CLI boundary can exit(1)
without the success marker being emitted.
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from commands.http.models import HttpCandidate, HttpDiscoveryError

if TYPE_CHECKING:
    from shared.http_workflow import HttpWorkflow

# Fallback base query for legacy configs that do not define
# http.shodan.query_components.base_query.
_BASE_QUERY = 'http.title:"Index of /"'


def query_http_shodan(
    workflow: "HttpWorkflow",
    country: Optional[str] = None,
    custom_filters: Optional[str] = None,
) -> List[HttpCandidate]:
    """
    Query Shodan for HTTP open-directory servers.

    Returns an empty list when Shodan finds no matches (not an error).
    Raises HttpDiscoveryError on API or network failure.
    """
    out = workflow.output

    target_countries = workflow.config.resolve_target_countries(country)
    query = build_http_query(workflow, target_countries, custom_filters)

    if target_countries:
        out.info(f"Querying Shodan for HTTP servers in: {', '.join(target_countries)}")
    else:
        out.info("Querying Shodan for HTTP servers (global scan)")

    # Resolve max_results: HTTP-specific → global → hard default.
    http_cfg = workflow.config.get_http_config()
    http_lim = http_cfg.get("shodan", {}).get("query_limits", {}).get("max_results")
    smb_lim = workflow.config.get_shodan_config().get("query_limits", {}).get("max_results")
    max_results = http_lim if http_lim is not None else (smb_lim if smb_lim is not None else 1000)

    try:
        import shodan
    except ImportError as exc:
        raise HttpDiscoveryError(f"shodan package not installed: {exc}") from exc

    try:
        api_key = workflow.config.get_shodan_api_key()
        api = shodan.Shodan(api_key)
        results = api.search(query, limit=max_results)
    except shodan.APIError as exc:
        out.error(f"Shodan API error: {exc}")
        raise HttpDiscoveryError(str(exc)) from exc
    except Exception as exc:
        out.error(f"Shodan query failed: {exc}")
        raise HttpDiscoveryError(str(exc)) from exc

    # Deduplicate by (ip, port) so non-standard ports on the same IP are retained.
    # Last-wins for exact (ip, port) duplicates (Shodan rarely emits them).
    by_ip_port: dict = {}
    for match in results.get("matches", []):
        ip = match.get("ip_str", "")
        if not ip:
            continue
        port = match.get("port", 80)
        by_ip_port[(ip, port)] = match

    if not by_ip_port:
        out.warning(f"No HTTP candidates found in Shodan for query: {query}")
        return []

    out.success(f"Found {len(by_ip_port)} HTTP candidates in Shodan database")

    candidates: List[HttpCandidate] = []
    for (ip, _port), match in by_ip_port.items():
        location = match.get("location", {})
        country_name = location.get("country_name") or match.get("country_name") or ""
        country_code = location.get("country_code") or match.get("country_code") or ""
        port = match.get("port", 80)
        scheme = "https" if port == 443 else "http"
        banner = match.get("data", "") or ""
        title = (match.get("http", {}) or {}).get("title", "") or ""
        meta = {
            "org": match.get("org", ""),
            "isp": match.get("isp", ""),
            "country_name": country_name,
            "country_code": country_code,
            "port": port,
            "hostnames": match.get("hostnames", []),
        }
        candidates.append(HttpCandidate(
            ip=ip,
            port=port,
            scheme=scheme,
            banner=banner,
            title=title,
            country=country_name,
            country_code=country_code,
            shodan_data=meta,
        ))

    return candidates


def build_http_query(
    workflow: "HttpWorkflow",
    countries: List[str],
    custom_filters: Optional[str] = None,
) -> str:
    """
    Assemble a Shodan query string for HTTP open-directory discovery.
    """
    parts = [_resolve_http_base_query(workflow)]

    if custom_filters:
        parts.append(custom_filters)

    if countries:
        if len(countries) == 1:
            parts.append(f"country:{countries[0]}")
        else:
            parts.append(f"country:{','.join(countries)}")

    return " ".join(parts)


def _resolve_http_base_query(workflow: "HttpWorkflow") -> str:
    """Resolve HTTP base query from config with backward-compatible fallback."""
    http_cfg = workflow.config.get_http_config()
    query_components = http_cfg.get("shodan", {}).get("query_components", {})
    base_query = str(query_components.get("base_query", _BASE_QUERY) or "").strip()
    return base_query or _BASE_QUERY
