"""
SMBSeek GUI - Database Maintenance Engine

Pure functions for backup, export, statistics, and purge operations.
Extracted from DBToolsEngine (db_tools_engine.py) as a thin, testable module.

Dataclasses (DatabaseStats, PurgePreview) remain in db_tools_engine.py and are
passed as factory callables so this module stays free of circular imports.
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def create_backup(db_path: str, backup_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a timestamped backup of the current database.

    Args:
        db_path: Path to the source database
        backup_dir: Directory for backup (defaults to same directory as DB)

    Returns:
        Dictionary with backup result
    """
    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Current database not found'}

    if backup_dir is None:
        backup_dir = os.path.dirname(db_path)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    db_name = Path(db_path).stem
    backup_name = f"{db_name}_backup_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    src_conn: Optional[sqlite3.Connection] = None
    dst_conn: Optional[sqlite3.Connection] = None
    try:
        # Use SQLite online backup API so WAL-mode commits are captured safely.
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(backup_path)
        src_conn.backup(dst_conn)
        dst_conn.commit()
        return {
            'success': True,
            'backup_path': backup_path,
            'size_bytes': os.path.getsize(backup_path)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()


def _check_disk_space(required_bytes: int, path: str) -> bool:
    """Check if sufficient disk space is available."""
    try:
        stat = os.statvfs(path)
        available = stat.f_bavail * stat.f_frsize
        return available >= required_bytes
    except Exception:
        return True  # Assume OK if we can't check


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_database(
    db_path: str,
    output_path: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Export the database to a new file using VACUUM INTO for a clean copy.

    Args:
        db_path: Path to the source database
        output_path: Path for the exported database
        progress_callback: Optional progress callback

    Returns:
        Dictionary with export result
    """
    if progress_callback:
        progress_callback(0, "Preparing export...")

    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Current database not found'}

    # Check disk space
    db_size = os.path.getsize(db_path)
    if not _check_disk_space(db_size * 2, os.path.dirname(output_path)):
        return {'success': False, 'error': 'Insufficient disk space'}

    try:
        if progress_callback:
            progress_callback(10, "Creating optimized copy...")

        conn = sqlite3.connect(db_path)
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
    db_path: str,
    backup_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Create a quick timestamped backup.

    Args:
        db_path: Path to the source database
        backup_dir: Directory for backup (defaults to DB directory)
        progress_callback: Optional progress callback

    Returns:
        Dictionary with backup result
    """
    if progress_callback:
        progress_callback(0, "Creating backup...")

    result = create_backup(db_path, backup_dir)

    if progress_callback:
        progress_callback(100, "Backup completed" if result['success'] else "Backup failed")

    return result


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_database_stats(db_path: str, stats_factory: Callable) -> Any:
    """
    Gather statistics about the current database.

    Args:
        db_path: Path to the database
        stats_factory: Callable that returns a fresh DatabaseStats instance

    Returns:
        DatabaseStats with all metrics
    """
    stats = stats_factory()

    if not os.path.exists(db_path):
        return stats

    stats.database_size_bytes = os.path.getsize(db_path)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row['name'] for row in cursor.fetchall()}
        column_cache: Dict[str, set] = {}

        def has_table(name: str) -> bool:
            return name in existing_tables

        def table_columns(name: str) -> set:
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


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def vacuum_database(
    db_path: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Vacuum the database to reclaim space and optimize.

    Args:
        db_path: Path to the database
        progress_callback: Optional progress callback

    Returns:
        Dictionary with vacuum result
    """
    if progress_callback:
        progress_callback(0, "Starting vacuum...")

    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Database not found'}

    size_before = os.path.getsize(db_path)

    try:
        if progress_callback:
            progress_callback(20, "Optimizing database...")

        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()

        size_after = os.path.getsize(db_path)

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


def integrity_check(db_path: str) -> Dict[str, Any]:
    """
    Run SQLite integrity check on the database.

    Args:
        db_path: Path to the database

    Returns:
        Dictionary with integrity check result
    """
    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Database not found'}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------

def preview_purge(db_path: str, older_than_days: int, preview_factory: Callable) -> Any:
    """
    Preview what would be deleted by a purge operation.

    Args:
        db_path: Path to the database
        older_than_days: Delete servers not seen in this many days
        preview_factory: Callable that returns a fresh PurgePreview instance

    Returns:
        PurgePreview with counts of affected records
    """
    preview = preview_factory()
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff - timedelta(days=older_than_days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    preview.cutoff_date = cutoff_str

    if not os.path.exists(db_path):
        return preview

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        column_cache: Dict[str, set] = {}

        def has_table(name: str) -> bool:
            return name in existing_tables

        def table_columns(name: str) -> set:
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
    db_path: str,
    older_than_days: int,
    preview_factory: Callable,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    Execute purge of old data.

    Args:
        db_path: Path to the database
        older_than_days: Delete servers not seen in this many days
        preview_factory: Callable that returns a fresh PurgePreview instance
        progress_callback: Optional progress callback

    Returns:
        Dictionary with purge result
    """
    if progress_callback:
        progress_callback(0, "Preparing purge...")

    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Database not found'}

    preview = preview_purge(db_path, older_than_days, preview_factory)

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

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        column_cache: Dict[str, set] = {}

        def table_columns(table_name: str) -> set:
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
