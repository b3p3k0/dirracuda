"""Protocol-list/query methods extracted from database_access.py."""

from __future__ import annotations

import ipaddress
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

def get_http_server_detail(
    self,
    ip_address: str,
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return HTTP endpoint detail for the requested row.

    Resolution order:
    1. protocol_server_id (authoritative per-row identity)
    2. ip_address + port endpoint
    3. most-recently-seen row for ip_address (legacy fallback)

    Returns None if no row found or HTTP tables are absent.
    Silently swallows all exceptions so missing HTTP tables are non-fatal.
    """
    try:
        with self._get_connection() as conn:
            if protocol_server_id is not None:
                row = conn.execute(
                    "SELECT id, scheme, port, probe_host, probe_path FROM http_servers WHERE id = ?",
                    (int(protocol_server_id),),
                ).fetchone()
            elif port is not None:
                row = conn.execute(
                    "SELECT id, scheme, port, probe_host, probe_path FROM http_servers "
                    "WHERE ip_address = ? AND port = ? "
                    "ORDER BY last_seen DESC, id DESC LIMIT 1",
                    (ip_address, int(port)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, scheme, port, probe_host, probe_path FROM http_servers "
                    "WHERE ip_address = ? "
                    "ORDER BY last_seen DESC, id DESC LIMIT 1",
                    (ip_address,),
                ).fetchone()
            if row:
                return {
                    "protocol_server_id": int(row[0]),
                    "scheme": row[1] or "http",
                    "port": int(row[2] or 80),
                    "probe_host": row[3] or None,
                    "probe_path": row[4] or None,
                }
            return None
    except Exception:
        return None

def get_host_protocols(self, ip: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Query v_host_protocols for protocol presence per IP.

    Args:
        ip: Specific IP address to look up, or None to return all hosts.

    Returns:
        List of dicts with keys: ip_address, has_smb, has_ftp,
        protocol_presence ('smb_only' | 'ftp_only' | 'both').
    """
    query = (
        "SELECT ip_address, has_smb, has_ftp, protocol_presence"
        " FROM v_host_protocols"
    )
    params: tuple = ()
    if ip:
        query += " WHERE ip_address = ?"
        params = (ip,)
    try:
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []

def get_dual_protocol_count(self) -> int:
    """Return count of IPs present in both smb_servers and ftp_servers."""
    try:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM v_host_protocols"
                " WHERE has_smb = 1 AND has_ftp = 1"
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0

# ------------------------------------------------------------------
# Unified protocol list — UNION ALL of SMB (S) and FTP (F) rows
# ------------------------------------------------------------------

def get_protocol_server_list(
    self,
    limit: Optional[int] = 100,
    offset: int = 0,
    country_filter: Optional[str] = None,
    recent_scan_only: bool = False,
) -> Tuple[List[Dict], int]:
    """
    Return a unified, paginated list of SMB and FTP server rows.

    Each row carries a ``host_type`` field ('S' for SMB, 'F' for FTP) and a
    stable ``row_key`` (e.g. "S:123" / "F:456") so the same IP address can
    appear twice when both protocols are present without colliding.

    Protocol-specific state (favorite, avoid, probe, extracted, rce) is read
    from the correct per-protocol table — SMB state never bleeds into FTP
    rows and vice-versa.

    Args:
        limit:          Max rows to return. ``None`` returns all rows.
        offset:         Pagination offset.
        country_filter: ISO 3166-1 alpha-2 country code, or None for all.
        recent_scan_only: If True, restrict to rows seen within 1 hour of
                        the most recent last_seen timestamp across both tables.

    Returns:
        Tuple of (rows, total_count) where rows is a list of dicts.
    """
    if self.mock_mode:
        return self._get_mock_protocol_list(limit, offset, country_filter)

    try:
        return self._query_protocol_server_list_smb_ftp_http(
            limit, offset, country_filter, recent_scan_only
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        # Tier-2: HTTP tables absent (pre-HTTP migration) — try SMB+FTP
        if "no such table: http_" in msg:
            try:
                return self._query_protocol_server_list_smb_ftp(
                    limit, offset, country_filter, recent_scan_only
                )
            except sqlite3.OperationalError as exc2:
                # Tier-3: FTP tables also absent — fall back to SMB-only
                if "no such table: ftp_" in str(exc2).lower():
                    return self._query_protocol_server_list_smb_only(
                        limit, offset, country_filter, recent_scan_only
                    )
                raise
        # Tier-3 direct: FTP tables absent without HTTP tables (edge case)
        elif "no such table: ftp_" in msg:
            return self._query_protocol_server_list_smb_only(
                limit, offset, country_filter, recent_scan_only
            )
        raise

def _normalize_iso_to_utc_sql_timestamp(self, timestamp: Optional[str]) -> Optional[str]:
    """
    Convert an ISO-like timestamp string into SQLite UTC datetime text.

    ScanManager stores scan start/end values via ``datetime.now().isoformat()``
    (local time, usually naive). FTP access rows are written by SQLite
    ``CURRENT_TIMESTAMP`` (UTC). This normalizes GUI times to UTC so we can
    safely filter the just-finished scan window.
    """
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp))
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.replace(tzinfo=local_tz)

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _normalize_iso_to_local_sql_timestamp(self, timestamp: Optional[str]) -> Optional[str]:
    """
    Convert an ISO-like timestamp string into local naive SQL datetime text.

    SMB share_access rows are commonly written as local naive timestamps
    (via datetime.now().isoformat()) in legacy write paths. This helper
    normalizes scan window boundaries to that local-naive shape so cohort
    filtering can match those rows reliably.
    """
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp))
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is not None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.astimezone(local_tz).replace(tzinfo=None)

    return dt.strftime("%Y-%m-%d %H:%M:%S")

