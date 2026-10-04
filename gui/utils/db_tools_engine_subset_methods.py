"""Allowlisted, host-scoped exports into a fresh current-schema database."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Dict, Iterable, Optional

from shared.db_migrations import run_migrations


@dataclass(frozen=True)
class Rule:
    action: str
    selector: str = ""
    reason: str = ""
    host_type: Optional[str] = None
    credentials: bool = False
    null_columns: tuple[str, ...] = ()


def _host_selector(host_type: str, column: str) -> str:
    return f"{column} IN (SELECT id FROM temp.subset_hosts WHERE host_type = '{host_type}')"


_POLYMORPHIC = (
    "(host_type, protocol_server_id) IN (SELECT host_type, id FROM temp.subset_hosts)"
)

# Insertion order is FK-safe. This registry owns selection, redaction and exclusion.
SUBSET_TABLE_REGISTRY: Dict[str, Rule] = {
    "scan_sessions": Rule("INCLUDE", "id IN (SELECT id FROM temp.subset_sessions)"),
    **{
        table: Rule("INCLUDE", _host_selector(kind, "id"), host_type=kind)
        for kind, table in (("S", "smb_servers"), ("F", "ftp_servers"), ("H", "http_servers"))
    },
    "probe_snapshots": Rule("INCLUDE", _POLYMORPHIC),
    **{
        table: Rule("INCLUDE", "snapshot_id IN (SELECT id FROM main.probe_snapshots)")
        for table in ("probe_snapshot_entries", "probe_snapshot_errors", "probe_snapshot_rce")
    },
    **{
        table: Rule(
            "INCLUDE", _host_selector(kind, "server_id"),
            credentials=table == "share_credentials",
            null_columns=("snapshot_path",) if table.endswith("_probe_cache") else (),
        )
        for kind, tables in (
            ("S", ("share_access", "share_credentials", "file_manifests", "vulnerabilities",
                   "host_user_flags", "host_probe_cache")),
            ("F", ("ftp_access", "ftp_user_flags", "ftp_probe_cache")),
            ("H", ("http_access", "http_user_flags", "http_probe_cache")),
        )
        for table in tables
    },
    "sherlock_results": Rule("INCLUDE", _POLYMORPHIC),
    "sherlock_hits": Rule("INCLUDE", "result_id IN (SELECT id FROM main.sherlock_results)"),
    "failure_logs": Rule("EXCLUDE", reason="Scan noise, keyed by IP only"),
    "extract_run_summaries": Rule("EXCLUDE", reason="Local download activity"),
    **{
        table: Rule("EXCLUDE", reason="The target writes its own migration metadata")
        for table in ("app_migration_state", "app_migration_reports")
    },
    **{
        table: Rule("EXCLUDE", reason="Discovery-run provenance")
        for table in ("reddit_posts", "reddit_targets", "reddit_ingest_state",
                      "dork_runs", "dork_results", "censys_runs", "censys_results")
    },
    **{
        table: Rule("EXCLUDE", reason="Legacy import tables, not current protocol host data")
        for table in ("servers", "shares")
    },
    **{
        table: Rule("EXCLUDE", reason="Maintenance backup or intermediate schema rebuild")
        for table in ("share_access_backup", "share_access_cleanup_backup",
                      "share_access_new", "http_servers_new")
    },
}


class _ExportCancelled(Exception):
    pass


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({_quote(table)})")]


def _copy_table(conn: sqlite3.Connection, table: str, rule: Rule, columns: list[str]) -> int:
    projection = ", ".join("NULL" if col in rule.null_columns else _quote(col) for col in columns)
    return conn.execute(
        f"INSERT INTO main.{_quote(table)} ({', '.join(map(_quote, columns))}) "
        f"SELECT {projection} FROM src.{_quote(table)} WHERE {rule.selector}"
    ).rowcount


def export_subset(
    self, output_path: str, row_keys: Iterable[str], *,
    include_credentials: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Copy selected hosts without opening the source writable or exporting local paths.

    ``run_migrations`` alone creates every core table, including the SMB base
    tables. Discovery stores are deliberately not initialized in the output.
    Malformed row keys raise ValueError; operational failures return a result.
    """
    parsed = {}
    for key in row_keys:
        if not isinstance(key, str) or not re.fullmatch(r"[SFH]:[0-9]+", key):
            raise ValueError(f"Malformed row key: {key!r}")
        kind, value = key.split(":")
        server_id = int(value)
        if not 0 < server_id <= 2**63 - 1:
            raise ValueError(f"Malformed row key: {key!r}")
        parsed[key] = (kind, server_id)

    result = dict(success=False, output_path=output_path, size_bytes=0,
                  hosts={"S": 0, "F": 0, "H": 0}, rows={}, missing=[],
                  warnings=[], error=None, cancelled=False)
    conn = None
    partial = None
    owns_partial = False

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise _ExportCancelled("Export cancelled")

    def progress(percent, message):
        check_cancel()
        if progress_callback is not None:
            progress_callback(percent, message)
        check_cancel()

    try:
        source = os.path.realpath(self.current_db_path)
        output = os.path.abspath(output_path)
        partial = output + ".partial"
        protected = {os.path.realpath(str(self.current_db_path) + suffix)
                     for suffix in ("", "-wal", "-shm", "-journal")}
        protected.update(source + suffix for suffix in ("-wal", "-shm", "-journal"))
        if any(os.path.realpath(path) in protected for path in
               (output, partial, partial + "-wal", partial + "-shm", partial + "-journal")):
            raise ValueError("Output must not overwrite the active database or its journal files")
        if not parsed:
            raise ValueError("No hosts selected")
        if not self._check_disk_space(os.path.getsize(source), os.path.dirname(output)):
            raise OSError("Insufficient disk space")
        progress(0, "Preparing subset export")
        # Exclusive creation also refuses stale partials/symlinks without touching them.
        if any(os.path.lexists(partial + suffix) for suffix in ("-wal", "-shm", "-journal")):
            raise FileExistsError("Partial database journal files already exist")
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owns_partial = True
        os.close(fd)
        run_migrations(partial)
        check_cancel()
        conn = sqlite3.connect(partial, uri=True)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("ATTACH DATABASE ? AS src", (Path(source).as_uri() + "?mode=ro",))
        # Hold one source read snapshot across host resolution and all table copies.
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute("SELECT name FROM src.sqlite_master WHERE type='table'")
                  if not row[0].startswith("sqlite_")}
        for table in sorted(tables - SUBSET_TABLE_REGISTRY.keys()):
            result['warnings'].append(f"Unknown source table {table!r} excluded")
        conn.execute("CREATE TEMP TABLE subset_hosts (host_type TEXT, id INTEGER, PRIMARY KEY(host_type, id))")
        conn.executemany("INSERT OR IGNORE INTO temp.subset_hosts VALUES (?, ?)", parsed.values())
        existing = set()
        for table, rule in SUBSET_TABLE_REGISTRY.items():
            if rule.host_type is None:
                continue
            if table in tables:
                existing.update((rule.host_type, row[0]) for row in conn.execute(
                    f"SELECT id FROM src.{_quote(table)} WHERE {rule.selector}"))
        result['missing'] = [key for key, pair in parsed.items() if pair not in existing]
        conn.executemany("DELETE FROM temp.subset_hosts WHERE host_type = ? AND id = ?",
                         set(parsed.values()) - existing)
        if not existing:
            raise ValueError("No selected host exists in the source")

        copy_rules = {
            table: rule for table, rule in SUBSET_TABLE_REGISTRY.items()
            if rule.action == "INCLUDE" and table in tables
            and (include_credentials or not rule.credentials)
        }
        columns = {}
        for table in copy_rules:
            source_columns = set(_columns(conn, "src", table))
            columns[table] = [col for col in _columns(conn, "main", table) if col in source_columns]
        conn.execute("CREATE TEMP TABLE subset_sessions (id INTEGER PRIMARY KEY)")
        for table, rule in copy_rules.items():
            check_cancel()
            if "session_id" in columns[table]:
                conn.execute(
                    "INSERT OR IGNORE INTO temp.subset_sessions "
                    f"SELECT session_id FROM src.{_quote(table)} "
                    f"WHERE ({rule.selector}) AND session_id IS NOT NULL"
                )
        for index, (table, rule) in enumerate(copy_rules.items()):
            progress(5 + int(85 * index / len(copy_rules)), f"Copying {table}")
            result['rows'][table] = _copy_table(conn, table, rule, columns[table])
            if rule.host_type is not None:
                result['hosts'][rule.host_type] = result['rows'][table]
        progress(95, "Validating subset export")
        if conn.execute("PRAGMA main.foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("Subset foreign key check failed")
        conn.commit()
        conn.execute("DETACH DATABASE src")
        if conn.execute("PRAGMA main.journal_mode=DELETE").fetchone()[0] != "delete":
            raise sqlite3.DatabaseError("Could not finalize subset journal mode")
        if conn.execute("PRAGMA main.integrity_check").fetchall() != [("ok",)]:
            raise sqlite3.IntegrityError("Subset integrity check failed")
        conn.close()
        conn = None
        size = os.path.getsize(partial)
        progress(100, "Subset validated; finalizing export")
        os.replace(partial, output)
        owns_partial = False
        result.update(success=True, size_bytes=size)
    except _ExportCancelled as exc:
        result.update(cancelled=True, error=str(exc))
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if conn is not None:
            conn.close()
        if owns_partial:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.unlink(partial + suffix)
                except FileNotFoundError:
                    pass
    return result


def bind_db_tools_engine_subset_methods(engine_cls, shared_symbols: Dict[str, Any]) -> None:
    """Attach the subset export satellite using the engine's binding convention."""
    engine_cls.export_subset = export_subset
