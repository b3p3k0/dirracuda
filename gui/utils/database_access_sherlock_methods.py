"""Sherlock persistence helpers bound onto DatabaseReader (C2).

Stores the latest Sherlock exposure-triage summary plus capped hit details per
host/protocol, and reads them back with a staleness flag. Display-only: this
module never downloads files, reads content, authenticates, or probes — it
persists a precomputed `MatchResult` and reads it back.

Guards check real runtime table/column/index presence before every statement,
because `DatabaseReader.__init__` swallows migration failures, so the Sherlock
tables may be absent or partial even when migrations nominally ran.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from shared.sherlock import MatchResult, Severity

# Detail-row cap per host. total_hit_count is always the full count; this only
# bounds the rows persisted into sherlock_hits.
SHERLOCK_HIT_DETAIL_CAP = 50

_SEVERITY_TO_TOKEN = {
    Severity.HIGH: "high",
    Severity.MED: "med",
    Severity.LOW: "low",
}

# Required columns per table, split by operation. Each set lists every column the
# corresponding statement actually touches, so a partial table is rejected by the
# explicit guard (deterministic no-op) rather than failing into the broad
# OperationalError fallback.
_RESULT_WRITE_COLS = {
    "id",
    "host_type",
    "protocol_server_id",
    "ip_address",
    "port",
    "snapshot_id",
    "highest_severity",
    "total_hit_count",
    "detail_count",
    "truncated",
    "scanned_at",
    "updated_at",
}
_HITS_WRITE_COLS = {
    "result_id",
    "severity",
    "category",
    "label",
    "pattern",
    "display_path",
    "created_at",
}
_RESULT_READ_COLS = {
    "id",
    "host_type",
    "protocol_server_id",
    "ip_address",
    "port",
    "snapshot_id",
    "highest_severity",
    "total_hit_count",
    "detail_count",
    "truncated",
    "scanned_at",
}
_HITS_READ_COLS = {
    "id",
    "result_id",
    "severity",
    "category",
    "label",
    "pattern",
    "display_path",
}

_SERVER_TABLES = {"S": "smb_servers", "F": "ftp_servers", "H": "http_servers"}
_CACHE_TABLES = {"S": "host_probe_cache", "F": "ftp_probe_cache", "H": "http_probe_cache"}


def _severity_token(severity: Optional[Severity]) -> Optional[str]:
    return _SEVERITY_TO_TOKEN.get(severity)


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(cur: sqlite3.Cursor, table: str) -> set:
    return {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def _unique_index_covers(cur: sqlite3.Cursor, table: str, cols) -> bool:
    """True iff some UNIQUE index on `table` covers exactly the given columns.

    Shape-verified (not name-based): a drifted non-unique or wrong-column index
    of the expected name cannot pass.
    """
    cols = tuple(cols)
    for idx in cur.execute(f"PRAGMA index_list({table})").fetchall():
        # PRAGMA index_list columns: seq, name, unique, origin, partial
        if int(idx[2]) != 1:
            continue
        safe_name = str(idx[1]).replace('"', '""')
        idx_cols = tuple(row[2] for row in cur.execute(f'PRAGMA index_info("{safe_name}")').fetchall())
        if idx_cols == cols:
            return True
    return False


def store_sherlock_result(
    self,
    ip_address: str,
    host_type: str,
    snapshot_id: Optional[int],
    match_result: MatchResult,
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> Optional[int]:
    """Persist the latest Sherlock summary + capped hits for one host/protocol.

    Returns sherlock_results.id, or None when the host/server cannot be resolved,
    the snapshot is missing (snapshot_id is None), the input is not a MatchResult,
    or the Sherlock tables/columns are absent. Replaces any prior result for the
    same (host_type, protocol_server_id).
    """
    host_type = (host_type or "S").upper()
    if not ip_address or host_type not in ("S", "F", "H"):
        return None
    if snapshot_id is None:
        return None
    if not isinstance(match_result, MatchResult):
        return None

    server_table = _SERVER_TABLES[host_type]
    severity_token = _severity_token(match_result.highest_severity)
    total = int(match_result.hit_count)
    hits = list(match_result.hits)[:SHERLOCK_HIT_DETAIL_CAP]
    detail_count = len(hits)
    truncated = 1 if total > detail_count else 0

    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if not (_table_exists(cur, "sherlock_results") and _table_exists(cur, "sherlock_hits")):
                return None
            if not _RESULT_WRITE_COLS.issubset(_table_columns(cur, "sherlock_results")):
                return None
            if not _HITS_WRITE_COLS.issubset(_table_columns(cur, "sherlock_hits")):
                return None

            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return None

            insert_values = (
                host_type,
                server_id,
                ip_address,
                port,
                int(snapshot_id),
                severity_token,
                total,
                detail_count,
                truncated,
            )
            update_values = (
                ip_address,
                port,
                int(snapshot_id),
                severity_token,
                total,
                detail_count,
                truncated,
                host_type,
                server_id,
            )
            insert_sql = """
                INSERT INTO sherlock_results
                    (host_type, protocol_server_id, ip_address, port, snapshot_id,
                     highest_severity, total_hit_count, detail_count, truncated,
                     scanned_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
            update_sql = """
                UPDATE sherlock_results SET
                    ip_address=?, port=?, snapshot_id=?, highest_severity=?,
                    total_hit_count=?, detail_count=?, truncated=?,
                    scanned_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE host_type=? AND protocol_server_id=?
            """

            if _unique_index_covers(cur, "sherlock_results", ("host_type", "protocol_server_id")):
                # Atomic, race-safe upsert on the verified unique index.
                cur.execute(
                    insert_sql.rstrip()
                    + """
                    ON CONFLICT(host_type, protocol_server_id) DO UPDATE SET
                        ip_address=excluded.ip_address,
                        port=excluded.port,
                        snapshot_id=excluded.snapshot_id,
                        highest_severity=excluded.highest_severity,
                        total_hit_count=excluded.total_hit_count,
                        detail_count=excluded.detail_count,
                        truncated=excluded.truncated,
                        scanned_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    insert_values,
                )
            else:
                # Defensive fallback when no correctly-shaped unique index exists.
                cur.execute(update_sql, update_values)
                if cur.rowcount == 0:
                    try:
                        cur.execute(insert_sql, insert_values)
                    except sqlite3.IntegrityError:
                        cur.execute(update_sql, update_values)

            row = cur.execute(
                "SELECT id FROM sherlock_results WHERE host_type=? AND protocol_server_id=?",
                (host_type, server_id),
            ).fetchone()
            if not row:
                return None
            result_id = int(row["id"])

            # Explicit child cleanup (FK cascade is off on these connections).
            cur.execute("DELETE FROM sherlock_hits WHERE result_id = ?", (result_id,))
            if hits:
                cur.executemany(
                    """
                    INSERT INTO sherlock_hits
                        (result_id, severity, category, label, pattern, display_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [
                        (
                            result_id,
                            _severity_token(hit.severity),
                            hit.category,
                            hit.label,
                            hit.pattern,
                            hit.display_path,
                        )
                        for hit in hits
                    ],
                )
            conn.commit()
    except sqlite3.OperationalError:
        return None

    self.clear_cache()
    return result_id


def get_sherlock_result_for_host(
    self,
    ip_address: str,
    host_type: str,
    *,
    protocol_server_id: Optional[int] = None,
    port: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return the latest Sherlock summary + hits for a host, with a `stale` flag.

    None when the host is unscanned or the Sherlock tables are absent. `stale` is
    True when the persisted snapshot_id no longer matches the host's current
    latest_snapshot_id (or that anchor is unavailable); callers render a blank
    Risk cell for stale / None.
    """
    host_type = (host_type or "S").upper()
    if not ip_address or host_type not in ("S", "F", "H"):
        return None

    server_table = _SERVER_TABLES[host_type]
    cache_table = _CACHE_TABLES[host_type]

    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if not (_table_exists(cur, "sherlock_results") and _table_exists(cur, "sherlock_hits")):
                return None
            if not _RESULT_READ_COLS.issubset(_table_columns(cur, "sherlock_results")):
                return None
            if not _HITS_READ_COLS.issubset(_table_columns(cur, "sherlock_hits")):
                return None

            server_id = self._resolve_protocol_server_id(
                cur,
                ip_address=ip_address,
                host_type=host_type,
                server_table=server_table,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if server_id is None:
                return None

            row = cur.execute(
                """
                SELECT id, host_type, protocol_server_id, ip_address, port, snapshot_id,
                       highest_severity, total_hit_count, detail_count, truncated, scanned_at
                FROM sherlock_results
                WHERE host_type=? AND protocol_server_id=?
                """,
                (host_type, server_id),
            ).fetchone()
            if row is None:
                return None

            result_id = int(row["id"])
            stored_snapshot_id = row["snapshot_id"]

            current_latest: Optional[int] = None
            if _table_exists(cur, cache_table) and "latest_snapshot_id" in _table_columns(cur, cache_table):
                cache_row = cur.execute(
                    f"SELECT latest_snapshot_id FROM {cache_table} WHERE server_id = ?",
                    (server_id,),
                ).fetchone()
                if cache_row is not None and cache_row["latest_snapshot_id"] is not None:
                    current_latest = int(cache_row["latest_snapshot_id"])

            stale = (
                stored_snapshot_id is None
                or current_latest is None
                or int(stored_snapshot_id) != current_latest
            )

            hit_rows = cur.execute(
                """
                SELECT severity, category, label, pattern, display_path
                FROM sherlock_hits WHERE result_id = ? ORDER BY id
                """,
                (result_id,),
            ).fetchall()
            hits = [
                {
                    "severity": hr["severity"],
                    "category": hr["category"],
                    "label": hr["label"],
                    "pattern": hr["pattern"],
                    "display_path": hr["display_path"],
                }
                for hr in hit_rows
            ]

            return {
                "host_type": row["host_type"],
                "protocol_server_id": row["protocol_server_id"],
                "ip_address": row["ip_address"],
                "port": row["port"],
                "snapshot_id": stored_snapshot_id,
                "highest_severity": row["highest_severity"],
                "total_hit_count": row["total_hit_count"],
                "detail_count": row["detail_count"],
                "truncated": bool(row["truncated"]),
                "scanned_at": row["scanned_at"],
                "stale": stale,
                "hits": hits,
            }
    except sqlite3.OperationalError:
        return None


def get_sherlock_risk_summary_map(self) -> Dict[str, Dict[str, Any]]:
    """Return a row_key -> {severity, count, stale} map for every stored result.

    row_key is "<host_type>:<protocol_server_id>" to match the Server List. Each
    protocol is processed independently so a missing/partial cache table (legacy
    DBs may lack ftp_probe_cache/http_probe_cache or latest_snapshot_id) degrades
    only that protocol's staleness — those rows fall back to stale=True (blank) —
    and never blanks the others. The renderer owns the blank decision; this reader
    just reports raw severity/count/stale. Returns {} when sherlock_results is
    absent/partial. Display-only: no network, no content reads.
    """
    summary: Dict[str, Dict[str, Any]] = {}
    try:
        with self._get_connection() as conn:
            cur = conn.cursor()
            if not _table_exists(cur, "sherlock_results"):
                return {}
            if not _RESULT_READ_COLS.issubset(_table_columns(cur, "sherlock_results")):
                return {}

            for host_type, cache_table in _CACHE_TABLES.items():
                try:
                    cache_cols = _table_columns(cur, cache_table) if _table_exists(cur, cache_table) else set()
                    cache_ok = (
                        "server_id" in cache_cols
                        and "latest_snapshot_id" in cache_cols
                    )
                    if cache_ok:
                        rows = cur.execute(
                            f"""
                            SELECT r.protocol_server_id AS protocol_server_id,
                                   r.highest_severity AS highest_severity,
                                   r.total_hit_count AS total_hit_count,
                                   r.snapshot_id AS snapshot_id,
                                   c.latest_snapshot_id AS latest_snapshot_id
                            FROM sherlock_results r
                            LEFT JOIN {cache_table} c
                                ON c.server_id = r.protocol_server_id
                            WHERE r.host_type = ?
                            """,
                            (host_type,),
                        ).fetchall()
                    else:
                        rows = cur.execute(
                            """
                            SELECT protocol_server_id AS protocol_server_id,
                                   highest_severity AS highest_severity,
                                   total_hit_count AS total_hit_count,
                                   snapshot_id AS snapshot_id,
                                   NULL AS latest_snapshot_id
                            FROM sherlock_results
                            WHERE host_type = ?
                            """,
                            (host_type,),
                        ).fetchall()
                except sqlite3.OperationalError:
                    continue

                for row in rows:
                    try:
                        protocol_server_id = int(row["protocol_server_id"])
                    except (TypeError, ValueError):
                        continue

                    stored_snapshot_id = row["snapshot_id"]
                    latest = row["latest_snapshot_id"]
                    try:
                        stored_id = int(stored_snapshot_id) if stored_snapshot_id is not None else None
                    except (TypeError, ValueError):
                        stored_id = None
                    try:
                        latest_id = int(latest) if latest is not None else None
                    except (TypeError, ValueError):
                        latest_id = None
                    try:
                        count = int(row["total_hit_count"] or 0)
                    except (TypeError, ValueError):
                        count = 0

                    stale = stored_id is None or latest_id is None or stored_id != latest_id
                    row_key = f"{host_type}:{protocol_server_id}"
                    summary[row_key] = {
                        "severity": row["highest_severity"],
                        "count": count,
                        "stale": stale,
                    }
    except sqlite3.OperationalError:
        return {}
    return summary


def bind_database_access_sherlock_methods(reader_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach Sherlock store/read methods onto DatabaseReader."""
    globals().update(shared_symbols)
    for name in (
        "store_sherlock_result",
        "get_sherlock_result_for_host",
        "get_sherlock_risk_summary_map",
    ):
        setattr(reader_cls, name, globals()[name])
