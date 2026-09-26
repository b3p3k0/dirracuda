"""One host's full detail payload for the Web UI (read-only).

Split out of ``db.py``, which also builds the paginated results tables. The
two jobs share only the small helpers in ``db_common``. ``db.py`` re-exports
``get_result_details`` so ``experimental.webui.db.get_result_details`` keeps
resolving for ``app.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from experimental.webui.db_common import (
    _connect,
    _format_last_seen,
    _inspect_columns,
    _inspect_tables,
    _table_has_columns,
    _to_int,
)
from experimental.webui.open_with_url import (
    build_open_with_url as _build_open_with_url,
    safe_port as _safe_port,
    snapshot_explicit_base_path as _snapshot_explicit_base_path,
    snapshot_open_path_candidates as _snapshot_open_path_candidates,
    split_csv_items as _split_csv_items,
)


def _normalize_snapshot_path(path_value: object) -> list[str]:
    text = _clean_text(path_value).replace("\\", "/")
    if not text:
        return []
    return [segment for segment in text.split("/") if segment and segment != "."]


def _build_snapshot_tree(subdirectories: object, files: object) -> dict[str, Any]:
    root: dict[str, Any] = {"dirs": {}, "files": []}

    for raw_subdir in _safe_list(subdirectories):
        segments = _normalize_snapshot_path(raw_subdir)
        if not segments:
            continue
        node = root
        for segment in segments:
            child = node["dirs"].get(segment)
            if child is None:
                child = {"dirs": {}, "files": []}
                node["dirs"][segment] = child
            node = child

    for raw_file in _safe_list(files):
        segments = _normalize_snapshot_path(raw_file)
        if not segments:
            continue
        *folder_segments, file_name = segments
        node = root
        for segment in folder_segments:
            child = node["dirs"].get(segment)
            if child is None:
                child = {"dirs": {}, "files": []}
                node["dirs"][segment] = child
            node = child
        if file_name not in node["files"]:
            node["files"].append(file_name)

    return root


def _render_snapshot_tree(tree: dict[str, Any], lines: list[str], indent: int) -> int:
    emitted = 0
    prefix = " " * indent
    for dir_name, child in tree["dirs"].items():
        lines.append(f"{prefix}📁 {dir_name}/")
        emitted += 1
        emitted += _render_snapshot_tree(child, lines, indent + 3)
    for file_name in tree["files"]:
        lines.append(f"{prefix}• {file_name}")
        emitted += 1
    return emitted


def _format_root_file_name(item: object) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return _clean_text(
            item.get("name")
            or item.get("file_name")
            or item.get("file")
        )
    return _clean_text(item)


def _latest_snapshot_id_for_host(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    host_type: str,
    server_id: int,
) -> Optional[int]:
    cache_table = {
        "S": "host_probe_cache",
        "F": "ftp_probe_cache",
        "H": "http_probe_cache",
    }.get(host_type)
    if not cache_table:
        return None
    if not _table_has_columns(table_columns, cache_table, "server_id", "latest_snapshot_id"):
        return None
    row = conn.execute(
        f"SELECT latest_snapshot_id FROM {cache_table} WHERE server_id = ? LIMIT 1",
        [server_id],
    ).fetchone()
    if row is None or row["latest_snapshot_id"] is None:
        return None
    try:
        return int(row["latest_snapshot_id"])
    except (TypeError, ValueError):
        return None


def _load_probe_snapshot_payload(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    host_type: str,
    server_id: int,
) -> Optional[dict]:
    snapshot_id = _latest_snapshot_id_for_host(conn, table_columns, host_type, server_id)
    if snapshot_id is None:
        return None
    if not _table_has_columns(table_columns, "probe_snapshots", "id", "raw_snapshot_json"):
        return None

    row = conn.execute(
        "SELECT raw_snapshot_json FROM probe_snapshots WHERE id = ? LIMIT 1",
        [snapshot_id],
    ).fetchone()
    if row is None or not row["raw_snapshot_json"]:
        return None
    try:
        payload = json.loads(row["raw_snapshot_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _probe_tree_lines(
    snapshot: Optional[dict],
    *,
    fallback_paths: str = "",
) -> list[str]:
    lines = ["Probe Snapshot Tree (stored):"]
    if not isinstance(snapshot, dict):
        lines.append("  (no stored tree available)")
        if fallback_paths:
            lines.append(f"  Fallback Paths: {fallback_paths}")
        return lines

    shares = _safe_list(snapshot.get("shares"))
    if not shares:
        lines.append("  (stored snapshot has no shares)")
        if fallback_paths:
            lines.append(f"  Fallback Paths: {fallback_paths}")
        return lines

    for idx, share in enumerate(shares, start=1):
        if not isinstance(share, dict):
            continue
        share_name = _clean_text(share.get("share")) or f"share_{idx}"
        lines.append(f"  Share: {share_name}")

        root_files = []
        for root_item in _safe_list(share.get("root_files")):
            file_name = _format_root_file_name(root_item)
            if file_name:
                root_files.append(file_name)
        if root_files:
            lines.append("    Root files:")
            for file_name in root_files:
                lines.append(f"      • {file_name}")
            if share.get("root_files_truncated"):
                lines.append("      … additional root files not shown")

        directories = _safe_list(share.get("directories"))
        if not directories:
            if not root_files:
                lines.append("    (no directories returned)")
            continue

        for directory in directories:
            if not isinstance(directory, dict):
                continue
            dir_name = _clean_text(directory.get("name")) or "(unnamed)"
            lines.append(f"    📁 {dir_name}/")
            tree = _build_snapshot_tree(
                directory.get("subdirectories"),
                directory.get("files"),
            )
            if _render_snapshot_tree(tree, lines, indent=7) == 0:
                lines.append("       (no files or subdirectories listed)")
            if directory.get("subdirectories_truncated"):
                lines.append("       … additional subdirectories not shown")
            if directory.get("files_truncated"):
                lines.append("       … additional files not shown")
        if share.get("directories_truncated"):
            lines.append("    … additional directories not shown")

    if fallback_paths:
        lines.append(f"  Fallback Paths: {fallback_paths}")
    return lines


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _country_display(country: object, country_code: object) -> str:
    c = _clean_text(country) or "Unknown"
    cc = _clean_text(country_code)
    if cc:
        return f"{c} ({cc})"
    return c


def _safe_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return []


def _get_smb_detail(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_id: int,
) -> Optional[dict]:
    if not _table_has_columns(table_columns, "smb_servers", "id", "ip_address"):
        return None

    smb_cols = table_columns["smb_servers"]
    sql = f"""
        SELECT
            s.id AS protocol_server_id,
            s.ip_address AS ip_address,
            { "COALESCE(s.country, 'Unknown')" if "country" in smb_cols else "'Unknown'" } AS country,
            { "COALESCE(s.country_code, '')" if "country_code" in smb_cols else "''" } AS country_code,
            { "COALESCE(s.status, 'unknown')" if "status" in smb_cols else "'unknown'" } AS status,
            { "COALESCE(s.first_seen, '')" if "first_seen" in smb_cols else "''" } AS first_seen,
            { "COALESCE(s.last_seen, '')" if "last_seen" in smb_cols else "''" } AS last_seen,
            { "COALESCE(s.scan_count, 0)" if "scan_count" in smb_cols else "0" } AS scan_count,
            { "COALESCE(s.auth_method, '')" if "auth_method" in smb_cols else "''" } AS auth_method,
            { "COALESCE(s.notes, '')" if "notes" in smb_cols else "''" } AS server_notes,
            {
                "(SELECT COALESCE(uf.notes, '') FROM host_user_flags uf "
                "WHERE uf.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_user_flags", "server_id", "notes")
                else "''"
            } AS user_notes,
            {
                "(SELECT COALESCE(uf.favorite, 0) FROM host_user_flags uf "
                "WHERE uf.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_user_flags", "server_id", "favorite")
                else "0"
            } AS favorite,
            {
                "(SELECT COALESCE(uf.avoid, 0) FROM host_user_flags uf "
                "WHERE uf.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_user_flags", "server_id", "avoid")
                else "0"
            } AS avoid,
            {
                "(SELECT COALESCE(pc.status, 'unprobed') FROM host_probe_cache pc "
                "WHERE pc.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_probe_cache", "server_id", "status")
                else "'unprobed'"
            } AS probe_status,
            {
                "(SELECT COALESCE(pc.extracted, 0) FROM host_probe_cache pc "
                "WHERE pc.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_probe_cache", "server_id", "extracted")
                else "0"
            } AS extracted,
            {
                "(SELECT COALESCE(pc.indicator_matches, 0) FROM host_probe_cache pc "
                "WHERE pc.server_id = s.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "host_probe_cache", "server_id", "indicator_matches"
                )
                else "0"
            } AS indicator_matches,
            {
                "(SELECT COALESCE(pc.indicator_samples, '') FROM host_probe_cache pc "
                "WHERE pc.server_id = s.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "host_probe_cache", "server_id", "indicator_samples"
                )
                else "''"
            } AS indicator_samples,
            {
                "(SELECT COALESCE(pc.snapshot_path, '') FROM host_probe_cache pc "
                "WHERE pc.server_id = s.id LIMIT 1)"
                if _table_has_columns(table_columns, "host_probe_cache", "server_id", "snapshot_path")
                else "''"
            } AS snapshot_path
        FROM smb_servers s
        WHERE s.id = ?
        LIMIT 1
    """
    row = conn.execute(sql, [server_id]).fetchone()
    if row is None:
        return None

    data = dict(row)
    accessible_names: list[str] = []
    denied_names: list[str] = []
    accessible_count = 0
    denied_count = 0

    if _table_has_columns(table_columns, "share_access", "server_id", "accessible"):
        has_name = "share_name" in table_columns.get("share_access", set())
        if has_name:
            share_rows = conn.execute(
                """
                SELECT
                    COALESCE(share_name, '') AS share_name,
                    COALESCE(accessible, 0) AS accessible
                FROM share_access
                WHERE server_id = ?
                ORDER BY id DESC
                LIMIT 500
                """,
                [server_id],
            ).fetchall()
            for item in share_rows:
                name = _clean_text(item["share_name"])
                if _to_int(item["accessible"]) == 1:
                    accessible_count += 1
                    if name:
                        accessible_names.append(name)
                else:
                    denied_count += 1
                    if name:
                        denied_names.append(name)
        else:
            count_row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN COALESCE(accessible, 0) = 1 THEN 1 ELSE 0 END) AS accessible_count,
                    SUM(CASE WHEN COALESCE(accessible, 0) = 0 THEN 1 ELSE 0 END) AS denied_count
                FROM share_access
                WHERE server_id = ?
                """,
                [server_id],
            ).fetchone()
            accessible_count = _to_int(count_row["accessible_count"])
            denied_count = _to_int(count_row["denied_count"])

    notes = _first_non_empty(data.get("user_notes"), data.get("server_notes"))
    access_summary = f"accessible={accessible_count}, denied={denied_count}"
    snapshot = _load_probe_snapshot_payload(
        conn,
        table_columns,
        "S",
        _to_int(data.get("protocol_server_id")),
    )
    tree_fallback = ", ".join(accessible_names) if accessible_names else "(none)"
    tree_lines = _probe_tree_lines(snapshot, fallback_paths=tree_fallback)
    open_path_candidates = list(accessible_names)
    open_path_candidates.extend(
        _snapshot_open_path_candidates(snapshot, include_share_names=True)
    )
    open_with_url = _build_open_with_url(
        scheme="smb",
        host=data.get("ip_address"),
        path_candidates=open_path_candidates,
    )

    full_lines = [
        "Protocol: SMB",
        f"Host ID: {_to_int(data.get('protocol_server_id'))}",
        f"IP Address: {_clean_text(data.get('ip_address'))}",
        f"Status: {_clean_text(data.get('status')) or 'unknown'}",
        f"First Seen: {_clean_text(data.get('first_seen')) or 'n/a'}",
        f"Last Seen: {_clean_text(data.get('last_seen')) or 'n/a'}",
        f"Country: {_country_display(data.get('country'), data.get('country_code'))}",
        f"Scan Count: {_to_int(data.get('scan_count'))}",
        f"Auth Method: {_clean_text(data.get('auth_method')) or 'unknown'}",
        f"Favorite: {'yes' if _to_int(data.get('favorite')) else 'no'}",
        f"Avoid: {'yes' if _to_int(data.get('avoid')) else 'no'}",
        f"Accessible Shares ({accessible_count}): {', '.join(accessible_names) or '(none)'}",
        f"Denied Shares ({denied_count}): {', '.join(denied_names) or '(none)'}",
        "",
        *tree_lines,
        "",
        "Probe Summary:",
        f"Probe Status: {_clean_text(data.get('probe_status')) or 'unprobed'}",
        f"Extracted: {'yes' if _to_int(data.get('extracted')) else 'no'}",
        f"Indicator Matches: {_to_int(data.get('indicator_matches'))}",
        f"Indicator Samples: {_clean_text(data.get('indicator_samples')) or '(none)'}",
        f"Snapshot Path: {_clean_text(data.get('snapshot_path')) or '(none)'}",
        f"Notes: {notes or '(none)'}",
    ]

    return {
        "row_key": f"S:{_to_int(data.get('protocol_server_id'))}",
        "host_type": "S",
        "protocol": "SMB",
        "protocol_server_id": _to_int(data.get("protocol_server_id")),
        "ip_address": _clean_text(data.get("ip_address")),
        "open_with_url": open_with_url,
        "overview": {
            "protocol": "SMB",
            "status": _clean_text(data.get("status")) or "unknown",
            "last_seen": _format_last_seen(data.get("last_seen")),
            "country": _clean_text(data.get("country")) or "Unknown",
            "country_code": _clean_text(data.get("country_code")),
            "scan_count": _to_int(data.get("scan_count")),
            "auth_method": _clean_text(data.get("auth_method")) or "unknown",
            "access_summary": access_summary,
            "probe_status": _clean_text(data.get("probe_status")) or "unprobed",
            "extracted": bool(_to_int(data.get("extracted"))),
        },
        "notes": notes,
        "full_details_text": "\n".join(full_lines),
    }


