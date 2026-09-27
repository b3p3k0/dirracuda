"""Dorkbook schema validation and transactional v1-to-v2 sidecar upgrade."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time


SCHEMA_VERSION = 2
TABLE = "dorkbook_entries"
LEGACY_COLUMNS = {
    "entry_id", "protocol", "nickname", "query", "query_normalized", "notes",
    "row_kind", "builtin_key", "created_at", "updated_at",
}
COMMON_CHECKS = (
    "CHECK (row_kind IN ('builtin', 'custom'))",
    "CHECK ((row_kind = 'builtin' AND builtin_key IS NOT NULL) OR row_kind = 'custom')",
)
DESTINATION_CHECK = """CHECK (
    (provider = 'shodan' AND protocol IS NOT NULL AND protocol IN ('SMB', 'FTP', 'HTTP'))
    OR (provider = 'self_hosted' AND protocol IS NULL)
)"""
DDL = f"""
CREATE TABLE dorkbook_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'shodan',
    protocol TEXT,
    topic TEXT NOT NULL DEFAULT 'General',
    nickname TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL,
    query_normalized TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    row_kind TEXT NOT NULL DEFAULT 'custom',
    builtin_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    {DESTINATION_CHECK},
    {COMMON_CHECKS[0]},
    {COMMON_CHECKS[1]}
)
"""
INDEXES = (
    """CREATE UNIQUE INDEX ux_dorkbook_shodan_query
       ON dorkbook_entries(provider, protocol, query_normalized)
       WHERE provider = 'shodan'""",
    """CREATE UNIQUE INDEX ux_dorkbook_self_hosted_query
       ON dorkbook_entries(provider, query_normalized)
       WHERE provider = 'self_hosted'""",
)


def _error(detail: str) -> RuntimeError:
    return RuntimeError(
        f"dorkbook sidecar schema: {detail}. Keep this database; "
        "use a compatible Dirracuda version or restore its pre-upgrade backup "
        "with Dirracuda and the Web UI stopped."
    )


def _compact(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def _indexes(conn: sqlite3.Connection) -> list:
    indexes = []
    for row in conn.execute("PRAGMA index_list('dorkbook_entries')"):
        name, unique, partial = row[1], bool(row[2]), bool(row[4])
        quoted = '"' + name.replace('"', '""') + '"'
        keys = [r for r in conn.execute(f"PRAGMA index_xinfo({quoted})") if r[5]]
        columns = tuple(r[2] for r in keys)
        binary = all(r[4] == "BINARY" for r in keys)
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        indexes.append((columns, unique and binary, partial, _compact(sql or "")))
    return indexes


def _check_shape(conn: sqlite3.Connection, *, legacy: bool = False) -> None:
    indexes = _indexes(conn)
    if legacy:
        if not any(cols == ("protocol", "query_normalized") and unique and not partial
                   for cols, unique, partial, _ in indexes):
            raise _error("missing UNIQUE(protocol, query_normalized)")
    else:
        for columns, predicate in (
            (("provider", "protocol", "query_normalized"), "whereprovider='shodan'"),
            (("provider", "query_normalized"), "whereprovider='self_hosted'"),
        ):
            if not any(cols == columns and unique and partial and sql.endswith(predicate)
                       for cols, unique, partial, sql in indexes):
                raise _error("missing provider-scoped UNIQUE query index")
    if not any(cols == ("builtin_key",) and unique and not partial
               for cols, unique, partial, _ in indexes):
        raise _error("missing UNIQUE(builtin_key)")

    info = {row[1]: row for row in conn.execute("PRAGMA table_xinfo(dorkbook_entries)")}
    expected = LEGACY_COLUMNS if legacy else LEGACY_COLUMNS | {"provider", "topic"}
    if set(info) != expected or any(row[6] for row in info.values()):
        raise _error("unsupported columns (missing or extra fields)")
    for name, row in info.items():
        expected_type = "INTEGER" if name == "entry_id" else "TEXT"
        nullable = name in ("entry_id", "builtin_key") or (name == "protocol" and not legacy)
        if row[2].upper() != expected_type or bool(row[3]) == nullable:
            raise _error(f"unsupported column definition for {name}")
    if info["entry_id"][5] != 1 or any(row[5] for name, row in info.items() if name != "entry_id"):
        raise _error("unsupported primary key")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()[0]
    checks = (*COMMON_CHECKS, "CHECK (protocol IN ('SMB', 'FTP', 'HTTP'))") if legacy else (
        *COMMON_CHECKS, DESTINATION_CHECK,
    )
    if "autoincrement" not in _compact(sql) or any(_compact(c) not in _compact(sql) for c in checks):
        raise _error("unsupported destination or row-kind constraints")


def check_schema(conn: sqlite3.Connection) -> None:
    """Validate columns, destination checks and actual unique-index definitions."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise _error(f"newer schema version {version}")
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(dorkbook_entries)")}
    legacy = "provider" not in columns
    _check_shape(conn, legacy=legacy)
    if legacy:
        raise _error("legacy schema; call init_db() to upgrade before opening")
    if version != SCHEMA_VERSION:
        raise _error(f"unsupported schema version {version}")


