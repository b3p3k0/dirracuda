"""
Dirracuda - Database Tools Engine

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
REQUIRED_FTP_SERVER_TARGET_COLUMNS = REQUIRED_FTP_SERVER_COLUMNS | {'id'}
REQUIRED_HTTP_SERVER_TARGET_COLUMNS = REQUIRED_HTTP_SERVER_COLUMNS | {'id'}

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
    Database tools engine for Dirracuda.

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


from gui.utils.db_tools_engine_core_methods import bind_db_tools_engine_core_methods
from gui.utils.db_tools_engine_merge_methods import bind_db_tools_engine_merge_methods
from gui.utils.db_tools_engine_maintenance_methods import bind_db_tools_engine_maintenance_methods

_SHARED_BIND_SYMBOLS: Dict[str, Any] = {
    "MIN_DATE": MIN_DATE,
    "BATCH_SIZE": BATCH_SIZE,
    "REQUIRED_TABLES": REQUIRED_TABLES,
    "REQUIRED_SERVER_COLUMNS": REQUIRED_SERVER_COLUMNS,
    "REQUIRED_SHARE_ACCESS_COLUMNS": REQUIRED_SHARE_ACCESS_COLUMNS,
    "REQUIRED_FTP_SERVER_COLUMNS": REQUIRED_FTP_SERVER_COLUMNS,
    "REQUIRED_HTTP_SERVER_COLUMNS": REQUIRED_HTTP_SERVER_COLUMNS,
    "REQUIRED_FTP_ACCESS_COLUMNS": REQUIRED_FTP_ACCESS_COLUMNS,
    "REQUIRED_HTTP_ACCESS_COLUMNS": REQUIRED_HTTP_ACCESS_COLUMNS,
    "REQUIRED_SHARE_CREDENTIALS_COLUMNS": REQUIRED_SHARE_CREDENTIALS_COLUMNS,
    "REQUIRED_FILE_MANIFEST_COLUMNS": REQUIRED_FILE_MANIFEST_COLUMNS,
    "REQUIRED_VULNERABILITY_COLUMNS": REQUIRED_VULNERABILITY_COLUMNS,
    "REQUIRED_FAILURE_LOG_COLUMNS": REQUIRED_FAILURE_LOG_COLUMNS,
    "REQUIRED_FTP_ACCESS_TARGET_COLUMNS": REQUIRED_FTP_ACCESS_TARGET_COLUMNS,
    "REQUIRED_HTTP_ACCESS_TARGET_COLUMNS": REQUIRED_HTTP_ACCESS_TARGET_COLUMNS,
    "REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS": REQUIRED_SHARE_CREDENTIALS_TARGET_COLUMNS,
    "REQUIRED_FILE_MANIFEST_TARGET_COLUMNS": REQUIRED_FILE_MANIFEST_TARGET_COLUMNS,
    "REQUIRED_VULNERABILITY_TARGET_COLUMNS": REQUIRED_VULNERABILITY_TARGET_COLUMNS,
    "REQUIRED_FAILURE_LOG_TARGET_COLUMNS": REQUIRED_FAILURE_LOG_TARGET_COLUMNS,
    "REQUIRED_FTP_SERVER_TARGET_COLUMNS": REQUIRED_FTP_SERVER_TARGET_COLUMNS,
    "REQUIRED_HTTP_SERVER_TARGET_COLUMNS": REQUIRED_HTTP_SERVER_TARGET_COLUMNS,
    "REQUIRED_SCAN_SESSION_TARGET_COLUMNS": REQUIRED_SCAN_SESSION_TARGET_COLUMNS,
    "REQUIRED_SMB_SERVER_TARGET_COLUMNS": REQUIRED_SMB_SERVER_TARGET_COLUMNS,
    "REQUIRED_SHARE_ACCESS_TARGET_COLUMNS": REQUIRED_SHARE_ACCESS_TARGET_COLUMNS,
    "MergeConflictStrategy": MergeConflictStrategy,
    "MergeResult": MergeResult,
    "DatabaseStats": DatabaseStats,
    "PurgePreview": PurgePreview,
    "SchemaValidation": SchemaValidation,
    "CSVImportResult": CSVImportResult,
    "normalize_db_timestamp": normalize_db_timestamp,
}

bind_db_tools_engine_core_methods(DBToolsEngine, _SHARED_BIND_SYMBOLS)
bind_db_tools_engine_merge_methods(DBToolsEngine, _SHARED_BIND_SYMBOLS)
bind_db_tools_engine_maintenance_methods(DBToolsEngine, _SHARED_BIND_SYMBOLS)

def get_db_tools_engine(db_path: str) -> DBToolsEngine:
    """
    Factory function to create a DBToolsEngine instance.

    Args:
        db_path: Path to the database file

    Returns:
        DBToolsEngine instance
    """
    return DBToolsEngine(db_path)
