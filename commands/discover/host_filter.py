from typing import Set, List


def apply_exclusions(op, ip_addresses: Set[str]) -> Set[str]:
    """
    Apply exclusion filters to IP addresses.
    """
    if not isinstance(op.shodan_host_metadata, dict):
        op.output.error(
            f"CRITICAL: shodan_host_metadata corrupted at start of exclusion filtering - expected dict, "
            f"got {type(op.shodan_host_metadata)}: {op.shodan_host_metadata}"
        )
        op.shodan_host_metadata = {}

    if not op.exclusions:
        return ip_addresses

    total_ips = len(ip_addresses)
    op.output.info(f"Applying exclusion filters to {total_ips} IPs...")

    filtered_ips = set()
    excluded_count = 0
    processed_count = 0

    try:
        progress_interval = int(op.config.get("exclusions", "progress_interval", 100))
    except (ValueError, TypeError):
        progress_interval = 100

    for ip in ip_addresses:
        processed_count += 1

        if processed_count % progress_interval == 0 or processed_count == 1 or processed_count == total_ips:
            progress_pct = (processed_count / total_ips) * 100
            op.output.info(f"🔍 Filtering progress: {processed_count}/{total_ips} ({progress_pct:.1f}%) | Excluded: {excluded_count}")

        if should_exclude_ip(op, ip):
            excluded_count += 1
            op.shodan_host_metadata.pop(ip, None)
        else:
            filtered_ips.add(ip)

    op.stats['excluded_ips'] = excluded_count

    if excluded_count > 0:
        op.output.info(f"✓ Excluded {excluded_count} IPs (ISPs, cloud providers, etc.)")

    return filtered_ips


def should_exclude_ip(op, ip: str) -> bool:
    """
    Check if IP should be excluded based on organization using cached metadata.
    """
    if not isinstance(op.shodan_host_metadata, dict):
        op.output.error(
            f"CRITICAL: shodan_host_metadata corrupted - expected dict, got {type(op.shodan_host_metadata)}: "
            f"{op.shodan_host_metadata}"
        )
        op.shodan_host_metadata = {}

    metadata = op.shodan_host_metadata.get(ip, {})
    org_normalized = metadata.get('org_normalized')
    isp_normalized = metadata.get('isp_normalized')

    if not isinstance(org_normalized, str):
        org = metadata.get('org', '')
        org_normalized = org.lower() if isinstance(org, str) else ''
    if not isinstance(isp_normalized, str):
        isp = metadata.get('isp', '')
        isp_normalized = isp.lower() if isinstance(isp, str) else ''

    # Metadata missing or incomplete: fail-open to scan.
    if not org_normalized and not isp_normalized:
        return False

    for pattern in getattr(op, "exclusion_patterns", []):
        if pattern in org_normalized or pattern in isp_normalized:
            return True
    return False


def load_exclusions(op) -> List[str]:
    """Load exclusion list from config (supports JSON and legacy .txt)."""
    exclusions = op.config.get_exclusion_list()
    op.exclusion_patterns = [pattern.lower() for pattern in exclusions]
    op.output.print_if_verbose(f"Loaded {len(exclusions)} exclusion patterns")
    return exclusions
