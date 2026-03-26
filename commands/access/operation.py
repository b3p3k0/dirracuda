"""
Dirracuda Access Operations

Share access verification split into cohesive helpers to reduce file size
while keeping the public API stable.
"""

import csv
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Set, List, Dict, Optional, Any

# Add project paths for imports when executed outside package context
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config import load_config, get_standard_timestamp  # noqa: F401 (retained for compatibility)
from shared.database import create_workflow_database  # noqa: F401
from shared.output import create_output_manager  # noqa: F401

from .models import AccessResult
from .smb_support import SMB_AVAILABLE
from . import share_enumerator, share_tester, rce_analyzer


class AccessOperation:
    """
    SMB share access verification operation.
    """

    def __init__(self, config, output, database, session_id, cautious_mode=False, check_rce=False):
        self.config = config
        self.output = output
        self.database = database
        self.session_id = session_id
        self.cautious_mode = cautious_mode
        self.check_rce = check_rce

        self.results = []
        self.total_targets = 0

    def enumerate_shares(self, ip, username, password):
        return share_enumerator.enumerate_shares(self, ip, username, password)

    def test_share_access(self, ip, share_name, username, password):
        return share_tester.test_share_access(self, ip, share_name, username, password)

    def _extract_nt_status(self, message: str) -> Optional[str]:
        return share_tester._extract_nt_status(message)

    def _analyze_rce_vulnerabilities(self, target_result: Dict[str, Any]) -> None:
        return rce_analyzer.analyze_rce_vulnerabilities(self, target_result)

    def execute(self, target_ips: Set[str], recent_hours=None) -> AccessResult:
        """
        Execute the access verification operation.
        """
        if not SMB_AVAILABLE:
            raise RuntimeError("SMB libraries not available. Install with: pip install smbprotocol pyspnego")

        self.output.print_if_verbose("Starting share access verification...")

        target_ip_list = list(target_ips)

        authenticated_hosts = self.database.get_authenticated_hosts(
            ip_filter=target_ip_list,
            recent_hours=recent_hours
        )

        if not authenticated_hosts:
            self.output.warning("No authenticated hosts available for share testing")
            return AccessResult(
                accessible_hosts=0,
                accessible_shares=0,
                share_details=[]
            )

        self.total_targets = len(authenticated_hosts)
        self.output.info(f"Testing share access on {self.total_targets} authenticated hosts")

        # Create shared SafeProbeRunner for this scan (used by RCE analysis)
        self._probe_runner = None
        if self.check_rce:
            try:
                from shared.rce_scanner.probes import SafeProbeRunner
                legacy_mode = getattr(self, 'legacy_mode', False)
                self._probe_runner = SafeProbeRunner(self.config, legacy_mode=legacy_mode)
            except Exception as e:
                self.output.error(f"Failed to initialize RCE probe runner: {e}")
                self._probe_runner = None

        max_concurrent = self.config.get_max_concurrent_hosts()
        max_workers = min(max_concurrent, len(authenticated_hosts) or 1)

        results_by_index = [None] * len(authenticated_hosts)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_metadata = {}
            for index, host in enumerate(authenticated_hosts):
                host_position = index + 1
                future = executor.submit(self.process_target, host, host_position)
                future_to_metadata[future] = (index, host_position)

            for future in future_to_metadata:
                index, _host_position = future_to_metadata[future]
                try:
                    result = future.result()
                    results_by_index[index] = result
                except Exception as e:
                    host = authenticated_hosts[index]
                    error_result = {
                        'ip_address': host.get('ip_address', 'unknown'),
                        'country': host.get('country', 'unknown'),
                        'auth_method': host.get('auth_method', 'unknown'),
                        'timestamp': get_standard_timestamp(),
                        'error': f"Processing failed: {str(e)}",
                        'shares_found': [],
                        'accessible_shares': [],
                        'share_details': []
                    }
                    results_by_index[index] = error_result
                    self.output.error(f"Failed to process {host.get('ip_address', 'unknown')}: {str(e)}")

        self.results = results_by_index

        accessible_hosts, accessible_shares, share_details = self._save_and_summarize_results()

        self.output.print_if_verbose(f"Access verification completed: {accessible_hosts} hosts, {accessible_shares} shares")

        return AccessResult(
            accessible_hosts=accessible_hosts,
            accessible_shares=accessible_shares,
            share_details=share_details
        )

    def parse_auth_method(self, auth_method_str):
        """Parse authentication method string to extract credentials."""
        auth_lower = auth_method_str.lower()

        if 'anonymous' in auth_lower:
            return "", ""
        elif 'guest/blank' in auth_lower or 'guest/' in auth_lower:
            return "guest", ""
        elif 'guest/guest' in auth_lower:
            return "guest", "guest"
        else:
            self.output.print_if_verbose(f"Unknown auth method '{auth_method_str}', defaulting to guest/guest")
            return "guest", "guest"

    def process_target(self, host_record, host_position):
        """Process a single host target for share access testing."""
        ip = host_record['ip_address']
        country = host_record.get('country', 'Unknown')
        auth_method = host_record['auth_method']

        host_label = f"Host {host_position}/{self.total_targets}"
        self.output.info(f"[{host_position}/{self.total_targets}] Testing {ip} ({country})...")

        username, password = self.parse_auth_method(auth_method)
        self.output.info(f"Using auth: {username}/{password if password else '[blank]'}")

        target_result = {
            'ip_address': ip,
            'country': country,
            'auth_method': auth_method,
            'timestamp': get_standard_timestamp(),
            'shares_found': [],
            'accessible_shares': [],
            'share_details': []
        }

        try:
            port_timeout = self.config.get_connection_timeout()
            if not self.check_port(ip, 445, port_timeout):
                self.output.error(f"Port 445 not accessible on {ip}")
                target_result['error'] = 'Port 445 not accessible'
                return target_result

            shares = self.enumerate_shares(ip, username, password)
            target_result['shares_found'] = shares

            if not shares:
                self.output.warning(f"No non-administrative shares found on {ip}")
                if self.check_rce:
                    self._analyze_rce_vulnerabilities(target_result)
                return target_result

            self.output.success(f"Found {len(shares)} shares to test on {ip}")

            for i, share_name in enumerate(shares, 1):
                access_result = self.test_share_access(ip, share_name, username, password)
                target_result['share_details'].append(access_result)

                if access_result['accessible']:
                    target_result['accessible_shares'].append(share_name)
                    self.output.success(f"{host_label}: Share {i}/{len(shares)}: {share_name} - accessible")
                else:
                    message = access_result.get('error', 'not accessible') or ''
                    status = access_result.get('auth_status') or ''
                    if 'ACCESS_DENIED' in status:
                        self.output.warning(f"{host_label}: Share {i}/{len(shares)}: {share_name} - Access denied")
                    elif 'BAD_NETWORK_NAME' in status or 'share not found' in message.lower():
                        self.output.warning(f"{host_label}: Share {i}/{len(shares)}: {share_name} - {message}")
                    elif 'TIMEOUT' in status or 'timeout' in message.lower() or 'connection' in message.lower():
                        self.output.error(f"{host_label}: Share {i}/{len(shares)}: {share_name} - {message}")
                    else:
                        self.output.warning(f"{host_label}: Share {i}/{len(shares)}: {share_name} - Access failed")

                if share_name != shares[-1]:
                    delay = self.config.get_share_access_delay()
                    time.sleep(delay)

            accessible_count = len(target_result['accessible_shares'])
            total_count = len(shares)

            if accessible_count > total_count:
                self.output.error(f"VALIDATION ERROR: {accessible_count} accessible > {total_count} total shares on {ip}")
                target_result['validation_error'] = f"Accessible count ({accessible_count}) exceeds total count ({total_count})"

            if len(set(target_result['accessible_shares'])) != len(target_result['accessible_shares']):
                duplicates = [x for x in target_result['accessible_shares'] if target_result['accessible_shares'].count(x) > 1]
                self.output.warning(f"VALIDATION WARNING: Duplicate shares in accessible list: {set(duplicates)}")
                target_result['validation_warning'] = f"Duplicate accessible shares: {set(duplicates)}"

            invalid_shares = [share for share in target_result['accessible_shares'] if share not in shares]
            if invalid_shares:
                self.output.error(f"VALIDATION ERROR: Accessible shares not in original list: {invalid_shares}")
                target_result['validation_error'] = f"Invalid accessible shares: {invalid_shares}"

            if accessible_count > 0:
                self.output.success(f"{accessible_count}/{total_count} shares accessible on {ip}: {', '.join(target_result['accessible_shares'])}")
            else:
                self.output.warning(f"0/{total_count} shares accessible on {ip}")

            if self.check_rce:
                self._analyze_rce_vulnerabilities(target_result)

        except Exception as e:
            self.output.error(f"Error testing target {ip}: {str(e)[:50]}")
            target_result['error'] = str(e)

        return target_result

    def _save_and_summarize_results(self):
        """
        Save results to database using session_id and compute summary statistics.
        """
        try:
            stored_count = 0
            validation_errors = 0

            for result in self.results:
                if 'error' in result or 'validation_error' in result:
                    if 'validation_error' in result:
                        validation_errors += 1
                        self.output.print_if_verbose(f"Skipping storage of {result.get('ip_address', 'unknown')} due to validation error")
                    continue

                if self.database.store_share_access_result(self.session_id, result):
                    stored_count += 1
                else:
                    self.output.print_if_verbose(f"Failed to store results for {result.get('ip_address', 'unknown')}")

            accessible_hosts = set()
            total_accessible_shares = 0
            share_details = []

            for result in self.results:
                if 'error' in result or 'validation_error' in result:
                    continue

                ip_address = result.get('ip_address')
                accessible_shares = result.get('accessible_shares', [])

                if accessible_shares:
                    accessible_hosts.add(ip_address)
                    total_accessible_shares += len(accessible_shares)

                    for share_name in accessible_shares:
                        share_details.append({
                            'ip_address': ip_address,
                            'share_name': share_name,
                            'accessible': True
                        })

            valid_results_count = len([r for r in self.results if 'error' not in r and 'validation_error' not in r])
            self.output.print_if_verbose(f"Stored {stored_count}/{valid_results_count} valid results to database")

            if validation_errors > 0:
                self.output.warning(f"Excluded {validation_errors} results due to validation errors")

            return len(accessible_hosts), total_accessible_shares, share_details

        except Exception as e:
            self.output.error(f"Failed to save results to database: {e}")
            return 0, 0, []

    def check_port(self, ip, port, timeout):
        """Check if a specific port is open on the target."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def save_results(self):
        """Save results to database and JSON file."""
        try:
            session_id = self.database.create_session('access')
            stored_count = 0
            validation_errors = 0

            for result in self.results:
                if 'error' in result or 'validation_error' in result:
                    if 'validation_error' in result:
                        validation_errors += 1
                        self.output.print_if_verbose(f"Skipping storage of {result.get('ip_address', 'unknown')} due to validation error")
                    continue

                if self.database.store_share_access_result(session_id, result):
                    stored_count += 1
                else:
                    self.output.print_if_verbose(f"Failed to store results for {result.get('ip_address', 'unknown')}")

            valid_results_count = len([r for r in self.results if 'error' not in r and 'validation_error' not in r])
            self.output.print_if_verbose(f"Stored {stored_count}/{valid_results_count} valid results to database")
            if validation_errors > 0:
                self.output.warning(f"Excluded {validation_errors} results due to validation errors")

            output_file = f"share_access_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            output_data = {
                'metadata': {
                    'tool': 'smbseek_access',
                    'version': '1.0',
                    'scan_date': get_standard_timestamp(),
                    'total_targets': self.total_targets,
                    'config': {
                        'share_access_delay': self.config.get_share_access_delay(),
                        'timeout': self.config.get_connection_timeout()
                    }
                },
                'results': self.results
            }

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            self.output.success(f"Results saved to {output_file}")

        except Exception as e:
            self.output.error(f"Failed to save results: {e}")

    def print_summary(self):
        """Print analysis summary."""
        if not self.results:
            return

        total_targets = len(self.results)
        targets_with_shares = len([r for r in self.results if r.get('shares_found')])
        targets_with_access = len([r for r in self.results if r.get('accessible_shares')])

        total_shares_found = sum(len(r.get('shares_found', [])) for r in self.results)
        total_accessible_shares = sum(len(r.get('accessible_shares', [])) for r in self.results)

        self.output.header("=== SUMMARY ===")
        self.output.info(f"Total targets processed: {total_targets}")
        self.output.info(f"Targets with shares: {targets_with_shares}")
        self.output.info(f"Targets with accessible shares: {targets_with_access}")
        self.output.info(f"Total shares found: {total_shares_found}")
        self.output.info(f"Total accessible shares: {total_accessible_shares}")

        if total_shares_found > 0:
            access_rate = (total_accessible_shares / total_shares_found) * 100
            self.output.info(f"Share access rate: {access_rate:.1f}%")
