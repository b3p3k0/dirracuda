"""
SMBSeek GUI - Database Merge Engine

Pure-function helpers for the db_tools_engine merge/import pipeline.
No imports from gui.utils.db_tools_engine (prevents circular imports).
All constants, enum values, and sentinels are passed in from DBToolsEngine adapters.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from shared.config import normalize_db_timestamp

from gui.utils import db_merge_import_engine as _merge_import

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def parse_timestamp(ts_str: Optional[str], min_date: datetime) -> datetime:
    """Parse timestamp string, returning min_date for NULL/invalid."""
    if not ts_str:
        return min_date
    try:
        for fmt in [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%d'
        ]:
            try:
                return datetime.strptime(ts_str.split('+')[0].split('Z')[0], fmt)
            except ValueError:
                continue
        return min_date
    except Exception:
        return min_date


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if the given table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    """Return the column-name set for a table, or empty set when absent."""
    if not table_exists(conn, table_name):
        return set()

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return set()

    first = rows[0]
    if isinstance(first, sqlite3.Row):
        return {row["name"] for row in rows}
    return {row[1] for row in rows}


def table_has_required_columns(
    conn: sqlite3.Connection,
    table_name: str,
    required_columns: set,
) -> bool:
    """Return True when a table exists and includes all required columns."""
    columns = table_columns(conn, table_name)
    if not columns:
        return False
    return required_columns.issubset(columns)


# ---------------------------------------------------------------------------
# Server merge functions
# ---------------------------------------------------------------------------

def merge_servers(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy,
    progress: Callable,
    *,
    parse_ts_fn: Callable,
    keep_newer,
    keep_source,
    batch_size: int,
) -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Merge servers from external DB into current DB.

    Returns:
        Tuple of (stats dict, id_mapping dict)
    """
    stats = {'added': 0, 'updated': 0, 'skipped': 0}
    id_mapping = {}  # external_id -> current_id

    # Get all external servers
    ext_cursor = ext_conn.cursor()
    ext_columns = table_columns(ext_conn, "smb_servers")

    def _select_or_default(col: str, default_sql: str) -> str:
        if col in ext_columns:
            return col
        return f"{default_sql} AS {col}"

    select_parts = [
        "id",
        "ip_address",
        _select_or_default("country", "NULL"),
        _select_or_default("country_code", "NULL"),
        _select_or_default("auth_method", "NULL"),
        _select_or_default("shodan_data", "NULL"),
        "first_seen",
        "last_seen",
        _select_or_default("scan_count", "1"),
        _select_or_default("status", "'active'"),
        _select_or_default("notes", "NULL"),
    ]
    ext_cursor.execute(
        f"SELECT {', '.join(select_parts)} FROM smb_servers ORDER BY last_seen DESC"
    )
    ext_servers = ext_cursor.fetchall()

    cur_cursor = cur_conn.cursor()
    cur_columns = table_columns(cur_conn, "smb_servers")
    total = len(ext_servers)

    for i, ext_row in enumerate(ext_servers):
        ext_id = ext_row['id']
        ip = ext_row['ip_address']

        # Check if IP exists in current DB
        cur_cursor.execute(
            "SELECT id, last_seen FROM smb_servers WHERE ip_address = ?",
            (ip,)
        )
        cur_row = cur_cursor.fetchone()

        if cur_row is None:
            # New server - insert
            cur_cursor.execute("""
                INSERT INTO smb_servers (
                    ip_address, country, country_code, auth_method,
                    shodan_data, first_seen, last_seen, scan_count, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ip, ext_row['country'], ext_row['country_code'],
                ext_row['auth_method'], ext_row['shodan_data'],
                normalize_db_timestamp(ext_row['first_seen']),
                normalize_db_timestamp(ext_row['last_seen']),
                ext_row['scan_count'] or 1, ext_row['status'] or 'active',
                ext_row['notes']
            ))
            id_mapping[ext_id] = cur_cursor.lastrowid
            stats['added'] += 1
        else:
            # Existing server - apply conflict strategy
            cur_id = cur_row['id']
            id_mapping[ext_id] = cur_id

            ext_time = parse_ts_fn(ext_row['last_seen'])
            cur_time = parse_ts_fn(cur_row['last_seen'])

            should_update = (
                (strategy == keep_newer and ext_time > cur_time) or
                (strategy == keep_source)
            )

            if should_update:
                assignments = [
                    "last_seen = ?",
                    "auth_method = COALESCE(?, auth_method)",
                    "country = COALESCE(?, country)",
                    "country_code = COALESCE(?, country_code)",
                    "scan_count = scan_count + ?",
                ]
                if "updated_at" in cur_columns:
                    assignments.append("updated_at = CURRENT_TIMESTAMP")

                values = [
                    normalize_db_timestamp(ext_row['last_seen']),
                    ext_row['auth_method'],
                    ext_row['country'],
                    ext_row['country_code'],
                    ext_row['scan_count'] or 0,
                    cur_id,
                ]
                cur_cursor.execute(
                    f"UPDATE smb_servers SET {', '.join(assignments)} WHERE id = ?",
                    tuple(values),
                )
                stats['updated'] += 1
            else:
                stats['skipped'] += 1

        # Batch progress update (commit is managed by merge_database transaction)
        if (i + 1) % batch_size == 0:
            pct = 10 + int(((i + 1) / total) * 40)  # 10-50% for server merge
            progress(pct, f"Merged {i + 1}/{total} servers...")

    return stats, id_mapping


def merge_ftp_servers(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy,
    *,
    parse_ts_fn: Callable,
    keep_newer,
    keep_source,
    required_cols: set,
) -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Merge ftp_servers rows by ip_address.

    Returns:
        Tuple of (stats dict, id_mapping dict)
    """
    stats = {'added': 0, 'updated': 0, 'skipped': 0}
    id_mapping: Dict[int, int] = {}

    if not table_has_required_columns(ext_conn, "ftp_servers", required_cols):
        return stats, id_mapping
    if not table_has_required_columns(cur_conn, "ftp_servers", required_cols):
        return stats, id_mapping

    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()
    ext_cursor.execute("""
        SELECT id, ip_address, country, country_code, port, anon_accessible,
               banner, shodan_data, first_seen, last_seen, scan_count, status, notes
        FROM ftp_servers
        ORDER BY last_seen DESC
    """)
    ext_rows = ext_cursor.fetchall()

    for row in ext_rows:
        ext_id = row['id']
        ip = row['ip_address']
        cur_cursor.execute(
            "SELECT id, last_seen FROM ftp_servers WHERE ip_address = ?",
            (ip,),
        )
        cur_row = cur_cursor.fetchone()

        if cur_row is None:
            cur_cursor.execute("""
                INSERT INTO ftp_servers (
                    ip_address, country, country_code, port, anon_accessible,
                    banner, shodan_data, first_seen, last_seen, scan_count, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ip,
                row['country'],
                row['country_code'],
                row['port'] if row['port'] is not None else 21,
                row['anon_accessible'] if row['anon_accessible'] is not None else 0,
                row['banner'],
                row['shodan_data'],
                normalize_db_timestamp(row['first_seen']),
                normalize_db_timestamp(row['last_seen']),
                row['scan_count'] or 1,
                row['status'] or 'active',
                row['notes'],
            ))
            id_mapping[ext_id] = cur_cursor.lastrowid
            stats['added'] += 1
            continue

        cur_id = cur_row['id']
        id_mapping[ext_id] = cur_id

        ext_time = parse_ts_fn(row['last_seen'])
        cur_time = parse_ts_fn(cur_row['last_seen'])
        should_update = (
            (strategy == keep_newer and ext_time > cur_time) or
            (strategy == keep_source)
        )
        if not should_update:
            stats['skipped'] += 1
            continue

        cur_cursor.execute("""
            UPDATE ftp_servers SET
                last_seen = ?,
                country = COALESCE(?, country),
                country_code = COALESCE(?, country_code),
                port = COALESCE(?, port),
                anon_accessible = COALESCE(?, anon_accessible),
                banner = COALESCE(?, banner),
                status = COALESCE(?, status),
                scan_count = scan_count + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            normalize_db_timestamp(row['last_seen']),
            row['country'],
            row['country_code'],
            row['port'],
            row['anon_accessible'],
            row['banner'],
            row['status'],
            row['scan_count'] or 0,
            cur_id,
        ))
        stats['updated'] += 1

    return stats, id_mapping


