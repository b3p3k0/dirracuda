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

    # Resolve max_results: FTP-specific → global → hard default.
    ftp_cfg = workflow.config.get_ftp_config()
    ftp_lim = ftp_cfg.get("shodan", {}).get("query_limits", {}).get("max_results")
    smb_lim = workflow.config.get_shodan_config().get("query_limits", {}).get("max_results")
    max_results = ftp_lim if ftp_lim is not None else (smb_lim if smb_lim is not None else 1000)

    try:
        import shodan
    except ImportError as exc:
        raise FtpDiscoveryError(f"shodan package not installed: {exc}") from exc

    try:
        api_key = workflow.config.get_shodan_api_key()
        api = shodan.Shodan(api_key)
        results = api.search(query, limit=max_results)
    except shodan.APIError as exc:
        out.error(f"Shodan API error: {exc}")
        raise FtpDiscoveryError(str(exc)) from exc
    except Exception as exc:
        out.error(f"Shodan query failed: {exc}")
        raise FtpDiscoveryError(str(exc)) from exc

    # Deduplicate by IP (last-wins; Shodan rarely returns duplicates).
    by_ip: dict = {}
    for match in results.get("matches", []):
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
