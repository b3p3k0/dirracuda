"""Deterministic lifecycle and lock-contract tests for ScanManager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gui.utils import scan_manager as sm_mod


pytestmark = pytest.mark.scenario


class _DummyBackendInterface:
    """BackendInterface stand-in for non-network lifecycle tests."""

    instances = []

    def __init__(self, backend_path: str):
        self.backend_path = Path(backend_path).resolve()
        self.config_path = self.backend_path / "conf" / "config.json"
        self.terminate_calls = 0
        _DummyBackendInterface.instances.append(self)

    def terminate_current_operation(self) -> None:
        self.terminate_calls += 1


class _DummyThread:
    """Thread stand-in that records start() without running workers."""

    created = []

    def __init__(self, target=None, args=(), daemon=True, **_kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        _DummyThread.created.append(self)

    def start(self) -> None:
        self.started = True


class _FailingBackendInterface:
    def __init__(self, _backend_path: str):
        raise RuntimeError("backend init failed")


def _write_lock(path: Path, *, process_id: int = 1, scan_type: str = "complete") -> None:
    path.write_text(
        json.dumps(
            {
                "start_time": "2026-04-25T00:00:00",
                "scan_type": scan_type,
                "country": None,
                "process_id": process_id,
                "created_by": "Dirracuda",
            }
        ),
        encoding="utf-8",
    )


def _start_method_args(method_name: str, tmp_path: Path):
    common = {
        "scan_options": {"country": None},
        "backend_path": str(tmp_path),
        "progress_callback": lambda *_a: None,
        "log_callback": lambda _line: None,
        "config_path": None,
    }
    if method_name == "start_scan":
        return common
    if method_name == "start_ftp_scan":
        return common
    if method_name == "start_http_scan":
        return common
    raise AssertionError(f"Unknown start method: {method_name}")


def test_init_removes_stale_lock(tmp_path, monkeypatch):
    lock_file = tmp_path / ".scan_lock"
    _write_lock(lock_file, process_id=98765)
    monkeypatch.setattr(sm_mod.ScanManager, "_process_exists", lambda self, _pid: False)
    sm_mod.ScanManager(gui_directory=str(tmp_path))
    assert not lock_file.exists()


def test_init_preserves_valid_lock(tmp_path, monkeypatch):
    lock_file = tmp_path / ".scan_lock"
    _write_lock(lock_file, process_id=123)
    monkeypatch.setattr(sm_mod.ScanManager, "_process_exists", lambda self, _pid: True)
    sm_mod.ScanManager(gui_directory=str(tmp_path))
    assert lock_file.exists()


def test_init_removes_corrupted_lock_file(tmp_path):
    lock_file = tmp_path / ".scan_lock"
    lock_file.write_text("{not-json", encoding="utf-8")
    sm_mod.ScanManager(gui_directory=str(tmp_path))
    assert not lock_file.exists()


def test_is_scan_active_prefers_in_memory_state(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm.is_scanning = True
    assert sm.is_scan_active() is True


def test_is_scan_active_clears_stale_lock(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    _write_lock(sm.lock_file, process_id=456)
    sm._process_exists = lambda _pid: False
    assert sm.is_scan_active() is False
    assert not sm.lock_file.exists()


def test_create_lock_file_rejects_when_scan_active(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm.is_scanning = True
    assert sm.create_lock_file(scan_type="complete") is False
    assert not sm.lock_file.exists()


def test_remove_lock_file_idempotent(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    _write_lock(sm.lock_file, process_id=321)
    sm.remove_lock_file()
    sm.remove_lock_file()
    assert not sm.lock_file.exists()


@pytest.mark.parametrize(
    ("method_name", "scan_type", "protocol"),
    [
        ("start_scan", "complete", None),
        ("start_ftp_scan", "ftp", "ftp"),
        ("start_http_scan", "http", "http"),
    ],
)
def test_start_methods_initialize_scan_state(
    method_name: str,
    scan_type: str,
    protocol: str | None,
    tmp_path,
    monkeypatch,
):
    _DummyBackendInterface.instances.clear()
    _DummyThread.created.clear()
    monkeypatch.setattr(sm_mod, "BackendInterface", _DummyBackendInterface)
    monkeypatch.setattr(sm_mod.threading, "Thread", _DummyThread)

    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    method = getattr(sm, method_name)
    started = method(**_start_method_args(method_name, tmp_path))

    assert started is True
    assert sm.is_scanning is True
    assert sm.scan_thread is _DummyThread.created[-1]
    assert sm.scan_thread.started is True
    assert sm.lock_file.exists()
    lock_data = json.loads(sm.lock_file.read_text(encoding="utf-8"))
    assert lock_data.get("scan_type") == scan_type
    if protocol is None:
        assert "protocol" not in sm.scan_results
    else:
        assert sm.scan_results.get("protocol") == protocol


@pytest.mark.parametrize("method_name", ["start_scan", "start_ftp_scan", "start_http_scan"])
def test_start_methods_reject_when_already_active(method_name: str, tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm.is_scanning = True
    method = getattr(sm, method_name)
    started = method(**_start_method_args(method_name, tmp_path))
    assert started is False


@pytest.mark.parametrize("method_name", ["start_scan", "start_ftp_scan", "start_http_scan"])
def test_start_methods_failure_path_cleans_state_and_lock(method_name: str, tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "BackendInterface", _FailingBackendInterface)
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm._update_progress = MagicMock()

    method = getattr(sm, method_name)
    started = method(**_start_method_args(method_name, tmp_path))

    assert started is False
    assert sm.is_scanning is False
    assert not sm.lock_file.exists()
    sm._update_progress.assert_called_once()
    assert sm._update_progress.call_args.args[0] == 0
    assert sm._update_progress.call_args.args[2] == "error"


def test_interrupt_scan_returns_false_when_inactive(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    assert sm.interrupt_scan() is False


def test_interrupt_scan_updates_state_and_calls_backend(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    backend = _DummyBackendInterface(str(tmp_path))
    sm.backend_interface = backend
    sm.is_scanning = True
    sm.scan_results = {"status": "running"}

    assert sm.interrupt_scan() is True
    assert backend.terminate_calls == 1
    assert sm.scan_results.get("status") == "cancelling"
    assert "cancellation_start" in sm.scan_results


def test_interrupt_scan_handles_backend_termination_error(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm.is_scanning = True
    sm.scan_results = {"status": "running"}
    sm.backend_interface = MagicMock()
    sm.backend_interface.terminate_current_operation.side_effect = RuntimeError("boom")

    assert sm.interrupt_scan() is False
    assert sm.scan_results.get("status") == "cancelling"


def test_cleanup_scan_always_clears_state_lock_and_log_callback(tmp_path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    _write_lock(sm.lock_file, process_id=111)
    sm.is_scanning = True
    sm.log_callback = lambda _line: None
    sm.scan_results = {"status": "running"}

    sm._cleanup_scan()

    assert sm.is_scanning is False
    assert sm.log_callback is None
    assert not sm.lock_file.exists()
    assert "cleanup_time" in sm.scan_results
