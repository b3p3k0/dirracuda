"""Read-only Sherlock badge + detail reads for the Web UI (C6).

Self-contained and read-only: opens its own `mode=ro` connection (reusing
`db.py`'s connection/inspection helpers so `db.py` stays at zero net growth) and
returns persisted Sherlock summaries written by C2. It never downloads files,
reads content, authenticates, probes, or mutates config — colors come from the
read-only `sherlock.json` shard via the canonical config-store accessor.

The list badge map honors the alert-only quiet contract (absent / stale / zero /
malformed severity → omitted). The detail reader is explanatory: it returns
stale/zero results with flags so the detail pane can describe them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from experimental.webui.db import (
    _connect,
    _inspect_columns,
    _inspect_tables,
    _table_has_columns,
)
from shared.config_store import get_config_store
from shared.sherlock import Severity, default_settings, settings_from_dict
from shared.sherlock.model import SherlockSettings

_CACHE_TABLES = {
    "S": "host_probe_cache",
    "F": "ftp_probe_cache",
    "H": "http_probe_cache",
}

_SEVERITY_BY_TOKEN = {
    "high": Severity.HIGH,
    "med": Severity.MED,
    "low": Severity.LOW,
}


def _load_sherlock_settings(config_store: Any = None) -> SherlockSettings:
    """Return the SherlockSettings from the sherlock.json shard.

    `config_store` is injectable for tests so they never touch the developer's
    real ~/.dirracuda shard. Any failure / missing shard falls back to the
    validated defaults. Callers use `settings.tint_for(...)` so the user-color
    precedence matches the desktop surfaces.
    """
    try:
        store = config_store if config_store is not None else get_config_store()
        raw = store.load_module_prefs("sherlock")
        return settings_from_dict(raw) if isinstance(raw, dict) else default_settings()
    except Exception:
        return default_settings()


def _to_int(value: object) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def get_sherlock_badge_map(db_path: Path, *, config_store: Any = None) -> Dict[str, Dict[str, str]]:
    """Return row_key -> {text, color} for displayable Sherlock findings.

    row_key is "<host_type>:<protocol_server_id>" to match the results rows.
    Quiet contract: only fresh (snapshot_id == cache latest), positive-count,
    recognized-severity rows are returned. Returns {} when sherlock_results is
    absent/partial or the DB is unreadable.
    """
    if not Path(db_path).exists():
        return {}

    settings = _load_sherlock_settings(config_store)
    out: Dict[str, Dict[str, str]] = {}
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError:
        return {}
    try:
        tables = _inspect_tables(conn)
        if "sherlock_results" not in tables:
            return {}
        result_cols = _inspect_columns(conn, "sherlock_results")
        if not {
            "host_type",
            "protocol_server_id",
            "snapshot_id",
            "highest_severity",
            "total_hit_count",
        }.issubset(result_cols):
            return {}
        has_result_tag = "display_color_tag" in result_cols
        tag_select = "display_color_tag" if has_result_tag else "NULL"
        tag_select_joined = "r.display_color_tag" if has_result_tag else "NULL"

        table_columns: Dict[str, set] = {}
        for cache_table in _CACHE_TABLES.values():
            if cache_table in tables:
                table_columns[cache_table] = _inspect_columns(conn, cache_table)

        for host_type, cache_table in _CACHE_TABLES.items():
            cache_ok = _table_has_columns(
                table_columns, cache_table, "server_id", "latest_snapshot_id"
            )
            try:
                if cache_ok:
                    rows = conn.execute(
                        f"""
                        SELECT r.protocol_server_id AS protocol_server_id,
                               r.highest_severity AS highest_severity,
                               r.total_hit_count AS total_hit_count,
                               r.snapshot_id AS snapshot_id,
                               {tag_select_joined} AS display_color_tag,
                               c.latest_snapshot_id AS latest_snapshot_id
                        FROM sherlock_results r
                        LEFT JOIN {cache_table} c
                            ON c.server_id = r.protocol_server_id
                        WHERE r.host_type = ?
                        """,
                        (host_type,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT protocol_server_id AS protocol_server_id,
                               highest_severity AS highest_severity,
                               total_hit_count AS total_hit_count,
                               snapshot_id AS snapshot_id,
                               {tag_select} AS display_color_tag,
                               NULL AS latest_snapshot_id
                        FROM sherlock_results
                        WHERE host_type = ?
                        """,
                        (host_type,),
                    ).fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                server_id = _to_int(row["protocol_server_id"])
                if server_id is None:
                    continue
                stored_id = _to_int(row["snapshot_id"])
                latest_id = _to_int(row["latest_snapshot_id"])
                if stored_id is None or latest_id is None or stored_id != latest_id:
                    continue  # stale / no anchor -> blank
                count = _to_int(row["total_hit_count"]) or 0
                if count <= 0:
                    continue
                token = (row["highest_severity"] or "").strip().lower() if row["highest_severity"] else ""
                severity = _SEVERITY_BY_TOKEN.get(token)
                if severity is None:
                    continue  # malformed severity -> blank
                out[f"{host_type}:{server_id}"] = {
                    "text": severity.display_text(count),
                    "color": settings.tint_for(severity, row["display_color_tag"]),
                    "display_color_tag": row["display_color_tag"],
                }
    finally:
        conn.close()
    return out


