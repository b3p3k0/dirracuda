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