def _get_ftp_latest_access(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_id: int,
) -> dict:
    if not _table_has_columns(table_columns, "ftp_access", "server_id", "id"):
        return {}
    cols = table_columns["ftp_access"]
    sql = f"""
        SELECT
            { "COALESCE(accessible, 0)" if "accessible" in cols else "0" } AS accessible,
            { "COALESCE(auth_status, '')" if "auth_status" in cols else "''" } AS auth_status,
            { "COALESCE(root_listing_available, 0)" if "root_listing_available" in cols else "0" } AS root_listing_available,
            { "COALESCE(root_entry_count, 0)" if "root_entry_count" in cols else "0" } AS root_entry_count,
            { "COALESCE(error_message, '')" if "error_message" in cols else "''" } AS error_message,
            { "COALESCE(test_timestamp, '')" if "test_timestamp" in cols else "''" } AS test_timestamp
        FROM ftp_access
        WHERE server_id = ?
        ORDER BY datetime(test_timestamp) DESC, id DESC
        LIMIT 1
    """
    row = conn.execute(sql, [server_id]).fetchone()
    return dict(row) if row is not None else {}


def _get_ftp_detail(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_id: int,
) -> Optional[dict]:
    if not _table_has_columns(table_columns, "ftp_servers", "id", "ip_address"):
        return None

    ftp_cols = table_columns["ftp_servers"]
    sql = f"""
        SELECT
            f.id AS protocol_server_id,
            f.ip_address AS ip_address,
            { "COALESCE(f.country, 'Unknown')" if "country" in ftp_cols else "'Unknown'" } AS country,
            { "COALESCE(f.country_code, '')" if "country_code" in ftp_cols else "''" } AS country_code,
            { "COALESCE(f.status, 'unknown')" if "status" in ftp_cols else "'unknown'" } AS status,
            { "COALESCE(f.first_seen, '')" if "first_seen" in ftp_cols else "''" } AS first_seen,
            { "COALESCE(f.last_seen, '')" if "last_seen" in ftp_cols else "''" } AS last_seen,
            { "COALESCE(f.scan_count, 0)" if "scan_count" in ftp_cols else "0" } AS scan_count,
            { "COALESCE(f.port, 21)" if "port" in ftp_cols else "21" } AS port,
            { "COALESCE(f.anon_accessible, 0)" if "anon_accessible" in ftp_cols else "0" } AS anon_accessible,
            { "COALESCE(f.banner, '')" if "banner" in ftp_cols else "''" } AS banner,
            { "COALESCE(f.notes, '')" if "notes" in ftp_cols else "''" } AS server_notes,
            {
                "(SELECT COALESCE(uf.notes, '') FROM ftp_user_flags uf "
                "WHERE uf.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_user_flags", "server_id", "notes")
                else "''"
            } AS user_notes,
            {
                "(SELECT COALESCE(uf.favorite, 0) FROM ftp_user_flags uf "
                "WHERE uf.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_user_flags", "server_id", "favorite")
                else "0"
            } AS favorite,
            {
                "(SELECT COALESCE(uf.avoid, 0) FROM ftp_user_flags uf "
                "WHERE uf.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_user_flags", "server_id", "avoid")
                else "0"
            } AS avoid,
            {
                "(SELECT COALESCE(pc.status, 'unprobed') FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_probe_cache", "server_id", "status")
                else "'unprobed'"
            } AS probe_status,
            {
                "(SELECT COALESCE(pc.extracted, 0) FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_probe_cache", "server_id", "extracted")
                else "0"
            } AS extracted,
            {
                "(SELECT COALESCE(pc.indicator_matches, 0) FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "ftp_probe_cache", "server_id", "indicator_matches"
                )
                else "0"
            } AS indicator_matches,
            {
                "(SELECT COALESCE(pc.indicator_samples, '') FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "ftp_probe_cache", "server_id", "indicator_samples"
                )
                else "''"
            } AS indicator_samples,
            {
                "(SELECT COALESCE(pc.snapshot_path, '') FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(table_columns, "ftp_probe_cache", "server_id", "snapshot_path")
                else "''"
            } AS snapshot_path,
            {
                "(SELECT COALESCE(pc.accessible_dirs_count, 0) FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "ftp_probe_cache", "server_id", "accessible_dirs_count"
                )
                else "0"
            } AS accessible_dirs_count,
            {
                "(SELECT COALESCE(pc.accessible_dirs_list, '') FROM ftp_probe_cache pc "
                "WHERE pc.server_id = f.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "ftp_probe_cache", "server_id", "accessible_dirs_list"
                )
                else "''"
            } AS accessible_dirs_list
        FROM ftp_servers f
        WHERE f.id = ?
        LIMIT 1
    """
    row = conn.execute(sql, [server_id]).fetchone()
    if row is None:
        return None

    data = dict(row)
    access = _get_ftp_latest_access(conn, table_columns, server_id)
    notes = _first_non_empty(data.get("user_notes"), data.get("server_notes"))
    dirs_count = _to_int(data.get("accessible_dirs_count"))
    access_summary = f"dirs={dirs_count}, denied=0"
    snapshot = _load_probe_snapshot_payload(
        conn,
        table_columns,
        "F",
        _to_int(data.get("protocol_server_id")),
    )
    tree_lines = _probe_tree_lines(
        snapshot,
        fallback_paths=_clean_text(data.get("accessible_dirs_list")) or "(none)",
    )
    open_path_candidates = _split_csv_items(data.get("accessible_dirs_list"))
    open_path_candidates.extend(_snapshot_open_path_candidates(snapshot))
    open_base_path = _snapshot_explicit_base_path(snapshot)
    open_with_url = _build_open_with_url(
        scheme="ftp",
        host=data.get("ip_address"),
        default_port=_safe_port(data.get("port"), 21),
        path_candidates=open_path_candidates,
        explicit_base_path=open_base_path,
        prefer_root_when_ambiguous=True,
    )

    full_lines = [
        "Protocol: FTP",
        f"Host ID: {_to_int(data.get('protocol_server_id'))}",
        f"IP Address: {_clean_text(data.get('ip_address'))}",
        f"Port: {_to_int(data.get('port'), 21)}",
        f"Status: {_clean_text(data.get('status')) or 'unknown'}",
        f"First Seen: {_clean_text(data.get('first_seen')) or 'n/a'}",
        f"Last Seen: {_clean_text(data.get('last_seen')) or 'n/a'}",
        f"Country: {_country_display(data.get('country'), data.get('country_code'))}",
        f"Scan Count: {_to_int(data.get('scan_count'))}",
        f"Anonymous Accessible: {'yes' if _to_int(data.get('anon_accessible')) else 'no'}",
        f"Banner: {_clean_text(data.get('banner')) or '(none)'}",
        f"Favorite: {'yes' if _to_int(data.get('favorite')) else 'no'}",
        f"Avoid: {'yes' if _to_int(data.get('avoid')) else 'no'}",
        "",
        *tree_lines,
        "",
        "Probe Summary:",
        f"Probe Status: {_clean_text(data.get('probe_status')) or 'unprobed'}",
        f"Extracted: {'yes' if _to_int(data.get('extracted')) else 'no'}",
        f"Accessible Dirs ({dirs_count}): {_clean_text(data.get('accessible_dirs_list')) or '(none)'}",
        f"Indicator Matches: {_to_int(data.get('indicator_matches'))}",
        f"Indicator Samples: {_clean_text(data.get('indicator_samples')) or '(none)'}",
        f"Snapshot Path: {_clean_text(data.get('snapshot_path')) or '(none)'}",
        "",
        f"Latest FTP Access: {'found' if access else 'none'}",
        f"  Accessible: {'yes' if _to_int(access.get('accessible')) else 'no'}",
        f"  Auth Status: {_clean_text(access.get('auth_status')) or '(none)'}",
        f"  Root Listing Available: {'yes' if _to_int(access.get('root_listing_available')) else 'no'}",
        f"  Root Entry Count: {_to_int(access.get('root_entry_count'))}",
        f"  Test Timestamp: {_clean_text(access.get('test_timestamp')) or '(none)'}",
        f"  Error Message: {_clean_text(access.get('error_message')) or '(none)'}",
        f"Notes: {notes or '(none)'}",
    ]

    return {
        "row_key": f"F:{_to_int(data.get('protocol_server_id'))}",
        "host_type": "F",
        "protocol": "FTP",
        "protocol_server_id": _to_int(data.get("protocol_server_id")),
        "ip_address": _clean_text(data.get("ip_address")),
        "open_with_url": open_with_url,
        "overview": {
            "protocol": "FTP",
            "status": _clean_text(data.get("status")) or "unknown",
            "last_seen": _format_last_seen(data.get("last_seen")),
            "country": _clean_text(data.get("country")) or "Unknown",
            "country_code": _clean_text(data.get("country_code")),
            "scan_count": _to_int(data.get("scan_count")),
            "auth_method": "anonymous" if _to_int(data.get("anon_accessible")) else "unknown",
            "access_summary": access_summary,
            "probe_status": _clean_text(data.get("probe_status")) or "unprobed",
            "extracted": bool(_to_int(data.get("extracted"))),
        },
        "notes": notes,
        "full_details_text": "\n".join(full_lines),
    }


