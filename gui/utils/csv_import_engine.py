"""
SMBSeek GUI - CSV Import Engine

Pure helper functions for the CSV host import pipeline, extracted from
DBToolsEngine to reduce module size.  All behaviour is identical to the
original methods; this module only re-hosts the logic.

Public API consumed by DBToolsEngine adapters:
    normalize_csv_column_name, normalize_host_type
    coerce_db_timestamp, coerce_int, coerce_bool
    read_csv_host_records
    analyze_csv_hosts
    prepare_csv_host_row
    upsert_csv_smb_row, upsert_csv_ftp_row, upsert_csv_http_row

This module has no imports from the parent engine module; protocol column
sets are received as the ``protocol_specs`` parameter to avoid circular
dependencies.
"""

import csv
import logging
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from shared.config import normalize_db_timestamp

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private schema helpers (used only by analyze_csv_hosts)
# ---------------------------------------------------------------------------

def _table_exists_csv(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if *table_name* exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns_csv(conn: sqlite3.Connection, table_name: str) -> Set[str]:
    """Return the column-name set for a table, or empty set when absent."""
    if not _table_exists_csv(conn, table_name):
        return set()

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return set()

    first = rows[0]
    if isinstance(first, sqlite3.Row):
        return {row["name"] for row in rows}
    return {row[1] for row in rows}


# ---------------------------------------------------------------------------
# Pure normalization helpers (no DB access)
# ---------------------------------------------------------------------------

def normalize_csv_column_name(key: str) -> str:
    """Normalize CSV column names to lowercase snake_case."""
    return key.strip().lower().replace('-', '_').replace(' ', '_')


def normalize_host_type(raw_value: Optional[str]) -> Optional[str]:
    """Map host type aliases to canonical S/F/H; default to S when blank.

    Returns None for unrecognised values so that the caller's invalid-row
    skip logic is preserved.
    """
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


def coerce_db_timestamp(raw_value: Any, fallback: str) -> str:
    """Normalize timestamps to canonical DB format, falling back safely."""
    normalized = normalize_db_timestamp(raw_value)
    if isinstance(normalized, str) and normalized.strip():
        return normalized[:19]
    return fallback


def coerce_int(raw_value: Any, default: int, minimum: Optional[int] = None) -> int:
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


def coerce_bool(raw_value: Any, default: bool = False) -> bool:
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


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def read_csv_host_records(csv_path: str) -> List[Tuple[int, Dict[str, str]]]:
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
            normalize_csv_column_name(header)
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
                norm_key = normalize_csv_column_name(key)
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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def analyze_csv_hosts(
    csv_path: str,
    conn: sqlite3.Connection,
    protocol_specs: Dict[str, Tuple[str, Set[str]]],
    include_rows: bool,
) -> Dict[str, Any]:
    """
    Parse and validate CSV host rows against current runtime schema.

    ``protocol_specs`` maps each canonical host type letter ('S', 'F', 'H')
    to a ``(table_name, required_columns_set)`` tuple.  Assembled by the
    DBToolsEngine adapter from module-level REQUIRED_* constants.

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

    protocol_support: Dict[str, Dict[str, Any]] = {}
    existing_ip_sets: Dict[str, set] = {'S': set(), 'F': set(), 'H': set()}
    for host_type, (table_name, required_columns) in protocol_specs.items():
        columns = _table_columns_csv(conn, table_name)
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
        records = read_csv_host_records(csv_path)
    except Exception as e:
        analysis['errors'].append(str(e))
        return analysis

    if not records:
        analysis['errors'].append("CSV file has no data rows.")
        return analysis

    seen_in_file: Dict[str, set] = {'S': set(), 'F': set(), 'H': set()}
    unsupported_rows: Dict[str, int] = {'S': 0, 'F': 0, 'H': 0}

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for row_number, raw_row in records:
        analysis['rows_total'] += 1

        ip_address = (raw_row.get('ip_address') or '').strip()
        if not ip_address:
            analysis['rows_skipped'] += 1
            add_warning(f"Row {row_number}: missing ip_address; row skipped.")
            continue

        host_type = normalize_host_type(
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

        prepared = prepare_csv_host_row(raw_row, host_type, now_ts, row_number, add_warning)
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


# ---------------------------------------------------------------------------
# Row normalization
# ---------------------------------------------------------------------------

def prepare_csv_host_row(
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

    last_seen = coerce_db_timestamp(last_seen_raw, now_ts)
    first_seen = coerce_db_timestamp(first_seen_raw, last_seen)

    scan_count = coerce_int(raw_row.get('scan_count'), default=1, minimum=1)
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
        prepared['port'] = coerce_int(raw_port, default=21, minimum=1)
        if raw_port and prepared['port'] == 21 and raw_port not in ('21', '21.0'):
            add_warning(f"Row {row_number}: invalid FTP port '{raw_port}'; using 21.")

        prepared['anon_accessible'] = coerce_bool(
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
        prepared['port'] = coerce_int(raw_port, default=default_port, minimum=1)
        if raw_port and prepared['port'] == default_port and raw_port not in (str(default_port), f"{default_port}.0"):
            add_warning(
                f"Row {row_number}: invalid HTTP port '{raw_port}'; using {default_port}."
            )

    return prepared


# ---------------------------------------------------------------------------
# Protocol upserts
# ---------------------------------------------------------------------------

# Strategy value constants — mirrors MergeConflictStrategy enum values;
# using .value string comparison avoids any import of the parent engine module.
_STRATEGY_KEEP_SOURCE = 'keep_source'
_STRATEGY_KEEP_NEWER = 'keep_newer'


def _should_update(strategy: Any, source_time: datetime, current_time: datetime) -> bool:
    """Return True when the conflict strategy calls for overwriting current data."""
    v = strategy.value
    return v == _STRATEGY_KEEP_SOURCE or (v == _STRATEGY_KEEP_NEWER and source_time > current_time)


def upsert_csv_smb_row(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: Any,
    parse_ts_fn: Callable[[str], datetime],
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
    source_time = parse_ts_fn(row['last_seen'])
    current_time = parse_ts_fn(current_last_seen)
    if not _should_update(strategy, source_time, current_time):
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


def upsert_csv_ftp_row(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: Any,
    parse_ts_fn: Callable[[str], datetime],
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
    source_time = parse_ts_fn(row['last_seen'])
    current_time = parse_ts_fn(current_last_seen)
    if not _should_update(strategy, source_time, current_time):
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


def upsert_csv_http_row(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    strategy: Any,
    parse_ts_fn: Callable[[str], datetime],
) -> Tuple[int, int, int]:
    """Upsert one HTTP CSV row according to conflict strategy."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, last_seen FROM http_servers WHERE ip_address = ?",
        (row['ip_address'],),
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
            row['port'],
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
    source_time = parse_ts_fn(row['last_seen'])
    current_time = parse_ts_fn(current_last_seen)
    if not _should_update(strategy, source_time, current_time):
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
         WHERE ip_address = ?
    """, (
        row['country'],
        row['country_code'],
        row['port'],
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
    ))
    return 0, 1, 0
