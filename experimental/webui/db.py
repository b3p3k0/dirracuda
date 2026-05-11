"""Read-only protocol host summaries and database export for the Web UI."""

import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_DB_PATH = Path.home() / ".dirracuda" / "data" / "dirracuda.db"
_EXPORT_DIR = Path.home() / ".dirracuda" / "exports"

_PAGE_MIN, _PAGE_MAX = 1, 10000
_PAGE_SIZE_MIN, _PAGE_SIZE_MAX = 1, 200
_PAGE_SIZE_DEFAULT = 50

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# Allowlist for generated export filenames (timestamp + 8-char hex suffix)
_EXPORT_FILENAME_RE = re.compile(r"^dirracuda_export_\d{8}_\d{6}_[0-9a-f]{8}\.db$")

_PROBE_STATUS_EMOJI = {
    "clean": "✔",
    "issue": "✖",
    "unprobed": "○",
}
_RCE_STATUS_EMOJI = {
    "not_run": "⭘",
    "clean": "✓",
    "flagged": "✖",
    "unknown": "?",
    "error": "⚠",
}


def _validate_bounds(
    page: int,
    page_size: int,
    country: Optional[str],
) -> tuple:
    """Validate and normalise pagination/filter inputs. Raises ValueError on bad input."""
    if page < _PAGE_MIN or page > _PAGE_MAX:
        raise ValueError(f"page must be between {_PAGE_MIN} and {_PAGE_MAX}")
    if page_size < _PAGE_SIZE_MIN or page_size > _PAGE_SIZE_MAX:
        raise ValueError(
            f"page_size must be between {_PAGE_SIZE_MIN} and {_PAGE_SIZE_MAX}"
        )
    clean_country: Optional[str] = None
    if country is not None:
        c = country.strip().upper()
        if c and not _COUNTRY_RE.fullmatch(c):
            raise ValueError("country must be exactly 2 letters A-Z")
        clean_country = c if c else None
    return page, page_size, clean_country


def _inspect_tables(conn: sqlite3.Connection) -> set:
    """Return set of table names present in the DB."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _inspect_columns(conn: sqlite3.Connection, table: str) -> set:
    """Return set of column names for *table*. table must be a literal from our code."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a read-only URI connection. Raises OperationalError if DB absent or locked."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_smb_results(
    db_path: Path,
    page: int,
    page_size: int,
    country: Optional[str],
) -> list:
    """Return paginated SMB host rows with optional share summary."""
    if not Path(db_path).exists():
        return []
    conn = _connect(db_path)
    try:
        tables = _inspect_tables(conn)
        if "smb_servers" not in tables:
            return []

        has_sa = "share_access" in tables
        has_comment = has_sa and "share_comment" in _inspect_columns(conn, "share_access")

        offset = (page - 1) * page_size
        params: list = []
        where_clause = ""
        if country:
            where_clause = "WHERE s.country_code = ?"
            params.append(country)

        if has_sa:
            comment_col = (
                "GROUP_CONCAT(CASE WHEN sa.accessible THEN sa.share_comment END)"
                if has_comment
                else "NULL"
            )
            extra = (
                f"    COUNT(CASE WHEN sa.accessible THEN 1 END) AS accessible_count,\n"
                f"    GROUP_CONCAT(CASE WHEN sa.accessible THEN sa.share_name END)"
                f" AS accessible_names,\n"
                f"    {comment_col} AS share_comments"
            )
            join = "LEFT JOIN share_access sa ON s.id = sa.server_id"
            group = "GROUP BY s.id"
        else:
            extra = "0 AS accessible_count, NULL AS accessible_names, NULL AS share_comments"
            join = ""
            group = ""

        sql = f"""
            SELECT
                s.ip_address, s.country, s.country_code, s.auth_method, s.status,
                s.first_seen, s.last_seen, s.scan_count,
                {extra}
            FROM smb_servers s
            {join}
            {where_clause}
            {group}
            ORDER BY s.last_seen DESC, s.id DESC
            LIMIT ? OFFSET ?
        """
        params += [page_size, offset]
        cur = conn.execute(sql, params)
        result = []
        for row in cur.fetchall():
            r = dict(row)
            names_raw = r.get("accessible_names") or ""
            share_names = [n for n in names_raw.split(",") if n] if names_raw else []
            result.append({
                "ip": r["ip_address"],
                "country": r.get("country"),
                "country_code": r.get("country_code"),
                "auth_method": r.get("auth_method"),
                "status": r.get("status"),
                "first_seen": r.get("first_seen"),
                "last_seen": r.get("last_seen"),
                "scan_count": r.get("scan_count") or 0,
                "accessible_shares": r.get("accessible_count") or 0,
                "share_names": share_names,
                "copy_str": f"smb://{r['ip_address']}",
            })
        return result
    finally:
        conn.close()