def get_protocol_scan_cohort_server_ids(
    self,
    host_type: Optional[str],
    scan_start_time: Optional[str],
    scan_end_time: Optional[str],
) -> Set[int]:
    """
    Return protocol server IDs belonging to a single scan window.

    This is used by dashboard post-scan bulk probe/extract flows to ensure
    targets come only from accessible hosts in the immediately completed
    scan, not from broader "recent row" windows.
    """
    if self.mock_mode:
        return set()

    proto = (host_type or "").strip().upper()
    if proto not in {"S", "F", "H"}:
        return set()

    start_utc = self._normalize_iso_to_utc_sql_timestamp(scan_start_time)
    end_utc = self._normalize_iso_to_utc_sql_timestamp(scan_end_time)
    start_local = self._normalize_iso_to_local_sql_timestamp(scan_start_time)
    end_local = self._normalize_iso_to_local_sql_timestamp(scan_end_time)
    if not start_utc or not end_utc:
        return set()

    # SMB: cohort is hosts with at least one accessible share test record
    # in this scan window.
    if proto == "S":
        sql_by_test_ts = """
            SELECT DISTINCT s.id AS server_id
            FROM smb_servers s
            INNER JOIN share_access sa ON sa.server_id = s.id
            WHERE s.status = 'active'
              AND COALESCE(sa.accessible, 0) = 1
              AND datetime(sa.test_timestamp) >= datetime(?)
              AND datetime(sa.test_timestamp) <= datetime(?)
        """
        sql_by_created_at = sql_by_test_ts.replace("sa.test_timestamp", "sa.created_at")

        def _run_smb_window(start_ts: str, end_ts: str) -> list[sqlite3.Row]:
            try:
                with self._get_connection() as conn:
                    return conn.execute(sql_by_test_ts, (start_ts, end_ts)).fetchall()
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "no such column" in msg:
                    try:
                        with self._get_connection() as conn:
                            return conn.execute(sql_by_created_at, (start_ts, end_ts)).fetchall()
                    except sqlite3.OperationalError:
                        return []
                if "no such table" in msg:
                    return []
                return []

        # SMB compatibility: accept both UTC-like and local-naive scan windows.
        # Legacy SMB write paths store local naive timestamps; newer paths can
        # produce UTC-like values. Union both to avoid dropping valid hosts.
        rows = _run_smb_window(start_utc, end_utc)
        if start_local and end_local and (start_local != start_utc or end_local != end_utc):
            rows.extend(_run_smb_window(start_local, end_local))
    else:
        # FTP/HTTP: cohort is hosts with an accessible stage-2 result in
        # this scan window.
        if proto == "F":
            access_table = "ftp_access"
            server_table = "ftp_servers"
        else:
            access_table = "http_access"
            server_table = "http_servers"

        sql_by_test_ts = f"""
            SELECT DISTINCT a.server_id
            FROM {access_table} a
            INNER JOIN {server_table} s ON s.id = a.server_id
            WHERE s.status = 'active'
              AND COALESCE(a.accessible, 0) = 1
              AND datetime(a.test_timestamp) >= datetime(?)
              AND datetime(a.test_timestamp) <= datetime(?)
        """
        sql_by_created_at = sql_by_test_ts.replace("a.test_timestamp", "a.created_at")

        try:
            with self._get_connection() as conn:
                rows = conn.execute(sql_by_test_ts, (start_utc, end_utc)).fetchall()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            # Legacy schema fallback if test_timestamp is absent.
            if "no such column" in msg:
                try:
                    with self._get_connection() as conn:
                        rows = conn.execute(sql_by_created_at, (start_utc, end_utc)).fetchall()
                except sqlite3.OperationalError:
                    return set()
            # Protocol tables missing on older databases.
            elif "no such table" in msg:
                return set()
            else:
                return set()

    ids: Set[int] = set()
    for row in rows:
        try:
            ids.add(int(row["server_id"]))
        except (TypeError, ValueError, KeyError):
            continue
    return ids

