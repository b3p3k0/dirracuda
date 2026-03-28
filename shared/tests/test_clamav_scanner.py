"""Unit tests for shared/clamav_scanner.py.

All subprocess.Popen and shutil.which calls are monkeypatched.
No real ClamAV binary is required.
"""
import subprocess
from unittest.mock import MagicMock

import pytest

from shared.clamav_scanner import ClamAVScanner, ScanResult, scanner_from_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_popen(returncode=0, stdout="", stderr=""):
    """Return a mock Popen instance whose communicate() returns (stdout, stderr)."""
    mock = MagicMock()
    mock.communicate.return_value = (stdout, stderr)
    mock.returncode = returncode
    return mock


def _which_both(name):
    """Simulate both clamdscan and clamscan present."""
    return f"/usr/bin/{name}"


def _which_clamscan_only(name):
    """Simulate only clamscan present (clamdscan absent)."""
    return "/usr/bin/clamscan" if name == "clamscan" else None


def _which_none(_name):
    return None


# ---------------------------------------------------------------------------
# Backend selection — auto
# ---------------------------------------------------------------------------

def test_auto_prefers_clamdscan_when_available(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    mock_proc = _make_popen(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", lambda *a, **kw: mock_proc)

    result = ClamAVScanner(backend="auto").scan_file("/tmp/file.txt")

    assert result.verdict == "clean"
    assert result.backend_used == "clamdscan"


def test_auto_falls_back_to_clamscan(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_clamscan_only)
    mock_proc = _make_popen(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", lambda *a, **kw: mock_proc)

    result = ClamAVScanner(backend="auto").scan_file("/tmp/file.txt")

    assert result.verdict == "clean"
    assert result.backend_used == "clamscan"


def test_auto_no_binary_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_none)

    result = ClamAVScanner(backend="auto").scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert result.backend_used is None
    assert "no scanner binary found" in result.error


# ---------------------------------------------------------------------------
# Backend selection — explicit clamdscan
# ---------------------------------------------------------------------------

def test_explicit_clamdscan_present(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    mock_proc = _make_popen(returncode=0)
    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", lambda *a, **kw: mock_proc)

    result = ClamAVScanner(backend="clamdscan").scan_file("/tmp/file.txt")

    assert result.backend_used == "clamdscan"


def test_explicit_clamdscan_missing_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_none)

    result = ClamAVScanner(backend="clamdscan").scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert result.backend_used is None


# ---------------------------------------------------------------------------
# Backend selection — explicit clamscan
# ---------------------------------------------------------------------------

def test_explicit_clamscan_present(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_clamscan_only)
    mock_proc = _make_popen(returncode=0)
    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", lambda *a, **kw: mock_proc)

    result = ClamAVScanner(backend="clamscan").scan_file("/tmp/file.txt")

    assert result.backend_used == "clamscan"


def test_explicit_clamscan_missing_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_none)

    result = ClamAVScanner(backend="clamscan").scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert result.backend_used is None


# ---------------------------------------------------------------------------
# Invalid backend
# ---------------------------------------------------------------------------

def test_invalid_backend_returns_error():
    result = ClamAVScanner(backend="foo").scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert result.backend_used is None
    assert "invalid backend: foo" in result.error


# ---------------------------------------------------------------------------
# Exit-code / result mapping
# ---------------------------------------------------------------------------

def test_exit_0_returns_clean(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: _make_popen(returncode=0, stdout="", stderr=""),
    )

    result = ClamAVScanner().scan_file("/tmp/clean.txt")

    assert result.verdict == "clean"
    assert result.signature is None
    assert result.exit_code == 0


def test_exit_1_returns_infected_with_signature(monkeypatch):
    stdout = "/tmp/evil.txt: Eicar-Test-Signature FOUND"
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: _make_popen(returncode=1, stdout=stdout, stderr=""),
    )

    result = ClamAVScanner().scan_file("/tmp/evil.txt")

    assert result.verdict == "infected"
    assert result.signature == "Eicar-Test-Signature"
    assert result.exit_code == 1


def test_exit_1_no_found_line_signature_is_none(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: _make_popen(returncode=1, stdout="something unexpected", stderr=""),
    )

    result = ClamAVScanner().scan_file("/tmp/evil.txt")

    assert result.verdict == "infected"
    assert result.signature is None


def test_exit_2_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: _make_popen(returncode=2, stdout="", stderr="engine error"),
    )

    result = ClamAVScanner().scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert result.exit_code == 2
    assert "engine error" in result.raw_output


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)

    kill_called = []

    def _communicate_timeout(timeout=None):
        raise subprocess.TimeoutExpired(cmd="clamdscan", timeout=timeout)

    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = _communicate_timeout
    mock_proc.kill.side_effect = lambda: kill_called.append(True)

    # After kill, second communicate() drains without error
    mock_proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="clamdscan", timeout=5),
        ("", ""),
    ]

    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", lambda *a, **kw: mock_proc)

    result = ClamAVScanner(timeout_seconds=5).scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert "scanner timeout" in result.error
    assert kill_called, "proc.kill() was not called"


# ---------------------------------------------------------------------------
# Launch failures
# ---------------------------------------------------------------------------

def test_file_not_found_returns_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no such file")),
    )

    result = ClamAVScanner().scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert "scanner not found" in result.error


def test_oserror_non_fnf_returns_launch_error(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError("permission denied")),
    )

    result = ClamAVScanner().scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert "failed to launch scanner" in result.error


# ---------------------------------------------------------------------------
# raw_output preservation (clamdscan daemon not running)
# ---------------------------------------------------------------------------

def test_clamdscan_could_not_connect_preserves_raw_output(monkeypatch):
    stderr = "ERROR: Could not connect to clamd on /var/run/clamav/clamd.ctl: No such file or directory"
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)
    monkeypatch.setattr(
        "shared.clamav_scanner.subprocess.Popen",
        lambda *a, **kw: _make_popen(returncode=2, stdout="", stderr=stderr),
    )

    result = ClamAVScanner(backend="clamdscan").scan_file("/tmp/file.txt")

    assert result.verdict == "error"
    assert "Could not connect to clamd" in result.raw_output


# ---------------------------------------------------------------------------
# shell=False guard
# ---------------------------------------------------------------------------

def test_shell_false_always(monkeypatch):
    monkeypatch.setattr("shared.clamav_scanner.shutil.which", _which_both)

    captured_kwargs = {}

    def _fake_popen(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_popen(returncode=0)

    monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", _fake_popen)

    ClamAVScanner().scan_file("/tmp/file.txt")

    assert captured_kwargs["shell"] is False


# ---------------------------------------------------------------------------
# scanner_from_config factory
# ---------------------------------------------------------------------------

def test_scanner_from_config_passes_values():
    cfg = {
        "backend": "clamscan",
        "clamscan_path": "/opt/clamav/bin/clamscan",
        "clamdscan_path": "/opt/clamav/bin/clamdscan",
        "timeout_seconds": 30,
    }

    scanner = scanner_from_config(cfg)

    assert scanner.backend == "clamscan"
    assert scanner.clamscan_path == "/opt/clamav/bin/clamscan"
    assert scanner.clamdscan_path == "/opt/clamav/bin/clamdscan"
    assert scanner.timeout_seconds == 30


def test_scanner_from_config_defaults():
    scanner = scanner_from_config({})

    assert scanner.backend == "auto"
    assert scanner.clamscan_path == "clamscan"
    assert scanner.clamdscan_path == "clamdscan"
    assert scanner.timeout_seconds == 60