def get_ftp_results(
    db_path: Path,
    page: int,
    page_size: int,
    country: Optional[str],
) -> list:
    """Return paginated FTP host rows with optional probe summary."""
    if not Path(db_path).exists():
        return []
    conn = _connect(db_path)
    try:
        tables = _inspect_tables(conn)
        if "ftp_servers" not in tables:
            return []

        has_probe = "ftp_probe_cache" in tables
        has_dirs = has_probe and (
            "accessible_dirs_count" in _inspect_columns(conn, "ftp_probe_cache")
        )

        offset = (page - 1) * page_size
        params: list = []
        where_clause = ""
        if country:
            where_clause = "WHERE s.country_code = ?"
            params.append(country)

        if has_dirs:
            dirs_col = "COALESCE(fpc.accessible_dirs_count, 0) AS accessible_dirs"
            join = "LEFT JOIN ftp_probe_cache fpc ON s.id = fpc.server_id"
        else:
            dirs_col = "0 AS accessible_dirs"
            join = ""

        sql = f"""
            SELECT
                s.ip_address, s.country, s.country_code, s.port, s.anon_accessible,
                s.status, s.first_seen, s.last_seen, s.scan_count,
                {dirs_col}
            FROM ftp_servers s
            {join}
            {where_clause}
            ORDER BY s.last_seen DESC, s.id DESC
            LIMIT ? OFFSET ?
        """
        params += [page_size, offset]
        cur = conn.execute(sql, params)
        result = []
        for row in cur.fetchall():
            r = dict(row)
            port = r.get("port") or 21
            result.append({
                "ip": r["ip_address"],
                "country": r.get("country"),
                "country_code": r.get("country_code"),
                "port": port,
                "anon_accessible": bool(r.get("anon_accessible")),
                "status": r.get("status"),
                "first_seen": r.get("first_seen"),
                "last_seen": r.get("last_seen"),
                "scan_count": r.get("scan_count") or 0,
                "accessible_dirs": r.get("accessible_dirs") or 0,
                "copy_str": f"ftp://{r['ip_address']}:{port}",
            })
        return result
    finally:
        conn.close()


def get_http_results(
    db_path: Path,
    page: int,
    page_size: int,
    country: Optional[str],
) -> list:
    """Return paginated HTTP host rows with optional access summary."""
    if not Path(db_path).exists():
        return []
    conn = _connect(db_path)
    try:
        tables = _inspect_tables(conn)
        if "http_servers" not in tables:
            return []

        has_ha = "http_access" in tables
        if has_ha:
            ha_cols = _inspect_columns(conn, "http_access")
            has_dir_count = "dir_count" in ha_cols
            has_file_count = "file_count" in ha_cols
            has_is_index = "is_index_page" in ha_cols
            has_accessible = "accessible" in ha_cols
        else:
            has_dir_count = has_file_count = has_is_index = has_accessible = False

        offset = (page - 1) * page_size
        params: list = []
        where_clause = ""
        if country:
            where_clause = "WHERE s.country_code = ?"
            params.append(country)

        if has_ha:
            dir_expr = "COALESCE(MAX(ha.dir_count), 0)" if has_dir_count else "0"
            file_expr = "COALESCE(MAX(ha.file_count), 0)" if has_file_count else "0"
            idx_expr = (
                "MAX(CASE WHEN ha.is_index_page THEN 1 ELSE 0 END)"
                if has_is_index
                else "0"
            )
            acc_expr = (
                "MAX(CASE WHEN ha.accessible THEN 1 ELSE 0 END)"
                if has_accessible
                else "0"
            )
            extra = (
                f"    {dir_expr} AS dir_count,\n"
                f"    {file_expr} AS file_count,\n"
                f"    {idx_expr} AS is_index_page,\n"
                f"    {acc_expr} AS last_accessible"
            )
            join = "LEFT JOIN http_access ha ON s.id = ha.server_id"
            group = "GROUP BY s.id"
        else:
            extra = (
                "0 AS dir_count, 0 AS file_count, 0 AS is_index_page, 0 AS last_accessible"
            )
            join = ""
            group = ""

        sql = f"""
            SELECT
                s.ip_address, s.country, s.country_code, s.port, s.scheme, s.title,
                s.status, s.first_seen, s.last_seen, s.scan_count,
                {extra}
            FROM http_servers s
            {join}
            {where_clause}
            {group}
            ORDER BY s.last_seen DESC, s.id DESC
            LIMIT ? OFFSET ?
        """
        params += [page_size, offset]
        cur = conn.execute(sql, params)
        result = []
        for row in cur.fetchall():
            r = dict(row)
            scheme = r.get("scheme") or "http"
            port = r.get("port") or 80
            result.append({
                "ip": r["ip_address"],
                "country": r.get("country"),
                "country_code": r.get("country_code"),
                "port": port,
                "scheme": scheme,
                "title": r.get("title"),
                "status": r.get("status"),
                "first_seen": r.get("first_seen"),
                "last_seen": r.get("last_seen"),
                "scan_count": r.get("scan_count") or 0,
                "dir_count": r.get("dir_count") or 0,
                "file_count": r.get("file_count") or 0,
                "is_index_page": bool(r.get("is_index_page")),
                "last_accessible": bool(r.get("last_accessible")),
                "copy_str": f"{scheme}://{r['ip_address']}:{port}",
            })
        return result
    finally:
        conn.close()


