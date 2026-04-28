import shodan
from typing import Set, Optional, Tuple, List, Dict, Any

from commands.discover import host_filter


SHODAN_PAGE_SIZE = 100
SHODAN_RESULT_FIELDS = [
    "ip_str",
    "location.country_name",
    "location.country_code",
    "org",
    "isp",
]


def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
    """Coerce integer-like values to bounded ints with a safe fallback."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < minimum:
        return default
    return result


def _resolve_query_limits(shodan_config: Dict[str, Any]) -> Dict[str, int]:
    """Resolve SMB Shodan query limits including credit budget controls."""
    query_limits = shodan_config.get("query_limits", {}) if isinstance(shodan_config, dict) else {}
    max_results = _coerce_int(query_limits.get("max_results"), 1000)
    max_query_credits = _coerce_int(
        query_limits.get("smb_max_query_credits_per_scan", query_limits.get("max_query_credits_per_scan")),
        1,
    )
    min_usable_hosts_target = _coerce_int(query_limits.get("min_usable_hosts_target"), 50)

    effective_limit = min(max_results, max_query_credits * SHODAN_PAGE_SIZE)
    max_pages = max(1, (effective_limit + SHODAN_PAGE_SIZE - 1) // SHODAN_PAGE_SIZE)

    return {
        "max_results": max_results,
        "max_query_credits_per_scan": max_query_credits,
        "min_usable_hosts_target": min_usable_hosts_target,
        "effective_limit": effective_limit,
        "max_pages": max_pages,
    }


def _record_result_metadata(op, result: dict) -> Optional[str]:
    """Record metadata for a Shodan result and return its IP when valid."""
    ip = result.get("ip_str")
    if not isinstance(ip, str) or not ip:
        return None

    if not isinstance(op.shodan_host_metadata, dict):
        op.output.error(
            f"CRITICAL: shodan_host_metadata corrupted during Shodan result processing - "
            f"expected dict, got {type(op.shodan_host_metadata)}: {op.shodan_host_metadata}"
        )
        op.shodan_host_metadata = {}

    location = result.get("location", {})
    country_name = location.get("country_name") or result.get("country_name")
    country_code = location.get("country_code") or result.get("country_code")
    org = result.get("org", "")
    isp = result.get("isp", "")

    metadata = op.shodan_host_metadata.setdefault(ip, {})

    if country_name and not metadata.get("country_name"):
        metadata["country_name"] = country_name
    if country_code and not metadata.get("country_code"):
        metadata["country_code"] = country_code
    if org and not metadata.get("org_normalized") and isinstance(org, str):
        metadata["org"] = org
        metadata["org_normalized"] = org.lower()
    if isp and not metadata.get("isp_normalized") and isinstance(isp, str):
        metadata["isp"] = isp
        metadata["isp_normalized"] = isp.lower()

    return ip


def _collect_shodan_matches(op, query: str, query_limits: Dict[str, int]) -> List[dict]:
    """
    Fetch Shodan matches within the configured credit budget and result cap.
    """
    effective_limit = query_limits["effective_limit"]
    max_pages = query_limits["max_pages"]
    adaptive_enabled = query_limits["max_query_credits_per_scan"] > 1
    usable_target = query_limits["min_usable_hosts_target"]

    if effective_limit <= 0:
        return []

    op.output.info(
        "SMB Shodan budget: "
        f"requested {query_limits['max_results']} results, "
        f"budget {query_limits['max_query_credits_per_scan']} credit(s), "
        f"effective limit {effective_limit} ({max_pages} page(s) max)"
    )

    matches: List[dict] = []
    collected_ips: Set[str] = set()
    non_excluded_candidate_ips: Set[str] = set()
    page = 1
    pages_fetched = 0

    while len(matches) < effective_limit and page <= max_pages:
        try:
            response = op.shodan_api.search(
                query,
                page=page,
                minify=False,
                fields=SHODAN_RESULT_FIELDS,
            )
        except shodan.APIError as e:
            err_text = str(e).lower()
            if ("unable to parse json response" in err_text or "search cursor timed out" in err_text) and matches:
                op.output.warning(
                    f"Shodan API paging interrupted on page {page} ({e}); using "
                    f"{len(matches)} results collected so far"
                )
                break
            raise
        pages_fetched = page

        page_matches = response.get("matches", [])
        if not isinstance(page_matches, list) or not page_matches:
            break

        remaining = effective_limit - len(matches)
        page_slice = page_matches[:remaining]
        matches.extend(page_slice)

        for result in page_slice:
            ip = _record_result_metadata(op, result)
            if not ip or ip in collected_ips:
                continue
            collected_ips.add(ip)
            if not host_filter.should_exclude_ip(op, ip):
                non_excluded_candidate_ips.add(ip)

        if len(page_matches) < SHODAN_PAGE_SIZE:
            break

        if adaptive_enabled and len(non_excluded_candidate_ips) >= usable_target:
            op.output.info(
                "SMB adaptive query target reached: "
                f"{len(non_excluded_candidate_ips)} exclusion-passing candidates after {page} page(s)"
            )
            break

        page += 1

    op.output.print_if_verbose(
        f"Shodan query fetched {len(matches)} matches over {pages_fetched} page(s); "
        f"exclusion-passing candidates: {len(non_excluded_candidate_ips)}"
    )
    if isinstance(getattr(op, "stats", None), dict):
        op.stats["shodan_pages_fetched"] = pages_fetched
        op.stats["shodan_effective_limit"] = effective_limit
        op.stats["shodan_non_excluded_candidates"] = len(non_excluded_candidate_ips)
    return matches


def query_shodan(op, country: Optional[str] = None, custom_filters: Optional[str] = None) -> Tuple[Set[str], str]:
    """
    Query Shodan for SMB servers in specified country.
    Mirrors previous _query_shodan logic but lives in a helper module.
    """
    # Debug trace at start of Shodan query
    op.output.print_if_verbose(
        f"DEBUG: At start of _query_shodan - shodan_host_metadata type: {type(op.shodan_host_metadata)}, "
        f"len: {len(op.shodan_host_metadata) if isinstance(op.shodan_host_metadata, dict) else 'N/A'}"
    )

    target_countries = op.config.resolve_target_countries(country)
    query = ""

    if target_countries:
        op.output.info(f"Querying Shodan for SMB servers in: {', '.join(target_countries)}")
        op.output.print_if_verbose(f"Country-specific scan: {len(target_countries)} countries specified")
    else:
        op.output.info("Performing global Shodan search (no country filter)")
        op.output.print_if_verbose("Global scan mode: maximum discovery coverage")

    try:
        query = build_targeted_query(op, target_countries, custom_filters)

        shodan_config = op.config.get_shodan_config()
        query_limits = _resolve_query_limits(shodan_config)
        matches = _collect_shodan_matches(op, query, query_limits)

        ip_addresses = set()
        for result in matches:
            ip = _record_result_metadata(op, result)
            if not ip:
                continue
            ip_addresses.add(ip)

        op.stats['shodan_results'] = len(ip_addresses)
        op.output.success(f"Found {len(ip_addresses)} SMB servers in Shodan database")
        op.output.print_if_verbose(f"Captured metadata for {len(op.shodan_host_metadata)} hosts")

        return ip_addresses, query

    except shodan.APIError as e:
        op.output.error(f"Shodan API error: {e}")
        return set(), query
    except Exception as e:
        op.output.error(f"Shodan query failed: {e}")
        return set(), query


def build_targeted_query(op, countries: List[str], custom_filters: Optional[str] = None) -> str:
    """
    Build a targeted Shodan query for vulnerable SMB servers.
    """
    query_config = op.config.get("shodan", "query_components", {})

    base_query = query_config.get("base_query", "smb authentication: disabled")
    product_filter = query_config.get("product_filter", 'product:"Samba"')

    query_parts = [base_query, product_filter]

    if custom_filters:
        query_parts.append(custom_filters)
        op.output.print_if_verbose(f"Custom Shodan filters applied: {custom_filters}")
    else:
        op.output.print_if_verbose("No custom Shodan filters applied")

    if countries:
        if len(countries) == 1:
            country_filter = f'country:{countries[0]}'
        else:
            country_codes = ','.join(countries)
            country_filter = f'country:{country_codes}'
        query_parts.append(country_filter)

    additional_exclusions = query_config.get("additional_exclusions", ['-"DSL"'])

    query_parts.extend(additional_exclusions)

    final_query = ' '.join(query_parts)
    query_type = "country-specific" if countries else "global"
    op.output.print_if_verbose(f"Shodan query ({query_type}): {final_query}")

    return final_query
