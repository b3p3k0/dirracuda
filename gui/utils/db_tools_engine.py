"""
SMBSeek GUI - Database Tools Engine

Business logic for database management operations including import/merge,
export/backup, statistics, and maintenance. Separated from UI for testability.

Design Decision: All database operations are centralized here to ensure
data integrity and provide consistent behavior. The merge algorithm handles
duplicate IPs by comparing last_seen timestamps.
"""

import os
import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import time
import logging

from shared.config import normalize_db_timestamp
from gui.utils import csv_import_engine as _csv
from gui.utils import db_merge_engine as _db_merge
from gui.utils import db_maintenance_engine as _maint
from gui.utils import db_preflight_engine as _preflight
from gui.utils import db_tools_csv_orchestration_engine as _csv_orch
from gui.utils.db_preflight_engine import (
    REQUIRED_FAILURE_LOG_COLUMNS,
    REQUIRED_FAILURE_LOG_TARGET_COLUMNS,
    REQUIRED_FILE_MANIFEST_COLUMNS,
    REQUIRED_FILE_MANIFEST_TARGET_COLUMNS,
    REQUIRED_FTP_ACCESS_COLUMNS,
    REQUIRED_FTP_ACCESS_TARGET_COLUMNS,
    REQUIRED_FTP_SERVER_COLUMNS,
    REQUIRED_FTP_SERVER_TARGET_COLUMNS,
    REQUIRED_HTTP_ACCESS_COLUMNS,
    REQUIRED_HTTP_ACCESS_TARGET_COLUMNS,
    REQUIRED_HTTP_SERVER_COLUMNS,
    REQUIRED_HTTP_SERVER_TARGET_COLUMNS,
    REQUIRED_SCAN_SESSION_TARGET_COLUMNS,
    REQUIRED_SERVER_COLUMNS,
    REQUIRED_SHARE_ACCESS_COLUMNS,
    REQUIRED_SHARE_ACCESS_TARGET_COLUMNS,
    REQUIRED_SHARE_CREDENTIALS_COLUMNS,
    REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS,
    REQUIRED_SMB_SERVER_TARGET_COLUMNS,
    REQUIRED_TABLES,
    REQUIRED_VULNERABILITY_COLUMNS,
    REQUIRED_VULNERABILITY_TARGET_COLUMNS,
)

_logger = logging.getLogger(__name__)

# Minimum date for NULL timestamp comparisons
MIN_DATE = datetime(1970, 1, 1)

# Batch size for commit operations during merge
BATCH_SIZE = 500


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


