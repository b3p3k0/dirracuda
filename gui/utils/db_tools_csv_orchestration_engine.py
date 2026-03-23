"""
SMBSeek GUI - CSV Import Orchestration Engine

Orchestrates the import_csv_hosts pipeline: backup, disk-space check,
CSV analysis, transactional row upserts, progress reporting, and session
finalization.

Extracted from DBToolsEngine to allow focused testing and reduce module size.
All logic is a verbatim move; behavior is unchanged.
"""

import os
import sqlite3
import time
import logging
from typing import Any, Callable, Optional


def import_csv_hosts(
    engine: Any,
    csv_path: str,
    strategy: Any,
    auto_backup: bool,
    progress_callback: Optional[Callable[[int, str], None]],
    result_factory: Callable[..., Any],
    logger: logging.Logger,
) -> Any:
    """
    Import protocol-aware host rows from CSV into SMB/FTP/HTTP server tables.

    Rows are written per protocol table based on host_type:
    - S -> smb_servers
    - F -> ftp_servers
    - H -> http_servers
    """
    start_time = time.time()
    result = result_factory(success=False, protocol_counts={'S': 0, 'F': 0, 'H': 0})

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
            backup_result = engine.create_backup()
            if backup_result['success']:
                result.backup_path = backup_result['backup_path']
            else:
                result.warnings.append(
                    f"Backup failed: {backup_result.get('error', 'Unknown error')}"
                )

        db_size = os.path.getsize(engine.current_db_path)
        if not engine._check_disk_space(db_size * 2, os.path.dirname(engine.current_db_path)):
            result.errors.append("Insufficient disk space for CSV import")
            return result

        conn = sqlite3.connect(engine.current_db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            progress(10, "Analyzing CSV rows...")
            analysis = engine._analyze_csv_hosts(csv_path, conn, include_rows=True)
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
            import_session_id = engine._create_import_session(
                conn,
                os.path.basename(csv_path)
            )

            total = len(rows_to_import)
            for i, row in enumerate(rows_to_import):
                host_type = row['host_type']
                if host_type == 'S':
                    added, updated, skipped = engine._upsert_csv_smb_row(conn, row, strategy)
                elif host_type == 'F':
                    added, updated, skipped = engine._upsert_csv_ftp_row(conn, row, strategy)
                elif host_type == 'H':
                    added, updated, skipped = engine._upsert_csv_http_row(conn, row, strategy)
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
            engine._finalize_import_session(
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
        logger.exception("CSV import failed")
        result.errors.append(str(e))

    result.duration_seconds = time.time() - start_time
    return result
