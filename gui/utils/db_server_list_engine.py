"""
Server-list query engine extracted from DatabaseReader.

Module-level functions only; no imports from database_access (no cycle).
Receives DB connectivity via a callable context-manager parameter.
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple


def get_server_list(
    get_connection_fn,
    mock_mode: bool,
    mock_data: dict,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """Get paginated server list; dispatches to mock or real-DB path."""
    if mock_mode:
        servers = mock_data["servers"]
        if country_filter:
            servers = [s for s in servers if s["country_code"] == country_filter]
        if recent_scan_only:
            # In mock mode, just return first few servers to simulate recent scan
            servers = servers[:4]  # Mock recent scan with 4 servers

        total = len(servers)
        end = (offset + limit) if limit is not None else None
        paginated = servers[offset:end]
        return paginated, total

    return query_server_list(get_connection_fn, limit, offset, country_filter, recent_scan_only)


def query_server_list(
    get_connection_fn,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool = False,
) -> Tuple[List[Dict], int]:
    """Execute server list query with enhanced share tracking data."""
    with get_connection_fn() as conn:
        # Check if enhanced view exists, fall back to legacy query if not
        view_exists_query = """
        SELECT name FROM sqlite_master
        WHERE type='view' AND name='v_host_share_summary'
        """
        view_exists = conn.execute(view_exists_query).fetchone() is not None

        if view_exists:
            return query_server_list_enhanced(conn, limit, offset, country_filter, recent_scan_only)
        else:
            return query_server_list_legacy(conn, limit, offset, country_filter, recent_scan_only)


def query_server_list_enhanced(
    conn: sqlite3.Connection,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
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

    flags_map = load_user_flags_map(conn)
    probe_map = load_probe_cache_map(conn)

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


def query_server_list_legacy(
    conn: sqlite3.Connection,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
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

    flags_map = load_user_flags_map(conn)
    probe_map = load_probe_cache_map(conn)

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


def load_user_flags_map(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
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


def load_probe_cache_map(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
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