def _check_legacy_objects(conn: sqlite3.Connection) -> None:
    """Never discard unknown tables, triggers, views or custom indexes in a rebuild."""
    objects = conn.execute(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
    ).fetchall()
    for kind, name, table in objects:
        if kind == "table" and name == TABLE:
            continue
        if kind == "index" and table == TABLE and name == "ux_dorkbook_protocol_query_norm":
            continue
        raise _error(f"unsupported object {name!r}; migration requires review")


def _backup_legacy(path: Path) -> Path:
    """Back up through a separate read connection while the caller holds the write lock."""
    fd, backup = tempfile.mkstemp(prefix=path.name + ".pre-providers-", suffix=".bak", dir=path.parent)
    os.close(fd)
    backup_path = Path(backup)
    deadline = time.monotonic() + 10

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("Dorkbook backup timed out; retry when the sidecar is idle")

    try:
        # The active migration connection must not be the backup source: backing
        # up from a connection in a write transaction can wait indefinitely.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(str(backup_path))) as target:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise _error("backup integrity check failed")
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def _create(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    for index in INDEXES:
        conn.execute(index)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (TABLE,)).fetchone()
    old_count = conn.execute("SELECT COUNT(*) FROM dorkbook_entries").fetchone()[0]
    # Keep the old table's name stable until its replacement is ready, following
    # SQLite's generalized ALTER TABLE procedure.
    conn.execute(DDL.replace("CREATE TABLE dorkbook_entries", "CREATE TABLE dorkbook_entries_v2", 1))
    columns = ", ".join(sorted(LEGACY_COLUMNS))
    conn.execute(
        f"INSERT INTO dorkbook_entries_v2 ({columns}) SELECT {columns} FROM dorkbook_entries"
    )
    new_count = conn.execute("SELECT COUNT(*) FROM dorkbook_entries_v2").fetchone()[0]
    if old_count != new_count or conn.execute(
        f"SELECT {columns} FROM dorkbook_entries EXCEPT SELECT {columns} FROM dorkbook_entries_v2"
    ).fetchone():
        raise _error("migration row preservation failed")
    conn.execute("DROP TABLE dorkbook_entries")
    conn.execute("ALTER TABLE dorkbook_entries_v2 RENAME TO dorkbook_entries")
    if sequence:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name=?", (sequence[0], TABLE))
    for index in INDEXES:
        conn.execute(index)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    check_schema(conn)
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise _error("migration integrity check failed")


def ensure_schema(conn: sqlite3.Connection, path: Path) -> None:
    """Initialize/migrate inside the caller's BEGIN IMMEDIATE transaction."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise _error(f"newer schema version {version}")
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(dorkbook_entries)")}
    if not columns:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchone() or version:
            raise _error("unrecognized database; refusing to initialize it")
        _create(conn)
        check_schema(conn)
    elif "provider" in columns:
        check_schema(conn)
    else:
        if version not in (0, 1):
            raise _error(f"unsupported legacy schema version {version}")
        _check_shape(conn, legacy=True)
        _check_legacy_objects(conn)
        backup = _backup_legacy(path)
        try:
            _migrate_legacy(conn)
        except Exception as exc:
            raise _error(f"upgrade failed; original schema retained on rollback; backup at {backup}") from exc