@dataclass
class CSVImportResult:
    """Result of CSV host import operation."""
    success: bool
    rows_total: int = 0
    rows_valid: int = 0
    rows_skipped: int = 0
    servers_added: int = 0
    servers_updated: int = 0
    servers_skipped: int = 0
    protocol_counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    backup_path: Optional[str] = None


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
        return _preflight.validate_external_schema(external_db_path, SchemaValidation)

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
        return _preflight.preview_merge(
            external_db_path,
            current_db_path=self.current_db_path,
            schema_factory=SchemaValidation,
            table_columns_fn=self._table_columns,
            table_has_required_columns_fn=self._table_has_required_columns,
        )

    # -------------------------------------------------------------------------
    # CSV Host Import (S/F/H server rows)
    # -------------------------------------------------------------------------

    def preview_csv_import(self, csv_path: str) -> Dict[str, Any]:
        """
        Preview CSV host import outcomes without writing to the database.

        CSV contract:
        - Required: ip_address
        - Optional: host_type (S/F/H; defaults to S), country, country_code,
          auth_method, first_seen, last_seen, scan_count, status, notes,
          port, anon_accessible, banner, scheme, title, shodan_data
        """
        return _preflight.preview_csv_import(
            csv_path,
            current_db_path=self.current_db_path,
            analyze_csv_hosts_fn=self._analyze_csv_hosts,
        )

    def import_csv_hosts(
        self,
        csv_path: str,
        strategy: MergeConflictStrategy = MergeConflictStrategy.KEEP_NEWER,
        auto_backup: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> CSVImportResult:
        """
        Import protocol-aware host rows from CSV into SMB/FTP/HTTP server tables.

        Rows are written per protocol table based on host_type:
        - S -> smb_servers
        - F -> ftp_servers
        - H -> http_servers
        """
        return _csv_orch.import_csv_hosts(
            engine=self,
            csv_path=csv_path,
            strategy=strategy,
            auto_backup=auto_backup,
            progress_callback=progress_callback,
            result_factory=CSVImportResult,
            logger=_logger,
        )

    def _read_csv_host_records(self, csv_path: str) -> List[Tuple[int, Dict[str, str]]]:
        """Read CSV rows with normalized header keys; skip comment-only lines."""
        return _csv.read_csv_host_records(csv_path)

    def _analyze_csv_hosts(
        self,
        csv_path: str,
        conn: sqlite3.Connection,
        include_rows: bool,
    ) -> Dict[str, Any]:
        """Parse and validate CSV host rows against current runtime schema."""
        protocol_specs = {
            'S': ('smb_servers', REQUIRED_SMB_SERVER_TARGET_COLUMNS),
            'F': ('ftp_servers', REQUIRED_FTP_SERVER_TARGET_COLUMNS),
            'H': ('http_servers', REQUIRED_HTTP_SERVER_TARGET_COLUMNS),
        }
        return _csv.analyze_csv_hosts(csv_path, conn, protocol_specs, include_rows)

    def _prepare_csv_host_row(
        self,
        raw_row: Dict[str, str],
        host_type: str,
        now_ts: str,
        row_number: int,
        add_warning: Callable[[str], None],
    ) -> Dict[str, Any]:
        """Normalize CSV row values for protocol-specific upsert operations."""
        return _csv.prepare_csv_host_row(raw_row, host_type, now_ts, row_number, add_warning)

    def _normalize_csv_column_name(self, key: str) -> str:
        """Normalize CSV column names to lowercase snake_case."""
        return _csv.normalize_csv_column_name(key)

    def _normalize_host_type(self, raw_value: Optional[str]) -> Optional[str]:
        """Map host type aliases to canonical S/F/H; default to S when blank."""
        return _csv.normalize_host_type(raw_value)

    def _coerce_db_timestamp(self, raw_value: Any, fallback: str) -> str:
        """Normalize timestamps to canonical DB format, falling back safely."""
        return _csv.coerce_db_timestamp(raw_value, fallback)

    def _coerce_int(self, raw_value: Any, default: int, minimum: Optional[int] = None) -> int:
        """Best-effort integer coercion with lower-bound guard."""
        return _csv.coerce_int(raw_value, default, minimum)

    def _coerce_bool(self, raw_value: Any, default: bool = False) -> bool:
        """Best-effort boolean coercion for CSV text values."""
        return _csv.coerce_bool(raw_value, default)

    def _upsert_csv_smb_row(
        self,
        conn: sqlite3.Connection,
        row: Dict[str, Any],
        strategy: MergeConflictStrategy,
    ) -> Tuple[int, int, int]:
        """Upsert one SMB CSV row according to conflict strategy."""
        return _csv.upsert_csv_smb_row(conn, row, strategy, self._parse_timestamp)

    def _upsert_csv_ftp_row(
        self,
        conn: sqlite3.Connection,
        row: Dict[str, Any],
        strategy: MergeConflictStrategy,
    ) -> Tuple[int, int, int]:
        """Upsert one FTP CSV row according to conflict strategy."""
        return _csv.upsert_csv_ftp_row(conn, row, strategy, self._parse_timestamp)

    def _upsert_csv_http_row(
        self,
        conn: sqlite3.Connection,
        row: Dict[str, Any],
        strategy: MergeConflictStrategy,
    ) -> Tuple[int, int, int]:
        """Upsert one HTTP CSV row according to conflict strategy."""
        return _csv.upsert_csv_http_row(conn, row, strategy, self._parse_timestamp)

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
        return _maint.create_backup(self.current_db_path, backup_dir)

    def _check_disk_space(self, required_bytes: int, path: str) -> bool:
        """Check if sufficient disk space is available."""
        return _maint._check_disk_space(required_bytes, path)

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
        return _db_merge.create_import_session(conn, source_filename)

    def _finalize_import_session(self, conn: sqlite3.Connection, session_id: int, total_targets: int):
        """Update the import session with final statistics (legacy-column aware)."""
        return _db_merge.finalize_import_session(conn, session_id, total_targets)

    def _parse_timestamp(self, ts_str: Optional[str]) -> datetime:
        """Parse timestamp string, returning MIN_DATE for NULL/invalid."""
        return _db_merge.parse_timestamp(ts_str, min_date=MIN_DATE)

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        """Return True if the given table exists in the database."""
        return _db_merge.table_exists(conn, table_name)

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set:
        """Return the column-name set for a table, or empty set when absent."""
        return _db_merge.table_columns(conn, table_name)

    def _table_has_required_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        required_columns: set,
    ) -> bool:
        """Return True when a table exists and includes all required columns."""
        return _db_merge.table_has_required_columns(conn, table_name, required_columns)

    def _validate_current_merge_schema(self, conn: sqlite3.Connection) -> List[str]:
        """Validate target DB core tables/columns required for merge writes."""
        required_specs = [
            ("scan_sessions", REQUIRED_SCAN_SESSION_TARGET_COLUMNS),
            ("smb_servers", REQUIRED_SMB_SERVER_TARGET_COLUMNS),
            ("share_access", REQUIRED_SHARE_ACCESS_TARGET_COLUMNS),
        ]
        return _db_merge.validate_current_merge_schema(conn, required_specs)

    def _merge_servers(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        strategy: MergeConflictStrategy,
        progress: Callable[[int, str], None]
    ) -> Tuple[Dict[str, int], Dict[int, int]]:
        """Merge servers from external DB into current DB."""
        return _db_merge.merge_servers(
            ext_conn, cur_conn, strategy, progress,
            parse_ts_fn=self._parse_timestamp,
            keep_newer=MergeConflictStrategy.KEEP_NEWER,
            keep_source=MergeConflictStrategy.KEEP_SOURCE,
            batch_size=BATCH_SIZE,
        )

    def _merge_ftp_servers(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        strategy: MergeConflictStrategy,
    ) -> Tuple[Dict[str, int], Dict[int, int]]:
        """Merge ftp_servers rows by ip_address."""
        return _db_merge.merge_ftp_servers(
            ext_conn, cur_conn, strategy,
            parse_ts_fn=self._parse_timestamp,
            keep_newer=MergeConflictStrategy.KEEP_NEWER,
            keep_source=MergeConflictStrategy.KEEP_SOURCE,
            required_cols=REQUIRED_FTP_SERVER_COLUMNS,
        )

    def _merge_http_servers(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        strategy: MergeConflictStrategy,
    ) -> Tuple[Dict[str, int], Dict[int, int]]:
        """Merge http_servers rows by ip_address."""
        return _db_merge.merge_http_servers(
            ext_conn, cur_conn, strategy,
            parse_ts_fn=self._parse_timestamp,
            keep_newer=MergeConflictStrategy.KEEP_NEWER,
            keep_source=MergeConflictStrategy.KEEP_SOURCE,
            required_cols=REQUIRED_HTTP_SERVER_COLUMNS,
        )

    def _import_share_access(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import share_access records with deduplication."""
        return _db_merge.import_share_access(
            ext_conn, cur_conn, id_mapping, import_session_id,
            parse_ts_fn=self._parse_timestamp,
        )

    def _import_ftp_access(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int,
    ) -> int:
        """Import ftp_access summary rows with per-server latest-record deduplication."""
        return _db_merge.import_ftp_access(
            ext_conn, cur_conn, id_mapping, import_session_id,
            parse_ts_fn=self._parse_timestamp,
            required_read=REQUIRED_FTP_ACCESS_COLUMNS,
            required_target=REQUIRED_FTP_ACCESS_TARGET_COLUMNS,
        )

    def _import_http_access(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int,
    ) -> int:
        """Import http_access summary rows with per-server latest-record deduplication."""
        return _db_merge.import_http_access(
            ext_conn, cur_conn, id_mapping, import_session_id,
            parse_ts_fn=self._parse_timestamp,
            required_read=REQUIRED_HTTP_ACCESS_COLUMNS,
            required_target=REQUIRED_HTTP_ACCESS_TARGET_COLUMNS,
        )

    def _import_share_credentials(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import share_credentials records (has unique index, use INSERT OR IGNORE)."""
        return _db_merge.import_share_credentials(
            ext_conn, cur_conn, id_mapping, import_session_id,
            required_read=REQUIRED_SHARE_CREDENTIALS_COLUMNS,
            required_target=REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS,
        )

    def _import_file_manifests(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import file_manifests records with deduplication by (server_id, share_name, file_path)."""
        return _db_merge.import_file_manifests(
            ext_conn, cur_conn, id_mapping, import_session_id,
            parse_ts_fn=self._parse_timestamp,
            required_read=REQUIRED_FILE_MANIFEST_COLUMNS,
            required_target=REQUIRED_FILE_MANIFEST_TARGET_COLUMNS,
        )

    def _import_vulnerabilities(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import vulnerabilities records with deduplication by (server_id, vuln_type, cve_ids)."""
        return _db_merge.import_vulnerabilities(
            ext_conn, cur_conn, id_mapping, import_session_id,
            required_read=REQUIRED_VULNERABILITY_COLUMNS,
            required_target=REQUIRED_VULNERABILITY_TARGET_COLUMNS,
        )

    def _import_failure_logs(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import failure_logs records (keyed by ip_address, not server_id)."""
        return _db_merge.import_failure_logs(
            ext_conn, cur_conn, id_mapping, import_session_id,
            required_read=REQUIRED_FAILURE_LOG_COLUMNS,
            required_target=REQUIRED_FAILURE_LOG_TARGET_COLUMNS,
        )

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
        return _maint.export_database(self.current_db_path, output_path, progress_callback)

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
        return _maint.quick_backup(self.current_db_path, backup_dir, progress_callback)

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_database_stats(self) -> DatabaseStats:
        """
        Gather statistics about the current database.

        Returns:
            DatabaseStats with all metrics
        """
        return _maint.get_database_stats(self.current_db_path, DatabaseStats)

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
        return _maint.vacuum_database(self.current_db_path, progress_callback)

    def integrity_check(self) -> Dict[str, Any]:
        """
        Run SQLite integrity check on the database.

        Returns:
            Dictionary with integrity check result
        """
        return _maint.integrity_check(self.current_db_path)

    def preview_purge(self, older_than_days: int) -> PurgePreview:
        """
        Preview what would be deleted by a purge operation.

        Args:
            older_than_days: Delete servers not seen in this many days

        Returns:
            PurgePreview with counts of affected records
        """
        return _maint.preview_purge(self.current_db_path, older_than_days, PurgePreview)

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
        return _maint.execute_purge(self.current_db_path, older_than_days, PurgePreview, progress_callback)


def get_db_tools_engine(db_path: str) -> DBToolsEngine:
    """
    Factory function to create a DBToolsEngine instance.

    Args:
        db_path: Path to the database file

    Returns:
        DBToolsEngine instance
    """
    return DBToolsEngine(db_path)