def _build_union_sql(self, smb_where: str, ftp_where: str) -> str:
    """Return the UNION ALL query string for both protocol halves."""
    return f"""
    SELECT
        'S'                        AS host_type,
        s.id                       AS protocol_server_id,
        'S:' || CAST(s.id AS TEXT) AS row_key,
        s.ip_address,
        s.country,
        s.country_code,
        s.last_seen,
        s.scan_count,
        s.status,
        s.auth_method,
        COALESCE(sa_sum.total_shares, 0)            AS total_shares,
        COALESCE(sa_sum.accessible_shares, 0)       AS accessible_shares,
        COALESCE(sa_sum.accessible_shares_list, '')  AS accessible_shares_list,
        NULL                                         AS port,
        NULL                                         AS banner,
        NULL                                         AS anon_accessible,
        COALESCE(uf.favorite, 0)                    AS favorite,
        COALESCE(uf.avoid, 0)                       AS avoid,
        COALESCE(uf.notes, '')                      AS notes,
        COALESCE(pc.status, 'unprobed')             AS probe_status,
        COALESCE(pc.indicator_matches, 0)           AS indicator_matches,
        COALESCE(pc.extracted, 0)                   AS extracted,
        COALESCE(pc.rce_status, 'not_run')          AS rce_status
    FROM smb_servers s
    LEFT JOIN (
        SELECT
            server_id,
            COUNT(share_name)                                         AS total_shares,
            COUNT(CASE WHEN accessible = 1 THEN 1 END)               AS accessible_shares,
            GROUP_CONCAT(
                CASE WHEN accessible = 1 THEN share_name END, ','
            )                                                         AS accessible_shares_list
        FROM share_access
        GROUP BY server_id
    ) sa_sum ON s.id = sa_sum.server_id
    LEFT JOIN host_user_flags  uf ON uf.server_id = s.id
    LEFT JOIN host_probe_cache pc ON pc.server_id = s.id
    {smb_where}

    UNION ALL

    SELECT
        'F'                        AS host_type,
        f.id                       AS protocol_server_id,
        'F:' || CAST(f.id AS TEXT) AS row_key,
        f.ip_address,
        f.country,
        f.country_code,
        f.last_seen,
        f.scan_count,
        f.status,
        'anonymous'                AS auth_method,
        COALESCE(
            fpc.accessible_dirs_count,
            CASE
                WHEN fa_latest.accessible = 1 AND fa_latest.root_listing_available = 1
                THEN COALESCE(fa_latest.root_entry_count, 0)
                ELSE 0
            END,
            0
        ) AS total_shares,
        COALESCE(
            fpc.accessible_dirs_count,
            CASE
                WHEN fa_latest.accessible = 1 AND fa_latest.root_listing_available = 1
                THEN COALESCE(fa_latest.root_entry_count, 0)
                ELSE 0
            END,
            0
        ) AS accessible_shares,
        COALESCE(fpc.accessible_dirs_list, '') AS accessible_shares_list,
        f.port,
        f.banner,
        f.anon_accessible,
        COALESCE(fuf.favorite, 0)           AS favorite,
        COALESCE(fuf.avoid, 0)              AS avoid,
        COALESCE(fuf.notes, '')             AS notes,
        COALESCE(fpc.status, 'unprobed')    AS probe_status,
        COALESCE(fpc.indicator_matches, 0)  AS indicator_matches,
        COALESCE(fpc.extracted, 0)          AS extracted,
        COALESCE(fpc.rce_status, 'not_run') AS rce_status
    FROM ftp_servers f
    LEFT JOIN ftp_user_flags  fuf ON fuf.server_id = f.id
    LEFT JOIN ftp_probe_cache fpc ON fpc.server_id = f.id
    LEFT JOIN (
        SELECT
            a.server_id,
            a.accessible,
            a.root_listing_available,
            a.root_entry_count
        FROM ftp_access a
        INNER JOIN (
            SELECT server_id, MAX(id) AS max_id
            FROM ftp_access
            GROUP BY server_id
        ) latest
          ON latest.server_id = a.server_id
         AND latest.max_id    = a.id
    ) fa_latest ON fa_latest.server_id = f.id
    {ftp_where}
    """

