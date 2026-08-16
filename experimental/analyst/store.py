"""Secure SQLite connection and transaction primitives for Analyst state."""

from __future__ import annotations

import os
import hashlib
import json
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, TypeVar
from urllib.parse import quote

from shared.path_service import get_paths

from .db_schema import (
    APPLICATION_ID,
    KNOWN_SCHEMA_VERSIONS,
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    AnalystSchemaError,
    initialize_schema,
    validate_runtime_schema,
    validate_schema,
    validate_v1_migration_candidate,
)
from .inventory import InventoryResult
from .state import RESUMABLE_RUN_STATES, RunState
from .worker_contract import (
    WorkerContractError,
    WorkerRunContext,
    parse_source_identity,
)


BUSY_TIMEOUT_MS = 250
TRANSACTION_ATTEMPTS = 4
TRANSACTION_BACKOFF_SECONDS = (0.025, 0.050, 0.100)

_T = TypeVar("_T")
Transaction = Callable[[sqlite3.Connection], _T]


class AnalystStoreError(RuntimeError):
    """The Analyst sidecar could not be opened or used safely."""


class AnalystStoreBusy(AnalystStoreError):
    """The bounded SQLite writer wait expired."""


class ForkRequired(AnalystStoreError):
    """Immutable run identity drift requires a new run."""


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Immutable run identity and configuration persisted before launch."""

    run_id: str
    mode: str
    source_mode: str
    source_root: str
    output_root: str
    source_identity: Mapping[str, object]
    report_label: str
    model_tag: str
    model_digest: str
    worksheet_version: str
    prompt_sha256: str
    response_schema_sha256: str
    detector_rules_version: str
    detector_rules_sha256: str
    parser_bundle: Mapping[str, object]
    chunk_chars: int
    overlap_chars: int
    num_ctx: int
    num_predict: int
    isolation_mode: str
    reduced_isolation_ack: bool
    host_type: str | None = None
    protocol_server_id: int | None = None
    ip_address: str | None = None
    port: int | None = None
    extract_summary_row_id: int | None = None


def get_db_path(override: Path | None = None) -> Path:
    """Return the canonical sidecar path, with an explicit test override."""
    path = Path(override) if override is not None else get_paths().analyst_db_file
    if not path.is_absolute():
        raise ValueError("Analyst sidecar path must be absolute")
    return path


def initialize_database(path: Path | None = None) -> Path:
    """Create, narrowly migrate, or validate the owner-only Analyst v2 sidecar."""
    resolved = get_db_path(path)
    _ensure_owner_directory(resolved.parent)
    created = _create_database_file(resolved)
    try:
        if not created:
            _recover_hot_journal(resolved)
            _audit_existing_database(resolved)
        conn = _connect(resolved, read_only=False, validate=False)
        try:
            conn.row_factory = None
            initialize_schema(conn)
        finally:
            conn.close()
        _require_owner_file(resolved)
        _fsync_directory(resolved.parent)
        return resolved
    except BaseException:
        # An empty v0 file is an intentional recoverable initialization state.
        if created:
            _require_owner_file(resolved)
        raise


def open_connection(
    path: Path | None = None, *, read_only: bool = False,
) -> sqlite3.Connection:
    """Open one exact v2 connection with the frozen C8 PRAGMA policy."""
    return _connect(get_db_path(path), read_only=read_only, validate=True)


def run_immediate(
    operation: Transaction[_T], *, path: Path | None = None,
) -> _T:
    """Run one short write transaction with bounded whole-transaction retry."""
    last_busy: sqlite3.Error | None = None
    for attempt in range(TRANSACTION_ATTEMPTS):
        conn: sqlite3.Connection | None = None
        try:
            conn = open_connection(path)
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.execute("COMMIT")
            return result
        except sqlite3.Error as exc:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            if not _is_primary_busy(exc):
                raise
            last_busy = exc
        except BaseException:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if conn is not None:
                conn.close()
        if attempt < len(TRANSACTION_BACKOFF_SECONDS):
            time.sleep(TRANSACTION_BACKOFF_SECONDS[attempt])
    raise AnalystStoreBusy("Analyst sidecar remained busy after bounded retry") from last_busy


def create_run(
    spec: RunSpec,
    inventory: InventoryResult,
    *,
    now_utc: str | None = None,
    path: Path | None = None,
) -> None:
    """Persist one ready run and its complete inventory atomically."""
    if not isinstance(spec, RunSpec) or not isinstance(inventory, InventoryResult):
        raise TypeError("create_run requires frozen RunSpec and InventoryResult values")
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")
    source_json, source_sha = _canonical_json_identity(spec.source_identity)
    parser_json, parser_sha = _canonical_json_identity(spec.parser_bundle)

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_runs("
            "run_id,state,created_at_utc,updated_at_utc,mode,source_mode,"
            "source_root,output_root,source_identity_json,source_identity_sha256,"
            "report_label,host_type,protocol_server_id,ip_address,port,"
            "extract_summary_row_id,model_tag,model_digest,worksheet_version,"
            "prompt_sha256,response_schema_sha256,detector_rules_version,"
            "detector_rules_sha256,parser_bundle_json,parser_bundle_sha256,"
            "chunk_chars,overlap_chars,num_ctx,num_predict,isolation_mode,"
            "reduced_isolation_ack) VALUES(?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?,"
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                spec.run_id, timestamp, timestamp, spec.mode, spec.source_mode,
                spec.source_root, spec.output_root, source_json, source_sha,
                spec.report_label, spec.host_type, spec.protocol_server_id,
                spec.ip_address, spec.port, spec.extract_summary_row_id,
                spec.model_tag, spec.model_digest, spec.worksheet_version,
                spec.prompt_sha256, spec.response_schema_sha256,
                spec.detector_rules_version, spec.detector_rules_sha256,
                parser_json, parser_sha, spec.chunk_chars, spec.overlap_chars,
                spec.num_ctx, spec.num_predict, spec.isolation_mode,
                int(spec.reduced_isolation_ack),
            ),
        )
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) VALUES(?,?)",
            (spec.run_id, timestamp),
        )
        conn.executemany(
            "INSERT INTO analyst_files("
            "run_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,inode,"
            "mode,sha256,stage,work_state,updated_at_utc) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'discovered','pending',?)",
            (
                (
                    spec.run_id, ordinal, item.relative_path, item.size,
                    item.mtime_ns, item.ctime_ns, item.device, item.inode,
                    item.mode, item.sha256, timestamp,
                )
                for ordinal, item in enumerate(inventory.files)
            ),
        )
        conn.executemany(
            "INSERT INTO analyst_inventory_exclusions("
            "run_id,ordinal,relative_path,reason) VALUES(?,?,?,?)",
            (
                (spec.run_id, ordinal, item.relative_path, item.reason)
                for ordinal, item in enumerate(inventory.exclusions)
            ),
        )

    run_immediate(operation, path=path)


def list_active_runs(
    *, path: Path | None = None, limit: int = 100,
) -> tuple[sqlite3.Row, ...]:
    """Return bounded, content-free run summaries for either GUI."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("active-run limit must be between 1 and 1000")
    conn = open_connection(path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT run_id,state,revision,created_at_utc,updated_at_utc,mode,"
            "source_mode,report_label,host_type,protocol_server_id,ip_address,port,"
            "cancel_requested_at_utc FROM analyst_runs "
            "WHERE state NOT IN ('complete','abandoned') "
            "ORDER BY updated_at_utc DESC,run_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(rows)
    finally:
        conn.close()


def verify_run_spec(spec: RunSpec, *, path: Path | None = None) -> None:
    """Fail without mutation when any immutable run-wide input has drifted."""
    if not isinstance(spec, RunSpec):
        raise TypeError("verify_run_spec requires a frozen RunSpec")
    source_json, source_sha = _canonical_json_identity(spec.source_identity)
    parser_json, parser_sha = _canonical_json_identity(spec.parser_bundle)
    columns = (
        "mode,source_mode,source_root,output_root,source_identity_json,"
        "source_identity_sha256,report_label,host_type,protocol_server_id,"
        "ip_address,port,extract_summary_row_id,model_tag,model_digest,"
        "worksheet_version,prompt_sha256,response_schema_sha256,"
        "detector_rules_version,detector_rules_sha256,parser_bundle_json,"
        "parser_bundle_sha256,chunk_chars,overlap_chars,num_ctx,num_predict,"
        "isolation_mode,reduced_isolation_ack"
    )
    expected = (
        spec.mode, spec.source_mode, spec.source_root, spec.output_root,
        source_json, source_sha, spec.report_label, spec.host_type,
        spec.protocol_server_id, spec.ip_address, spec.port,
        spec.extract_summary_row_id, spec.model_tag, spec.model_digest,
        spec.worksheet_version, spec.prompt_sha256,
        spec.response_schema_sha256, spec.detector_rules_version,
        spec.detector_rules_sha256, parser_json, parser_sha, spec.chunk_chars,
        spec.overlap_chars, spec.num_ctx, spec.num_predict,
        spec.isolation_mode, int(spec.reduced_isolation_ack),
    )
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            f"SELECT {columns} FROM analyst_runs WHERE run_id=?", (spec.run_id,)
        ).fetchone()
        if row is None:
            raise AnalystStoreError("Analyst run does not exist")
        if tuple(row) != expected:
            raise ForkRequired("Analyst run identity changed; fork a new run")
    finally:
        conn.close()


