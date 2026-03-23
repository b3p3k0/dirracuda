"""
Protocol-Write Engine — extracted from DatabaseReader.

No circular imports: this module imports sqlite3 and typing only.
All database connectivity is supplied by the caller via get_conn_fn / clear_cache_fn.

Each public function matches the DatabaseReader method it replaced 1:1:
  - same SQL behaviour
  - same error-suppression rules (ftp_/http_ missing tables degrade gracefully)
  - same cache-invalidation points
  - same return shapes
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------

def _append_unique(target: List[str], items: List[str]) -> None:
    """Append each item in *items* to *target* only if not already present."""
    for ip in items:
        if ip not in target:
            target.append(ip)


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------

def upsert_user_flags_for_host(
    get_conn_fn,
    clear_cache_fn,
    ip_address: str,
    host_type: str,
    *,
    favorite: Optional[bool] = None,
    avoid: Optional[bool] = None,
    notes: Optional[str] = None,
) -> None:
    """Route favorite/avoid/notes write to SMB, FTP, or HTTP tables based on host_type.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        ip_address: IP address of the host.
        host_type: 'S' for SMB (host_user_flags), 'F' for FTP (ftp_user_flags),
                   'H' for HTTP (http_user_flags).
        favorite: Set favorite flag, or None to leave unchanged.
        avoid: Set avoid flag, or None to leave unchanged.
        notes: Set notes text, or None to leave unchanged.

    No-op for invalid host_type or unknown IP.
    FTP/HTTP branch degrades gracefully when tables are absent (pre-migration).
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
        with get_conn_fn() as conn:
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
    clear_cache_fn()


def upsert_probe_cache_for_host(
    get_conn_fn,
    clear_cache_fn,
    ip_address: str,
    host_type: str,
    *,
    status: str,
    indicator_matches: int,
    snapshot_path: Optional[str] = None,
    accessible_dirs_count: Optional[int] = None,
    accessible_dirs_list: Optional[str] = None,
    accessible_files_count: Optional[int] = None,
) -> None:
    """Route probe cache write to SMB, FTP, or HTTP tables based on host_type.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        ip_address: IP address of the host.
        host_type: 'S' for SMB (host_probe_cache), 'F' for FTP (ftp_probe_cache),
                   'H' for HTTP (http_probe_cache).
        status: Probe status string.
        indicator_matches: Number of indicator matches found.
        snapshot_path: Optional path to probe snapshot; existing value preserved when None.
        accessible_dirs_count: FTP/HTTP accessible directory count.
        accessible_dirs_list: FTP/HTTP comma-separated directory paths.
        accessible_files_count: HTTP-only accessible file count.

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
        with get_conn_fn() as conn:
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
    clear_cache_fn()


def upsert_extracted_flag_for_host(
    get_conn_fn,
    clear_cache_fn,
    ip_address: str,
    host_type: str,
    extracted: bool = True,
) -> None:
    """Route extracted flag write to SMB, FTP, or HTTP tables based on host_type.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        ip_address: IP address of the host.
        host_type: 'S' for SMB (host_probe_cache), 'F' for FTP (ftp_probe_cache),
                   'H' for HTTP (http_probe_cache).
        extracted: True to mark as extracted, False to clear.

    No-op for invalid host_type or unknown IP.
    FTP/HTTP branch degrades gracefully when tables are absent (pre-migration).
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
        with get_conn_fn() as conn:
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
    clear_cache_fn()