def _build_http_arm(self, http_where: str) -> str:
    """Return the HTTP SELECT arm for the 3-protocol UNION ALL query.

    Produces the same 23 columns in the same order as _build_union_sql arms.
    """
    return f"""
    SELECT
        'H'                         AS host_type,
        hs.id                       AS protocol_server_id,
        'H:' || CAST(hs.id AS TEXT) AS row_key,
        hs.ip_address,
        hs.country,
        hs.country_code,
        hs.last_seen,
        hs.scan_count,
        hs.status,
        'http'                      AS auth_method,
        COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0)
                                    AS total_shares,
        COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0)
                                    AS accessible_shares,
        COALESCE(hpc.accessible_dirs_list, '') AS accessible_shares_list,
        hs.port,
        hs.banner,
        0                           AS anon_accessible,
        COALESCE(huf.favorite, 0)   AS favorite,
        COALESCE(huf.avoid, 0)      AS avoid,
        COALESCE(huf.notes, '')     AS notes,
        COALESCE(hpc.status, 'unprobed')          AS probe_status,
        COALESCE(hpc.indicator_matches, 0)        AS indicator_matches,
        COALESCE(hpc.extracted, 0)                AS extracted,
        COALESCE(hpc.rce_status, 'not_run')       AS rce_status
    FROM http_servers hs
    LEFT JOIN http_user_flags  huf ON huf.server_id = hs.id
    LEFT JOIN http_probe_cache hpc ON hpc.server_id = hs.id
    {http_where}
    """

def _query_protocol_server_list_smb_ftp_http(
    self,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """Execute full UNION ALL query (SMB + FTP + HTTP)."""
    with self._get_connection() as conn:
        smb_where  = "WHERE s.status = 'active'"
        ftp_where  = "WHERE f.status = 'active'"
        http_where = "WHERE hs.status = 'active'"
        smb_params:  List[Any] = []
        ftp_params:  List[Any] = []
        http_params: List[Any] = []

        if country_filter:
            smb_where  += " AND s.country_code = ?"
            ftp_where  += " AND f.country_code = ?"
            http_where += " AND hs.country_code = ?"
            smb_params.append(country_filter)
            ftp_params.append(country_filter)
            http_params.append(country_filter)

        if recent_scan_only:
            cutoff = self._get_protocol_recent_cutoff(conn)
            if cutoff:
                smb_where  += " AND datetime(s.last_seen)  >= datetime(?, '-1 hour')"
                ftp_where  += " AND datetime(f.last_seen)  >= datetime(?, '-1 hour')"
                http_where += " AND datetime(hs.last_seen) >= datetime(?, '-1 hour')"
                smb_params.append(cutoff)
                ftp_params.append(cutoff)
                http_params.append(cutoff)

        union_sql = (
            self._build_union_sql(smb_where, ftp_where)
            + "\n        UNION ALL\n"
            + self._build_http_arm(http_where)
        )
        union_params = smb_params + ftp_params + http_params

        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM ({union_sql}) _u",
            union_params,
        ).fetchone()["total"]

        data_sql = (
            f"SELECT * FROM ({union_sql}) _u"
            f" ORDER BY datetime(last_seen) DESC, row_key ASC"
        )
        data_params = list(union_params)
        if limit is not None and limit > 0:
            data_sql += " LIMIT ? OFFSET ?"
            data_params += [limit, offset]

        rows = conn.execute(data_sql, data_params).fetchall()
        return [dict(row) for row in rows], total

