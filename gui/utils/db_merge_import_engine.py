"""
SMBSeek GUI - Database Merge Import Engine

Pure-function helpers for importing related-data records during a merge operation.
Extracted from db_merge_engine.py; no imports from that module (prevents circular imports).
All callers reach these functions via thin delegates in db_merge_engine.
"""

import sqlite3
from typing import Callable, Dict, Set


# ---------------------------------------------------------------------------
# Module-private schema helpers (independent copies — no import from db_merge_engine)
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if the given table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    """Return the column-name set for a table, or empty set when absent."""
    if not _table_exists(conn, table_name):
        return set()

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return set()

    first = rows[0]
    if isinstance(first, sqlite3.Row):
        return {row["name"] for row in rows}
    return {row[1] for row in rows}


def _table_has_required_columns(
    conn: sqlite3.Connection,
    table_name: str,
    required_columns: set,
) -> bool:
    """Return True when a table exists and includes all required columns."""
    columns = _table_columns(conn, table_name)
    if not columns:
        return False
    return required_columns.issubset(columns)


# ---------------------------------------------------------------------------
# Access import functions
# ---------------------------------------------------------------------------

def import_share_access(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    parse_ts_fn: Callable,
) -> int:
    """Import share_access records with deduplication."""
    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    # Get existing shares in current DB for deduplication
    cur_cursor.execute("SELECT server_id, share_name, test_timestamp FROM share_access")
    existing = {
        (row['server_id'], row['share_name']): row['test_timestamp']
        for row in cur_cursor.fetchall()
    }

    # Only import shares for servers we've added or updated
    server_ids = tuple(id_mapping.keys())
    if not server_ids:
        return 0

    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, share_name, accessible, auth_status, permissions,
               share_type, share_comment, test_timestamp, access_details, error_message
        FROM share_access
        WHERE server_id IN ({placeholders})
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        share_key = (new_server_id, row['share_name'])

        # Deduplication: check if share exists
        if share_key in existing:
            ext_time = parse_ts_fn(row['test_timestamp'])
            cur_time = parse_ts_fn(existing[share_key])
            if ext_time <= cur_time:
                continue  # Skip - current is newer or same
            # Update existing record
            cur_cursor.execute("""
                UPDATE share_access SET
                    accessible = ?, auth_status = ?, permissions = ?,
                    share_type = ?, share_comment = ?, test_timestamp = ?,
                    access_details = ?, error_message = ?, session_id = ?
                WHERE server_id = ? AND share_name = ?
            """, (
                row['accessible'], row['auth_status'], row['permissions'],
                row['share_type'], row['share_comment'], row['test_timestamp'],
                row['access_details'], row['error_message'], import_session_id,
                new_server_id, row['share_name']
            ))
        else:
            # Insert new record
            cur_cursor.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status,
                    permissions, share_type, share_comment, test_timestamp,
                    access_details, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_server_id, import_session_id, row['share_name'],
                row['accessible'], row['auth_status'], row['permissions'],
                row['share_type'], row['share_comment'], row['test_timestamp'],
                row['access_details'], row['error_message']
            ))

        imported += 1

    return imported


