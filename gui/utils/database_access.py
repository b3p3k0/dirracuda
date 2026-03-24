"""
SMBSeek Database Access Layer

Provides read-only access to the SQLite database with connection management,
caching, and thread-safe operations. Designed for GUI dashboard updates
and data browsing without interfering with backend operations.

Design Decision: Read-only access prevents any interference with backend
database operations while providing real-time dashboard updates.
"""

import sqlite3
import threading
import time
import json
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta
try:
    from error_codes import get_error, format_error_message
except ImportError:
    from .error_codes import get_error, format_error_message


class DatabaseReader:
    """
    Read-only database access for SMBSeek GUI.
    
    Provides efficient, thread-safe access to the SMBSeek database with
    connection pooling, retry logic, and caching for dashboard updates.
    
    Design Pattern: Read-only with connection management to handle
    database locks when backend is writing during scans.
    """
    
    def __init__(self, db_path: str = "../backend/smbseek.db", cache_duration: int = 5):
        """
        Initialize database reader.
        
        Args:
            db_path: Path to SQLite database file
            cache_duration: Cache duration in seconds for dashboard queries
            
        Design Decision: Short cache duration balances real-time updates
        with performance during dashboard refreshes.
        """
        self.db_path = Path(db_path).resolve()
        self.cache_duration = cache_duration
        self.cache = {}
        self.cache_timestamps = {}
        self.connection_lock = threading.Lock()

        try:
            from shared.db_migrations import run_migrations
            run_migrations(str(self.db_path))
        except Exception:
            pass

        # Ensure new RCE columns exist even on older databases (idempotent)
        try:
            self._ensure_rce_columns()
        except Exception:
            pass
        
        # Mock mode for testing
        self.mock_mode = False
        self.mock_data = self._get_mock_data()
        
        # Don't validate during initialization - let caller handle validation
        # self._validate_database()

    def _ensure_rce_columns(self) -> None:
        """
        Best-effort migration to add RCE columns if missing.

        Mirrors shared.db_migrations but runs here to protect GUI users who
        open older databases without running CLI migrations first.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("PRAGMA table_info(host_probe_cache)")
            columns = [row[1] for row in cur.fetchall()]

            altered = False
            if "rce_status" not in columns:
                conn.execute("ALTER TABLE host_probe_cache ADD COLUMN rce_status TEXT DEFAULT 'not_run'")
                altered = True
            if "rce_verdict_summary" not in columns:
                conn.execute("ALTER TABLE host_probe_cache ADD COLUMN rce_verdict_summary TEXT")
                altered = True

            if altered:
                conn.commit()
    
    def get_smbseek_schema_definition(self) -> Dict[str, Any]:
        """
        Get comprehensive SMBSeek database schema definition.
        
        Returns:
            Dictionary with schema definition including core and optional tables
        """
        return {
            'core_tables': {
                'smb_servers': 'Central SMB server registry with discovery metadata',
                'scan_sessions': 'Scan session tracking and audit trail'
            },
            'data_tables': {
                'share_access': 'SMB share accessibility results and permissions',
                'file_manifests': 'File discovery and manifest records',
                'vulnerabilities': 'Security vulnerability findings',
                'failure_logs': 'Connection failure logs and analysis'
            },
            'system_tables': {
                'sqlite_sequence': 'SQLite auto-increment sequence tracking'
            },
            'views': {
                'v_active_servers': 'Active servers with aggregated metrics',
                'v_vulnerability_summary': 'Vulnerability summary by type and severity',
                'v_scan_statistics': 'Scan statistics and success rates'
            },
            'minimum_required': ['smb_servers', 'scan_sessions'],
            'recommended': ['smb_servers', 'scan_sessions', 'share_access']
        }
    
    def analyze_database_schema(self, db_path: str) -> Dict[str, Any]:
        """
        Analyze database schema and compare to SMBSeek expectations.
        
        Args:
            db_path: Path to database file
            
        Returns:
            Comprehensive analysis of database schema compatibility
        """
        analysis = {
            'path': db_path,
            'valid': False,
            'schema_info': {},
            'tables_found': [],
            'tables_missing': [],
            'unexpected_tables': [],
            'record_counts': {},
            'compatibility_level': 'none',  # none, partial, full
            'import_recommendation': '',
            'warnings': [],
            'errors': []
        }
        
        try:
            # Check if file exists first
            if not Path(db_path).exists():
                error_info = get_error("DB001", {"path": db_path})
                analysis['errors'].append(error_info['full_message'])
                return analysis
                
            schema_def = self.get_smbseek_schema_definition()
            expected_tables = set()
            expected_tables.update(schema_def['core_tables'].keys())
            expected_tables.update(schema_def['data_tables'].keys())
            
            with sqlite3.connect(db_path, timeout=10) as conn:
                # Get all tables
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """)
                actual_tables = set(row[0] for row in cursor.fetchall())
                analysis['tables_found'] = list(actual_tables)
                
                # Analyze table compatibility
                core_tables_present = set(schema_def['core_tables'].keys()) & actual_tables
                data_tables_present = set(schema_def['data_tables'].keys()) & actual_tables
                
                analysis['tables_missing'] = list(expected_tables - actual_tables)
                analysis['unexpected_tables'] = list(actual_tables - expected_tables)
                
                # Get record counts for known tables
                for table in actual_tables:
                    try:
                        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                        analysis['record_counts'][table] = cursor.fetchone()[0]
                    except Exception as e:
                        analysis['warnings'].append(f"Could not count records in {table}: {e}")
                
                # Determine compatibility level
                if len(core_tables_present) >= 2:  # At least 2 core tables
                    if len(actual_tables & expected_tables) == len(expected_tables):
                        analysis['compatibility_level'] = 'full'
                        analysis['import_recommendation'] = 'Full SMBSeek database - ready for import'
                    elif len(core_tables_present) == len(schema_def['core_tables']):
                        analysis['compatibility_level'] = 'partial'
                        analysis['import_recommendation'] = 'Partial SMBSeek database - core data available'
                        if len(data_tables_present) > 0:
                            analysis['import_recommendation'] += f' with {len(data_tables_present)} additional data tables'
                    else:
                        analysis['compatibility_level'] = 'minimal'
                        analysis['import_recommendation'] = 'Basic SMBSeek database - limited functionality'
                    
                    analysis['valid'] = True
                else:
                    analysis['compatibility_level'] = 'none'
                    analysis['import_recommendation'] = 'Not a compatible SMBSeek database'
                    error_info = get_error("VAL001", {"tables_found": list(core_tables_present)})
                    analysis['errors'].append(error_info['full_message'])
                
                # Add specific warnings
                if analysis['tables_missing']:
                    analysis['warnings'].append(f"Missing expected tables: {analysis['tables_missing']}")
                if analysis['unexpected_tables']:
                    analysis['warnings'].append(f"Unexpected tables found: {analysis['unexpected_tables']}")
                
                analysis['schema_info'] = {
                    'core_tables_present': list(core_tables_present),
                    'data_tables_present': list(data_tables_present),
                    'total_tables': len(actual_tables),
                    'total_records': sum(analysis['record_counts'].values())
                }
                
        except Exception as e:
            error_info = get_error("DB011", {"error": str(e)})
            analysis['errors'].append(error_info['full_message'])
        
        return analysis
    
    def validate_database(self, db_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Validate database exists and is accessible (legacy method for backward compatibility).
        
        Args:
            db_path: Optional path to validate (defaults to self.db_path)
            
        Returns:
            Dictionary with validation results (simplified format for compatibility)
        """
        path_to_validate = db_path or str(self.db_path)
        
        # Use comprehensive analysis but return simplified result for compatibility
        analysis = self.analyze_database_schema(path_to_validate)
        
        # Convert comprehensive analysis to legacy format
        result = {
            'valid': analysis['valid'],
            'path': analysis['path'],
            'exists': len(analysis['errors']) == 0 or 'DB001' not in str(analysis['errors']),
            'readable': len(analysis['errors']) == 0 or 'access error' not in str(analysis['errors']).lower(),
            'has_tables': len(analysis['tables_found']) > 0,
            'error': analysis['errors'][0] if analysis['errors'] else None
        }
        
        # Add file existence check for legacy compatibility
        if not Path(path_to_validate).exists():
            result['exists'] = False
            error_info = get_error("DB001", {"path": path_to_validate})
            result['error'] = error_info['full_message']
        
        return result
    
    def set_database_path(self, new_path: str) -> bool:
        """
        Update database path after validation.
        
        Args:
            new_path: New database path
            
        Returns:
            True if path set successfully
        """
        try:
            self.db_path = Path(new_path).resolve()
            return True
        except Exception:
            return False
    
    def enable_mock_mode(self) -> None:
        """
        Enable mock mode for testing without real database.
        
        Design Decision: Mock mode allows GUI testing when database
        doesn't exist or contains no test data.
        """
        self.mock_mode = True
    
    def disable_mock_mode(self) -> None:
        """Disable mock mode and use real database."""
        self.mock_mode = False
    
    @contextmanager
    def _get_connection(self, timeout: int = 30):
        """
        Get database connection with timeout and retry logic.
        
        Args:
            timeout: Connection timeout in seconds
            
        Yields:
            SQLite connection object
            
        Design Decision: Timeout and retry logic handles database locks
        when backend is writing during active scans.
        """
        with self.connection_lock:
            conn = None
            try:
                conn = sqlite3.connect(
                    self.db_path,
                    timeout=timeout,
                    check_same_thread=False
                )
                conn.row_factory = sqlite3.Row  # Dict-like access
                yield conn
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    # Database is locked, likely backend is writing
                    time.sleep(1)
                    # Try once more with shorter timeout
                    try:
                        conn = sqlite3.connect(self.db_path, timeout=5)
                        conn.row_factory = sqlite3.Row
                        yield conn
                    except sqlite3.OperationalError:
                        raise sqlite3.OperationalError(
                            "Database is locked by backend operation. "
                            "Try again in a moment."
                        )
                else:
                    raise
            finally:
                if conn:
                    conn.close()
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Get key metrics for dashboard display.
        
        Returns:
            Dictionary with dashboard metrics:
            - total_servers: Total SMB servers in database
            - accessible_shares: Total accessible shares
            - high_risk_vulnerabilities: Count of high/critical vulnerabilities
            - recent_discoveries: Servers discovered in most recent completed scan session
            
        Design Decision: Single query optimized for dashboard performance
        with caching to reduce database load during frequent updates.
        """
        # Include database modification time in cache key for automatic invalidation
        cache_key = f"dashboard_summary_{self._get_db_modified_time()}"
        
        if self._is_cached(cache_key):
            return self.cache[cache_key]
        
        if self.mock_mode:
            summary = {
                "total_servers": 7,
                "accessible_shares": 17,
                "servers_with_accessible_shares": 5,
                "total_shares": 23,
                "high_risk_vulnerabilities": 3,
                "recent_discoveries": {
                    "discovered": 4,
                    "accessible": 2,
                    "display": "4 / 2"
                },
                "last_scan": "2025-01-21T14:20:00",
                "database_size_mb": 2.3
            }
        else:
            summary = self._query_dashboard_summary()
        
        self._cache_result(cache_key, summary)
        return summary
    
    def get_dashboard_stats(self) -> Dict[str, Any]:
        """
        Alias for get_dashboard_summary() for backward compatibility.
        
        Returns:
            Dictionary with dashboard metrics (same as get_dashboard_summary)
        """
        return self.get_dashboard_summary()
    
    def _query_dashboard_summary(self) -> Dict[str, Any]:
        """Execute dashboard summary query."""
        with self._get_connection() as conn:
            # Enhanced query - includes servers with accessible shares and total shares count
            basic_query = """
            SELECT
                (SELECT COUNT(*) FROM smb_servers WHERE status = 'active') as total_servers,
                (SELECT COUNT(DISTINCT CONCAT(server_id, '|', share_name))
                 FROM share_access WHERE accessible = 1) as accessible_shares,
                (SELECT COUNT(DISTINCT server_id) FROM share_access WHERE accessible = 1) as servers_with_accessible_shares,
                (SELECT COUNT(DISTINCT CONCAT(server_id, '|', share_name)) FROM share_access) as total_shares,
                (SELECT COUNT(*) FROM vulnerabilities
                 WHERE severity IN ('high', 'critical') AND status = 'open') as high_risk_vulnerabilities
            """

            result = conn.execute(basic_query).fetchone()
            
            # Get recent discoveries from most recent completed scan session
            recent_discoveries_query = """
            SELECT 
                ss.successful_targets as servers_discovered,
                COUNT(DISTINCT CASE WHEN sa.accessible = 1 THEN CONCAT(sa.server_id, '|', sa.share_name) END) as shares_accessible
            FROM scan_sessions ss
            LEFT JOIN share_access sa ON sa.session_id = ss.id
            WHERE ss.status = 'completed' AND ss.successful_targets > 0
              AND ss.timestamp = (
                  SELECT MAX(timestamp) 
                  FROM scan_sessions 
                  WHERE status = 'completed' AND successful_targets > 0
              )
            GROUP BY ss.id, ss.successful_targets
            """
            recent_result = conn.execute(recent_discoveries_query).fetchone()
            
            # Get last scan time
            last_scan_query = "SELECT MAX(last_seen) as last_scan FROM smb_servers"
            last_scan_result = conn.execute(last_scan_query).fetchone()
            
            # Get database size (approximate)
            size_query = "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
            size_result = conn.execute(size_query).fetchone()
            
            # Format recent discoveries data
            if recent_result:
                discovered = recent_result["servers_discovered"] or 0
                accessible = recent_result["shares_accessible"] or 0
                recent_discoveries = {
                    "discovered": discovered,
                    "accessible": accessible,
                    "display": f"{discovered} / {accessible}"
                }
            else:
                recent_discoveries = {
                    "discovered": 0,
                    "accessible": 0,
                    "display": "--"
                }
            
            return {
                "total_servers": result["total_servers"] or 0,
                "accessible_shares": result["accessible_shares"] or 0,
                "servers_with_accessible_shares": result["servers_with_accessible_shares"] or 0,
                "total_shares": result["total_shares"] or 0,
                "high_risk_vulnerabilities": result["high_risk_vulnerabilities"] or 0,
                "recent_discoveries": recent_discoveries,
                "last_scan": last_scan_result["last_scan"] or "Never",
                "database_size_mb": round((size_result["size"] or 0) / (1024 * 1024), 1)
            }
    
    def get_top_findings(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get top security findings for dashboard display.
        
        Args:
            limit: Maximum number of findings to return
            
        Returns:
            List of finding dictionaries with IP, country, vulnerability summary
            
        Design Decision: Pre-prioritized query returns most critical findings
        for immediate security attention.
        """
        cache_key = f"top_findings_{limit}_{self._get_db_modified_time()}"
        
        if self._is_cached(cache_key):
            return self.cache[cache_key]
        
        if self.mock_mode:
            findings = [
                {
                    "ip_address": "192.168.1.45",
                    "country": "US",
                    "auth_method": "Anonymous",
                    "accessible_shares": 7,
                    "severity": "critical",
                    "summary": "7 open shares, possible ransomware risk"
                },
                {
                    "ip_address": "10.0.0.123",
                    "country": "GB", 
                    "auth_method": "Guest/Blank",
                    "accessible_shares": 3,
                    "severity": "medium",
                    "summary": "Anonymous access to SYSVOL"
                },
                {
                    "ip_address": "172.16.5.78",
                    "country": "CA",
                    "auth_method": "Guest/Guest",
                    "accessible_shares": 1,
                    "severity": "low",
                    "summary": "Weak authentication, 1 accessible file"
                }
            ][:limit]
        else:
            findings = self._query_top_findings(limit)
        
        self._cache_result(cache_key, findings)
        return findings
    
    def _query_top_findings(self, limit: int) -> List[Dict[str, Any]]:
        """Execute top findings query."""
        with self._get_connection() as conn:
            # Fixed query - use subquery to prevent share count multiplication
            query = """
            SELECT 
                s.ip_address,
                s.country,
                s.auth_method,
                COALESCE(sa_summary.accessible_shares, 0) as accessible_shares,
                v.severity,
                COALESCE(v.title, CONCAT(COALESCE(sa_summary.accessible_shares, 0), ' accessible shares')) as summary
            FROM smb_servers s
            LEFT JOIN (
                SELECT 
                    server_id,
                    COUNT(CASE WHEN accessible = 1 THEN 1 END) as accessible_shares
                FROM share_access
                GROUP BY server_id
            ) sa_summary ON s.id = sa_summary.server_id
            LEFT JOIN vulnerabilities v ON s.id = v.server_id AND v.status = 'open'
            WHERE s.status = 'active'
            ORDER BY 
                CASE COALESCE(v.severity, 'none')
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                    ELSE 5
                END,
                accessible_shares DESC
            LIMIT ?
            """
            
            results = conn.execute(query, (limit,)).fetchall()
            
            return [
                {
                    "ip_address": row["ip_address"],
                    "country": row["country"] or "Unknown",
                    "auth_method": row["auth_method"] or "Unknown",
                    "accessible_shares": row["accessible_shares"] or 0,
                    "severity": row["severity"] or "unknown",
                    "summary": row["summary"] or f"{row['accessible_shares']} accessible shares"
                }
                for row in results
            ]
    
    def get_country_breakdown(self) -> Dict[str, int]:
        """
        Get server count by country for geographic breakdown.
        
        Returns:
            Dictionary mapping country codes to server counts
        """
        cache_key = f"country_breakdown_{self._get_db_modified_time()}"
        
        if self._is_cached(cache_key):
            return self.cache[cache_key]
        
        if self.mock_mode:
            breakdown = {
                "US": 4,
                "GB": 2,
                "CA": 1
            }
        else:
            breakdown = self._query_country_breakdown()
        
        self._cache_result(cache_key, breakdown)
        return breakdown
    
    def _query_country_breakdown(self) -> Dict[str, int]:
        """Execute country breakdown query."""
        with self._get_connection() as conn:
            query = """
            SELECT country_code, COUNT(*) as count
            FROM smb_servers 
            WHERE status = 'active' AND country_code IS NOT NULL
            GROUP BY country_code
            ORDER BY count DESC
            """
            
            results = conn.execute(query).fetchall()
            return {row["country_code"]: row["count"] for row in results}
    
    def get_recent_activity(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get recent scanning activity for activity timeline.
        
        Args:
            days: Number of days to look back
            
        Returns:
            List of activity records with timestamps and counts
        """
        cache_key = f"recent_activity_{days}_{self._get_db_modified_time()}"
        
        if self._is_cached(cache_key):
            return self.cache[cache_key]
        
        if self.mock_mode:
            activity = [
                {"date": "2025-01-21", "discoveries": 4, "scans": 2},
                {"date": "2025-01-20", "discoveries": 1, "scans": 1},
                {"date": "2025-01-19", "discoveries": 2, "scans": 1}
            ]
        else:
            activity = self._query_recent_activity(days)
        
        self._cache_result(cache_key, activity)
        return activity
    
    def _query_recent_activity(self, days: int) -> List[Dict[str, Any]]:
        """Execute recent activity query."""
        with self._get_connection() as conn:
            query = """
            SELECT 
                DATE(last_seen) as date,
                COUNT(*) as discoveries,
                COUNT(DISTINCT DATE(last_seen)) as scans
            FROM smb_servers 
            WHERE last_seen >= datetime('now', '-{} days')
            GROUP BY DATE(last_seen)
            ORDER BY date DESC
            """.format(days)
            
            results = conn.execute(query).fetchall()
            
            return [
                {
                    "date": row["date"],
                    "discoveries": row["discoveries"],
                    "scans": row["scans"]
                }
                for row in results
            ]
    
    def get_server_list(self, limit: Optional[int] = 100, offset: int = 0, 
                       country_filter: Optional[str] = None,
                       recent_scan_only: bool = False) -> Tuple[List[Dict], int]:
        """
        Get paginated server list for drill-down windows.
        
        Args:
            limit: Maximum servers to return
            offset: Offset for pagination
            country_filter: Optional country code filter
            recent_scan_only: If True, filter to servers from most recent scan session
            
        Returns:
            Tuple of (server_list, total_count)
        """
        if self.mock_mode:
            servers = self.mock_data["servers"]
            if country_filter:
                servers = [s for s in servers if s["country_code"] == country_filter]
            if recent_scan_only:
                # In mock mode, just return first few servers to simulate recent scan
                servers = servers[:4]  # Mock recent scan with 4 servers
            
            total = len(servers)
            paginated = servers[offset:offset + limit]
            return paginated, total
        
        return self._query_server_list(limit, offset, country_filter, recent_scan_only)
    
    def _query_server_list(self, limit: Optional[int], offset: int, 
                          country_filter: Optional[str],
                          recent_scan_only: bool = False) -> Tuple[List[Dict], int]:
        """Execute server list query with enhanced share tracking data."""
        with self._get_connection() as conn:
            # Check if enhanced view exists, fall back to legacy query if not
            view_exists_query = """
            SELECT name FROM sqlite_master 
            WHERE type='view' AND name='v_host_share_summary'
            """
            view_exists = conn.execute(view_exists_query).fetchone() is not None
            
            if view_exists:
                return self._query_server_list_enhanced(conn, limit, offset, country_filter, recent_scan_only)
            else:
                return self._query_server_list_legacy(conn, limit, offset, country_filter, recent_scan_only)
    
    def _query_server_list_enhanced(self, conn: sqlite3.Connection, limit: Optional[int], offset: int,
                                   country_filter: Optional[str], recent_scan_only: bool) -> Tuple[List[Dict], int]:
        """Execute enhanced server list query using v_host_share_summary view."""
        # Base query using enhanced view
        where_clause = "WHERE 1=1"
        params = []
        
        if country_filter:
            where_clause += " AND country_code = ?"
            params.append(country_filter)
        
        # Filter for recent scan only
        if recent_scan_only:
            # Get the most recent server timestamp (indicates most recent scan activity)
            recent_timestamp_query = """
            SELECT MAX(datetime(last_seen)) as recent_timestamp
            FROM v_host_share_summary
            """
            timestamp_result = conn.execute(recent_timestamp_query).fetchone()
            if timestamp_result and timestamp_result["recent_timestamp"]:
                recent_time = timestamp_result["recent_timestamp"]
                # Filter servers seen within 1 hour of the most recent activity
                where_clause += " AND datetime(last_seen) >= datetime(?, '-1 hour')"
                params.append(recent_time)

        # Count query
        count_query = f"""
        SELECT COUNT(*) as total
        FROM v_host_share_summary
        {where_clause}
        """
        
        total_count = conn.execute(count_query, params).fetchone()["total"]
        
        # Enhanced data query using the new view
        data_query = f"""
        SELECT 
            ip_address,
            country,
            country_code,
            auth_method,
            last_seen,
            scan_count,
            total_shares_discovered,
            accessible_shares_count,
            accessible_shares_list,
            access_rate_percent
        FROM v_host_share_summary
        {where_clause}
        ORDER BY datetime(last_seen) DESC
        """
        data_params = list(params)
        if limit is not None and limit > 0:
            data_query += " LIMIT ? OFFSET ?"
            data_params.extend([limit, offset])
        results = conn.execute(data_query, data_params).fetchall()

        flags_map = self._load_user_flags_map(conn)
        probe_map = self._load_probe_cache_map(conn)

        servers = []
        for row in results:
            ip = row["ip_address"]
            flags = flags_map.get(ip, {})
            probe = probe_map.get(ip, {})
            servers.append({
                "ip_address": ip,
                "country": row["country"],
                "country_code": row["country_code"],
                "auth_method": row["auth_method"],
                "last_seen": row["last_seen"],
                "scan_count": row["scan_count"],
                "total_shares": row["total_shares_discovered"],
                "accessible_shares": row["accessible_shares_count"],
                "accessible_shares_list": row["accessible_shares_list"] or "",
                "access_rate_percent": row["access_rate_percent"],
                "favorite": flags.get("favorite", 0),
                "avoid": flags.get("avoid", 0),
                "notes": flags.get("notes", ""),
                "probe_status": probe.get("status", "unprobed"),
                "indicator_matches": probe.get("indicator_matches", 0),
                "extracted": probe.get("extracted", 0),
                "rce_status": probe.get("rce_status", "not_run"),
                # Include vulnerabilities as 0 for backward compatibility
                "vulnerabilities": 0
            })

        return servers, total_count
    
    def _query_server_list_legacy(self, conn: sqlite3.Connection, limit: Optional[int], offset: int,
                                 country_filter: Optional[str], recent_scan_only: bool) -> Tuple[List[Dict], int]:
        """Execute legacy server list query for backward compatibility."""
        # Base query
        where_clause = "WHERE s.status = 'active'"
        params = []
        
        if country_filter:
            where_clause += " AND s.country_code = ?"
            params.append(country_filter)
        
        # Filter for recent scan only
        if recent_scan_only:
            # Get the most recent server timestamp (indicates most recent scan activity)
            recent_timestamp_query = """
            SELECT MAX(datetime(last_seen)) as recent_timestamp
            FROM smb_servers
            WHERE status = 'active'
            """
            timestamp_result = conn.execute(recent_timestamp_query).fetchone()
            if timestamp_result and timestamp_result["recent_timestamp"]:
                recent_time = timestamp_result["recent_timestamp"]
                # Filter servers seen within 1 hour of the most recent activity
                # This captures servers from the most recent scanning session
                where_clause += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                params.append(recent_time)
        
        # Count query
        count_query = f"""
        SELECT COUNT(*) as total
        FROM smb_servers s
        {where_clause}
        """
        
        total_count = conn.execute(count_query, params).fetchone()["total"]
        
        # Enhanced legacy query - includes comma-separated share list generation
        data_query = f"""
        SELECT 
            s.ip_address,
            s.country,
            s.country_code,
            s.auth_method,
            s.last_seen,
            s.scan_count,
            COALESCE(sa_summary.total_shares, 0) as total_shares,
            COALESCE(sa_summary.accessible_shares, 0) as accessible_shares,
            COALESCE(sa_summary.accessible_shares_list, '') as accessible_shares_list,
            COALESCE(v_summary.vulnerabilities, 0) as vulnerabilities
        FROM smb_servers s
        LEFT JOIN (
            SELECT 
                server_id,
                COUNT(share_name) as total_shares,
                COUNT(CASE WHEN accessible = 1 THEN 1 END) as accessible_shares,
                GROUP_CONCAT(
                    CASE WHEN accessible = 1 THEN share_name END, 
                    ','
                ) as accessible_shares_list
            FROM share_access
            GROUP BY server_id
        ) sa_summary ON s.id = sa_summary.server_id
        LEFT JOIN (
            SELECT server_id, COUNT(*) as vulnerabilities
            FROM vulnerabilities 
            WHERE status = 'open'
            GROUP BY server_id
        ) v_summary ON s.id = v_summary.server_id
        {where_clause}
        ORDER BY datetime(s.last_seen) DESC
        """
        
        data_params = list(params)
        if limit is not None and limit > 0:
            data_query += " LIMIT ? OFFSET ?"
            data_params.extend([limit, offset])
        results = conn.execute(data_query, data_params).fetchall()
        
        flags_map = self._load_user_flags_map(conn)
        probe_map = self._load_probe_cache_map(conn)

        servers = []
        for row in results:
            ip = row["ip_address"]
            flags = flags_map.get(ip, {})
            probe = probe_map.get(ip, {})
            servers.append({
                "ip_address": ip,
                "country": row["country"],
                "country_code": row["country_code"],
                "auth_method": row["auth_method"],
                "last_seen": row["last_seen"],
                "scan_count": row["scan_count"],
                "total_shares": row["total_shares"],
                "accessible_shares": row["accessible_shares"],
                "accessible_shares_list": row["accessible_shares_list"] or "",
                "vulnerabilities": row["vulnerabilities"],
                "favorite": flags.get("favorite", 0),
                "avoid": flags.get("avoid", 0),
                "notes": flags.get("notes", ""),
                "probe_status": probe.get("status", "unprobed"),
                "indicator_matches": probe.get("indicator_matches", 0),
                "extracted": probe.get("extracted", 0),
                "rce_status": probe.get("rce_status", "not_run"),
            })

        return servers, total_count

    def _load_user_flags_map(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        query = """
        SELECT s.ip_address, f.favorite, f.avoid, f.notes
        FROM host_user_flags f
        JOIN smb_servers s ON s.id = f.server_id
        """
        rows = conn.execute(query).fetchall()
        return {
            row["ip_address"]: {
                "favorite": row["favorite"] or 0,
                "avoid": row["avoid"] or 0,
                "notes": row["notes"] or "",
            }
            for row in rows
        }

    def _load_probe_cache_map(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        query = """
        SELECT s.ip_address, pc.status, pc.indicator_matches, pc.extracted, pc.rce_status
        FROM host_probe_cache pc
        JOIN smb_servers s ON s.id = pc.server_id
        """
        rows = conn.execute(query).fetchall()
        return {
            row["ip_address"]: {
                "status": row["status"] or "unprobed",
                "indicator_matches": row["indicator_matches"] or 0,
                "extracted": row["extracted"] or 0,
                "rce_status": row["rce_status"] or "not_run",
            }
            for row in rows
        }
    
    def _is_cached(self, key: str) -> bool:
        """Check if data is cached and still valid."""
        if key not in self.cache:
            return False
        
        timestamp = self.cache_timestamps.get(key, 0)
        return (time.time() - timestamp) < self.cache_duration
    
    def _cache_result(self, key: str, data: Any) -> None:
        """Cache query result with timestamp."""
        self.cache[key] = data
        self.cache_timestamps[key] = time.time()
    
    def _get_db_modified_time(self) -> int:
        """
        Get database last modification time for cache invalidation.
        
        Returns:
            Database modification time as integer timestamp
        """
        try:
            import os
            return int(os.path.getmtime(self.db_path))
        except:
            return int(time.time())
    
    def clear_cache(self) -> None:
        """Clear all cached data."""
        self.cache.clear()
        self.cache_timestamps.clear()

    # --- Write helpers for GUI flags/probe cache -------------------------

    def upsert_user_flags(self, ip_address: str, *, favorite: Optional[bool] = None,
                          avoid: Optional[bool] = None, notes: Optional[str] = None) -> None:
        """SMB-compatible shim. Delegates to upsert_user_flags_for_host with host_type='S'."""
        self.upsert_user_flags_for_host(ip_address, 'S', favorite=favorite, avoid=avoid, notes=notes)

    def upsert_probe_cache(self, ip_address: str, *, status: str, indicator_matches: int,
                           snapshot_path: Optional[str] = None) -> None:
        """SMB-compatible shim. Delegates to upsert_probe_cache_for_host with host_type='S'."""
        self.upsert_probe_cache_for_host(ip_address, 'S', status=status,
                                         indicator_matches=indicator_matches,
                                         snapshot_path=snapshot_path)

    def upsert_extracted_flag(self, ip_address: str, extracted: bool = True) -> None:
        """SMB-compatible shim. Delegates to upsert_extracted_flag_for_host with host_type='S'."""
        self.upsert_extracted_flag_for_host(ip_address, 'S', extracted=extracted)

    # --- Protocol-aware write helpers (dual-protocol routing) ----------------

    def upsert_user_flags_for_host(self, ip_address: str, host_type: str, *,
                                    favorite: Optional[bool] = None,
                                    avoid: Optional[bool] = None,
                                    notes: Optional[str] = None) -> None:
        """Route favorite/avoid/notes write to SMB or FTP tables based on host_type.

        Args:
            ip_address: IP address of the host
            host_type: 'S' for SMB (writes host_user_flags), 'F' for FTP (writes ftp_user_flags)
            favorite: Set favorite flag, or None to leave unchanged
            avoid: Set avoid flag, or None to leave unchanged
            notes: Set notes text, or None to leave unchanged

        No-op for invalid host_type or unknown IP.
        FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
        """
        host_type = (host_type or "").upper()
        if not ip_address or host_type not in ('S', 'F', 'H'):
            return
        if host_type == 'S':
            server_table = 'smb_servers'
            flags_table  = 'host_user_flags'
        elif host_type == 'F':
            server_table = 'ftp_servers'
            flags_table  = 'ftp_user_flags'
        else:
            server_table = 'http_servers'
            flags_table  = 'http_user_flags'
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT id FROM {server_table} WHERE ip_address = ?", (ip_address,))
                row = cur.fetchone()
                if not row:
                    return
                server_id = row["id"]
                cur.execute(
                    f"SELECT favorite, avoid, notes FROM {flags_table} WHERE server_id = ?",
                    (server_id,),
                )
                existing  = cur.fetchone()
                fav_val   = existing["favorite"] if existing else 0
                avoid_val = existing["avoid"]    if existing else 0
                notes_val = existing["notes"]    if existing else ""
                if favorite is not None:
                    fav_val = 1 if favorite else 0
                if avoid is not None:
                    avoid_val = 1 if avoid else 0
                if notes is not None:
                    notes_val = notes
                cur.execute(
                    f"""
                    INSERT INTO {flags_table} (server_id, favorite, avoid, notes, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(server_id) DO UPDATE SET
                        favorite=excluded.favorite,
                        avoid=excluded.avoid,
                        notes=excluded.notes,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (server_id, fav_val, avoid_val, notes_val),
                )
                conn.commit()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if host_type == 'F' and "no such table: ftp_" in msg:
                return  # FTP tables absent — migration not yet run; degrade gracefully
            if host_type == 'H' and "no such table: http_" in msg:
                return  # HTTP tables absent — migration not yet run; degrade gracefully
            raise
        self.clear_cache()

    def upsert_probe_cache_for_host(self, ip_address: str, host_type: str, *,
                                     status: str,
                                     indicator_matches: int,
                                     snapshot_path: Optional[str] = None,
                                     accessible_dirs_count: Optional[int] = None,
                                     accessible_dirs_list: Optional[str] = None,
                                     accessible_files_count: Optional[int] = None) -> None:
        """Route probe cache write to SMB, FTP, or HTTP tables based on host_type.

        Args:
            ip_address: IP address of the host
            host_type: 'S' for SMB (host_probe_cache), 'F' for FTP (ftp_probe_cache),
                       'H' for HTTP (http_probe_cache)
            status: Probe status string
            indicator_matches: Number of indicator matches found
            snapshot_path: Optional path to probe snapshot; existing value preserved when None
            accessible_dirs_count: FTP/HTTP accessible directory count
            accessible_dirs_list: FTP/HTTP comma-separated directory paths
            accessible_files_count: HTTP-only accessible file count

        No-op for invalid host_type or unknown IP.
        FTP/HTTP branches degrade gracefully when tables are absent (pre-migration).
        """
        host_type = (host_type or "").upper()
        if not ip_address or host_type not in ('S', 'F', 'H'):
            return
        if host_type == 'S':
            server_table = 'smb_servers'
            cache_table  = 'host_probe_cache'
        elif host_type == 'F':
            server_table = 'ftp_servers'
            cache_table  = 'ftp_probe_cache'
        else:
            server_table = 'http_servers'
            cache_table  = 'http_probe_cache'
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT id FROM {server_table} WHERE ip_address = ?", (ip_address,))
                row = cur.fetchone()
                if not row:
                    return
                server_id = row["id"]
                if host_type == 'F':
                    cur.execute(
                        f"""
                        INSERT INTO {cache_table}
                            (server_id, status, last_probe_at, indicator_matches, snapshot_path,
                             accessible_dirs_count, accessible_dirs_list, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(server_id) DO UPDATE SET
                            status=excluded.status,
                            last_probe_at=excluded.last_probe_at,
                            indicator_matches=excluded.indicator_matches,
                            snapshot_path=COALESCE(excluded.snapshot_path, {cache_table}.snapshot_path),
                            accessible_dirs_count=COALESCE(excluded.accessible_dirs_count, {cache_table}.accessible_dirs_count),
                            accessible_dirs_list=COALESCE(excluded.accessible_dirs_list, {cache_table}.accessible_dirs_list),
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            server_id,
                            status,
                            indicator_matches,
                            snapshot_path,
                            accessible_dirs_count,
                            accessible_dirs_list,
                        ),
                    )
                elif host_type == 'H':
                    cur.execute(
                        f"""
                        INSERT INTO {cache_table}
                            (server_id, status, last_probe_at, indicator_matches, snapshot_path,
                             accessible_dirs_count, accessible_dirs_list, accessible_files_count,
                             updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(server_id) DO UPDATE SET
                            status=excluded.status,
                            last_probe_at=excluded.last_probe_at,
                            indicator_matches=excluded.indicator_matches,
                            snapshot_path=COALESCE(excluded.snapshot_path, {cache_table}.snapshot_path),
                            accessible_dirs_count=COALESCE(excluded.accessible_dirs_count, {cache_table}.accessible_dirs_count),
                            accessible_dirs_list=COALESCE(excluded.accessible_dirs_list, {cache_table}.accessible_dirs_list),
                            accessible_files_count=COALESCE(excluded.accessible_files_count, {cache_table}.accessible_files_count),
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            server_id,
                            status,
                            indicator_matches,
                            snapshot_path,
                            accessible_dirs_count,
                            accessible_dirs_list,
                            accessible_files_count,
                        ),
                    )
                else:
                    cur.execute(
                        f"""
                        INSERT INTO {cache_table}
                            (server_id, status, last_probe_at, indicator_matches, snapshot_path, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(server_id) DO UPDATE SET
                            status=excluded.status,
                            last_probe_at=excluded.last_probe_at,
                            indicator_matches=excluded.indicator_matches,
                            snapshot_path=COALESCE(excluded.snapshot_path, {cache_table}.snapshot_path),
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (server_id, status, indicator_matches, snapshot_path),
                    )
                conn.commit()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if host_type == 'F' and "no such table: ftp_" in msg:
                return
            if host_type == 'H' and "no such table: http_" in msg:
                return
            raise
        self.clear_cache()

    def upsert_extracted_flag_for_host(self, ip_address: str, host_type: str,
                                        extracted: bool = True) -> None:
        """Route extracted flag write to SMB or FTP tables based on host_type.

        Args:
            ip_address: IP address of the host
            host_type: 'S' for SMB (writes host_probe_cache), 'F' for FTP (writes ftp_probe_cache)
            extracted: True to mark as extracted, False to clear

        No-op for invalid host_type or unknown IP.
        FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
        """
        host_type = (host_type or "").upper()
        if not ip_address or host_type not in ('S', 'F', 'H'):
            return
        if host_type == 'S':
            server_table = 'smb_servers'
            cache_table  = 'host_probe_cache'
        elif host_type == 'F':
            server_table = 'ftp_servers'
            cache_table  = 'ftp_probe_cache'
        else:
            server_table = 'http_servers'
            cache_table  = 'http_probe_cache'
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT id FROM {server_table} WHERE ip_address = ?", (ip_address,))
                row = cur.fetchone()
                if not row:
                    return
                server_id = row["id"]
                cur.execute(
                    f"""
                    INSERT INTO {cache_table} (server_id, extracted, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(server_id) DO UPDATE SET
                        extracted=excluded.extracted,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (server_id, 1 if extracted else 0),
                )
                conn.commit()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if host_type == 'F' and "no such table: ftp_" in msg:
                return
            if host_type == 'H' and "no such table: http_" in msg:
                return
            raise
        self.clear_cache()

    def upsert_rce_status_for_host(self, ip_address: str, host_type: str,
                                    rce_status: str,
                                    verdict_summary: Optional[str] = None) -> None:
        """Route RCE analysis status write to SMB or FTP tables based on host_type.

        Args:
            ip_address: IP address of the host
            host_type: 'S' for SMB (writes host_probe_cache), 'F' for FTP (writes ftp_probe_cache)
            rce_status: Status string ('not_run', 'clean', 'flagged', 'unknown', 'error');
                        invalid values are normalized to 'unknown'
            verdict_summary: Optional JSON summary of verdicts

        No-op for invalid host_type or unknown IP.
        FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
        """
        host_type = (host_type or "").upper()
        if not ip_address or host_type not in ('S', 'F', 'H'):
            return
        valid_statuses = {'not_run', 'clean', 'flagged', 'unknown', 'error'}
        if rce_status not in valid_statuses:
            rce_status = 'unknown'
        if host_type == 'S':
            server_table = 'smb_servers'
            cache_table  = 'host_probe_cache'
        elif host_type == 'F':
            server_table = 'ftp_servers'
            cache_table  = 'ftp_probe_cache'
        else:
            server_table = 'http_servers'
            cache_table  = 'http_probe_cache'
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT id FROM {server_table} WHERE ip_address = ?", (ip_address,))
                row = cur.fetchone()
                if not row:
                    return
                server_id = row["id"]
                cur.execute(
                    f"""
                    INSERT INTO {cache_table} (server_id, rce_status, rce_verdict_summary, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(server_id) DO UPDATE SET
                        rce_status=excluded.rce_status,
                        rce_verdict_summary=excluded.rce_verdict_summary,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (server_id, rce_status, verdict_summary),
                )
                conn.commit()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if host_type == 'F' and "no such table: ftp_" in msg:
                return
            if host_type == 'H' and "no such table: http_" in msg:
                return
            raise
        self.clear_cache()

    def bulk_delete_servers(self, ip_addresses: List[str]) -> Dict[str, Any]:
        """
        Bulk delete servers and cascade to related tables.

        Args:
            ip_addresses: List of IP addresses to delete

        Returns:
            Dict with:
            - 'deleted_count': Number of servers actually deleted (from rowcount)
            - 'deleted_ips': List of IPs successfully deleted (for probe cache cleanup)
            - 'error': Error message if operation failed (None on success)
        """
        if not ip_addresses:
            return {"deleted_count": 0, "deleted_ips": [], "error": None}

        try:
            # Deduplicate IPs
            unique_ips = list(set(ip_addresses))

            total_deleted_count = 0
            all_deleted_ips = []

            # Process in batches of 500 (SQLite limit: 999 parameters)
            batch_size = 500
            for i in range(0, len(unique_ips), batch_size):
                batch = unique_ips[i:i + batch_size]

                with self._get_connection() as conn:
                    cur = conn.cursor()

                    # Query existing IPs to find which ones actually exist
                    placeholders = ','.join('?' * len(batch))
                    query = f"SELECT id, ip_address FROM smb_servers WHERE ip_address IN ({placeholders})"
                    cur.execute(query, batch)
                    found_servers = cur.fetchall()

                    if not found_servers:
                        # Nothing to delete in this batch
                        continue

                    found_ips = [row["ip_address"] for row in found_servers]

                    # Delete failure_logs explicitly (no CASCADE on this table)
                    failure_placeholders = ','.join('?' * len(found_ips))
                    delete_failures_query = f"DELETE FROM failure_logs WHERE ip_address IN ({failure_placeholders})"
                    cur.execute(delete_failures_query, found_ips)

                    # Delete servers (CASCADE handles related tables)
                    delete_servers_query = f"DELETE FROM smb_servers WHERE ip_address IN ({failure_placeholders})"
                    cur.execute(delete_servers_query, found_ips)

                    # Check rowcount to verify actual deletes
                    batch_deleted_count = cur.rowcount

                    if batch_deleted_count > 0:
                        # Commit transaction (commits both failure_logs and smb_servers deletes)
                        conn.commit()

                        # Track deleted IPs and count
                        all_deleted_ips.extend(found_ips)
                        total_deleted_count += batch_deleted_count

            # Invalidate cache after successful deletes
            if total_deleted_count > 0:
                self.clear_cache()

            return {
                "deleted_count": total_deleted_count,
                "deleted_ips": all_deleted_ips,
                "error": None
            }

        except Exception as e:
            # Return error in result dict
            return {
                "deleted_count": 0,
                "deleted_ips": [],
                "error": str(e)
            }

    def bulk_delete_rows(self, row_specs: List[Tuple[str, str]]) -> Dict[str, Any]:
        """
        Delete rows by (host_type, ip_address) pairs.

        'S' tuples → DELETE FROM smb_servers WHERE ip_address IN (...)
        'F' tuples → DELETE FROM ftp_servers WHERE ip_address IN (...)
        No cross-protocol deletion possible by construction.

        Returns:
            deleted_count:    total rows removed across both protocols
            deleted_ips:      union of all removed IPs (for display/logging)
            deleted_smb_ips:  IPs where the SMB row was removed — used by caller
                              to selectively clear file-based probe cache
            error:            error string if any partial failure, else None
        """
        if not row_specs:
            return {"deleted_count": 0, "deleted_ips": [], "deleted_smb_ips": [], "error": None}

        smb_ips  = list({ip for ht, ip in row_specs if ht == "S" and ip})
        ftp_ips  = list({ip for ht, ip in row_specs if ht == "F" and ip})
        http_ips = list({ip for ht, ip in row_specs if ht == "H" and ip})

        total_deleted_count = 0
        all_deleted_ips: List[str] = []
        all_deleted_smb_ips: List[str] = []
        error_parts: List[str] = []

        def _append_unique(items: List[str]) -> None:
            for ip in items:
                if ip not in all_deleted_ips:
                    all_deleted_ips.append(ip)

        batch_size = 500

        # --- SMB delete ---
        for i in range(0, len(smb_ips), batch_size):
            batch = smb_ips[i:i + batch_size]
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    placeholders = ','.join('?' * len(batch))
                    cur.execute(
                        f"SELECT ip_address FROM smb_servers WHERE ip_address IN ({placeholders})",
                        batch,
                    )
                    found_smb = [row["ip_address"] for row in cur.fetchall()]
                    if not found_smb:
                        continue
                    fp = ','.join('?' * len(found_smb))
                    # failure_logs has no FK — delete explicitly for SMB IPs only
                    # TODO: failure_logs has no protocol column (schema is IP-only); deleting
                    # by SMB IP is safe because failure_logs rows belong to SMB probes.
                    # FTP-deleted IPs intentionally skip this to avoid clearing SMB sibling data.
                    cur.execute(f"DELETE FROM failure_logs WHERE ip_address IN ({fp})", found_smb)
                    cur.execute(f"DELETE FROM smb_servers WHERE ip_address IN ({fp})", found_smb)
                    n = cur.rowcount
                    if n > 0:
                        conn.commit()
                        _append_unique(found_smb)
                        all_deleted_smb_ips.extend(found_smb)
                        total_deleted_count += n
            except Exception as exc:
                error_parts.append(f"SMB delete error: {exc}")

        # --- FTP delete ---
        for i in range(0, len(ftp_ips), batch_size):
            batch = ftp_ips[i:i + batch_size]
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    placeholders = ','.join('?' * len(batch))
                    cur.execute(
                        f"SELECT ip_address FROM ftp_servers WHERE ip_address IN ({placeholders})",
                        batch,
                    )
                    found_ftp = [row["ip_address"] for row in cur.fetchall()]
                    if not found_ftp:
                        continue
                    fp = ','.join('?' * len(found_ftp))
                    # ftp_user_flags and ftp_probe_cache CASCADE from ftp_servers — no explicit delete needed
                    cur.execute(f"DELETE FROM ftp_servers WHERE ip_address IN ({fp})", found_ftp)
                    n = cur.rowcount
                    if n > 0:
                        conn.commit()
                        _append_unique(found_ftp)
                        total_deleted_count += n
            except sqlite3.OperationalError as exc:
                if "no such table: ftp_servers" in str(exc).lower():
                    error_parts.append("FTP tables not yet migrated; FTP rows not deleted.")
                else:
                    error_parts.append(f"FTP delete error: {exc}")
            except Exception as exc:
                error_parts.append(f"FTP delete error: {exc}")

        # --- HTTP delete ---
        for i in range(0, len(http_ips), batch_size):
            batch = http_ips[i:i + batch_size]
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    placeholders = ','.join('?' * len(batch))
                    cur.execute(
                        f"SELECT ip_address FROM http_servers WHERE ip_address IN ({placeholders})",
                        batch,
                    )
                    found_http = [row["ip_address"] for row in cur.fetchall()]
                    if not found_http:
                        continue
                    fp = ','.join('?' * len(found_http))
                    # http_user_flags and http_probe_cache CASCADE from http_servers
                    cur.execute(f"DELETE FROM http_servers WHERE ip_address IN ({fp})", found_http)
                    n = cur.rowcount
                    if n > 0:
                        conn.commit()
                        _append_unique(found_http)
                        total_deleted_count += n
            except sqlite3.OperationalError as exc:
                if "no such table: http_servers" in str(exc).lower():
                    error_parts.append("HTTP tables not yet migrated; HTTP rows not deleted.")
                else:
                    error_parts.append(f"HTTP delete error: {exc}")
            except Exception as exc:
                error_parts.append(f"HTTP delete error: {exc}")

        if total_deleted_count > 0:
            self.clear_cache()

        return {
            "deleted_count": total_deleted_count,
            "deleted_ips": all_deleted_ips,
            "deleted_smb_ips": all_deleted_smb_ips,
            "error": "; ".join(error_parts) if error_parts else None,
        }

    def _get_mock_data(self) -> Dict[str, Any]:
        """Get mock data for testing."""
        return {
            "servers": [
                {
                    "ip_address": "192.168.1.45",
                    "country": "United States",
                    "country_code": "US",
                    "auth_method": "Anonymous",
                    "last_seen": "2025-01-21T14:20:00",
                    "scan_count": 3,
                    "accessible_shares": 7,
                    "vulnerabilities": 2
                },
                {
                    "ip_address": "10.0.0.123",
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "auth_method": "Guest/Blank",
                    "last_seen": "2025-01-21T11:45:00",
                    "scan_count": 2,
                    "accessible_shares": 3,
                    "vulnerabilities": 1
                },
                {
                    "ip_address": "172.16.5.78",
                    "country": "Canada",
                    "country_code": "CA",
                    "auth_method": "Guest/Guest",
                    "last_seen": "2025-01-20T16:00:00",
                    "scan_count": 1,
                    "accessible_shares": 1,
                    "vulnerabilities": 0
                }
            ]
        }
    
    def is_database_available(self) -> bool:
        """
        Check if database is available and accessible.
        
        Returns:
            True if database can be accessed, False otherwise
        """
        if self.mock_mode:
            return True
        
        try:
            with self._get_connection(timeout=5) as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except (sqlite3.Error, FileNotFoundError):
            return False

    # --- SMB file browser helpers -------------------------------------

    def get_server_auth_method(self, ip_address: str) -> Optional[str]:
        """Return auth_method string for a server by IP."""
        query = "SELECT auth_method FROM smb_servers WHERE ip_address = ? LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, (ip_address,)).fetchone()
            return row["auth_method"] if row else None

    def get_smb_shodan_data(self, ip_address: str) -> Optional[str]:
        """Return the raw shodan_data JSON string for an SMB server by IP."""
        query = "SELECT shodan_data FROM smb_servers WHERE ip_address = ? LIMIT 1"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query, (ip_address,)).fetchone()
                return row["shodan_data"] if row else None
        except Exception:
            return None

    def get_accessible_shares(self, ip_address: str) -> List[Dict[str, Any]]:
        """
        Fetch accessible shares for the given server IP.

        Returns list of dicts: {share_name, permissions, last_tested}
        """
        query = """
        SELECT sa.share_name, sa.permissions, sa.test_timestamp
        FROM share_access sa
        JOIN smb_servers s ON sa.server_id = s.id
        WHERE s.ip_address = ? AND sa.accessible = 1
        ORDER BY sa.share_name
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (ip_address,)).fetchall()
            return [
                {
                    "share_name": row["share_name"],
                    "permissions": row["permissions"],
                    "last_tested": row["test_timestamp"],
                }
                for row in rows
            ]

    def get_denied_shares(self, ip_address: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch denied/non-accessible shares for the given server IP.

        Returns list of dicts: {share_name, auth_status, error_message, last_tested}
        """
        query = """
        SELECT sa.share_name, sa.auth_status, sa.error_message, sa.test_timestamp
        FROM share_access sa
        JOIN smb_servers s ON sa.server_id = s.id
        WHERE s.ip_address = ? AND sa.accessible = 0
        ORDER BY sa.share_name
        """
        with self._get_connection() as conn:
            if limit:
                rows = conn.execute(query + " LIMIT ?", (ip_address, limit)).fetchall()
            else:
                rows = conn.execute(query, (ip_address,)).fetchall()
            return [
                {
                    "share_name": row["share_name"],
                    "auth_status": row["auth_status"],
                    "error_message": row["error_message"],
                    "last_tested": row["test_timestamp"],
                }
                for row in rows
            ]

    def get_denied_share_counts(self) -> Dict[str, int]:
        """
        Return a mapping of ip_address -> denied share count.
        """
        query = """
        SELECT s.ip_address, COUNT(sa.id) as denied_count
        FROM smb_servers s
        LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = 0
        GROUP BY s.ip_address
        """
        with self._get_connection() as conn:
            rows = conn.execute(query).fetchall()
            return {row["ip_address"]: row["denied_count"] or 0 for row in rows}

    # --- Share credentials ---------------------------------------------

    def get_share_credentials(self, ip_address: str) -> List[Dict[str, Any]]:
        """
        Fetch stored credentials for shares on the given host.

        Returns:
            List of dicts with share_name, username, password, source, last_verified_at.
        """
        query = """
            SELECT sc.share_name, sc.username, sc.password, sc.source, sc.last_verified_at
            FROM share_credentials sc
            JOIN smb_servers s ON sc.server_id = s.id
            WHERE s.ip_address = ?
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (ip_address,)).fetchall()
            return [
                {
                    "share_name": row["share_name"],
                    "username": row["username"],
                    "password": row["password"],
                    "source": row["source"],
                    "last_verified_at": row["last_verified_at"],
                }
                for row in rows
            ]

    # --- RCE status helpers ---------------------------------------------

    def get_rce_status(self, ip_address: str) -> Optional[str]:
        """
        Get RCE analysis status for a host.

        Args:
            ip_address: IP address of the host

        Returns:
            RCE status string: 'not_run', 'clean', 'flagged', 'unknown', or 'error'
            Returns 'not_run' if no status found.
        """
        query = """
            SELECT pc.rce_status
            FROM host_probe_cache pc
            JOIN smb_servers s ON pc.server_id = s.id
            WHERE s.ip_address = ?
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (ip_address,)).fetchone()
            return row["rce_status"] if row and row["rce_status"] else "not_run"

    def get_rce_status_for_host(self, ip_address: str, host_type: str) -> str:
        """
        Get RCE analysis status for a host, protocol-aware.

        Args:
            ip_address: IP address of the host
            host_type:  'S' → query host_probe_cache JOIN smb_servers
                        'F' → query ftp_probe_cache JOIN ftp_servers

        Returns:
            RCE status string, or 'not_run' if not found or table absent.
        """
        host_type = (host_type or "S").upper()
        if host_type == "S":
            return self.get_rce_status(ip_address)
        if host_type == "H":
            try:
                query = """
                    SELECT pc.rce_status
                    FROM http_probe_cache pc
                    JOIN http_servers s ON pc.server_id = s.id
                    WHERE s.ip_address = ?
                """
                with self._get_connection() as conn:
                    row = conn.execute(query, (ip_address,)).fetchone()
                    return row["rce_status"] if row and row["rce_status"] else "not_run"
            except sqlite3.OperationalError:
                return "not_run"
        # FTP path
        try:
            query = """
                SELECT pc.rce_status
                FROM ftp_probe_cache pc
                JOIN ftp_servers s ON pc.server_id = s.id
                WHERE s.ip_address = ?
            """
            with self._get_connection() as conn:
                row = conn.execute(query, (ip_address,)).fetchone()
                return row["rce_status"] if row and row["rce_status"] else "not_run"
        except sqlite3.OperationalError:
            return "not_run"

    def upsert_rce_status(self, ip_address: str, rce_status: str,
                          verdict_summary: Optional[str] = None) -> None:
        """SMB-compatible shim. Delegates to upsert_rce_status_for_host with host_type='S'."""
        self.upsert_rce_status_for_host(ip_address, 'S', rce_status, verdict_summary)

    # ------------------------------------------------------------------
    # FTP sidecar read methods
    # All methods guard against OperationalError in case the migration has
    # not yet fired (e.g. very early startup), returning safe empty values.
    # ------------------------------------------------------------------

    def get_ftp_servers(self, country: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return active FTP server rows, optionally filtered by country_code.

        Args:
            country: ISO 3166-1 alpha-2 code to filter by, or None for all.

        Returns:
            List of dicts with ftp_servers columns.
        """
        query = "SELECT * FROM ftp_servers WHERE status = 'active'"
        params: tuple = ()
        if country:
            query += " AND country_code = ?"
            params = (country,)
        query += " ORDER BY last_seen DESC"
        try:
            with self._get_connection() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []

    def get_ftp_server_count(self) -> int:
        """Return count of active FTP servers."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ftp_servers WHERE status = 'active'"
                ).fetchone()
                return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    def get_http_server_detail(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """
        Return {scheme, port} for the most-recently-seen http_servers row for ip_address.

        Returns None if no row found or HTTP tables are absent.
        Silently swallows all exceptions so missing HTTP tables are non-fatal.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT scheme, port FROM http_servers WHERE ip_address = ? "
                    "ORDER BY last_seen DESC LIMIT 1",
                    (ip_address,)
                ).fetchone()
                if row:
                    return {"scheme": row[0] or "http", "port": int(row[1] or 80)}
                return None
        except Exception:
            return None

    def get_host_protocols(self, ip: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query v_host_protocols for protocol presence per IP.

        Args:
            ip: Specific IP address to look up, or None to return all hosts.

        Returns:
            List of dicts with keys: ip_address, has_smb, has_ftp,
            protocol_presence ('smb_only' | 'ftp_only' | 'both').
        """
        query = (
            "SELECT ip_address, has_smb, has_ftp, protocol_presence"
            " FROM v_host_protocols"
        )
        params: tuple = ()
        if ip:
            query += " WHERE ip_address = ?"
            params = (ip,)
        try:
            with self._get_connection() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []

    def get_dual_protocol_count(self) -> int:
        """Return count of IPs present in both smb_servers and ftp_servers."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM v_host_protocols"
                    " WHERE has_smb = 1 AND has_ftp = 1"
                ).fetchone()
                return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    # ------------------------------------------------------------------
    # Unified protocol list — UNION ALL of SMB (S) and FTP (F) rows
    # ------------------------------------------------------------------

    def get_protocol_server_list(
        self,
        limit: Optional[int] = 100,
        offset: int = 0,
        country_filter: Optional[str] = None,
        recent_scan_only: bool = False,
    ) -> Tuple[List[Dict], int]:
        """
        Return a unified, paginated list of SMB and FTP server rows.

        Each row carries a ``host_type`` field ('S' for SMB, 'F' for FTP) and a
        stable ``row_key`` (e.g. "S:123" / "F:456") so the same IP address can
        appear twice when both protocols are present without colliding.

        Protocol-specific state (favorite, avoid, probe, extracted, rce) is read
        from the correct per-protocol table — SMB state never bleeds into FTP
        rows and vice-versa.

        Args:
            limit:          Max rows to return. ``None`` returns all rows.
            offset:         Pagination offset.
            country_filter: ISO 3166-1 alpha-2 country code, or None for all.
            recent_scan_only: If True, restrict to rows seen within 1 hour of
                            the most recent last_seen timestamp across both tables.

        Returns:
            Tuple of (rows, total_count) where rows is a list of dicts.
        """
        if self.mock_mode:
            return self._get_mock_protocol_list(limit, offset, country_filter)

        try:
            return self._query_protocol_server_list_smb_ftp_http(
                limit, offset, country_filter, recent_scan_only
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            # Tier-2: HTTP tables absent (pre-HTTP migration) — try SMB+FTP
            if "no such table: http_" in msg:
                try:
                    return self._query_protocol_server_list_smb_ftp(
                        limit, offset, country_filter, recent_scan_only
                    )
                except sqlite3.OperationalError as exc2:
                    # Tier-3: FTP tables also absent — fall back to SMB-only
                    if "no such table: ftp_" in str(exc2).lower():
                        return self._query_protocol_server_list_smb_only(
                            limit, offset, country_filter, recent_scan_only
                        )
                    raise
            # Tier-3 direct: FTP tables absent without HTTP tables (edge case)
            elif "no such table: ftp_" in msg:
                return self._query_protocol_server_list_smb_only(
                    limit, offset, country_filter, recent_scan_only
                )
            raise

    def _build_union_sql(self, smb_where: str, ftp_where: str) -> str:
        """Return the UNION ALL query string for both protocol halves."""
        return f"""
        SELECT
            'S'                        AS host_type,
            s.id                       AS protocol_server_id,
            'S:' || CAST(s.id AS TEXT) AS row_key,
            s.ip_address,
            s.country,
            s.country_code,
            s.last_seen,
            s.scan_count,
            s.status,
            s.auth_method,
            COALESCE(sa_sum.total_shares, 0)            AS total_shares,
            COALESCE(sa_sum.accessible_shares, 0)       AS accessible_shares,
            COALESCE(sa_sum.accessible_shares_list, '')  AS accessible_shares_list,
            NULL                                         AS port,
            NULL                                         AS banner,
            NULL                                         AS anon_accessible,
            COALESCE(uf.favorite, 0)                    AS favorite,
            COALESCE(uf.avoid, 0)                       AS avoid,
            COALESCE(uf.notes, '')                      AS notes,
            COALESCE(pc.status, 'unprobed')             AS probe_status,
            COALESCE(pc.indicator_matches, 0)           AS indicator_matches,
            COALESCE(pc.extracted, 0)                   AS extracted,
            COALESCE(pc.rce_status, 'not_run')          AS rce_status
        FROM smb_servers s
        LEFT JOIN (
            SELECT
                server_id,
                COUNT(share_name)                                         AS total_shares,
                COUNT(CASE WHEN accessible = 1 THEN 1 END)               AS accessible_shares,
                GROUP_CONCAT(
                    CASE WHEN accessible = 1 THEN share_name END, ','
                )                                                         AS accessible_shares_list
            FROM share_access
            GROUP BY server_id
        ) sa_sum ON s.id = sa_sum.server_id
        LEFT JOIN host_user_flags  uf ON uf.server_id = s.id
        LEFT JOIN host_probe_cache pc ON pc.server_id = s.id
        {smb_where}

        UNION ALL

        SELECT
            'F'                        AS host_type,
            f.id                       AS protocol_server_id,
            'F:' || CAST(f.id AS TEXT) AS row_key,
            f.ip_address,
            f.country,
            f.country_code,
            f.last_seen,
            f.scan_count,
            f.status,
            'anonymous'                AS auth_method,
            COALESCE(
                fpc.accessible_dirs_count,
                CASE
                    WHEN fa_latest.accessible = 1 AND fa_latest.root_listing_available = 1
                    THEN COALESCE(fa_latest.root_entry_count, 0)
                    ELSE 0
                END,
                0
            ) AS total_shares,
            COALESCE(
                fpc.accessible_dirs_count,
                CASE
                    WHEN fa_latest.accessible = 1 AND fa_latest.root_listing_available = 1
                    THEN COALESCE(fa_latest.root_entry_count, 0)
                    ELSE 0
                END,
                0
            ) AS accessible_shares,
            COALESCE(fpc.accessible_dirs_list, '') AS accessible_shares_list,
            f.port,
            f.banner,
            f.anon_accessible,
            COALESCE(fuf.favorite, 0)           AS favorite,
            COALESCE(fuf.avoid, 0)              AS avoid,
            COALESCE(fuf.notes, '')             AS notes,
            COALESCE(fpc.status, 'unprobed')    AS probe_status,
            COALESCE(fpc.indicator_matches, 0)  AS indicator_matches,
            COALESCE(fpc.extracted, 0)          AS extracted,
            COALESCE(fpc.rce_status, 'not_run') AS rce_status
        FROM ftp_servers f
        LEFT JOIN ftp_user_flags  fuf ON fuf.server_id = f.id
        LEFT JOIN ftp_probe_cache fpc ON fpc.server_id = f.id
        LEFT JOIN (
            SELECT
                a.server_id,
                a.accessible,
                a.root_listing_available,
                a.root_entry_count
            FROM ftp_access a
            INNER JOIN (
                SELECT server_id, MAX(id) AS max_id
                FROM ftp_access
                GROUP BY server_id
            ) latest
              ON latest.server_id = a.server_id
             AND latest.max_id    = a.id
        ) fa_latest ON fa_latest.server_id = f.id
        {ftp_where}
        """

    def _build_http_arm(self, http_where: str) -> str:
        """Return the HTTP SELECT arm for the 3-protocol UNION ALL query.

        Produces the same 23 columns in the same order as _build_union_sql arms.
        """
        return f"""
        SELECT
            'H'                         AS host_type,
            hs.id                       AS protocol_server_id,
            'H:' || CAST(hs.id AS TEXT) AS row_key,
            hs.ip_address,
            hs.country,
            hs.country_code,
            hs.last_seen,
            hs.scan_count,
            hs.status,
            'http'                      AS auth_method,
            COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0)
                                        AS total_shares,
            COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0)
                                        AS accessible_shares,
            COALESCE(hpc.accessible_dirs_list, '') AS accessible_shares_list,
            hs.port,
            hs.banner,
            0                           AS anon_accessible,
            COALESCE(huf.favorite, 0)   AS favorite,
            COALESCE(huf.avoid, 0)      AS avoid,
            COALESCE(huf.notes, '')     AS notes,
            COALESCE(hpc.status, 'unprobed')          AS probe_status,
            COALESCE(hpc.indicator_matches, 0)        AS indicator_matches,
            COALESCE(hpc.extracted, 0)                AS extracted,
            COALESCE(hpc.rce_status, 'not_run')       AS rce_status
        FROM http_servers hs
        LEFT JOIN http_user_flags  huf ON huf.server_id = hs.id
        LEFT JOIN http_probe_cache hpc ON hpc.server_id = hs.id
        {http_where}
        """

    def _query_protocol_server_list_smb_ftp_http(
        self,
        limit: Optional[int],
        offset: int,
        country_filter: Optional[str],
        recent_scan_only: bool,
    ) -> Tuple[List[Dict], int]:
        """Execute full UNION ALL query (SMB + FTP + HTTP)."""
        with self._get_connection() as conn:
            smb_where  = "WHERE s.status = 'active'"
            ftp_where  = "WHERE f.status = 'active'"
            http_where = "WHERE hs.status = 'active'"
            smb_params:  List[Any] = []
            ftp_params:  List[Any] = []
            http_params: List[Any] = []

            if country_filter:
                smb_where  += " AND s.country_code = ?"
                ftp_where  += " AND f.country_code = ?"
                http_where += " AND hs.country_code = ?"
                smb_params.append(country_filter)
                ftp_params.append(country_filter)
                http_params.append(country_filter)

            if recent_scan_only:
                cutoff = self._get_protocol_recent_cutoff(conn)
                if cutoff:
                    smb_where  += " AND datetime(s.last_seen)  >= datetime(?, '-1 hour')"
                    ftp_where  += " AND datetime(f.last_seen)  >= datetime(?, '-1 hour')"
                    http_where += " AND datetime(hs.last_seen) >= datetime(?, '-1 hour')"
                    smb_params.append(cutoff)
                    ftp_params.append(cutoff)
                    http_params.append(cutoff)

            union_sql = (
                self._build_union_sql(smb_where, ftp_where)
                + "\n        UNION ALL\n"
                + self._build_http_arm(http_where)
            )
            union_params = smb_params + ftp_params + http_params

            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM ({union_sql}) _u",
                union_params,
            ).fetchone()["total"]

            data_sql = (
                f"SELECT * FROM ({union_sql}) _u"
                f" ORDER BY datetime(last_seen) DESC, row_key ASC"
            )
            data_params = list(union_params)
            if limit is not None and limit > 0:
                data_sql += " LIMIT ? OFFSET ?"
                data_params += [limit, offset]

            rows = conn.execute(data_sql, data_params).fetchall()
            return [dict(row) for row in rows], total

    def _query_protocol_server_list_smb_ftp(
        self,
        limit: Optional[int],
        offset: int,
        country_filter: Optional[str],
        recent_scan_only: bool,
    ) -> Tuple[List[Dict], int]:
        """Execute SMB + FTP UNION ALL query (tier-2 fallback when HTTP tables absent)."""
        with self._get_connection() as conn:
            smb_where = "WHERE s.status = 'active'"
            ftp_where = "WHERE f.status = 'active'"
            smb_params: List[Any] = []
            ftp_params: List[Any] = []

            if country_filter:
                smb_where += " AND s.country_code = ?"
                ftp_where += " AND f.country_code = ?"
                smb_params.append(country_filter)
                ftp_params.append(country_filter)

            if recent_scan_only:
                cutoff = self._get_protocol_recent_cutoff(conn)
                if cutoff:
                    smb_where += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                    ftp_where += " AND datetime(f.last_seen) >= datetime(?, '-1 hour')"
                    smb_params.append(cutoff)
                    ftp_params.append(cutoff)

            union_sql = self._build_union_sql(smb_where, ftp_where)
            union_params = smb_params + ftp_params

            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM ({union_sql}) _u",
                union_params,
            ).fetchone()["total"]

            data_sql = (
                f"SELECT * FROM ({union_sql}) _u"
                f" ORDER BY datetime(last_seen) DESC, row_key ASC"
            )
            data_params = list(union_params)
            if limit is not None and limit > 0:
                data_sql += " LIMIT ? OFFSET ?"
                data_params += [limit, offset]

            rows = conn.execute(data_sql, data_params).fetchall()
            return [dict(row) for row in rows], total

    def _query_protocol_server_list_smb_only(
        self,
        limit: Optional[int],
        offset: int,
        country_filter: Optional[str],
        recent_scan_only: bool,
    ) -> Tuple[List[Dict], int]:
        """SMB-only fallback used when FTP tables are absent."""
        with self._get_connection() as conn:
            smb_where = "WHERE s.status = 'active'"
            smb_params: List[Any] = []

            if country_filter:
                smb_where += " AND s.country_code = ?"
                smb_params.append(country_filter)

            if recent_scan_only:
                row = conn.execute(
                    "SELECT MAX(datetime(last_seen)) AS cutoff"
                    " FROM smb_servers WHERE status = 'active'"
                ).fetchone()
                cutoff = row["cutoff"] if row else None
                if cutoff:
                    smb_where += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                    smb_params.append(cutoff)

            smb_sql = f"""
            SELECT
                'S'                        AS host_type,
                s.id                       AS protocol_server_id,
                'S:' || CAST(s.id AS TEXT) AS row_key,
                s.ip_address,
                s.country,
                s.country_code,
                s.last_seen,
                s.scan_count,
                s.status,
                s.auth_method,
                COALESCE(sa_sum.total_shares, 0)            AS total_shares,
                COALESCE(sa_sum.accessible_shares, 0)       AS accessible_shares,
                COALESCE(sa_sum.accessible_shares_list, '')  AS accessible_shares_list,
                NULL                                         AS port,
                NULL                                         AS banner,
                NULL                                         AS anon_accessible,
                COALESCE(uf.favorite, 0)                    AS favorite,
                COALESCE(uf.avoid, 0)                       AS avoid,
                COALESCE(uf.notes, '')                      AS notes,
                COALESCE(pc.status, 'unprobed')             AS probe_status,
                COALESCE(pc.indicator_matches, 0)           AS indicator_matches,
                COALESCE(pc.extracted, 0)                   AS extracted,
                COALESCE(pc.rce_status, 'not_run')          AS rce_status
            FROM smb_servers s
            LEFT JOIN (
                SELECT
                    server_id,
                    COUNT(share_name)                                         AS total_shares,
                    COUNT(CASE WHEN accessible = 1 THEN 1 END)               AS accessible_shares,
                    GROUP_CONCAT(
                        CASE WHEN accessible = 1 THEN share_name END, ','
                    )                                                         AS accessible_shares_list
                FROM share_access
                GROUP BY server_id
            ) sa_sum ON s.id = sa_sum.server_id
            LEFT JOIN host_user_flags  uf ON uf.server_id = s.id
            LEFT JOIN host_probe_cache pc ON pc.server_id = s.id
            {smb_where}
            """

            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM ({smb_sql}) _u",
                smb_params,
            ).fetchone()["total"]

            data_sql = (
                f"SELECT * FROM ({smb_sql}) _u"
                f" ORDER BY datetime(last_seen) DESC, row_key ASC"
            )
            data_params = list(smb_params)
            if limit is not None and limit > 0:
                data_sql += " LIMIT ? OFFSET ?"
                data_params += [limit, offset]

            rows = conn.execute(data_sql, data_params).fetchall()
            return [dict(row) for row in rows], total

    def _get_protocol_recent_cutoff(self, conn: sqlite3.Connection) -> Optional[str]:
        """
        Return the most recent last_seen timestamp across SMB, FTP, and HTTP servers.

        Uses SQL datetime() normalization to handle mixed timestamp formats
        (YYYY-MM-DD HH:MM:SS vs YYYY-MM-DDTHH:MM:SS) correctly. Falls back
        progressively if HTTP or FTP tables are absent (pre-migration).
        """
        try:
            row = conn.execute("""
                SELECT MAX(datetime(ts)) AS cutoff FROM (
                    SELECT MAX(datetime(last_seen)) AS ts
                    FROM smb_servers WHERE status = 'active'
                    UNION ALL
                    SELECT MAX(datetime(last_seen)) AS ts
                    FROM ftp_servers WHERE status = 'active'
                    UNION ALL
                    SELECT MAX(datetime(last_seen)) AS ts
                    FROM http_servers WHERE status = 'active'
                )
            """).fetchone()
            return row["cutoff"] if row else None
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "no such table: http_" in msg or "no such table: ftp_" in msg:
                # Fall back to SMB + FTP only (or SMB only if FTP also absent)
                try:
                    row = conn.execute("""
                        SELECT MAX(datetime(ts)) AS cutoff FROM (
                            SELECT MAX(datetime(last_seen)) AS ts
                            FROM smb_servers WHERE status = 'active'
                            UNION ALL
                            SELECT MAX(datetime(last_seen)) AS ts
                            FROM ftp_servers WHERE status = 'active'
                        )
                    """).fetchone()
                    return row["cutoff"] if row else None
                except sqlite3.OperationalError:
                    row = conn.execute(
                        "SELECT MAX(datetime(last_seen)) AS cutoff"
                        " FROM smb_servers WHERE status = 'active'"
                    ).fetchone()
                    return row["cutoff"] if row else None
            raise

    def _get_mock_protocol_list(
        self,
        limit: Optional[int],
        offset: int,
        country_filter: Optional[str],
    ) -> Tuple[List[Dict], int]:
        """Return mock S+F rows for testing without a real database."""
        rows: List[Dict] = [
            {
                "host_type": "S",
                "protocol_server_id": 1,
                "row_key": "S:1",
                "ip_address": "192.168.1.45",
                "country": "United States",
                "country_code": "US",
                "last_seen": "2025-01-21T14:20:00",
                "scan_count": 3,
                "status": "active",
                "auth_method": "Anonymous",
                "total_shares": 7,
                "accessible_shares": 7,
                "accessible_shares_list": "ADMIN$,C$,IPC$,share1,share2,share3,share4",
                "port": None,
                "banner": None,
                "anon_accessible": None,
                "favorite": 0,
                "avoid": 0,
                "notes": "",
                "probe_status": "unprobed",
                "indicator_matches": 0,
                "extracted": 0,
                "rce_status": "not_run",
            },
            {
                "host_type": "F",
                "protocol_server_id": 1,
                "row_key": "F:1",
                "ip_address": "10.0.0.123",
                "country": "United Kingdom",
                "country_code": "GB",
                "last_seen": "2025-01-21T11:45:00",
                "scan_count": 1,
                "status": "active",
                "auth_method": "anonymous",
                "total_shares": 0,
                "accessible_shares": 0,
                "accessible_shares_list": "",
                "port": 21,
                "banner": "220 FTP server ready",
                "anon_accessible": 1,
                "favorite": 0,
                "avoid": 0,
                "notes": "",
                "probe_status": "unprobed",
                "indicator_matches": 0,
                "extracted": 0,
                "rce_status": "not_run",
            },
        ]

        if country_filter:
            rows = [r for r in rows if r["country_code"] == country_filter]

        total = len(rows)
        paginated = rows[offset : (offset + limit) if limit is not None else None]
        return paginated, total
