"""
SMBSeek GUI - Database Tools Engine

Business logic for database management operations including import/merge,
export/backup, statistics, and maintenance. Separated from UI for testability.

Design Decision: All database operations are centralized here to ensure
data integrity and provide consistent behavior. The merge algorithm handles
duplicate IPs by comparing last_seen timestamps.
"""

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import time
import logging

from shared.config import normalize_db_timestamp

_logger = logging.getLogger(__name__)

# Minimum date for NULL timestamp comparisons
MIN_DATE = datetime(1970, 1, 1)

# Batch size for commit operations during merge
BATCH_SIZE = 500

# Required tables for schema validation.
# These are the minimum core tables required for safe SMB merge/import.
REQUIRED_TABLES = {
    'scan_sessions',
    'smb_servers',
    'share_access',
}

# Required columns in smb_servers for merge
REQUIRED_SERVER_COLUMNS = {'ip_address', 'country', 'auth_method', 'last_seen', 'first_seen'}
REQUIRED_SHARE_ACCESS_COLUMNS = {
    'server_id', 'share_name', 'accessible', 'auth_status', 'permissions',
    'share_type', 'share_comment', 'test_timestamp', 'access_details', 'error_message',
}

# Required columns in optional protocol sidecar tables when they exist.
# These reflect columns queried unconditionally by merge/import paths.
REQUIRED_FTP_SERVER_COLUMNS = {
    'ip_address', 'country', 'country_code', 'port', 'anon_accessible',
    'banner', 'shodan_data', 'first_seen', 'last_seen', 'scan_count', 'status', 'notes',
}
REQUIRED_HTTP_SERVER_COLUMNS = {
    'ip_address', 'country', 'country_code', 'port', 'scheme', 'banner', 'title',
    'shodan_data', 'first_seen', 'last_seen', 'scan_count', 'status', 'notes',
}
REQUIRED_FTP_ACCESS_COLUMNS = {
    'server_id', 'accessible', 'auth_status', 'root_listing_available', 'root_entry_count',
    'error_message', 'test_timestamp', 'access_details',
}
REQUIRED_HTTP_ACCESS_COLUMNS = {
    'server_id', 'accessible', 'status_code', 'is_index_page', 'dir_count', 'file_count',
    'tls_verified', 'error_message', 'access_details', 'test_timestamp',
}
REQUIRED_SHARE_CREDENTIALS_COLUMNS = {
    'server_id', 'share_name', 'username', 'password', 'source', 'last_verified_at',
}
REQUIRED_FILE_MANIFEST_COLUMNS = {
    'server_id', 'share_name', 'file_path', 'file_name', 'file_size', 'file_type',
    'file_extension', 'mime_type', 'last_modified', 'is_ransomware_indicator',
    'is_sensitive', 'discovery_timestamp', 'metadata',
}
REQUIRED_VULNERABILITY_COLUMNS = {
    'server_id', 'vuln_type', 'severity', 'title', 'description', 'evidence',
    'remediation', 'cvss_score', 'cve_ids', 'discovery_timestamp', 'status', 'notes',
}
REQUIRED_FAILURE_LOG_COLUMNS = {
    'ip_address', 'failure_timestamp', 'failure_type', 'failure_reason',
    'shodan_data', 'analysis_results', 'retry_count',
}

# Required columns in current/target DB tables for merge writes.
# Source-side validation can remain less strict for legacy compatibility.
REQUIRED_FTP_ACCESS_TARGET_COLUMNS = REQUIRED_FTP_ACCESS_COLUMNS | {'session_id'}
REQUIRED_HTTP_ACCESS_TARGET_COLUMNS = REQUIRED_HTTP_ACCESS_COLUMNS | {'session_id'}
REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS = REQUIRED_SHARE_CREDENTIALS_COLUMNS | {'session_id'}
REQUIRED_FILE_MANIFEST_TARGET_COLUMNS = REQUIRED_FILE_MANIFEST_COLUMNS | {'session_id'}
REQUIRED_VULNERABILITY_TARGET_COLUMNS = REQUIRED_VULNERABILITY_COLUMNS | {'session_id'}
REQUIRED_FAILURE_LOG_TARGET_COLUMNS = REQUIRED_FAILURE_LOG_COLUMNS | {'session_id', 'last_retry_timestamp'}

# Required columns in core target tables needed for merge writes.
REQUIRED_SCAN_SESSION_TARGET_COLUMNS = {'id'}
REQUIRED_SMB_SERVER_TARGET_COLUMNS = {
    'id', 'ip_address', 'country', 'country_code', 'auth_method', 'shodan_data',
    'first_seen', 'last_seen', 'scan_count', 'status', 'notes',
}
REQUIRED_SHARE_ACCESS_TARGET_COLUMNS = REQUIRED_SHARE_ACCESS_COLUMNS | {'session_id'}


