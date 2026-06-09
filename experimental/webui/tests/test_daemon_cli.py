"""Headless daemon launcher and CLI contract tests."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import types

import pytest

import experimental.webui.daemon_cli as daemon_cli
import experimental.webui.service_control as service_control


def _cfg():
    return types.SimpleNamespace(
        enabled=True,
        bind_address="0.0.0.0",
        port=2600,
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        trusted_hosts=[],
        tls=types.SimpleNamespace(
            enabled=False,
            cert_file="",
            key_file="",
            allow_insecure_remote=True,
        ),
    )


def test_help_lists_operational_and_extensible_command_groups(capsys):
    with pytest.raises(SystemExit) as exc:
        daemon_cli.main(["--help"])

    output = capsys.readouterr().out
    assert exc.value.code == 0
    for command in (
        "start",
        "stop",
        "restart",
        "status",
        "run",
        "logs",
        "doctor",
        "config",
        "credentials",
        "systemd",
    ):
        assert command in output


def test_json_status_has_stable_envelope(monkeypatch, capsys):
    monkeypatch.setattr(daemon_cli, "_load_config", _cfg)
    monkeypatch.setattr(
        service_control,
        "get_status",
        lambda *_a: service_control.ServiceStatus(
            "running",
            "direct",
            True,
            "0.0.0.0",
            2600,
            pid=42,
            managed=True,
        ),
    )

    code = daemon_cli.main(["status", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {
        "ok",
        "command",
        "state",
        "backend",
        "message",
        "details",
    }
    assert payload["backend"] == "direct"
    assert payload["details"]["pid"] == 42
    assert payload["details"]["security_mode"] == "remote_http_plaintext"
    assert payload["details"]["warnings"]


def test_stopped_status_uses_exit_code_three(monkeypatch, capsys):
    monkeypatch.setattr(daemon_cli, "_load_config", _cfg)
    monkeypatch.setattr(
        service_control,
        "get_status",
        lambda *_a: service_control.ServiceStatus(
            "stopped", "direct", False, "0.0.0.0", 2600
        ),
    )

    code = daemon_cli.main(["status", "--json"])

    assert code == daemon_cli.EXIT_STOPPED
    assert json.loads(capsys.readouterr().out)["state"] == "stopped"


def test_json_follow_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        daemon_cli.main(["logs", "--follow", "--json"])

    assert exc.value.code == daemon_cli.EXIT_USAGE
    assert "--json cannot be combined" in capsys.readouterr().err


def test_config_path_prints_only_path(monkeypatch, capsys, tmp_path):
    import experimental.webui.config as webui_config

    path = tmp_path / "webui.json"
    monkeypatch.setattr(webui_config, "get_config_path", lambda: path)

    assert daemon_cli.main(["config", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(path)


def test_config_check_plaintext_warning_keeps_success_exit(
    monkeypatch, capsys, tmp_path,
):
    import experimental.webui.config as webui_config
    import experimental.webui.server as server

    monkeypatch.setattr(daemon_cli, "_load_config", _cfg)
    monkeypatch.setattr(webui_config, "get_config_path", lambda: tmp_path / "webui.json")
    monkeypatch.setattr(server, "runtime_config_error", lambda *_args: None)

    code = daemon_cli.main(["config", "check", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["details"]["security_mode"] == "remote_http_plaintext"
    assert payload["details"]["warnings"]


def test_credentials_set_prompts_twice_and_saves(monkeypatch, capsys):
    import experimental.webui.auth as auth

    saved = []
    prompts = iter(["long-enough-passphrase", "long-enough-passphrase"])
    monkeypatch.setattr(auth, "get_credential_usernames", lambda: [])
    monkeypatch.setattr(auth, "set_password", lambda user, password: saved.append((user, password)))
    monkeypatch.setattr(daemon_cli.getpass, "getpass", lambda _prompt: next(prompts))

    code = daemon_cli.main(["credentials", "set", "admin"])

    assert code == 0
    assert saved == [("admin", "long-enough-passphrase")]
    assert "saved" in capsys.readouterr().out


def test_credentials_existing_user_requires_confirmation(monkeypatch, capsys):
    import experimental.webui.auth as auth

    monkeypatch.setattr(auth, "get_credential_usernames", lambda: ["admin"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    code = daemon_cli.main(["credentials", "set", "admin"])

    assert code == 1
    assert "cancelled" in capsys.readouterr().out


def test_credentials_invalid_username_fails_before_prompt(monkeypatch, capsys):
    import experimental.webui.auth as auth

    prompted = []
    monkeypatch.setattr(auth, "get_credential_usernames", lambda: [])
    monkeypatch.setattr(
        daemon_cli.getpass,
        "getpass",
        lambda prompt: prompted.append(prompt),
    )

    code = daemon_cli.main(["credentials", "set", " bad "])

    assert code == 1
    assert prompted == []
    assert "whitespace" in capsys.readouterr().out


def test_credentials_getpass_fallback_fails_closed(monkeypatch, capsys):
    import warnings
    import experimental.webui.auth as auth

    monkeypatch.setattr(auth, "get_credential_usernames", lambda: [])

    def fallback(_prompt):
        warnings.warn("cannot hide input", daemon_cli.getpass.GetPassWarning)
        return "visible-secret"

    monkeypatch.setattr(daemon_cli.getpass, "getpass", fallback)

    code = daemon_cli.main(["credentials", "set", "admin"])

    assert code == 1
    assert "hidden interactive terminal" in capsys.readouterr().out


def test_follow_file_reopens_after_rotation(tmp_path, monkeypatch, capsys):
    path = tmp_path / "webui.log"
    path.write_text("old\n", encoding="utf-8")
    sleeps = 0

    def rotate_then_interrupt(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            path.replace(tmp_path / "webui.log.1")
            path.write_text("new-after-rotation\n", encoding="utf-8")
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_cli.time, "sleep", rotate_then_interrupt)

    code = daemon_cli._follow_file(path, [])

    assert code == daemon_cli.EXIT_INTERRUPTED
    assert "new-after-rotation" in capsys.readouterr().out


def test_daemon_modules_import_without_tkinter(monkeypatch):
    blocked = {"tkinter"}

    class Blocker:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".", 1)[0] in blocked:
                raise AssertionError(f"headless import attempted: {fullname}")
            return None

    blocker = Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        __import__("experimental.webui.daemon_cli")
        __import__("experimental.webui.service_control")
        __import__("experimental.webui.systemd_control")
        __import__("experimental.webui.server")
        assert not any(
            name == "tkinter" or name.startswith("tkinter.")
            for name in sys.modules
        )
    finally:
        sys.meta_path.remove(blocker)


def test_root_launcher_reexecs_repository_venv(monkeypatch):
    launcher = Path(__file__).resolve().parents[3] / "dirracuda-d"
    loader = SourceFileLoader("dirracuda_daemon_launcher", str(launcher))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    called = {}
    monkeypatch.setattr(
        module.os,
        "execv",
        lambda executable, argv: called.update(executable=executable, argv=argv),
    )
    monkeypatch.setattr(module.sys, "argv", [str(launcher), "status", "--json"])

    assert module.main() == 1
    assert called["executable"].endswith("/venv/bin/python")
    assert called["argv"][-2:] == ["status", "--json"]
