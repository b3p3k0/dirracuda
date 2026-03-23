"""
Tests for _ScanManagerProtocolMixin extracted into scan_manager_protocol_mixin.py.

Covers:
- Ownership verification (methods defined on mixin, not ScanManager directly)
- FTP/HTTP startup: state transitions, thread spawning, exception cleanup
- HTTP worker: config overrides, filters/verbose forwarding, context-manager
  skip when no overrides, exception → _handle_scan_error + _cleanup_scan
- FTP startup parity smoke test
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from gui.utils import scan_manager_protocol_mixin as mixin_mod
from gui.utils.scan_manager import ScanManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_start_sm():
    """Minimal ScanManager stub suitable for start_ftp/http_scan tests."""
    sm = ScanManager.__new__(ScanManager)
    sm.is_scan_active = lambda: False
    sm.create_lock_file = lambda *a, **k: True
    sm.remove_lock_file = MagicMock()
    sm._update_progress = MagicMock()
    return sm


def _make_worker_sm():
    """Minimal ScanManager stub suitable for *_scan_worker tests."""
    sm = ScanManager.__new__(ScanManager)
    sm.backend_interface = MagicMock()
    sm.log_callback = None
    sm._update_progress = MagicMock()
    sm._process_scan_results = MagicMock()
    sm._handle_scan_error = MagicMock()
    sm._cleanup_scan = MagicMock()
    sm._handle_backend_progress = MagicMock()
    sm._handle_backend_log_line = MagicMock()

    captured = {}

    @contextmanager
    def _fake_override(overrides):
        captured["overrides"] = overrides
        yield

    sm.backend_interface._temporary_config_override = _fake_override
    sm.backend_interface.run_http_scan = MagicMock(return_value={"success": True})
    sm._captured_overrides = captured
    return sm


# ---------------------------------------------------------------------------
# 1. Ownership — methods must be defined on the mixin, not ScanManager itself
# ---------------------------------------------------------------------------

def test_scan_manager_protocol_methods_owned_by_mixin():
    for name in ("start_ftp_scan", "_ftp_scan_worker", "start_http_scan", "_http_scan_worker"):
        method = getattr(ScanManager, name)
        assert "_ScanManagerProtocolMixin" in method.__qualname__, (
            f"{name}.__qualname__ = {method.__qualname__!r} — "
            "method was not moved to _ScanManagerProtocolMixin"
        )


# ---------------------------------------------------------------------------
# 2. start_http_scan: state + thread
# ---------------------------------------------------------------------------

def test_start_http_scan_sets_protocol_state_and_starts_thread(monkeypatch, tmp_path):
    sm = _make_start_sm()
    started_threads = []

    class _DummyBI:
        def __init__(self, path):
            self.config_path = None

    class _DummyThread:
        def __init__(self, target=None, args=(), daemon=True):
            self.target = target
        def start(self):
            started_threads.append(self)

    monkeypatch.setattr(mixin_mod, "BackendInterface", _DummyBI)
    monkeypatch.setattr(mixin_mod.threading, "Thread", _DummyThread)

    result = sm.start_http_scan(
        scan_options={"country": "US"},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
    )

    assert result is True
    assert sm.is_scanning is True
    assert sm.scan_results["protocol"] == "http"
    assert sm.scan_results["country"] == "US"
    assert len(started_threads) == 1


# ---------------------------------------------------------------------------
# 3. start_http_scan: startup exception → cleanup
# ---------------------------------------------------------------------------

def test_start_http_scan_startup_exception_cleans_up_and_reports(monkeypatch, tmp_path):
    sm = _make_start_sm()

    class _BoomBI:
        def __init__(self, *a):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(mixin_mod, "BackendInterface", _BoomBI)

    result = sm.start_http_scan(
        scan_options={"country": "US"},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
    )

    assert result is False
    assert sm.is_scanning is False
    sm.remove_lock_file.assert_called_once()
    sm._update_progress.assert_called_once()
    msg = sm._update_progress.call_args[0][1]
    assert "Failed to start HTTP scan" in msg


# ---------------------------------------------------------------------------
# 4. start_ftp_scan: startup exception → cleanup (FTP/HTTP symmetry)
# ---------------------------------------------------------------------------

def test_start_ftp_scan_startup_exception_cleans_up_and_reports(monkeypatch, tmp_path):
    sm = _make_start_sm()

    class _BoomBI:
        def __init__(self, *a):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(mixin_mod, "BackendInterface", _BoomBI)

    result = sm.start_ftp_scan(
        scan_options={"country": "US"},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
    )

    assert result is False
    assert sm.is_scanning is False
    sm.remove_lock_file.assert_called_once()
    sm._update_progress.assert_called_once()
    msg = sm._update_progress.call_args[0][1]
    assert "Failed to start FTP scan" in msg


# ---------------------------------------------------------------------------
# 5. _http_scan_worker: config overrides
# ---------------------------------------------------------------------------

def test_http_scan_worker_applies_config_overrides():
    sm = _make_worker_sm()
    scan_options = {
        "country": "US",
        "api_key_override": "MY_KEY",
        "max_shodan_results": 300,
        "discovery_max_concurrent_hosts": 10,
        "access_max_concurrent_hosts": 5,
        "connect_timeout": 3,
        "request_timeout": 7,
        "subdir_timeout": 15,
        "verify_http": True,
        "verify_https": False,
        "allow_insecure_tls": True,
        "bulk_probe_enabled": False,
    }
    sm._http_scan_worker(scan_options)

    overrides = sm._captured_overrides.get("overrides", {})

    assert overrides.get("shodan", {}).get("api_key") == "MY_KEY"
    assert (
        overrides.get("http", {})
        .get("shodan", {})
        .get("query_limits", {})
        .get("max_results")
        == 300
    )
    assert overrides.get("http", {}).get("discovery", {}).get("max_concurrent_hosts") == 10
    assert overrides.get("http", {}).get("access", {}).get("max_concurrent_hosts") == 5

    verif = overrides.get("http", {}).get("verification", {})
    assert verif.get("connect_timeout") == 3
    assert verif.get("request_timeout") == 7
    assert verif.get("subdir_timeout") == 15
    assert verif.get("verify_http") is True
    assert verif.get("verify_https") is False
    assert verif.get("allow_insecure_tls") is True

    assert overrides.get("http", {}).get("bulk_probe_enabled") is False


# ---------------------------------------------------------------------------
# 6. _http_scan_worker: filters and verbose forwarded correctly
# ---------------------------------------------------------------------------

def test_http_scan_worker_passes_filters_and_verbose_to_backend():
    sm = _make_worker_sm()
    sm._http_scan_worker({
        "country": None,
        "verbose": True,
        "custom_filters": 'org:"Test ISP"',
    })

    sm.backend_interface.run_http_scan.assert_called_once()
    _, kwargs = sm.backend_interface.run_http_scan.call_args
    assert kwargs.get("verbose") is True
    assert kwargs.get("filters") == 'org:"Test ISP"'


# ---------------------------------------------------------------------------
# 7. _http_scan_worker: no overrides → context manager not entered
# ---------------------------------------------------------------------------

def test_http_scan_worker_no_overrides_skips_temporary_override_context():
    sm = _make_worker_sm()
    entered = {"called": False}

    @contextmanager
    def _track(overrides):
        entered["called"] = True
        yield

    sm.backend_interface._temporary_config_override = _track
    sm._http_scan_worker({"country": None})
    assert not entered["called"]


# ---------------------------------------------------------------------------
# 8. _http_scan_worker: exception → _handle_scan_error + _cleanup_scan
# ---------------------------------------------------------------------------

def test_http_scan_worker_exception_calls_handle_scan_error_and_cleanup():
    sm = _make_worker_sm()
    err = RuntimeError("scan blew up")
    sm.backend_interface.run_http_scan.side_effect = err

    sm._http_scan_worker({"country": None})

    sm._handle_scan_error.assert_called_once_with(err)
    sm._cleanup_scan.assert_called_once()


# ---------------------------------------------------------------------------
# 9. start_ftp_scan: parity smoke — thread started and protocol set
# ---------------------------------------------------------------------------

def test_start_ftp_scan_still_starts_thread_and_sets_protocol(monkeypatch, tmp_path):
    sm = _make_start_sm()
    started_threads = []

    class _DummyBI:
        def __init__(self, path):
            self.config_path = None

    class _DummyThread:
        def __init__(self, target=None, args=(), daemon=True):
            self.target = target
        def start(self):
            started_threads.append(self)

    monkeypatch.setattr(mixin_mod, "BackendInterface", _DummyBI)
    monkeypatch.setattr(mixin_mod.threading, "Thread", _DummyThread)

    result = sm.start_ftp_scan(
        scan_options={"country": "DE"},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
    )

    assert result is True
    assert sm.scan_results["protocol"] == "ftp"
    assert sm.scan_results["country"] == "DE"
    assert len(started_threads) == 1


# ---------------------------------------------------------------------------
# 10. start_http_scan: already scanning → returns False immediately
# ---------------------------------------------------------------------------

def test_start_http_scan_already_scanning_returns_false(monkeypatch, tmp_path):
    sm = _make_start_sm()
    sm.is_scan_active = lambda: True  # override: scan already running

    result = sm.start_http_scan(
        scan_options={"country": "US"},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
    )

    assert result is False