def _table_has_columns(table_columns: dict[str, set[str]], table: str, *columns: str) -> bool:
    cols = table_columns.get(table)
    if not cols:
        return False
    return all(col in cols for col in columns)


def _format_last_seen(value: Optional[str]) -> str:
    if not value:
        return "Never"
    text = str(value).strip()
    if not text:
        return "Never"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _probe_status_to_emoji(status: Optional[str]) -> str:
    return _PROBE_STATUS_EMOJI.get((status or "").strip().lower(), "⚪")


def _rce_status_to_emoji(status: Optional[str]) -> str:
    return _RCE_STATUS_EMOJI.get((status or "").strip().lower(), "⭘")


def _extract_status_to_emoji(extracted: object) -> str:
    try:
        return "✔" if int(extracted) else "○"
    except (TypeError, ValueError):
        return "○"


def _build_smb_arm(
    table_columns: dict[str, set[str]],
    country: Optional[str],
) -> tuple[Optional[str], list]:
    if not _table_has_columns(table_columns, "smb_servers", "id", "ip_address", "last_seen"):
        return None, []

    params: list = []
    where = ""
    if "status" in table_columns["smb_servers"]:
        where = "WHERE s.status = 'active'"
    if country:
        if "country_code" not in table_columns["smb_servers"]:
            return None, []
        if where:
            where += " AND s.country_code = ?"
        else:
            where = "WHERE s.country_code = ?"
        params.append(country)

    accessible_count_expr = "0"
    accessible_list_expr = "''"
    denied_count_expr = "0"
    if _table_has_columns(table_columns, "share_access", "server_id", "accessible"):
        accessible_count_expr = (
            "(SELECT COUNT(1) FROM share_access sa "
            "WHERE sa.server_id = s.id AND COALESCE(sa.accessible, 0) = 1)"
        )
        denied_count_expr = (
            "(SELECT COUNT(1) FROM share_access sa "
            "WHERE sa.server_id = s.id AND COALESCE(sa.accessible, 0) = 0)"
        )
        if "share_name" in table_columns["share_access"]:
            accessible_list_expr = (
                "(SELECT COALESCE(GROUP_CONCAT(sa.share_name, ','), '') "
                "FROM share_access sa "
                "WHERE sa.server_id = s.id AND COALESCE(sa.accessible, 0) = 1)"
            )

    favorite_expr = "0"
    avoid_expr = "0"
    if _table_has_columns(table_columns, "host_user_flags", "server_id"):
        if "favorite" in table_columns["host_user_flags"]:
            favorite_expr = (
                "(SELECT COALESCE(uf.favorite, 0) "
                "FROM host_user_flags uf WHERE uf.server_id = s.id LIMIT 1)"
            )
        if "avoid" in table_columns["host_user_flags"]:
            avoid_expr = (
                "(SELECT COALESCE(uf.avoid, 0) "
                "FROM host_user_flags uf WHERE uf.server_id = s.id LIMIT 1)"
            )

    probe_status_expr = "'unprobed'"
    rce_status_expr = "'not_run'"
    extracted_expr = "0"
    if _table_has_columns(table_columns, "host_probe_cache", "server_id"):
        if "status" in table_columns["host_probe_cache"]:
            probe_status_expr = (
                "(SELECT COALESCE(pc.status, 'unprobed') "
                "FROM host_probe_cache pc WHERE pc.server_id = s.id LIMIT 1)"
            )
        if "rce_status" in table_columns["host_probe_cache"]:
            rce_status_expr = (
                "(SELECT COALESCE(pc.rce_status, 'not_run') "
                "FROM host_probe_cache pc WHERE pc.server_id = s.id LIMIT 1)"
            )
        if "extracted" in table_columns["host_probe_cache"]:
            extracted_expr = (
                "(SELECT COALESCE(pc.extracted, 0) "
                "FROM host_probe_cache pc WHERE pc.server_id = s.id LIMIT 1)"
            )

    sql = f"""
        SELECT
            'S' AS host_type,
            s.id AS protocol_server_id,
            'S:' || CAST(s.id AS TEXT) AS row_key,
            s.ip_address AS ip_address,
            COALESCE(s.country, 'Unknown') AS country,
            s.last_seen AS last_seen,
            {favorite_expr} AS favorite,
            {avoid_expr} AS avoid,
            {probe_status_expr} AS probe_status,
            {rce_status_expr} AS rce_status,
            {extracted_expr} AS extracted,
            {accessible_count_expr} AS accessible_shares,
            {accessible_list_expr} AS accessible_shares_list,
            {denied_count_expr} AS denied_shares_count
        FROM smb_servers s
        {where}
    """
    return sql, params


