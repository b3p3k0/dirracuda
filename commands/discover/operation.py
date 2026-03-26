"""
Dirracuda Discover Operations

Discovery and authentication testing split into cohesive helper modules to
reduce individual file size while keeping the public API stable.
"""

import os
import sys
import threading
from typing import Set, List, Dict, Optional, Tuple

import shodan

# Add project paths for imports when executed outside package context
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
tools_path = os.path.join(PROJECT_ROOT, 'tools')
if tools_path not in sys.path:
    sys.path.insert(0, tools_path)

from shared.config import load_config  # noqa: F401  (import retained for compatibility)
from shared.database import create_workflow_database  # noqa: F401
from shared.output import create_output_manager  # noqa: F401

from .models import DiscoverResult
from .smb_support import SMB_AVAILABLE
from .connection_pool import SMBConnectionPool
from . import shodan_query, host_filter, auth


class DiscoverOperation:
    """
    SMB discovery and authentication testing operation.
    """

    def __init__(self, config, output, database, session_id, cautious_mode: bool = False):
        self.config = config
        self.output = output
        self.database = database
        self.session_id = session_id
        self.cautious_mode = cautious_mode

        self.shodan_host_metadata = {}
        self._host_lookup_cache = {}
        self._auth_rate_lock = threading.Lock()
        self._last_auth_attempt = 0
        self._smbclient_auth_cache = {}
        self._connection_pool = SMBConnectionPool(max_connections_per_host=1, idle_timeout=60)

        try:
            api_key = self.config.get_shodan_api_key()
            self.shodan_api = shodan.Shodan(api_key)
        except ValueError as e:
            self.shodan_api = None
            self.output.error(str(e))

        self.exclusions = host_filter.load_exclusions(self)

        self.smbclient_available = auth.check_smbclient_availability()
        if not self.smbclient_available:
            self.output.print_if_verbose(
                "SMB adapter unavailable; install required Python SMB libraries "
                "(smbprotocol/impacket) for discovery authentication"
            )

        self.stats = {
            'shodan_results': 0,
            'excluded_ips': 0,
            'new_hosts': 0,
            'skipped_hosts': 0,
            'successful_auth': 0,
            'failed_auth': 0,
            'total_processed': 0
        }

    def execute(self, country=None, rescan_all=False, rescan_failed=False,
                force_hosts=None, custom_filters: Optional[str] = None) -> DiscoverResult:
        """
        Execute the discover operation.
        """
        if not SMB_AVAILABLE:
            raise RuntimeError("SMB libraries not available. Please install: pip install smbprotocol")

        if not self.shodan_api:
            raise RuntimeError("Shodan API not available")

        self.shodan_host_metadata = {}
        self._host_lookup_cache = {}
        self._smbclient_auth_cache = {}

        if force_hosts is None:
            force_hosts = set()

        self.output.print_if_verbose("Starting discovery operation...")
        if force_hosts:
            self.output.print_if_verbose(f"Forced hosts specified: {', '.join(sorted(force_hosts))}")

        if custom_filters is None:
            custom_filters = ""

        shodan_results, query_used = self._query_shodan(country, custom_filters)

        if force_hosts:
            forced_hosts_added = force_hosts - shodan_results
            if forced_hosts_added:
                self.output.print_if_verbose(f"Adding {len(forced_hosts_added)} forced hosts not in Shodan results")
                for ip in forced_hosts_added:
                    self.shodan_host_metadata[ip] = {'country_name': 'Unknown'}
            shodan_results = shodan_results.union(force_hosts)

        if not shodan_results:
            self.output.warning("No results from Shodan query and no forced hosts")
            return DiscoverResult(
                query_used=query_used,
                total_hosts=0,
                authenticated_hosts=0,
                host_ips=set()
            )

        self.output.print_if_verbose(
            f"DEBUG: Before exclusions - shodan_host_metadata type: {type(self.shodan_host_metadata)}, "
            f"len: {len(self.shodan_host_metadata) if isinstance(self.shodan_host_metadata, dict) else 'N/A'}"
        )
        filtered_results = self._apply_exclusions(shodan_results)

        hosts_to_scan, filter_stats = self.database.get_new_hosts_filter(
            filtered_results,
            rescan_all=rescan_all,
            rescan_failed=rescan_failed,
            output_manager=self.output
        )

        if force_hosts:
            forced_hosts_bypassed = force_hosts - hosts_to_scan
            if forced_hosts_bypassed:
                self.output.print_if_verbose(f"Adding {len(forced_hosts_bypassed)} forced hosts (bypassing database filters)")
                hosts_to_scan = hosts_to_scan.union(force_hosts)
                filter_stats['forced_hosts'] = len(force_hosts)
                filter_stats['to_scan'] = len(hosts_to_scan)

        if not isinstance(self.shodan_host_metadata, dict):
            self.output.error(
                f"CRITICAL: shodan_host_metadata corrupted before trimming - expected dict, "
                f"got {type(self.shodan_host_metadata)}: {self.shodan_host_metadata}"
            )
            self.shodan_host_metadata = {}

        if not isinstance(hosts_to_scan, (set, list, tuple)):
            self.output.error(
                f"CRITICAL: hosts_to_scan has unexpected type - expected set/list/tuple, "
                f"got {type(hosts_to_scan)}: {hosts_to_scan}"
            )
            hosts_to_scan = set()

        try:
            new_metadata = {}
            for ip in hosts_to_scan:
                if ip in self.shodan_host_metadata:
                    if isinstance(self.shodan_host_metadata[ip], dict):
                        new_metadata[ip] = self.shodan_host_metadata[ip]
                    else:
                        self.output.warning(
                            f"Skipping corrupted metadata for IP {ip}: "
                            f"expected dict, got {type(self.shodan_host_metadata[ip])}"
                        )

            self.shodan_host_metadata = new_metadata
        except Exception as e:
            self.output.error(f"CRITICAL: Error during metadata trimming: {e}")
            self.shodan_host_metadata = {}

        self.database.display_scan_statistics(filter_stats, hosts_to_scan)

        if not hosts_to_scan:
            self.output.info("No new hosts to scan")
            return DiscoverResult(
                query_used=query_used,
                total_hosts=len(filtered_results),
                authenticated_hosts=0,
                host_ips=set()
            )

        successful_hosts = self._test_smb_authentication(hosts_to_scan, country)
        authenticated_ips = self._save_to_database(successful_hosts, country)

        self.output.print_if_verbose(f"Discovery operation completed: {len(authenticated_ips)} authenticated hosts")

        return DiscoverResult(
            query_used=query_used,
            total_hosts=len(hosts_to_scan),
            authenticated_hosts=len(authenticated_ips),
            host_ips=authenticated_ips
        )

    # Wrapper methods delegate to helper modules; kept for backward compatibility and readability.
    def _query_shodan(self, country=None, custom_filters: Optional[str] = None) -> Tuple[Set[str], str]:
        return shodan_query.query_shodan(self, country, custom_filters)

    def _build_targeted_query(self, countries: list, custom_filters: Optional[str] = None) -> str:
        return shodan_query.build_targeted_query(self, countries, custom_filters)

    def _apply_exclusions(self, ip_addresses: Set[str]) -> Set[str]:
        return host_filter.apply_exclusions(self, ip_addresses)

    def _should_exclude_ip(self, ip: str) -> bool:
        return host_filter.should_exclude_ip(self, ip)

    def _load_exclusions(self) -> List[str]:
        return host_filter.load_exclusions(self)

    def _check_smbclient_availability(self) -> bool:
        return auth.check_smbclient_availability()

    def _throttled_auth_wait(self) -> None:
        return auth.throttled_auth_wait(self)

    def _basic_throttled_auth_wait(self) -> None:
        return auth.basic_throttled_auth_wait(self)

    def _test_single_host_concurrent(self, ip: str, country=None) -> Dict:
        return auth.test_single_host_concurrent(self, ip, country)

    def _test_smb_authentication(self, ip_addresses: Set[str], country=None) -> List[Dict]:
        return auth.test_smb_authentication(self, ip_addresses, country)

    def _test_smb_authentication_sequential(self, ip_list: List[str], country=None) -> List[Dict]:
        return auth.test_smb_authentication_sequential(self, ip_list, country)

    def _test_single_host(self, ip: str, country=None) -> Optional[Dict]:
        return auth.test_single_host(self, ip, country)

    def _check_port(self, ip: str, port: int) -> bool:
        return auth.check_port(self, ip, port)

    def _test_smb_auth(self, ip: str, username: str, password: str) -> bool:
        return auth.test_smb_auth(self, ip, username, password)

    def _test_smb_alternative(self, ip: str) -> Optional[str]:
        return auth.test_smb_alternative(self, ip)

    def _get_optimal_workers(self, total_hosts: int, max_concurrent: int) -> int:
        return auth.get_optimal_workers(self, total_hosts, max_concurrent)

    def _report_concurrent_progress(self, completed: int, total: int,
                                    success_count: int, failed_count: int,
                                    active_threads: int):
        return auth.report_concurrent_progress(self, completed, total, success_count, failed_count, active_threads)

    def _save_to_database(self, successful_hosts: List[Dict], country=None) -> Set[str]:
        """
        Save successful authentication results to database using provided session_id.
        """
        try:
            from db_manager import SMBSeekDataAccessLayer
            dal = SMBSeekDataAccessLayer(self.database.db_manager)

            authenticated_ips = set()
            for host in successful_hosts:
                dal.get_or_create_server(
                    ip_address=host['ip_address'],
                    country=host['country'],
                    auth_method=host['auth_method'],
                    country_code=host.get('country_code')
                )
                authenticated_ips.add(host['ip_address'])

            self.output.print_if_verbose(f"Saved {len(successful_hosts)} authenticated hosts to database")
            return authenticated_ips

        except Exception as e:
            self.output.error(f"Failed to save results to database: {e}")
            return set()
