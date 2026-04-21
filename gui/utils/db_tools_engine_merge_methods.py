"""Merge/import DBToolsEngine methods extracted from db_tools_engine.py."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

def merge_database(
    self,
    external_db_path: str,
    strategy=None,
    auto_backup: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> MergeResult:
    """
    Merge an external database into the current database.

    Args:
        external_db_path: Path to the external database to merge
        strategy: How to resolve conflicts for existing IPs
        auto_backup: Create backup before merge (recommended)
        progress_callback: Optional callback for progress updates (percent, message)

    Returns:
        MergeResult with merge statistics
    """
    if strategy is None:
        strategy = MergeConflictStrategy.KEEP_NEWER

    start_time = time.time()
    result = MergeResult(success=False)

    def progress(pct: int, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    try:
        # Phase 0: Safety - create backup
        progress(0, "Preparing merge...")

        if auto_backup:
            progress(2, "Creating backup...")
            backup_result = self.create_backup()
            if backup_result['success']:
                result.backup_path = backup_result['backup_path']
            else:
                result.warnings.append(f"Backup failed: {backup_result.get('error', 'Unknown error')}")

        # Check disk space (estimate 2x current DB size needed)
        db_size = os.path.getsize(self.current_db_path)
        if not self._check_disk_space(db_size * 2, os.path.dirname(self.current_db_path)):
            result.errors.append("Insufficient disk space for merge operation")
            return result

        # Phase 1: Schema validation
        progress(5, "Validating external database schema...")
        validation = self.validate_external_schema(external_db_path)
        if not validation.valid:
            result.errors.extend(validation.errors)
            return result

        # Open connections
        ext_conn = sqlite3.connect(f"file:{external_db_path}?mode=ro", uri=True)
        ext_conn.row_factory = sqlite3.Row

        cur_conn = sqlite3.connect(self.current_db_path)
        cur_conn.row_factory = sqlite3.Row
        cur_conn.execute("PRAGMA foreign_keys = ON")

        ext_tables = {
            row['name']
            for row in ext_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cur_tables = {
            row['name']
            for row in cur_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        current_schema_errors = self._validate_current_merge_schema(cur_conn)
        if current_schema_errors:
            result.errors.extend(current_schema_errors)
            ext_conn.close()
            cur_conn.close()
            return result

        for table_name, label in (
            ("ftp_servers", "FTP server"),
            ("http_servers", "HTTP server"),
        ):
            if table_name in ext_tables and table_name not in cur_tables:
                skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if skipped_rows > 0:
                    result.warnings.append(
                        f"Target DB missing {table_name}; "
                        f"{skipped_rows} {label} rows from source were skipped."
                    )
            elif table_name in ext_tables and table_name in cur_tables:
                required_columns = (
                    REQUIRED_FTP_SERVER_COLUMNS if table_name == "ftp_servers"
                    else REQUIRED_HTTP_SERVER_COLUMNS
                )
                target_columns = self._table_columns(cur_conn, table_name)
                missing_columns = sorted(required_columns - target_columns)
                if missing_columns:
                    skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                    if skipped_rows > 0:
                        result.warnings.append(
                            f"Target table {table_name} missing required columns "
                            f"({', '.join(missing_columns)}); "
                            f"{skipped_rows} {label} rows from source were skipped."
                        )
        for table_name, label, required_columns in (
            ("share_credentials", "share credential", REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS),
            ("file_manifests", "file manifest", REQUIRED_FILE_MANIFEST_TARGET_COLUMNS),
            ("vulnerabilities", "vulnerability", REQUIRED_VULNERABILITY_TARGET_COLUMNS),
            ("failure_logs", "failure log", REQUIRED_FAILURE_LOG_TARGET_COLUMNS),
        ):
            if table_name in ext_tables and table_name not in cur_tables:
                skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if skipped_rows > 0:
                    result.warnings.append(
                        f"Target DB missing {table_name}; "
                        f"{skipped_rows} {label} rows from source were skipped."
                    )
            elif table_name in ext_tables and table_name in cur_tables:
                target_columns = self._table_columns(cur_conn, table_name)
                missing_columns = sorted(required_columns - target_columns)
                if missing_columns:
                    skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                    if skipped_rows > 0:
                        result.warnings.append(
                            f"Target table {table_name} missing required columns "
                            f"({', '.join(missing_columns)}); "
                            f"{skipped_rows} {label} rows from source were skipped."
                        )
        for table_name, label in (
            ("ftp_access", "FTP access"),
            ("http_access", "HTTP access"),
        ):
            if table_name in ext_tables and table_name not in cur_tables:
                skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if skipped_rows > 0:
                    result.warnings.append(
                        f"Target DB missing {table_name}; "
                        f"{skipped_rows} {label} rows from source were skipped."
                    )
            elif table_name in ext_tables and table_name in cur_tables:
                required_columns = (
                    REQUIRED_FTP_ACCESS_TARGET_COLUMNS if table_name == "ftp_access"
                    else REQUIRED_HTTP_ACCESS_TARGET_COLUMNS
                )
                target_columns = self._table_columns(cur_conn, table_name)
                missing_columns = sorted(required_columns - target_columns)
                if missing_columns:
                    skipped_rows = ext_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                    if skipped_rows > 0:
                        result.warnings.append(
                            f"Target table {table_name} missing required columns "
                            f"({', '.join(missing_columns)}); "
                            f"{skipped_rows} {label} rows from source were skipped."
                        )

        try:
            # Keep the merge atomic: any failure rolls back all staged changes.
            cur_conn.execute("BEGIN IMMEDIATE")

            # Phase 2: Create import session
            progress(8, "Creating import session...")
            import_session_id = self._create_import_session(
                cur_conn,
                os.path.basename(external_db_path)
            )

            # Phase 3: Merge protocol server registries
            progress(10, "Merging SMB servers...")
            smb_stats, smb_id_mapping = self._merge_servers(
                ext_conn, cur_conn, strategy, progress
            )
            progress(30, "Merging FTP servers...")
            ftp_stats, ftp_id_mapping = self._merge_ftp_servers(
                ext_conn, cur_conn, strategy
            )
            progress(40, "Merging HTTP servers...")
            http_stats, http_id_mapping = self._merge_http_servers(
                ext_conn, cur_conn, strategy
            )

            result.servers_added = smb_stats['added'] + ftp_stats['added'] + http_stats['added']
            result.servers_updated = smb_stats['updated'] + ftp_stats['updated'] + http_stats['updated']
            result.servers_skipped = smb_stats['skipped'] + ftp_stats['skipped'] + http_stats['skipped']

            # Phase 4: Import related data
            progress(50, "Importing SMB share access records...")
            result.shares_imported = self._import_share_access(
                ext_conn, cur_conn, smb_id_mapping, import_session_id
            )

            progress(58, "Importing FTP access records...")
            result.shares_imported += self._import_ftp_access(
                ext_conn, cur_conn, ftp_id_mapping, import_session_id
            )

            progress(64, "Importing HTTP access records...")
            result.shares_imported += self._import_http_access(
                ext_conn, cur_conn, http_id_mapping, import_session_id
            )

            progress(70, "Importing share credentials...")
            result.credentials_imported = self._import_share_credentials(
                ext_conn, cur_conn, smb_id_mapping, import_session_id
            )

            progress(78, "Importing file manifests...")
            result.file_manifests_imported = self._import_file_manifests(
                ext_conn, cur_conn, smb_id_mapping, import_session_id
            )

            progress(84, "Importing vulnerabilities...")
            result.vulnerabilities_imported = self._import_vulnerabilities(
                ext_conn, cur_conn, smb_id_mapping, import_session_id
            )

            # Phase 5: Import failure logs
            progress(90, "Importing failure logs...")
            result.failure_logs_imported = self._import_failure_logs(
                ext_conn, cur_conn, smb_id_mapping, import_session_id
            )

            # Phase 6: Finalize
            progress(95, "Finalizing merge...")
            self._finalize_import_session(
                cur_conn, import_session_id,
                result.servers_added + result.servers_updated
            )

            cur_conn.commit()
            result.success = True
            progress(100, "Merge completed successfully")

        except Exception:
            cur_conn.rollback()
            raise
        finally:
            ext_conn.close()
            cur_conn.close()

    except Exception as e:
        _logger.exception("Merge operation failed")
        result.errors.append(str(e))

    result.duration_seconds = time.time() - start_time
    return result

def _create_import_session(self, conn: sqlite3.Connection, source_filename: str) -> int:
    """
    Create a scan session record for the import operation.

    Uses runtime column detection for legacy compatibility with older
    scan_sessions layouts.
    """
    cursor = conn.cursor()
    columns = self._table_columns(conn, "scan_sessions")
    if not columns:
        raise RuntimeError("Current database missing required table: scan_sessions")

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insert_values: Dict[str, Any] = {}
    if "tool_name" in columns:
        insert_values["tool_name"] = "db_import"
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

def _finalize_import_session(self, conn: sqlite3.Connection, session_id: int, total_targets: int):
    """Update the import session with final statistics (legacy-column aware)."""
    columns = self._table_columns(conn, "scan_sessions")
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

def _parse_timestamp(self, ts_str: Optional[str]) -> datetime:
    """Parse timestamp string, returning MIN_DATE for NULL/invalid."""
    if not ts_str:
        return MIN_DATE
    try:
        # Handle various timestamp formats
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
        return MIN_DATE
    except Exception:
        return MIN_DATE

def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if the given table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None

def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return the column-name set for a table, or empty set when absent."""
    if not self._table_exists(conn, table_name):
        return set()

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return set()

    first = rows[0]
    if isinstance(first, sqlite3.Row):
        return {row["name"] for row in rows}
    return {row[1] for row in rows}

