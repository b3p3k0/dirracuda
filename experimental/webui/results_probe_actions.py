"""Async row-level probe actions for Web UI results table."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from gui.utils import probe_patterns
from gui.utils.database_access import DatabaseReader
from gui.utils.probe_cache_dispatch import dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot

_PROBE_LIMITS = {
    "max_directories": 3,
    "max_files": 5,
    "timeout_seconds": 10,
    "max_depth": 1,
}

_HOST_TABLES = {
    "S": {
        "server": "smb_servers",
        "probe": "host_probe_cache",
        "default_port": None,
    },
    "F": {
        "server": "ftp_servers",
        "probe": "ftp_probe_cache",
        "default_port": 21,
    },
    "H": {
        "server": "http_servers",
        "probe": "http_probe_cache",
        "default_port": 80,
    },
}


class ProbeJobConflictError(RuntimeError):
    """Raised when a new probe job is requested while one is already active."""

    def __init__(self, job_id: str) -> None:
        super().__init__("probe job already running")
        self.job_id = job_id


@dataclass
class _ProbeTarget:
    host_type: str
    protocol_server_id: int
    row_key: str


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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


def _table_has_columns(table_columns: dict[str, set[str]], table: str, *columns: str) -> bool:
    cols = table_columns.get(table)
    if not cols:
        return False
    return all(column in cols for column in columns)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_http_path(value: Any) -> str:
    path = str(value or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not path.startswith("/"):
        return "/" + path.lstrip("/")
    return path


def _fallback_upsert_probe_cache(
    db_path: Path,
    *,
    host_type: str,
    protocol_server_id: int,
    status: str,
    indicator_matches: int,
    latest_snapshot_id: Optional[int] = None,
    accessible_dirs_count: Optional[int] = None,
    accessible_dirs_list: Optional[str] = None,
    accessible_files_count: Optional[int] = None,
) -> None:
    probe_table = _HOST_TABLES[host_type]["probe"]
    conn = _connect_rw(db_path)
    try:
        cols = _inspect_columns(conn, probe_table)
        if "server_id" not in cols or "status" not in cols or "indicator_matches" not in cols:
            raise ValueError(f"missing required table/column: {probe_table}.status")

        set_parts = ["status = ?", "indicator_matches = ?"]
        update_vals: list[Any] = [status, indicator_matches]
        insert_cols = ["server_id", "status", "indicator_matches"]
        insert_sql_values = ["?", "?", "?"]
        insert_params: list[Any] = [protocol_server_id, status, indicator_matches]

        if "latest_snapshot_id" in cols:
            set_parts.append("latest_snapshot_id = ?")
            insert_cols.append("latest_snapshot_id")
            insert_sql_values.append("?")
            insert_params.append(latest_snapshot_id)
            update_vals.append(latest_snapshot_id)
        if "accessible_dirs_count" in cols:
            set_parts.append("accessible_dirs_count = ?")
            insert_cols.append("accessible_dirs_count")
            insert_sql_values.append("?")
            insert_params.append(accessible_dirs_count)
            update_vals.append(accessible_dirs_count)
        if "accessible_dirs_list" in cols:
            set_parts.append("accessible_dirs_list = ?")
            insert_cols.append("accessible_dirs_list")
            insert_sql_values.append("?")
            insert_params.append(accessible_dirs_list)
            update_vals.append(accessible_dirs_list)
        if "accessible_files_count" in cols:
            set_parts.append("accessible_files_count = ?")
            insert_cols.append("accessible_files_count")
            insert_sql_values.append("?")
            insert_params.append(accessible_files_count)
            update_vals.append(accessible_files_count)
        if "last_probe_at" in cols:
            set_parts.append("last_probe_at = CURRENT_TIMESTAMP")
            insert_cols.append("last_probe_at")
            insert_sql_values.append("CURRENT_TIMESTAMP")
        if "updated_at" in cols:
            set_parts.append("updated_at = CURRENT_TIMESTAMP")
            insert_cols.append("updated_at")
            insert_sql_values.append("CURRENT_TIMESTAMP")

        existing = conn.execute(
            f"SELECT 1 FROM {probe_table} WHERE server_id = ? LIMIT 1",
            (protocol_server_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                f"INSERT INTO {probe_table} ({', '.join(insert_cols)}) "
                f"VALUES ({', '.join(insert_sql_values)})",
                insert_params,
            )
        else:
            conn.execute(
                f"UPDATE {probe_table} SET {', '.join(set_parts)} WHERE server_id = ?",
                update_vals + [protocol_server_id],
            )
        conn.commit()
    finally:
        conn.close()


def _safe_upsert_probe_cache_for_host(
    db_path: Path,
    db_reader: DatabaseReader,
    *,
    ip_address: str,
    host_type: str,
    protocol_server_id: int,
    status: str,
    indicator_matches: int,
    latest_snapshot_id: Optional[int] = None,
    accessible_dirs_count: Optional[int] = None,
    accessible_dirs_list: Optional[str] = None,
    accessible_files_count: Optional[int] = None,
    port: Optional[int] = None,
) -> None:
    try:
        db_reader.upsert_probe_cache_for_host(
            ip_address,
            host_type,
            status=status,
            indicator_matches=indicator_matches,
            snapshot_path=None,
            latest_snapshot_id=latest_snapshot_id,
            accessible_dirs_count=accessible_dirs_count,
            accessible_dirs_list=accessible_dirs_list,
            accessible_files_count=accessible_files_count,
            protocol_server_id=protocol_server_id,
            port=port,
        )
    except Exception:
        _fallback_upsert_probe_cache(
            db_path,
            host_type=host_type,
            protocol_server_id=protocol_server_id,
            status=status,
            indicator_matches=indicator_matches,
            latest_snapshot_id=latest_snapshot_id,
            accessible_dirs_count=accessible_dirs_count,
            accessible_dirs_list=accessible_dirs_list,
            accessible_files_count=accessible_files_count,
        )


def _resolve_target_row(db_path: Path, target: _ProbeTarget) -> dict[str, Any]:
    host_tables = _HOST_TABLES.get(target.host_type)
    if host_tables is None:
        raise ValueError("invalid host_type")

    conn = _connect_ro(db_path)
    try:
        tables = _inspect_tables(conn)
        if host_tables["server"] not in tables:
            raise ValueError(f"missing required table: {host_tables['server']}")
        if host_tables["probe"] not in tables:
            raise ValueError(f"missing required table: {host_tables['probe']}")

        table_columns = {name: _inspect_columns(conn, name) for name in tables}

        if not _table_has_columns(table_columns, host_tables["server"], "id", "ip_address"):
            raise ValueError(f"missing required table/column: {host_tables['server']}.ip_address")
        if not _table_has_columns(
            table_columns,
            host_tables["probe"],
            "server_id",
            "status",
            "indicator_matches",
        ):
            raise ValueError(f"missing required table/column: {host_tables['probe']}.status")

        if target.host_type == "S":
            smb_cols = table_columns.get("smb_servers", set())
            auth_expr = "COALESCE(auth_method, '')" if "auth_method" in smb_cols else "''"
            row = conn.execute(
                f"SELECT id, ip_address, {auth_expr} AS auth_method "
                "FROM smb_servers WHERE id = ? LIMIT 1",
                (target.protocol_server_id,),
            ).fetchone()
            if row is None:
                raise ValueError("target not found")

            shares: list[str] = []
            if _table_has_columns(table_columns, "share_access", "server_id", "share_name", "accessible"):
                cur = conn.execute(
                    "SELECT share_name FROM share_access "
                    "WHERE server_id = ? AND COALESCE(accessible, 0) = 1",
                    (target.protocol_server_id,),
                )
                shares = [
                    str(s[0]).strip()
                    for s in cur.fetchall()
                    if str(s[0] or "").strip()
                ]

            return {
                "host_type": "S",
                "protocol_server_id": int(row["id"]),
                "row_key": target.row_key,
                "ip_address": str(row["ip_address"]),
                "auth_method": str(row["auth_method"] or ""),
                "shares": shares,
            }

        if target.host_type == "F":
            ftp_cols = table_columns.get("ftp_servers", set())
            port_expr = "port" if "port" in ftp_cols else "NULL"
            row = conn.execute(
                f"SELECT id, ip_address, {port_expr} AS port "
                "FROM ftp_servers WHERE id = ? LIMIT 1",
                (target.protocol_server_id,),
            ).fetchone()
            if row is None:
                raise ValueError("target not found")

            return {
                "host_type": "F",
                "protocol_server_id": int(row["id"]),
                "row_key": target.row_key,
                "ip_address": str(row["ip_address"]),
                "port": _as_int(row["port"], 21),
            }

        http_cols = table_columns.get("http_servers", set())
        port_expr = "port" if "port" in http_cols else "NULL"
        scheme_expr = "scheme" if "scheme" in http_cols else "NULL"
        probe_host_expr = "probe_host" if "probe_host" in http_cols else "NULL"
        probe_path_expr = "probe_path" if "probe_path" in http_cols else "NULL"
        row = conn.execute(
            "SELECT "
            "id, ip_address, "
            f"{port_expr} AS port, "
            f"{scheme_expr} AS scheme, "
            f"{probe_host_expr} AS probe_host, "
            f"{probe_path_expr} AS probe_path "
            "FROM http_servers WHERE id = ? LIMIT 1",
            (target.protocol_server_id,),
        ).fetchone()
        if row is None:
            raise ValueError("target not found")

        port = _as_int(row["port"], 80)
        scheme = str(row["scheme"] or ("https" if port == 443 else "http")).strip().lower()
        if scheme not in {"http", "https"}:
            scheme = "https" if port == 443 else "http"
        probe_host = str(row["probe_host"] or "").strip() or None
        probe_path = _normalize_http_path(row["probe_path"])

        return {
            "host_type": "H",
            "protocol_server_id": int(row["id"]),
            "row_key": target.row_key,
            "ip_address": str(row["ip_address"]),
            "port": port,
            "scheme": scheme,
            "probe_host": probe_host,
            "probe_path": probe_path,
        }
    finally:
        conn.close()


def _probe_one_target(
    db_path: Path,
    config_path: Optional[Path],
    indicator_patterns: list[tuple[str, object]],
    target_data: dict[str, Any],
) -> dict[str, Any]:
    host_type = str(target_data.get("host_type") or "").strip().upper()
    protocol_server_id = _as_int(target_data.get("protocol_server_id"), 0)
    row_key = str(target_data.get("row_key") or f"{host_type}:{protocol_server_id}")

    target = _ProbeTarget(
        host_type=host_type,
        protocol_server_id=protocol_server_id,
        row_key=row_key,
    )

    resolved = _resolve_target_row(db_path, target)

    db_reader = DatabaseReader(str(db_path))
    cancel_event = threading.Event()
    kwargs: dict[str, Any] = {
        "max_directories": _PROBE_LIMITS["max_directories"],
        "max_files": _PROBE_LIMITS["max_files"],
        "timeout_seconds": _PROBE_LIMITS["timeout_seconds"],
        "cancel_event": cancel_event,
        "max_depth": _PROBE_LIMITS["max_depth"],
        "db_reader": db_reader,
    }

    ip_address = str(resolved["ip_address"])

    if host_type == "S":
        auth_method = str(resolved.get("auth_method") or "")
        kwargs.update(
            {
                "shares": list(resolved.get("shares") or []),
                "username": "" if "anonymous" in auth_method.lower() else "guest",
                "password": "",
                "allow_empty": True,
            }
        )
    elif host_type == "F":
        kwargs["port"] = _as_int(resolved.get("port"), 21)
    elif host_type == "H":
        kwargs["port"] = _as_int(resolved.get("port"), 80)
        kwargs["scheme"] = str(resolved.get("scheme") or "http")
        kwargs["request_host"] = resolved.get("probe_host")
        kwargs["start_path"] = _normalize_http_path(resolved.get("probe_path"))
        kwargs["protocol_server_id"] = _as_int(resolved.get("protocol_server_id"), 0)
    else:
        raise ValueError("invalid host_type")

    snapshot = dispatch_probe_run(ip_address, host_type, **kwargs)
    analysis = probe_patterns.attach_indicator_analysis(snapshot, indicator_patterns)
    probe_status = "issue" if bool(analysis.get("is_suspicious")) else "clean"
    indicator_matches = len(analysis.get("matches", []))

    if host_type == "S":
        try:
            snapshot_id = db_reader.upsert_probe_snapshot_for_host(ip_address, "S", snapshot)
        except Exception:
            snapshot_id = None
        _safe_upsert_probe_cache_for_host(
            db_path,
            db_reader,
            ip_address=ip_address,
            host_type="S",
            protocol_server_id=resolved["protocol_server_id"],
            status=probe_status,
            indicator_matches=indicator_matches,
            latest_snapshot_id=snapshot_id,
        )
    elif host_type == "F":
        port = _as_int(resolved.get("port"), 21)
        try:
            snapshot_id = db_reader.upsert_probe_snapshot_for_host(
                ip_address,
                "F",
                snapshot,
                protocol_server_id=resolved["protocol_server_id"],
                port=port,
            )
        except Exception:
            snapshot_id = None
        summary = summarize_probe_snapshot(snapshot)
        display_entries = summary["display_entries"]
        _safe_upsert_probe_cache_for_host(
            db_path,
            db_reader,
            ip_address=ip_address,
            host_type="F",
            protocol_server_id=resolved["protocol_server_id"],
            status=probe_status,
            indicator_matches=indicator_matches,
            latest_snapshot_id=snapshot_id,
            accessible_dirs_count=len(display_entries),
            accessible_dirs_list=",".join(display_entries),
            port=port,
        )
    else:
        port = _as_int(resolved.get("port"), 80)
        try:
            snapshot_id = db_reader.upsert_probe_snapshot_for_host(
                ip_address,
                "H",
                snapshot,
                protocol_server_id=resolved["protocol_server_id"],
                port=port,
            )
        except Exception:
            snapshot_id = None
        summary = summarize_probe_snapshot(snapshot)
        _safe_upsert_probe_cache_for_host(
            db_path,
            db_reader,
            ip_address=ip_address,
            host_type="H",
            protocol_server_id=resolved["protocol_server_id"],
            status=probe_status,
            indicator_matches=indicator_matches,
            latest_snapshot_id=snapshot_id,
            accessible_dirs_count=len(summary["directory_names"]),
            accessible_dirs_list=",".join(summary["display_entries"]),
            accessible_files_count=int(summary["total_file_count"]),
            port=port,
        )

    return {
        "host_type": host_type,
        "protocol_server_id": resolved["protocol_server_id"],
        "row_key": row_key,
        "ok": True,
        "state": {
            "probe_status": probe_status,
            "indicator_matches": indicator_matches,
        },
    }


class ResultsProbeJobManager:
    """Single-active-job in-process manager for results probe actions."""

    def __init__(
        self,
        *,
        db_path: Path,
        main_config_path: Optional[Path],
        keep_completed_seconds: int = 300,
    ) -> None:
        self._db_path = Path(db_path)
        self._main_config_path = Path(main_config_path).expanduser() if main_config_path else None
        self._keep_completed_seconds = max(10, int(keep_completed_seconds))
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: Optional[str] = None

    def start_job(self, targets: list[dict[str, Any]]) -> dict[str, Any]:
        self._prune_locked(time.time())

        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active.get("status") == "running":
                    raise ProbeJobConflictError(self._active_job_id)

            job_id = secrets.token_hex(8)
            now = time.time()
            job = {
                "job_id": job_id,
                "status": "running",
                "summary": {
                    "total": len(targets),
                    "completed": 0,
                    "succeeded": 0,
                    "failed": 0,
                },
                "results": [],
                "started_at": now,
                "finished_at": None,
                "_expires_at": None,
            }
            self._jobs[job_id] = job
            self._active_job_id = job_id

        worker = threading.Thread(
            target=self._run_job,
            args=(job_id, list(targets)),
            daemon=True,
            name=f"webui-results-probe-{job_id}",
        )
        worker.start()

        return {
            "job_id": job_id,
            "status": "running",
            "total_targets": len(targets),
            "poll_url": f"/api/results/actions/probe/{job_id}",
        }

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            job = self._jobs.get(job_id)
            if job is None:
                return None
            summary = dict(job["summary"])
            results = [dict(item) for item in job["results"]]
            return {
                "job_id": job["job_id"],
                "status": job["status"],
                "summary": summary,
                "results": results,
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
            }

    def _run_job(self, job_id: str, targets: list[dict[str, Any]]) -> None:
        if not self._db_path.exists():
            self._finish_job(
                job_id,
                {
                    "host_type": "",
                    "protocol_server_id": 0,
                    "row_key": "",
                    "ok": False,
                    "error": f"database not found: {self._db_path}",
                },
            )
            return

        indicator_patterns: list[tuple[str, object]] = []
        if self._main_config_path is not None:
            try:
                indicators = probe_patterns.load_ransomware_indicators(str(self._main_config_path))
                indicator_patterns = probe_patterns.compile_indicator_patterns(indicators)
            except Exception:
                indicator_patterns = []

        for target in targets:
            try:
                outcome = _probe_one_target(
                    self._db_path,
                    self._main_config_path,
                    indicator_patterns,
                    target,
                )
            except Exception as exc:
                host_type = str(target.get("host_type") or "").upper()
                protocol_server_id = _as_int(target.get("protocol_server_id"), 0)
                row_key = str(target.get("row_key") or f"{host_type}:{protocol_server_id}")
                outcome = {
                    "host_type": host_type,
                    "protocol_server_id": protocol_server_id,
                    "row_key": row_key,
                    "ok": False,
                    "error": str(exc),
                }
            self._append_outcome(job_id, outcome)

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "completed"
            job["finished_at"] = time.time()
            job["_expires_at"] = job["finished_at"] + self._keep_completed_seconds
            if self._active_job_id == job_id:
                self._active_job_id = None

    def _append_outcome(self, job_id: str, outcome: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["results"].append(outcome)
            job["summary"]["completed"] += 1
            if outcome.get("ok"):
                job["summary"]["succeeded"] += 1
            else:
                job["summary"]["failed"] += 1

    def _finish_job(self, job_id: str, outcome: dict[str, Any]) -> None:
        self._append_outcome(job_id, outcome)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "completed"
            job["finished_at"] = time.time()
            job["_expires_at"] = job["finished_at"] + self._keep_completed_seconds
            if self._active_job_id == job_id:
                self._active_job_id = None

    def _prune_locked(self, now_ts: float) -> None:
        stale_ids = []
        for jid, job in self._jobs.items():
            expires_at = job.get("_expires_at")
            if expires_at is not None and float(expires_at) <= now_ts:
                stale_ids.append(jid)
        for jid in stale_ids:
            self._jobs.pop(jid, None)
            if self._active_job_id == jid:
                self._active_job_id = None