class MergeConflictStrategy(Enum):
    """Strategy for resolving conflicts when merging databases."""
    KEEP_NEWER = "keep_newer"       # Keep record with newer last_seen
    KEEP_SOURCE = "keep_source"     # Always prefer source (external) DB
    KEEP_CURRENT = "keep_current"   # Always prefer current DB


@dataclass
class MergeResult:
    """Result of a database merge operation."""
    success: bool
    servers_added: int = 0
    servers_updated: int = 0
    servers_skipped: int = 0
    shares_imported: int = 0
    credentials_imported: int = 0
    vulnerabilities_imported: int = 0
    file_manifests_imported: int = 0
    failure_logs_imported: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    backup_path: Optional[str] = None


@dataclass
class DatabaseStats:
    """Statistics about the database."""
    total_servers: int = 0
    active_servers: int = 0
    total_shares: int = 0
    accessible_shares: int = 0
    total_vulnerabilities: int = 0
    total_file_manifests: int = 0
    total_sessions: int = 0
    total_credentials: int = 0
    database_size_bytes: int = 0
    oldest_record: Optional[str] = None
    newest_record: Optional[str] = None
    countries: Dict[str, int] = field(default_factory=dict)


@dataclass
class PurgePreview:
    """Preview of what would be deleted by a purge operation."""
    servers_to_delete: int = 0
    shares_to_delete: int = 0
    credentials_to_delete: int = 0
    file_manifests_to_delete: int = 0
    vulnerabilities_to_delete: int = 0
    user_flags_to_delete: int = 0
    probe_cache_to_delete: int = 0
    total_records: int = 0
    cutoff_date: Optional[str] = None