def _table_has_required_columns(
    self,
    conn: sqlite3.Connection,
    table_name: str,
    required_columns: set[str],
) -> bool:
    """Return True when a table exists and includes all required columns."""
    columns = self._table_columns(conn, table_name)
    if not columns:
        return False
    return required_columns.issubset(columns)

def _validate_current_merge_schema(self, conn: sqlite3.Connection) -> List[str]:
    """Validate target DB core tables/columns required for merge writes."""
    errors: List[str] = []
    for table_name, required_columns in (
        ("scan_sessions", REQUIRED_SCAN_SESSION_TARGET_COLUMNS),
        ("smb_servers", REQUIRED_SMB_SERVER_TARGET_COLUMNS),
        ("share_access", REQUIRED_SHARE_ACCESS_TARGET_COLUMNS),
    ):
        if not self._table_exists(conn, table_name):
            errors.append(f"Current database missing required table: {table_name}")
            continue

        existing_columns = self._table_columns(conn, table_name)
        missing_columns = sorted(required_columns - existing_columns)
        if missing_columns:
            errors.append(
                f"Current database table {table_name} missing required columns: "
                f"{', '.join(missing_columns)}"
            )
    return errors

def _merge_servers(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy: MergeConflictStrategy,
    progress: Callable[[int, str], None]
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
    ext_columns = self._table_columns(ext_conn, "smb_servers")

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
    cur_columns = self._table_columns(cur_conn, "smb_servers")
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

            ext_time = self._parse_timestamp(ext_row['last_seen'])
            cur_time = self._parse_timestamp(cur_row['last_seen'])

            should_update = (
                (strategy == MergeConflictStrategy.KEEP_NEWER and ext_time > cur_time) or
                (strategy == MergeConflictStrategy.KEEP_SOURCE)
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
        if (i + 1) % BATCH_SIZE == 0:
            pct = 10 + int(((i + 1) / total) * 40)  # 10-50% for server merge
            progress(pct, f"Merged {i + 1}/{total} servers...")

    return stats, id_mapping

def _merge_ftp_servers(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy: MergeConflictStrategy,
) -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Merge ftp_servers rows by ip_address.

    Returns:
        Tuple of (stats dict, id_mapping dict)
    """
    stats = {'added': 0, 'updated': 0, 'skipped': 0}
    id_mapping: Dict[int, int] = {}

    if not self._table_has_required_columns(ext_conn, "ftp_servers", REQUIRED_FTP_SERVER_COLUMNS):
        return stats, id_mapping
    if not self._table_has_required_columns(cur_conn, "ftp_servers", REQUIRED_FTP_SERVER_COLUMNS):
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

        ext_time = self._parse_timestamp(row['last_seen'])
        cur_time = self._parse_timestamp(cur_row['last_seen'])
        should_update = (
            (strategy == MergeConflictStrategy.KEEP_NEWER and ext_time > cur_time) or
            (strategy == MergeConflictStrategy.KEEP_SOURCE)
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

def _merge_http_servers(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    strategy: MergeConflictStrategy,
) -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Merge http_servers rows by endpoint (ip_address + port).

    Returns:
        Tuple of (stats dict, id_mapping dict)
    """
    stats = {'added': 0, 'updated': 0, 'skipped': 0}
    id_mapping: Dict[int, int] = {}

    if not self._table_has_required_columns(ext_conn, "http_servers", REQUIRED_HTTP_SERVER_COLUMNS):
        return stats, id_mapping
    if not self._table_has_required_columns(cur_conn, "http_servers", REQUIRED_HTTP_SERVER_COLUMNS):
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
        try:
            endpoint_port = int(row['port']) if row['port'] is not None else 80
        except (TypeError, ValueError):
            endpoint_port = 80
        cur_cursor.execute(
            "SELECT id, last_seen FROM http_servers WHERE ip_address = ? AND port = ?",
            (ip, endpoint_port),
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
                endpoint_port,
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

        ext_time = self._parse_timestamp(row['last_seen'])
        cur_time = self._parse_timestamp(cur_row['last_seen'])
        should_update = (
            (strategy == MergeConflictStrategy.KEEP_NEWER and ext_time > cur_time) or
            (strategy == MergeConflictStrategy.KEEP_SOURCE)
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

def _import_share_access(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int
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
            ext_time = self._parse_timestamp(row['test_timestamp'])
            cur_time = self._parse_timestamp(existing[share_key])
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

def _import_ftp_access(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
) -> int:
    """
    Import ftp_access summary rows with per-server latest-record deduplication.
    """
    if not id_mapping:
        return 0
    if not self._table_has_required_columns(ext_conn, "ftp_access", REQUIRED_FTP_ACCESS_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "ftp_access", REQUIRED_FTP_ACCESS_TARGET_COLUMNS):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    cur_cursor.execute("SELECT id, server_id, test_timestamp FROM ftp_access ORDER BY id DESC")
    existing_latest: Dict[int, Dict[str, Any]] = {}
    for row in cur_cursor.fetchall():
        server_id = row['server_id']
        ts = row['test_timestamp']
        prev = existing_latest.get(server_id)
        if prev is None or self._parse_timestamp(ts) > self._parse_timestamp(prev['test_timestamp']):
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

        ext_time = self._parse_timestamp(row['test_timestamp'])
        existing = existing_latest.get(new_server_id)
        if existing is not None and ext_time <= self._parse_timestamp(existing['test_timestamp']):
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

def _import_http_access(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int,
) -> int:
    """
    Import http_access summary rows with per-server latest-record deduplication.
    """
    if not id_mapping:
        return 0
    if not self._table_has_required_columns(ext_conn, "http_access", REQUIRED_HTTP_ACCESS_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "http_access", REQUIRED_HTTP_ACCESS_TARGET_COLUMNS):
        return 0

    imported = 0
    ext_cursor = ext_conn.cursor()
    cur_cursor = cur_conn.cursor()

    cur_cursor.execute("SELECT id, server_id, test_timestamp FROM http_access ORDER BY id DESC")
    existing_latest: Dict[int, Dict[str, Any]] = {}
    for row in cur_cursor.fetchall():
        server_id = row['server_id']
        ts = row['test_timestamp']
        prev = existing_latest.get(server_id)
        if prev is None or self._parse_timestamp(ts) > self._parse_timestamp(prev['test_timestamp']):
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

        ext_time = self._parse_timestamp(row['test_timestamp'])
        existing = existing_latest.get(new_server_id)
        if existing is not None and ext_time <= self._parse_timestamp(existing['test_timestamp']):
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

def _import_share_credentials(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int
) -> int:
    """Import share_credentials records (has unique index, use INSERT OR IGNORE)."""
    if not self._table_has_required_columns(ext_conn, "share_credentials", REQUIRED_SHARE_CREDENTIALS_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "share_credentials", REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS):
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

def _import_file_manifests(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int
) -> int:
    """Import file_manifests records with deduplication by (server_id, share_name, file_path)."""
    if not self._table_has_required_columns(ext_conn, "file_manifests", REQUIRED_FILE_MANIFEST_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "file_manifests", REQUIRED_FILE_MANIFEST_TARGET_COLUMNS):
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
            ext_time = self._parse_timestamp(row['discovery_timestamp'])
            cur_time = self._parse_timestamp(existing[file_key])
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

def _import_vulnerabilities(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int
) -> int:
    """Import vulnerabilities records with deduplication by (server_id, vuln_type, cve_ids)."""
    if not self._table_has_required_columns(ext_conn, "vulnerabilities", REQUIRED_VULNERABILITY_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "vulnerabilities", REQUIRED_VULNERABILITY_TARGET_COLUMNS):
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

def _import_failure_logs(
    self,
    ext_conn: sqlite3.Connection,
    cur_conn: sqlite3.Connection,
    id_mapping: Dict[int, int],
    import_session_id: int
) -> int:
    """Import failure_logs records (keyed by ip_address, not server_id)."""
    if not self._table_has_required_columns(ext_conn, "failure_logs", REQUIRED_FAILURE_LOG_COLUMNS):
        return 0
    if not self._table_has_required_columns(cur_conn, "failure_logs", REQUIRED_FAILURE_LOG_TARGET_COLUMNS):
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

# -------------------------------------------------------------------------
# Export Operations
# -------------------------------------------------------------------------



def bind_db_tools_engine_merge_methods(engine_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted merge/import methods onto DBToolsEngine."""
    globals().update(shared_symbols)
    method_names = (
        "merge_database",
        "_create_import_session",
        "_finalize_import_session",
        "_parse_timestamp",
        "_table_exists",
        "_table_columns",
        "_table_has_required_columns",
        "_validate_current_merge_schema",
        "_merge_servers",
        "_merge_ftp_servers",
        "_merge_http_servers",
        "_import_share_access",
        "_import_ftp_access",
        "_import_http_access",
        "_import_share_credentials",
        "_import_file_manifests",
        "_import_vulnerabilities",
        "_import_failure_logs",
    )
    for name in method_names:
        setattr(engine_cls, name, globals()[name])
