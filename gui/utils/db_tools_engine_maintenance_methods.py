"""Export/stats/maintenance DBToolsEngine methods extracted from db_tools_engine.py."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

def export_database(
    self,
    output_path: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Export the database to a new file using VACUUM INTO for a clean copy.

    Args:
        output_path: Path for the exported database
        progress_callback: Optional progress callback

    Returns:
        Dictionary with export result
    """
    if progress_callback:
        progress_callback(0, "Preparing export...")

    if not os.path.exists(self.current_db_path):
        return {'success': False, 'error': 'Current database not found'}

    # Check disk space
    db_size = os.path.getsize(self.current_db_path)
    if not self._check_disk_space(db_size * 2, os.path.dirname(output_path)):
        return {'success': False, 'error': 'Insufficient disk space'}

    try:
        if progress_callback:
            progress_callback(10, "Creating optimized copy...")

        conn = sqlite3.connect(self.current_db_path)
        conn.execute(f"VACUUM INTO ?", (output_path,))
        conn.close()

        if progress_callback:
            progress_callback(100, "Export completed")

        return {
            'success': True,
            'output_path': output_path,
            'size_bytes': os.path.getsize(output_path)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def quick_backup(
    self,
    backup_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Create a quick timestamped backup.

    Args:
        backup_dir: Directory for backup (defaults to DB directory)
        progress_callback: Optional progress callback

    Returns:
        Dictionary with backup result
    """
    if progress_callback:
        progress_callback(0, "Creating backup...")

    result = self.create_backup(backup_dir)

    if progress_callback:
        progress_callback(100, "Backup completed" if result['success'] else "Backup failed")

    return result

# -------------------------------------------------------------------------
# Statistics
# -------------------------------------------------------------------------

def get_database_stats(self) -> DatabaseStats:
    """
    Gather statistics about the current database.

    Returns:
        DatabaseStats with all metrics
    """
    stats = DatabaseStats()

    if not os.path.exists(self.current_db_path):
        return stats

    stats.database_size_bytes = os.path.getsize(self.current_db_path)

    try:
        conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row['name'] for row in cursor.fetchall()}
        column_cache: Dict[str, set[str]] = {}

        def has_table(name: str) -> bool:
            return name in existing_tables

        def table_columns(name: str) -> set[str]:
            if name not in column_cache:
                if not has_table(name):
                    column_cache[name] = set()
                else:
                    cursor.execute(f"PRAGMA table_info({name})")
                    column_cache[name] = {row['name'] for row in cursor.fetchall()}
            return column_cache[name]

        # Server counts across all protocol registries
        for table_name in ("smb_servers", "ftp_servers", "http_servers"):
            if not has_table(table_name):
                continue
            columns = table_columns(table_name)
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            table_total = cursor.fetchone()[0]
            stats.total_servers += table_total

            if "status" in columns:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE status = 'active'")
                stats.active_servers += cursor.fetchone()[0]
            else:
                # Legacy defensive fallback: treat rows as active when status is unavailable.
                stats.active_servers += table_total

        # Access/share summary counts across SMB/FTP/HTTP access tables
        access_table_specs = (
            ("share_access", "accessible"),
            ("ftp_access", "accessible"),
            ("http_access", "accessible"),
        )
        for table_name, accessible_col in access_table_specs:
            if not has_table(table_name):
                continue
            columns = table_columns(table_name)
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            stats.total_shares += cursor.fetchone()[0]
            if accessible_col in columns:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {accessible_col} = 1")
                stats.accessible_shares += cursor.fetchone()[0]

        # Other counts
        if has_table("vulnerabilities"):
            cursor.execute("SELECT COUNT(*) FROM vulnerabilities")
            stats.total_vulnerabilities = cursor.fetchone()[0]

        if has_table("file_manifests"):
            cursor.execute("SELECT COUNT(*) FROM file_manifests")
            stats.total_file_manifests = cursor.fetchone()[0]

        if has_table("scan_sessions"):
            cursor.execute("SELECT COUNT(*) FROM scan_sessions")
            stats.total_sessions = cursor.fetchone()[0]

        # Check if share_credentials exists
        if has_table("share_credentials"):
            cursor.execute("SELECT COUNT(*) FROM share_credentials")
            stats.total_credentials = cursor.fetchone()[0]

        # Date range across all protocol server registries
        server_tables = [t for t in ("smb_servers", "ftp_servers", "http_servers") if has_table(t)]
        if server_tables:
            min_tables = [t for t in server_tables if "first_seen" in table_columns(t)]
            if min_tables:
                min_parts = " UNION ALL ".join(
                    f"SELECT MIN(first_seen) AS ts FROM {table_name}" for table_name in min_tables
                )
                cursor.execute(f"SELECT MIN(ts) FROM ({min_parts}) _mins WHERE ts IS NOT NULL")
                row = cursor.fetchone()
                stats.oldest_record = row[0] if row and row[0] else None

            max_tables = [t for t in server_tables if "last_seen" in table_columns(t)]
            if max_tables:
                max_parts = " UNION ALL ".join(
                    f"SELECT MAX(last_seen) AS ts FROM {table_name}" for table_name in max_tables
                )
                cursor.execute(f"SELECT MAX(ts) FROM ({max_parts}) _maxs WHERE ts IS NOT NULL")
                row = cursor.fetchone()
                stats.newest_record = row[0] if row and row[0] else None

            country_tables = [t for t in server_tables if "country" in table_columns(t)]
            if country_tables:
                country_parts = " UNION ALL ".join(
                    f"SELECT country AS country FROM {table_name}" for table_name in country_tables
                )
                cursor.execute(f"""
                    SELECT country, COUNT(*) as cnt
                    FROM ({country_parts}) _countries
                    WHERE country IS NOT NULL AND country != ''
                    GROUP BY country
                    ORDER BY cnt DESC
                """)
                stats.countries = {row['country']: row['cnt'] for row in cursor.fetchall()}

        conn.close()

    except Exception as e:
        _logger.warning("Failed to gather database stats: %s", e)

    return stats

# -------------------------------------------------------------------------
# Maintenance
# -------------------------------------------------------------------------

def vacuum_database(
    self,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Vacuum the database to reclaim space and optimize.

    Args:
        progress_callback: Optional progress callback

    Returns:
        Dictionary with vacuum result
    """
    if progress_callback:
        progress_callback(0, "Starting vacuum...")

    if not os.path.exists(self.current_db_path):
        return {'success': False, 'error': 'Database not found'}

    size_before = os.path.getsize(self.current_db_path)

    try:
        if progress_callback:
            progress_callback(20, "Optimizing database...")

        conn = sqlite3.connect(self.current_db_path)
        conn.execute("VACUUM")
        conn.close()

        size_after = os.path.getsize(self.current_db_path)

        if progress_callback:
            progress_callback(100, "Vacuum completed")

        return {
            'success': True,
            'size_before': size_before,
            'size_after': size_after,
            'space_saved': size_before - size_after
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def integrity_check(self) -> Dict[str, Any]:
    """
    Run SQLite integrity check on the database.

    Returns:
        Dictionary with integrity check result
    """
    if not os.path.exists(self.current_db_path):
        return {'success': False, 'error': 'Database not found'}

    try:
        conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        conn.close()

        return {
            'success': True,
            'integrity_ok': result == 'ok',
            'message': result
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def preview_purge(self, older_than_days: int) -> PurgePreview:
    """
    Preview what would be deleted by a purge operation.

    Args:
        older_than_days: Delete servers not seen in this many days

    Returns:
        PurgePreview with counts of affected records
    """
    preview = PurgePreview()
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff - timedelta(days=older_than_days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    preview.cutoff_date = cutoff_str

    if not os.path.exists(self.current_db_path):
        return preview

    try:
        conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        column_cache: Dict[str, set[str]] = {}

        def has_table(name: str) -> bool:
            return name in existing_tables

        def table_columns(name: str) -> set[str]:
            if name not in column_cache:
                if not has_table(name):
                    column_cache[name] = set()
                else:
                    cursor.execute(f"PRAGMA table_info({name})")
                    column_cache[name] = {row['name'] for row in cursor.fetchall()}
            return column_cache[name]

        def has_columns(name: str, *columns: str) -> bool:
            table_cols = table_columns(name)
            return all(col in table_cols for col in columns)

        # SMB-side purge counts
        if has_columns("smb_servers", "id", "last_seen"):
            cursor.execute("""
                SELECT COUNT(*) FROM smb_servers
                WHERE date(last_seen) < date(?)
            """, (cutoff_str,))
            preview.servers_to_delete += cursor.fetchone()[0]

            if has_columns("share_access", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM share_access sa
                    JOIN smb_servers s ON s.id = sa.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.shares_to_delete += cursor.fetchone()[0]

            if has_columns("share_credentials", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM share_credentials sc
                    JOIN smb_servers s ON s.id = sc.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.credentials_to_delete += cursor.fetchone()[0]

            if has_columns("file_manifests", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM file_manifests fm
                    JOIN smb_servers s ON s.id = fm.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.file_manifests_to_delete += cursor.fetchone()[0]

            if has_columns("vulnerabilities", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM vulnerabilities v
                    JOIN smb_servers s ON s.id = v.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.vulnerabilities_to_delete += cursor.fetchone()[0]

            if has_columns("host_user_flags", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM host_user_flags uf
                    JOIN smb_servers s ON s.id = uf.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.user_flags_to_delete += cursor.fetchone()[0]

            if has_columns("host_probe_cache", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM host_probe_cache pc
                    JOIN smb_servers s ON s.id = pc.server_id
                    WHERE date(s.last_seen) < date(?)
                """, (cutoff_str,))
                preview.probe_cache_to_delete += cursor.fetchone()[0]

        # FTP-side purge counts
        if has_columns("ftp_servers", "id", "last_seen"):
            cursor.execute("""
                SELECT COUNT(*) FROM ftp_servers
                WHERE date(last_seen) < date(?)
            """, (cutoff_str,))
            preview.servers_to_delete += cursor.fetchone()[0]

            if has_columns("ftp_access", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM ftp_access a
                    JOIN ftp_servers f ON f.id = a.server_id
                    WHERE date(f.last_seen) < date(?)
                """, (cutoff_str,))
                preview.shares_to_delete += cursor.fetchone()[0]

            if has_columns("ftp_user_flags", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM ftp_user_flags uf
                    JOIN ftp_servers f ON f.id = uf.server_id
                    WHERE date(f.last_seen) < date(?)
                """, (cutoff_str,))
                preview.user_flags_to_delete += cursor.fetchone()[0]

            if has_columns("ftp_probe_cache", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM ftp_probe_cache pc
                    JOIN ftp_servers f ON f.id = pc.server_id
                    WHERE date(f.last_seen) < date(?)
                """, (cutoff_str,))
                preview.probe_cache_to_delete += cursor.fetchone()[0]

        # HTTP-side purge counts
        if has_columns("http_servers", "id", "last_seen"):
            cursor.execute("""
                SELECT COUNT(*) FROM http_servers
                WHERE date(last_seen) < date(?)
            """, (cutoff_str,))
            preview.servers_to_delete += cursor.fetchone()[0]

            if has_columns("http_access", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM http_access a
                    JOIN http_servers h ON h.id = a.server_id
                    WHERE date(h.last_seen) < date(?)
                """, (cutoff_str,))
                preview.shares_to_delete += cursor.fetchone()[0]

            if has_columns("http_user_flags", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM http_user_flags uf
                    JOIN http_servers h ON h.id = uf.server_id
                    WHERE date(h.last_seen) < date(?)
                """, (cutoff_str,))
                preview.user_flags_to_delete += cursor.fetchone()[0]

            if has_columns("http_probe_cache", "server_id"):
                cursor.execute("""
                    SELECT COUNT(*) FROM http_probe_cache pc
                    JOIN http_servers h ON h.id = pc.server_id
                    WHERE date(h.last_seen) < date(?)
                """, (cutoff_str,))
                preview.probe_cache_to_delete += cursor.fetchone()[0]

        conn.close()

        preview.total_records = (
            preview.servers_to_delete +
            preview.shares_to_delete +
            preview.credentials_to_delete +
            preview.file_manifests_to_delete +
            preview.vulnerabilities_to_delete +
            preview.user_flags_to_delete +
            preview.probe_cache_to_delete
        )

    except Exception as e:
        _logger.warning("Failed to preview purge: %s", e)

    return preview

def execute_purge(
    self,
    older_than_days: int,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Execute purge of old data.

    Args:
        older_than_days: Delete servers not seen in this many days
        progress_callback: Optional progress callback

    Returns:
        Dictionary with purge result
    """
    if progress_callback:
        progress_callback(0, "Preparing purge...")

    if not os.path.exists(self.current_db_path):
        return {'success': False, 'error': 'Database not found'}

    preview = self.preview_purge(older_than_days)

    if preview.servers_to_delete == 0:
        return {
            'success': True,
            'servers_deleted': 0,
            'total_records_deleted': 0,
            'message': 'No servers found matching purge criteria'
        }

    try:
        if progress_callback:
            progress_callback(10, f"Deleting {preview.servers_to_delete} servers...")

        conn = sqlite3.connect(self.current_db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        column_cache: Dict[str, set[str]] = {}

        def table_columns(table_name: str) -> set[str]:
            if table_name not in column_cache:
                if table_name not in existing_tables:
                    column_cache[table_name] = set()
                else:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    column_cache[table_name] = {row[1] for row in cursor.fetchall()}
            return column_cache[table_name]

        def has_columns(table_name: str, *columns: str) -> bool:
            cols = table_columns(table_name)
            return all(col in cols for col in columns)

        deleted = 0
        for table_name in ("smb_servers", "ftp_servers", "http_servers"):
            if not has_columns(table_name, "last_seen"):
                continue
            cursor.execute(
                f"DELETE FROM {table_name} WHERE date(last_seen) < date(?)",
                (preview.cutoff_date,),
            )
            deleted += max(cursor.rowcount, 0)

        conn.commit()
        conn.close()

        if progress_callback:
            progress_callback(100, f"Purge completed: {deleted} servers deleted")

        return {
            'success': True,
            'servers_deleted': deleted,
            'total_records_deleted': preview.total_records,
            'cutoff_date': preview.cutoff_date
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}




def bind_db_tools_engine_maintenance_methods(engine_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted maintenance/stat/export methods onto DBToolsEngine."""
    globals().update(shared_symbols)
    method_names = (
        "export_database",
        "quick_backup",
        "get_database_stats",
        "vacuum_database",
        "integrity_check",
        "preview_purge",
        "execute_purge",
    )
    for name in method_names:
        setattr(engine_cls, name, globals()[name])
