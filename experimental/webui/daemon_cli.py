"""Command-line interface for the runtime-headless Dirracuda Web UI daemon."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import getpass
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Optional
import warnings


VERSION = "1.0.0"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_STOPPED = 3
EXIT_INTERRUPTED = 130


@dataclass
class CommandResult:
    ok: bool
    command: str
    state: str
    backend: str = "none"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    exit_code: int = EXIT_OK
    raw_lines: Optional[list[str]] = None

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("exit_code", None)
        data.pop("raw_lines", None)
        return data


def _security_details(cfg) -> dict[str, Any]:
    from experimental.webui.config import security_mode, security_warnings

    return {
        "security_mode": security_mode(cfg),
        "warnings": security_warnings(cfg),
    }


def _with_security(result: CommandResult, cfg) -> CommandResult:
    result.details.update(_security_details(cfg))
    return result


def _from_action(command: str, result, cfg=None) -> CommandResult:
    command_result = CommandResult(
        ok=result.ok,
        command=command,
        state=result.state,
        backend=result.backend,
        message=result.reason,
        details={"pid": result.pid, **result.details},
        exit_code=EXIT_OK if result.ok else EXIT_FAILED,
    )
    return _with_security(command_result, cfg) if cfg is not None else command_result


def _config_details(cfg) -> dict[str, Any]:
    from experimental.webui.service_control import get_listen_url, get_url

    tls_active = bool(cfg.tls.enabled and cfg.tls.cert_file and cfg.tls.key_file)
    return {
        "path": "",
        "enabled": cfg.enabled,
        "bind_address": cfg.bind_address,
        "port": cfg.port,
        "remote_enabled": cfg.remote_enabled,
        "tls_enabled": cfg.tls.enabled,
        "tls_active": tls_active,
        "insecure_remote_override": cfg.tls.allow_insecure_remote,
        "allowed_cidrs": list(cfg.allowed_cidrs),
        "trusted_hosts": list(getattr(cfg, "trusted_hosts", ())),
        "listen_url": get_listen_url(cfg.bind_address, cfg.port, tls=tls_active),
        "local_url": get_url(cfg.bind_address, cfg.port, tls=tls_active),
        **_security_details(cfg),
    }


def _load_config():
    from experimental.webui.config import load_config

    return load_config()


def _handle_start(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.service_control import start

    cfg = _load_config()
    return _from_action("start", start(cfg.bind_address, cfg.port), cfg)


def _handle_stop(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.service_control import stop

    cfg = _load_config()
    return _from_action("stop", stop(cfg.bind_address, cfg.port), cfg)


def _handle_restart(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.service_control import restart

    cfg = _load_config()
    return _from_action("restart", restart(cfg.bind_address, cfg.port), cfg)


def _handle_status(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.service_control import get_status

    cfg = _load_config()
    status = get_status(cfg.bind_address, cfg.port)
    message = status.reason
    if status.running:
        message = "Web UI is healthy."
    elif status.state == "stopped":
        message = "Web UI is stopped."
    return _with_security(CommandResult(
        ok=status.running,
        command="status",
        state=status.state,
        backend=status.backend,
        message=message,
        details=status.as_dict(),
        exit_code=EXIT_OK if status.running else (
            EXIT_STOPPED if status.state == "stopped" else EXIT_FAILED
        ),
    ), cfg)


def _handle_run(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.server import run

    try:
        run()
    except KeyboardInterrupt:
        return CommandResult(
            False,
            "run",
            "interrupted",
            "foreground",
            "Foreground server interrupted.",
            exit_code=EXIT_INTERRUPTED,
        )
    return CommandResult(
        True,
        "run",
        "stopped",
        "foreground",
        "Foreground server exited cleanly.",
    )


def _tail_lines(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except FileNotFoundError:
        return []


def _follow_file(path: Path, initial: list[str]) -> int:
    for line in initial:
        print(line)
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"No direct-process log exists at {path}", file=sys.stderr)
        return EXIT_FAILED
    try:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                print(line, end="", flush=True)
                continue
            try:
                current = path.stat()
                opened = os.fstat(handle.fileno())
                rotated = (
                    (current.st_dev, current.st_ino)
                    != (opened.st_dev, opened.st_ino)
                    or current.st_size < handle.tell()
                )
            except FileNotFoundError:
                rotated = False
            if rotated:
                handle.close()
                handle = path.open("r", encoding="utf-8", errors="replace")
                continue
            time.sleep(0.25)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        handle.close()


def _handle_logs(args: argparse.Namespace) -> CommandResult:
    from experimental.webui.service_control import get_log_path, get_status
    from experimental.webui.systemd_control import UNIT_NAME

    cfg = _load_config()
    status = get_status(cfg.bind_address, cfg.port)
    if status.backend == "systemd":
        cmd = [
            "journalctl",
            "--user",
            "-u",
            UNIT_NAME,
            "-n",
            str(args.lines),
            "--no-pager",
        ]
        if args.follow:
            cmd.append("-f")
            try:
                return_code = subprocess.run(cmd, check=False).returncode
            except KeyboardInterrupt:
                return_code = EXIT_INTERRUPTED
            return CommandResult(
                return_code == 0,
                "logs",
                "complete" if return_code == 0 else "failed",
                "systemd",
                exit_code=return_code,
            )
        result = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        lines = result.stdout.splitlines()
        return CommandResult(
            result.returncode == 0,
            "logs",
            "complete" if result.returncode == 0 else "failed",
            "systemd",
            result.stderr.strip(),
            {"lines": lines},
            EXIT_OK if result.returncode == 0 else EXIT_FAILED,
            raw_lines=lines,
        )

    path = get_log_path()
    lines = _tail_lines(path, args.lines)
    if args.follow:
        code = _follow_file(path, lines)
        return CommandResult(
            code == 0,
            "logs",
            "complete" if code == 0 else "interrupted",
            "direct",
            exit_code=code,
        )
    message = "" if lines else f"No log entries found at {path}"
    return CommandResult(
        True,
        "logs",
        "complete",
        "direct",
        message,
        {"path": str(path), "lines": lines},
        raw_lines=lines,
    )


def _path_writable(path: Path) -> bool:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK | os.X_OK)


def _handle_doctor(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.auth import CredentialError, credential_exists
    from experimental.webui.config import get_config_path
    from experimental.webui.server import runtime_config_error
    from experimental.webui.service_control import get_log_path, get_status
    from shared.path_service import get_paths

    checks: list[dict[str, Any]] = []

    def add(name: str, state: str, message: str) -> None:
        checks.append({"name": name, "state": state, "message": message})

    missing = []
    for module_name in ("fastapi", "uvicorn", "jinja2", "httpx"):
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
    add(
        "dependencies",
        "pass" if not missing else "fail",
        "available" if not missing else f"missing: {', '.join(missing)}",
    )

    cfg = None
    try:
        cfg = _load_config()
        config_error = runtime_config_error(cfg, cfg.bind_address)
        add(
            "config",
            "pass" if not config_error else "fail",
            config_error or f"valid: {get_config_path()}",
        )
    except Exception as exc:
        add("config", "fail", str(exc))

    try:
        has_credentials = credential_exists()
        add(
            "credentials",
            "pass" if has_credentials else "fail",
            "usable credential store" if has_credentials else "no credential configured",
        )
    except CredentialError as exc:
        add("credentials", "fail", str(exc))

    paths = get_paths()
    for name, path in (
        ("state_path", paths.state_dir),
        ("log_path", get_log_path().parent),
    ):
        add(
            name,
            "pass" if _path_writable(path) else "fail",
            f"writable: {path}" if _path_writable(path) else f"not writable: {path}",
        )

    backend = "none"
    if cfg is not None:
        status = get_status(cfg.bind_address, cfg.port)
        backend = status.backend
        add(
            "runtime",
            "pass" if status.running else "warn",
            status.reason or status.state,
        )
        posture = _security_details(cfg)
        add(
            "security",
            "warn" if posture["warnings"] else "pass",
            posture["warnings"][0] if posture["warnings"] else posture["security_mode"],
        )

    failures = [item for item in checks if item["state"] == "fail"]
    warnings = [item for item in checks if item["state"] == "warn"]
    return CommandResult(
        not failures,
        "doctor",
        "pass" if not failures else "fail",
        backend,
        (
            f"{len(checks) - len(failures) - len(warnings)} passed, "
            f"{len(warnings)} warnings, {len(failures)} failed"
        ),
        {"checks": checks, **(_security_details(cfg) if cfg is not None else {})},
        EXIT_OK if not failures else EXIT_FAILED,
    )


def _handle_config_path(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.config import get_config_path

    path = get_config_path()
    return CommandResult(
        True,
        "config path",
        "complete",
        message=str(path),
        details={"path": str(path)},
    )


def _handle_config_check(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.config import get_config_path
    from experimental.webui.server import runtime_config_error

    cfg = _load_config()
    error = runtime_config_error(cfg, cfg.bind_address)
    details = _config_details(cfg)
    details["path"] = str(get_config_path())
    return CommandResult(
        not error,
        "config check",
        "valid" if not error else "invalid",
        message=error or "Web UI configuration is valid.",
        details=details,
        exit_code=EXIT_OK if not error else EXIT_FAILED,
    )


def _handle_credentials_set(args: argparse.Namespace) -> CommandResult:
    from experimental.webui.auth import (
        get_credential_usernames,
        set_password,
        validate_username,
    )

    try:
        validate_username(args.username)
    except ValueError as exc:
        return CommandResult(
            False,
            "credentials set",
            "failed",
            message=str(exc),
            exit_code=EXIT_FAILED,
        )
    existing = get_credential_usernames()
    if args.username in existing and not args.force:
        try:
            reply = input(
                f"Credential for {args.username!r} exists. Replace it? [y/N] "
            ).strip().lower()
        except EOFError:
            reply = ""
        if reply not in {"y", "yes"}:
            return CommandResult(
                False,
                "credentials set",
                "cancelled",
                message="Credential was not changed.",
                exit_code=EXIT_FAILED,
            )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
    except (EOFError, getpass.GetPassWarning):
        return CommandResult(
            False,
            "credentials set",
            "failed",
            message="A hidden interactive terminal is required to set credentials.",
            exit_code=EXIT_FAILED,
        )
    if password != confirmation:
        return CommandResult(
            False,
            "credentials set",
            "failed",
            message="Passwords do not match.",
            exit_code=EXIT_FAILED,
        )
    try:
        set_password(args.username, password)
    except Exception as exc:
        return CommandResult(
            False,
            "credentials set",
            "failed",
            message=str(exc),
            exit_code=EXIT_FAILED,
        )
    return CommandResult(
        True,
        "credentials set",
        "saved",
        message=f"Credential saved for {args.username}.",
        details={"username": args.username, "replaced": args.username in existing},
    )


def _systemd_status_result() -> CommandResult:
    from experimental.webui.systemd_control import get_unit_path, inspect_state

    state = inspect_state()
    details = asdict(state)
    details["unit_path"] = str(get_unit_path())
    ok = state.installed and state.managed and state.active
    if state.installed and not state.managed:
        status_name = "ambiguous"
        exit_code = EXIT_FAILED
    elif ok:
        status_name = "running"
        exit_code = EXIT_OK
    elif state.installed:
        status_name = "stopped"
        exit_code = EXIT_STOPPED
    else:
        status_name = "not_installed"
        exit_code = EXIT_STOPPED
    return CommandResult(
        ok,
        "systemd status",
        status_name,
        "systemd",
        state.reason,
        details,
        exit_code,
    )


def _handle_systemd_install(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.systemd_control import install_unit

    return _from_action("systemd install", install_unit())


def _handle_systemd_uninstall(_args: argparse.Namespace) -> CommandResult:
    from experimental.webui.systemd_control import uninstall_unit

    return _from_action("systemd uninstall", uninstall_unit())


def _handle_systemd_status(_args: argparse.Namespace) -> CommandResult:
    return _systemd_status_result()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirracuda-d",
        description="Run and manage the runtime-headless Dirracuda Web UI.",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    handlers: tuple[tuple[str, str, Callable], ...] = (
        ("start", "Start the Web UI service.", _handle_start),
        ("stop", "Stop the Web UI service.", _handle_stop),
        ("restart", "Restart the Web UI service.", _handle_restart),
        ("status", "Show service state and health.", _handle_status),
        ("run", "Run the Web UI in the foreground.", _handle_run),
        ("doctor", "Check daemon prerequisites and runtime state.", _handle_doctor),
    )
    for name, help_text, handler in handlers:
        subparser = commands.add_parser(name, help=help_text, description=help_text)
        subparser.set_defaults(handler=handler)

    logs = commands.add_parser("logs", help="Read Web UI service logs.")
    logs.add_argument("-n", "--lines", type=int, default=100)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(handler=_handle_logs)

    config = commands.add_parser("config", help="Inspect Web UI configuration.")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_path = config_commands.add_parser("path", help="Print the config path.")
    config_path.set_defaults(handler=_handle_config_path)
    config_check = config_commands.add_parser("check", help="Validate configuration.")
    config_check.set_defaults(handler=_handle_config_check)

    credentials = commands.add_parser("credentials", help="Manage Web UI credentials.")
    credential_commands = credentials.add_subparsers(
        dest="credential_command", required=True
    )
    credential_set = credential_commands.add_parser("set", help="Set a password.")
    credential_set.add_argument("username")
    credential_set.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing username without confirmation.",
    )
    credential_set.set_defaults(handler=_handle_credentials_set)

    systemd = commands.add_parser("systemd", help="Manage the per-user systemd unit.")
    systemd_commands = systemd.add_subparsers(dest="systemd_command", required=True)
    for name, help_text, handler in (
        ("install", "Install, enable, and start the user unit.", _handle_systemd_install),
        ("uninstall", "Stop, disable, and remove the user unit.", _handle_systemd_uninstall),
        ("status", "Show user-unit installation and state.", _handle_systemd_status),
    ):
        subparser = systemd_commands.add_parser(name, help=help_text)
        subparser.set_defaults(handler=handler)
    return parser


def _print_human(result: CommandResult) -> None:
    if result.raw_lines is not None:
        for line in result.raw_lines:
            print(line)
        if result.message:
            print(result.message, file=sys.stderr)
        return
    if result.command == "config path":
        print(result.details["path"])
        return
    headline = f"{result.command}: {result.state}"
    if result.backend != "none":
        headline += f" ({result.backend})"
    print(headline)
    if result.message:
        print(result.message)
    if result.command == "status":
        for key in ("pid", "listen_url", "local_url"):
            value = result.details.get(key)
            if value is not None:
                print(f"{key.replace('_', ' ').title()}: {value}")
    elif result.command == "doctor":
        for check in result.details.get("checks", []):
            print(f"[{check['state'].upper()}] {check['name']}: {check['message']}")
    elif result.command == "config check":
        for key in (
            "path",
            "listen_url",
            "local_url",
            "remote_enabled",
            "tls_enabled",
            "insecure_remote_override",
            "security_mode",
        ):
            print(f"{key.replace('_', ' ').title()}: {result.details[key]}")
    for warning in result.details.get("warnings", []):
        print(f"WARNING: {warning}")


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in arguments
    if json_requested:
        arguments = [arg for arg in arguments if arg != "--json"]
        arguments.insert(0, "--json")
    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command == "logs" and args.lines < 1:
        parser.error("--lines must be at least 1")
    if args.command == "logs" and args.follow and args.json:
        parser.error("--json cannot be combined with --follow")
    try:
        result = args.handler(args)
    except KeyboardInterrupt:
        result = CommandResult(
            False,
            args.command,
            "interrupted",
            message="Interrupted.",
            exit_code=EXIT_INTERRUPTED,
        )
    except Exception as exc:
        result = CommandResult(
            False,
            args.command,
            "failed",
            message=str(exc),
            exit_code=EXIT_FAILED,
        )
    if args.json:
        print(json.dumps(result.payload(), sort_keys=True))
    else:
        _print_human(result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
