"""
SMBSeek GUI - Database Tools Engine

Business logic for database management operations including import/merge,
export/backup, statistics, and maintenance. Separated from UI for testability.

Design Decision: All database operations are centralized here to ensure
data integrity and provide consistent behavior. The merge algorithm handles
duplicate IPs by comparing last_seen timestamps.
"""

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
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

# Required tables for schema validation
REQUIRED_TABLES = {'smb_servers', 'scan_sessions'}

# Required columns in smb_servers for merge
REQUIRED_SERVER_COLUMNS = {'ip_address', 'country', 'auth_method', 'last_seen', 'first_seen'}


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
                result.missing_tables = list(missing_tables)
                result.errors.append(f"Missing required tables: {', '.join(missing_tables)}")

            # Check required columns in smb_servers
            if 'smb_servers' in existing_tables:
                cursor.execute("PRAGMA table_info(smb_servers)")
                existing_columns = {row['name'] for row in cursor.fetchall()}

                missing_columns = REQUIRED_SERVER_COLUMNS - existing_columns
                if missing_columns:
                    result.valid = False
                    result.missing_columns = list(missing_columns)
                    result.errors.append(f"Missing required columns in smb_servers: {', '.join(missing_columns)}")

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

            # Get external server IPs
            ext_cursor = ext_conn.cursor()
            ext_cursor.execute("SELECT ip_address, last_seen FROM smb_servers")
            ext_servers = {row['ip_address']: row['last_seen'] for row in ext_cursor.fetchall()}

            # Get current server IPs
            cur_cursor = cur_conn.cursor()
            cur_cursor.execute("SELECT ip_address, last_seen FROM smb_servers")
            cur_servers = {row['ip_address']: row['last_seen'] for row in cur_cursor.fetchall()}

            # Calculate overlap
            new_ips = set(ext_servers.keys()) - set(cur_servers.keys())
            existing_ips = set(ext_servers.keys()) & set(cur_servers.keys())

            # Count related records in external DB
            ext_cursor.execute("SELECT COUNT(*) FROM share_access")
            total_shares = ext_cursor.fetchone()[0]

            ext_cursor.execute("SELECT COUNT(*) FROM vulnerabilities")
            total_vulns = ext_cursor.fetchone()[0]

            ext_cursor.execute("SELECT COUNT(*) FROM file_manifests")
            total_files = ext_cursor.fetchone()[0]

            ext_conn.close()
            cur_conn.close()

            return {
                'valid': True,
                'external_servers': len(ext_servers),
                'new_servers': len(new_ips),
                'existing_servers': len(existing_ips),
                'total_shares': total_shares,
                'total_vulnerabilities': total_vulns,
                'total_file_manifests': total_files
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

        try:
            shutil.copy2(self.current_db_path, backup_path)
            return {
                'success': True,
                'backup_path': backup_path,
                'size_bytes': os.path.getsize(backup_path)
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

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

            try:
                # Phase 2: Create import session
                progress(8, "Creating import session...")
                import_session_id = self._create_import_session(
                    cur_conn,
                    os.path.basename(external_db_path)
                )

                # Phase 3: Merge servers
                progress(10, "Merging servers...")
                server_stats, id_mapping = self._merge_servers(
                    ext_conn, cur_conn, strategy, progress
                )
                result.servers_added = server_stats['added']
                result.servers_updated = server_stats['updated']
                result.servers_skipped = server_stats['skipped']

                # Phase 4: Import related data
                progress(50, "Importing share access records...")
                result.shares_imported = self._import_share_access(
                    ext_conn, cur_conn, id_mapping, import_session_id
                )

                progress(60, "Importing share credentials...")
                result.credentials_imported = self._import_share_credentials(
                    ext_conn, cur_conn, id_mapping, import_session_id
                )

                progress(70, "Importing file manifests...")
                result.file_manifests_imported = self._import_file_manifests(
                    ext_conn, cur_conn, id_mapping, import_session_id
                )

                progress(80, "Importing vulnerabilities...")
                result.vulnerabilities_imported = self._import_vulnerabilities(
                    ext_conn, cur_conn, id_mapping, import_session_id
                )

                # Phase 5: Import failure logs
                progress(90, "Importing failure logs...")
                result.failure_logs_imported = self._import_failure_logs(
                    ext_conn, cur_conn, id_mapping, import_session_id
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

            finally:
                ext_conn.close()
                cur_conn.close()

        except Exception as e:
            _logger.exception("Merge operation failed")
            result.errors.append(str(e))

        result.duration_seconds = time.time() - start_time
        return result

    def _create_import_session(self, conn: sqlite3.Connection, source_filename: str) -> int:
        """Create a scan session record for the import operation."""
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scan_sessions (
                tool_name, scan_type, status, notes, timestamp, started_at
            ) VALUES (
                'smbseek', 'db_import', 'running',
                ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """, (f"Imported from: {source_filename}",))
        return cursor.lastrowid

    def _finalize_import_session(self, conn: sqlite3.Connection, session_id: int, total_targets: int):
        """Update the import session with final statistics."""
        conn.execute("""
            UPDATE scan_sessions
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                total_targets = ?,
                successful_targets = ?
            WHERE id = ?
        """, (total_targets, total_targets, session_id))

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
        ext_cursor.execute("""
            SELECT id, ip_address, country, country_code, auth_method,
                   shodan_data, first_seen, last_seen, scan_count, status, notes
            FROM smb_servers
            ORDER BY last_seen DESC
        """)
        ext_servers = ext_cursor.fetchall()

        cur_cursor = cur_conn.cursor()
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
                    cur_cursor.execute("""
                        UPDATE smb_servers SET
                            last_seen = ?,
                            auth_method = COALESCE(?, auth_method),
                            country = COALESCE(?, country),
                            country_code = COALESCE(?, country_code),
                            scan_count = scan_count + ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        normalize_db_timestamp(ext_row['last_seen']),
                        ext_row['auth_method'],
                        ext_row['country'],
                        ext_row['country_code'],
                        ext_row['scan_count'] or 0,
                        cur_id
                    ))
                    stats['updated'] += 1
                else:
                    stats['skipped'] += 1

            # Batch commit and progress update
            if (i + 1) % BATCH_SIZE == 0:
                cur_conn.commit()
                pct = 10 + int((i / total) * 40)  # 10-50% for server merge
                progress(pct, f"Merged {i + 1}/{total} servers...")

        cur_conn.commit()
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

        cur_conn.commit()
        return imported

    def _import_share_credentials(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import share_credentials records (has unique index, use INSERT OR IGNORE)."""
        imported = 0
        ext_cursor = ext_conn.cursor()
        cur_cursor = cur_conn.cursor()

        # Check if table exists in external DB
        ext_cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='share_credentials'"
        )
        if not ext_cursor.fetchone():
            return 0

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

        cur_conn.commit()
        return imported

    def _import_file_manifests(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import file_manifests records with deduplication by (server_id, share_name, file_path)."""
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

        cur_conn.commit()
        return imported

    def _import_vulnerabilities(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import vulnerabilities records with deduplication by (server_id, vuln_type, cve_ids)."""
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

        cur_conn.commit()
        return imported

    def _import_failure_logs(
        self,
        ext_conn: sqlite3.Connection,
        cur_conn: sqlite3.Connection,
        id_mapping: Dict[int, int],
        import_session_id: int
    ) -> int:
        """Import failure_logs records (keyed by ip_address, not server_id)."""
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

        cur_conn.commit()
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

            # Server counts
            cursor.execute("SELECT COUNT(*) FROM smb_servers")
            stats.total_servers = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM smb_servers WHERE status = 'active'")
            stats.active_servers = cursor.fetchone()[0]

            # Share counts
            cursor.execute("SELECT COUNT(*) FROM share_access")
            stats.total_shares = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM share_access WHERE accessible = 1")
            stats.accessible_shares = cursor.fetchone()[0]

            # Other counts
            cursor.execute("SELECT COUNT(*) FROM vulnerabilities")
            stats.total_vulnerabilities = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM file_manifests")
            stats.total_file_manifests = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM scan_sessions")
            stats.total_sessions = cursor.fetchone()[0]

            # Check if share_credentials exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='share_credentials'"
            )
            if cursor.fetchone():
                cursor.execute("SELECT COUNT(*) FROM share_credentials")
                stats.total_credentials = cursor.fetchone()[0]

            # Date range
            cursor.execute("SELECT MIN(first_seen) FROM smb_servers")
            row = cursor.fetchone()
            stats.oldest_record = row[0] if row and row[0] else None

            cursor.execute("SELECT MAX(last_seen) FROM smb_servers")
            row = cursor.fetchone()
            stats.newest_record = row[0] if row and row[0] else None

            # Country distribution
            cursor.execute("""
                SELECT country, COUNT(*) as cnt
                FROM smb_servers
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
        cutoff = cutoff.replace(day=cutoff.day - older_than_days if cutoff.day > older_than_days else 1)
        cutoff_str = cutoff.strftime('%Y-%m-%d')
        preview.cutoff_date = cutoff_str

        if not os.path.exists(self.current_db_path):
            return preview

        try:
            conn = sqlite3.connect(f"file:{self.current_db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Count servers to delete
            cursor.execute("""
                SELECT COUNT(*) FROM smb_servers
                WHERE date(last_seen) < date(?)
            """, (cutoff_str,))
            preview.servers_to_delete = cursor.fetchone()[0]

            if preview.servers_to_delete > 0:
                # Get IDs of servers to delete
                cursor.execute("""
                    SELECT id FROM smb_servers
                    WHERE date(last_seen) < date(?)
                """, (cutoff_str,))
                server_ids = tuple(row['id'] for row in cursor.fetchall())
                placeholders = ','.join('?' * len(server_ids))

                # Count related records (CASCADE will delete these)
                cursor.execute(f"SELECT COUNT(*) FROM share_access WHERE server_id IN ({placeholders})", server_ids)
                preview.shares_to_delete = cursor.fetchone()[0]

                # Check if share_credentials exists
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='share_credentials'"
                )
                if cursor.fetchone():
                    cursor.execute(f"SELECT COUNT(*) FROM share_credentials WHERE server_id IN ({placeholders})", server_ids)
                    preview.credentials_to_delete = cursor.fetchone()[0]

                cursor.execute(f"SELECT COUNT(*) FROM file_manifests WHERE server_id IN ({placeholders})", server_ids)
                preview.file_manifests_to_delete = cursor.fetchone()[0]

                cursor.execute(f"SELECT COUNT(*) FROM vulnerabilities WHERE server_id IN ({placeholders})", server_ids)
                preview.vulnerabilities_to_delete = cursor.fetchone()[0]

                cursor.execute(f"SELECT COUNT(*) FROM host_user_flags WHERE server_id IN ({placeholders})", server_ids)
                preview.user_flags_to_delete = cursor.fetchone()[0]

                cursor.execute(f"SELECT COUNT(*) FROM host_probe_cache WHERE server_id IN ({placeholders})", server_ids)
                preview.probe_cache_to_delete = cursor.fetchone()[0]

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

            # Delete servers (CASCADE handles related records)
            cursor.execute("""
                DELETE FROM smb_servers
                WHERE date(last_seen) < date(?)
            """, (preview.cutoff_date,))

            deleted = cursor.rowcount
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
