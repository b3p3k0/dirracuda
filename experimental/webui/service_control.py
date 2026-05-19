"""
Service control helpers for the web UI process.

State is tracked via a pidfile at ~/.dirracuda/state/webui.pid so it survives
desktop app close/reopen. No GUI imports; webui-package imports are lazy.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = str(_REPO_ROOT / "experimental" / "webui" / "server.py")
_SERVER_MODULE = "experimental.webui.server"
_PID_FILE = Path.home() / ".dirracuda" / "state" / "webui.pid"
_START_TIMEOUT_SECONDS = 6.0
_START_POLL_INTERVAL_SECONDS = 0.1
_STDERR_HISTORY_LINES = 8
_REASON_MAX_CHARS = 220


@dataclass(frozen=True)
class StartResult:
    ok: bool
    state: str
    reason: str = ""


class _Ownership(enum.Enum):
    OURS = "ours"
    ALIEN = "alien"
    UNKNOWN = "unknown"


def _format_reason(reason: str) -> str:
    compact = " ".join(str(reason).strip().split())
    if not compact:
        return ""
    if len(compact) > _REASON_MAX_CHARS:
        return compact[: _REASON_MAX_CHARS - 1] + "..."
    return compact


def _is_our_cmdline(tokens: list[str]) -> bool:
    if _SERVER_PATH in tokens:
        return True
    for i in range(len(tokens) - 1):
        if tokens[i] == "-m" and tokens[i + 1] == _SERVER_MODULE:
            return True
    return False


def _read_pid_record() -> Optional[dict]:
    try:
        return json.loads(_PID_FILE.read_text())
    except Exception:
        return None


def _read_pid() -> Optional[int]:
    record = _read_pid_record()
    if record is None:
        return None
    try:
        return int(record["pid"])
    except Exception:
        return None


def _write_pid(pid: int, host: str, port: int) -> None:
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(json.dumps({"pid": pid, "host": host, "port": port}))


def _clear_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


def _check_ownership(pid: int) -> _Ownership:
    try:
        import psutil
        tokens = psutil.Process(pid).cmdline()
        return _Ownership.OURS if _is_our_cmdline(tokens) else _Ownership.ALIEN
    except ImportError:
        pass
    except Exception:
        return _Ownership.UNKNOWN

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        tokens = [t.decode(errors="replace") for t in raw.split(b"\x00") if t]
        return _Ownership.OURS if _is_our_cmdline(tokens) else _Ownership.ALIEN
    except (FileNotFoundError, OSError):
        pass

    return _Ownership.UNKNOWN


def _health_ok(host: str, port: int) -> bool:
    try:
        import urllib.request
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_running(host: str = "127.0.0.1", port: int = 5480) -> bool:
    """Return True if the web UI service appears to be running."""
    pid = _read_pid()
    if pid is None:
        return False
    if not _pid_alive(pid):
        _clear_pid()
        return False
    ownership = _check_ownership(pid)
    if ownership is _Ownership.ALIEN:
        _clear_pid()
        return False
    return _health_ok(host, port)


def get_url(host: str = "127.0.0.1", port: int = 5480) -> str:
    return f"http://{host}:{port}"


def _drain_stderr(stream, sink: list[str]) -> None:
    try:
        for line in stream:
            msg = _format_reason(line)
            if not msg:
                continue
            sink.append(msg)
            if len(sink) > _STDERR_HISTORY_LINES:
                del sink[:-_STDERR_HISTORY_LINES]
    except Exception:
        return


def _stderr_tail(sink: list[str], proc: subprocess.Popen) -> str:
    if sink:
        return _format_reason(" | ".join(sink))
    stream = getattr(proc, "stderr", None)
    if stream is None:
        return ""
    try:
        remaining = stream.read()
    except Exception:
        return ""
    return _format_reason(remaining)


def start(host: str = "127.0.0.1", port: int = 5480) -> StartResult:
    """Start the web UI server and wait for health to become ready."""
    if is_running(host, port):
        return StartResult(ok=True, state="already_running")
    cmd = [sys.executable, "-m", _SERVER_MODULE, "--host", host, "--port", str(port)]
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "cwd": str(_REPO_ROOT),
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        return StartResult(
            ok=False,
            state="failed",
            reason=f"launch error: {exc}",
        )

    stderr_lines: list[str] = []
    if proc.stderr is not None:
        threading.Thread(
            target=_drain_stderr,
            args=(proc.stderr, stderr_lines),
            daemon=True,
        ).start()

    _write_pid(proc.pid, host, port)
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        if _health_ok(host, port):
            return StartResult(ok=True, state="running")

        rc = proc.poll()
        if rc is not None:
            _clear_pid()
            detail = _stderr_tail(stderr_lines, proc)
            reason = f"process exited with code {rc}"
            if detail:
                reason = f"{reason}: {detail}"
            return StartResult(ok=False, state="failed", reason=reason)

        time.sleep(_START_POLL_INTERVAL_SECONDS)

    rc = proc.poll()
    if rc is None:
        try:
            proc.terminate()
        except Exception:
            pass
        _clear_pid()
        return StartResult(
            ok=False,
            state="failed",
            reason="startup timeout waiting for health check",
        )

    _clear_pid()
    detail = _stderr_tail(stderr_lines, proc)
    reason = f"process exited with code {rc}"
    if detail:
        reason = f"{reason}: {detail}"
    return StartResult(ok=False, state="failed", reason=reason)


def stop() -> bool:
    """Stop the web UI server. Returns False when ownership is unconfirmed or process not found."""
    record = _read_pid_record()
    if record is None:
        return False
    try:
        pid = int(record["pid"])
    except Exception:
        return False

    if not _pid_alive(pid):
        _clear_pid()
        return False

    ownership = _check_ownership(pid)
    if ownership is _Ownership.ALIEN:
        _clear_pid()
        return False
    if ownership is _Ownership.UNKNOWN:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return False
    _clear_pid()
    return True
