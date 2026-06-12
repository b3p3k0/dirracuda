"""Per-user systemd backend for the Dirracuda Web UI daemon."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


UNIT_NAME = "dirracuda-d.service"
MANAGED_MARKER = "# Managed by Dirracuda dirracuda-d"


@dataclass(frozen=True)
class SystemdState:
    installed: bool
    managed: bool
    enabled: bool
    active: bool
    pid: Optional[int] = None
    reason: str = ""


def get_unit_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "systemd" / "user" / UNIT_NAME


def _unit_is_managed(path: Optional[Path] = None) -> bool:
    candidate = path or get_unit_path()
    try:
        return MANAGED_MARKER in candidate.read_text(encoding="utf-8")
    except OSError:
        return False


def unit_installed() -> bool:
    return get_unit_path().is_file()


def _quote(value: Path | str) -> str:
    return (
        '"'
        + str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        + '"'
    )


def _unit_path(value: Path | str) -> str:
    """Escape a path for directives that do not accept shell-like quoting."""
    return (
        str(value)
        .replace("\\", "\\x5c")
        .replace(" ", "\\x20")
        .replace("\t", "\\x09")
        .replace("%", "%%")
    )


def render_unit(repo_root: Optional[Path] = None) -> str:
    root = Path(
        repo_root or Path(__file__).resolve().parents[2]
    ).resolve(strict=False)
    launcher = root / "dirracuda-d"
    return f"""\
{MANAGED_MARKER}
[Unit]
Description=Dirracuda headless Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory={_unit_path(root)}
ExecStart={_quote(launcher)} run
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _systemctl_value(property_name: str) -> str:
    result = _run_systemctl(
        "show",
        UNIT_NAME,
        f"--property={property_name}",
        "--value",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def inspect_state() -> SystemdState:
    path = get_unit_path()
    if not path.is_file():
        return SystemdState(False, False, False, False)
    managed = _unit_is_managed(path)
    enabled_result = _run_systemctl("is-enabled", UNIT_NAME)
    active_result = _run_systemctl("is-active", UNIT_NAME)
    pid_text = _systemctl_value("MainPID")
    try:
        pid = int(pid_text)
    except (TypeError, ValueError):
        pid = None
    if pid == 0:
        pid = None
    reason = ""
    if not managed:
        reason = f"unit exists but lacks the Dirracuda marker: {path}"
    return SystemdState(
        True,
        managed,
        enabled_result.returncode == 0 and enabled_result.stdout.strip() == "enabled",
        active_result.returncode == 0 and active_result.stdout.strip() == "active",
        pid,
        reason,
    )


def service_status(host: str, port: int):
    from experimental.webui.service_control import ServiceStatus, _health_ok, _tls_enabled

    state = inspect_state()
    tls = _tls_enabled()
    if not state.managed:
        return ServiceStatus(
            "ambiguous",
            "systemd",
            False,
            host,
            port,
            pid=state.pid,
            reason=state.reason,
            tls=tls,
        )
    healthy = _health_ok(host, port, tls=tls)
    if state.active and healthy:
        name = "running"
    elif state.active:
        name = "unhealthy"
    elif healthy:
        name = "unmanaged"
    else:
        name = "stopped"
    return ServiceStatus(
        name,
        "systemd",
        healthy,
        host,
        port,
        pid=state.pid,
        reason="" if name in {"running", "stopped"} else (
            "systemd unit is active but health is unavailable"
            if name == "unhealthy"
            else "health endpoint is active while the systemd unit is inactive"
        ),
        managed=state.managed,
        tls=tls,
    )


def _action(command: str, host: str, port: int):
    from experimental.webui.service_control import ActionResult, _wait_for_health, _tls_enabled

    state = inspect_state()
    if not state.managed:
        return ActionResult(False, "ambiguous", state.reason, backend="systemd")
    result = _run_systemctl(command, UNIT_NAME)
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"systemctl {command} failed"
        return ActionResult(False, "failed", reason, backend="systemd")
    if command == "stop":
        return ActionResult(True, "stopped", backend="systemd")
    if _wait_for_health(host, port, tls=_tls_enabled()):
        return ActionResult(True, "running", backend="systemd")
    return ActionResult(
        False,
        "unhealthy",
        f"systemctl {command} succeeded but health did not become ready",
        backend="systemd",
    )


def start_service(host: str, port: int):
    from experimental.webui.service_control import ActionResult, _startup_preflight

    cfg, reason = _startup_preflight()
    if cfg is None:
        return ActionResult(False, "failed", reason, backend="systemd")
    current = service_status(cfg.bind_address, cfg.port)
    if current.running:
        return ActionResult(
            True, "already_running", backend="systemd", pid=current.pid
        )
    return _action("start", cfg.bind_address, cfg.port)


def stop_service(host: str, port: int):
    from experimental.webui.service_control import ActionResult

    current = service_status(host, port)
    if current.state == "stopped":
        return ActionResult(True, "already_stopped", backend="systemd")
    return _action("stop", host, port)


def restart_service(host: str, port: int):
    from experimental.webui.service_control import ActionResult, _startup_preflight

    cfg, reason = _startup_preflight()
    if cfg is None:
        return ActionResult(False, "failed", reason, backend="systemd")
    return _action("restart", cfg.bind_address, cfg.port)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
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


def install_unit():
    from experimental.webui.service_control import (
        ActionResult,
        _startup_preflight,
        _tls_enabled,
        _wait_for_health,
        direct_status,
        stop_direct,
    )

    if shutil.which("systemctl") is None:
        return ActionResult(False, "failed", "systemctl is not available", backend="systemd")
    cfg, reason = _startup_preflight()
    if cfg is None:
        return ActionResult(False, "failed", reason, backend="systemd")
    path = get_unit_path()
    if path.exists() and not _unit_is_managed(path):
        return ActionResult(
            False,
            "ambiguous",
            f"refusing to overwrite unmanaged unit: {path}",
            backend="systemd",
        )
    previous_state = inspect_state() if path.exists() else None
    if previous_state is not None and previous_state.active:
        stopped_unit = _run_systemctl("stop", UNIT_NAME)
        if stopped_unit.returncode != 0:
            reason = stopped_unit.stderr.strip() or "could not stop existing user unit"
            return ActionResult(False, "failed", reason, backend="systemd")
    else:
        direct = direct_status(cfg.bind_address, cfg.port)
        if direct.state in {"unmanaged", "ambiguous", "unhealthy"}:
            return ActionResult(False, direct.state, direct.reason, backend="systemd")
        if direct.running:
            stopped = stop_direct(cfg.bind_address, cfg.port)
            if not stopped.ok:
                return ActionResult(
                    False, stopped.state, stopped.reason, backend="systemd"
                )

    previous = path.read_bytes() if path.exists() else None
    try:
        _atomic_write(path, render_unit())
        reload_result = _run_systemctl("daemon-reload")
        if reload_result.returncode != 0:
            raise RuntimeError(reload_result.stderr.strip() or "systemctl daemon-reload failed")
        enable_result = _run_systemctl("enable", "--now", UNIT_NAME)
        if enable_result.returncode != 0:
            raise RuntimeError(enable_result.stderr.strip() or "systemctl enable --now failed")
    except Exception as exc:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, previous.decode("utf-8"))
        _run_systemctl("daemon-reload")
        if previous_state is not None and previous_state.active:
            _run_systemctl("start", UNIT_NAME)
        return ActionResult(False, "failed", str(exc), backend="systemd")

    if _wait_for_health(cfg.bind_address, cfg.port, tls=_tls_enabled()):
        return ActionResult(
            True,
            "installed",
            backend="systemd",
            details={"unit_path": str(path), "enabled": True, "started": True},
        )
    return ActionResult(
        False,
        "unhealthy",
        "unit was installed and enabled but health is unavailable",
        backend="systemd",
        details={"unit_path": str(path)},
    )


def uninstall_unit():
    from experimental.webui.service_control import ActionResult

    path = get_unit_path()
    if not path.exists():
        return ActionResult(True, "already_uninstalled", backend="systemd")
    if not _unit_is_managed(path):
        return ActionResult(
            False,
            "ambiguous",
            f"refusing to remove unmanaged unit: {path}",
            backend="systemd",
        )
    result = _run_systemctl("disable", "--now", UNIT_NAME)
    if result.returncode != 0:
        reason = result.stderr.strip() or "systemctl disable --now failed"
        return ActionResult(False, "failed", reason, backend="systemd")
    path.unlink()
    reload_result = _run_systemctl("daemon-reload")
    if reload_result.returncode != 0:
        return ActionResult(
            False,
            "failed",
            reload_result.stderr.strip() or "systemctl daemon-reload failed",
            backend="systemd",
        )
    return ActionResult(True, "uninstalled", backend="systemd")


def read_journal(lines: int, *, follow: bool = False) -> subprocess.CompletedProcess[str]:
    args = ["journalctl", "--user", "-u", UNIT_NAME, "-n", str(lines), "--no-pager"]
    if follow:
        args.append("-f")
    return subprocess.run(args, text=True, check=False)