def merge_http_servers(
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy,
    *,
    parse_ts_fn: Callable,
    keep_newer,
    keep_source,
    required_cols: set,
) -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Merge http_servers rows by ip_address.

    Returns:
        Tuple of (stats dict, id_mapping dict)
    """
    stats = {'added': 0, 'updated': 0, 'skipped': 0}
    id_mapping: Dict[int, int] = {}

    if not table_has_required_columns(ext_conn, "http_servers", required_cols):
        return stats, id_mapping
    if not table_has_required_columns(cur_conn, "http_servers", required_cols):
        return stats, id_mapping

    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()
    ext_cursor.execute("""
        SELECT id, ip_address, country, country_code, port, scheme, banner, title,
               shodan_data, first_seen, last_seen, scan_count, status, notes
        FROM http_servers
        ORDER BY last_seen DESC
    """)
    ext_rows = ext_cursor.fetchall()

    for row in ext_rows:
        ext_id = row['id']
        ip = row['ip_address']
        cur_cursor.execute(
            "SELECT id, last_seen FROM http_servers WHERE ip_address = ?",
            (ip,),
        )
        cur_row = cur_cursor.fetchone()

        if cur_row is None:
            cur_cursor.execute("""
                INSERT INTO http_servers (
                    ip_address, country, country_code, port, scheme, banner, title,
                    shodan_data, first_seen, last_seen, scan_count, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ip,
                row['country'],
                row['country_code'],
                row['port'] if row['port'] is not None else 80,
                row['scheme'] or 'http',
                row['banner'],
                row['title'],
                row['shodan_data'],
                normalize_db_timestamp(row['first_seen']),
                normalize_db_timestamp(row['last_seen']),
                row['scan_count'] or 1,
                row['status'] or 'active',
                row['notes'],
            ))
            id_mapping[ext_id] = cur_cursor.lastrowid
            stats['added'] += 1
            continue

        cur_id = cur_row['id']
        id_mapping[ext_id] = cur_id

        ext_time = parse_ts_fn(row['last_seen'])
        cur_time = parse_ts_fn(cur_row['last_seen'])
        should_update = (
            (strategy == keep_newer and ext_time > cur_time) or
            (strategy == keep_source)
        )
        if not should_update:
            stats['skipped'] += 1
            continue

        cur_cursor.execute("""
            UPDATE http_servers SET
                last_seen = ?,
                country = COALESCE(?, country),
                country_code = COALESCE(?, country_code),
                port = COALESCE(?, port),
                scheme = COALESCE(?, scheme),
                banner = COALESCE(?, banner),
                title = COALESCE(?, title),
                status = COALESCE(?, status),
                scan_count = scan_count + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            normalize_db_timestamp(row['last_seen']),
            row['country'],
            row['country_code'],
            row['port'],
            row['scheme'],
            row['banner'],
            row['title'],
            row['status'],
            row['scan_count'] or 0,
            cur_id,
        ))
        stats['updated'] += 1

    return stats, id_mapping


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
    return _merge_import.import_share_access(
        ext_conn, cur_conn, id_mapping, import_session_id, parse_ts_fn=parse_ts_fn
    )


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
    return _merge_import.import_ftp_access(
        ext_conn, cur_conn, id_mapping, import_session_id,
        parse_ts_fn=parse_ts_fn, required_read=required_read, required_target=required_target,
    )


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
    return _merge_import.import_http_access(
        ext_conn, cur_conn, id_mapping, import_session_id,
        parse_ts_fn=parse_ts_fn, required_read=required_read, required_target=required_target,
    )


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
    return _merge_import.import_share_credentials(
        ext_conn, cur_conn, id_mapping, import_session_id,
        required_read=required_read, required_target=required_target,
    )


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
    return _merge_import.import_file_manifests(
        ext_conn, cur_conn, id_mapping, import_session_id,
        parse_ts_fn=parse_ts_fn, required_read=required_read, required_target=required_target,
    )


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
    return _merge_import.import_vulnerabilities(
        ext_conn, cur_conn, id_mapping, import_session_id,
        required_read=required_read, required_target=required_target,
    )


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
    return _merge_import.import_failure_logs(
        ext_conn, cur_conn, id_mapping, import_session_id,
        required_read=required_read, required_target=required_target,
    )


# ---------------------------------------------------------------------------
# Import session lifecycle
# ---------------------------------------------------------------------------

def create_import_session(conn: sqlite3.Connection, source_filename: str) -> int:
    """
    Create a scan session record for the import operation.

    Uses runtime column detection for legacy compatibility with older
    scan_sessions layouts.
    """
    cursor = conn.cursor()
    columns = table_columns(conn, "scan_sessions")
    if not columns:
        raise RuntimeError("Current database missing required table: scan_sessions")

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insert_values: Dict[str, Any] = {}
    if "tool_name" in columns:
        insert_values["tool_name"] = "smbseek"
    if "scan_type" in columns:
        insert_values["scan_type"] = "db_import"
    if "status" in columns:
        insert_values["status"] = "running"
    if "notes" in columns:
        insert_values["notes"] = f"Imported from: {source_filename}"
    if "timestamp" in columns:
        insert_values["timestamp"] = now_ts
    if "started_at" in columns:
        insert_values["started_at"] = now_ts

    if not insert_values:
        cursor.execute("INSERT INTO scan_sessions DEFAULT VALUES")
        return cursor.lastrowid

    cols = list(insert_values.keys())
    placeholders = ", ".join(["?"] * len(cols))
    cursor.execute(
        f"INSERT INTO scan_sessions ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(insert_values[col] for col in cols),
    )
    return cursor.lastrowid


def finalize_import_session(
    conn: sqlite3.Connection, session_id: int, total_targets: int
) -> None:
    """Update the import session with final statistics (legacy-column aware)."""
    columns = table_columns(conn, "scan_sessions")
    if not columns or "id" not in columns:
        return

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    assignments: List[str] = []
    values: List[Any] = []

    if "status" in columns:
        assignments.append("status = ?")
        values.append("completed")
    if "completed_at" in columns:
        assignments.append("completed_at = ?")
        values.append(now_ts)
    if "total_targets" in columns:
        assignments.append("total_targets = ?")
        values.append(total_targets)
    if "successful_targets" in columns:
        assignments.append("successful_targets = ?")
        values.append(total_targets)

    if not assignments:
        return

    values.append(session_id)
    conn.execute(
        f"UPDATE scan_sessions SET {', '.join(assignments)} WHERE id = ?",
        tuple(values),
    )


def validate_current_merge_schema(
    conn: sqlite3.Connection,
    required_specs: List[Tuple[str, Set[str]]],
) -> List[str]:
    """Validate target DB core tables/columns required for merge writes."""
    errors: List[str] = []
    for table_name, required_columns in required_specs:
        if not table_exists(conn, table_name):
            errors.append(f"Current database missing required table: {table_name}")
            continue

        existing_columns = table_columns(conn, table_name)
        missing_columns = sorted(required_columns - existing_columns)
        if missing_columns:
            errors.append(
                f"Current database table {table_name} missing required columns: "
                f"{', '.join(missing_columns)}"
            )
    return errors
