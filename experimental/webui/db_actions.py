"""Write-side host-state toggles for Web UI results actions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_VALID_ACTIONS = frozenset({"favorite", "avoid", "compromised"})

_HOST_TABLES = {
    "S": {
        "server": "smb_servers",
        "flags": "host_user_flags",
        "probe": "host_probe_cache",
    },
    "F": {
        "server": "ftp_servers",
        "flags": "ftp_user_flags",
        "probe": "ftp_probe_cache",
    },
    "H": {
        "server": "http_servers",
        "flags": "http_user_flags",
        "probe": "http_probe_cache",
    },
}


def _connect_rw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _inspect_tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _inspect_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _table_has_columns(
    table_columns: dict[str, set[str]], table: str, *columns: str
) -> bool:
    cols = table_columns.get(table)
    if not cols:
        return False
    return all(column in cols for column in columns)


def _ensure_server_row(
    conn: sqlite3.Connection,
    server_table: str,
    protocol_server_id: int,
) -> None:
    row = conn.execute(
        f"SELECT id FROM {server_table} WHERE id = ? LIMIT 1",
        (protocol_server_id,),
    ).fetchone()
    if row is None:
        raise ValueError("target not found")


def _toggle_user_flag(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    action: str,
    server_table: str,
    flags_table: str,
    protocol_server_id: int,
) -> dict[str, Any]:
    _ensure_server_row(conn, server_table, protocol_server_id)
    if not _table_has_columns(table_columns, flags_table, "server_id", action):
        raise ValueError(f"missing required table/column: {flags_table}.{action}")

    row = conn.execute(
        f"SELECT COALESCE({action}, 0) AS value FROM {flags_table} "
        "WHERE server_id = ? LIMIT 1",
        (protocol_server_id,),
    ).fetchone()
    before = int(row["value"]) if row is not None else 0
    after = 0 if before else 1

    has_updated_at = "updated_at" in table_columns.get(flags_table, set())
    existing = conn.execute(
        f"SELECT 1 FROM {flags_table} WHERE server_id = ? LIMIT 1",
        (protocol_server_id,),
    ).fetchone()

    if existing is None:
        if has_updated_at:
            conn.execute(
                f"INSERT INTO {flags_table} (server_id, {action}, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (protocol_server_id, after),
            )
        else:
            conn.execute(
                f"INSERT INTO {flags_table} (server_id, {action}) VALUES (?, ?)",
                (protocol_server_id, after),
            )
    else:
        if has_updated_at:
            conn.execute(
                f"UPDATE {flags_table} SET {action} = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE server_id = ?",
                (after, protocol_server_id),
            )
        else:
            conn.execute(
                f"UPDATE {flags_table} SET {action} = ? WHERE server_id = ?",
                (after, protocol_server_id),
            )

    return {action: after}


def _toggle_compromised(
    conn: sqlite3.Connection,
    table_columns: dict[str, set[str]],
    server_table: str,
    probe_table: str,
    protocol_server_id: int,
) -> dict[str, Any]:
    _ensure_server_row(conn, server_table, protocol_server_id)
    if not _table_has_columns(
        table_columns, probe_table, "server_id", "status", "indicator_matches"
    ):
        raise ValueError(f"missing required table/column: {probe_table}.status")

    row = conn.execute(
        f"SELECT COALESCE(status, 'unprobed') AS status, "
        "COALESCE(indicator_matches, 0) AS indicator_matches "
        f"FROM {probe_table} WHERE server_id = ? LIMIT 1",
        (protocol_server_id,),
    ).fetchone()
    before_status = str(row["status"]).lower() if row is not None else "unprobed"
    before_matches = int(row["indicator_matches"]) if row is not None else 0
    is_compromised = before_status == "issue" or before_matches > 0

    if is_compromised:
        after_status = "clean"
        after_matches = 0
    else:
        after_status = "issue"
        after_matches = before_matches if before_matches > 0 else 1

    probe_columns = table_columns.get(probe_table, set())
    has_last_probe_at = "last_probe_at" in probe_columns
    has_updated_at = "updated_at" in probe_columns
    existing = conn.execute(
        f"SELECT 1 FROM {probe_table} WHERE server_id = ? LIMIT 1",
        (protocol_server_id,),
    ).fetchone()

    if existing is None:
        insert_cols = ["server_id", "status", "indicator_matches"]
        insert_vals = ["?", "?", "?"]
        params: list[Any] = [protocol_server_id, after_status, after_matches]
        if has_last_probe_at:
            insert_cols.append("last_probe_at")
            insert_vals.append("CURRENT_TIMESTAMP")
        if has_updated_at:
            insert_cols.append("updated_at")
            insert_vals.append("CURRENT_TIMESTAMP")
        conn.execute(
            f"INSERT INTO {probe_table} ({', '.join(insert_cols)}) "
            f"VALUES ({', '.join(insert_vals)})",
            params,
        )
    else:
        set_parts = ["status = ?", "indicator_matches = ?"]
        params = [after_status, after_matches]
        if has_last_probe_at:
            set_parts.append("last_probe_at = CURRENT_TIMESTAMP")
        if has_updated_at:
            set_parts.append("updated_at = CURRENT_TIMESTAMP")
        params.append(protocol_server_id)
        conn.execute(
            f"UPDATE {probe_table} SET {', '.join(set_parts)} WHERE server_id = ?",
            params,
        )

    return {"probe_status": after_status, "indicator_matches": after_matches}


def toggle_results_actions(
    db_path: Path, action: str, targets: list[dict[str, Any]]
) -> dict[str, Any]:
    """Toggle favorite/avoid/compromised for protocol rows and return per-target outcomes."""
    if action not in _VALID_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    if not Path(db_path).exists():
        raise FileNotFoundError(f"database not found: {db_path}")

    conn = _connect_rw(Path(db_path))
    try:
        tables = _inspect_tables(conn)
        table_columns: dict[str, set[str]] = {}
        for table in tables:
            table_columns[table] = _inspect_columns(conn, table)

        results: list[dict[str, Any]] = []
        updated = 0
        failed = 0

        for target in targets:
            host_type = str(target.get("host_type") or "").upper()
            protocol_server_id = int(target.get("protocol_server_id"))
            row_key = str(target.get("row_key") or "")

            host_tables = _HOST_TABLES.get(host_type)
            if host_tables is None:
                results.append(
                    {
                        "host_type": host_type,
                        "protocol_server_id": protocol_server_id,
                        "row_key": row_key,
                        "ok": False,
                        "error": "invalid host_type",
                    }
                )
                failed += 1
                continue

            conn.execute("SAVEPOINT results_action_target")
            try:
                if action in ("favorite", "avoid"):
                    state = _toggle_user_flag(
                        conn,
                        table_columns,
                        action,
                        host_tables["server"],
                        host_tables["flags"],
                        protocol_server_id,
                    )
                else:
                    state = _toggle_compromised(
                        conn,
                        table_columns,
                        host_tables["server"],
                        host_tables["probe"],
                        protocol_server_id,
                    )
                conn.execute("RELEASE SAVEPOINT results_action_target")
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT results_action_target")
                conn.execute("RELEASE SAVEPOINT results_action_target")
                results.append(
                    {
                        "host_type": host_type,
                        "protocol_server_id": protocol_server_id,
                        "row_key": row_key,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                failed += 1
                continue

            results.append(
                {
                    "host_type": host_type,
                    "protocol_server_id": protocol_server_id,
                    "row_key": row_key,
                    "ok": True,
                    "action": action,
                    "state": state,
                }
            )
            updated += 1

        conn.commit()
        return {
            "action": action,
            "updated": updated,
            "failed": failed,
            "results": results,
        }
    finally:
        conn.close()