def _query_protocol_server_list_smb_ftp(
    self,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """Execute SMB + FTP UNION ALL query (tier-2 fallback when HTTP tables absent)."""
    with self._get_connection() as conn:
        smb_where = "WHERE s.status = 'active'"
        ftp_where = "WHERE f.status = 'active'"
        smb_params: List[Any] = []
        ftp_params: List[Any] = []

        if country_filter:
            smb_where += " AND s.country_code = ?"
            ftp_where += " AND f.country_code = ?"
            smb_params.append(country_filter)
            ftp_params.append(country_filter)

        if recent_scan_only:
            cutoff = self._get_protocol_recent_cutoff(conn)
            if cutoff:
                smb_where += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                ftp_where += " AND datetime(f.last_seen) >= datetime(?, '-1 hour')"
                smb_params.append(cutoff)
                ftp_params.append(cutoff)

        union_sql = self._build_union_sql(smb_where, ftp_where)
        union_params = smb_params + ftp_params

        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM ({union_sql}) _u",
            union_params,
        ).fetchone()["total"]

        data_sql = (
            f"SELECT * FROM ({union_sql}) _u"
            f" ORDER BY datetime(last_seen) DESC, row_key ASC"
        )
        data_params = list(union_params)
        if limit is not None and limit > 0:
            data_sql += " LIMIT ? OFFSET ?"
            data_params += [limit, offset]

        rows = conn.execute(data_sql, data_params).fetchall()
        return [dict(row) for row in rows], total

def _query_protocol_server_list_smb_only(
    self,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """SMB-only fallback used when FTP tables are absent."""
    with self._get_connection() as conn:
        smb_where = "WHERE s.status = 'active'"
        smb_params: List[Any] = []

        if country_filter:
            smb_where += " AND s.country_code = ?"
            smb_params.append(country_filter)

        if recent_scan_only:
            row = conn.execute(
                "SELECT MAX(datetime(last_seen)) AS cutoff"
                " FROM smb_servers WHERE status = 'active'"
            ).fetchone()
            cutoff = row["cutoff"] if row else None
            if cutoff:
                smb_where += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                smb_params.append(cutoff)

        smb_sql = f"""
        SELECT
            'S'                        AS host_type,
            s.id                       AS protocol_server_id,
            'S:' || CAST(s.id AS TEXT) AS row_key,
            s.ip_address,
            s.country,
            s.country_code,
            s.last_seen,
            s.scan_count,
            s.status,
            s.auth_method,
            COALESCE(sa_sum.total_shares, 0)            AS total_shares,
            COALESCE(sa_sum.accessible_shares, 0)       AS accessible_shares,
            COALESCE(sa_sum.accessible_shares_list, '')  AS accessible_shares_list,
            NULL                                         AS port,
            NULL                                         AS banner,
            NULL                                         AS anon_accessible,
            COALESCE(uf.favorite, 0)                    AS favorite,
            COALESCE(uf.avoid, 0)                       AS avoid,
            COALESCE(uf.notes, '')                      AS notes,
            COALESCE(pc.status, 'unprobed')             AS probe_status,
            COALESCE(pc.indicator_matches, 0)           AS indicator_matches,
            COALESCE(pc.extracted, 0)                   AS extracted,
            COALESCE(pc.rce_status, 'not_run')          AS rce_status
        FROM smb_servers s
        LEFT JOIN (
            SELECT
                server_id,
                COUNT(share_name)                                         AS total_shares,
                COUNT(CASE WHEN accessible = 1 THEN 1 END)               AS accessible_shares,
                GROUP_CONCAT(
                    CASE WHEN accessible = 1 THEN share_name END, ','
                )                                                         AS accessible_shares_list
            FROM share_access
            GROUP BY server_id
        ) sa_sum ON s.id = sa_sum.server_id
        LEFT JOIN host_user_flags  uf ON uf.server_id = s.id
        LEFT JOIN host_probe_cache pc ON pc.server_id = s.id
        {smb_where}
        """

        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM ({smb_sql}) _u",
            smb_params,
        ).fetchone()["total"]

        data_sql = (
            f"SELECT * FROM ({smb_sql}) _u"
            f" ORDER BY datetime(last_seen) DESC, row_key ASC"
        )
        data_params = list(smb_params)
        if limit is not None and limit > 0:
            data_sql += " LIMIT ? OFFSET ?"
            data_params += [limit, offset]

        rows = conn.execute(data_sql, data_params).fetchall()
        return [dict(row) for row in rows], total

