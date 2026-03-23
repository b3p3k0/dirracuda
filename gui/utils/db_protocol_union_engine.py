"""
Protocol-union listing engine for SMBSeek DatabaseReader.

Module-level functions that build and execute the UNION ALL queries combining
SMB, FTP, and HTTP server rows into a single paginated result set.

Extracted from gui/utils/database_access.py to reduce hotspot size.
No circular imports: this module imports sqlite3 and typing only.
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple


def build_union_sql(smb_where: str, ftp_where: str) -> str:
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


def build_http_arm(http_where: str) -> str:
    """Return the HTTP SELECT arm for the 3-protocol UNION ALL query.

    Produces the same 23 columns in the same order as build_union_sql arms.
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


def get_recent_cutoff(conn: sqlite3.Connection) -> Optional[str]:
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


def query_smb_ftp_http(
    get_connection_fn,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """Execute full UNION ALL query (SMB + FTP + HTTP)."""
    with get_connection_fn() as conn:
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
            cutoff = get_recent_cutoff(conn)
            if cutoff:
                smb_where  += " AND datetime(s.last_seen)  >= datetime(?, '-1 hour')"
                ftp_where  += " AND datetime(f.last_seen)  >= datetime(?, '-1 hour')"
                http_where += " AND datetime(hs.last_seen) >= datetime(?, '-1 hour')"
                smb_params.append(cutoff)
                ftp_params.append(cutoff)
                http_params.append(cutoff)

        union_sql = (
            build_union_sql(smb_where, ftp_where)
            + "\n        UNION ALL\n"
            + build_http_arm(http_where)
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


def query_smb_ftp(
    get_connection_fn,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """Execute SMB + FTP UNION ALL query (tier-2 fallback when HTTP tables absent)."""
    with get_connection_fn() as conn:
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
            cutoff = get_recent_cutoff(conn)
            if cutoff:
                smb_where += " AND datetime(s.last_seen) >= datetime(?, '-1 hour')"
                ftp_where += " AND datetime(f.last_seen) >= datetime(?, '-1 hour')"
                smb_params.append(cutoff)
                ftp_params.append(cutoff)

        union_sql = build_union_sql(smb_where, ftp_where)
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


def query_smb_only(
    get_connection_fn,
    limit: Optional[int],
    offset: int,
    country_filter: Optional[str],
    recent_scan_only: bool,
) -> Tuple[List[Dict], int]:
    """SMB-only fallback used when FTP tables are absent."""
    with get_connection_fn() as conn:
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


def get_mock_list(
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


def get_protocol_server_list(
    get_connection_fn,
    mock_mode: bool,
    limit: Optional[int] = 100,
    offset: int = 0,
    country_filter: Optional[str] = None,
    recent_scan_only: bool = False,
) -> Tuple[List[Dict], int]:
    """
    Tiered dispatch: try SMB+FTP+HTTP, fall back to SMB+FTP, then SMB-only.

    Callers should use DatabaseReader.get_protocol_server_list instead of
    calling this function directly.
    """
    if mock_mode:
        return get_mock_list(limit, offset, country_filter)

    try:
        return query_smb_ftp_http(
            get_connection_fn, limit, offset, country_filter, recent_scan_only
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        # Tier-2: HTTP tables absent (pre-HTTP migration) — try SMB+FTP
        if "no such table: http_" in msg:
            try:
                return query_smb_ftp(
                    get_connection_fn, limit, offset, country_filter, recent_scan_only
                )
            except sqlite3.OperationalError as exc2:
                # Tier-3: FTP tables also absent — fall back to SMB-only
                if "no such table: ftp_" in str(exc2).lower():
                    return query_smb_only(
                        get_connection_fn, limit, offset, country_filter, recent_scan_only
                    )
                raise
        # Tier-3 direct: FTP tables absent without HTTP tables (edge case)
        elif "no such table: ftp_" in msg:
            return query_smb_only(
                get_connection_fn, limit, offset, country_filter, recent_scan_only
            )
        raise
