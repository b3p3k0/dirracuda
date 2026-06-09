"""Per-user systemd backend tests."""

from __future__ import annotations

import subprocess
import types

import experimental.webui.service_control as service_control
import experimental.webui.systemd_control as systemd_control


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_render_unit_uses_foreground_launcher_and_hardening(tmp_path):
    unit = systemd_control.render_unit(tmp_path)

    assert systemd_control.MANAGED_MARKER in unit
    assert f"WorkingDirectory={tmp_path}" in unit
    assert f'ExecStart="{tmp_path / "dirracuda-d"}" run' in unit
    assert "Restart=on-failure" in unit
    assert "TimeoutStopSec=15" in unit
    assert "KillMode=control-group" in unit
    assert "UMask=0077" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "WantedBy=default.target" in unit


def test_render_unit_escapes_working_directory_spaces():
    unit = systemd_control.render_unit("/tmp/Dirracuda Checkout")

    assert "WorkingDirectory=/tmp/Dirracuda\\x20Checkout" in unit
    assert 'ExecStart="/tmp/Dirracuda Checkout/dirracuda-d" run' in unit


def test_install_refuses_unmanaged_existing_unit(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    unit_path.write_text("[Unit]\nDescription=someone else\n")
    cfg = types.SimpleNamespace(bind_address="127.0.0.1", port=2600)
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)
    monkeypatch.setattr(systemd_control.shutil, "which", lambda _name: "/bin/systemctl")
    monkeypatch.setattr(service_control, "_startup_preflight", lambda: (cfg, ""))
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a: service_control.ServiceStatus(
            "stopped", "direct", False, "127.0.0.1", 2600
        ),
    )

    result = systemd_control.install_unit()

    assert result.state == "ambiguous"
    assert "refusing to overwrite" in result.reason


def test_install_writes_enables_and_starts_managed_unit(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    cfg = types.SimpleNamespace(bind_address="127.0.0.1", port=2600)
    commands = []
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)
    monkeypatch.setattr(systemd_control.shutil, "which", lambda _name: "/bin/systemctl")
    monkeypatch.setattr(service_control, "_startup_preflight", lambda: (cfg, ""))
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a: service_control.ServiceStatus(
            "stopped", "direct", False, "127.0.0.1", 2600
        ),
    )
    monkeypatch.setattr(service_control, "_wait_for_health", lambda *_a, **_k: True)
    monkeypatch.setattr(service_control, "_tls_enabled", lambda: False)

    def run_systemctl(*args):
        commands.append(args)
        return _completed()

    monkeypatch.setattr(systemd_control, "_run_systemctl", run_systemctl)

    result = systemd_control.install_unit()

    assert result.state == "installed"
    assert systemd_control.MANAGED_MARKER in unit_path.read_text()
    assert ("daemon-reload",) in commands
    assert ("enable", "--now", systemd_control.UNIT_NAME) in commands


def test_install_rolls_back_unit_when_enable_fails(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    cfg = types.SimpleNamespace(bind_address="127.0.0.1", port=2600)
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)
    monkeypatch.setattr(systemd_control.shutil, "which", lambda _name: "/bin/systemctl")
    monkeypatch.setattr(service_control, "_startup_preflight", lambda: (cfg, ""))
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a: service_control.ServiceStatus(
            "stopped", "direct", False, "127.0.0.1", 2600
        ),
    )

    def run_systemctl(*args):
        if args[:2] == ("enable", "--now"):
            return _completed(1, stderr="user bus unavailable")
        return _completed()

    monkeypatch.setattr(systemd_control, "_run_systemctl", run_systemctl)

    result = systemd_control.install_unit()

    assert result.state == "failed"
    assert "user bus" in result.reason
    assert not unit_path.exists()


def test_reinstall_stops_existing_managed_unit_before_update(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    unit_path.write_text(systemd_control.render_unit(tmp_path))
    cfg = types.SimpleNamespace(bind_address="127.0.0.1", port=2600)
    commands = []
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)
    monkeypatch.setattr(systemd_control.shutil, "which", lambda _name: "/bin/systemctl")
    monkeypatch.setattr(service_control, "_startup_preflight", lambda: (cfg, ""))
    monkeypatch.setattr(
        systemd_control,
        "inspect_state",
        lambda: systemd_control.SystemdState(True, True, True, True, 42),
    )
    monkeypatch.setattr(service_control, "_wait_for_health", lambda *_a, **_k: True)
    monkeypatch.setattr(service_control, "_tls_enabled", lambda: False)
    monkeypatch.setattr(
        systemd_control,
        "_run_systemctl",
        lambda *args: commands.append(args) or _completed(),
    )

    result = systemd_control.install_unit()

    assert result.state == "installed"
    assert commands[0] == ("stop", systemd_control.UNIT_NAME)
    assert ("enable", "--now", systemd_control.UNIT_NAME) in commands


def test_uninstall_refuses_unmanaged_unit(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    unit_path.write_text("not managed")
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)

    result = systemd_control.uninstall_unit()

    assert result.state == "ambiguous"
    assert unit_path.exists()


def test_uninstall_disables_removes_and_reloads(tmp_path, monkeypatch):
    unit_path = tmp_path / "dirracuda-d.service"
    unit_path.write_text(systemd_control.render_unit(tmp_path))
    commands = []
    monkeypatch.setattr(systemd_control, "get_unit_path", lambda: unit_path)
    monkeypatch.setattr(
        systemd_control,
        "_run_systemctl",
        lambda *args: commands.append(args) or _completed(),
    )

    result = systemd_control.uninstall_unit()

    assert result.state == "uninstalled"
    assert not unit_path.exists()
    assert ("disable", "--now", systemd_control.UNIT_NAME) in commands
    assert ("daemon-reload",) in commands
