"""Console severity remains readable with and without ANSI colors."""

import logging
import sys
from types import SimpleNamespace

import pytest

from gui.utils.logging_config import ConsoleFormatter
from gui.utils import logging_config


@pytest.mark.parametrize("level,color", [(logging.WARNING, "33"), (logging.ERROR, "31")])
def test_terminal_uses_severity_colors(monkeypatch, level, color):
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(isatty=lambda: True))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    record = logging.LogRecord("test", level, "", 0, "Shutdown outcome", (), None)
    assert ConsoleFormatter().format(record) == f"\033[{color}mShutdown outcome\033[0m"


@pytest.mark.parametrize("tty,no_color", [(False, False), (True, True)])
def test_redirected_or_disabled_color_is_plain_text(monkeypatch, tty, no_color):
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(isatty=lambda: tty))
    if no_color:
        monkeypatch.setenv("NO_COLOR", "")
    record = logging.LogRecord("test", logging.ERROR, "", 0, "Shutdown outcome", (), None)
    assert ConsoleFormatter().format(record) == "Shutdown outcome"


@pytest.mark.parametrize("tty,no_color", [(True, False), (False, False), (True, True)])
def test_traceback_is_saved_with_plain_console_instruction(monkeypatch, tmp_path, tty, no_color):
    create = logging_config.tempfile.NamedTemporaryFile
    calls = []

    def temporary_file(**kwargs):
        calls.append(kwargs.copy())
        kwargs["dir"] = tmp_path
        return create(**kwargs)

    monkeypatch.setattr(logging_config.tempfile, "NamedTemporaryFile", temporary_file)
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.setenv("TERM", "xterm")
    try:
        raise ValueError("full diagnostic detail")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, "", 0, "Database issue; details follow", (), sys.exc_info())
    output = ConsoleFormatter().format(record)
    dumps = list(tmp_path.glob("dirracuda-*.log"))
    assert len(dumps) == 1
    assert calls[0]["dir"] == "/tmp"
    assert dumps[0].stat().st_mode & 0o777 == 0o600
    content = dumps[0].read_text()
    assert "Traceback (most recent call last)" in content
    assert "ValueError: full diagnostic detail" in content
    assert "Database issue" in content
    assert "\033[" not in content
    summary = "\033[31mDatabase issue\033[0m" if tty and not no_color else "Database issue"
    assert output.startswith(summary + " Console log is saved; run cat ")
    assert "\033[" not in output.split(" Console log", 1)[1]
    assert "Traceback" not in output
    assert "full diagnostic detail" not in output
    assert "details follow" not in output
    assert record.exc_info  # Other log handlers still receive the full error.
    assert str(dumps[0]) in output


@pytest.mark.parametrize("failure_stage", ["create", "write", "close"])
def test_dump_failure_keeps_original_traceback(monkeypatch, failure_stage):
    from contextlib import contextmanager

    def fail(*args, **kwargs):
        raise OSError("disk full")

    @contextmanager
    def broken_dump(**kwargs):
        yield SimpleNamespace(name="unused", write=fail if failure_stage == "write" else lambda _: None)
        fail()

    monkeypatch.setattr(logging_config.tempfile, "NamedTemporaryFile",
                        fail if failure_stage == "create" else broken_dump)
    try:
        raise RuntimeError("original failure")
    except RuntimeError:
        record = logging.LogRecord("test", logging.ERROR, "", 0, "Task failed", (), sys.exc_info())
    output = ConsoleFormatter().format(record)
    assert "original failure" in output
    assert "Traceback" in output
    assert "Could not save diagnostic dump" in output
    assert "disk full" in output
    assert "Console log is saved" not in output


def test_multiline_diagnostics_get_distinct_dumps(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    create = logging_config.tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        kwargs["dir"] = tmp_path
        return create(**kwargs)

    monkeypatch.setattr(logging_config.tempfile, "NamedTemporaryFile", temporary_file)
    formatter = ConsoleFormatter()

    def report(index):
        record = logging.LogRecord("test", logging.ERROR, "", 0,
                                   "Task failed\nDiagnostic %s", (index,), None)
        return formatter.format(record)

    with ThreadPoolExecutor(max_workers=3) as executor:
        messages = list(executor.map(report, range(3)))
    dumps = list(tmp_path.glob("dirracuda-*.log"))
    assert len(dumps) == 3
    assert {dump.read_text() for dump in dumps} == {
        f"Task failed\nDiagnostic {index}\n" for index in range(3)
    }
    assert all("Diagnostic" not in message and "run cat " in message for message in messages)
