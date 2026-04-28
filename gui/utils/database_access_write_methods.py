"""Write and legacy-read methods extracted from database_access.py."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from error_codes import get_error, format_error_message
except ImportError:
    from .error_codes import get_error, format_error_message

def _resolve_protocol_server_id(
    self,
    cur: sqlite3.Cursor,
    *,
    ip_address: str,
    host_type: str,
    server_table: str,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> Optional[int]:
    """
    Resolve authoritative server_id for protocol-aware writes.

    Resolution order:
    1. Explicit protocol_server_id (if present and exists)
    2. HTTP endpoint key (ip + port) when host_type='H' and port is provided
    3. Legacy fallback by ip_address (most recent row)
    """
    if protocol_server_id is not None:
        try:
            psid = int(protocol_server_id)
        except (TypeError, ValueError):
            psid = None
        if psid is not None:
            cur.execute(f"SELECT id FROM {server_table} WHERE id = ?", (psid,))
            row = cur.fetchone()
            if row:
                return int(row["id"])

    if host_type == "H" and port is not None and ip_address:
        try:
            endpoint_port = int(port)
        except (TypeError, ValueError):
            endpoint_port = None
        if endpoint_port is not None:
            cur.execute(
                "SELECT id FROM http_servers WHERE ip_address = ? AND port = ? "
                "ORDER BY last_seen DESC, id DESC LIMIT 1",
                (ip_address, endpoint_port),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])

    if ip_address:
        cur.execute(
            f"SELECT id FROM {server_table} WHERE ip_address = ? "
            "ORDER BY last_seen DESC, id DESC LIMIT 1",
            (ip_address,),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])

    return None

# --- Write helpers for GUI flags/probe cache -------------------------

def upsert_user_flags(self, ip_address: str, *, favorite: Optional[bool] = None,
                      avoid: Optional[bool] = None, notes: Optional[str] = None) -> None:
    """SMB-compatible shim. Delegates to upsert_user_flags_for_host with host_type='S'."""
    self.upsert_user_flags_for_host(ip_address, 'S', favorite=favorite, avoid=avoid, notes=notes)

def upsert_probe_cache(self, ip_address: str, *, status: str, indicator_matches: int,
                       snapshot_path: Optional[str] = None) -> None:
    """SMB-compatible shim. Delegates to upsert_probe_cache_for_host with host_type='S'."""
    self.upsert_probe_cache_for_host(ip_address, 'S', status=status,
                                     indicator_matches=indicator_matches,
                                     snapshot_path=snapshot_path)

def upsert_extracted_flag(self, ip_address: str, extracted: bool = True) -> None:
    """SMB-compatible shim. Delegates to upsert_extracted_flag_for_host with host_type='S'."""
    self.upsert_extracted_flag_for_host(ip_address, 'S', extracted=extracted)

# --- Protocol-aware write helpers (dual-protocol routing) ----------------

def upsert_user_flags_for_host(self, ip_address: str, host_type: str, *,
                                favorite: Optional[bool] = None,
                                avoid: Optional[bool] = None,
                                notes: Optional[str] = None,
                                protocol_server_id: Optional[int] = None,
                                port: Optional[int] = None) -> None:
    """Route favorite/avoid/notes write to SMB or FTP tables based on host_type.

    Args:
        ip_address: IP address of the host
        host_type: 'S' for SMB (writes host_user_flags), 'F' for FTP (writes ftp_user_flags)
        favorite: Set favorite flag, or None to leave unchanged
        avoid: Set avoid flag, or None to leave unchanged
        notes: Set notes text, or None to leave unchanged

    No-op for invalid host_type or unknown IP.
    FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
    """
    host_type = (host_type or "").upper()
    if not ip_address or host_type not in ('S', 'F', 'H'):
        return
    if host_type == 'S':
        server_table = 'smb_servers'
        flags_table  = 'host_user_flags'
    elif host_type == 'F':
        server_table = 'ftp_servers'
        flags_table  = 'ftp_user_flags'
    else:
        server_table = 'http_servers'
        flags_table  = 'http_user_flags'
    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return
            cur.execute(
                f"SELECT favorite, avoid, notes FROM {flags_table} WHERE server_id = ?",
                (server_id,),
            )
            existing  = cur.fetchone()
            fav_val   = existing["favorite"] if existing else 0
            avoid_val = existing["avoid"]    if existing else 0
            notes_val = existing["notes"]    if existing else ""
            if favorite is not None:
                fav_val = 1 if favorite else 0
            if avoid is not None:
                avoid_val = 1 if avoid else 0
            if notes is not None:
                notes_val = notes
            cur.execute(
                f"""
                INSERT INTO {flags_table} (server_id, favorite, avoid, notes, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    favorite=excluded.favorite,
                    avoid=excluded.avoid,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, fav_val, avoid_val, notes_val),
            )
            conn.commit()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if host_type == 'F' and "no such table: ftp_" in msg:
            return  # FTP tables absent — migration not yet run; degrade gracefully
        if host_type == 'H' and "no such table: http_" in msg:
            return  # HTTP tables absent — migration not yet run; degrade gracefully
        raise
    self.clear_cache()


