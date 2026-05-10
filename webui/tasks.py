"""Scan task queue and subprocess runner for the Web UI.

C4 intentionally delegates scans to the existing CLI entrypoints. The queue is
in-memory, single-process, and one-active-scan-at-a-time for v1.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import enum
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parent.parent
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

    @model_validator(mode="after")
    def _probe_protocol_check(self) -> "ScanRequest":
        if self.run_probe_after_scan and self.protocol != "smb":
            raise ValueError(
                "run_probe_after_scan is only supported for protocol 'smb'; "
                f"got {self.protocol!r}"
            )
        return self


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


def build_command(request: ScanRequest) -> list[str]:
    cmd = [
        sys.executable,
        str(_PROTOCOL_SCRIPTS[request.protocol]),
        "--verbose",
        "--config",
        str(_CONFIG_PATH),
    ]
    if request.countries:
        cmd += ["--country", ",".join(request.countries)]
    if request.filters:
        cmd += ["--filter", request.filters]
    if request.run_probe_after_scan:
        cmd.append("--check-rce")
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


def _parse_progress(task: ScanTask, line: str) -> None:
    with task._lock:
        task.stdout_lines.append(line)
        match = _PROGRESS_RE.search(line)
        if not match:
            return
        pct = int(match.group(1))
        if 0 <= pct <= 100:
            task.progress_pct = float(pct)
            task.progress_message = line


class ScanQueue:
    def __init__(self) -> None:
        self._tasks: dict[str, ScanTask] = {}
        self._active: Optional[ScanTask] = None
        self._queue: deque[ScanTask] = deque()
        self._lock = threading.Lock()

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

            cmd = build_command(task.request)
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
            with task._lock:
                if task._cancel_requested:
                    task.status = TaskStatus.CANCELLED
                elif proc.returncode == 0:
                    task.status = TaskStatus.DONE
                else:
                    task.status = TaskStatus.FAILED
                task.finished_at = time.time()
        except Exception:
            with task._lock:
                if task.status != TaskStatus.CANCELLED:
                    task.status = TaskStatus.FAILED
                task.progress_message = "scan runner error"
                task.finished_at = time.time()
        finally:
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
