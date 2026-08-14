"""Strict bubblewrap parser supervisor with bounded IPC and cgroup tasks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Final

from .inventory import InventoryFile

BWRAP: Final = Path("/usr/bin/bwrap")
PRLIMIT: Final = Path("/usr/bin/prlimit")
SYSTEMD_RUN: Final = Path("/usr/bin/systemd-run")
SYSTEMCTL: Final = Path("/usr/bin/systemctl")
SYSTEM_PYTHON: Final = Path("/usr/bin/python3")
REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CGROUP_CONTROLLERS: Final = Path("/sys/fs/cgroup/cgroup.controllers")
SANDBOX_INPUT: Final = "/input/document"
READ_SIZE: Final = 64 * 1024
UNIT_TOKEN_RE: Final = re.compile(r"[0-9a-f]{16}\Z", re.ASCII)

CancelCheck = Callable[[], bool]


class SandboxInputMode(str, Enum):
    NAMED_BIND = "named_bind"
    SEALED_DATA = "sealed_data"


@dataclass(frozen=True, slots=True)
class RuntimeBind:
    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    address_space_bytes: int = 1 << 30
    cpu_seconds: int = 20
    open_files: int = 64
    tasks: int = 16
    wall_seconds: float = 45.0
    stdout_bytes: int = 8 * 1024 * 1024
    stderr_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        integer_fields = (
            self.address_space_bytes, self.cpu_seconds, self.open_files,
            self.tasks, self.stdout_bytes, self.stderr_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in integer_fields):
            raise ValueError("sandbox integer limits must be positive")
        if (not isinstance(self.wall_seconds, (int, float))
                or isinstance(self.wall_seconds, bool)
                or not math.isfinite(self.wall_seconds)
                or self.wall_seconds <= 0):
            raise ValueError("wall_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    reason: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    unit_name: str | None

    @property
    def ok(self) -> bool:
        return self.reason == "success"


@dataclass(frozen=True, slots=True)
class SandboxCapability:
    ok: bool
    reason: str
    checks: tuple[tuple[str, bool], ...]


class _SourceCheckCancelled(Exception):
    """Internal control flow for cancellation during source hashing."""


def system_runtime_binds() -> tuple[RuntimeBind, ...]:
    """Runtime used only for generic C3 probes, not a parser dependency set."""
    paths = [Path("/usr"), Path("/lib"), Path("/lib64")]
    cache = Path("/etc/ld.so.cache")
    if cache.exists():
        paths.append(cache)
    return tuple(RuntimeBind(path, path) for path in paths if path.exists())


def build_argv(
    *,
    source_fd: int,
    command: tuple[str, ...],
    runtime_binds: tuple[RuntimeBind, ...],
    limits: SandboxLimits,
    unit_token: str,
    input_mode: SandboxInputMode = SandboxInputMode.NAMED_BIND,
) -> tuple[list[str], str]:
    """Build the exact systemd -> bwrap -> prlimit -> parser command."""
    if not isinstance(limits, SandboxLimits):
        raise ValueError("sandbox limits have an invalid type")
    _validate_request(source_fd, command, runtime_binds, unit_token, input_mode)
    unit = f"dirracuda-analyst-parser-{unit_token}.scope"
    argv = [
        str(SYSTEMD_RUN), "--user", "--scope", "--collect", "--quiet",
        f"--unit={unit}", f"--property=TasksMax={limits.tasks}",
        "--property=KillMode=control-group", "--property=SendSIGKILL=yes", "--",
        str(BWRAP),
        "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--cap-drop", "ALL", "--die-with-parent", "--new-session",
        "--clearenv",
        "--setenv", "HOME", "/sandbox-home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONHASHSEED", "0",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/sandbox-home",
        "--dir", "/input",
        "--ro-bind-fd" if input_mode is SandboxInputMode.NAMED_BIND
        else "--ro-bind-data",
        str(source_fd), SANDBOX_INPUT,
    ]
    for binding in runtime_binds:
        argv.extend(("--ro-bind", str(binding.source), str(binding.destination)))
    argv.extend((
        "--chdir", "/tmp",
        str(PRLIMIT),
        f"--as={limits.address_space_bytes}",
        f"--cpu={limits.cpu_seconds}",
        f"--nofile={limits.open_files}",
        "--core=0", "--", *command,
    ))
    return argv, unit


def run_sandboxed(
    *,
    source_fd: int,
    expected: InventoryFile,
    command: tuple[str, ...],
    runtime_binds: tuple[RuntimeBind, ...],
    limits: SandboxLimits | None = None,
    cancel_check: CancelCheck | None = None,
    unit_token: str | None = None,
    input_mode: SandboxInputMode = SandboxInputMode.NAMED_BIND,
) -> SandboxResult:
    """Run one parser command and return only bounded in-memory output."""
    chosen_limits = limits or SandboxLimits()
    token = unit_token or secrets.token_hex(8)
    if not isinstance(expected, InventoryFile):
        return SandboxResult("sandbox_error", None, b"", b"", None)
    if not all(value for _, value in _prerequisite_checks(False)):
        return SandboxResult("sandbox_unavailable", None, b"", b"", None)
    handoff_fd = source_fd
    close_handoff = False
    try:
        if input_mode is SandboxInputMode.SEALED_DATA:
            handoff_fd = _open_sealed_data_handoff(source_fd)
            close_handoff = True
        argv, unit = build_argv(
            source_fd=handoff_fd,
            command=command,
            runtime_binds=runtime_binds,
            limits=chosen_limits,
            unit_token=token,
            input_mode=input_mode,
        )
    except (OSError, ValueError):
        if close_handoff:
            os.close(handoff_fd)
        return SandboxResult("sandbox_error", None, b"", b"", None)
    try:
        source_matches = _source_matches(source_fd, expected, cancel_check)
    except _SourceCheckCancelled:
        if close_handoff:
            os.close(handoff_fd)
        return SandboxResult("cancelled", None, b"", b"", unit)
    if not source_matches:
        if close_handoff:
            os.close(handoff_fd)
        return SandboxResult(
            "source_changed_since_inventory", None, b"", b"", None
        )

    environment = _systemd_environment()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(handoff_fd,),
            close_fds=True,
            shell=False,
            start_new_session=True,
            env=environment,
        )
    except OSError:
        if close_handoff:
            os.close(handoff_fd)
        return SandboxResult("sandbox_unavailable", None, b"", b"", unit)
    if close_handoff:
        os.close(handoff_fd)

    reason, stdout, stderr = _collect_bounded(
        process, unit, chosen_limits, cancel_check, environment
    )
    returncode = process.returncode
    if reason == "complete":
        reason = _return_reason(returncode)
    if reason not in {"cancelled", "parse_timeout", "parser_output_limit"}:
        if not _source_matches(source_fd, expected, None):
            return SandboxResult(
                "source_changed_since_inventory", returncode, b"", b"", unit
            )
    if reason != "success":
        stdout, stderr = b"", b""
    return SandboxResult(reason, returncode, stdout, stderr, unit)


def strict_preflight() -> SandboxCapability:
    """Live synthetic proof for the strict sandbox and cgroup boundary."""
    missing = tuple((name, value) for name, value in _prerequisite_checks(True)
                    if not value)
    if missing:
        return SandboxCapability(False, "sandbox_unavailable", missing)
    source_fd, source_path = tempfile.mkstemp(prefix="dirracuda-c3-public-")
    try:
        os.write(source_fd, b"C3_SYNTHETIC_INPUT")
        expected = _inventory_for_fd(source_fd)
        probe = _preflight_probe()
        result = run_sandboxed(
            source_fd=source_fd,
            expected=expected,
            command=(str(SYSTEM_PYTHON), "-c", probe),
            runtime_binds=system_runtime_binds(),
            limits=SandboxLimits(wall_seconds=20.0, stdout_bytes=64 * 1024),
        )
    finally:
        os.close(source_fd)
        Path(source_path).unlink(missing_ok=True)
    if not result.ok:
        return SandboxCapability(
            False, "sandbox_unavailable", (("probe_completed", False),)
        )
    try:
        payload = json.loads(result.stdout)
    except (UnicodeError, ValueError):
        return SandboxCapability(False, "sandbox_error", (("probe_json", False),))
    checks = (
        ("input_fd_bound", payload.get("input") == "C3_SYNTHETIC_INPUT"),
        ("network_unreachable", payload.get("network") != "reachable"),
        ("host_home_absent", payload.get("home_visible") is False),
        ("repository_absent", payload.get("repo_visible") is False),
        ("task_limit_enforced", type(payload.get("forked")) is int
         and 0 < payload["forked"] < SandboxLimits().tasks),
    )
    return SandboxCapability(all(value for _, value in checks),
                             "success" if all(v for _, v in checks)
                             else "sandbox_error", checks)


def _collect_bounded(
    process: subprocess.Popen,
    unit: str,
    limits: SandboxLimits,
    cancel_check: CancelCheck | None,
    environment: dict[str, str],
) -> tuple[str, bytes, bytes]:
    assert process.stdout is not None and process.stderr is not None
    streams = {
        process.stdout.fileno(): ("stdout", limits.stdout_bytes),
        process.stderr.fileno(): ("stderr", limits.stderr_bytes),
    }
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for fd in streams:
        os.set_blocking(fd, False)
        selector.register(fd, selectors.EVENT_READ)
    deadline = time.monotonic() + limits.wall_seconds
    reason = "complete"
    killed = False
    try:
        while selector.get_map():
            if _cancel_requested(cancel_check):
                reason = "cancelled"
                if not killed:
                    _kill_unit(process, unit, environment)
                    killed = True
            elif reason == "complete" and time.monotonic() >= deadline:
                reason = "parse_timeout"
                if not killed:
                    _kill_unit(process, unit, environment)
                    killed = True
            for key, _mask in selector.select(0.05):
                fd = key.fd
                name, cap = streams[fd]
                try:
                    chunk = os.read(fd, READ_SIZE)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(fd)
                    continue
                remaining = cap - len(buffers[name])
                if remaining > 0:
                    buffers[name].extend(chunk[:remaining])
                if len(chunk) > remaining and reason == "complete":
                    reason = "parser_output_limit"
                    _kill_unit(process, unit, environment)
                    killed = True
            if reason != "complete" and process.poll() is not None:
                continue
            if process.poll() is not None and not selector.get_map():
                break
    finally:
        selector.close()
        if process.poll() is None:
            _kill_unit(process, unit, environment)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return reason, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _kill_unit(process: subprocess.Popen, unit: str,
               environment: dict[str, str]) -> None:
    try:
        subprocess.run(
            [str(SYSTEMCTL), "--user", "kill", "--kill-whom=all",
             "--signal=SIGKILL", unit],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _source_matches(source_fd: int, expected: InventoryFile,
                    cancel_check: CancelCheck | None) -> bool:
    try:
        if _cancel_requested(cancel_check):
            raise _SourceCheckCancelled
        observed = os.fstat(source_fd)
        if not stat.S_ISREG(observed.st_mode):
            return False
        if (
            observed.st_dev != expected.device
            or observed.st_ino != expected.inode
            or observed.st_size != expected.size
            or observed.st_mtime_ns != expected.mtime_ns
            or observed.st_ctime_ns != expected.ctime_ns
            or stat.S_IMODE(observed.st_mode) != expected.mode
        ):
            return False
        digest = hashlib.sha256()
        offset = 0
        while offset < observed.st_size:
            if _cancel_requested(cancel_check):
                raise _SourceCheckCancelled
            chunk = os.pread(source_fd, min(READ_SIZE, observed.st_size - offset), offset)
            if not chunk:
                return False
            digest.update(chunk)
            offset += len(chunk)
        if _cancel_requested(cancel_check):
            raise _SourceCheckCancelled
        return (digest.hexdigest() == expected.sha256
                and _stable_stat(observed, os.fstat(source_fd)))
    except OSError:
        return False


def _inventory_for_fd(fd: int) -> InventoryFile:
    observed = os.fstat(fd)
    digest = hashlib.sha256()
    offset = 0
    while offset < observed.st_size:
        chunk = os.pread(fd, READ_SIZE, offset)
        if not chunk:
            raise OSError("short synthetic source read")
        digest.update(chunk)
        offset += len(chunk)
    return InventoryFile(
        relative_path="synthetic",
        size=observed.st_size,
        mtime_ns=observed.st_mtime_ns,
        ctime_ns=observed.st_ctime_ns,
        device=observed.st_dev,
        inode=observed.st_ino,
        mode=stat.S_IMODE(observed.st_mode),
        sha256=digest.hexdigest(),
    )


def _stable_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _return_reason(returncode: int | None) -> str:
    if returncode == 0:
        return "success"
    if returncode is not None and (returncode < 0 or 129 <= returncode <= 192):
        return "parse_signal"
    return "parse_error"


def _validate_request(source_fd: int, command: tuple[str, ...],
                      runtime_binds: tuple[RuntimeBind, ...],
                      unit_token: str, input_mode: SandboxInputMode) -> None:
    if type(source_fd) is not int or source_fd < 0:
        raise ValueError("source fd must be nonnegative")
    os.fstat(source_fd)
    if type(input_mode) is not SandboxInputMode:
        raise ValueError("sandbox input mode is invalid")
    if input_mode is SandboxInputMode.SEALED_DATA and not _has_required_seals(source_fd):
        raise ValueError("anonymous input is not fully sealed")
    if type(command) is not tuple or not command or not all(
            type(item) is str and item for item in command):
        raise ValueError("sandbox command must be nonempty strings")
    if not Path(command[0]).is_absolute():
        raise ValueError("sandbox executable must be absolute")
    executable = Path(command[0])
    private_roots = (Path("/input"), Path("/tmp"), Path("/sandbox-home"),
                     Path("/proc"), Path("/dev"))
    if any(executable == root or root in executable.parents
           for root in private_roots):
        raise ValueError("sandbox executable overlaps a private mount")
    if type(runtime_binds) is not tuple:
        raise ValueError("runtime binds must be a tuple")
    if type(unit_token) is not str or not UNIT_TOKEN_RE.fullmatch(unit_token):
        raise ValueError("unit token is invalid")
    seen_destinations: set[str] = set()
    reserved = (Path("/input"), Path("/tmp"), Path("/sandbox-home"),
                Path("/proc"), Path("/dev"), Path("/run/user"))
    source_path = _descriptor_path(source_fd)
    for binding in runtime_binds:
        if not isinstance(binding, RuntimeBind):
            raise ValueError("runtime bind has an invalid type")
        source, destination = binding.source, binding.destination
        if (not isinstance(source, Path) or not isinstance(destination, Path)
                or not source.is_absolute() or not destination.is_absolute()
                or ".." in source.parts or ".." in destination.parts
                or destination == Path("/") or not source.exists()):
            raise ValueError("runtime bind is unsafe")
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as exc:
            raise ValueError("runtime bind is unsafe") from exc
        protected = (Path.home().resolve(), REPO_ROOT.resolve())
        if (resolved_source == Path("/")
                or any(resolved_source == path
                       or resolved_source in path.parents for path in protected)
                or (source_path is not None
                    and (resolved_source == source_path
                         or resolved_source in source_path.parents))):
            raise ValueError("runtime bind exposes a protected tree")
        if any(destination == root or root in destination.parents
               for root in reserved):
            raise ValueError("runtime destination overlaps a private mount")
        rendered = str(destination)
        if rendered in seen_destinations:
            raise ValueError("runtime bind destination is duplicated")
        seen_destinations.add(rendered)


def _trusted_tool(path: Path) -> bool:
    try:
        link = path.lstat()
        resolved = path.resolve(strict=True)
        observed = resolved.stat()
    except OSError:
        return False
    link_is_safe = (link.st_uid == 0
                    and (stat.S_ISLNK(link.st_mode)
                         or not link.st_mode & 0o022))
    return (link_is_safe and stat.S_ISREG(observed.st_mode)
            and os.access(resolved, os.X_OK)
            and observed.st_uid == 0 and not observed.st_mode & 0o022)


def _prerequisite_checks(include_probe_python: bool) -> tuple[tuple[str, bool], ...]:
    tools = [BWRAP, PRLIMIT, SYSTEMD_RUN, SYSTEMCTL]
    if include_probe_python:
        tools.append(SYSTEM_PYTHON)
    checks = [(f"tool:{path.name}", _trusted_tool(path)) for path in tools]
    try:
        controllers = CGROUP_CONTROLLERS.read_text(encoding="ascii").split()
    except OSError:
        controllers = []
    checks.append(("cgroup:pids", "pids" in controllers))
    return tuple(checks)


def _descriptor_path(fd: int) -> Path | None:
    try:
        rendered = os.readlink(f"/proc/self/fd/{fd}")
        if not rendered.startswith("/") or rendered.endswith(" (deleted)"):
            return None
        return Path(rendered).resolve(strict=True)
    except OSError:
        return None


def _cancel_requested(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return cancel_check() is not False
    except Exception:
        return True


def _open_sealed_data_handoff(source_fd: int) -> int:
    if not _has_required_seals(source_fd):
        raise ValueError("anonymous input is not fully sealed")
    cloned = os.open(
        f"/proc/self/fd/{source_fd}", os.O_RDONLY | os.O_CLOEXEC
    )
    try:
        left, right = os.fstat(source_fd), os.fstat(cloned)
        if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
            raise ValueError("anonymous input identity changed")
        os.lseek(cloned, 0, os.SEEK_SET)
        return cloned
    except Exception:
        os.close(cloned)
        raise


def _has_required_seals(fd: int) -> bool:
    required = (
        fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    )
    try:
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    except OSError:
        return False
    return seals & required == required


def _systemd_environment() -> dict[str, str]:
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "SYSTEMD_COLORS": "0",
        "SYSTEMD_PAGER": "cat",
    }
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _preflight_probe() -> str:
    home = str(Path.home())
    repo = str(REPO_ROOT)
    return f'''import json, os, signal, socket, time
result = {{}}
result["input"] = open({SANDBOX_INPUT!r}, encoding="ascii").read()
try:
    sock = socket.socket()
    sock.settimeout(0.2)
    sock.connect(("127.0.0.1", 11434))
    result["network"] = "reachable"
except OSError:
    result["network"] = "blocked"
result["home_visible"] = os.path.exists({home!r})
result["repo_visible"] = os.path.exists({repo!r})
children = []
for _ in range(32):
    try:
        pid = os.fork()
    except OSError:
        break
    if pid == 0:
        time.sleep(5)
        os._exit(0)
    children.append(pid)
result["forked"] = len(children)
for pid in children:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
for pid in children:
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''