def _get_protocol_recent_cutoff(self, conn: sqlite3.Connection) -> Optional[str]:
    """
    Return the most recent last_seen timestamp across SMB, FTP, and HTTP servers.

    Uses SQL datetime() normalization to handle mixed timestamp formats
    (YYYY-MM-DD HH:MM:SS vs YYYY-MM-DDTHH:MM:SS) correctly. Falls back
    progressively if HTTP or FTP tables are absent (pre-migration).
    """
    try:
        row = conn.execute("""
            SELECT MAX(datetime(ts)) AS cutoff FROM (
                SELECT MAX(datetime(last_seen)) AS ts
                FROM smb_servers WHERE status = 'active'
                UNION ALL
                SELECT MAX(datetime(last_seen)) AS ts
                FROM ftp_servers WHERE status = 'active'
                UNION ALL
                SELECT MAX(datetime(last_seen)) AS ts
                FROM http_servers WHERE status = 'active'
            )
        """).fetchone()
        return row["cutoff"] if row else None
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "no such table: http_" in msg or "no such table: ftp_" in msg:
            # Fall back to SMB + FTP only (or SMB only if FTP also absent)
            try:
                row = conn.execute("""
                    SELECT MAX(datetime(ts)) AS cutoff FROM (
                        SELECT MAX(datetime(last_seen)) AS ts
                        FROM smb_servers WHERE status = 'active'
                        UNION ALL
                        SELECT MAX(datetime(last_seen)) AS ts
                        FROM ftp_servers WHERE status = 'active'
                    )
                """).fetchone()
                return row["cutoff"] if row else None
            except sqlite3.OperationalError:
                row = conn.execute(
                    "SELECT MAX(datetime(last_seen)) AS cutoff"
                    " FROM smb_servers WHERE status = 'active'"
                ).fetchone()
                return row["cutoff"] if row else None
        raise

def _get_mock_protocol_list(
    self,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
) -> Tuple[List[Dict], int]:
    """Return mock S+F rows for testing without a real database."""
    rows: List[Dict] = [
        {
            "host_type": "S",
            "protocol_server_id": 1,
            "row_key": "S:1",
            "ip_address": "192.168.1.45",
            "country": "United States",
            "country_code": "US",
            "last_seen": "2025-01-21T14:20:00",
            "scan_count": 3,
            "status": "active",
            "auth_method": "Anonymous",
            "total_shares": 7,
            "accessible_shares": 7,
            "accessible_shares_list": "ADMIN$,C$,IPC$,share1,share2,share3,share4",
            "port": None,
            "banner": None,
            "anon_accessible": None,
            "favorite": 0,
            "avoid": 0,
            "notes": "",
            "probe_status": "unprobed",
            "indicator_matches": 0,
            "extracted": 0,
            "rce_status": "not_run",
        },
        {
            "host_type": "F",
            "protocol_server_id": 1,
            "row_key": "F:1",
            "ip_address": "10.0.0.123",
            "country": "United Kingdom",
            "country_code": "GB",
            "last_seen": "2025-01-21T11:45:00",
            "scan_count": 1,
            "status": "active",
            "auth_method": "anonymous",
            "total_shares": 0,
            "accessible_shares": 0,
            "accessible_shares_list": "",
            "port": 21,
            "banner": "220 FTP server ready",
            "anon_accessible": 1,
            "favorite": 0,
            "avoid": 0,
            "notes": "",
            "probe_status": "unprobed",
            "indicator_matches": 0,
            "extracted": 0,
            "rce_status": "not_run",
        },
    ]

    if country_filter:
        rows = [r for r in rows if r["country_code"] == country_filter]

    total = len(rows)
    paginated = rows[offset : (offset + limit) if limit is not None else None]
    return paginated, total


def bind_database_access_protocol_methods(reader_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach extracted protocol query methods onto DatabaseReader."""
    globals().update(shared_symbols)
    method_names = (
        "get_http_server_detail",
        "get_host_protocols",
        "get_dual_protocol_count",
        "get_protocol_server_list",
        "_normalize_iso_to_utc_sql_timestamp",
        "_normalize_iso_to_local_sql_timestamp",
        "get_protocol_scan_cohort_server_ids",
        "_build_union_sql",
        "_build_http_arm",
        "_query_protocol_server_list_smb_ftp_http",
        "_query_protocol_server_list_smb_ftp",
        "_query_protocol_server_list_smb_only",
        "_get_protocol_recent_cutoff",
        "_get_mock_protocol_list",
    )
    for name in method_names:
        setattr(reader_cls, name, globals()[name])
