"""
Mock operations for BackendInterface testing.

Contains functions that simulate realistic backend operations without requiring
actual backend dependencies, Shodan API keys, or network access.
"""

import time
from typing import Dict, List, Optional, Callable, Any


def mock_initialization_scan(progress_callback: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """
    Mock initialization scan for testing.

    Args:
        progress_callback: Progress callback function

    Returns:
        Mock scan results
    """
    progress_steps = [
        (5, "Reading configuration..."),
        (15, "Configuration loaded: ../backend/conf/config.json"),
        (25, "Starting scan for countries: US"),
        (40, "Connecting to Shodan API..."),
        (55, "Processing host discovery..."),
        (70, "Testing SMB connections..."),
        (85, "Analyzing results..."),
        (95, "Creating database..."),
        (100, "Scan completed successfully")
    ]

    for percentage, message in progress_steps:
        if progress_callback:
            progress_callback(percentage, message)
        time.sleep(0.5)  # Simulate work

    return {
        'success': True,
        'database_path': '../backend/smbseek.db',
        'servers_found': 25,
        'scan_result': {
            'success': True,
            'hosts_tested': 25,
            'successful_auth': 5,
            'failed_auth': 20
        }
    }


def mock_scan_operation(countries: List[str], progress_callback: Optional[Callable]) -> Dict:
    """
    Mock scan operation for testing.

    Args:
        countries: Countries to "scan"
        progress_callback: Progress callback function

    Returns:
        Mock scan results

    Design Decision: Realistic progress simulation helps test UI
    responsiveness and progress display functionality.
    """
    if progress_callback:
        # Simulate realistic scan progress
        stages = [
            (10, "Querying Shodan for SMB servers"),
            (20, "Applying exclusion filters"),
            (25, "Database filtering complete"),
            (30, "Testing SMB authentication on 120 hosts"),
            (50, "Progress: 60/120 (50.0%) | Success: 8, Failed: 52"),
            (75, "Progress: 90/120 (75.0%) | Success: 18, Failed: 72"),
            (100, "Discovery complete")
        ]

        for percentage, message in stages:
            time.sleep(0.5)  # Simulate work
            progress_callback(percentage, message)

    return {
        "success": True,
        "shodan_results": 150,
        "hosts_tested": 120,
        "successful_auth": 23,
        "failed_auth": 97,
        "session_id": 3,
        "countries": countries
    }


def mock_discover_operation(countries: List[str], progress_callback: Optional[Callable]) -> Dict:
    """Mock discovery-only operation."""
    return mock_scan_operation(countries, progress_callback)


def mock_access_verification_operation(recent_days: Optional[int], progress_callback: Optional[Callable]) -> Dict:
    """Mock access verification operation."""
    if progress_callback:
        # Simulate recent filtering progress
        stages = [
            (10, f"Loading hosts from last {recent_days or 90} days"),
            (25, "Found 45 hosts within recent timeframe"),
            (40, "Testing SMB access on 45 recent hosts"),
            (70, "Progress: 32/45 (71.1%) | Success: 12, Failed: 20"),
            (90, "Progress: 43/45 (95.6%) | Success: 18, Failed: 25"),
            (100, "Access verification completed")
        ]

        for percentage, message in stages:
            time.sleep(0.3)  # Simulate work
            progress_callback(percentage, message)

    return {
        "success": True,
        "recent_days_filter": recent_days or 90,
        "hosts_tested": 45,
        "successful_auth": 18,
        "failed_auth": 27,
        "skipped_hosts": 75  # Hosts skipped due to recent filtering
    }


def mock_ftp_scan_operation(
    countries: List[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """Mock FTP scan for --mock mode and tests."""
    steps = [
        (5.0,   "Starting FTP scan (mock)"),
        (15.0,  "[1/2] FTP Discovery"),
        (30.0,  "📊 Progress: 3/10 (30.0%)"),
        (50.0,  "📊 Progress: 5/10 (50.0%)"),
        (70.0,  "📊 Progress: 7/10 (70.0%)"),
        (80.0,  "[2/2] FTP Access Verification"),
        (90.0,  "📊 Progress: 9/10 (90.0%)"),
        (100.0, "🎉 FTP scan completed successfully"),
    ]
    for pct, msg in steps:
        if progress_callback:
            progress_callback(pct, msg)
        time.sleep(0.3)

    return {
        "success": True,
        "hosts_scanned": 0,
        "hosts_accessible": 0,
        "accessible_shares": 0,
    }


def mock_http_scan_operation(
    countries: List[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """Mock HTTP scan for --mock mode and tests."""
    steps = [
        (5.0,   "Starting HTTP scan (mock)"),
        (15.0,  "[1/2] HTTP Discovery"),
        (30.0,  "📊 Progress: 3/10 (30.0%)"),
        (50.0,  "📊 Progress: 5/10 (50.0%)"),
        (70.0,  "📊 Progress: 7/10 (70.0%)"),
        (80.0,  "[2/2] HTTP Access Verification"),
        (90.0,  "📊 Progress: 9/10 (90.0%)"),
        (100.0, "🎉 HTTP scan completed successfully"),
    ]
    for pct, msg in steps:
        if progress_callback:
            progress_callback(pct, msg)
        time.sleep(0.3)

    return {
        "success": True,
        "hosts_scanned": 0,
        "hosts_accessible": 0,
        "accessible_shares": 0,
    }


def mock_access_on_servers_operation(ip_list: List[str], progress_callback: Optional[Callable]) -> Dict:
    """Mock access verification on specific servers."""
    if progress_callback:
        # Simulate targeted server testing
        total_servers = len(ip_list)
        for i, ip in enumerate(ip_list):
            percentage = ((i + 1) / total_servers) * 100
            progress_callback(percentage, f"Testing {ip}...")
            time.sleep(0.2)

    return {
        "success": True,
        "servers_specified": ip_list,
        "hosts_tested": len(ip_list),
        "successful_auth": len(ip_list) // 3,  # Mock some successes
        "failed_auth": len(ip_list) - (len(ip_list) // 3)
    }