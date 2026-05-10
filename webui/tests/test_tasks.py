"""Unit tests for Web UI scan task validation and queue behavior."""

import subprocess
import sys

import pytest
from pydantic import ValidationError

from webui import tasks as task_module
from webui.tasks import (
    CancelResult,
    ScanQueue,
    ScanRequest,
    ScanTask,
    TaskStatus,
    _REPO_ROOT,
    _build_subprocess_env,
    _parse_progress,
    build_command,
)


def _request(**overrides):
    data = {"protocol": "smb"}
    data.update(overrides)
    return ScanRequest(**data)


def test_build_command_smb():
    cmd = build_command(_request(countries=["US"]))
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("cli/smbseek.py")
    assert "--verbose" in cmd
    assert "--config" in cmd
    assert "--country" in cmd
    assert "US" in cmd


def test_build_command_ftp():
    cmd = build_command(_request(protocol="ftp"))
    assert cmd[1].endswith("cli/ftpseek.py")


def test_build_command_http():
    cmd = build_command(_request(protocol="http"))
    assert cmd[1].endswith("cli/httpseek.py")


def test_build_command_probe_smb():
    cmd = build_command(_request(run_probe_after_scan=True))
    assert "--check-rce" in cmd


def test_build_command_no_country():
    cmd = build_command(_request())
    assert "--country" not in cmd


def test_build_command_no_filter():
    cmd = build_command(_request(filters=""))
    assert "--filter" not in cmd


def test_build_subprocess_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "existing")
    env = _build_subprocess_env()
    assert env["PYTHONUNBUFFERED"] == "1"
    assert str(_REPO_ROOT) in env["PYTHONPATH"].split(":")


def test_validate_bad_protocol():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="ssh")


def test_validate_probe_ftp_rejected():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="ftp", run_probe_after_scan=True)


def test_validate_probe_http_rejected():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="http", run_probe_after_scan=True)


def test_validate_bad_country():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", countries=["ZZ"])


def test_validate_filter_too_long():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", filters="x" * 501)


@pytest.mark.parametrize("bad_char", ["\x00", "\r", "\n", "\x1f"])
def test_validate_filter_control_chars(bad_char):
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", filters=f"org:Example{bad_char}port:445")


def test_validate_extra_fields_rejected():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", extra="evil")


def test_validate_bool_strict_rejects_string():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", run_probe_after_scan="false")


def test_validate_countries_default_factory():
    first = ScanRequest(protocol="smb")
    second = ScanRequest(protocol="smb")
    first.countries.append("US")
    assert second.countries == []


def test_validate_verbose_field_absent():
    with pytest.raises(ValidationError):
        ScanRequest(protocol="smb", verbose=False)


def test_task_initial_state():
    task = ScanTask(task_id="abc", request=_request())
    assert task.status == TaskStatus.QUEUED
    assert task.stdout_lines == []
    assert task.progress_pct == 0.0
    assert not hasattr(task, "stderr_lines")


def test_task_to_dict_no_private_fields():
    task = ScanTask(task_id="abc", request=_request())
    data = task.to_dict()
    assert "_process" not in data
    assert "_lock" not in data
    assert "_cancel_requested" not in data


def test_task_to_dict_snapshot_under_lock():
    task = ScanTask(task_id="abc", request=_request(countries=["US"]))
    task.stdout_lines.append("one")
    snapshot = task.to_dict()
    task.stdout_lines.append("two")
    task.request.countries.append("GB")
    assert snapshot["log"] == ["one"]
    assert snapshot["countries"] == ["US"]


def test_popen_called_with_correct_args(monkeypatch):
    calls = {}

    class FakePopen:
        pid = 1234
        returncode = 0
        stdout = iter(())

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakePopen()

    monkeypatch.setattr(task_module.subprocess, "Popen", fake_popen)
    queue = ScanQueue()
    task = ScanTask(task_id="abc", request=_request())
    queue._active = task

    queue._run(task)

    assert isinstance(calls["args"], list)
    assert calls["kwargs"]["shell"] is False
    assert calls["kwargs"]["cwd"] == str(_REPO_ROOT)
    assert calls["kwargs"]["stderr"] == subprocess.STDOUT
    assert calls["kwargs"]["env"]["PYTHONUNBUFFERED"] == "1"