def _build_ftp_arm(
    table_columns: dict[str, set[str]],
    country: Optional[str],
) -> tuple[Optional[str], list]:
    if not _table_has_columns(table_columns, "ftp_servers", "id", "ip_address", "last_seen"):
        return None, []

    params: list = []
    where = ""
    if "status" in table_columns["ftp_servers"]:
        where = "WHERE f.status = 'active'"
    if country:
        if "country_code" not in table_columns["ftp_servers"]:
            return None, []
        if where:
            where += " AND f.country_code = ?"
        else:
            where = "WHERE f.country_code = ?"
        params.append(country)

    favorite_expr = "0"
    avoid_expr = "0"
    if _table_has_columns(table_columns, "ftp_user_flags", "server_id"):
        if "favorite" in table_columns["ftp_user_flags"]:
            favorite_expr = (
                "(SELECT COALESCE(uf.favorite, 0) "
                "FROM ftp_user_flags uf WHERE uf.server_id = f.id LIMIT 1)"
            )
        if "avoid" in table_columns["ftp_user_flags"]:
            avoid_expr = (
                "(SELECT COALESCE(uf.avoid, 0) "
                "FROM ftp_user_flags uf WHERE uf.server_id = f.id LIMIT 1)"
            )

    probe_status_expr = "'unprobed'"
    rce_status_expr = "'not_run'"
    extracted_expr = "0"
    accessible_count_expr = "0"
    accessible_list_expr = "''"
    if _table_has_columns(table_columns, "ftp_probe_cache", "server_id"):
        if "status" in table_columns["ftp_probe_cache"]:
            probe_status_expr = (
                "(SELECT COALESCE(pc.status, 'unprobed') "
                "FROM ftp_probe_cache pc WHERE pc.server_id = f.id LIMIT 1)"
            )
        if "rce_status" in table_columns["ftp_probe_cache"]:
            rce_status_expr = (
                "(SELECT COALESCE(pc.rce_status, 'not_run') "
                "FROM ftp_probe_cache pc WHERE pc.server_id = f.id LIMIT 1)"
            )
        if "extracted" in table_columns["ftp_probe_cache"]:
            extracted_expr = (
                "(SELECT COALESCE(pc.extracted, 0) "
                "FROM ftp_probe_cache pc WHERE pc.server_id = f.id LIMIT 1)"
            )
        if "accessible_dirs_count" in table_columns["ftp_probe_cache"]:
            accessible_count_expr = (
                "(SELECT COALESCE(pc.accessible_dirs_count, 0) "
                "FROM ftp_probe_cache pc WHERE pc.server_id = f.id LIMIT 1)"
            )
        if "accessible_dirs_list" in table_columns["ftp_probe_cache"]:
            accessible_list_expr = (
                "(SELECT COALESCE(pc.accessible_dirs_list, '') "
                "FROM ftp_probe_cache pc WHERE pc.server_id = f.id LIMIT 1)"
            )

    sql = f"""
        SELECT
            'F' AS host_type,
            f.id AS protocol_server_id,
            'F:' || CAST(f.id AS TEXT) AS row_key,
            f.ip_address AS ip_address,
            COALESCE(f.country, 'Unknown') AS country,
            f.last_seen AS last_seen,
            {favorite_expr} AS favorite,
            {avoid_expr} AS avoid,
            {probe_status_expr} AS probe_status,
            {rce_status_expr} AS rce_status,
            {extracted_expr} AS extracted,
            {accessible_count_expr} AS accessible_shares,
            {accessible_list_expr} AS accessible_shares_list,
            0 AS denied_shares_count
        FROM ftp_servers f
        {where}
    """
    return sql, params