def _iter_snapshot_entries(snapshot: Dict[str, Any]):
    """Yield normalized entry records from probe snapshot payload."""
    shares = snapshot.get("shares") if isinstance(snapshot, dict) else None
    if not isinstance(shares, list):
        return
    for share in shares:
        if not isinstance(share, dict):
            continue
        share_name = str(share.get("share") or "")
        root_files = share.get("root_files")
        if isinstance(root_files, list):
            for name in root_files:
                if not name:
                    continue
                path = str(name)
                yield {
                    "share_name": share_name,
                    "entry_kind": "root_file",
                    "path": path,
                    "parent_path": "",
                    "is_truncated": 1 if share.get("root_files_truncated") else 0,
                    "metadata_json": None,
                }

        directories = share.get("directories")
        if isinstance(directories, list):
            for directory in directories:
                if isinstance(directory, str):
                    dir_path = directory
                    subdirs = []
                    files = []
                    dir_truncated = False
                    file_truncated = False
                elif isinstance(directory, dict):
                    dir_path = str(directory.get("path") or directory.get("directory") or "")
                    subdirs = directory.get("subdirectories")
                    files = directory.get("files")
                    dir_truncated = bool(directory.get("subdirectories_truncated"))
                    file_truncated = bool(directory.get("files_truncated"))
                else:
                    continue

                if not dir_path:
                    continue

                yield {
                    "share_name": share_name,
                    "entry_kind": "directory",
                    "path": dir_path,
                    "parent_path": "",
                    "is_truncated": 1 if share.get("directories_truncated") else 0,
                    "metadata_json": None,
                }

                if isinstance(subdirs, list):
                    for sub in subdirs:
                        sub_name = str(sub or "").strip()
                        if not sub_name:
                            continue
                        yield {
                            "share_name": share_name,
                            "entry_kind": "subdirectory",
                            "path": f"{dir_path.rstrip('/')}/{sub_name}",
                            "parent_path": dir_path,
                            "is_truncated": 1 if dir_truncated else 0,
                            "metadata_json": None,
                        }

                if isinstance(files, list):
                    for item in files:
                        file_name = str(item or "").strip()
                        if not file_name:
                            continue
                        yield {
                            "share_name": share_name,
                            "entry_kind": "file",
                            "path": f"{dir_path.rstrip('/')}/{file_name}",
                            "parent_path": dir_path,
                            "is_truncated": 1 if file_truncated else 0,
                            "metadata_json": None,
                        }


def _iter_snapshot_errors(snapshot: Dict[str, Any]):
    """Yield normalized error records from probe snapshot payload."""
    errors = snapshot.get("errors") if isinstance(snapshot, dict) else None
    if not isinstance(errors, list):
        return
    for err in errors:
        if isinstance(err, dict):
            share_name = str(err.get("share") or "")
            message = str(err.get("message") or "").strip()
        else:
            share_name = ""
            message = str(err or "").strip()
        if not message:
            continue
        yield {"share_name": share_name, "message": message}