def load_worker_run(
    run_id: str, *, path: Path | None = None,
) -> WorkerRunContext:
    """Load one exact, runnable C10 context without mutating durable state."""
    _require_text(run_id, "run_id")
    columns = (
        "run_id,state,revision,mode,source_mode,source_root,output_root,"
        "source_identity_json,source_identity_sha256,report_label,host_type,"
        "protocol_server_id,ip_address,port,extract_summary_row_id,model_tag,"
        "model_digest,worksheet_version,prompt_sha256,response_schema_sha256,"
        "detector_rules_version,detector_rules_sha256,parser_bundle_json,"
        "parser_bundle_sha256,chunk_chars,overlap_chars,num_ctx,num_predict,"
        "isolation_mode,reduced_isolation_ack"
    )
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            f"SELECT {columns} FROM analyst_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise AnalystStoreError("Analyst run does not exist")
    finally:
        conn.close()

    try:
        source = _decode_canonical_identity(
            row["source_identity_json"],
            row["source_identity_sha256"],
            "source identity",
        )
        root_identity = parse_source_identity(source)
        _decode_canonical_identity(
            row["parser_bundle_json"],
            row["parser_bundle_sha256"],
            "parser bundle",
        )
        ack = row["reduced_isolation_ack"]
        if type(ack) is not int or ack not in {0, 1}:
            raise WorkerContractError("reduced-isolation acknowledgement is invalid")
        return WorkerRunContext(
            run_id=row["run_id"],
            observed_state=RunState(row["state"]),
            observed_revision=row["revision"],
            mode=row["mode"],
            source_mode=row["source_mode"],
            source_root=row["source_root"],
            output_root=row["output_root"],
            root_identity=root_identity,
            source_identity_sha256=row["source_identity_sha256"],
            report_label=row["report_label"],
            host_type=row["host_type"],
            protocol_server_id=row["protocol_server_id"],
            ip_address=row["ip_address"],
            port=row["port"],
            extract_summary_row_id=row["extract_summary_row_id"],
            model_tag=row["model_tag"],
            model_digest=row["model_digest"],
            worksheet_version=row["worksheet_version"],
            prompt_sha256=row["prompt_sha256"],
            response_schema_sha256=row["response_schema_sha256"],
            detector_rules_version=row["detector_rules_version"],
            detector_rules_sha256=row["detector_rules_sha256"],
            parser_bundle_json=row["parser_bundle_json"],
            parser_bundle_sha256=row["parser_bundle_sha256"],
            chunk_chars=row["chunk_chars"],
            overlap_chars=row["overlap_chars"],
            num_ctx=row["num_ctx"],
            num_predict=row["num_predict"],
            isolation_mode=row["isolation_mode"],
            reduced_isolation_ack=bool(ack),
        )
    except (KeyError, TypeError, ValueError, WorkerContractError) as exc:
        raise ForkRequired(
            "persisted Analyst run identity is not a runnable C10 contract"
        ) from exc


