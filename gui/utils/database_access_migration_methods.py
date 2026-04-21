"""Migration/report write methods extracted from database_access_write_methods.py."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional


def get_migration_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
    """Return app_migration_state value for key, or default when missing."""
    if not key:
        return default
    try:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_migration_state WHERE key = ?",
                (key,),
            ).fetchone()
            if row and row["value"] is not None:
                return str(row["value"])
    except sqlite3.OperationalError:
        return default
    return default


def set_migration_state(self, key: str, value: Optional[str]) -> None:
    """Upsert app_migration_state key/value."""
    if not key:
        return
    try:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_migration_state (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def append_migration_report(
    self,
    migration_name: str,
    source: str,
    reason_code: str,
    *,
    item_key: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Append one migration report row for skipped/error/summary outcomes."""
    if not migration_name or not source or not reason_code:
        return
    try:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_migration_reports
                    (migration_name, source, item_key, reason_code, detail, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (migration_name, source, item_key, reason_code, detail),
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def upsert_extract_run_summary(
    self,
    summary: Dict[str, Any],
    *,
    ip_address: Optional[str] = None,
    host_type: str = "S",
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
    source: str = "extract_runner",
) -> Optional[int]:
    """Persist extraction summary metadata to extract_run_summaries table."""
    if not isinstance(summary, dict):
        return None
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    ip_value = str(ip_address or summary.get("ip_address") or "").strip()
    if not ip_value:
        return None
    host_type = (host_type or "S").upper()
    if host_type not in ("S", "F", "H"):
        host_type = "S"
    try:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO extract_run_summaries
                    (ip_address, host_type, protocol_server_id, port,
                     started_at, finished_at, stop_reason, timed_out,
                     files_downloaded, bytes_downloaded, files_skipped, errors_count,
                     clamav_summary_json, summary_json, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    ip_value,
                    host_type,
                    protocol_server_id,
                    port,
                    summary.get("started_at"),
                    summary.get("finished_at"),
                    summary.get("stop_reason"),
                    1 if summary.get("timed_out") else 0,
                    int(totals.get("files_downloaded") or 0),
                    int(totals.get("bytes_downloaded") or 0),
                    int(totals.get("files_skipped") or 0),
                    len(summary.get("errors") or []),
                    json.dumps(summary.get("clamav"), default=str)
                    if summary.get("clamav") is not None
                    else None,
                    json.dumps(summary, default=str),
                    source,
                ),
            )
            conn.commit()
            return int(cur.lastrowid) if cur.lastrowid else None
    except sqlite3.OperationalError:
        return None


def bind_database_access_migration_methods(reader_cls, _shared_symbols: Optional[Dict[str, Any]] = None) -> None:
    """Attach migration/report methods onto DatabaseReader."""
    for name in (
        "get_migration_state",
        "set_migration_state",
        "append_migration_report",
        "upsert_extract_run_summary",
    ):
        setattr(reader_cls, name, globals()[name])