def _get_http_latest_access(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_id: int,
) -> dict:
    if not _table_has_columns(table_columns, "http_access", "server_id", "id"):
        return {}
    cols = table_columns["http_access"]
    sql = f"""
        SELECT
            { "COALESCE(accessible, 0)" if "accessible" in cols else "0" } AS accessible,
            { "COALESCE(status_code, 0)" if "status_code" in cols else "0" } AS status_code,
            { "COALESCE(is_index_page, 0)" if "is_index_page" in cols else "0" } AS is_index_page,
            { "COALESCE(dir_count, 0)" if "dir_count" in cols else "0" } AS dir_count,
            { "COALESCE(file_count, 0)" if "file_count" in cols else "0" } AS file_count,
            { "COALESCE(tls_verified, 0)" if "tls_verified" in cols else "0" } AS tls_verified,
            { "COALESCE(error_message, '')" if "error_message" in cols else "''" } AS error_message,
            { "COALESCE(test_timestamp, '')" if "test_timestamp" in cols else "''" } AS test_timestamp
        FROM http_access
        WHERE server_id = ?
        ORDER BY datetime(test_timestamp) DESC, id DESC
        LIMIT 1
    """
    row = conn.execute(sql, [server_id]).fetchone()
    return dict(row) if row is not None else {}