def import_ftp_access(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    parse_ts_fn: Callable,
    required_read: set,
    required_target: set,
) -> int:
    """
    Import ftp_access summary rows with per-server latest-record deduplication.
    """
    if not id_mapping:
        return 0
    if not _table_has_required_columns(ext_conn, "ftp_access", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "ftp_access", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    cur_cursor.execute("SELECT id, server_id, test_timestamp FROM ftp_access ORDER BY id DESC")
    existing_latest: Dict[int, Dict] = {}
    for row in cur_cursor.fetchall():
        server_id = row['server_id']
        ts = row['test_timestamp']
        prev = existing_latest.get(server_id)
        if prev is None or parse_ts_fn(ts) > parse_ts_fn(prev['test_timestamp']):
            existing_latest[server_id] = {'id': row['id'], 'test_timestamp': ts}

    server_ids = tuple(id_mapping.keys())
    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, accessible, auth_status, root_listing_available, root_entry_count,
               error_message, test_timestamp, access_details
        FROM ftp_access
        WHERE server_id IN ({placeholders})
        ORDER BY server_id, test_timestamp DESC, id DESC
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        ext_time = parse_ts_fn(row['test_timestamp'])
        existing = existing_latest.get(new_server_id)
        if existing is not None and ext_time <= parse_ts_fn(existing['test_timestamp']):
            continue

        if existing is None:
            cur_cursor.execute("""
                INSERT INTO ftp_access (
                    server_id, session_id, accessible, auth_status, root_listing_available,
                    root_entry_count, error_message, test_timestamp, access_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_server_id,
                import_session_id,
                row['accessible'],
                row['auth_status'],
                row['root_listing_available'],
                row['root_entry_count'],
                row['error_message'],
                row['test_timestamp'],
                row['access_details'],
            ))
            existing_latest[new_server_id] = {
                'id': cur_cursor.lastrowid,
                'test_timestamp': row['test_timestamp'],
            }
        else:
            cur_cursor.execute("""
                UPDATE ftp_access SET
                    session_id = ?,
                    accessible = ?,
                    auth_status = ?,
                    root_listing_available = ?,
                    root_entry_count = ?,
                    error_message = ?,
                    test_timestamp = ?,
                    access_details = ?
                WHERE id = ?
            """, (
                import_session_id,
                row['accessible'],
                row['auth_status'],
                row['root_listing_available'],
                row['root_entry_count'],
                row['error_message'],
                row['test_timestamp'],
                row['access_details'],
                existing['id'],
            ))
            existing_latest[new_server_id] = {
                'id': existing['id'],
                'test_timestamp': row['test_timestamp'],
            }

        imported += 1

    return imported


def import_http_access(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    parse_ts_fn: Callable,
    required_read: set,
    required_target: set,
) -> int:
    """
    Import http_access summary rows with per-server latest-record deduplication.
    """
    if not id_mapping:
        return 0
    if not _table_has_required_columns(ext_conn, "http_access", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "http_access", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    cur_cursor.execute("SELECT id, server_id, test_timestamp FROM http_access ORDER BY id DESC")
    existing_latest: Dict[int, Dict] = {}
    for row in cur_cursor.fetchall():
        server_id = row['server_id']
        ts = row['test_timestamp']
        prev = existing_latest.get(server_id)
        if prev is None or parse_ts_fn(ts) > parse_ts_fn(prev['test_timestamp']):
            existing_latest[server_id] = {'id': row['id'], 'test_timestamp': ts}

    server_ids = tuple(id_mapping.keys())
    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, accessible, status_code, is_index_page, dir_count, file_count,
               tls_verified, error_message, access_details, test_timestamp
        FROM http_access
        WHERE server_id IN ({placeholders})
        ORDER BY server_id, test_timestamp DESC, id DESC
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        ext_time = parse_ts_fn(row['test_timestamp'])
        existing = existing_latest.get(new_server_id)
        if existing is not None and ext_time <= parse_ts_fn(existing['test_timestamp']):
            continue

        if existing is None:
            cur_cursor.execute("""
                INSERT INTO http_access (
                    server_id, session_id, accessible, status_code, is_index_page, dir_count,
                    file_count, tls_verified, error_message, access_details, test_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_server_id,
                import_session_id,
                row['accessible'],
                row['status_code'],
                row['is_index_page'],
                row['dir_count'],
                row['file_count'],
                row['tls_verified'],
                row['error_message'],
                row['access_details'],
                row['test_timestamp'],
            ))
            existing_latest[new_server_id] = {
                'id': cur_cursor.lastrowid,
                'test_timestamp': row['test_timestamp'],
            }
        else:
            cur_cursor.execute("""
                UPDATE http_access SET
                    session_id = ?,
                    accessible = ?,
                    status_code = ?,
                    is_index_page = ?,
                    dir_count = ?,
                    file_count = ?,
                    tls_verified = ?,
                    error_message = ?,
                    access_details = ?,
                    test_timestamp = ?
                WHERE id = ?
            """, (
                import_session_id,
                row['accessible'],
                row['status_code'],
                row['is_index_page'],
                row['dir_count'],
                row['file_count'],
                row['tls_verified'],
                row['error_message'],
                row['access_details'],
                row['test_timestamp'],
                existing['id'],
            ))
            existing_latest[new_server_id] = {
                'id': existing['id'],
                'test_timestamp': row['test_timestamp'],
            }

        imported += 1

    return imported


def import_share_credentials(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    required_read: set,
    required_target: set,
) -> int:
    """Import share_credentials records (has unique index, use INSERT OR IGNORE)."""
    if not _table_has_required_columns(ext_conn, "share_credentials", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "share_credentials", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    server_ids = tuple(id_mapping.keys())
    if not server_ids:
        return 0

    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, share_name, username, password, source, last_verified_at
        FROM share_credentials
        WHERE server_id IN ({placeholders})
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        # INSERT OR IGNORE due to unique constraint
        cur_cursor.execute("""
            INSERT OR IGNORE INTO share_credentials (
                server_id, share_name, username, password, source,
                session_id, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            new_server_id, row['share_name'], row['username'],
            row['password'], row['source'], import_session_id,
            row['last_verified_at']
        ))
        if cur_cursor.rowcount > 0:
            imported += 1

    return imported


# ---------------------------------------------------------------------------
# Artifact import functions
# ---------------------------------------------------------------------------

def import_file_manifests(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    parse_ts_fn: Callable,
    required_read: set,
    required_target: set,
) -> int:
    """Import file_manifests records with deduplication by (server_id, share_name, file_path)."""
    if not _table_has_required_columns(ext_conn, "file_manifests", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "file_manifests", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    # Get existing file manifests for deduplication
    cur_cursor.execute(
        "SELECT server_id, share_name, file_path, discovery_timestamp FROM file_manifests"
    )
    existing = {
        (row['server_id'], row['share_name'], row['file_path']): row['discovery_timestamp']
        for row in cur_cursor.fetchall()
    }

    server_ids = tuple(id_mapping.keys())
    if not server_ids:
        return 0

    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, share_name, file_path, file_name, file_size, file_type,
               file_extension, mime_type, last_modified, is_ransomware_indicator,
               is_sensitive, discovery_timestamp, metadata
        FROM file_manifests
        WHERE server_id IN ({placeholders})
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        file_key = (new_server_id, row['share_name'], row['file_path'])

        # Deduplication
        if file_key in existing:
            ext_time = parse_ts_fn(row['discovery_timestamp'])
            cur_time = parse_ts_fn(existing[file_key])
            if ext_time <= cur_time:
                continue  # Skip

        # Insert (no update for file manifests - they're discovery records)
        if file_key not in existing:
            cur_cursor.execute("""
                INSERT INTO file_manifests (
                    server_id, session_id, share_name, file_path, file_name,
                    file_size, file_type, file_extension, mime_type, last_modified,
                    is_ransomware_indicator, is_sensitive, discovery_timestamp, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_server_id, import_session_id, row['share_name'],
                row['file_path'], row['file_name'], row['file_size'],
                row['file_type'], row['file_extension'], row['mime_type'],
                row['last_modified'], row['is_ransomware_indicator'],
                row['is_sensitive'], row['discovery_timestamp'], row['metadata']
            ))
            imported += 1

    return imported


def import_vulnerabilities(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    required_read: set,
    required_target: set,
) -> int:
    """Import vulnerabilities records with deduplication by (server_id, vuln_type, cve_ids)."""
    if not _table_has_required_columns(ext_conn, "vulnerabilities", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "vulnerabilities", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    # Get existing vulnerabilities for deduplication
    cur_cursor.execute(
        "SELECT server_id, vuln_type, cve_ids FROM vulnerabilities"
    )
    existing = {
        (row['server_id'], row['vuln_type'], row['cve_ids'] or '')
        for row in cur_cursor.fetchall()
    }

    server_ids = tuple(id_mapping.keys())
    if not server_ids:
        return 0

    placeholders = ','.join('?' * len(server_ids))
    ext_cursor.execute(f"""
        SELECT server_id, vuln_type, severity, title, description, evidence,
               remediation, cvss_score, cve_ids, discovery_timestamp, status, notes
        FROM vulnerabilities
        WHERE server_id IN ({placeholders})
    """, server_ids)

    for row in ext_cursor.fetchall():
        new_server_id = id_mapping.get(row['server_id'])
        if new_server_id is None:
            continue

        vuln_key = (new_server_id, row['vuln_type'], row['cve_ids'] or '')

        if vuln_key in existing:
            continue  # Skip existing vulnerabilities

        cur_cursor.execute("""
            INSERT INTO vulnerabilities (
                server_id, session_id, vuln_type, severity, title, description,
                evidence, remediation, cvss_score, cve_ids, discovery_timestamp,
                status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            new_server_id, import_session_id, row['vuln_type'],
            row['severity'], row['title'], row['description'],
            row['evidence'], row['remediation'], row['cvss_score'],
            row['cve_ids'], row['discovery_timestamp'],
            row['status'] or 'open', row['notes']
        ))
        imported += 1

    return imported


def import_failure_logs(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
    *,
    required_read: set,
    required_target: set,
) -> int:
    """Import failure_logs records (keyed by ip_address, not server_id)."""
    if not _table_has_required_columns(ext_conn, "failure_logs", required_read):
        return 0
    if not _table_has_required_columns(cur_conn, "failure_logs", required_target):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    # Get current server IPs from mapping
    cur_cursor.execute("SELECT id, ip_address FROM smb_servers")
    server_ips = {row['ip_address'] for row in cur_cursor.fetchall()}

    # Also get IPs of servers we imported
    ext_cursor.execute("SELECT id, ip_address FROM smb_servers")
    imported_ips = {
        row['ip_address'] for row in ext_cursor.fetchall()
        if row['id'] in id_mapping
    }

    # Get existing failure logs for deduplication
    cur_cursor.execute("SELECT ip_address, failure_type FROM failure_logs")
    existing = {
        (row['ip_address'], row['failure_type'])
        for row in cur_cursor.fetchall()
    }

    ext_cursor.execute("""
        SELECT ip_address, failure_timestamp, failure_type, failure_reason,
               shodan_data, analysis_results, retry_count
        FROM failure_logs
    """)

    for row in ext_cursor.fetchall():
        ip = row['ip_address']
        if ip not in imported_ips:
            continue

        log_key = (ip, row['failure_type'])

        if log_key in existing:
            # Update retry count if exists
            cur_cursor.execute("""
                UPDATE failure_logs SET
                    retry_count = retry_count + ?,
                    last_retry_timestamp = CURRENT_TIMESTAMP
                WHERE ip_address = ? AND failure_type = ?
            """, (row['retry_count'] or 0, ip, row['failure_type']))
        else:
            cur_cursor.execute("""
                INSERT INTO failure_logs (
                    session_id, ip_address, failure_timestamp, failure_type,
                    failure_reason, shodan_data, analysis_results, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                import_session_id, ip, row['failure_timestamp'],
                row['failure_type'], row['failure_reason'],
                row['shodan_data'], row['analysis_results'],
                row['retry_count'] or 0
            ))
            imported += 1

    return imported
