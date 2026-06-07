"""Service control startup behavior tests."""

from __future__ import annotations

import io
import sys
import urllib.request

import experimental.webui.service_control as service_control


class _FakeProc:
    def __init__(self, poll_values, stderr_text="", pid=12345):
        self.pid = pid
        self._poll_values = list(poll_values)
        self.stderr = io.StringIO(stderr_text)
        self.terminated = False

    def poll(self):
        if self._poll_values:
            return self._poll_values.pop(0)
        return None

    def terminate(self):
        self.terminated = True


def _patch_start_side_effects(monkeypatch):
    monkeypatch.setattr(service_control, "_write_pid", lambda *_a, **_k: None)
    monkeypatch.setattr(service_control, "_clear_pid", lambda: None)
    monkeypatch.setattr(service_control.time, "sleep", lambda *_a, **_k: None)


def test_start_uses_module_launch_command(monkeypatch):
    _patch_start_side_effects(monkeypatch)
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: False)
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)

    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc([1], stderr_text="boom")

    monkeypatch.setattr(service_control.subprocess, "Popen", _fake_popen)

    result = service_control.start("127.0.0.1", 5480)

    assert result.state == "failed"
    assert captured["cmd"][:3] == [sys.executable, "-m", "experimental.webui.server"]
    assert captured["cmd"][-4:] == ["--host", "127.0.0.1", "--port", "5480"]
    assert captured["kwargs"]["cwd"] == str(service_control._REPO_ROOT)


def test_start_early_exit_returns_failed_reason_with_exit_code(monkeypatch):
    _patch_start_side_effects(monkeypatch)
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: False)
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)
    monkeypatch.setattr(
        service_control.subprocess,
        "Popen",
        lambda *_a, **_k: _FakeProc([3], stderr_text="ModuleNotFoundError: experimental"),
    )

    result = service_control.start("127.0.0.1", 5480)

    assert result.ok is False
    assert result.state == "failed"
    assert "code 3" in result.reason
    assert "ModuleNotFoundError" in result.reason


def test_start_reports_running_when_health_turns_ready(monkeypatch):
    _patch_start_side_effects(monkeypatch)
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: False)

    class _Health:
        def __init__(self):
            self.calls = 0

        def __call__(self, *_a, **_k):
            self.calls += 1
            return self.calls >= 2

    monkeypatch.setattr(service_control, "_health_ok", _Health())
    monkeypatch.setattr(
        service_control.subprocess,
        "Popen",
        lambda *_a, **_k: _FakeProc([None, None], stderr_text=""),
    )

    result = service_control.start("127.0.0.1", 5480)

    assert result.ok is True
    assert result.state == "running"
    assert result.reason == ""


def test_start_already_running_short_circuits(monkeypatch):
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: True)

    def _fail_if_called(*_a, **_k):
        raise AssertionError("Popen should not be called when already running")

    monkeypatch.setattr(service_control.subprocess, "Popen", _fail_if_called)

    result = service_control.start("127.0.0.1", 5480)

    assert result.ok is True
    assert result.state == "already_running"
    assert result.reason == ""


def test_get_url_defaults_to_new_port():
    assert service_control.get_url() == "http://127.0.0.1:2600"


def test_wildcard_urls_separate_listener_from_local_browser():
    assert service_control.get_listen_url("0.0.0.0", 2600) == "http://0.0.0.0:2600"
    assert service_control.get_url("0.0.0.0", 2600) == "http://127.0.0.1:2600"
    assert service_control.get_listen_url("::", 2600) == "http://[::]:2600"
    assert service_control.get_url("::", 2600) == "http://[::1]:2600"


def test_health_check_uses_loopback_for_wildcard_listener(monkeypatch):
    captured = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _urlopen(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    assert service_control._health_ok("0.0.0.0", 2600) is True
    assert captured == {"url": "http://127.0.0.1:2600/health", "timeout": 2}


def test_start_retains_wildcard_in_command_and_pid_record(monkeypatch):
    writes = []
    captured = {}
    monkeypatch.setattr(service_control, "_write_pid", lambda *args: writes.append(args))
    monkeypatch.setattr(service_control, "_clear_pid", lambda: None)
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: False)
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: True)

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc([None])

    monkeypatch.setattr(service_control.subprocess, "Popen", _fake_popen)

    result = service_control.start("0.0.0.0", 2600)

    assert result.state == "running"
    assert captured["cmd"][-4:] == ["--host", "0.0.0.0", "--port", "2600"]
    assert writes == [(12345, "0.0.0.0", 2600)]


def test_start_defaults_to_new_port(monkeypatch):
    _patch_start_side_effects(monkeypatch)
    monkeypatch.setattr(service_control, "is_running", lambda *_a, **_k: False)
    monkeypatch.setattr(service_control, "_health_ok", lambda *_a, **_k: False)

    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc([1], stderr_text="boom")

    monkeypatch.setattr(service_control.subprocess, "Popen", _fake_popen)

    result = service_control.start()

    assert result.state == "failed"
    assert captured["cmd"][:3] == [sys.executable, "-m", "experimental.webui.server"]
    assert captured["cmd"][-4:] == ["--host", "127.0.0.1", "--port", "2600"]