def upsert_rce_status_for_host(
    get_conn_fn,
    clear_cache_fn,
    ip_address: str,
    host_type: str,
    rce_status: str,
    verdict_summary: Optional[str] = None,
) -> None:
    """Route RCE analysis status write to SMB, FTP, or HTTP tables based on host_type.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        ip_address: IP address of the host.
        host_type: 'S' for SMB (host_probe_cache), 'F' for FTP (ftp_probe_cache),
                   'H' for HTTP (http_probe_cache).
        rce_status: Status string ('not_run', 'clean', 'flagged', 'unknown', 'error');
                    invalid values are normalized to 'unknown'.
        verdict_summary: Optional JSON summary of verdicts.

    No-op for invalid host_type or unknown IP.
    FTP/HTTP branch degrades gracefully when tables are absent (pre-migration).
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
        with get_conn_fn() as conn:
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
    clear_cache_fn()


# ---------------------------------------------------------------------------
# Bulk delete helpers
# ---------------------------------------------------------------------------

def bulk_delete_servers(
    get_conn_fn,
    clear_cache_fn,
    ip_addresses: List[str],
) -> Dict[str, Any]:
    """Bulk delete SMB servers and cascade to related tables.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        ip_addresses: List of IP addresses to delete.

    Returns:
        Dict with:
        - 'deleted_count': Number of servers actually deleted (from rowcount).
        - 'deleted_ips': List of IPs successfully deleted (for probe cache cleanup).
        - 'error': Error message if operation failed (None on success).
    """
    if not ip_addresses:
        return {"deleted_count": 0, "deleted_ips": [], "error": None}

    try:
        unique_ips = list(set(ip_addresses))

        total_deleted_count = 0
        all_deleted_ips: List[str] = []

        batch_size = 500
        for i in range(0, len(unique_ips), batch_size):
            batch = unique_ips[i:i + batch_size]

            with get_conn_fn() as conn:
                cur = conn.cursor()

                placeholders = ','.join('?' * len(batch))
                query = f"SELECT id, ip_address FROM smb_servers WHERE ip_address IN ({placeholders})"
                cur.execute(query, batch)
                found_servers = cur.fetchall()

                if not found_servers:
                    continue

                found_ips = [row["ip_address"] for row in found_servers]

                # Delete failure_logs explicitly (no CASCADE on this table)
                failure_placeholders = ','.join('?' * len(found_ips))
                delete_failures_query = f"DELETE FROM failure_logs WHERE ip_address IN ({failure_placeholders})"
                cur.execute(delete_failures_query, found_ips)

                # Delete servers (CASCADE handles related tables)
                delete_servers_query = f"DELETE FROM smb_servers WHERE ip_address IN ({failure_placeholders})"
                cur.execute(delete_servers_query, found_ips)

                batch_deleted_count = cur.rowcount

                if batch_deleted_count > 0:
                    conn.commit()
                    all_deleted_ips.extend(found_ips)
                    total_deleted_count += batch_deleted_count

        if total_deleted_count > 0:
            clear_cache_fn()

        return {
            "deleted_count": total_deleted_count,
            "deleted_ips": all_deleted_ips,
            "error": None,
        }

    except Exception as e:
        return {
            "deleted_count": 0,
            "deleted_ips": [],
            "error": str(e),
        }


def bulk_delete_rows(
    get_conn_fn,
    clear_cache_fn,
    row_specs: List[Tuple[str, str]],
) -> Dict[str, Any]:
    """Delete rows by (host_type, ip_address) pairs.

    'S' tuples → DELETE FROM smb_servers WHERE ip_address IN (...)
    'F' tuples → DELETE FROM ftp_servers WHERE ip_address IN (...)
    'H' tuples → DELETE FROM http_servers WHERE ip_address IN (...)
    No cross-protocol deletion possible by construction.

    Args:
        get_conn_fn: Callable returning a DB connection context manager.
        clear_cache_fn: Callable that clears the reader cache.
        row_specs: List of (host_type, ip_address) pairs.

    Returns:
        deleted_count:    total rows removed across all protocols.
        deleted_ips:      union of all removed IPs (for display/logging).
        deleted_smb_ips:  IPs where the SMB row was removed — used by caller
                          to selectively clear file-based probe cache.
        error:            error string if any partial failure, else None.
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

    batch_size = 500

    # --- SMB delete ---
    for i in range(0, len(smb_ips), batch_size):
        batch = smb_ips[i:i + batch_size]
        try:
            with get_conn_fn() as conn:
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
                    _append_unique(all_deleted_ips, found_smb)
                    all_deleted_smb_ips.extend(found_smb)
                    total_deleted_count += n
        except Exception as exc:
            error_parts.append(f"SMB delete error: {exc}")

    # --- FTP delete ---
    for i in range(0, len(ftp_ips), batch_size):
        batch = ftp_ips[i:i + batch_size]
        try:
            with get_conn_fn() as conn:
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
                    _append_unique(all_deleted_ips, found_ftp)
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
            with get_conn_fn() as conn:
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
                    _append_unique(all_deleted_ips, found_http)
                    total_deleted_count += n
        except sqlite3.OperationalError as exc:
            if "no such table: http_servers" in str(exc).lower():
                error_parts.append("HTTP tables not yet migrated; HTTP rows not deleted.")
            else:
                error_parts.append(f"HTTP delete error: {exc}")
        except Exception as exc:
            error_parts.append(f"HTTP delete error: {exc}")

    if total_deleted_count > 0:
        clear_cache_fn()

    return {
        "deleted_count": total_deleted_count,
        "deleted_ips": all_deleted_ips,
        "deleted_smb_ips": all_deleted_smb_ips,
        "error": "; ".join(error_parts) if error_parts else None,
    }
