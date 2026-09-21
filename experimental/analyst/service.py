"""Desktop-safe creation, launch, cancellation, and hydration for Analyst runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Final, Sequence

from shared.path_service import DirracudaPaths, get_paths
from shared.extract_manifest import ExtractSummaryReference

from .inventory import InventoryResult, inventory_tree
from .manifest import ExtractionManifest, ManifestError, load_extraction_manifest
from .models import ANALYST_DEFAULTS
from .contact_contract import ContactStatus
from .ollama_client import OllamaClient, OllamaDiscoveryError
from .ollama_contract import (
    MAX_JSON_NODES,
    OLLAMA_ENDPOINT,
    DiscoveredModel,
    OllamaStatus,
    build_discovery_request,
    is_cloud_model_tag,
    valid_model_digest,
)
from .ollama_state import (
    finish_discovery_contact,
    precharge_discovery_contact,
)
from .state import RunState
from .store import (
    AnalystStoreBusy,
    AnalystStoreError,
    RunSpec,
    abandon_run as abandon_stored_run,
    create_run,
    delete_run as store_delete_run,
    initialize_database,
    open_connection,
    run_immediate,
)
from .worker_contract import build_source_identity, validate_worker_run_id


MAX_RUN_LIST: Final = 500
MAX_REPORT_LABEL_CHARS: Final = 120
_LABEL_COMPONENT_CHARS: Final = 48
_RUN_ID_BYTES: Final = 16
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MODE_VALUES = frozenset({"fast", "deep"})


class ServiceFailure(str, Enum):
    BUSY = "busy"
    CONTRACT = "contract"
    OUTPUT_INVALID = "output_invalid"
    OUTPUT_UNSAFE_FS = "output_unsafe_fs"
    INVENTORY = "inventory"
    STATE = "state"
    STORAGE = "storage"
    LAUNCH = "launch"
    CANCEL = "cancel"
    ABANDON = "abandon"
    DELETE = "delete"
    REPORT = "report"
    DISCOVERY = "discovery"


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
    output_base: Path | None = field(repr=False)
    report_label: str = field(repr=False)
    mode: str = "fast"

    def __post_init__(self) -> None:
        _require_absolute_path(self.source_root, "source")
        if self.output_base is not None:
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
    risk_level: str | None = None

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
            or self.risk_level not in {None, "HIGH", "MED", "LOW"}
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
            f"{self.terminal_files}/{self.discovered_files} finalized · "
            f"{self.model_reviewed_files}/{self.selected_files} model-reviewed"
        )

    @property
    def result_label(self) -> str:
        if self.risk_level is not None:
            return f"● {self.risk_level} risk"
        if self.state in {
            RunState.RUNNING,
            RunState.CANCEL_REQUESTED,
            RunState.FINALIZING,
        }:
            return self.progress
        return "-"


TokenFactory = Callable[[int], str]
PopenFactory = Callable[..., subprocess.Popen[bytes]]


def discover_models(*, path: Path | None = None) -> tuple[DiscoveredModel, ...]:
    """Explicitly discover, charge, and persist bounded local model identities."""
    try:
        initialize_database(path)
        request = build_discovery_request()
        charge = precharge_discovery_contact(
            request.endpoint, request.request_sha256, path=path,
        )
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None

    try:
        discovered = OllamaClient().list_models()
    except OllamaDiscoveryError as exc:
        _finish_failed_discovery(charge.contact_id, exc.status, path=path)
        raise AnalystServiceError(ServiceFailure.DISCOVERY) from None
    except Exception:
        _finish_failed_discovery(
            charge.contact_id, OllamaStatus.TRANSPORT_UNAVAILABLE, path=path,
        )
        raise AnalystServiceError(ServiceFailure.DISCOVERY) from None
    try:
        models = _normalize_discovered_models(discovered)
    except Exception:
        _finish_failed_discovery(
            charge.contact_id, OllamaStatus.PROTOCOL_VIOLATION, path=path,
        )
        raise AnalystServiceError(ServiceFailure.DISCOVERY) from None

    try:
        finished = finish_discovery_contact(
            charge.contact_id,
            ContactStatus.SUCCESS,
            len(models),
            path=path,
        )
        _upsert_discovered_models(
            request.endpoint, models, finished.finished_at_utc, path=path,
        )
        return models
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def list_discovered_models(
    *, path: Path | None = None,
) -> tuple[DiscoveredModel, ...]:
    """Read the persisted model list without contacting Ollama."""
    try:
        conn = open_connection(path, read_only=True)
        try:
            rows = conn.execute(
                "SELECT model_tag,model_digest FROM analyst_discovered_model "
                "WHERE endpoint=? ORDER BY model_tag",
                (OLLAMA_ENDPOINT,),
            ).fetchall()
        finally:
            conn.close()
        return tuple(
            DiscoveredModel(str(row["model_tag"]), str(row["model_digest"]))
            for row in rows
        )
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def create_directory_run(
    request: DirectoryRunRequest,
    *,
    model_tag: str | None = None,
    model_digest: str | None = None,
    path: Path | None = None,
    run_id_factory: TokenFactory = secrets.token_hex,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[str, InventoryResult]:
    """Inventory and atomically persist one standalone directory run."""
    if type(request) is not DirectoryRunRequest:
        raise TypeError("directory run requires a typed request")
    if not callable(run_id_factory):
        raise TypeError("run id factory must be callable")
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")
    try:
        selected_model_tag, selected_model_digest = _run_model_identity(
            model_tag, model_digest,
        )
    except (TypeError, ValueError):
        raise AnalystServiceError(ServiceFailure.CONTRACT) from None
    try:
        selected_output = _selected_output_base(request.output_base)
        _require_existing_directory(selected_output)
    except AnalystServiceError:
        raise
    try:
        inventory = inventory_tree(
            request.source_root,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
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
        _create_private_output_directory(selected_output, output_root)
        spec = RunSpec(
            run_id=run_id,
            mode=request.mode,
            source_mode="unknown",
            source_root=str(request.source_root),
            output_root=str(output_root),
            source_identity=build_source_identity(inventory),
            report_label=request.report_label,
            model_tag=selected_model_tag,
            model_digest=selected_model_digest,
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
    model_tag: str | None = None,
    model_digest: str | None = None,
    path: Path | None = None,
    paths: DirracudaPaths | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> RunLaunch:
    """Persist a run first, then launch it; launch failure leaves it resumable."""
    run_id, _inventory = create_directory_run(
        request,
        model_tag=model_tag,
        model_digest=model_digest,
        path=path,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    return launch_run(run_id, path=path, paths=paths)


def create_manifest_run(
    reference: ExtractSummaryReference,
    *,
    main_db_path: Path | None,
    output_base: Path | None,
    report_label: str,
    mode: str = "fast",
    model_tag: str | None = None,
    model_digest: str | None = None,
    path: Path | None = None,
    run_id_factory: TokenFactory = secrets.token_hex,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, ExtractionManifest]:
    """Persist one exact extraction-manifest run without guessing host identity."""
    if type(reference) is not ExtractSummaryReference:
        raise TypeError("manifest run requires a structured extraction reference")
    try:
        selected_model_tag, selected_model_digest = _run_model_identity(
            model_tag, model_digest,
        )
    except (TypeError, ValueError):
        raise AnalystServiceError(ServiceFailure.CONTRACT) from None
    try:
        from .worker_preflight import (
            current_detector_rules,
            current_parser_bundle_mapping,
        )
        from .worksheet import prompt_template_hash, schema_hash

        manifest = load_extraction_manifest(
            reference,
            main_db_path=main_db_path,
            cancel_check=cancel_check,
        )
        selected_output = _selected_output_base(output_base)
        request = DirectoryRunRequest(
            manifest.source_root, selected_output, report_label, mode,
        )
        _require_existing_directory(selected_output)
        run_id = run_id_factory(_RUN_ID_BYTES)
        if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("run id source returned an invalid value")
        detector_version, detector_sha256 = current_detector_rules()
        output_root = _manifest_output_root(request, run_id, manifest.ip_address)
        _create_private_output_directory(selected_output, output_root)
        spec = RunSpec(
            run_id=run_id,
            mode=mode,
            source_mode="extraction_manifest",
            source_root=str(manifest.source_root),
            output_root=str(output_root),
            source_identity=build_source_identity(manifest.inventory),
            report_label=report_label,
            model_tag=selected_model_tag,
            model_digest=selected_model_digest,
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
            host_type=manifest.host_type,
            protocol_server_id=manifest.protocol_server_id,
            ip_address=manifest.ip_address,
            port=manifest.port,
            extract_summary_row_id=reference.db_row_id,
        )
        initialize_database(path)
        create_run(spec, manifest.inventory, path=path)
        return run_id, manifest
    except ManifestError:
        raise AnalystServiceError(ServiceFailure.INVENTORY) from None
    except (TypeError, ValueError, OSError):
        raise AnalystServiceError(ServiceFailure.CONTRACT) from None
    except AnalystServiceError:
        raise
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def create_manifest_and_launch(
    reference: ExtractSummaryReference,
    *,
    main_db_path: Path | None,
    output_base: Path | None,
    report_label: str,
    mode: str = "fast",
    model_tag: str | None = None,
    model_digest: str | None = None,
    path: Path | None = None,
    paths: DirracudaPaths | None = None,
) -> RunLaunch:
    """Persist an exact manifest run first, then detach the production worker."""
    run_id, _manifest = create_manifest_run(
        reference,
        main_db_path=main_db_path,
        output_base=output_base,
        report_label=report_label,
        mode=mode,
        model_tag=model_tag,
        model_digest=model_digest,
        path=path,
    )
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


def abandon_run(run_id: str, *, path: Path | None = None) -> None:
    """Terminalize one lease-free resumable run."""
    try:
        abandon_stored_run(run_id, path=path)
    except AnalystStoreError:
        raise AnalystServiceError(ServiceFailure.ABANDON) from None


def delete_run(run_id: str, *, path: Path | None = None) -> None:
    try:
        output_root = store_delete_run(run_id, path=path)
    except AnalystStoreError:
        raise AnalystServiceError(ServiceFailure.DELETE) from None
    _remove_report_dir(output_root, run_id)
    _remove_run_logs(run_id)


def _remove_report_dir(output_root: str, run_id: str) -> None:
    path = Path(output_root)
    if not path.is_absolute():
        return
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if info.st_uid != os.getuid():
        return
    if run_id[:12] not in path.name:
        return
    shutil.rmtree(path, ignore_errors=True)


def _remove_run_logs(run_id: str) -> None:
    logs_dir = get_paths().analyst_logs_dir
    try:
        logs = tuple(logs_dir.glob(f"{run_id}-*.log"))
    except OSError:
        return
    for log in logs:
        try:
            info = log.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            if info.st_uid != os.getuid():
                continue
            log.unlink()
        except OSError:
            continue


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
                "rd.risk_level,"
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
                "LEFT JOIN analyst_read rd ON rd.run_id=r.run_id "
                "LEFT JOIN analyst_files f ON f.run_id=r.run_id "
                "GROUP BY r.run_id,rd.risk_level "
                "ORDER BY r.updated_at_utc DESC,r.run_id LIMIT ?",
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
                risk_level=(
                    None if row["risk_level"] is None else str(row["risk_level"])
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


def read_report_json(
    run_id: str, *, path: Path | None = None,
) -> tuple[dict, bool]:
    """Read validated report.json while surfacing content-seal drift."""
    try:
        from .report import open_completed_report_relaxed
        from .report_json import validate_report_json

        canonical = validate_worker_run_id(run_id)
        opened = open_completed_report_relaxed(canonical, path=path)
        payload = json.loads(opened.report_json_path.read_bytes())
        validate_report_json(payload)
        if payload["run"]["run_id"] != canonical:
            raise ValueError("report identity does not match the completed run")
        return payload, opened.changed
    except AnalystStoreBusy:
        raise AnalystServiceError(ServiceFailure.BUSY) from None
    except Exception:
        raise AnalystServiceError(ServiceFailure.REPORT) from None


def _normalize_discovered_models(value: object) -> tuple[DiscoveredModel, ...]:
    if type(value) is not tuple or len(value) > MAX_JSON_NODES:
        raise ValueError("discovered model result is outside its bound")
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("discovered model row is malformed")
        tag, digest = item
        if (
            type(tag) is not str
            or not tag
            or is_cloud_model_tag(tag)
            or tag in seen
            or not valid_model_digest(digest)
        ):
            raise ValueError("discovered model identity is invalid")
        seen.add(tag)
        models.append(DiscoveredModel(tag, digest))
    return tuple(sorted(models, key=lambda model: model.model_tag))


def _run_model_identity(
    model_tag: str | None,
    model_digest: str | None,
) -> tuple[str, str]:
    if model_tag is None and model_digest is None:
        return ANALYST_DEFAULTS.model_tag, ANALYST_DEFAULTS.model_digest
    if (
        type(model_tag) is not str
        or not model_tag
        or is_cloud_model_tag(model_tag)
        or not valid_model_digest(model_digest)
    ):
        raise ValueError("run model identity is invalid")
    return model_tag, model_digest


def _finish_failed_discovery(
    contact_id: str,
    status: OllamaStatus,
    *,
    path: Path | None,
) -> None:
    if status in {OllamaStatus.SUCCESS, OllamaStatus.MODEL_INVALID}:
        status = OllamaStatus.PROTOCOL_VIOLATION
    try:
        durable_status = ContactStatus(status.value)
        finish_discovery_contact(
            contact_id, durable_status, None, path=path,
        )
    except Exception:
        raise AnalystServiceError(ServiceFailure.STORAGE) from None


def _upsert_discovered_models(
    endpoint: str,
    models: tuple[DiscoveredModel, ...],
    timestamp: str,
    *,
    path: Path | None,
) -> None:
    def operation(conn) -> None:
        for model in models:
            conn.execute(
                "INSERT INTO analyst_discovered_model("
                "endpoint,model_tag,model_digest,first_seen_utc,last_seen_utc) "
                "VALUES(?,?,?,?,?) ON CONFLICT(endpoint,model_tag) DO UPDATE SET "
                "model_digest=excluded.model_digest,"
                "last_seen_utc=excluded.last_seen_utc",
                (
                    endpoint,
                    model.model_tag,
                    model.model_digest,
                    timestamp,
                    timestamp,
                ),
            )

    run_immediate(operation, path=path)


def _run_output_root(request: DirectoryRunRequest, run_id: str) -> Path:
    label = request.report_label.casefold().encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", label).strip("-")[:_LABEL_COMPONENT_CHARS]
    if not slug:
        slug = "unattributed"
    digest = hashlib.sha256(request.report_label.encode("utf-8")).hexdigest()[:8]
    component = f"{slug}-{digest}-{run_id[:12]}"
    return _selected_output_base(request.output_base) / "_analyst" / component


def _manifest_output_root(
    request: DirectoryRunRequest, run_id: str, host_identity: str,
) -> Path:
    label = host_identity.casefold().encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", label).strip("-")[:_LABEL_COMPONENT_CHARS]
    if not slug:
        slug = "host"
    digest = hashlib.sha256(host_identity.encode("utf-8")).hexdigest()[:8]
    return request.output_base / "_analyst" / f"{slug}-{digest}-{run_id[:12]}"


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
    try:
        _require_absolute_path(path, "output")
        current = _open_existing_directory(path)
    except (OSError, TypeError, ValueError):
        raise AnalystServiceError(ServiceFailure.OUTPUT_INVALID) from None
    os.close(current)


def _selected_output_base(output_base: Path | None) -> Path:
    if output_base is not None:
        return output_base
    try:
        selected = get_paths().analyst_reports_dir
        selected.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = selected.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AnalystServiceError(ServiceFailure.OUTPUT_INVALID)
        if info.st_uid != os.getuid():
            raise AnalystServiceError(ServiceFailure.OUTPUT_UNSAFE_FS)
        os.chmod(selected, 0o700)
        if stat.S_IMODE(selected.lstat().st_mode) != 0o700:
            raise AnalystServiceError(ServiceFailure.OUTPUT_UNSAFE_FS)
        return selected
    except AnalystServiceError:
        raise
    except OSError:
        raise AnalystServiceError(ServiceFailure.OUTPUT_UNSAFE_FS) from None


def _open_existing_directory(path: Path) -> int:
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
        return current
    except BaseException:
        os.close(current)
        raise


def _create_private_output_directory(output_base: Path, output_root: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        relative = output_root.relative_to(output_base)
        if len(relative.parts) != 2:
            raise OSError("output root is outside the output contract")
        current = _open_existing_directory(output_base)
        try:
            for index, component in enumerate(relative.parts):
                is_target = index == len(relative.parts) - 1
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    if is_target:
                        raise OSError("output target already exists") from None
                child = os.open(component, flags, dir_fd=current)
                info = os.fstat(child)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                ):
                    os.close(child)
                    raise AnalystServiceError(ServiceFailure.OUTPUT_UNSAFE_FS)
                os.close(current)
                current = child
        finally:
            os.close(current)
    except AnalystServiceError:
        raise
    except (OSError, TypeError, ValueError):
        raise AnalystServiceError(ServiceFailure.OUTPUT_INVALID) from None


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
    "abandon_run",
    "cancel_run",
    "completed_report_html",
    "create_and_launch",
    "create_manifest_and_launch",
    "create_manifest_run",
    "create_directory_run",
    "delete_run",
    "launch_run",
    "discover_models",
    "list_discovered_models",
    "list_run_summaries",
    "reconcile_for_hydration",
    "read_report_json",
    "resume_run",
)