def get_sherlock_detail(
    db_path: Path,
    host_type: str,
    server_id: int,
    *,
    config_store: Any = None,
) -> Optional[Dict[str, Any]]:
    """Return the explanatory Sherlock detail for one host, or None when unscanned.

    Unlike the badge map, stale / zero-hit results are returned (with `stale` /
    counts) so the detail pane can describe them. `hits` are already capped at
    store time (C2).
    """
    host_type = (host_type or "").strip().upper()
    if host_type not in _CACHE_TABLES:
        return None
    sid = _to_int(server_id)
    if sid is None:
        return None
    if not Path(db_path).exists():
        return None

    settings = _load_sherlock_settings(config_store)
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        tables = _inspect_tables(conn)
        if "sherlock_results" not in tables or "sherlock_hits" not in tables:
            return None
        result_cols = _inspect_columns(conn, "sherlock_results")
        if not {
            "id",
            "host_type",
            "protocol_server_id",
            "snapshot_id",
            "highest_severity",
            "total_hit_count",
        }.issubset(result_cols):
            return None
        hit_cols = _inspect_columns(conn, "sherlock_hits")
        if not {"id", "result_id", "severity", "category", "label", "pattern", "display_path"}.issubset(hit_cols):
            return None

        has_scanned = "scanned_at" in result_cols
        has_truncated = "truncated" in result_cols
        has_result_tag = "display_color_tag" in result_cols
        has_hit_tag = "color_tag" in hit_cols
        select_extra = (
            f"{'scanned_at' if has_scanned else 'NULL'} AS scanned_at, "
            f"{'truncated' if has_truncated else '0'} AS truncated, "
            f"{'display_color_tag' if has_result_tag else 'NULL'} AS display_color_tag"
        )
        try:
            row = conn.execute(
                f"""
                SELECT id, snapshot_id, highest_severity, total_hit_count, {select_extra}
                FROM sherlock_results
                WHERE host_type = ? AND protocol_server_id = ?
                """,
                (host_type, sid),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None

        result_id = _to_int(row["id"])
        stored_id = _to_int(row["snapshot_id"])

        latest_id: Optional[int] = None
        cache_table = _CACHE_TABLES[host_type]
        if cache_table in tables:
            cache_cols = _inspect_columns(conn, cache_table)
            if {"server_id", "latest_snapshot_id"}.issubset(cache_cols):
                cache_row = conn.execute(
                    f"SELECT latest_snapshot_id FROM {cache_table} WHERE server_id = ? LIMIT 1",
                    (sid,),
                ).fetchone()
                if cache_row is not None:
                    latest_id = _to_int(cache_row["latest_snapshot_id"])

        stale = stored_id is None or latest_id is None or stored_id != latest_id
        count = _to_int(row["total_hit_count"]) or 0
        token = (row["highest_severity"] or "").strip().lower() if row["highest_severity"] else ""
        severity = _SEVERITY_BY_TOKEN.get(token)

        hits = []
        if result_id is not None:
            hit_tag_select = "color_tag" if has_hit_tag else "NULL"
            hit_rows = conn.execute(
                f"""
                SELECT severity, category, label, pattern, display_path,
                       {hit_tag_select} AS color_tag
                FROM sherlock_hits WHERE result_id = ? ORDER BY id
                """,
                (result_id,),
            ).fetchall()
            for hr in hit_rows:
                hits.append(
                    {
                        "severity": hr["severity"],
                        "category": hr["category"],
                        "label": hr["label"],
                        "pattern": hr["pattern"],
                        "display_path": hr["display_path"],
                        "color_tag": hr["color_tag"],
                    }
                )

        return {
            "severity": token if severity is not None else None,
            "text": severity.display_text(count) if severity is not None else None,
            "count": count,
            "scanned_at": row["scanned_at"],
            "stale": stale,
            "truncated": bool(row["truncated"]),
            "color": settings.tint_for(severity, row["display_color_tag"]) if severity is not None else None,
            "display_color_tag": row["display_color_tag"],
            "hits": hits,
        }
    finally:
        conn.close()
