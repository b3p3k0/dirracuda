"""Desktop-safe creation, launch, cancellation, and hydration for Analyst runs."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Final, Sequence

from shared.path_service import DirracudaPaths, get_paths

from .inventory import InventoryResult, inventory_tree
from .models import ANALYST_DEFAULTS
from .state import RunState
from .store import RunSpec, create_run, initialize_database, open_connection
from .worker_contract import build_source_identity, validate_worker_run_id


MAX_RUN_LIST: Final = 500
MAX_REPORT_LABEL_CHARS: Final = 120
_LABEL_COMPONENT_CHARS: Final = 48
_RUN_ID_BYTES: Final = 16
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MODE_VALUES = frozenset({"fast", "deep"})


class ServiceFailure(str, Enum):
    CONTRACT = "contract"
    INVENTORY = "inventory"
    STATE = "state"
    STORAGE = "storage"
    LAUNCH = "launch"
    CANCEL = "cancel"
    REPORT = "report"


class AnalystServiceError(RuntimeError):
    """A closed, content-free desktop service failure."""

    def __init__(self, code: ServiceFailure) -> None:
        if type(code) is not ServiceFailure:
            raise TypeError("service failure must use the closed enum")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class DirectoryRunRequest:
    """Validated low-input request used by the Accessories launcher."""

    source_root: Path = field(repr=False)
    output_base: Path = field(repr=False)
    report_label: str = field(repr=False)
    mode: str = "fast"

    def __post_init__(self) -> None:
        _require_absolute_path(self.source_root, "source")
        _require_absolute_path(self.output_base, "output")
        if (
            type(self.report_label) is not str
            or not self.report_label.strip()
            or self.report_label != self.report_label.strip()
            or len(self.report_label) > MAX_REPORT_LABEL_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in self.report_label)
        ):
            raise ValueError("report label is outside the desktop contract")
        if type(self.mode) is not str or self.mode not in _MODE_VALUES:
            raise ValueError("analysis mode is outside the desktop contract")


@dataclass(frozen=True, slots=True)
class RunLaunch:
    run_id: str
    pid: int
    log_file: Path = field(repr=False)

    def __post_init__(self) -> None:
        validate_worker_run_id(self.run_id)
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("worker pid must be positive")
        _require_absolute_path(self.log_file, "worker log")


@dataclass(frozen=True, slots=True)
class CancelResult:
    run_id: str
    signal_sent: bool

    def __post_init__(self) -> None:
        validate_worker_run_id(self.run_id)
        if type(self.signal_sent) is not bool:
            raise ValueError("cancel signal result must be bool")


@dataclass(frozen=True, slots=True)
class AnalystRunSummary:
    run_id: str
    state: RunState
    report_label: str = field(repr=False)
    mode: str
    created_at_utc: str
    updated_at_utc: str
    discovered_files: int
    terminal_files: int
    selected_files: int
    model_reviewed_files: int
    detector_hits: int
    model_findings: int
    schedule_state: str
    resource_not_before_utc: str | None

    def __post_init__(self) -> None:
        validate_worker_run_id(self.run_id)
        if type(self.state) is not RunState:
            raise ValueError("run summary state is invalid")
        if (
            type(self.report_label) is not str
            or not self.report_label
            or type(self.mode) is not str
            or self.mode not in _MODE_VALUES
            or type(self.created_at_utc) is not str
            or not self.created_at_utc
            or type(self.updated_at_utc) is not str
            or not self.updated_at_utc
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.discovered_files,
                    self.terminal_files,
                    self.selected_files,
                    self.model_reviewed_files,
                    self.detector_hits,
                    self.model_findings,
                )
            )
            or self.terminal_files > self.discovered_files
            or self.selected_files > self.discovered_files
            or self.model_reviewed_files > self.selected_files
            or self.schedule_state not in {"available", "backoff", "paused_resource"}
            or (
                self.resource_not_before_utc is not None
                and type(self.resource_not_before_utc) is not str
            )
        ):
            raise ValueError("run summary is internally inconsistent")

    @property
    def task_id(self) -> str:
        return f"analyst:{self.run_id}"

    @property
    def progress(self) -> str:
        return (
            f"{self.terminal_files}/{self.discovered_files} files · "
            f"{self.model_reviewed_files}/{self.selected_files} model-reviewed"
        )


TokenFactory = Callable[[int], str]
PopenFactory = Callable[..., subprocess.Popen[bytes]]


def create_directory_run(
    request: DirectoryRunRequest,
    *,
    path: Path | None = None,
    run_id_factory: TokenFactory = secrets.token_hex,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, InventoryResult]:
    """Inventory and atomically persist one standalone directory run."""
    if type(request) is not DirectoryRunRequest:
        raise TypeError("directory run requires a typed request")
    if not callable(run_id_factory):
        raise TypeError("run id factory must be callable")
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")
    try:
        _require_existing_directory(request.output_base)
    except OSError:
        raise AnalystServiceError(ServiceFailure.CONTRACT) from None
    try:
        inventory = inventory_tree(
            request.source_root, cancel_check=cancel_check,
        )
    except Exception:
        raise AnalystServiceError(ServiceFailure.INVENTORY) from None
    try:
        from .worker_preflight import (
            current_detector_rules,
            current_parser_bundle_mapping,
        )
        from .worksheet import prompt_template_hash, schema_hash

        run_id = run_id_factory(_RUN_ID_BYTES)
        if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("run id source returned an invalid value")
        detector_version, detector_sha256 = current_detector_rules()
        output_root = _run_output_root(request, run_id)
        spec = RunSpec(
            run_id=run_id,
            mode=request.mode,
            source_mode="unknown",
            source_root=str(request.source_root),
            output_root=str(output_root),
            source_identity=build_source_identity(inventory),
            report_label=request.report_label,
            model_tag=ANALYST_DEFAULTS.model_tag,
            model_digest=ANALYST_DEFAULTS.model_digest,
            worksheet_version=ANALYST_DEFAULTS.worksheet_version,
            prompt_sha256=prompt_template_hash(),
            response_schema_sha256=schema_hash(),
            detector_rules_version=detector_version,
            detector_rules_sha256=detector_sha256,
            parser_bundle=current_parser_bundle_mapping(),
            chunk_chars=ANALYST_DEFAULTS.chunk_chars,
            overlap_chars=ANALYST_DEFAULTS.overlap_chars,
            num_ctx=ANALYST_DEFAULTS.num_ctx,
            num_predict=ANALYST_DEFAULTS.num_predict,
            isolation_mode="strict",
            reduced_isolation_ack=False,
        )
        initialize_database(path)
        create_run(spec, inventory, path=path)
        return run_id, inventory
    except AnalystServiceError:
        raise
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def launch_run(
    run_id: str,
    *,
    path: Path | None = None,
    paths: DirracudaPaths | None = None,
    popen_factory: PopenFactory | None = None,
    log_token_factory: TokenFactory = secrets.token_hex,
) -> RunLaunch:
    """Launch the exact detached production worker without waiting for it."""
    try:
        canonical = validate_worker_run_id(run_id)
    except Exception:
        raise AnalystServiceError(ServiceFailure.CONTRACT) from None
    if path is not None and Path(path) != (paths or get_paths()).analyst_db_file:
        # A DB override is test-only and the detached production worker cannot
        # safely inherit it through argv or environment.
        raise AnalystServiceError(ServiceFailure.CONTRACT)
    chosen_popen = subprocess.Popen if popen_factory is None else popen_factory
    if not callable(chosen_popen) or not callable(log_token_factory):
        raise TypeError("launch dependencies must be callable")
    selected = paths or get_paths()
    try:
        _require_launchable_run(canonical, path=path)
        python = selected.repo_root / "venv" / "bin" / "python"
        resolved_python = python.resolve(strict=True)
        info = resolved_python.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(resolved_python, os.X_OK):
            raise OSError("worker interpreter is unavailable")
        log_dir = _ensure_private_directory(selected.analyst_logs_dir)
        token = log_token_factory(8)
        if (
            type(token) is not str
            or len(token) != 16
            or any(char not in "0123456789abcdef" for char in token)
        ):
            raise ValueError("log token is invalid")
        log_file = log_dir / f"{canonical}-{token}.log"
        fd = os.open(
            log_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(fd, "wb", closefd=True) as sink:
                process = chosen_popen(
                    (
                        str(python), "-B", "-m", "experimental.analyst.worker",
                        "--run-id", canonical,
                    ),
                    cwd=str(selected.repo_root),
                    shell=False,
                    close_fds=True,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                )
        except BaseException:
            try:
                log_file.unlink()
            except OSError:
                pass
            raise
        pid = process.pid
        return RunLaunch(canonical, pid, log_file)
    except AnalystServiceError:
        raise
    except Exception:
        raise AnalystServiceError(ServiceFailure.LAUNCH) from None


def create_and_launch(
    request: DirectoryRunRequest,
    *,
    path: Path | None = None,
    paths: DirracudaPaths | None = None,
) -> RunLaunch:
    """Persist a run first, then launch it; launch failure leaves it resumable."""
    run_id, _inventory = create_directory_run(request, path=path)
    return launch_run(run_id, path=path, paths=paths)


def cancel_run(run_id: str, *, path: Path | None = None) -> CancelResult:
    """Persist cancellation intent before any exact worker signal."""
    try:
        from .lease import request_cancel, signal_cancel

        canonical = validate_worker_run_id(run_id)
        fence = request_cancel(canonical, path=path)
        signalled = False if fence is None else signal_cancel(fence)
        return CancelResult(canonical, signalled)
    except Exception:
        raise AnalystServiceError(ServiceFailure.CANCEL) from None


def resume_run(
    run_id: str,
    *,
    path: Path | None = None,
    paths: DirracudaPaths | None = None,
) -> RunLaunch:
    """Explicitly authorize a due resource pause, then launch a new worker."""
    try:
        canonical = validate_worker_run_id(run_id)
        conn = open_connection(path, read_only=True)
        try:
            row = conn.execute(
                "SELECT r.state,s.state AS schedule_state FROM analyst_runs r "
                "JOIN analyst_ollama_schedule s ON s.run_id=r.run_id "
                "WHERE r.run_id=?", (canonical,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or RunState(str(row["state"])) not in {
            RunState.READY,
            RunState.INTERRUPTED,
            RunState.CANCELLED_PENDING_RESUME,
        }:
            raise AnalystServiceError(ServiceFailure.STATE)
        if str(row["schedule_state"]) == "paused_resource":
            from .ollama_state import authorize_resource_resume

            authorize_resource_resume(canonical, path=path)
        return launch_run(canonical, path=path, paths=paths)
    except AnalystServiceError:
        raise
    except Exception:
        raise AnalystServiceError(ServiceFailure.STATE) from None


def list_run_summaries(
    *, path: Path | None = None, limit: int = 100,
) -> tuple[AnalystRunSummary, ...]:
    """Return bounded durable summaries for task hydration and the run browser."""
    if type(limit) is not int or not 1 <= limit <= MAX_RUN_LIST:
        raise ValueError("run summary limit is outside the desktop contract")
    try:
        conn = open_connection(path, read_only=True)
        try:
            rows = conn.execute(
                "SELECT r.run_id,r.state,r.report_label,r.mode,r.created_at_utc,"
                "r.updated_at_utc,s.state AS schedule_state,s.not_before_utc,"
                "count(f.file_id) AS discovered_files,"
                "sum(CASE WHEN f.work_state='terminal' THEN 1 ELSE 0 END) terminal_files,"
                "sum(CASE WHEN f.selected_for_model=1 THEN 1 ELSE 0 END) selected_files,"
                "sum(CASE WHEN f.stage IN ('model_reviewed','model_response_valid') "
                "THEN 1 ELSE 0 END) model_reviewed_files,"
                "(SELECT count(*) FROM analyst_detector_hits h JOIN analyst_files hf "
                "ON hf.file_id=h.file_id WHERE hf.run_id=r.run_id) detector_hits,"
                "(SELECT count(*) FROM analyst_model_findings m JOIN analyst_chunks c "
                "ON c.chunk_id=m.chunk_id JOIN analyst_files mf ON mf.file_id=c.file_id "
                "WHERE mf.run_id=r.run_id AND mf.terminal_code='complete_model_reviewed') "
                "model_findings FROM analyst_runs r "
                "JOIN analyst_ollama_schedule s ON s.run_id=r.run_id "
                "LEFT JOIN analyst_files f ON f.run_id=r.run_id "
                "GROUP BY r.run_id ORDER BY r.updated_at_utc DESC,r.run_id LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return tuple(
            AnalystRunSummary(
                run_id=str(row["run_id"]),
                state=RunState(str(row["state"])),
                report_label=str(row["report_label"]),
                mode=str(row["mode"]),
                created_at_utc=str(row["created_at_utc"]),
                updated_at_utc=str(row["updated_at_utc"]),
                discovered_files=int(row["discovered_files"]),
                terminal_files=int(row["terminal_files"] or 0),
                selected_files=int(row["selected_files"] or 0),
                model_reviewed_files=int(row["model_reviewed_files"] or 0),
                detector_hits=int(row["detector_hits"]),
                model_findings=int(row["model_findings"]),
                schedule_state=str(row["schedule_state"]),
                resource_not_before_utc=(
                    None if row["not_before_utc"] is None
                    else str(row["not_before_utc"])
                ),
            )
            for row in rows
        )
    except AnalystServiceError:
        raise
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def reconcile_for_hydration(*, path: Path | None = None) -> str:
    """Run one content-free lease reconciliation before desktop hydration."""
    try:
        from .lease import reconcile_lease

        return reconcile_lease(path=path).value
    except Exception:
        raise AnalystServiceError(ServiceFailure.STATE) from None


def completed_report_html(run_id: str, *, path: Path | None = None) -> Path:
    """Verify a complete report manifest before returning its local HTML path."""
    try:
        from .report import verify_completed_report

        canonical = validate_worker_run_id(run_id)
        verify_completed_report(canonical, path=path)
        conn = open_connection(path, read_only=True)
        try:
            row = conn.execute(
                "SELECT output_root FROM analyst_runs "
                "WHERE run_id=? AND state='complete'", (canonical,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValueError("complete run is unavailable")
        target = Path(str(row["output_root"])) / "report.html"
        _require_absolute_path(target, "report")
        return target
    except Exception:
        raise AnalystServiceError(ServiceFailure.REPORT) from None


def _run_output_root(request: DirectoryRunRequest, run_id: str) -> Path:
    label = request.report_label.casefold().encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", label).strip("-")[:_LABEL_COMPONENT_CHARS]
    if not slug:
        slug = "unattributed"
    digest = hashlib.sha256(request.report_label.encode("utf-8")).hexdigest()[:8]
    component = f"{slug}-{digest}-{run_id[:12]}"
    return request.output_base / "_analyst" / component


def _require_launchable_run(run_id: str, *, path: Path | None) -> None:
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or RunState(str(row["state"])) not in {
        RunState.READY,
        RunState.INTERRUPTED,
        RunState.CANCELLED_PENDING_RESUME,
    }:
        raise AnalystServiceError(ServiceFailure.STATE)


def _ensure_private_directory(path: Path) -> Path:
    _require_absolute_path(path, "log directory")
    components = tuple(os.fspath(path).split("/")[1:])
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current = -1
    try:
        current = os.open("/", flags)
        for component in components:
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=current)
                child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        info = os.fstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise OSError("log directory is unsafe")
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.fchmod(current, 0o700)
        return path
    except OSError:
        raise AnalystServiceError(ServiceFailure.LAUNCH) from None
    finally:
        if current >= 0:
            os.close(current)


def _require_existing_directory(path: Path) -> None:
    """Open every existing output-base component without following symlinks."""
    components = tuple(os.fspath(path).split("/")[1:])
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for component in components:
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise OSError("output base is not a directory")
    finally:
        os.close(current)


def _require_absolute_path(path: object, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError(f"{label} path must be an absolute Path")
    raw = os.fspath(path)
    parts = tuple(raw.split("/")[1:])
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in raw
        or "\x00" in raw
        or len(raw) > 4096
    ):
        raise ValueError(f"{label} path is not canonical")
    return path


__all__: Sequence[str] = (
    "AnalystRunSummary",
    "AnalystServiceError",
    "CancelResult",
    "DirectoryRunRequest",
    "MAX_REPORT_LABEL_CHARS",
    "MAX_RUN_LIST",
    "RunLaunch",
    "ServiceFailure",
    "cancel_run",
    "completed_report_html",
    "create_and_launch",
    "create_directory_run",
    "launch_run",
    "list_run_summaries",
    "reconcile_for_hydration",
    "resume_run",
)