def abandon_run(
    run_id: str, *, now_utc: str | None = None, path: Path | None = None,
) -> None:
    """Explicitly terminalize all remaining files on a lease-free run."""
    _require_text(run_id, "run_id")
    timestamp = _utc_now() if now_utc is None else _require_text(now_utc, "now_utc")

    def operation(conn: sqlite3.Connection) -> None:
        from .ollama_state import reconcile_dispatching_contacts

        lease = conn.execute(
            "SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND run_id=?", (run_id,)
        ).fetchone()
        if lease is not None:
            raise AnalystStoreError("cannot abandon a run with an active worker lease")
        row = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise AnalystStoreError("Analyst run does not exist")
        state = RunState(str(row["state"]))
        if state not in RESUMABLE_RUN_STATES:
            raise AnalystStoreError("Analyst run is not abandonable")
        reconcile_dispatching_contacts(
            conn, run_id, timestamp, cancelled=True,
        )
        conn.execute(
            "UPDATE analyst_model_attempts SET state='cancelled_unverified',"
            "finished_at_utc=?,failure_code='cancelled_unverified' "
            "WHERE state='dispatching' AND chunk_id IN ("
            "SELECT c.chunk_id FROM analyst_chunks c JOIN analyst_files f "
            "ON f.file_id=c.file_id WHERE f.run_id=?)",
            (timestamp, run_id),
        )
        conn.execute(
            "UPDATE analyst_files SET work_state='terminal',"
            "terminal_code='cancelled_abandoned',terminal_detail='operator_abandon',"
            "active_generation=NULL,revision=revision+1,updated_at_utc=? "
            "WHERE run_id=? AND work_state!='terminal'",
            (timestamp, run_id),
        )
        cursor = conn.execute(
            "UPDATE analyst_runs SET state='abandoned',completion_code='abandoned',"
            "finished_at_utc=?,updated_at_utc=?,revision=revision+1 "
            "WHERE run_id=? AND state=?",
            (timestamp, timestamp, run_id, state.value),
        )
        if cursor.rowcount != 1:
            raise AnalystStoreError("Analyst run changed during abandon")

    run_immediate(operation, path=path)


