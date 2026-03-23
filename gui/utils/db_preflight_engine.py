"""
SMBSeek GUI - Database Preflight Engine

Schema requirements and validation/preview logic for external database operations.
Constants are defined here for line-budget centralization; db_tools_engine.py
re-imports them all so every existing consumer continues to work unchanged.
"""

import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Required tables for schema validation.
# These are the minimum core tables required for safe SMB merge/import.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Free functions (extracted from DBToolsEngine for line-budget)
# ---------------------------------------------------------------------------

def validate_external_schema(external_db_path: str, schema_factory) -> Any:
    """
    Validate that an external database has a compatible schema.

    Args:
        external_db_path: Path to the external database to validate
        schema_factory: Callable that returns a SchemaValidation-like result object

    Returns:
        SchemaValidation with validation results
    """
    result = schema_factory(valid=True)

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

        def validate_required_columns(table_name: str, required_columns: set) -> None:
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


def preview_merge(
    external_db_path: str,
    current_db_path: str,
    schema_factory,
    table_columns_fn: Callable,
    table_has_required_columns_fn: Callable,
) -> Dict[str, Any]:
    """
    Preview what would happen if the external database was merged.

    Args:
        external_db_path: Path to the external database
        current_db_path: Path to the current (target) database
        schema_factory: Callable that returns a SchemaValidation-like result object
        table_columns_fn: Callable(conn, table_name) -> set of column names
        table_has_required_columns_fn: Callable(conn, table_name, required_cols) -> bool

    Returns:
        Dictionary with preview statistics
    """
    validation = validate_external_schema(external_db_path, schema_factory)
    if not validation.valid:
        return {
            'valid': False,
            'errors': validation.errors
        }

    try:
        ext_conn = sqlite3.connect(f"file:{external_db_path}?mode=ro", uri=True)
        ext_conn.row_factory = sqlite3.Row

        cur_conn = sqlite3.connect(f"file:{current_db_path}?mode=ro", uri=True)
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
                target_columns = table_columns_fn(cur_conn, table_name)
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
                target_columns = table_columns_fn(cur_conn, table_name)
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
                target_columns = table_columns_fn(cur_conn, table_name)
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
                if not table_has_required_columns_fn(cur_conn, table_name, REQUIRED_FTP_SERVER_COLUMNS):
                    continue
            if table_name == "http_servers":
                if not table_has_required_columns_fn(cur_conn, table_name, REQUIRED_HTTP_SERVER_COLUMNS):
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


def preview_csv_import(
    csv_path: str,
    current_db_path: str,
    analyze_csv_hosts_fn: Callable,
) -> Dict[str, Any]:
    """
    Preview CSV host import outcomes without writing to the database.

    CSV contract:
    - Required: ip_address
    - Optional: host_type (S/F/H; defaults to S), country, country_code,
      auth_method, first_seen, last_seen, scan_count, status, notes,
      port, anon_accessible, banner, scheme, title, shodan_data

    Args:
        csv_path: Path to the CSV file
        current_db_path: Path to the current (target) database
        analyze_csv_hosts_fn: Callable(csv_path, conn, include_rows) -> analysis dict
    """
    if not os.path.exists(csv_path):
        return {
            'valid': False,
            'errors': [f"CSV file not found: {csv_path}"]
        }

    try:
        conn = sqlite3.connect(f"file:{current_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        analysis = analyze_csv_hosts_fn(csv_path, conn, include_rows=False)
        conn.close()

        if analysis['errors']:
            return {
                'valid': False,
                'errors': analysis['errors'],
            }

        return {
            'valid': True,
            'total_rows': analysis['rows_total'],
            'valid_rows': analysis['rows_valid'],
            'skipped_rows': analysis['rows_skipped'],
            'new_servers': analysis['new_servers'],
            'existing_servers': analysis['existing_servers'],
            'protocol_counts': analysis['protocol_counts'],
            'warnings': analysis['warnings'],
        }

    except Exception as e:
        return {
            'valid': False,
            'errors': [str(e)]
        }
