"""Core/schema/CSV DBToolsEngine methods extracted from db_tools_engine.py."""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.config import normalize_db_timestamp

_logger = logging.getLogger(__name__)

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
    if not os.path.exists(csv_path):
        return {
            'valid': False,
            'errors': [f"CSV file not found: {csv_path}"]
        }

    try:
        conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        analysis = self._analyze_csv_hosts(csv_path, conn, include_rows=False)
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

def import_csv_hosts(
    self,
    csv_path: str,
    strategy=None,
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
    if strategy is None:
        strategy = MergeConflictStrategy.KEEP_NEWER

    start_time = time.time()
    result = CSVImportResult(success=False, protocol_counts={'S': 0, 'F': 0, 'H': 0})

    def progress(pct: int, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    if not os.path.exists(csv_path):
        result.errors.append(f"CSV file not found: {csv_path}")
        return result

    try:
        progress(0, "Preparing CSV import...")

        if auto_backup:
            progress(2, "Creating backup...")
            backup_result = self.create_backup()
            if backup_result['success']:
                result.backup_path = backup_result['backup_path']
            else:
                result.warnings.append(
                    f"Backup failed: {backup_result.get('error', 'Unknown error')}"
                )

        db_size = os.path.getsize(self.current_db_path)
        if not self._check_disk_space(db_size * 2, os.path.dirname(self.current_db_path)):
            result.errors.append("Insufficient disk space for CSV import")
            return result

        conn = sqlite3.connect(self.current_db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            progress(10, "Analyzing CSV rows...")
            analysis = self._analyze_csv_hosts(csv_path, conn, include_rows=True)
            result.rows_total = analysis['rows_total']
            result.rows_valid = analysis['rows_valid']
            result.rows_skipped = analysis['rows_skipped']
            result.protocol_counts = analysis['protocol_counts']
            result.warnings.extend(analysis['warnings'])

            if analysis['errors']:
                result.errors.extend(analysis['errors'])
                return result

            rows_to_import = analysis['rows']
            if not rows_to_import:
                result.errors.append("No valid CSV rows to import.")
                return result

            conn.execute("BEGIN IMMEDIATE")

            progress(20, "Creating import session...")
            import_session_id = self._create_import_session(
                conn,
                os.path.basename(csv_path)
            )

            total = len(rows_to_import)
            for i, row in enumerate(rows_to_import):
                host_type = row['host_type']
                if host_type == 'S':
                    added, updated, skipped = self._upsert_csv_smb_row(conn, row, strategy)
                elif host_type == 'F':
                    added, updated, skipped = self._upsert_csv_ftp_row(conn, row, strategy)
                elif host_type == 'H':
                    added, updated, skipped = self._upsert_csv_http_row(conn, row, strategy)
                else:
                    # Defensive: should never happen after validation.
                    result.rows_skipped += 1
                    continue

                result.servers_added += added
                result.servers_updated += updated
                result.servers_skipped += skipped

                if i % 50 == 0:
                    pct = 25 + int(((i + 1) / total) * 65)
                    progress(min(pct, 90), f"Importing row {i + 1}/{total}...")

            progress(92, "Finalizing import session...")
            self._finalize_import_session(
                conn,
                import_session_id,
                result.servers_added + result.servers_updated,
            )

            conn.commit()
            result.success = True
            progress(100, "CSV import completed successfully")

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except Exception as e:
        _logger.exception("CSV import failed")
        result.errors.append(str(e))

    result.duration_seconds = time.time() - start_time
    return result

def _read_csv_host_records(self, csv_path: str) -> List[Tuple[int, Dict[str, str]]]:
    """Read CSV rows with normalized header keys; skip comment-only lines."""
    records: List[Tuple[int, Dict[str, str]]] = []
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as csvfile:
        filtered_lines = (
            line for line in csvfile
            if not line.lstrip().startswith('#')
        )
        reader = csv.DictReader(filtered_lines)
        if not reader.fieldnames:
            raise ValueError("CSV file is missing a header row.")

        normalized_headers = {
            self._normalize_csv_column_name(header)
            for header in reader.fieldnames
            if header
        }
        if 'ip_address' not in normalized_headers:
            raise ValueError("Missing required CSV column: ip_address")

        for row_number, row in enumerate(reader, start=2):
            normalized_row: Dict[str, str] = {}
            for key, value in (row or {}).items():
                if key is None:
                    continue
                norm_key = self._normalize_csv_column_name(key)
                if isinstance(value, str):
                    normalized_row[norm_key] = value.strip()
                elif value is None:
                    normalized_row[norm_key] = ''
                else:
                    normalized_row[norm_key] = str(value)

            # Ignore fully empty rows.
            if not any(v for v in normalized_row.values()):
                continue
            records.append((row_number, normalized_row))

    return records

def _analyze_csv_hosts(
    self,
    csv_path: str,
    conn: sqlite3.Connection,
    include_rows: bool,
) -> Dict[str, Any]:
    """
    Parse and validate CSV host rows against current runtime schema.

    Returns analysis dictionary with summary counts, warnings, and parsed rows.
    """
    analysis: Dict[str, Any] = {
        'rows_total': 0,
        'rows_valid': 0,
        'rows_skipped': 0,
        'new_servers': 0,
        'existing_servers': 0,
        'protocol_counts': {'S': 0, 'F': 0, 'H': 0},
        'warnings': [],
        'errors': [],
        'rows': [],
    }

    warnings = analysis['warnings']
    dropped_warnings = 0
    max_warnings = 25

    def add_warning(message: str) -> None:
        nonlocal dropped_warnings
        if len(warnings) < max_warnings:
            warnings.append(message)
        else:
            dropped_warnings += 1

    protocol_specs = {
        'S': ('smb_servers', REQUIRED_SMB_SERVER_TARGET_COLUMNS),
        'F': ('ftp_servers', REQUIRED_FTP_SERVER_TARGET_COLUMNS),
        'H': ('http_servers', REQUIRED_HTTP_SERVER_TARGET_COLUMNS),
    }

    protocol_support: Dict[str, Dict[str, Any]] = {}
    existing_ip_sets: Dict[str, set[str]] = {'S': set(), 'F': set(), 'H': set()}
    for host_type, (table_name, required_columns) in protocol_specs.items():
        columns = self._table_columns(conn, table_name)
        if not columns:
            protocol_support[host_type] = {
                'supported': False,
                'reason': f"Target DB missing table {table_name}",
            }
            continue

        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            protocol_support[host_type] = {
                'supported': False,
                'reason': (
                    f"Target table {table_name} missing required columns: "
                    f"{', '.join(missing_columns)}"
                ),
            }
            continue

        protocol_support[host_type] = {'supported': True, 'reason': ''}
        rows = conn.execute(f"SELECT ip_address FROM {table_name}").fetchall()
        existing_ip_sets[host_type] = {row['ip_address'] for row in rows}

    try:
        records = self._read_csv_host_records(csv_path)
    except Exception as e:
        analysis['errors'].append(str(e))
        return analysis

    if not records:
        analysis['errors'].append("CSV file has no data rows.")
        return analysis

    seen_in_file: Dict[str, set[str]] = {'S': set(), 'F': set(), 'H': set()}
    unsupported_rows: Dict[str, int] = {'S': 0, 'F': 0, 'H': 0}

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for row_number, raw_row in records:
        analysis['rows_total'] += 1

        ip_address = (raw_row.get('ip_address') or '').strip()
        if not ip_address:
            analysis['rows_skipped'] += 1
            add_warning(f"Row {row_number}: missing ip_address; row skipped.")
            continue

        host_type = self._normalize_host_type(
            raw_row.get('host_type')
            or raw_row.get('protocol')
            or raw_row.get('type')
        )
        if host_type is None:
            analysis['rows_skipped'] += 1
            add_warning(
                f"Row {row_number}: invalid host_type '{raw_row.get('host_type', '')}'; "
                "expected S/F/H."
            )
            continue

        support = protocol_support[host_type]
        if not support['supported']:
            analysis['rows_skipped'] += 1
            unsupported_rows[host_type] += 1
            continue

        prepared = self._prepare_csv_host_row(raw_row, host_type, now_ts, row_number, add_warning)
        analysis['rows_valid'] += 1
        analysis['protocol_counts'][host_type] += 1

        if (
            ip_address in existing_ip_sets[host_type]
            or ip_address in seen_in_file[host_type]
        ):
            analysis['existing_servers'] += 1
        else:
            analysis['new_servers'] += 1
            seen_in_file[host_type].add(ip_address)

        if include_rows:
            analysis['rows'].append(prepared)

    for host_type, skipped_count in unsupported_rows.items():
        if skipped_count == 0:
            continue
        reason = protocol_support[host_type]['reason']
        add_warning(
            f"Skipped {skipped_count} {host_type} rows: {reason}."
        )

    if dropped_warnings:
        warnings.append(f"{dropped_warnings} additional warnings omitted.")

    return analysis

def _prepare_csv_host_row(
    self,
    raw_row: Dict[str, str],
    host_type: str,
    now_ts: str,
    row_number: int,
    add_warning: Callable[[str], None],
) -> Dict[str, Any]:
    """Normalize CSV row values for protocol-specific upsert operations."""
    last_seen_raw = (
        raw_row.get('last_seen')
        or raw_row.get('timestamp')
        or raw_row.get('updated_at')
    )
    first_seen_raw = raw_row.get('first_seen') or raw_row.get('created_at')

    last_seen = self._coerce_db_timestamp(last_seen_raw, now_ts)
    first_seen = self._coerce_db_timestamp(first_seen_raw, last_seen)

    scan_count = self._coerce_int(raw_row.get('scan_count'), default=1, minimum=1)
    if raw_row.get('scan_count') and scan_count == 1 and raw_row.get('scan_count') != '1':
        add_warning(f"Row {row_number}: invalid scan_count '{raw_row.get('scan_count')}'; using 1.")

    country_code = (raw_row.get('country_code') or '').upper() or None
    status = raw_row.get('status') or 'active'
    notes = raw_row.get('notes') or None
    shodan_data = raw_row.get('shodan_data') or None
    banner = raw_row.get('banner') or None

    prepared: Dict[str, Any] = {
        'host_type': host_type,
        'ip_address': (raw_row.get('ip_address') or '').strip(),
        'country': raw_row.get('country') or None,
        'country_code': country_code,
        'first_seen': first_seen,
        'last_seen': last_seen,
        'scan_count': scan_count,
        'status': status,
        'notes': notes,
        'shodan_data': shodan_data,
        'banner': banner,
        'auth_method': raw_row.get('auth_method') or None,
        'port': None,
        'anon_accessible': False,
        'scheme': None,
        'title': raw_row.get('title') or None,
    }

    if host_type == 'F':
        raw_port = raw_row.get('port')
        prepared['port'] = self._coerce_int(raw_port, default=21, minimum=1)
        if raw_port and prepared['port'] == 21 and raw_port not in ('21', '21.0'):
            add_warning(f"Row {row_number}: invalid FTP port '{raw_port}'; using 21.")

        prepared['anon_accessible'] = self._coerce_bool(
            raw_row.get('anon_accessible') or raw_row.get('anonymous'),
            default=False,
        )

    if host_type == 'H':
        raw_scheme = (raw_row.get('scheme') or 'http').strip().lower()
        if raw_scheme not in ('http', 'https'):
            add_warning(f"Row {row_number}: invalid scheme '{raw_scheme}'; using http.")
            raw_scheme = 'http'
        prepared['scheme'] = raw_scheme

        raw_port = raw_row.get('port')
        default_port = 443 if raw_scheme == 'https' else 80
        prepared['port'] = self._coerce_int(raw_port, default=default_port, minimum=1)
        if raw_port and prepared['port'] == default_port and raw_port not in (str(default_port), f"{default_port}.0"):
            add_warning(
                f"Row {row_number}: invalid HTTP port '{raw_port}'; using {default_port}."
            )

    return prepared

def _normalize_csv_column_name(self, key: str) -> str:
    """Normalize CSV column names to lowercase snake_case."""
    return key.strip().lower().replace('-', '_').replace(' ', '_')

def _normalize_host_type(self, raw_value: Optional[str]) -> Optional[str]:
    """Map host type aliases to canonical S/F/H; default to S when blank."""
    if raw_value is None:
        return 'S'
    value = str(raw_value).strip().upper()
    if not value:
        return 'S'

    mapping = {
        'S': 'S',
        'SMB': 'S',
        'F': 'F',
        'FTP': 'F',
        'H': 'H',
        'HTTP': 'H',
        'HTTPS': 'H',
    }
    return mapping.get(value)

def _coerce_db_timestamp(self, raw_value: Any, fallback: str) -> str:
    """Normalize timestamps to canonical DB format, falling back safely."""
    normalized = normalize_db_timestamp(raw_value)
    if isinstance(normalized, str) and normalized.strip():
        return normalized[:19]
    return fallback

def _coerce_int(self, raw_value: Any, default: int, minimum: Optional[int] = None) -> int:
    """Best-effort integer coercion with lower-bound guard."""
    if raw_value is None:
        return default
    if isinstance(raw_value, str) and not raw_value.strip():
        return default
    try:
        value = int(float(str(raw_value).strip()))
    except Exception:
        return default
    if minimum is not None and value < minimum:
        return default
    return value

def _coerce_bool(self, raw_value: Any, default: bool = False) -> bool:
    """Best-effort boolean coercion for CSV text values."""
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if not value:
        return default
    if value in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return default

def _upsert_csv_smb_row(
    self,
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: MergeConflictStrategy,
) -> Tuple[int, int, int]:
    """Upsert one SMB CSV row according to conflict strategy."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, last_seen FROM smb_servers WHERE ip_address = ?",
        (row['ip_address'],),
    )
    current = cursor.fetchone()
    if current is None:
        cursor.execute("""
            INSERT INTO smb_servers (
                ip_address, country, country_code, auth_method,
                shodan_data, first_seen, last_seen, scan_count, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row['ip_address'],
            row['country'],
            row['country_code'],
            row['auth_method'],
            row['shodan_data'],
            row['first_seen'],
            row['last_seen'],
            row['scan_count'],
            row['status'],
            row['notes'],
        ))
        return 1, 0, 0

    current_last_seen = current['last_seen'] if isinstance(current, sqlite3.Row) else current[1]
    source_time = self._parse_timestamp(row['last_seen'])
    current_time = self._parse_timestamp(current_last_seen)
    should_update = (
        strategy == MergeConflictStrategy.KEEP_SOURCE
        or (strategy == MergeConflictStrategy.KEEP_NEWER and source_time > current_time)
    )
    if not should_update:
        return 0, 0, 1

    cursor.execute("""
        UPDATE smb_servers
           SET country = ?,
               country_code = ?,
               auth_method = ?,
               shodan_data = ?,
               first_seen = ?,
               last_seen = ?,
               scan_count = ?,
               status = ?,
               notes = ?
         WHERE ip_address = ?
    """, (
        row['country'],
        row['country_code'],
        row['auth_method'],
        row['shodan_data'],
        row['first_seen'],
        row['last_seen'],
        row['scan_count'],
        row['status'],
        row['notes'],
        row['ip_address'],
    ))
    return 0, 1, 0

def _upsert_csv_ftp_row(
    self,
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: MergeConflictStrategy,
) -> Tuple[int, int, int]:
    """Upsert one FTP CSV row according to conflict strategy."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, last_seen FROM ftp_servers WHERE ip_address = ?",
        (row['ip_address'],),
    )
    current = cursor.fetchone()
    if current is None:
        cursor.execute("""
            INSERT INTO ftp_servers (
                ip_address, country, country_code, port, anon_accessible,
                banner, shodan_data, first_seen, last_seen, scan_count, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row['ip_address'],
            row['country'],
            row['country_code'],
            row['port'],
            1 if row['anon_accessible'] else 0,
            row['banner'],
            row['shodan_data'],
            row['first_seen'],
            row['last_seen'],
            row['scan_count'],
            row['status'],
            row['notes'],
        ))
        return 1, 0, 0

    current_last_seen = current['last_seen'] if isinstance(current, sqlite3.Row) else current[1]
    source_time = self._parse_timestamp(row['last_seen'])
    current_time = self._parse_timestamp(current_last_seen)
    should_update = (
        strategy == MergeConflictStrategy.KEEP_SOURCE
        or (strategy == MergeConflictStrategy.KEEP_NEWER and source_time > current_time)
    )
    if not should_update:
        return 0, 0, 1

    cursor.execute("""
        UPDATE ftp_servers
           SET country = ?,
               country_code = ?,
               port = ?,
               anon_accessible = ?,
               banner = ?,
               shodan_data = ?,
               first_seen = ?,
               last_seen = ?,
               scan_count = ?,
               status = ?,
               notes = ?
         WHERE ip_address = ?
    """, (
        row['country'],
        row['country_code'],
        row['port'],
        1 if row['anon_accessible'] else 0,
        row['banner'],
        row['shodan_data'],
        row['first_seen'],
        row['last_seen'],
        row['scan_count'],
        row['status'],
        row['notes'],
        row['ip_address'],
    ))
    return 0, 1, 0

def _upsert_csv_http_row(
    self,
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: MergeConflictStrategy,
) -> Tuple[int, int, int]:
    """Upsert one HTTP CSV row according to conflict strategy."""
    cursor = conn.cursor()
    try:
        endpoint_port = int(row.get('port') if row.get('port') not in (None, "") else 80)
    except (TypeError, ValueError):
        endpoint_port = 80
    cursor.execute(
        "SELECT id, last_seen FROM http_servers WHERE ip_address = ? AND port = ?",
        (row['ip_address'], endpoint_port),
    )
    current = cursor.fetchone()
    if current is None:
        cursor.execute("""
            INSERT INTO http_servers (
                ip_address, country, country_code, port, scheme, banner, title,
                shodan_data, first_seen, last_seen, scan_count, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row['ip_address'],
            row['country'],
            row['country_code'],
            endpoint_port,
            row['scheme'] or 'http',
            row['banner'],
            row['title'],
            row['shodan_data'],
            row['first_seen'],
            row['last_seen'],
            row['scan_count'],
            row['status'],
            row['notes'],
        ))
        return 1, 0, 0

    current_last_seen = current['last_seen'] if isinstance(current, sqlite3.Row) else current[1]
    source_time = self._parse_timestamp(row['last_seen'])
    current_time = self._parse_timestamp(current_last_seen)
    should_update = (
        strategy == MergeConflictStrategy.KEEP_SOURCE
        or (strategy == MergeConflictStrategy.KEEP_NEWER and source_time > current_time)
    )
    if not should_update:
        return 0, 0, 1

    cursor.execute("""
        UPDATE http_servers
           SET country = ?,
               country_code = ?,
               port = ?,
               scheme = ?,
               banner = ?,
               title = ?,
               shodan_data = ?,
               first_seen = ?,
               last_seen = ?,
               scan_count = ?,
               status = ?,
               notes = ?
         WHERE ip_address = ? AND port = ?
    """, (
        row['country'],
        row['country_code'],
        endpoint_port,
        row['scheme'] or 'http',
        row['banner'],
        row['title'],
        row['shodan_data'],
        row['first_seen'],
        row['last_seen'],
        row['scan_count'],
        row['status'],
        row['notes'],
        row['ip_address'],
        endpoint_port,
    ))
    return 0, 1, 0

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



def bind_db_tools_engine_core_methods(engine_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted core/schema/csv methods onto DBToolsEngine."""
    globals().update(shared_symbols)
    method_names = (
        "validate_external_schema",
        "preview_merge",
        "preview_csv_import",
        "import_csv_hosts",
        "_read_csv_host_records",
        "_analyze_csv_hosts",
        "_prepare_csv_host_row",
        "_normalize_csv_column_name",
        "_normalize_host_type",
        "_coerce_db_timestamp",
        "_coerce_int",
        "_coerce_bool",
        "_upsert_csv_smb_row",
        "_upsert_csv_ftp_row",
        "_upsert_csv_http_row",
        "create_backup",
        "_check_disk_space",
    )
    for name in method_names:
        setattr(engine_cls, name, globals()[name])