def _build_http_arm(
    table_columns: dict[str, set[str]],
    country: Optional[str],
) -> tuple[Optional[str], list]:
    if not _table_has_columns(table_columns, "http_servers", "id", "ip_address", "last_seen"):
        return None, []

    params: list = []
    where = ""
    if "status" in table_columns["http_servers"]:
        where = "WHERE h.status = 'active'"
    if country:
        if "country_code" not in table_columns["http_servers"]:
            return None, []
        if where:
            where += " AND h.country_code = ?"
        else:
            where = "WHERE h.country_code = ?"
        params.append(country)

    favorite_expr = "0"
    avoid_expr = "0"
    if _table_has_columns(table_columns, "http_user_flags", "server_id"):
        if "favorite" in table_columns["http_user_flags"]:
            favorite_expr = (
                "(SELECT COALESCE(uf.favorite, 0) "
                "FROM http_user_flags uf WHERE uf.server_id = h.id LIMIT 1)"
            )
        if "avoid" in table_columns["http_user_flags"]:
            avoid_expr = (
                "(SELECT COALESCE(uf.avoid, 0) "
                "FROM http_user_flags uf WHERE uf.server_id = h.id LIMIT 1)"
            )

    probe_status_expr = "'unprobed'"
    rce_status_expr = "'not_run'"
    extracted_expr = "0"
    accessible_count_expr = "0"
    accessible_list_expr = "''"
    if _table_has_columns(table_columns, "http_probe_cache", "server_id"):
        if "status" in table_columns["http_probe_cache"]:
            probe_status_expr = (
                "(SELECT COALESCE(pc.status, 'unprobed') "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        if "rce_status" in table_columns["http_probe_cache"]:
            rce_status_expr = (
                "(SELECT COALESCE(pc.rce_status, 'not_run') "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        if "extracted" in table_columns["http_probe_cache"]:
            extracted_expr = (
                "(SELECT COALESCE(pc.extracted, 0) "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        has_dirs = "accessible_dirs_count" in table_columns["http_probe_cache"]
        has_files = "accessible_files_count" in table_columns["http_probe_cache"]
        if has_dirs and has_files:
            accessible_count_expr = (
                "(SELECT COALESCE(pc.accessible_dirs_count, 0) + "
                "COALESCE(pc.accessible_files_count, 0) "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        elif has_dirs:
            accessible_count_expr = (
                "(SELECT COALESCE(pc.accessible_dirs_count, 0) "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        elif has_files:
            accessible_count_expr = (
                "(SELECT COALESCE(pc.accessible_files_count, 0) "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )
        if "accessible_dirs_list" in table_columns["http_probe_cache"]:
            accessible_list_expr = (
                "(SELECT COALESCE(pc.accessible_dirs_list, '') "
                "FROM http_probe_cache pc WHERE pc.server_id = h.id LIMIT 1)"
            )

    sql = f"""
        SELECT
            'H' AS host_type,
            h.id AS protocol_server_id,
            'H:' || CAST(h.id AS TEXT) AS row_key,
            h.ip_address AS ip_address,
            COALESCE(h.country, 'Unknown') AS country,
            h.last_seen AS last_seen,
            {favorite_expr} AS favorite,
            {avoid_expr} AS avoid,
            {probe_status_expr} AS probe_status,
            {rce_status_expr} AS rce_status,
            {extracted_expr} AS extracted,
            {accessible_count_expr} AS accessible_shares,
            {accessible_list_expr} AS accessible_shares_list,
            0 AS denied_shares_count
        FROM http_servers h
        {where}
    """
    return sql, params


def get_results_table_rows(
    db_path: Path,
    protocol: str,
    page: int,
    page_size: int,
    country: Optional[str],
    shares_only: bool = False,
    favorites_only: bool = False,
    hide_avoid: bool = False,
) -> tuple[list[dict], int]:
    """Return desktop-parity rows for one protocol/all with optional server-side filters."""
    if not Path(db_path).exists():
        return [], 0

    conn = _connect(db_path)
    try:
        tables = _inspect_tables(conn)
        table_columns: dict[str, set[str]] = {}
        for table in (
            "smb_servers",
            "ftp_servers",
            "http_servers",
            "share_access",
            "host_user_flags",
            "ftp_user_flags",
            "http_user_flags",
            "host_probe_cache",
            "ftp_probe_cache",
            "http_probe_cache",
        ):
            if table in tables:
                table_columns[table] = _inspect_columns(conn, table)

        arms: list[str] = []
        params: list = []

        if protocol in ("all", "smb"):
            smb_sql, smb_params = _build_smb_arm(table_columns, country)
            if smb_sql:
                arms.append(smb_sql)
                params.extend(smb_params)

        if protocol in ("all", "ftp"):
            ftp_sql, ftp_params = _build_ftp_arm(table_columns, country)
            if ftp_sql:
                arms.append(ftp_sql)
                params.extend(ftp_params)

        if protocol in ("all", "http"):
            http_sql, http_params = _build_http_arm(table_columns, country)
            if http_sql:
                arms.append(http_sql)
                params.extend(http_params)

        if not arms:
            return [], 0

        union_sql = "\nUNION ALL\n".join(arms)
        filter_clauses: list[str] = []
        if shares_only:
            filter_clauses.append("COALESCE(accessible_shares, 0) > 0")
        if favorites_only:
            filter_clauses.append("COALESCE(favorite, 0) = 1")
        if hide_avoid:
            filter_clauses.append("COALESCE(avoid, 0) != 1")
        filter_sql = f"WHERE {' AND '.join(filter_clauses)}" if filter_clauses else ""

        count_sql = f"SELECT COUNT(*) AS total_count FROM ({union_sql}) _u {filter_sql}"
        total_count = int(conn.execute(count_sql, params).fetchone()["total_count"])

        offset = (page - 1) * page_size
        data_sql = (
            f"SELECT * FROM ({union_sql}) _u {filter_sql} "
            "ORDER BY datetime(last_seen) DESC, row_key ASC "
            "LIMIT ? OFFSET ?"
        )
        rows = conn.execute(data_sql, params + [page_size, offset]).fetchall()

        result_rows: list[dict] = []
        for row in rows:
            data = dict(row)
            shares_count = int(data.get("accessible_shares") or 0)
            result_rows.append(
                {
                    "favorite": "✔" if int(data.get("favorite") or 0) else "○",
                    "avoid": "✖" if int(data.get("avoid") or 0) else "○",
                    "probe_status_emoji": _probe_status_to_emoji(data.get("probe_status")),
                    "rce_status_emoji": _rce_status_to_emoji(data.get("rce_status")),
                    "extract_status_emoji": _extract_status_to_emoji(data.get("extracted")),
                    "host_type": data.get("host_type") or "",
                    "ip_address": data.get("ip_address") or "",
                    "shares": f"📁 {shares_count}" if shares_count > 0 else str(shares_count),
                    "accessible_shares_list": data.get("accessible_shares_list") or "",
                    "denied_shares_count": int(data.get("denied_shares_count") or 0),
                    "last_seen": _format_last_seen(data.get("last_seen")),
                    "country": data.get("country") or "Unknown",
                }
            )
        return result_rows, total_count
    finally:
        conn.close()


def export_db(db_path: Path, export_dir: Path) -> Path:
    """
    Export the database using VACUUM INTO — same contract as gui export_database().

    Opens source with mode=rw (no-create) so a missing source raises OperationalError
    rather than silently creating an empty DB and exporting nothing.
    Returns the path of the created export artifact.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(4)
    filename = f"dirracuda_export_{timestamp}_{suffix}.db"
    dest_path = export_dir / filename
    if dest_path.exists():
        suffix = secrets.token_hex(4)
        filename = f"dirracuda_export_{timestamp}_{suffix}.db"
        dest_path = export_dir / filename

    # mode=rw: refuses to create a new file; raises OperationalError if db_path absent
    conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(dest_path),))
    finally:
        conn.close()
    return dest_path
