"""Headless Web UI lifecycle control shared by the CLI and desktop GUI."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import enum
import ipaddress
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from shared.path_service import get_paths


_PATHS = get_paths()
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = str(_REPO_ROOT / "experimental" / "webui" / "server.py")
_SERVER_MODULE = "experimental.webui.server"
_PID_FILE = _PATHS.state_dir / "webui.pid"
_START_LOCK_FILE = _PATHS.state_dir / "webui.start.lock"
_LOG_FILE = _PATHS.app_logs_dir / "webui.log"
_START_TIMEOUT_SECONDS = 10.0
_STOP_TIMEOUT_SECONDS = 15.0
_START_POLL_INTERVAL_SECONDS = 0.1
_REASON_MAX_CHARS = 500
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 2600


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    state: str
    reason: str = ""
    backend: str = "direct"
    pid: Optional[int] = None
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


# Compatibility name retained for desktop callers and older tests.
StartResult = ActionResult


@dataclass(frozen=True)
class ServiceStatus:
    state: str
    backend: str
    healthy: bool
    host: str
    port: int
    pid: Optional[int] = None
    reason: str = ""
    managed: bool = False
    tls: bool = False

    @property
    def running(self) -> bool:
        return self.state == "running" and self.healthy

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "backend": self.backend,
            "healthy": self.healthy,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "reason": self.reason,
            "managed": self.managed,
            "listen_url": get_listen_url(self.host, self.port, tls=self.tls),
            "local_url": get_url(self.host, self.port, tls=self.tls),
        }


class _Ownership(enum.Enum):
    OURS = "ours"
    ALIEN = "alien"
    UNKNOWN = "unknown"


def _format_reason(reason: object) -> str:
    compact = " ".join(str(reason).strip().split())
    if len(compact) > _REASON_MAX_CHARS:
        return compact[: _REASON_MAX_CHARS - 3] + "..."
    return compact


def _is_our_cmdline(tokens: list[str]) -> bool:
    if _SERVER_PATH in tokens:
        return True
    for i in range(len(tokens) - 1):
        if tokens[i] == "-m" and tokens[i + 1] == _SERVER_MODULE:
            return True
    return "dirracuda-d" in " ".join(tokens) and "run" in tokens


def _read_pid_record() -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(_PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_pid() -> Optional[int]:
    record = _read_pid_record()
    if record is None:
        return None
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _write_pid(pid: int, host: str, port: int) -> None:
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "pid": pid,
            "host": host,
            "port": port,
            "repo_root": str(_REPO_ROOT),
        },
        sort_keys=True,
    ) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{_PID_FILE.name}.",
        dir=str(_PID_FILE.parent),
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, _PID_FILE)
        os.chmod(_PID_FILE, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _clear_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
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
        tokens = [item.decode(errors="replace") for item in raw.split(b"\0") if item]
        return _Ownership.OURS if _is_our_cmdline(tokens) else _Ownership.ALIEN
    except (FileNotFoundError, OSError):
        return _Ownership.UNKNOWN


def _local_host_for_bind(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if not address.is_unspecified:
        return host
    return "127.0.0.1" if address.version == 4 else "::1"


def _format_url(host: str, port: int, *, tls: bool = False) -> str:
    try:
        is_ipv6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_ipv6 = ":" in host
    url_host = f"[{host}]" if is_ipv6 else host
    scheme = "https" if tls else "http"
    return f"{scheme}://{url_host}:{port}"


def get_url(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    *,
    tls: bool = False,
) -> str:
    """Return a locally usable browser URL for a configured listener."""
    return _format_url(_local_host_for_bind(host), port, tls=tls)


def get_listen_url(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    *,
    tls: bool = False,
) -> str:
    """Return the configured listener endpoint, including wildcard binds."""
    return _format_url(host, port, tls=tls)


def _health_ok(host: str, port: int, *, tls: bool = False) -> bool:
    try:
        import ssl
        import urllib.request

        url = f"{get_url(host, port, tls=tls)}/health"
        if tls:
            response_context = ssl._create_unverified_context()
            response = urllib.request.urlopen(
                url,
                timeout=2,
                context=response_context,
            )
        else:
            response = urllib.request.urlopen(url, timeout=2)
        with response:
            return response.status == 200
    except Exception:
        return False


def _tls_enabled() -> bool:
    try:
        from experimental.webui.config import load_config

        cfg = load_config()
        return bool(cfg.tls.enabled and cfg.tls.cert_file and cfg.tls.key_file)
    except Exception:
        return False


def _startup_preflight() -> tuple[Optional[Any], str]:
    try:
        from experimental.webui.auth import credential_exists
        from experimental.webui.config import load_config
        from experimental.webui.server import runtime_config_error

        cfg = load_config()
        error = runtime_config_error(cfg, cfg.bind_address)
        if error:
            return None, error
        if not credential_exists():
            return None, (
                "no usable Web UI credential exists; run "
                "./dirracuda-d credentials set USERNAME"
            )
        return cfg, ""
    except Exception as exc:
        return None, _format_reason(exc)


def _log_tail(lines: int = 8) -> str:
    try:
        content = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return _format_reason(" | ".join(content[-lines:]))


def get_log_path() -> Path:
    return _LOG_FILE


@contextmanager
def _start_lock() -> Iterator[None]:
    _START_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = _START_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def direct_status(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ServiceStatus:
    tls = _tls_enabled()
    pid = _read_pid()
    healthy = _health_ok(host, port, tls=tls)
    if pid is None:
        if healthy:
            return ServiceStatus(
                "unmanaged",
                "direct",
                True,
                host,
                port,
                reason="health endpoint is active but no managed PID record exists",
                tls=tls,
            )
        return ServiceStatus("stopped", "direct", False, host, port, tls=tls)

    if not _pid_alive(pid):
        _clear_pid()
        return ServiceStatus(
            "stale",
            "direct",
            False,
            host,
            port,
            pid=pid,
            reason="PID record referenced a process that is no longer running",
            tls=tls,
        )

    ownership = _check_ownership(pid)
    if ownership is _Ownership.ALIEN:
        return ServiceStatus(
            "ambiguous",
            "direct",
            healthy,
            host,
            port,
            pid=pid,
            reason="PID belongs to a different process; record was not removed",
            tls=tls,
        )
    if ownership is _Ownership.UNKNOWN:
        return ServiceStatus(
            "ambiguous",
            "direct",
            healthy,
            host,
            port,
            pid=pid,
            reason="process ownership could not be verified",
            tls=tls,
        )
    if healthy:
        return ServiceStatus(
            "running", "direct", True, host, port, pid=pid, managed=True, tls=tls
        )
    return ServiceStatus(
        "unhealthy",
        "direct",
        False,
        host,
        port,
        pid=pid,
        reason="managed process is alive but its health endpoint is unavailable",
        managed=True,
        tls=tls,
    )


def _wait_for_health(host: str, port: int, *, tls: bool) -> bool:
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _health_ok(host, port, tls=tls):
            return True
        time.sleep(_START_POLL_INTERVAL_SECONDS)
    return False


def start_direct(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ActionResult:
    """Start a detached, directly managed Web UI process."""
    with _start_lock():
        current = direct_status(host, port)
        if current.running:
            return ActionResult(
                True, "already_running", backend="direct", pid=current.pid
            )
        if current.state in {"unmanaged", "ambiguous", "unhealthy"}:
            return ActionResult(
                False,
                current.state,
                current.reason,
                backend="direct",
                pid=current.pid,
            )

        cfg, reason = _startup_preflight()
        if cfg is None:
            return ActionResult(False, "failed", reason, backend="direct")
        # Direct callers such as the desktop config dialog may pass an
        # explicitly selected endpoint. The daemon CLI itself passes canonical
        # config values, while the server performs the final override checks.
        tls = bool(cfg.tls.enabled and cfg.tls.cert_file and cfg.tls.key_file)
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log_handle = _LOG_FILE.open("a", encoding="utf-8")
        os.chmod(_LOG_FILE, 0o600)
        cmd = [
            sys.executable,
            "-m",
            _SERVER_MODULE,
            "--host",
            host,
            "--port",
            str(port),
            "--log-file",
            str(_LOG_FILE),
        ]
        kwargs: dict[str, Any] = {
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "cwd": str(_REPO_ROOT),
            "text": True,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            log_handle.close()
            return ActionResult(
                False, "failed", f"launch error: {exc}", backend="direct"
            )
        finally:
            log_handle.close()

        try:
            _write_pid(process.pid, host, port)
        except Exception as exc:
            try:
                process.terminate()
            except Exception:
                pass
            return ActionResult(
                False,
                "failed",
                f"could not write PID record: {exc}",
                backend="direct",
                pid=process.pid,
            )

        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _health_ok(host, port, tls=tls):
                return ActionResult(
                    True, "running", backend="direct", pid=process.pid
                )
            return_code = process.poll()
            if return_code is not None:
                _clear_pid()
                detail = _log_tail()
                reason = f"process exited with code {return_code}"
                if detail:
                    reason = f"{reason}: {detail}"
                return ActionResult(
                    False, "failed", reason, backend="direct", pid=process.pid
                )
            time.sleep(_START_POLL_INTERVAL_SECONDS)

        _signal_process_group(process.pid, signal.SIGTERM)
        _clear_pid()
        return ActionResult(
            False,
            "failed",
            "startup timeout waiting for health check",
            backend="direct",
            pid=process.pid,
        )


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    if os.name == "nt":
        os.kill(pid, sig)
        return
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def stop_direct(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ActionResult:
    current = direct_status(host, port)
    if current.state in {"stopped", "stale"}:
        return ActionResult(True, "already_stopped", backend="direct")
    if current.state in {"unmanaged", "ambiguous"} or current.pid is None:
        return ActionResult(
            False,
            current.state,
            current.reason or "service ownership could not be confirmed",
            backend="direct",
            pid=current.pid,
        )

    _signal_process_group(current.pid, signal.SIGTERM)
    deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _pid_alive(current.pid):
            _clear_pid()
            return ActionResult(True, "stopped", backend="direct", pid=current.pid)
        time.sleep(_START_POLL_INTERVAL_SECONDS)

    _signal_process_group(current.pid, signal.SIGKILL)
    kill_deadline = time.monotonic() + 2.0
    while time.monotonic() < kill_deadline:
        if not _pid_alive(current.pid):
            _clear_pid()
            return ActionResult(
                True,
                "stopped",
                "process required forced termination",
                backend="direct",
                pid=current.pid,
                details={"forced": True},
            )
        time.sleep(_START_POLL_INTERVAL_SECONDS)
    return ActionResult(
        False,
        "failed",
        "process did not exit after SIGTERM and SIGKILL",
        backend="direct",
        pid=current.pid,
    )


def _systemd_installed() -> bool:
    try:
        from experimental.webui.systemd_control import unit_installed

        return unit_installed()
    except Exception:
        return False


def get_status(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ServiceStatus:
    if _systemd_installed():
        from experimental.webui.systemd_control import service_status

        return service_status(host, port)
    return direct_status(host, port)


def is_running(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> bool:
    """Compatibility predicate used by existing desktop controls."""
    return get_status(host, port).running


def start(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ActionResult:
    if _systemd_installed():
        from experimental.webui.systemd_control import start_service

        return start_service(host, port)
    return start_direct(host, port)


def stop(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> ActionResult:
    if _systemd_installed():
        from experimental.webui.systemd_control import stop_service

        return stop_service(host, port)
    return stop_direct(host, port)


def restart(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    *,
    previous_host: Optional[str] = None,
    previous_port: Optional[int] = None,
) -> ActionResult:
    if _systemd_installed():
        from experimental.webui.systemd_control import restart_service

        return restart_service(host, port)
    probe_host = previous_host or host
    probe_port = previous_port or port
    # Route through the public facade so desktop tests and callers can
    # substitute lifecycle operations without reaching into backend internals.
    should_stop = is_running(probe_host, probe_port)
    if not should_stop:
        record = _read_pid_record() or {}
        try:
            record_matches = (
                str(record.get("host")) == probe_host
                and int(record.get("port")) == probe_port
            )
        except (TypeError, ValueError):
            record_matches = False
        if record_matches:
            previous = direct_status(probe_host, probe_port)
            should_stop = previous.managed and previous.state == "unhealthy"
    if should_stop:
        stopped = stop()
        if not stopped:
            reason = getattr(stopped, "reason", "") or (
                "stop did not complete; service may still be running"
            )
            state = getattr(stopped, "state", "failed")
            return ActionResult(False, state, reason, backend="direct")
    return start(host, port)