def _connect(
    path: Path, *, read_only: bool, validate: bool,
) -> sqlite3.Connection:
    before = _require_owner_file(path)
    mode = "ro" if read_only else "rw"
    uri_path = quote(os.fspath(path.absolute()), safe="/")
    uri = f"file:{uri_path}?mode={mode}&cache=private"
    try:
        conn = sqlite3.connect(
            uri,
            uri=True,
            autocommit=True,
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
    except sqlite3.Error:
        after = _require_owner_file(path)
        _require_same_file(before, after)
        raise
    try:
        _configure_connection(conn, read_only=read_only)
        after = _require_owner_file(path)
        _require_same_file(before, after)
        if validate:
            validate_runtime_schema(conn)
        conn.row_factory = sqlite3.Row
        return conn
    except BaseException:
        conn.close()
        raise


def _audit_existing_database(path: Path) -> None:
    """Reject partial or foreign state before any writable SQLite open."""
    conn = _connect(path, read_only=True, validate=False)
    try:
        app_row = conn.execute("PRAGMA application_id").fetchone()
        version_row = conn.execute("PRAGMA user_version").fetchone()
        if app_row is None or version_row is None:
            raise AnalystSchemaError("SQLite did not return database identity PRAGMAs")
        identity = (int(app_row[0]), int(version_row[0]))
        objects = conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if identity == (APPLICATION_ID, SCHEMA_VERSION):
            validate_schema(conn)
        elif identity == (APPLICATION_ID, PREVIOUS_SCHEMA_VERSION):
            validate_v1_migration_candidate(conn)
        elif identity != (0, 0) or objects is not None:
            raise AnalystSchemaError(
                "refusing to open a partial, foreign, or versioned Analyst database"
            )
    finally:
        conn.close()


def _recover_hot_journal(path: Path) -> None:
    """Permit SQLite's rollback recovery before the read-only schema audit."""
    journal = Path(os.fspath(path) + "-journal")
    if not journal.exists() and not journal.is_symlink():
        return
    _require_owner_file(journal)
    before = _require_owner_file(path)
    if before.st_size:
        application_id, user_version = _raw_sqlite_identity(path)
        if (
            application_id != APPLICATION_ID
            or user_version not in KNOWN_SCHEMA_VERSIONS
        ):
            raise AnalystSchemaError(
                "refusing hot-journal recovery for untrusted database identity"
            )
    uri_path = quote(os.fspath(path.absolute()), safe="/")
    conn = sqlite3.connect(
        f"file:{uri_path}?mode=rw&cache=private",
        uri=True,
        autocommit=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    try:
        # The first database read owns SQLite's native hot-journal recovery.
        conn.execute("PRAGMA schema_version").fetchone()
    finally:
        conn.close()
    _require_same_file(before, _require_owner_file(path))


def _raw_sqlite_identity(path: Path) -> tuple[int, int]:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        header = os.read(fd, 100)
    finally:
        os.close(fd)
    if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
        raise AnalystSchemaError("Analyst sidecar has an invalid SQLite header")
    return (
        int.from_bytes(header[68:72], "big"),
        int.from_bytes(header[60:64], "big"),
    )


def _configure_connection(conn: sqlite3.Connection, *, read_only: bool) -> None:
    if not read_only:
        row = conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        if row is None or str(row[0]).lower() != "delete":
            raise AnalystStoreError("SQLite refused Analyst rollback-journal mode")
    journal = conn.execute("PRAGMA journal_mode").fetchone()
    if journal is None or str(journal[0]).lower() != "delete":
        raise AnalystStoreError("Analyst sidecar is not in rollback-journal mode")

    settings = (
        ("synchronous", "EXTRA", 3),
        ("foreign_keys", "ON", 1),
        ("busy_timeout", str(BUSY_TIMEOUT_MS), BUSY_TIMEOUT_MS),
        ("mmap_size", "0", 0),
        ("temp_store", "MEMORY", 2),
        ("trusted_schema", "OFF", 0),
    )
    for name, value, expected in settings:
        conn.execute(f"PRAGMA {name}={value}")
        row = conn.execute(f"PRAGMA {name}").fetchone()
        if row is None or int(row[0]) != expected:
            raise AnalystStoreError(f"SQLite refused required Analyst {name} setting")
    if read_only:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone() != (1,):
            raise AnalystStoreError("SQLite refused read-only Analyst connection")


def _ensure_owner_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PermissionError("Analyst sidecar parent is not a real directory")
    if info.st_uid != os.getuid():
        raise PermissionError("Analyst sidecar parent is not owned by this user")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.lstat().st_mode) != 0o700:
        raise PermissionError("Analyst sidecar parent is not mode 0700")


def _create_database_file(path: Path) -> bool:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            _require_owner_file(path)
            return False
        try:
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent_fd)
        return True
    finally:
        os.close(parent_fd)


