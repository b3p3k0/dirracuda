"""
Service control helpers for the web UI process.

State is tracked via a pidfile at ~/.dirracuda/state/webui.pid so it survives
desktop app close/reopen. No GUI imports; webui-package imports are lazy.
"""

from __future__ import annotations

import enum
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVER_PATH = str(_REPO_ROOT / "webui" / "server.py")
_PID_FILE = Path.home() / ".dirracuda" / "state" / "webui.pid"


class _Ownership(enum.Enum):
    OURS = "ours"
    ALIEN = "alien"
    UNKNOWN = "unknown"


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
        return _Ownership.OURS if _SERVER_PATH in tokens else _Ownership.ALIEN
    except ImportError:
        pass
    except Exception:
        return _Ownership.UNKNOWN

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        tokens = [t.decode(errors="replace") for t in raw.split(b"\x00") if t]
        return _Ownership.OURS if _SERVER_PATH in tokens else _Ownership.ALIEN
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


def start(host: str = "127.0.0.1", port: int = 5480) -> bool:
    """Start the web UI server. Returns False if already running."""
    if is_running(host, port):
        return False
    cmd = [sys.executable, _SERVER_PATH, "--host", host, "--port", str(port)]
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(_REPO_ROOT),
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    _write_pid(proc.pid, host, port)
    return True


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
