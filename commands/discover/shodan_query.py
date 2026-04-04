import shodan
from typing import Set, Optional, Tuple, List


SHODAN_PAGE_SIZE = 100
MAX_SHODAN_PAGES = 200
SHODAN_RESULT_FIELDS = [
    "ip_str",
    "location.country_name",
    "location.country_code",
    "org",
    "isp",
]


def _collect_shodan_matches(op, query: str, max_results: int) -> List[dict]:
    """
    Fetch Shodan matches in smaller pages to avoid large one-shot payload stalls.
    """
    try:
        target = int(max_results)
    except (TypeError, ValueError):
        target = 1000

    if target <= 0:
        target = 1000

    matches: List[dict] = []
    page = 1

    while len(matches) < target and page <= MAX_SHODAN_PAGES:
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

        page_matches = response.get("matches", [])
        if not isinstance(page_matches, list) or not page_matches:
            break

        remaining = target - len(matches)
        matches.extend(page_matches[:remaining])

        if len(page_matches) < SHODAN_PAGE_SIZE:
            break

        page += 1

    op.output.print_if_verbose(f"Shodan paging fetched {len(matches)} matches across {page} page(s)")
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
        max_results = shodan_config['query_limits']['max_results']
        matches = _collect_shodan_matches(op, query, max_results)

        ip_addresses = set()
        for result in matches:
            ip = result.get('ip_str')
            if not isinstance(ip, str) or not ip:
                continue
            ip_addresses.add(ip)

            location = result.get('location', {})
            country_name = location.get('country_name') or result.get('country_name')
            country_code = location.get('country_code') or result.get('country_code')
            org = result.get('org', '')
            isp = result.get('isp', '')

            if not isinstance(op.shodan_host_metadata, dict):
                op.output.error(
                    f"CRITICAL: shodan_host_metadata corrupted during Shodan result processing - "
                    f"expected dict, got {type(op.shodan_host_metadata)}: {op.shodan_host_metadata}"
                )
                op.shodan_host_metadata = {}

            metadata = op.shodan_host_metadata.setdefault(ip, {})

            if country_name and not metadata.get('country_name'):
                metadata['country_name'] = country_name
            if country_code and not metadata.get('country_code'):
                metadata['country_code'] = country_code

            if org and not metadata.get('org_normalized') and isinstance(org, str):
                metadata['org'] = org
                metadata['org_normalized'] = org.lower()
            if isp and not metadata.get('isp_normalized') and isinstance(isp, str):
                metadata['isp'] = isp
                metadata['isp_normalized'] = isp.lower()

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