def test_queue_submit_starts_when_idle(monkeypatch):
    monkeypatch.setattr(ScanQueue, "_run", lambda self, task: None)
    queue = ScanQueue()
    task = queue.submit(_request())
    assert task.status == TaskStatus.RUNNING


def test_queue_submit_queues_when_active(monkeypatch):
    monkeypatch.setattr(ScanQueue, "_run", lambda self, task: None)
    queue = ScanQueue()
    queue.submit(_request())
    second = queue.submit(_request(protocol="ftp"))
    assert second.status == TaskStatus.QUEUED


def test_queue_fifo_order(monkeypatch):
    monkeypatch.setattr(ScanQueue, "_run", lambda self, task: None)
    queue = ScanQueue()
    queue.submit(_request())
    second = queue.submit(_request(protocol="ftp"))
    with queue._lock:
        queue._active = None
        queue._advance()
    assert second.status == TaskStatus.RUNNING


def test_queue_cancel_queued_returns_ok(monkeypatch):
    monkeypatch.setattr(ScanQueue, "_run", lambda self, task: None)
    queue = ScanQueue()
    queue.submit(_request())
    second = queue.submit(_request(protocol="ftp"))
    assert queue.cancel(second.task_id) == CancelResult.OK
    assert second.status == TaskStatus.CANCELLED


def test_queue_cancel_running_returns_ok(monkeypatch):
    monkeypatch.setattr(ScanQueue, "_run", lambda self, task: None)
    called = {}

    class FakeProc:
        pass

    monkeypatch.setattr(
        task_module, "_terminate_proc", lambda proc: called.setdefault("proc", proc)
    )
    queue = ScanQueue()
    monkeypatch.setattr(queue, "_kill_after", lambda proc, timeout: None)
    task = queue.submit(_request())
    proc = FakeProc()
    with task._lock:
        task._process = proc

    assert queue.cancel(task.task_id) == CancelResult.OK
    assert called["proc"] is proc
    assert task._cancel_requested is True


def test_queue_cancel_done_returns_terminal():
    queue = ScanQueue()
    task = ScanTask(task_id="done", request=_request(), status=TaskStatus.DONE)
    queue._tasks[task.task_id] = task
    assert queue.cancel(task.task_id) == CancelResult.TERMINAL


def test_queue_cancel_unknown_returns_not_found():
    assert ScanQueue().cancel("missing") == CancelResult.NOT_FOUND


def test_cancel_race_before_process_assigned(monkeypatch):
    def fail_popen(*args, **kwargs):
        raise AssertionError("Popen should not be called")

    monkeypatch.setattr(task_module.subprocess, "Popen", fail_popen)
    queue = ScanQueue()
    task = ScanTask(task_id="abc", request=_request(), status=TaskStatus.RUNNING)
    task._cancel_requested = True
    queue._active = task

    queue._run(task)

    assert task.status == TaskStatus.CANCELLED
    assert task.finished_at is not None


def test_parse_progress_known_pattern():
    task = ScanTask(task_id="abc", request=_request())
    _parse_progress(task, "[25%] discovering")
    assert task.progress_pct == 25.0
    assert task.stdout_lines == ["[25%] discovering"]


def test_parse_progress_unknown_line():
    task = ScanTask(task_id="abc", request=_request())
    _parse_progress(task, "plain output")
    assert task.progress_pct == 0.0
    assert task.stdout_lines == ["plain output"]


def test_parse_progress_out_of_range():
    task = ScanTask(task_id="abc", request=_request())
    task.progress_pct = 7.0
    _parse_progress(task, "[125%] strange")
    assert task.progress_pct == 7.0


def test_parse_progress_clamps_to_100():
    task = ScanTask(task_id="abc", request=_request())
    _parse_progress(task, "[100%] done")
    assert task.progress_pct == 100.0
    _parse_progress(task, "[101%] too much")
    assert task.progress_pct == 100.0