def upsert_probe_snapshot_for_host(
    self,
    ip_address: str,
    host_type: str,
    snapshot: Dict[str, Any],
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
    source: str = "runtime",
) -> Optional[int]:
    """
    Persist one probe snapshot payload into normalized tables and return snapshot_id.

    Idempotency is hash-based on canonicalized snapshot JSON, so repeated writes
    for identical payloads resolve to the same snapshot row.
    """
    host_type = (host_type or "S").upper()
    if not ip_address or host_type not in ("S", "F", "H") or not isinstance(snapshot, dict):
        return None

    endpoint_port: Optional[int] = None
    if host_type == "H":
        raw_port = port if port is not None else snapshot.get("port")
        try:
            endpoint_port = int(raw_port) if raw_port is not None else None
        except (TypeError, ValueError):
            endpoint_port = None
    elif host_type == "F":
        raw_port = port if port is not None else snapshot.get("port")
        try:
            endpoint_port = int(raw_port) if raw_port is not None else 21
        except (TypeError, ValueError):
            endpoint_port = 21

    payload_text = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    run_at = snapshot.get("run_at")

    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO probe_snapshots
                    (snapshot_hash, host_type, ip_address, port, protocol_server_id,
                     run_at, source, raw_snapshot_json, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(snapshot_hash) DO UPDATE SET
                    host_type=excluded.host_type,
                    ip_address=excluded.ip_address,
                    port=COALESCE(excluded.port, probe_snapshots.port),
                    protocol_server_id=COALESCE(excluded.protocol_server_id, probe_snapshots.protocol_server_id),
                    run_at=COALESCE(excluded.run_at, probe_snapshots.run_at),
                    source=excluded.source,
                    raw_snapshot_json=excluded.raw_snapshot_json
                """,
                (
                    payload_hash,
                    host_type,
                    ip_address,
                    endpoint_port,
                    protocol_server_id,
                    run_at,
                    source,
                    payload_text,
                ),
            )
            row = cur.execute(
                "SELECT id FROM probe_snapshots WHERE snapshot_hash = ?",
                (payload_hash,),
            ).fetchone()
            if not row:
                return None
            snapshot_id = int(row["id"])

            # Keep child tables deterministic/idempotent by replacing rows for snapshot_id.
            cur.execute("DELETE FROM probe_snapshot_entries WHERE snapshot_id = ?", (snapshot_id,))
            cur.execute("DELETE FROM probe_snapshot_errors WHERE snapshot_id = ?", (snapshot_id,))
            cur.execute("DELETE FROM probe_snapshot_rce WHERE snapshot_id = ?", (snapshot_id,))

            entry_rows = list(_iter_snapshot_entries(snapshot) or [])
            if entry_rows:
                cur.executemany(
                    """
                    INSERT INTO probe_snapshot_entries
                        (snapshot_id, share_name, entry_kind, path, parent_path, is_truncated, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [
                        (
                            snapshot_id,
                            item.get("share_name"),
                            item.get("entry_kind"),
                            item.get("path"),
                            item.get("parent_path"),
                            int(item.get("is_truncated") or 0),
                            item.get("metadata_json"),
                        )
                        for item in entry_rows
                    ],
                )

            error_rows = list(_iter_snapshot_errors(snapshot) or [])
            if error_rows:
                cur.executemany(
                    """
                    INSERT INTO probe_snapshot_errors
                        (snapshot_id, share_name, message, created_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [
                        (
                            snapshot_id,
                            item.get("share_name"),
                            item.get("message"),
                        )
                        for item in error_rows
                    ],
                )

            rce = snapshot.get("rce_analysis")
            if isinstance(rce, dict) and rce:
                cur.execute(
                    """
                    INSERT INTO probe_snapshot_rce
                        (snapshot_id, rce_status, verdict_summary, analysis_json, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        snapshot_id,
                        rce.get("rce_status"),
                        json.dumps(rce.get("verdict_summary"), default=str)
                        if isinstance(rce.get("verdict_summary"), (dict, list))
                        else rce.get("verdict_summary"),
                        json.dumps(rce, default=str),
                    ),
                )

            conn.commit()
            return snapshot_id
    except sqlite3.OperationalError as exc:
        # Graceful behavior when migrations have not run yet.
        if "no such table: probe_snapshot" in str(exc).lower():
            return None
        raise


def get_probe_snapshot_for_host(
    self,
    ip_address: str,
    host_type: str,
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return latest probe snapshot payload for a host/endpoint from DB, or None."""
    host_type = (host_type or "S").upper()
    if not ip_address or host_type not in ("S", "F", "H"):
        return None

    if host_type == "S":
        server_table = "smb_servers"
        cache_table = "host_probe_cache"
    elif host_type == "F":
        server_table = "ftp_servers"
        cache_table = "ftp_probe_cache"
    else:
        server_table = "http_servers"
        cache_table = "http_probe_cache"

    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            snapshot_row = None
            if server_id is not None:
                snapshot_row = cur.execute(
                    f"SELECT latest_snapshot_id FROM {cache_table} WHERE server_id = ?",
                    (server_id,),
                ).fetchone()
            latest_snapshot_id = (
                int(snapshot_row["latest_snapshot_id"])
                if snapshot_row and snapshot_row["latest_snapshot_id"] is not None
                else None
            )
            if latest_snapshot_id is not None:
                payload_row = cur.execute(
                    "SELECT raw_snapshot_json FROM probe_snapshots WHERE id = ?",
                    (latest_snapshot_id,),
                ).fetchone()
                if payload_row and payload_row["raw_snapshot_json"]:
                    try:
                        return json.loads(payload_row["raw_snapshot_json"])
                    except Exception:
                        return None

            # Fallback when latest_snapshot_id is not yet attached.
            params: List[Any] = [host_type, ip_address]
            where_sql = "host_type = ? AND ip_address = ?"
            if host_type == "H" and port is not None:
                where_sql += " AND port = ?"
                params.append(int(port))
            payload_row = cur.execute(
                f"""
                SELECT raw_snapshot_json
                FROM probe_snapshots
                WHERE {where_sql}
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if payload_row and payload_row["raw_snapshot_json"]:
                try:
                    return json.loads(payload_row["raw_snapshot_json"])
                except Exception:
                    return None
            return None
    except sqlite3.OperationalError:
        return None


def set_latest_probe_snapshot_for_host(
    self,
    ip_address: str,
    host_type: str,
    latest_snapshot_id: Optional[int],
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> None:
    """Attach latest_snapshot_id on protocol probe cache row without mutating status fields."""
    host_type = (host_type or "S").upper()
    if not ip_address or host_type not in ("S", "F", "H") or latest_snapshot_id is None:
        return

    if host_type == "S":
        server_table = "smb_servers"
        cache_table = "host_probe_cache"
    elif host_type == "F":
        server_table = "ftp_servers"
        cache_table = "ftp_probe_cache"
    else:
        server_table = "http_servers"
        cache_table = "http_probe_cache"

    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return
            cur.execute(
                f"""
                INSERT INTO {cache_table} (server_id, latest_snapshot_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    latest_snapshot_id=excluded.latest_snapshot_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, int(latest_snapshot_id)),
            )
            conn.commit()
    except sqlite3.OperationalError:
        return
    self.clear_cache()

def upsert_probe_cache_for_host(self, ip_address: str, host_type: str, *,
                                 status: str,
                                 indicator_matches: int,
                                 snapshot_path: Optional[str] = None,
                                 latest_snapshot_id: Optional[int] = None,
                                 accessible_dirs_count: Optional[int] = None,
                                 accessible_dirs_list: Optional[str] = None,
                                 accessible_files_count: Optional[int] = None,
                                 protocol_server_id: Optional[int] = None,
                                 port: Optional[int] = None) -> None:
    """Route probe cache write to SMB, FTP, or HTTP tables based on host_type.

    Args:
        ip_address: IP address of the host
        host_type: 'S' for SMB (host_probe_cache), 'F' for FTP (ftp_probe_cache),
                   'H' for HTTP (http_probe_cache)
        status: Probe status string
        indicator_matches: Number of indicator matches found
        snapshot_path: Optional path to probe snapshot; existing value preserved when None
        latest_snapshot_id: Optional FK-like id into probe_snapshots; preserved when None
        accessible_dirs_count: FTP/HTTP accessible directory count
        accessible_dirs_list: FTP/HTTP comma-separated directory paths
        accessible_files_count: HTTP-only accessible file count

    No-op for invalid host_type or unknown IP.
    FTP/HTTP branches degrade gracefully when tables are absent (pre-migration).
    """
    host_type = (host_type or "").upper()
    if not ip_address or host_type not in ('S', 'F', 'H'):
        return
    if host_type == 'S':
        server_table = 'smb_servers'
        cache_table  = 'host_probe_cache'
    elif host_type == 'F':
        server_table = 'ftp_servers'
        cache_table  = 'ftp_probe_cache'
    else:
        server_table = 'http_servers'
        cache_table  = 'http_probe_cache'
    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return
            table_columns = {
                str(row[1]) for row in cur.execute(f"PRAGMA table_info({cache_table})").fetchall()
            }
            include_latest_snapshot = "latest_snapshot_id" in table_columns
            include_dirs_count = "accessible_dirs_count" in table_columns
            include_dirs_list = "accessible_dirs_list" in table_columns
            include_files_count = "accessible_files_count" in table_columns

            insert_columns = [
                "server_id",
                "status",
                "last_probe_at",
                "indicator_matches",
                "snapshot_path",
            ]
            insert_values = ["?", "?", "CURRENT_TIMESTAMP", "?", "?"]
            params: List[Any] = [server_id, status, indicator_matches, snapshot_path]
            update_parts = [
                "status=excluded.status",
                "last_probe_at=excluded.last_probe_at",
                "indicator_matches=excluded.indicator_matches",
                f"snapshot_path=COALESCE(excluded.snapshot_path, {cache_table}.snapshot_path)",
            ]

            if include_latest_snapshot:
                insert_columns.append("latest_snapshot_id")
                insert_values.append("?")
                params.append(latest_snapshot_id)
                update_parts.append(
                    f"latest_snapshot_id=COALESCE(excluded.latest_snapshot_id, {cache_table}.latest_snapshot_id)"
                )
            if host_type in ("F", "H") and include_dirs_count:
                insert_columns.append("accessible_dirs_count")
                insert_values.append("?")
                params.append(accessible_dirs_count)
                update_parts.append(
                    f"accessible_dirs_count=COALESCE(excluded.accessible_dirs_count, {cache_table}.accessible_dirs_count)"
                )
            if host_type in ("F", "H") and include_dirs_list:
                insert_columns.append("accessible_dirs_list")
                insert_values.append("?")
                params.append(accessible_dirs_list)
                update_parts.append(
                    f"accessible_dirs_list=COALESCE(excluded.accessible_dirs_list, {cache_table}.accessible_dirs_list)"
                )
            if host_type == "H" and include_files_count:
                insert_columns.append("accessible_files_count")
                insert_values.append("?")
                params.append(accessible_files_count)
                update_parts.append(
                    f"accessible_files_count=COALESCE(excluded.accessible_files_count, {cache_table}.accessible_files_count)"
                )

            insert_columns.append("updated_at")
            insert_values.append("CURRENT_TIMESTAMP")
            update_parts.append("updated_at=CURRENT_TIMESTAMP")
            cur.execute(
                f"""
                INSERT INTO {cache_table} ({", ".join(insert_columns)})
                VALUES ({", ".join(insert_values)})
                ON CONFLICT(server_id) DO UPDATE SET
                    {", ".join(update_parts)}
                """,
                tuple(params),
            )
            conn.commit()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if host_type == 'F' and "no such table: ftp_" in msg:
            return
        if host_type == 'H' and "no such table: http_" in msg:
            return
        raise
    self.clear_cache()

def upsert_extracted_flag_for_host(self, ip_address: str, host_type: str,
                                    extracted: bool = True,
                                    protocol_server_id: Optional[int] = None,
                                    port: Optional[int] = None) -> None:
    """Route extracted flag write to SMB or FTP tables based on host_type.

    Args:
        ip_address: IP address of the host
        host_type: 'S' for SMB (writes host_probe_cache), 'F' for FTP (writes ftp_probe_cache)
        extracted: True to mark as extracted, False to clear

    No-op for invalid host_type or unknown IP.
    FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
    """
    host_type = (host_type or "").upper()
    if not ip_address or host_type not in ('S', 'F', 'H'):
        return
    if host_type == 'S':
        server_table = 'smb_servers'
        cache_table  = 'host_probe_cache'
    elif host_type == 'F':
        server_table = 'ftp_servers'
        cache_table  = 'ftp_probe_cache'
    else:
        server_table = 'http_servers'
        cache_table  = 'http_probe_cache'
    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return
            cur.execute(
                f"""
                INSERT INTO {cache_table} (server_id, extracted, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    extracted=excluded.extracted,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, 1 if extracted else 0),
            )
            conn.commit()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if host_type == 'F' and "no such table: ftp_" in msg:
            return
        if host_type == 'H' and "no such table: http_" in msg:
            return
        raise
    self.clear_cache()

def upsert_rce_status_for_host(self, ip_address: str, host_type: str,
                                rce_status: str,
                                verdict_summary: Optional[str] = None,
                                protocol_server_id: Optional[int] = None,
                                port: Optional[int] = None) -> None:
    """Route RCE analysis status write to SMB or FTP tables based on host_type.

    Args:
        ip_address: IP address of the host
        host_type: 'S' for SMB (writes host_probe_cache), 'F' for FTP (writes ftp_probe_cache)
        rce_status: Status string ('not_run', 'clean', 'flagged', 'unknown', 'error');
                    invalid values are normalized to 'unknown'
        verdict_summary: Optional JSON summary of verdicts

    No-op for invalid host_type or unknown IP.
    FTP branch degrades gracefully when ftp_ tables are absent (pre-migration).
    """
    host_type = (host_type or "").upper()
    if not ip_address or host_type not in ('S', 'F', 'H'):
        return
    valid_statuses = {'not_run', 'clean', 'flagged', 'unknown', 'error'}
    if rce_status not in valid_statuses:
        rce_status = 'unknown'
    if host_type == 'S':
        server_table = 'smb_servers'
        cache_table  = 'host_probe_cache'
    elif host_type == 'F':
        server_table = 'ftp_servers'
        cache_table  = 'ftp_probe_cache'
    else:
        server_table = 'http_servers'
        cache_table  = 'http_probe_cache'
    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return
            cur.execute(
                f"""
                INSERT INTO {cache_table} (server_id, rce_status, rce_verdict_summary, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    rce_status=excluded.rce_status,
                    rce_verdict_summary=excluded.rce_verdict_summary,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, rce_status, verdict_summary),
            )
            conn.commit()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if host_type == 'F' and "no such table: ftp_" in msg:
            return
        if host_type == 'H' and "no such table: http_" in msg:
            return
        raise
    self.clear_cache()

def upsert_manual_server_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upsert one manually-entered protocol row into the active database.

    Conflict identity:
    - SMB:  ip_address
    - FTP:  ip_address
    - HTTP: (ip_address, port)

    Returns:
        Dict with host_type, protocol_server_id, row_key, operation.
    """
    if not isinstance(payload, dict):
        raise ValueError("Manual record payload must be a dictionary.")

    host_type = str(payload.get("host_type") or "").strip().upper()
    if host_type not in {"S", "F", "H"}:
        raise ValueError("host_type must be one of: S, F, H.")

    ip_raw = str(payload.get("ip_address") or "").strip()
    if not ip_raw:
        raise ValueError("ip_address is required.")
    try:
        ip_address = str(ipaddress.ip_address(ip_raw))
    except ValueError as exc:
        raise ValueError(f"Invalid ip_address: {ip_raw}") from exc

    def _blank_to_none(value: Any) -> Optional[str]:
        text = str(value).strip() if value is not None else ""
        return text if text else None

    country = _blank_to_none(payload.get("country"))
    country_code = _blank_to_none(payload.get("country_code"))
    if country_code is not None:
        country_code = country_code.upper()
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("country_code must be a 2-letter alphabetic code.")

    operation = "insert"
    protocol_server_id: Optional[int] = None

    with self._get_connection() as conn:
        cur = conn.cursor()

        if host_type == "S":
            auth_method = _blank_to_none(payload.get("auth_method"))
            existing = cur.execute(
                "SELECT id FROM smb_servers WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            operation = "update" if existing else "insert"

            cur.execute(
                """
                INSERT INTO smb_servers
                    (ip_address, country, country_code, auth_method, last_seen, status)
                VALUES
                    (?, ?, ?, ?, CURRENT_TIMESTAMP, 'active')
                ON CONFLICT(ip_address) DO UPDATE SET
                    country=COALESCE(excluded.country, smb_servers.country),
                    country_code=COALESCE(excluded.country_code, smb_servers.country_code),
                    auth_method=COALESCE(excluded.auth_method, smb_servers.auth_method),
                    status='active',
                    last_seen=CURRENT_TIMESTAMP
                """,
                (ip_address, country, country_code, auth_method),
            )
            row = cur.execute(
                "SELECT id FROM smb_servers WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            protocol_server_id = int(row["id"]) if row else None

        elif host_type == "F":
            port_raw = payload.get("port")
            port: Optional[int] = None
            if port_raw not in (None, ""):
                try:
                    port = int(port_raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError("FTP port must be an integer between 1 and 65535.") from exc
                if port < 1 or port > 65535:
                    raise ValueError("FTP port must be an integer between 1 and 65535.")

            existing = cur.execute(
                "SELECT id FROM ftp_servers WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            operation = "update" if existing else "insert"

            if port is None:
                cur.execute(
                    """
                    INSERT INTO ftp_servers
                        (ip_address, country, country_code, last_seen, status)
                    VALUES
                        (?, ?, ?, CURRENT_TIMESTAMP, 'active')
                    ON CONFLICT(ip_address) DO UPDATE SET
                        country=COALESCE(excluded.country, ftp_servers.country),
                        country_code=COALESCE(excluded.country_code, ftp_servers.country_code),
                        status='active',
                        last_seen=CURRENT_TIMESTAMP
                    """,
                    (ip_address, country, country_code),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO ftp_servers
                        (ip_address, country, country_code, port, last_seen, status)
                    VALUES
                        (?, ?, ?, ?, CURRENT_TIMESTAMP, 'active')
                    ON CONFLICT(ip_address) DO UPDATE SET
                        country=COALESCE(excluded.country, ftp_servers.country),
                        country_code=COALESCE(excluded.country_code, ftp_servers.country_code),
                        port=excluded.port,
                        status='active',
                        last_seen=CURRENT_TIMESTAMP
                    """,
                    (ip_address, country, country_code, port),
                )

            row = cur.execute(
                "SELECT id FROM ftp_servers WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            protocol_server_id = int(row["id"]) if row else None

        else:  # host_type == "H"
            port_raw = payload.get("port")
            if port_raw in (None, ""):
                port = 80
            else:
                try:
                    port = int(port_raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError("HTTP port must be an integer between 1 and 65535.") from exc
            if port < 1 or port > 65535:
                raise ValueError("HTTP port must be an integer between 1 and 65535.")

            scheme = _blank_to_none(payload.get("scheme"))
            if scheme is None:
                scheme = "http"
            scheme = scheme.lower()
            if scheme not in {"http", "https"}:
                raise ValueError("HTTP scheme must be either 'http' or 'https'.")

            probe_host = _blank_to_none(payload.get("probe_host"))
            probe_path = _blank_to_none(payload.get("probe_path"))
            if probe_path is not None:
                probe_path = probe_path.split("?", 1)[0].split("#", 1)[0].strip()
                if not probe_path:
                    probe_path = "/"
                elif not probe_path.startswith("/"):
                    probe_path = "/" + probe_path.lstrip("/")

            banner = _blank_to_none(payload.get("banner"))
            title = _blank_to_none(payload.get("title"))

            existing = cur.execute(
                "SELECT id FROM http_servers WHERE ip_address = ? AND port = ?",
                (ip_address, port),
            ).fetchone()
            operation = "update" if existing else "insert"

            def _upsert_http() -> None:
                cur.execute(
                    """
                    INSERT INTO http_servers
                        (
                            ip_address, country, country_code, port, scheme,
                            probe_host, probe_path, banner, title, last_seen, status
                        )
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'active')
                    ON CONFLICT(ip_address, port) DO UPDATE SET
                        country=COALESCE(excluded.country, http_servers.country),
                        country_code=COALESCE(excluded.country_code, http_servers.country_code),
                        scheme=COALESCE(excluded.scheme, http_servers.scheme),
                        probe_host=COALESCE(excluded.probe_host, http_servers.probe_host),
                        probe_path=COALESCE(excluded.probe_path, http_servers.probe_path),
                        banner=COALESCE(excluded.banner, http_servers.banner),
                        title=COALESCE(excluded.title, http_servers.title),
                        status='active',
                        last_seen=CURRENT_TIMESTAMP
                    """,
                    (
                        ip_address,
                        country,
                        country_code,
                        port,
                        scheme,
                        probe_host,
                        probe_path,
                        banner,
                        title,
                    ),
                )

            try:
                _upsert_http()
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                missing_probe_cols = (
                    "no column named probe_host" in msg
                    or "no such column: http_servers.probe_host" in msg
                    or "no such column: excluded.probe_host" in msg
                    or "no column named probe_path" in msg
                    or "no such column: http_servers.probe_path" in msg
                    or "no such column: excluded.probe_path" in msg
                )
                if not missing_probe_cols:
                    raise
                # One best-effort schema repair + one retry for legacy/minimal schemas.
                self._ensure_http_columns()
                _upsert_http()

            row = cur.execute(
                "SELECT id FROM http_servers WHERE ip_address = ? AND port = ?",
                (ip_address, port),
            ).fetchone()
            protocol_server_id = int(row["id"]) if row else None

        if protocol_server_id is None:
            raise RuntimeError("Unable to resolve protocol_server_id after upsert.")

        conn.commit()

    self.clear_cache()
    return {
        "host_type": host_type,
        "protocol_server_id": protocol_server_id,
        "row_key": f"{host_type}:{protocol_server_id}",
        "operation": operation,
    }

def bulk_delete_servers(self, ip_addresses: List[str]) -> Dict[str, Any]:
    """
    Bulk delete servers and cascade to related tables.

    Args:
        ip_addresses: List of IP addresses to delete

    Returns:
        Dict with:
        - 'deleted_count': Number of servers actually deleted (from rowcount)
        - 'deleted_ips': List of IPs successfully deleted (for probe cache cleanup)
        - 'error': Error message if operation failed (None on success)
    """
    if not ip_addresses:
        return {"deleted_count": 0, "deleted_ips": [], "error": None}

    try:
        # Deduplicate IPs
        unique_ips = list(set(ip_addresses))

        total_deleted_count = 0
        all_deleted_ips = []

        # Process in batches of 500 (SQLite limit: 999 parameters)
        batch_size = 500
        for i in range(0, len(unique_ips), batch_size):
            batch = unique_ips[i:i + batch_size]

            with self._get_connection() as conn:
                cur = conn.cursor()

                # Query existing IPs to find which ones actually exist
                placeholders = ','.join('?' * len(batch))
                query = f"SELECT id, ip_address FROM smb_servers WHERE ip_address IN ({placeholders})"
                cur.execute(query, batch)
                found_servers = cur.fetchall()

                if not found_servers:
                    # Nothing to delete in this batch
                    continue

                found_ips = [row["ip_address"] for row in found_servers]

                # Delete failure_logs explicitly (no CASCADE on this table)
                failure_placeholders = ','.join('?' * len(found_ips))
                delete_failures_query = f"DELETE FROM failure_logs WHERE ip_address IN ({failure_placeholders})"
                cur.execute(delete_failures_query, found_ips)

                # Delete servers (CASCADE handles related tables)
                delete_servers_query = f"DELETE FROM smb_servers WHERE ip_address IN ({failure_placeholders})"
                cur.execute(delete_servers_query, found_ips)

                # Check rowcount to verify actual deletes
                batch_deleted_count = cur.rowcount

                if batch_deleted_count > 0:
                    # Commit transaction (commits both failure_logs and smb_servers deletes)
                    conn.commit()

                    # Track deleted IPs and count
                    all_deleted_ips.extend(found_ips)
                    total_deleted_count += batch_deleted_count

        # Invalidate cache after successful deletes
        if total_deleted_count > 0:
            self.clear_cache()

        return {
            "deleted_count": total_deleted_count,
            "deleted_ips": all_deleted_ips,
            "error": None
        }

    except Exception as e:
        # Return error in result dict
        return {
            "deleted_count": 0,
            "deleted_ips": [],
            "error": str(e)
        }

def bulk_delete_rows(self, row_specs: List[Tuple]) -> Dict[str, Any]:
    """
    Delete rows by protocol row specs.

    'S' tuples → DELETE FROM smb_servers WHERE ip_address IN (...)
    'F' tuples → DELETE FROM ftp_servers WHERE ip_address IN (...)
    'H' tuples may be either:
      - (host_type, ip_address)               [legacy; deletes all HTTP endpoints for IP]
      - (host_type, ip_address, port)         [endpoint-aware; deletes one HTTP row]

    No cross-protocol deletion possible by construction.

    Returns:
        deleted_count:    total rows removed across both protocols
        deleted_ips:      union of all removed IPs (for display/logging)
        deleted_smb_ips:  IPs where the SMB row was removed — used by caller
                          to selectively clear file-based probe cache
        error:            error string if any partial failure, else None
    """
    if not row_specs:
        return {"deleted_count": 0, "deleted_ips": [], "deleted_smb_ips": [], "error": None}

    smb_set: Set[str] = set()
    ftp_set: Set[str] = set()
    http_specs: List[Tuple[str, Optional[int]]] = []
    for spec in row_specs:
        if not spec:
            continue
        ht = str(spec[0]).upper() if len(spec) > 0 else ""
        ip = str(spec[1]).strip() if len(spec) > 1 and spec[1] else ""
        if not ip:
            continue
        if ht == "S":
            smb_set.add(ip)
            continue
        if ht == "F":
            ftp_set.add(ip)
            continue
        if ht != "H":
            continue
        port = None
        if len(spec) > 2 and spec[2] is not None:
            try:
                port = int(spec[2])
            except (TypeError, ValueError):
                port = None
        http_specs.append((ip, port))
    smb_ips = list(smb_set)
    ftp_ips = list(ftp_set)

    total_deleted_count = 0
    all_deleted_ips: List[str] = []
    all_deleted_smb_ips: List[str] = []
    error_parts: List[str] = []

    def _append_unique(items: List[str]) -> None:
        for ip in items:
            if ip not in all_deleted_ips:
                all_deleted_ips.append(ip)

    batch_size = 500

    # --- SMB delete ---
    for i in range(0, len(smb_ips), batch_size):
        batch = smb_ips[i:i + batch_size]
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                placeholders = ','.join('?' * len(batch))
                cur.execute(
                    f"SELECT ip_address FROM smb_servers WHERE ip_address IN ({placeholders})",
                    batch,
                )
                found_smb = [row["ip_address"] for row in cur.fetchall()]
                if not found_smb:
                    continue
                fp = ','.join('?' * len(found_smb))
                # failure_logs has no FK — delete explicitly for SMB IPs only
                # TODO: failure_logs has no protocol column (schema is IP-only); deleting
                # by SMB IP is safe because failure_logs rows belong to SMB probes.
                # FTP-deleted IPs intentionally skip this to avoid clearing SMB sibling data.
                cur.execute(f"DELETE FROM failure_logs WHERE ip_address IN ({fp})", found_smb)
                cur.execute(f"DELETE FROM smb_servers WHERE ip_address IN ({fp})", found_smb)
                n = cur.rowcount
                if n > 0:
                    conn.commit()
                    _append_unique(found_smb)
                    all_deleted_smb_ips.extend(found_smb)
                    total_deleted_count += n
        except Exception as exc:
            error_parts.append(f"SMB delete error: {exc}")

    # --- FTP delete ---
    for i in range(0, len(ftp_ips), batch_size):
        batch = ftp_ips[i:i + batch_size]
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                placeholders = ','.join('?' * len(batch))
                cur.execute(
                    f"SELECT ip_address FROM ftp_servers WHERE ip_address IN ({placeholders})",
                    batch,
                )
                found_ftp = [row["ip_address"] for row in cur.fetchall()]
                if not found_ftp:
                    continue
                fp = ','.join('?' * len(found_ftp))
                # ftp_user_flags and ftp_probe_cache CASCADE from ftp_servers — no explicit delete needed
                cur.execute(f"DELETE FROM ftp_servers WHERE ip_address IN ({fp})", found_ftp)
                n = cur.rowcount
                if n > 0:
                    conn.commit()
                    _append_unique(found_ftp)
                    total_deleted_count += n
        except sqlite3.OperationalError as exc:
            if "no such table: ftp_servers" in str(exc).lower():
                error_parts.append("FTP tables not yet migrated; FTP rows not deleted.")
            else:
                error_parts.append(f"FTP delete error: {exc}")
        except Exception as exc:
            error_parts.append(f"FTP delete error: {exc}")

    # --- HTTP delete ---
    for ip, port in http_specs:
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                if port is None:
                    cur.execute(
                        "SELECT id, ip_address FROM http_servers WHERE ip_address = ?",
                        (ip,),
                    )
                else:
                    cur.execute(
                        "SELECT id, ip_address FROM http_servers WHERE ip_address = ? AND port = ?",
                        (ip, port),
                    )
                rows = cur.fetchall()
                found_ids = [int(row["id"]) for row in rows]
                found_ips = [row["ip_address"] for row in rows]
                if not found_ids:
                    continue
                placeholders = ','.join('?' * len(found_ids))
                # http_user_flags and http_probe_cache CASCADE from http_servers
                cur.execute(f"DELETE FROM http_servers WHERE id IN ({placeholders})", found_ids)
                n = cur.rowcount
                if n > 0:
                    conn.commit()
                    _append_unique(found_ips)
                    total_deleted_count += n
        except sqlite3.OperationalError as exc:
            if "no such table: http_servers" in str(exc).lower():
                error_parts.append("HTTP tables not yet migrated; HTTP rows not deleted.")
            else:
                error_parts.append(f"HTTP delete error: {exc}")
        except Exception as exc:
            error_parts.append(f"HTTP delete error: {exc}")

    if total_deleted_count > 0:
        self.clear_cache()

    return {
        "deleted_count": total_deleted_count,
        "deleted_ips": all_deleted_ips,
        "deleted_smb_ips": all_deleted_smb_ips,
        "error": "; ".join(error_parts) if error_parts else None,
    }

def _get_mock_data(self) -> Dict[str, Any]:
    """Get mock data for testing."""
    return {
        "servers": [
            {
                "ip_address": "192.168.1.45",
                "country": "United States",
                "country_code": "US",
                "auth_method": "Anonymous",
                "last_seen": "2025-01-21T14:20:00",
                "scan_count": 3,
                "accessible_shares": 7,
                "vulnerabilities": 2
            },
            {
                "ip_address": "10.0.0.123",
                "country": "United Kingdom",
                "country_code": "GB",
                "auth_method": "Guest/Blank",
                "last_seen": "2025-01-21T11:45:00",
                "scan_count": 2,
                "accessible_shares": 3,
                "vulnerabilities": 1
            },
            {
                "ip_address": "172.16.5.78",
                "country": "Canada",
                "country_code": "CA",
                "auth_method": "Guest/Guest",
                "last_seen": "2025-01-20T16:00:00",
                "scan_count": 1,
                "accessible_shares": 1,
                "vulnerabilities": 0
            }
        ]
    }

def is_database_available(self) -> bool:
    """
    Check if database is available and accessible.
    
    Returns:
        True if database can be accessed, False otherwise
    """
    if self.mock_mode:
        return True
    
    try:
        with self._get_connection(timeout=5) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except (sqlite3.Error, FileNotFoundError):
        return False

# --- SMB file browser helpers -------------------------------------

def get_server_auth_method(self, ip_address: str) -> Optional[str]:
    """Return auth_method string for a server by IP."""
    query = "SELECT auth_method FROM smb_servers WHERE ip_address = ? LIMIT 1"
    with self._get_connection() as conn:
        row = conn.execute(query, (ip_address,)).fetchone()
        return row["auth_method"] if row else None

def get_smb_shodan_data(self, ip_address: str) -> Optional[str]:
    """Return the raw shodan_data JSON string for an SMB server by IP."""
    query = "SELECT shodan_data FROM smb_servers WHERE ip_address = ? LIMIT 1"
    try:
        with self._get_connection() as conn:
            row = conn.execute(query, (ip_address,)).fetchone()
            return row["shodan_data"] if row else None
    except Exception:
        return None

def get_accessible_shares(self, ip_address: str) -> List[Dict[str, Any]]:
    """
    Fetch accessible shares for the given server IP.

    Returns list of dicts: {share_name, permissions, last_tested}
    """
    query = """
    SELECT sa.share_name, sa.permissions, sa.test_timestamp
    FROM share_access sa
    JOIN smb_servers s ON sa.server_id = s.id
    WHERE s.ip_address = ? AND sa.accessible = 1
    ORDER BY sa.share_name
    """
    with self._get_connection() as conn:
        rows = conn.execute(query, (ip_address,)).fetchall()
        return [
            {
                "share_name": row["share_name"],
                "permissions": row["permissions"],
                "last_tested": row["test_timestamp"],
            }
            for row in rows
        ]

def get_denied_shares(self, ip_address: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch denied/non-accessible shares for the given server IP.

    Returns list of dicts: {share_name, auth_status, error_message, last_tested}
    """
    query = """
    SELECT sa.share_name, sa.auth_status, sa.error_message, sa.test_timestamp
    FROM share_access sa
    JOIN smb_servers s ON sa.server_id = s.id
    WHERE s.ip_address = ? AND sa.accessible = 0
    ORDER BY sa.share_name
    """
    with self._get_connection() as conn:
        if limit:
            rows = conn.execute(query + " LIMIT ?", (ip_address, limit)).fetchall()
        else:
            rows = conn.execute(query, (ip_address,)).fetchall()
        return [
            {
                "share_name": row["share_name"],
                "auth_status": row["auth_status"],
                "error_message": row["error_message"],
                "last_tested": row["test_timestamp"],
            }
            for row in rows
        ]

def get_denied_share_counts(self) -> Dict[str, int]:
    """
    Return a mapping of ip_address -> denied share count.
    """
    query = """
    SELECT s.ip_address, COUNT(sa.id) as denied_count
    FROM smb_servers s
    LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = 0
    GROUP BY s.ip_address
    """
    with self._get_connection() as conn:
        rows = conn.execute(query).fetchall()
        return {row["ip_address"]: row["denied_count"] or 0 for row in rows}

# --- Share credentials ---------------------------------------------

def get_share_credentials(self, ip_address: str) -> List[Dict[str, Any]]:
    """
    Fetch stored credentials for shares on the given host.

    Returns:
        List of dicts with share_name, username, password, source, last_verified_at.
    """
    query = """
        SELECT sc.share_name, sc.username, sc.password, sc.source, sc.last_verified_at
        FROM share_credentials sc
        JOIN smb_servers s ON sc.server_id = s.id
        WHERE s.ip_address = ?
    """
    with self._get_connection() as conn:
        rows = conn.execute(query, (ip_address,)).fetchall()
        return [
            {
                "share_name": row["share_name"],
                "username": row["username"],
                "password": row["password"],
                "source": row["source"],
                "last_verified_at": row["last_verified_at"],
            }
            for row in rows
        ]

# --- RCE status helpers ---------------------------------------------

def get_rce_status(self, ip_address: str) -> Optional[str]:
    """
    Get RCE analysis status for a host.

    Args:
        ip_address: IP address of the host

    Returns:
        RCE status string: 'not_run', 'clean', 'flagged', 'unknown', or 'error'
        Returns 'not_run' if no status found.
    """
    query = """
        SELECT pc.rce_status
        FROM host_probe_cache pc
        JOIN smb_servers s ON pc.server_id = s.id
        WHERE s.ip_address = ?
    """
    with self._get_connection() as conn:
        row = conn.execute(query, (ip_address,)).fetchone()
        return row["rce_status"] if row and row["rce_status"] else "not_run"

def get_rce_status_for_host(self, ip_address: str, host_type: str) -> str:
    """
    Get RCE analysis status for a host, protocol-aware.

    Args:
        ip_address: IP address of the host
        host_type:  'S' → query host_probe_cache JOIN smb_servers
                    'F' → query ftp_probe_cache JOIN ftp_servers

    Returns:
        RCE status string, or 'not_run' if not found or table absent.
    """
    host_type = (host_type or "S").upper()
    if host_type == "S":
        return self.get_rce_status(ip_address)
    if host_type == "H":
        try:
            query = """
                SELECT pc.rce_status
                FROM http_probe_cache pc
                JOIN http_servers s ON pc.server_id = s.id
                WHERE s.ip_address = ?
            """
            with self._get_connection() as conn:
                row = conn.execute(query, (ip_address,)).fetchone()
                return row["rce_status"] if row and row["rce_status"] else "not_run"
        except sqlite3.OperationalError:
            return "not_run"
    # FTP path
    try:
        query = """
            SELECT pc.rce_status
            FROM ftp_probe_cache pc
            JOIN ftp_servers s ON pc.server_id = s.id
            WHERE s.ip_address = ?
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (ip_address,)).fetchone()
            return row["rce_status"] if row and row["rce_status"] else "not_run"
    except sqlite3.OperationalError:
        return "not_run"

def upsert_rce_status(self, ip_address: str, rce_status: str,
                      verdict_summary: Optional[str] = None) -> None:
    """SMB-compatible shim. Delegates to upsert_rce_status_for_host with host_type='S'."""
    self.upsert_rce_status_for_host(ip_address, 'S', rce_status, verdict_summary)

# ------------------------------------------------------------------
# FTP sidecar read methods
# All methods guard against OperationalError in case the migration has
# not yet fired (e.g. very early startup), returning safe empty values.
# ------------------------------------------------------------------

def get_ftp_servers(self, country: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return active FTP server rows, optionally filtered by country_code.

    Args:
        country: ISO 3166-1 alpha-2 code to filter by, or None for all.

    Returns:
        List of dicts with ftp_servers columns.
    """
    query = "SELECT * FROM ftp_servers WHERE status = 'active'"
    params: tuple = ()
    if country:
        query += " AND country_code = ?"
        params = (country,)
    query += " ORDER BY last_seen DESC"
    try:
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []

def get_ftp_server_count(self) -> int:
    """Return count of active FTP servers."""
    try:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ftp_servers WHERE status = 'active'"
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def bind_database_access_write_methods(reader_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted write/legacy-read methods onto DatabaseReader."""
    globals().update(shared_symbols)
    method_names = (
        "_resolve_protocol_server_id",
        "upsert_user_flags",
        "upsert_probe_cache",
        "upsert_extracted_flag",
        "upsert_user_flags_for_host",
        "upsert_probe_cache_for_host",
        "upsert_probe_snapshot_for_host",
        "get_probe_snapshot_for_host",
        "set_latest_probe_snapshot_for_host",
        "upsert_extracted_flag_for_host",
        "upsert_rce_status_for_host",
        "upsert_manual_server_record",
        "bulk_delete_servers",
        "bulk_delete_rows",
        "_get_mock_data",
        "is_database_available",
        "get_server_auth_method",
        "get_smb_shodan_data",
        "get_accessible_shares",
        "get_denied_shares",
        "get_denied_share_counts",
        "get_share_credentials",
        "get_rce_status",
        "get_rce_status_for_host",
        "upsert_rce_status",
        "get_ftp_servers",
        "get_ftp_server_count",
    )
    for name in method_names:
        setattr(reader_cls, name, globals()[name])
    from gui.utils.database_access_migration_methods import bind_database_access_migration_methods
    bind_database_access_migration_methods(reader_cls, shared_symbols)