def _get_http_detail(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_id: int,
) -> Optional[dict]:
    if not _table_has_columns(table_columns, "http_servers", "id", "ip_address"):
        return None

    http_cols = table_columns["http_servers"]
    sql = f"""
        SELECT
            h.id AS protocol_server_id,
            h.ip_address AS ip_address,
            { "COALESCE(h.country, 'Unknown')" if "country" in http_cols else "'Unknown'" } AS country,
            { "COALESCE(h.country_code, '')" if "country_code" in http_cols else "''" } AS country_code,
            { "COALESCE(h.status, 'unknown')" if "status" in http_cols else "'unknown'" } AS status,
            { "COALESCE(h.first_seen, '')" if "first_seen" in http_cols else "''" } AS first_seen,
            { "COALESCE(h.last_seen, '')" if "last_seen" in http_cols else "''" } AS last_seen,
            { "COALESCE(h.scan_count, 0)" if "scan_count" in http_cols else "0" } AS scan_count,
            { "COALESCE(h.port, 80)" if "port" in http_cols else "80" } AS port,
            { "COALESCE(h.scheme, 'http')" if "scheme" in http_cols else "'http'" } AS scheme,
            { "COALESCE(h.title, '')" if "title" in http_cols else "''" } AS title,
            { "COALESCE(h.banner, '')" if "banner" in http_cols else "''" } AS banner,
            { "COALESCE(h.notes, '')" if "notes" in http_cols else "''" } AS server_notes,
            {
                "(SELECT COALESCE(uf.notes, '') FROM http_user_flags uf "
                "WHERE uf.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_user_flags", "server_id", "notes")
                else "''"
            } AS user_notes,
            {
                "(SELECT COALESCE(uf.favorite, 0) FROM http_user_flags uf "
                "WHERE uf.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_user_flags", "server_id", "favorite")
                else "0"
            } AS favorite,
            {
                "(SELECT COALESCE(uf.avoid, 0) FROM http_user_flags uf "
                "WHERE uf.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_user_flags", "server_id", "avoid")
                else "0"
            } AS avoid,
            {
                "(SELECT COALESCE(pc.status, 'unprobed') FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_probe_cache", "server_id", "status")
                else "'unprobed'"
            } AS probe_status,
            {
                "(SELECT COALESCE(pc.extracted, 0) FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_probe_cache", "server_id", "extracted")
                else "0"
            } AS extracted,
            {
                "(SELECT COALESCE(pc.indicator_matches, 0) FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "http_probe_cache", "server_id", "indicator_matches"
                )
                else "0"
            } AS indicator_matches,
            {
                "(SELECT COALESCE(pc.indicator_samples, '') FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "http_probe_cache", "server_id", "indicator_samples"
                )
                else "''"
            } AS indicator_samples,
            {
                "(SELECT COALESCE(pc.snapshot_path, '') FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(table_columns, "http_probe_cache", "server_id", "snapshot_path")
                else "''"
            } AS snapshot_path,
            {
                "(SELECT COALESCE(pc.accessible_dirs_count, 0) FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "http_probe_cache", "server_id", "accessible_dirs_count"
                )
                else "0"
            } AS accessible_dirs_count,
            {
                "(SELECT COALESCE(pc.accessible_files_count, 0) FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "http_probe_cache", "server_id", "accessible_files_count"
                )
                else "0"
            } AS accessible_files_count,
            {
                "(SELECT COALESCE(pc.accessible_dirs_list, '') FROM http_probe_cache pc "
                "WHERE pc.server_id = h.id LIMIT 1)"
                if _table_has_columns(
                    table_columns, "http_probe_cache", "server_id", "accessible_dirs_list"
                )
                else "''"
            } AS accessible_dirs_list
        FROM http_servers h
        WHERE h.id = ?
        LIMIT 1
    """
    row = conn.execute(sql, [server_id]).fetchone()
    if row is None:
        return None

    data = dict(row)
    access = _get_http_latest_access(conn, table_columns, server_id)
    notes = _first_non_empty(data.get("user_notes"), data.get("server_notes"))

    dirs_count = _to_int(data.get("accessible_dirs_count"))
    files_count = _to_int(data.get("accessible_files_count"))
    access_summary = f"dirs={dirs_count}, files={files_count}"
    snapshot = _load_probe_snapshot_payload(
        conn,
        table_columns,
        "H",
        _to_int(data.get("protocol_server_id")),
    )
    tree_lines = _probe_tree_lines(
        snapshot,
        fallback_paths=_clean_text(data.get("accessible_dirs_list")) or "(none)",
    )
    open_scheme = _clean_text(data.get("scheme")).lower()
    if open_scheme not in {"http", "https"}:
        open_scheme = "http"
    open_default_port = 443 if open_scheme == "https" else 80
    open_path_candidates = _split_csv_items(data.get("accessible_dirs_list"))
    open_path_candidates.extend(_snapshot_open_path_candidates(snapshot))
    open_base_path = _snapshot_explicit_base_path(snapshot)
    open_with_url = _build_open_with_url(
        scheme=open_scheme,
        host=data.get("ip_address"),
        default_port=_safe_port(data.get("port"), open_default_port),
        path_candidates=open_path_candidates,
        explicit_base_path=open_base_path,
        prefer_root_when_ambiguous=True,
    )

    full_lines = [
        "Protocol: HTTP",
        f"Host ID: {_to_int(data.get('protocol_server_id'))}",
        f"IP Address: {_clean_text(data.get('ip_address'))}",
        f"URL: {_clean_text(data.get('scheme') or 'http')}://{_clean_text(data.get('ip_address'))}:{_to_int(data.get('port'), 80)}",
        f"Status: {_clean_text(data.get('status')) or 'unknown'}",
        f"First Seen: {_clean_text(data.get('first_seen')) or 'n/a'}",
        f"Last Seen: {_clean_text(data.get('last_seen')) or 'n/a'}",
        f"Country: {_country_display(data.get('country'), data.get('country_code'))}",
        f"Scan Count: {_to_int(data.get('scan_count'))}",
        f"Title: {_clean_text(data.get('title')) or '(none)'}",
        f"Banner: {_clean_text(data.get('banner')) or '(none)'}",
        f"Favorite: {'yes' if _to_int(data.get('favorite')) else 'no'}",
        f"Avoid: {'yes' if _to_int(data.get('avoid')) else 'no'}",
        "",
        *tree_lines,
        "",
        "Probe Summary:",
        f"Probe Status: {_clean_text(data.get('probe_status')) or 'unprobed'}",
        f"Extracted: {'yes' if _to_int(data.get('extracted')) else 'no'}",
        f"Accessible Dirs ({dirs_count}): {_clean_text(data.get('accessible_dirs_list')) or '(none)'}",
        f"Accessible Files: {files_count}",
        f"Indicator Matches: {_to_int(data.get('indicator_matches'))}",
        f"Indicator Samples: {_clean_text(data.get('indicator_samples')) or '(none)'}",
        f"Snapshot Path: {_clean_text(data.get('snapshot_path')) or '(none)'}",
        "",
        f"Latest HTTP Access: {'found' if access else 'none'}",
        f"  Accessible: {'yes' if _to_int(access.get('accessible')) else 'no'}",
        f"  Status Code: {_to_int(access.get('status_code')) or '(none)'}",
        f"  Index Page: {'yes' if _to_int(access.get('is_index_page')) else 'no'}",
        f"  Dir Count: {_to_int(access.get('dir_count'))}",
        f"  File Count: {_to_int(access.get('file_count'))}",
        f"  TLS Verified: {'yes' if _to_int(access.get('tls_verified')) else 'no'}",
        f"  Test Timestamp: {_clean_text(access.get('test_timestamp')) or '(none)'}",
        f"  Error Message: {_clean_text(access.get('error_message')) or '(none)'}",
        f"Notes: {notes or '(none)'}",
    ]

    return {
        "row_key": f"H:{_to_int(data.get('protocol_server_id'))}",
        "host_type": "H",
        "protocol": "HTTP",
        "protocol_server_id": _to_int(data.get("protocol_server_id")),
        "ip_address": _clean_text(data.get("ip_address")),
        "open_with_url": open_with_url,
        "overview": {
            "protocol": "HTTP",
            "status": _clean_text(data.get("status")) or "unknown",
            "last_seen": _format_last_seen(data.get("last_seen")),
            "country": _clean_text(data.get("country")) or "Unknown",
            "country_code": _clean_text(data.get("country_code")),
            "scan_count": _to_int(data.get("scan_count")),
            "auth_method": _clean_text(data.get("scheme")) or "http",
            "access_summary": access_summary,
            "probe_status": _clean_text(data.get("probe_status")) or "unprobed",
            "extracted": bool(_to_int(data.get("extracted"))),
        },
        "notes": notes,
        "full_details_text": "\n".join(full_lines),
    }


def get_result_details(
    db_path: Path,
    host_type: str,
    protocol_server_id: int,
) -> Optional[dict]:
    """Return row details for inline /results expansion, or None when not found."""
    if not Path(db_path).exists():
        return None
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
            "probe_snapshots",
            "ftp_access",
            "http_access",
        ):
            if table in tables:
                table_columns[table] = _inspect_columns(conn, table)

        if host_type == "S":
            return _get_smb_detail(conn, table_columns, protocol_server_id)
        if host_type == "F":
            return _get_ftp_detail(conn, table_columns, protocol_server_id)
        if host_type == "H":
            return _get_http_detail(conn, table_columns, protocol_server_id)
        return None
    finally:
        conn.close()
