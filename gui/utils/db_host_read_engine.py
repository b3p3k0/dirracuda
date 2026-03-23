"""
Host-read query engine extracted from DatabaseReader.

Module-level functions only; no imports from database_access (no cycle).
Receives DB connectivity via a callable context-manager parameter.
"""

import sqlite3
from typing import Any, Dict, List, Optional


def get_server_auth_method(get_connection_fn, ip_address: str) -> Optional[str]:
    """Return auth_method string for a server by IP."""
    query = "SELECT auth_method FROM smb_servers WHERE ip_address = ? LIMIT 1"
    with get_connection_fn() as conn:
        row = conn.execute(query, (ip_address,)).fetchone()
        return row["auth_method"] if row else None


def get_accessible_shares(get_connection_fn, ip_address: str) -> List[Dict[str, Any]]:
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
    with get_connection_fn() as conn:
        rows = conn.execute(query, (ip_address,)).fetchall()
        return [
            {
                "share_name": row["share_name"],
                "permissions": row["permissions"],
                "last_tested": row["test_timestamp"],
            }
            for row in rows
        ]


def get_denied_shares(
    get_connection_fn, ip_address: str, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
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
    with get_connection_fn() as conn:
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


def get_denied_share_counts(get_connection_fn) -> Dict[str, int]:
    """
    Return a mapping of ip_address -> denied share count.
    """
    query = """
    SELECT s.ip_address, COUNT(sa.id) as denied_count
    FROM smb_servers s
    LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = 0
    GROUP BY s.ip_address
    """
    with get_connection_fn() as conn:
        rows = conn.execute(query).fetchall()
        return {row["ip_address"]: row["denied_count"] or 0 for row in rows}


def get_share_credentials(
    get_connection_fn, ip_address: str
) -> List[Dict[str, Any]]:
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
    with get_connection_fn() as conn:
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


def get_rce_status(get_connection_fn, ip_address: str) -> Optional[str]:
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
    with get_connection_fn() as conn:
        row = conn.execute(query, (ip_address,)).fetchone()
        return row["rce_status"] if row and row["rce_status"] else "not_run"


def get_rce_status_for_host(
    get_connection_fn, ip_address: str, host_type: str
) -> str:
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
        return get_rce_status(get_connection_fn, ip_address)
    if host_type == "H":
        try:
            query = """
                SELECT pc.rce_status
                FROM http_probe_cache pc
                JOIN http_servers s ON pc.server_id = s.id
                WHERE s.ip_address = ?
            """
            with get_connection_fn() as conn:
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
        with get_connection_fn() as conn:
            row = conn.execute(query, (ip_address,)).fetchone()
            return row["rce_status"] if row and row["rce_status"] else "not_run"
    except sqlite3.OperationalError:
        return "not_run"


def get_ftp_servers(
    get_connection_fn, country: Optional[str] = None
) -> List[Dict[str, Any]]:
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
        with get_connection_fn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def get_ftp_server_count(get_connection_fn) -> int:
    """Return count of active FTP servers."""
    try:
        with get_connection_fn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ftp_servers WHERE status = 'active'"
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def get_http_server_detail(
    get_connection_fn, ip_address: str
) -> Optional[Dict[str, Any]]:
    """
    Return {scheme, port} for the most-recently-seen http_servers row for ip_address.

    Returns None if no row found or HTTP tables are absent.
    Silently swallows all exceptions so missing HTTP tables are non-fatal.
    """
    try:
        with get_connection_fn() as conn:
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


def get_host_protocols(
    get_connection_fn, ip: Optional[str] = None
) -> List[Dict[str, Any]]:
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
        with get_connection_fn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def get_dual_protocol_count(get_connection_fn) -> int:
    """Return count of IPs present in both smb_servers and ftp_servers."""
    try:
        with get_connection_fn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM v_host_protocols"
                " WHERE has_smb = 1 AND has_ftp = 1"
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
