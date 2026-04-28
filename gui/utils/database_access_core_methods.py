"""Core/dashboard/cache methods extracted from database_access.py."""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from error_codes import get_error, format_error_message
except ImportError:
    from .error_codes import get_error, format_error_message

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

def _ensure_http_columns(self) -> None:
    """
    Best-effort migration to add legacy optional HTTP columns if missing.

    Some minimal/older schemas define http_servers without probe_host/probe_path.
    This helper is idempotent and safe to run at startup.
    """
    with sqlite3.connect(self.db_path) as conn:
        cur = conn.cursor()
        table_row = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='http_servers'"
        ).fetchone()
        if not table_row:
            return

        cols = [row[1] for row in cur.execute("PRAGMA table_info(http_servers)").fetchall()]
        altered = False
        if "probe_host" not in cols:
            cur.execute("ALTER TABLE http_servers ADD COLUMN probe_host TEXT")
            altered = True
        if "probe_path" not in cols:
            cur.execute("ALTER TABLE http_servers ADD COLUMN probe_path TEXT")
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
                    analysis['import_recommendation'] = 'Full Dirracuda database - ready for import'
                elif len(core_tables_present) == len(schema_def['core_tables']):
                    analysis['compatibility_level'] = 'partial'
                    analysis['import_recommendation'] = 'Partial Dirracuda database - core data available'
                    if len(data_tables_present) > 0:
                        analysis['import_recommendation'] += f' with {len(data_tables_present)} additional data tables'
                else:
                    analysis['compatibility_level'] = 'minimal'
                    analysis['import_recommendation'] = 'Basic Dirracuda database - limited functionality'

                analysis['valid'] = True
            else:
                analysis['compatibility_level'] = 'none'
                analysis['import_recommendation'] = 'Not a compatible Dirracuda database'
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



def bind_database_access_core_methods(reader_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted core/dashboard/cache methods onto DatabaseReader."""
    globals().update(shared_symbols)
    method_names = (
        "_ensure_rce_columns",
        "_ensure_http_columns",
        "get_smbseek_schema_definition",
        "analyze_database_schema",
        "validate_database",
        "set_database_path",
        "enable_mock_mode",
        "disable_mock_mode",
        "_get_connection",
        "get_dashboard_summary",
        "get_dashboard_stats",
        "_query_dashboard_summary",
        "get_top_findings",
        "_query_top_findings",
        "get_country_breakdown",
        "_query_country_breakdown",
        "get_recent_activity",
        "_query_recent_activity",
        "get_server_list",
        "_query_server_list",
        "_query_server_list_enhanced",
        "_query_server_list_legacy",
        "_load_user_flags_map",
        "_load_probe_cache_map",
        "_is_cached",
        "_cache_result",
        "_get_db_modified_time",
        "clear_cache",
    )
    for name in method_names:
        setattr(reader_cls, name, globals()[name])
