"""
Dirracuda Shared Database Operations

Enhanced database operations for the unified CLI including new host filtering,
workflow management, and intelligent scanning logic.
"""

import json
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Iterable
import sys

# Add tools directory to path for DatabaseManager import
tools_path = os.path.join(os.path.dirname(__file__), '..', 'tools')
sys.path.insert(0, tools_path)

from db_manager import DatabaseManager, SMBSeekDataAccessLayer
from shared.db_migrations import run_migrations
from shared.database_ftp_persistence import FtpPersistence
from shared.database_http_persistence import HttpPersistence


class SMBSeekWorkflowDatabase:
    """
    Enhanced database operations for Dirracuda unified CLI workflow.
    
    Provides intelligent host filtering, workflow tracking, and database
    operations optimized for the new unified interface.
    """
    
    def __init__(self, config, verbose=False):
        """
        Initialize workflow database manager.
        
        Args:
            config: SMBSeekConfig instance
            verbose: Enable verbose output for debugging
        """
        self.config = config
        self.db_path = config.get_database_path()
        try:
            run_migrations(self.db_path)
        except Exception:
            pass
        self.db_manager = DatabaseManager(self.db_path, config.config)
        self.dal = SMBSeekDataAccessLayer(self.db_manager)
        self._verbose = verbose
        
        # Check if this is first run (database creation)
        self.is_first_run = self._check_first_run()
    
    def _check_first_run(self) -> bool:
        """
        Check if this is the first run (new database).
        
        Returns:
            True if database was just created or is empty
        """
        if not os.path.exists(self.db_path):
            return True
        
        try:
            servers = self.db_manager.execute_query("SELECT COUNT(*) as count FROM smb_servers")
            return servers[0]['count'] == 0
        except sqlite3.OperationalError as e:
            # Guard against partial/invalid schemas without spamming noisy tracebacks
            if self._verbose:
                print(f"⚠ Database schema check failed: {e}")
            return True
        except Exception:
            return True
    
    def show_database_status(self):
        """Display database status and first-run warning if needed."""
        if self.is_first_run:
            # Yellow warning for first run
            print(f"\033[93m⚠ No database found; will be created at {os.path.abspath(self.db_path)}\033[0m")
            print(f"🆕 First run detected - will scan all Shodan results for initial population")
        else:
            try:
                servers = self.db_manager.execute_query("SELECT COUNT(*) as count FROM smb_servers")
                count = servers[0]['count']
                print(f"📊 Database found with {count} known servers")
            except:
                print(f"📊 Database found at {self.db_path}")
    
    def get_new_hosts_filter(self, shodan_ips: Set[str], rescan_all: bool = False, 
                           rescan_failed: bool = False, output_manager=None) -> Tuple[Set[str], Dict[str, int]]:
        """
        Filter Shodan results to identify hosts that need scanning.
        
        Args:
            shodan_ips: Set of IP addresses from Shodan query
            rescan_all: Force rescan of all hosts regardless of age
            rescan_failed: Include previously failed hosts for rescanning
            output_manager: Output manager for progress messages
            
        Returns:
            Tuple of (ips_to_scan, statistics_dict)
        """
        if output_manager:
            output_manager.info(f"Checking {len(shodan_ips)} IPs against database ({self._get_known_servers_count()} known servers)...")
        
        if self.is_first_run:
            # First run - scan everything
            stats = {
                'total_from_shodan': len(shodan_ips),
                'known_hosts': 0,
                'new_hosts': len(shodan_ips),
                'recently_scanned': 0,
                'failed_hosts': 0,
                'to_scan': len(shodan_ips)
            }
            return shodan_ips, stats
        
        # Get information about known hosts
        if output_manager:
            output_manager.info("Analyzing scan history and rescan policies...")
        known_hosts_info = self._get_known_hosts_info(shodan_ips)
        
        # Calculate cutoff date for rescanning
        rescan_cutoff = datetime.now() - timedelta(days=self.config.get("workflow", "rescan_after_days", 30))
        
        ips_to_scan = set()
        stats = {
            'total_from_shodan': len(shodan_ips),
            'known_hosts': 0,
            'new_hosts': 0,
            'recently_scanned': 0,
            'failed_hosts': 0,
            'to_scan': 0
        }
        
        for ip in shodan_ips:
            if ip not in known_hosts_info:
                # New host - always scan
                ips_to_scan.add(ip)
                stats['new_hosts'] += 1
            else:
                # Known host - check scanning rules
                host_info = known_hosts_info[ip]
                stats['known_hosts'] += 1
                
                last_seen = datetime.fromisoformat(host_info['last_seen'])
                is_old = last_seen < rescan_cutoff
                was_successful = host_info['scan_count'] > 0
                
                if rescan_all:
                    # Force rescan all
                    ips_to_scan.add(ip)
                elif not was_successful and rescan_failed:
                    # Rescan failed hosts if requested
                    ips_to_scan.add(ip)
                    stats['failed_hosts'] += 1
                elif not was_successful and not self.config.should_skip_failed_hosts():
                    # Rescan failed hosts if not skipping them
                    ips_to_scan.add(ip)
                    stats['failed_hosts'] += 1
                elif was_successful and is_old:
                    # Rescan successful hosts that are old enough
                    ips_to_scan.add(ip)
                else:
                    # Skip recently scanned hosts
                    stats['recently_scanned'] += 1
        
        stats['to_scan'] = len(ips_to_scan)
        
        if output_manager:
            output_manager.info(f"Database filtering complete: {stats['new_hosts']} new, {stats['known_hosts']} known, {stats['to_scan']} to scan")
        
        return ips_to_scan, stats
    
    def _get_known_servers_count(self) -> int:
        """Get count of known servers in database."""
        try:
            result = self.db_manager.execute_query("SELECT COUNT(*) as count FROM smb_servers")
            return result[0]['count']
        except:
            return 0
    
    def _get_known_hosts_info(self, ips: Set[str]) -> Dict[str, Dict]:
        """
        Get information about known hosts from database.
        Uses batch processing for large IP sets to improve performance.
        
        Args:
            ips: Set of IP addresses to check
            
        Returns:
            Dictionary mapping IP to host information
        """
        if not ips:
            return {}
        
        # For large IP sets, process in batches to avoid SQL query limits
        batch_size = 500  # SQLite SQLITE_MAX_VARIABLE_NUMBER default is 999
        ips_list = list(ips)
        host_info = {}
        
        try:
            for i in range(0, len(ips_list), batch_size):
                batch = ips_list[i:i + batch_size]
                
                # Create query with placeholders for this batch
                placeholders = ','.join(['?' for _ in batch])
                query = f"""
                    SELECT ip_address, last_seen, scan_count, status
                    FROM smb_servers 
                    WHERE ip_address IN ({placeholders})
                """
                
                results = self.db_manager.execute_query(query, tuple(batch))
                
                # Convert batch results to dictionary
                for row in results:
                    host_info[row['ip_address']] = dict(row)
            
            return host_info
            
        except Exception as e:
            print(f"⚠ Error checking known hosts: {e}")
            return {}
    
    def display_scan_statistics(self, stats: Dict[str, int], ips_to_scan: Set[str]):
        """
        Display scanning statistics to user.
        
        Args:
            stats: Statistics dictionary from get_new_hosts_filter
            ips_to_scan: Set of IPs that will be scanned
        """
        print(f"\n📊 Scan Planning:")
        print(f"  • Total from Shodan: {stats['total_from_shodan']}")
        
        if not self.is_first_run:
            print(f"  • Already known: {stats['known_hosts']}")
            print(f"  • New discoveries: {stats['new_hosts']}")
            if stats['recently_scanned'] > 0:
                print(f"  • Recently scanned (skipping): {stats['recently_scanned']}")
            if stats['failed_hosts'] > 0:
                print(f"  • Previously failed: {stats['failed_hosts']}")

        if stats.get('forced_hosts', 0) > 0:
            print(f"  • Forced hosts: {stats['forced_hosts']}")

        print(f"  • Will scan: {stats['to_scan']}")
        
        if stats['to_scan'] == 0:
            print(f"✅ No new hosts to scan. Use --rescan-all or --rescan-failed to override.")
        else:
            print(f"🚀 Proceeding with {stats['to_scan']} hosts...")
    
    def record_scan_session(self, session_data: Dict) -> str:
        """
        Record a new scan session in the database.

        Args:
            session_data: Session information dictionary

        Returns:
            Session ID
        """
        tool_name = session_data.get('tool_name', 'dirracuda')
        scan_type = session_data.get('scan_type')
        if scan_type is None:
            scan_type = 'smbseek_unified' if tool_name == 'dirracuda' else tool_name

        session_id = self.dal.create_scan_session(
            tool_name=tool_name,
            config_snapshot=session_data,
            scan_type=scan_type
        )

        # Map legacy keys to schema column names and update session with actual metrics
        update_data = {}

        # Map legacy keys to schema fields
        if 'targets_found' in session_data:
            update_data['total_targets'] = session_data['targets_found']
        elif 'total_targets' in session_data:
            update_data['total_targets'] = session_data['total_targets']

        if 'successful_connections' in session_data:
            update_data['successful_targets'] = session_data['successful_connections']
        elif 'successful_targets' in session_data:
            update_data['successful_targets'] = session_data['successful_targets']

        if 'failed_targets' in session_data:
            update_data['failed_targets'] = session_data['failed_targets']
        elif 'total_targets' in update_data and 'successful_targets' in update_data:
            # Compute failed_targets if not provided
            update_data['failed_targets'] = update_data['total_targets'] - update_data['successful_targets']

        # Update session status to completed if we have metrics
        if update_data:
            update_data['status'] = 'completed'
            self.dal.update_scan_session(session_id, **update_data)

        return session_id
    
    def create_session(self, tool_name: str, scan_type: Optional[str] = None) -> int:
        """
        Create a new scan session for database storage.
        
        Args:
            tool_name: Name of the tool creating the session
            scan_type: Optional scan type label; defaults to tool_name
            
        Returns:
            Session ID
        """
        effective_scan_type = scan_type if scan_type is not None else tool_name
        session_data = {
            'tool_name': tool_name,
            'scan_type': effective_scan_type,
            'timestamp': datetime.now().isoformat(),
            'config_snapshot': self.config.config if hasattr(self.config, 'config') else None
        }
        return self.dal.create_scan_session(
            tool_name=tool_name,
            config_snapshot=session_data,
            scan_type=effective_scan_type
        )
    
    def get_recent_activity_summary(self, days: int = 7) -> Dict:
        """
        Get summary of recent scanning activity.

        Args:
            days: Number of days to look back

        Returns:
            Summary dictionary
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()

        try:
            # Recent sessions - use correct schema column names
            sessions = self.db_manager.execute_query("""
                SELECT COUNT(*) as session_count,
                       SUM(total_targets) as total_targets,
                       SUM(successful_targets) as total_successful
                FROM scan_sessions
                WHERE timestamp >= ?
            """, (cutoff_str,))

            # Recently updated servers
            servers = self.db_manager.execute_query("""
                SELECT COUNT(*) as updated_servers
                FROM smb_servers
                WHERE last_seen >= ?
            """, (cutoff_str,))

            return {
                'days': days,
                'scan_sessions': sessions[0]['session_count'] or 0,
                'targets_found': sessions[0]['total_targets'] or 0,
                'successful_connections': sessions[0]['total_successful'] or 0,
                'updated_servers': servers[0]['updated_servers'] or 0
            }
        except Exception as e:
            print(f"⚠ Error getting activity summary: {e}")
            return {
                'days': days,
                'scan_sessions': 0,
                'targets_found': 0,
                'successful_connections': 0,
                'updated_servers': 0
            }
    
    def get_authenticated_hosts(self, recent_hours: Optional[int] = None,
                               ip_filter: Optional[Iterable[str]] = None) -> List[Dict]:
        """
        Get hosts that have successful SMB authentication.

        Args:
            recent_hours: Only return hosts discovered/scanned in the last N hours
            ip_filter: Only return hosts with IP addresses in this list

        Returns:
            List of host dictionaries with authentication information
        """
        try:
            # Normalize ip_filter to list of unique non-empty strings
            if ip_filter is not None:
                ip_list = [ip.strip() for ip in ip_filter if ip and ip.strip()]
                ip_list = list(dict.fromkeys(ip_list))  # Remove duplicates while preserving order
                if not ip_list:
                    return []  # Early return for empty filter, avoid DB round-trip
            else:
                ip_list = None

            # Build query with optional time filtering
            base_query = """
                SELECT DISTINCT s.ip_address, s.country, s.auth_method, s.last_seen,
                       GROUP_CONCAT(sa.share_name) as accessible_shares
                FROM smb_servers s
                LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = 1
                WHERE s.auth_method IS NOT NULL
            """

            params = []
            if recent_hours is not None:
                base_query += " AND s.last_seen >= datetime('now', 'localtime', '-{} hours')".format(int(recent_hours))

            if ip_list is not None:
                placeholders = ",".join("?" for _ in ip_list)
                base_query += f" AND s.ip_address IN ({placeholders})"
                params.extend(ip_list)

            base_query += " GROUP BY s.ip_address, s.country, s.auth_method, s.last_seen"
            
            hosts = self.db_manager.execute_query(base_query, tuple(params))
            
            # Parse accessible_shares from comma-separated string to list
            processed_hosts = []
            for host in hosts:
                host_dict = dict(host)  # Convert Row to dict
                if host_dict['accessible_shares']:
                    host_dict['accessible_shares'] = host_dict['accessible_shares'].split(',')
                else:
                    host_dict['accessible_shares'] = []
                processed_hosts.append(host_dict)
            
            return processed_hosts
            
        except Exception as e:
            print(f"⚠ Error getting authenticated hosts: {e}")
            return []
    
    def get_recent_authenticated_hosts(self, hours: int = 24) -> List[Dict]:
        """
        Get hosts that have successful SMB authentication within the last N hours.
        
        This is a convenience method specifically for recent host filtering to avoid
        testing access on hosts that were already scanned recently.
        
        Args:
            hours: Number of hours to look back (default: 24)
        
        Returns:
            List of host dictionaries with authentication information from recent scans
        """
        return self.get_authenticated_hosts(recent_hours=hours)
    
    def get_hosts_with_accessible_shares(self) -> List[Dict]:
        """
        Get hosts that have accessible SMB shares.
        
        Returns:
            List of host dictionaries with accessible share information
        """
        try:
            hosts = self.db_manager.execute_query("""
                SELECT DISTINCT s.ip_address, s.country, s.auth_method,
                       GROUP_CONCAT(sa.share_name) as accessible_shares
                FROM smb_servers s
                INNER JOIN share_access sa ON s.id = sa.server_id 
                WHERE sa.accessible = 1
                GROUP BY s.ip_address, s.country, s.auth_method
            """)
            
            # Parse accessible_shares from comma-separated string to list
            processed_hosts = []
            for host in hosts:
                host_dict = dict(host)  # Convert Row to dict
                if host_dict['accessible_shares']:
                    host_dict['accessible_shares'] = host_dict['accessible_shares'].split(',')
                else:
                    host_dict['accessible_shares'] = []
                processed_hosts.append(host_dict)
            
            return processed_hosts
            
        except Exception as e:
            print(f"⚠ Error getting hosts with accessible shares: {e}")
            return []
    
    def get_failed_connections(self) -> List[Dict]:
        """
        Get failed connection attempts for analysis.
        
        Returns:
            List of failed connection records
        """
        try:
            failed = self.db_manager.execute_query("""
                SELECT ip_address, country, last_seen, status
                FROM smb_servers 
                WHERE status = 'failed' OR status = 'timeout'
                ORDER BY last_seen DESC
            """)
            
            return failed
            
        except Exception as e:
            print(f"⚠ Error getting failed connections: {e}")
            return []
    
    def store_share_access_result(self, session_id: int, result: Dict) -> bool:
        """
        Store share access test results to database.
        
        Args:
            session_id: Session ID from scan session
            result: Result dictionary from access testing
            
        Returns:
            True if storage successful, False otherwise
        """
        try:
            # Validate result data before processing
            ip_address = result.get('ip_address')
            if not ip_address:
                error_msg = "⚠ Warning: No IP address in result data"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                return False
            
            # Check for validation errors from access command
            if 'validation_error' in result:
                error_msg = f"⚠ Warning: Validation error for {ip_address}: {result['validation_error']}"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                return False
            
            # Perform consistency checks
            shares_found = result.get('shares_found', [])
            accessible_shares = result.get('accessible_shares', [])
            
            # Critical validation: accessible shares should not exceed total shares
            if len(accessible_shares) > len(shares_found):
                error_msg = f"⚠ Warning: Data inconsistency for {ip_address}: {len(accessible_shares)} accessible > {len(shares_found)} total shares"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                return False
            
            # Check for invalid accessible shares (not in shares_found list)
            invalid_accessible = [share for share in accessible_shares if share not in shares_found]
            if invalid_accessible:
                error_msg = f"⚠ Warning: Invalid accessible shares for {ip_address}: {invalid_accessible}"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                return False
            
            # Get server_id from smb_servers table
            server_query = "SELECT id FROM smb_servers WHERE ip_address = ?"
            servers = self.db_manager.execute_query(server_query, (ip_address,))
            
            if not servers:
                error_msg = f"⚠ Warning: Server {ip_address} not found in database"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                return False
            
            server_id = servers[0]['id']
            
            # Store each share access result
            share_details = result.get('share_details', [])
            if not share_details:
                # Handle legacy format with accessible_shares list
                accessible_shares = result.get('accessible_shares', [])
                shares_found = result.get('shares_found', [])
                
                # Create share_details from legacy format
                share_details = []
                for share in shares_found:
                    share_details.append({
                        'share_name': share,
                        'accessible': share in accessible_shares,
                        'error': None if share in accessible_shares else 'Access denied'
                    })
            
            # DELETE existing shares for this server to prevent duplicates
            # This implements "update on rescan" behavior - only keep most recent data
            delete_query = "DELETE FROM share_access WHERE server_id = ?"
            try:
                self.db_manager.execute_query(delete_query, (server_id,))
            except Exception as e:
                error_msg = f"⚠ Warning: Failed to clear existing shares for {ip_address}: {e}"
                if hasattr(self, '_verbose') and self._verbose:
                    print(error_msg)
                # Continue anyway - let INSERT handle potential duplicates
            
            # Store each share result with current session
            stored_shares = 0
            for share_detail in share_details:
                share_name = share_detail.get('share_name')
                accessible = share_detail.get('accessible', False)
                error_message = share_detail.get('error')
                auth_status = share_detail.get('auth_status')
                
                if share_name:
                    try:
                        self.dal.add_share_access(
                            server_id=server_id,
                            session_id=session_id,
                            share_name=share_name,
                            accessible=accessible,
                            error_message=error_message,
                            auth_status=auth_status
                        )
                        stored_shares += 1
                    except Exception as e:
                        error_msg = f"⚠ Warning: Failed to store share {share_name} for {ip_address}: {e}"
                        if hasattr(self, '_verbose') and self._verbose:
                            print(error_msg)
                        continue
            
            # Previously logged stored share count here when verbose; suppressed to reduce noise.
            
            return True
            
        except Exception as e:
            error_msg = f"⚠ Error storing share access results for {result.get('ip_address', 'unknown')}: {e}"
            if hasattr(self, '_verbose') and self._verbose:
                print(error_msg)
            return False
    
    def get_all_discovered_shares_per_host(self) -> List[Dict]:
        """
        Get all discovered shares per host (both accessible and non-accessible).
        
        Returns:
            List of host dictionaries with all discovered share information
        """
        try:
            hosts = self.db_manager.execute_query("""
                SELECT DISTINCT s.ip_address, s.country, s.auth_method,
                       GROUP_CONCAT(sa.share_name) as all_shares
                FROM smb_servers s
                INNER JOIN share_access sa ON s.id = sa.server_id 
                GROUP BY s.ip_address, s.country, s.auth_method
                ORDER BY s.last_seen DESC
            """)
            
            # Parse all_shares from comma-separated string to list
            processed_hosts = []
            for host in hosts:
                host_dict = dict(host)  # Convert Row to dict
                if host_dict['all_shares']:
                    host_dict['all_shares'] = host_dict['all_shares'].split(',')
                else:
                    host_dict['all_shares'] = []
                processed_hosts.append(host_dict)
            
            return processed_hosts
            
        except Exception as e:
            print(f"⚠ Error getting all discovered shares per host: {e}")
            return []
    
    def get_complete_share_summary(self) -> List[Dict]:
        """
        Get complete share summary with total counts and accessible counts per host.
        Uses the v_host_share_summary view for optimized performance.
        
        Returns:
            List of host dictionaries with complete share statistics
        """
        try:
            # First check if the view exists
            view_check = self.db_manager.execute_query("""
                SELECT name FROM sqlite_master 
                WHERE type='view' AND name='v_host_share_summary'
            """)
            
            if not view_check:
                # Fall back to manual query if view doesn't exist
                if hasattr(self, '_verbose') and self._verbose:
                    print("⚠ v_host_share_summary view not found, using fallback query")
                return self._get_complete_share_summary_fallback()
            
            # Use the optimized view
            hosts = self.db_manager.execute_query("""
                SELECT ip_address, country, auth_method, first_seen, last_seen,
                       total_shares_discovered, accessible_shares_count,
                       all_shares_list, accessible_shares_list, last_share_test
                FROM v_host_share_summary
                ORDER BY last_seen DESC
            """)
            
            # Process the results
            processed_hosts = []
            for host in hosts:
                host_dict = dict(host)  # Convert Row to dict
                
                # Parse share lists from comma-separated strings
                if host_dict['all_shares_list']:
                    host_dict['all_shares_list'] = [s.strip() for s in host_dict['all_shares_list'].split(',') if s.strip()]
                else:
                    host_dict['all_shares_list'] = []
                
                if host_dict['accessible_shares_list']:
                    host_dict['accessible_shares_list'] = [s.strip() for s in host_dict['accessible_shares_list'].split(',') if s.strip()]
                else:
                    host_dict['accessible_shares_list'] = []
                
                processed_hosts.append(host_dict)
            
            return processed_hosts
            
        except Exception as e:
            print(f"⚠ Error getting complete share summary: {e}")
            return []
    
    def _get_complete_share_summary_fallback(self) -> List[Dict]:
        """
        Fallback method for complete share summary when view is not available.
        
        Returns:
            List of host dictionaries with complete share statistics
        """
        try:
            hosts = self.db_manager.execute_query("""
                SELECT s.ip_address, s.country, s.auth_method, s.first_seen, s.last_seen,
                       COUNT(sa.share_name) as total_shares_discovered,
                       SUM(CASE WHEN sa.accessible = 1 THEN 1 ELSE 0 END) as accessible_shares_count,
                       GROUP_CONCAT(sa.share_name) as all_shares_list,
                       GROUP_CONCAT(CASE WHEN sa.accessible = 1 THEN sa.share_name END) as accessible_shares_list,
                       MAX(sa.test_timestamp) as last_share_test
                FROM smb_servers s
                INNER JOIN share_access sa ON s.id = sa.server_id
                GROUP BY s.ip_address, s.country, s.auth_method, s.first_seen, s.last_seen
                ORDER BY s.last_seen DESC
            """)
            
            # Process the results
            processed_hosts = []
            for host in hosts:
                host_dict = dict(host)  # Convert Row to dict
                
                # Parse share lists from comma-separated strings
                if host_dict['all_shares_list']:
                    host_dict['all_shares_list'] = [s.strip() for s in host_dict['all_shares_list'].split(',') if s.strip()]
                else:
                    host_dict['all_shares_list'] = []
                
                if host_dict['accessible_shares_list']:
                    host_dict['accessible_shares_list'] = [s.strip() for s in host_dict['accessible_shares_list'].split(',') if s.strip()]
                else:
                    host_dict['accessible_shares_list'] = []
                
                processed_hosts.append(host_dict)
            
            return processed_hosts
            
        except Exception as e:
            print(f"⚠ Error in fallback complete share summary query: {e}")
            return []

    def upsert_rce_status(self, ip_address: str, rce_status: str,
                          verdict_summary: Optional[str] = None) -> None:
        """
        Update RCE analysis status for a host.

        Args:
            ip_address: IP address of the host
            rce_status: Status string ('not_run', 'clean', 'flagged', 'unknown', 'error')
            verdict_summary: Optional JSON summary of verdicts
        """
        if not ip_address:
            return

        valid_statuses = {'not_run', 'clean', 'flagged', 'unknown', 'error'}
        if rce_status not in valid_statuses:
            rce_status = 'unknown'

        try:
            rows = self.db_manager.execute_query(
                "SELECT id FROM smb_servers WHERE ip_address = ?",
                (ip_address,)
            )
            if not rows:
                return

            server_id = rows[0]['id']
            self.db_manager.execute_query(
                """
                INSERT INTO host_probe_cache (server_id, rce_status, rce_verdict_summary, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    rce_status=excluded.rce_status,
                    rce_verdict_summary=excluded.rce_verdict_summary,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, rce_status, verdict_summary)
            )
        except Exception as e:
            if self._verbose:
                print(f"Warning: Failed to persist RCE status for {ip_address}: {e}")

    def close(self):
        """Close database connections."""
        if hasattr(self, 'db_manager'):
            self.db_manager.close()


def create_workflow_database(config, verbose=False) -> SMBSeekWorkflowDatabase:
    """
    Create and initialize workflow database manager.
    
    Args:
        config: SMBSeekConfig instance
        verbose: Enable verbose database operations
        
    Returns:
        SMBSeekWorkflowDatabase instance
    """
    return SMBSeekWorkflowDatabase(config, verbose)