@dataclass
class SchemaValidation:
    """Result of schema validation."""
    valid: bool
    missing_tables: List[str] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class DBToolsEngine:
    """
    Database tools engine for SMBSeek GUI.

    Provides business logic for:
    - Schema validation
    - Import/merge operations with conflict resolution
    - Export/backup operations
    - Statistics gathering
    - Maintenance (vacuum, integrity check, purge)
    """

    def __init__(self, current_db_path: str):
        """
        Initialize the database tools engine.

        Args:
            current_db_path: Path to the current (target) database file
        """
        self.current_db_path = current_db_path

    # -------------------------------------------------------------------------
    # Schema Validation
    # -------------------------------------------------------------------------

    def validate_external_schema(self, external_db_path: str) -> SchemaValidation:
        """
        Validate that an external database has a compatible schema.

        Args:
            external_db_path: Path to the external database to validate

        Returns:
            SchemaValidation with validation results
        """
        result = SchemaValidation(valid=True)

        if not os.path.exists(external_db_path):
            result.valid = False
            result.errors.append(f"Database file not found: {external_db_path}")
            return result

        try:
            conn = sqlite3.connect(f"file:{external_db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check required tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row['name'] for row in cursor.fetchall()}

            missing_tables = REQUIRED_TABLES - existing_tables
            if missing_tables:
                result.valid = False
                sorted_missing = sorted(missing_tables)
                result.missing_tables = sorted_missing
                result.errors.append(f"Missing required tables: {', '.join(sorted_missing)}")

            def validate_required_columns(table_name: str, required_columns: set[str]) -> None:
                if table_name not in existing_tables:
                    return
                cursor.execute(f"PRAGMA table_info({table_name})")
                existing_columns = {row['name'] for row in cursor.fetchall()}

                missing = sorted(required_columns - existing_columns)
                if missing:
                    result.valid = False
                    result.missing_columns.extend([f"{table_name}.{col}" for col in missing])
                    result.errors.append(
                        f"Missing required columns in {table_name}: {', '.join(missing)}"
                    )

            # Core SMB merge columns
            validate_required_columns('smb_servers', REQUIRED_SERVER_COLUMNS)
            validate_required_columns('share_access', REQUIRED_SHARE_ACCESS_COLUMNS)
            # Optional protocol sidecars: validate only when table is present.
            validate_required_columns('ftp_servers', REQUIRED_FTP_SERVER_COLUMNS)
            validate_required_columns('http_servers', REQUIRED_HTTP_SERVER_COLUMNS)
            validate_required_columns('ftp_access', REQUIRED_FTP_ACCESS_COLUMNS)
            validate_required_columns('http_access', REQUIRED_HTTP_ACCESS_COLUMNS)
            # Optional artifact sidecars: validate only when table is present.
            validate_required_columns('share_credentials', REQUIRED_SHARE_CREDENTIALS_COLUMNS)
            validate_required_columns('file_manifests', REQUIRED_FILE_MANIFEST_COLUMNS)
            validate_required_columns('vulnerabilities', REQUIRED_VULNERABILITY_COLUMNS)
            validate_required_columns('failure_logs', REQUIRED_FAILURE_LOG_COLUMNS)

            conn.close()

        except sqlite3.Error as e:
            result.valid = False
            result.errors.append(f"Database error: {str(e)}")
        except Exception as e:
            result.valid = False
            result.errors.append(f"Validation error: {str(e)}")

        return result

    # -------------------------------------------------------------------------
    # Merge Preview
    # -------------------------------------------------------------------------

    def preview_merge(self, external_db_path: str) -> Dict[str, Any]:
        """
        Preview what would happen if the external database was merged.

        Args:
            external_db_path: Path to the external database

        Returns:
            Dictionary with preview statistics
        """
        validation = self.validate_external_schema(external_db_path)
        if not validation.valid:
            return {
                'valid': False,
                'errors': validation.errors
            }

        try:
            ext_conn = sqlite3.connect(f"file:{external_db_path}?mode=ro", uri=True)
            ext_conn.row_factory = sqlite3.Row

            cur_conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
            cur_conn.row_factory = sqlite3.Row

            ext_cursor = ext_conn.cursor()
            cur_cursor = cur_conn.cursor()

            ext_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ext_tables = {row['name'] for row in ext_cursor.fetchall()}
            cur_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            cur_tables = {row['name'] for row in cur_cursor.fetchall()}

            warnings: List[str] = []
            schema_skipped_servers = 0
            schema_skipped_shares = 0
            schema_skipped_artifacts = 0
            for table_name, label, required_columns in (
                ("ftp_servers", "FTP server", REQUIRED_FTP_SERVER_COLUMNS),
                ("http_servers", "HTTP server", REQUIRED_HTTP_SERVER_COLUMNS),
            ):
                if table_name in ext_tables and table_name not in cur_tables:
                    ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    skipped_rows = ext_cursor.fetchone()[0]
                    if skipped_rows > 0:
                        schema_skipped_servers += skipped_rows
                        warnings.append(
                            f"Target DB missing {table_name}; "
                            f"{skipped_rows} {label} rows from source will be skipped."
                        )
                elif table_name in ext_tables and table_name in cur_tables:
                    target_columns = self._table_columns(cur_conn, table_name)
                    missing_columns = sorted(required_columns - target_columns)
                    if missing_columns:
                        ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        skipped_rows = ext_cursor.fetchone()[0]
                        if skipped_rows > 0:
                            schema_skipped_servers += skipped_rows
                            warnings.append(
                                f"Target table {table_name} missing required columns "
                                f"({', '.join(missing_columns)}); "
                                f"{skipped_rows} {label} rows from source will be skipped."
                            )

            for table_name, label, required_columns in (
                ("share_credentials", "share credential", REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS),
                ("file_manifests", "file manifest", REQUIRED_FILE_MANIFEST_TARGET_COLUMNS),
                ("vulnerabilities", "vulnerability", REQUIRED_VULNERABILITY_TARGET_COLUMNS),
                ("failure_logs", "failure log", REQUIRED_FAILURE_LOG_TARGET_COLUMNS),
            ):
                if table_name in ext_tables and table_name not in cur_tables:
                    ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    skipped_rows = ext_cursor.fetchone()[0]
                    if skipped_rows > 0:
                        schema_skipped_artifacts += skipped_rows
                        warnings.append(
                            f"Target DB missing {table_name}; "
                            f"{skipped_rows} {label} rows from source will be skipped."
                        )
                elif table_name in ext_tables and table_name in cur_tables:
                    target_columns = self._table_columns(cur_conn, table_name)
                    missing_columns = sorted(required_columns - target_columns)
                    if missing_columns:
                        ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        skipped_rows = ext_cursor.fetchone()[0]
                        if skipped_rows > 0:
                            schema_skipped_artifacts += skipped_rows
                            warnings.append(
                                f"Target table {table_name} missing required columns "
                                f"({', '.join(missing_columns)}); "
                                f"{skipped_rows} {label} rows from source will be skipped."
                            )

            for table_name, label, required_columns in (
                ("ftp_access", "FTP access", REQUIRED_FTP_ACCESS_TARGET_COLUMNS),
                ("http_access", "HTTP access", REQUIRED_HTTP_ACCESS_TARGET_COLUMNS),
            ):
                if table_name in ext_tables and table_name not in cur_tables:
                    ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    skipped_rows = ext_cursor.fetchone()[0]
                    if skipped_rows > 0:
                        schema_skipped_shares += skipped_rows
                        warnings.append(
                            f"Target DB missing {table_name}; "
                            f"{skipped_rows} {label} rows from source will be skipped."
                        )
                elif table_name in ext_tables and table_name in cur_tables:
                    target_columns = self._table_columns(cur_conn, table_name)
                    missing_columns = sorted(required_columns - target_columns)
                    if missing_columns:
                        ext_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        skipped_rows = ext_cursor.fetchone()[0]
                        if skipped_rows > 0:
                            schema_skipped_shares += skipped_rows
                            warnings.append(
                                f"Target table {table_name} missing required columns "
                                f"({', '.join(missing_columns)}); "
                                f"{skipped_rows} {label} rows from source will be skipped."
                            )

            external_servers = 0
            new_servers = 0
            existing_servers = 0
            for table_name in ("smb_servers", "ftp_servers", "http_servers"):
                if table_name not in ext_tables or table_name not in cur_tables:
                    continue
                if table_name == "ftp_servers":
                    if not self._table_has_required_columns(cur_conn, table_name, REQUIRED_FTP_SERVER_COLUMNS):
                        continue
                if table_name == "http_servers":
                    if not self._table_has_required_columns(cur_conn, table_name, REQUIRED_HTTP_SERVER_COLUMNS):
                        continue

                ext_cursor.execute(f"SELECT ip_address FROM {table_name}")
                ext_ips = {row['ip_address'] for row in ext_cursor.fetchall()}
                cur_cursor.execute(f"SELECT ip_address FROM {table_name}")
                cur_ips = {row['ip_address'] for row in cur_cursor.fetchall()}

                external_servers += len(ext_ips)
                new_servers += len(ext_ips - cur_ips)
                existing_servers += len(ext_ips & cur_ips)

            total_shares = 0
            for access_table in ("share_access", "ftp_access", "http_access"):
                if access_table in ext_tables:
                    ext_cursor.execute(f"SELECT COUNT(*) FROM {access_table}")
                    total_shares += ext_cursor.fetchone()[0]

            total_vulns = 0
            if "vulnerabilities" in ext_tables:
                ext_cursor.execute("SELECT COUNT(*) FROM vulnerabilities")
                total_vulns = ext_cursor.fetchone()[0]

            total_files = 0
            if "file_manifests" in ext_tables:
                ext_cursor.execute("SELECT COUNT(*) FROM file_manifests")
                total_files = ext_cursor.fetchone()[0]

            ext_conn.close()
            cur_conn.close()

            return {
                'valid': True,
                'external_servers': external_servers,
                'new_servers': new_servers,
                'existing_servers': existing_servers,
                'total_shares': total_shares,
                'total_vulnerabilities': total_vulns,
                'total_file_manifests': total_files,
                'warnings': warnings,
                'schema_skipped_servers': schema_skipped_servers,
                'schema_skipped_shares': schema_skipped_shares,
                'schema_skipped_artifacts': schema_skipped_artifacts,
            }

        except Exception as e:
            return {
                'valid': False,
                'errors': [str(e)]
            }

    # -------------------------------------------------------------------------
    # Backup Operations
    # -------------------------------------------------------------------------

    def create_backup(self, backup_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a timestamped backup of the current database.

        Args:
            backup_dir: Directory for backup (defaults to same directory as DB)

        Returns:
            Dictionary with backup result
        """
        if not os.path.exists(self.current_db_path):
            return {'success': False, 'error': 'Current database not found'}

        if backup_dir is None:
            backup_dir = os.path.dirname(self.current_db_path)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        db_name = Path(self.current_db_path).stem
        backup_name = f"{db_name}_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_name)

        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        try:
            # Use SQLite online backup API so WAL-mode commits are captured safely.
            src_conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
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

    def _check_disk_space(self, required_bytes: int, path: str) -> bool:
        """Check if sufficient disk space is available."""
        try:
            stat = os.statvfs(path)
            available = stat.f_bavail * stat.f_frsize
            return available >= required_bytes
        except Exception:
            return True  # Assume OK if we can't check

    # -------------------------------------------------------------------------
    # Merge Operations
    # -------------------------------------------------------------------------

    def merge_database(
        self,
        external_db_path: str,
        strategy: MergeConflictStrategy = MergeConflictStrategy.KEEP_NEWER,
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
        Merge http_servers rows by ip_address.

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


def get_db_tools_engine(db_path: str) -> DBToolsEngine:
    """
    Factory function to create a DBToolsEngine instance.

    Args:
        db_path: Path to the database file

    Returns:
        DBToolsEngine instance
    """
    return DBToolsEngine(db_path)