def _require_owner_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PermissionError("Analyst sidecar is not a regular file")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError("Analyst sidecar must be owner-only mode 0600")
    return info


def _require_same_file(before: os.stat_result, after: os.stat_result) -> None:
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise AnalystStoreError("Analyst sidecar identity changed while opening")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_primary_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    return type(code) is int and code & 0xFF == sqlite3.SQLITE_BUSY


def _canonical_json_identity(value: Mapping[str, object]) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("run identity must be a mapping")
    try:
        body = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("run identity is not canonical JSON data") from exc
    if not body or len(body) > 65_536:
        raise ValueError("run identity exceeds its JSON bound")
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _decode_canonical_identity(
    body: object, digest: object, label: str,
) -> dict[str, object]:
    """Revalidate exact stored JSON bytes, object shape, and self-hash."""
    if type(body) is not str or not body or len(body) > 65_536:
        raise WorkerContractError(f"{label} JSON is invalid")
    if type(digest) is not str or len(digest) != 64:
        raise WorkerContractError(f"{label} hash is invalid")
    try:
        encoded = body.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise WorkerContractError(f"{label} JSON is not Unicode scalar text") from exc
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise WorkerContractError(f"{label} hash does not match its JSON")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WorkerContractError(f"{label} JSON has a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            body,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                WorkerContractError(f"{label} JSON has a non-finite number")
            ),
        )
    except WorkerContractError:
        raise
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError) as exc:
        raise WorkerContractError(f"{label} JSON cannot be decoded") from exc
    if type(value) is not dict:
        raise WorkerContractError(f"{label} JSON must be an object")
    canonical, _ = _canonical_json_identity(value)
    if canonical != body:
        raise WorkerContractError(f"{label} JSON is not canonical")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be nonempty text")
    return value


__all__ = [
    "AnalystStoreBusy",
    "AnalystStoreError",
    "BUSY_TIMEOUT_MS",
    "ForkRequired",
    "RunSpec",
    "TRANSACTION_ATTEMPTS",
    "abandon_run",
    "create_run",
    "get_db_path",
    "initialize_database",
    "list_active_runs",
    "load_worker_run",
    "open_connection",
    "run_immediate",
    "verify_run_spec",
]
