"""Scan task queue and subprocess runner for the Web UI.

C4 intentionally delegates scans to the existing CLI entrypoints. The queue is
in-memory, single-process, and one-active-scan-at-a-time for v1.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import enum
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from experimental.webui.post_scan_probe import run_post_scan_probe

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROTOCOL_SCRIPTS = {
    "smb": _REPO_ROOT / "cli" / "smbseek.py",
    "ftp": _REPO_ROOT / "cli" / "ftpseek.py",
    "http": _REPO_ROOT / "cli" / "httpseek.py",
}
_CONFIG_PATH = _REPO_ROOT / "conf" / "config.json"

# Keep aligned with cli/smbseek.py::validate_country_codes.
_VALID_COUNTRY_CODES = frozenset(
    {
        "AD",
        "AE",
        "AF",
        "AG",
        "AI",
        "AL",
        "AM",
        "AO",
        "AQ",
        "AR",
        "AS",
        "AT",
        "AU",
        "AW",
        "AX",
        "AZ",
        "BA",
        "BB",
        "BD",
        "BE",
        "BF",
        "BG",
        "BH",
        "BI",
        "BJ",
        "BL",
        "BM",
        "BN",
        "BO",
        "BQ",
        "BR",
        "BS",
        "BT",
        "BV",
        "BW",
        "BY",
        "BZ",
        "CA",
        "CC",
        "CD",
        "CF",
        "CG",
        "CH",
        "CI",
        "CK",
        "CL",
        "CM",
        "CN",
        "CO",
        "CR",
        "CU",
        "CV",
        "CW",
        "CX",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DM",
        "DO",
        "DZ",
        "EC",
        "EE",
        "EG",
        "EH",
        "ER",
        "ES",
        "ET",
        "FI",
        "FJ",
        "FK",
        "FM",
        "FO",
        "FR",
        "GA",
        "GB",
        "GD",
        "GE",
        "GF",
        "GG",
        "GH",
        "GI",
        "GL",
        "GM",
        "GN",
        "GP",
        "GQ",
        "GR",
        "GS",
        "GT",
        "GU",
        "GW",
        "GY",
        "HK",
        "HM",
        "HN",
        "HR",
        "HT",
        "HU",
        "ID",
        "IE",
        "IL",
        "IM",
        "IN",
        "IO",
        "IQ",
        "IR",
        "IS",
        "IT",
        "JE",
        "JM",
        "JO",
        "JP",
        "KE",
        "KG",
        "KH",
        "KI",
        "KM",
        "KN",
        "KP",
        "KR",
        "KW",
        "KY",
        "KZ",
        "LA",
        "LB",
        "LC",
        "LI",
        "LK",
        "LR",
        "LS",
        "LT",
        "LU",
        "LV",
        "LY",
        "MA",
        "MC",
        "MD",
        "ME",
        "MF",
        "MG",
        "MH",
        "MK",
        "ML",
        "MM",
        "MN",
        "MO",
        "MP",
        "MQ",
        "MR",
        "MS",
        "MT",
        "MU",
        "MV",
        "MW",
        "MX",
        "MY",
        "MZ",
        "NA",
        "NC",
        "NE",
        "NF",
        "NG",
        "NI",
        "NL",
        "NO",
        "NP",
        "NR",
        "NU",
        "NZ",
        "OM",
        "PA",
        "PE",
        "PF",
        "PG",
        "PH",
        "PK",
        "PL",
        "PM",
        "PN",
        "PR",
        "PS",
        "PT",
        "PW",
        "PY",
        "QA",
        "RE",
        "RO",
        "RS",
        "RU",
        "RW",
        "SA",
        "SB",
        "SC",
        "SD",
        "SE",
        "SG",
        "SH",
        "SI",
        "SJ",
        "SK",
        "SL",
        "SM",
        "SN",
        "SO",
        "SR",
        "SS",
        "ST",
        "SV",
        "SX",
        "SY",
        "SZ",
        "TC",
        "TD",
        "TF",
        "TG",
        "TH",
        "TJ",
        "TK",
        "TL",
        "TM",
        "TN",
        "TO",
        "TR",
        "TT",
        "TV",
        "TW",
        "TZ",
        "UA",
        "UG",
        "UM",
        "US",
        "UY",
        "UZ",
        "VA",
        "VC",
        "VE",
        "VG",
        "VI",
        "VN",
        "VU",
        "WF",
        "WS",
        "YE",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    }
)

_PROGRESS_RE = re.compile(r"\b(\d{1,3})%")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
logger = logging.getLogger(__name__)


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CancelResult(str, enum.Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    TERMINAL = "terminal"


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol: str
    countries: list[str] = Field(default_factory=list)
    max_shodan_results: int = 100
    run_probe_after_scan: bool = False
    filters: str = ""

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, value: str) -> str:
        if value not in _PROTOCOL_SCRIPTS:
            raise ValueError(f"protocol must be smb, ftp, or http; got {value!r}")
        return value

    @field_validator("countries")
    @classmethod
    def _validate_countries(cls, value: list[str]) -> list[str]:
        bad = [code for code in value if code.upper() not in _VALID_COUNTRY_CODES]
        if bad:
            raise ValueError(f"invalid country code(s): {bad}")
        return [code.upper() for code in value]

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, value: str) -> str:
        if len(value) > 500:
            raise ValueError("filters must not exceed 500 characters")
        if any(ord(char) < 32 for char in value):
            raise ValueError("filters contain invalid control characters")
        return value

    @field_validator("max_shodan_results")
    @classmethod
    def _validate_max_shodan_results(cls, value: int) -> int:
        if value < 1 or value > 100000:
            raise ValueError("max_shodan_results must be between 1 and 100000")
        return value


@dataclass
class ScanTask:
    task_id: str
    request: ScanRequest
    status: TaskStatus = TaskStatus.QUEUED
    stdout_lines: list[str] = field(default_factory=list)
    progress_pct: float = 0.0
    progress_message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "task_id": self.task_id,
                "status": self.status.value,
                "protocol": self.request.protocol,
                "countries": list(self.request.countries),
                "progress_pct": self.progress_pct,
                "progress_message": self.progress_message,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "log": list(self.stdout_lines[-100:]),
            }


def _query_credit_budget(max_results: int) -> int:
    return estimate_query_credits_for_cap(max_results)


def estimate_query_credits_for_cap(max_results: int) -> int:
    """Estimate Shodan query-credit usage for a candidate cap."""
    return max(1, (int(max_results) + 99) // 100)


def estimate_query_credits_by_protocol(
    protocols: list[str], max_results: int
) -> dict[str, int]:
    """Return per-protocol credit estimates using shared cap math."""
    credits = estimate_query_credits_for_cap(max_results)
    return {protocol: credits for protocol in protocols}


def _build_scan_config_overrides(request: ScanRequest) -> dict:
    max_results = int(request.max_shodan_results)
    budget = _query_credit_budget(max_results)
    return {
        "shodan": {
            "query_limits": {
                "max_results": max_results,
                "min_usable_hosts_target": max_results + 1,
                "smb_max_query_credits_per_scan": budget,
                "max_query_credits_per_scan": budget,
                "ftp_max_query_credits_per_scan": budget,
                "http_max_query_credits_per_scan": budget,
            }
        },
        "ftp": {"shodan": {"query_limits": {"max_results": max_results}}},
        "http": {"shodan": {"query_limits": {"max_results": max_results}}},
    }


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_base_scan_config(config_path: Path) -> dict:
    from shared.config import load_config as _load_core_config

    cfg = _load_core_config(str(config_path))
    return dict(cfg.config)


def _create_task_config_file(request: ScanRequest, base_config_path: Path) -> Path:
    base = _load_base_scan_config(base_config_path)
    merged = _deep_merge_dict(base, _build_scan_config_overrides(request))
    fd, temp_path = tempfile.mkstemp(
        suffix=".json",
        prefix=f"webui_scan_{request.protocol}_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    return Path(temp_path)


def _cleanup_task_config_file(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def build_command(request: ScanRequest, *, config_path: Optional[Path] = None) -> list[str]:
    cmd = [
        sys.executable,
        str(_PROTOCOL_SCRIPTS[request.protocol]),
        "--verbose",
        "--config",
        str(config_path or _CONFIG_PATH),
    ]
    if request.countries:
        cmd += ["--country", ",".join(request.countries)]
    if request.filters:
        cmd += ["--filter", request.filters]
    if request.protocol == "smb":
        cmd.append("--legacy")
    return cmd


def _build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{_REPO_ROOT}{os.pathsep}{existing}".rstrip(os.pathsep)
    return env


def _terminate_proc(proc: subprocess.Popen) -> None:
    if sys.platform.startswith("win"):
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (ProcessLookupError, OSError):
            proc.terminate()
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        proc.terminate()


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _parse_progress(task: ScanTask, line: str) -> None:
    clean_line = _strip_ansi(line)
    with task._lock:
        task.stdout_lines.append(clean_line)
        match = _PROGRESS_RE.search(clean_line)
        if not match:
            return
        pct = int(match.group(1))
        if 0 <= pct <= 100:
            task.progress_pct = float(pct)
            task.progress_message = clean_line


class ScanQueue:
    def __init__(self, *, base_config_path: Optional[Path] = None) -> None:
        self._tasks: dict[str, ScanTask] = {}
        self._active: Optional[ScanTask] = None
        self._queue: deque[ScanTask] = deque()
        self._lock = threading.Lock()
        if base_config_path is None:
            self._base_config_path = _CONFIG_PATH
        else:
            self._base_config_path = Path(base_config_path).expanduser().resolve(strict=False)

    def submit(self, request: ScanRequest) -> ScanTask:
        task = ScanTask(task_id=secrets.token_hex(8), request=request)
        with self._lock:
            self._tasks[task.task_id] = task
            if self._active is None:
                self._start(task)
            else:
                self._queue.append(task)
        return task

    def cancel(self, task_id: str) -> CancelResult:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return CancelResult.NOT_FOUND
            if task.status in {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED}:
                return CancelResult.TERMINAL
            if task.status == TaskStatus.QUEUED:
                self._queue.remove(task)
                with task._lock:
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = time.time()
                return CancelResult.OK
            if task.status == TaskStatus.RUNNING:
                with task._lock:
                    task._cancel_requested = True
                    task._cancel_event.set()
                    proc = task._process
                if proc is not None:
                    _terminate_proc(proc)
                    threading.Thread(
                        target=self._kill_after, args=(proc, 10), daemon=True
                    ).start()
                return CancelResult.OK
        return CancelResult.NOT_FOUND

    def get_task(self, task_id: str) -> Optional[ScanTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def queue_status(self) -> dict:
        with self._lock:
            return {
                "active": self._active.to_dict() if self._active else None,
                "queued": [task.to_dict() for task in self._queue],
            }

    def _start(self, task: ScanTask) -> None:
        with task._lock:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
        self._active = task
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def _advance(self) -> None:
        if self._active is None and self._queue:
            self._start(self._queue.popleft())

    def _finish_and_advance(self, task: ScanTask) -> None:
        with self._lock:
            if self._active is task:
                self._active = None
            self._advance()

    def _run(self, task: ScanTask) -> None:
        task_config_path: Optional[Path] = None
        base_config_path = self._base_config_path
        try:
            with task._lock:
                if task._cancel_requested:
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = time.time()
                    cancelled_before_start = True
                else:
                    cancelled_before_start = False
            if cancelled_before_start:
                return

            resolved_db_path = "unknown"
            try:
                from shared.config import load_config as _load_core_config

                resolved_db_path = _load_core_config(str(base_config_path)).get_database_path()
            except Exception:
                resolved_db_path = "unknown"
            logger.info(
                "scan runner start: task_id=%s protocol=%s config_path=%s db_path=%s",
                task.task_id,
                task.request.protocol,
                base_config_path,
                resolved_db_path,
            )

            task_config_path = _create_task_config_file(task.request, base_config_path)
            cmd = build_command(task.request, config_path=task_config_path)
            env = _build_subprocess_env()
            if sys.platform.startswith("win"):
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_REPO_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=0,
                    shell=False,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_REPO_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=0,
                    shell=False,
                    env=env,
                    start_new_session=True,
                )

            with task._lock:
                task._process = proc
                cancelled_after_start = task._cancel_requested
            if cancelled_after_start:
                _terminate_proc(proc)
                threading.Thread(
                    target=self._kill_after, args=(proc, 10), daemon=True
                ).start()

            if proc.stdout is not None:
                for line in proc.stdout:
                    _parse_progress(task, line.rstrip("\n"))

            proc.wait()
            run_probe_stage = False
            scan_start_iso = None
            scan_end_iso = None
            scan_cancel_event = None
            with task._lock:
                task._process = None
                if task._cancel_requested:
                    task.status = TaskStatus.CANCELLED
                elif proc.returncode != 0:
                    task.status = TaskStatus.FAILED
                elif task.request.run_probe_after_scan:
                    task.progress_message = "scan completed; probing verified hosts..."
                    started_at = task.started_at
                    finished_at = time.time()
                    scan_start_iso = (
                        datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
                        if started_at is not None
                        else None
                    )
                    scan_end_iso = datetime.fromtimestamp(
                        finished_at, tz=timezone.utc
                    ).isoformat()
                    scan_cancel_event = task._cancel_event
                    run_probe_stage = True
                elif proc.returncode == 0:
                    task.status = TaskStatus.DONE
                else:
                    task.status = TaskStatus.FAILED
                if not run_probe_stage:
                    task.finished_at = time.time()

            if run_probe_stage:
                probe_result = run_post_scan_probe(
                    protocol=task.request.protocol,
                    config_path=task_config_path or base_config_path,
                    scan_start_iso=scan_start_iso,
                    scan_end_iso=scan_end_iso,
                    cancel_event=scan_cancel_event,
                )

                with task._lock:
                    if task._cancel_requested or probe_result.cancelled:
                        task.status = TaskStatus.CANCELLED
                        task.progress_message = "cancelled during probe stage"
                    else:
                        task.status = TaskStatus.DONE
                        if probe_result.failed:
                            task.progress_message = (
                                "scan+probe complete with warnings: "
                                f"{probe_result.succeeded}/{probe_result.attempted} hosts probed, "
                                f"{probe_result.failed} failed"
                            )
                            for warning in probe_result.warnings:
                                task.stdout_lines.append(f"[probe warning] {warning}")
                        elif probe_result.attempted:
                            task.progress_message = (
                                "scan+probe complete: "
                                f"{probe_result.succeeded}/{probe_result.attempted} hosts probed"
                            )
                        else:
                            task.progress_message = (
                                "scan completed; no verified hosts required probing"
                            )
                    task.finished_at = time.time()
        except Exception:
            with task._lock:
                if task.status != TaskStatus.CANCELLED:
                    task.status = TaskStatus.FAILED
                task.progress_message = "scan runner error"
                task.finished_at = time.time()
        finally:
            _cleanup_task_config_file(task_config_path)
            self._finish_and_advance(task)

    def _kill_after(self, proc: subprocess.Popen, timeout_s: int) -> None:
        try:
            proc.wait(timeout=timeout_s)
            return
        except subprocess.TimeoutExpired:
            pass

        if sys.platform.startswith("win"):
            proc.terminate()
            return

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
