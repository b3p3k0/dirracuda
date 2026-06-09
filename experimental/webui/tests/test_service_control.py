"""Structured direct-process service control tests."""

from __future__ import annotations

import json
import logging
import os
import signal
import stat
import types
import urllib.request

import pytest

import experimental.webui.service_control as service_control
from experimental.webui.server import PrivateRotatingFileHandler


class _FakeProcess:
    def __init__(self, poll_values, pid=12345):
        self.pid = pid
        self._poll_values = list(poll_values)
        self.terminated = False

    def poll(self):
        if self._poll_values:
            return self._poll_values.pop(0)
        return None

    def terminate(self):
        self.terminated = True


@pytest.fixture
def isolated_control(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(service_control, "_PID_FILE", state_dir / "webui.pid")
    monkeypatch.setattr(service_control, "_START_LOCK_FILE", state_dir / "webui.lock")
    monkeypatch.setattr(service_control, "_LOG_FILE", log_dir / "webui.log")
    monkeypatch.setattr(service_control, "_systemd_installed", lambda: False)
    monkeypatch.setattr(service_control, "_tls_enabled", lambda: False)
    monkeypatch.setattr(service_control.time, "sleep", lambda *_a, **_k: None)
    return tmp_path


def _valid_cfg(host="127.0.0.1", port=2600):
    return types.SimpleNamespace(
        bind_address=host,
        port=port,
        tls=types.SimpleNamespace(enabled=False, cert_file="", key_file=""),
    )


def test_pid_record_is_atomic_and_private(isolated_control):
    service_control._write_pid(42, "0.0.0.0", 2600)

    payload = json.loads(service_control._PID_FILE.read_text())
    assert payload["pid"] == 42
    assert payload["host"] == "0.0.0.0"
    if os.name != "nt":
        assert stat.S_IMODE(service_control._PID_FILE.stat().st_mode) == 0o600


def test_direct_status_distinguishes_stopped_and_unmanaged(
    isolated_control, monkeypatch
):
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)
    assert service_control.direct_status().state == "stopped"

    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: True)
    status = service_control.direct_status()
    assert status.state == "unmanaged"
    assert status.healthy is True


def test_direct_status_clears_stale_record(isolated_control, monkeypatch):
    service_control._write_pid(42, "127.0.0.1", 2600)
    monkeypatch.setattr(service_control, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)

    status = service_control.direct_status()

    assert status.state == "stale"
    assert not service_control._PID_FILE.exists()


def test_direct_status_refuses_alien_pid(isolated_control, monkeypatch):
    service_control._write_pid(42, "127.0.0.1", 2600)
    monkeypatch.setattr(service_control, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        service_control,
        "_check_ownership",
        lambda _pid: service_control._Ownership.ALIEN,
    )
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)

    status = service_control.direct_status()

    assert status.state == "ambiguous"
    assert service_control._PID_FILE.exists()


def test_start_uses_module_command_persistent_log_and_wildcard_pid(
    isolated_control, monkeypatch
):
    captured = {}
    writes = []
    monkeypatch.setattr(
        service_control,
        "_startup_preflight",
        lambda: (_valid_cfg("0.0.0.0"), ""),
    )
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a, **_k: service_control.ServiceStatus(
            "stopped", "direct", False, "0.0.0.0", 2600
        ),
    )
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(
        service_control, "_write_pid", lambda *args: writes.append(args)
    )

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess([None])

    monkeypatch.setattr(service_control.subprocess, "Popen", fake_popen)

    result = service_control.start_direct("0.0.0.0", 2600)

    assert result.state == "running"
    assert captured["cmd"][-6:] == [
        "--host",
        "0.0.0.0",
        "--port",
        "2600",
        "--log-file",
        str(service_control._LOG_FILE),
    ]
    assert captured["kwargs"]["stderr"] is service_control.subprocess.STDOUT
    assert captured["kwargs"]["stdout"].name == str(service_control._LOG_FILE)
    assert writes == [(12345, "0.0.0.0", 2600)]


def test_start_reports_early_exit_with_log_tail(isolated_control, monkeypatch):
    monkeypatch.setattr(
        service_control, "_startup_preflight", lambda: (_valid_cfg(), "")
    )
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a, **_k: service_control.ServiceStatus(
            "stopped", "direct", False, "127.0.0.1", 2600
        ),
    )
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)
    monkeypatch.setattr(
        service_control.subprocess,
        "Popen",
        lambda *_a, **_k: _FakeProcess([3]),
    )

    result = service_control.start_direct()

    assert result.ok is False
    assert result.state == "failed"
    assert "code 3" in result.reason


def test_continuous_log_rotation_retains_three_private_files(tmp_path):
    path = tmp_path / "webui.log"
    handler = PrivateRotatingFileHandler(
        path,
        maxBytes=128,
        backupCount=3,
        encoding="utf-8",
    )
    logger = logging.getLogger(f"test.webui.rotation.{id(path)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for index in range(100):
            logger.info("line-%03d-%s", index, "x" * 40)
    finally:
        handler.close()
        logger.handlers = []

    files = sorted(tmp_path.glob("webui.log*"))
    assert 2 <= len(files) <= 4
    assert path.exists()
    if os.name != "nt":
        assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in files)


def test_start_fails_closed_without_credentials(isolated_control, monkeypatch):
    monkeypatch.setattr(
        service_control,
        "direct_status",
        lambda *_a, **_k: service_control.ServiceStatus(
            "stopped", "direct", False, "127.0.0.1", 2600
        ),
    )
    monkeypatch.setattr(
        service_control,
        "_startup_preflight",
        lambda: (None, "no usable Web UI credential exists"),
    )

    result = service_control.start_direct()

    assert result.state == "failed"
    assert "credential" in result.reason


def test_stop_waits_then_escalates_process_group(isolated_control, monkeypatch):
    statuses = service_control.ServiceStatus(
        "running", "direct", True, "127.0.0.1", 2600, pid=42, managed=True
    )
    alive = iter([True, True, False])
    signals = []
    monkeypatch.setattr(service_control, "direct_status", lambda *_a, **_k: statuses)
    monkeypatch.setattr(service_control, "_pid_alive", lambda _pid: next(alive))
    monkeypatch.setattr(
        service_control,
        "_signal_process_group",
        lambda pid, sig: signals.append((pid, sig)),
    )
    monkeypatch.setattr(service_control, "_STOP_TIMEOUT_SECONDS", 0)

    result = service_control.stop_direct()

    assert result.ok is True
    assert result.details["forced"] is True
    assert signals == [(42, signal.SIGTERM), (42, signal.SIGKILL)]


def test_wildcard_urls_separate_listener_from_local_browser():
    assert service_control.get_listen_url("0.0.0.0", 2600) == "http://0.0.0.0:2600"
    assert service_control.get_url("0.0.0.0", 2600) == "http://127.0.0.1:2600"
    assert service_control.get_listen_url("::", 2600) == "http://[::]:2600"
    assert service_control.get_url("::", 2600) == "http://[::1]:2600"
    status = service_control.ServiceStatus(
        "running", "direct", True, "::", 2600, tls=True
    )
    assert status.as_dict()["local_url"] == "https://[::1]:2600"


def test_health_check_uses_loopback_for_wildcard_listener(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(url, timeout):
        captured.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert service_control._health_ok("0.0.0.0", 2600) is True
    assert captured == {"url": "http://127.0.0.1:2600/health", "timeout": 2}
