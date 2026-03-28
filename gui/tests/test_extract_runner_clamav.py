"""C3 tests: ClamAV post-processor integration in extract_runner.

All tests are unit-level: no real ClamAV binary, no real SMB network.

Groups:
  - Sanitizer tests (14-16)
  - Disabled-path tests (1-3)
  - Precedence rule test (5)
  - Enabled-path / accumulator tests (6-9)
  - Fail-open tests (8-10)
  - Wiring tests: dashboard (11), batch.py (12), batch_operations.py (13)
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from shared.clamav_scanner import ScanResult
from shared.quarantine_postprocess import PostProcessInput, PostProcessResult
from gui.utils.extract_runner import (
    _sanitize_clamav_config,
    build_clamav_post_processor,
    run_extract,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_scan_result(verdict: str, backend: str = "clamscan", signature: str = None, error: str = None) -> ScanResult:
    return ScanResult(
        verdict=verdict,
        backend_used=backend,
        signature=signature,
        exit_code=0 if verdict == "clean" else (1 if verdict == "infected" else 2),
        raw_output="",
        error=error,
    )


def _fake_conn():
    """Fake SMBConnection serving one file: pub/a.txt (3 bytes)."""
    entry = MagicMock()
    entry.get_longname.return_value = "a.txt"
    entry.is_directory.return_value = False
    entry.get_filesize.return_value = 3
    entry.get_mtime_epoch.return_value = None

    conn = MagicMock()
    conn.listPath.return_value = [entry]
    conn.getFile.side_effect = lambda share, smb_path, writer: writer(b"abc")
    return conn


def _run_extract(tmp_path, monkeypatch, **kwargs):
    """Call run_extract with a monkeypatched SMBConnection."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    return run_extract(
        "1.2.3.4",
        ["pub"],
        download_dir=download_dir,
        username="",
        password="",
        max_total_bytes=0,
        max_file_bytes=0,
        max_file_count=0,
        max_seconds=0,
        max_depth=3,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        **kwargs,
    ), download_dir


# ---------------------------------------------------------------------------
# Sanitizer tests
# ---------------------------------------------------------------------------

def test_sanitize_enabled_string_false():
    assert _sanitize_clamav_config({"enabled": "false"})["enabled"] is False


def test_sanitize_enabled_string_false_uppercase():
    assert _sanitize_clamav_config({"enabled": "False"})["enabled"] is False


def test_sanitize_enabled_string_true():
    assert _sanitize_clamav_config({"enabled": "true"})["enabled"] is True


def test_sanitize_enabled_bool_true():
    assert _sanitize_clamav_config({"enabled": True})["enabled"] is True


def test_sanitize_enabled_bool_false():
    assert _sanitize_clamav_config({"enabled": False})["enabled"] is False


def test_sanitize_timeout_clamped_to_minimum_zero():
    assert _sanitize_clamav_config({"enabled": True, "timeout_seconds": 0})["timeout_seconds"] == 1


def test_sanitize_timeout_clamped_to_minimum_negative():
    assert _sanitize_clamav_config({"enabled": True, "timeout_seconds": -5})["timeout_seconds"] == 1


def test_sanitize_timeout_invalid_string_falls_back():
    assert _sanitize_clamav_config({"enabled": True, "timeout_seconds": "abc"})["timeout_seconds"] == 60


def test_sanitize_non_dict_returns_empty():
    assert _sanitize_clamav_config("not-a-dict") == {}
    assert _sanitize_clamav_config(None) == {}
    assert _sanitize_clamav_config(42) == {}


# ---------------------------------------------------------------------------
# Disabled-path tests
# ---------------------------------------------------------------------------

def test_disabled_path_no_clamav_config(tmp_path, monkeypatch):
    summary, _ = _run_extract(tmp_path, monkeypatch)
    assert summary["clamav"] == {"enabled": False}


def test_disabled_path_explicit_false(tmp_path, monkeypatch):
    summary, _ = _run_extract(tmp_path, monkeypatch, clamav_config={"enabled": False})
    assert summary["clamav"] == {"enabled": False}


def test_non_dict_clamav_config_disabled(tmp_path, monkeypatch):
    summary, _ = _run_extract(tmp_path, monkeypatch, clamav_config="not-a-dict")
    assert summary["clamav"] == {"enabled": False}


# ---------------------------------------------------------------------------
# Precedence: explicit post_processor wins over clamav_config
# ---------------------------------------------------------------------------

def test_precedence_post_processor_wins(tmp_path, monkeypatch):
    """Explicit post_processor takes precedence; build_clamav_post_processor must not run."""
    builder_called = []

    def spy_processor(inp: PostProcessInput) -> PostProcessResult:
        return PostProcessResult(
            final_path=inp.file_path,
            verdict="skipped",
            moved=False,
            destination="quarantine",
            metadata=None,
            error=None,
        )

    original_builder = __import__(
        "gui.utils.extract_runner", fromlist=["build_clamav_post_processor"]
    ).build_clamav_post_processor

    def spy_builder(cfg):
        builder_called.append(cfg)
        return original_builder(cfg)

    monkeypatch.setattr("gui.utils.extract_runner.build_clamav_post_processor", spy_builder)

    summary, _ = _run_extract(
        tmp_path, monkeypatch,
        post_processor=spy_processor,
        clamav_config={"enabled": True, "backend": "clamscan"},
    )
    # clamav_config ignored because post_processor was provided
    assert summary["clamav"] == {"enabled": False}
    assert builder_called == [], "build_clamav_post_processor must not be called when post_processor is set"


# ---------------------------------------------------------------------------
# Enabled-path accumulator tests
# ---------------------------------------------------------------------------

def test_enabled_clean_verdict_updates_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    clean_result = _make_scan_result("clean", backend="clamscan")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: clean_result,
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan",
                       "extracted_root": str(tmp_path / "extracted")},
    )

    cv = summary["clamav"]
    assert cv["enabled"] is True
    assert cv["files_scanned"] == 1
    assert cv["clean"] == 1
    assert cv["infected"] == 0
    assert cv["errors"] == 0
    assert cv["backend_used"] == "clamscan"
    assert cv["promoted"] == 1
    assert cv["infected_items"] == []
    assert cv["error_items"] == []
    # File was moved to extracted root
    assert (tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "a.txt").exists()
    assert not (download_dir / "pub" / "a.txt").exists()


def test_enabled_infected_verdict_updates_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    infected_result = _make_scan_result("infected", backend="clamscan", signature="Eicar-Test-Signature")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: infected_result,
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan",
                       "extracted_root": str(tmp_path / "extracted")},
    )

    cv = summary["clamav"]
    assert cv["infected"] == 1
    assert cv["clean"] == 0
    assert len(cv["infected_items"]) == 1
    assert cv["infected_items"][0]["signature"] == "Eicar-Test-Signature"
    assert cv["known_bad_moved"] == 1
    # File moved to known_bad subtree under quarantine root
    assert (tmp_path / "q" / "known_bad" / "1.2.3.4" / "20260328" / "pub" / "a.txt").exists()


def test_scanner_error_failopen_does_not_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    error_result = _make_scan_result("error", backend="clamscan", error="scanner timeout: 60s")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: error_result,
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan"},
    )

    # File still recorded in summary (extraction completed)
    assert len(summary["files"]) == 1
    cv = summary["clamav"]
    assert cv["errors"] == 1
    assert len(cv["error_items"]) == 1
    assert "scanner timeout" in cv["error_items"][0]["error"]


def test_scanner_exception_failopen(tmp_path, monkeypatch):
    """Processor raises an exception: file stays in quarantine, clamav errors incremented."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan"},
    )

    # Extraction completed; file at original quarantine path
    assert len(summary["files"]) == 1
    expected = str(download_dir / "pub" / "a.txt")
    assert summary["files"][0]["saved_to"] == expected

    # Both the top-level errors list and clamav block record the failure
    pp_errors = [e for e in summary["errors"] if "post_processor error" in e.get("message", "")]
    assert pp_errors
    assert "boom" in pp_errors[0]["message"]
    assert summary["clamav"]["errors"] == 1
    assert summary["clamav"]["error_items"][0]["error"] == "boom"


def test_init_failure_failopen(tmp_path, monkeypatch):
    """build_clamav_post_processor raises: init error recorded, extraction unaffected."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    monkeypatch.setattr(
        "gui.utils.extract_runner.build_clamav_post_processor",
        lambda cfg, **kw: (_ for _ in ()).throw(RuntimeError("init-fail")),
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan"},
    )

    assert len(summary["files"]) == 1  # extraction still completed
    cv = summary["clamav"]
    assert cv["enabled"] is True
    assert cv["errors"] == 1
    assert cv["error_items"][0]["path"] == "(clamav-init)"
    assert "init-fail" in cv["error_items"][0]["error"]


def test_invalid_timeout_does_not_crash(tmp_path, monkeypatch):
    """Invalid timeout_seconds is sanitized; extraction and scan complete without error."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    clean_result = _make_scan_result("clean", backend="clamscan")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: clean_result,
    )

    download_dir = tmp_path / "q" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "timeout_seconds": "abc"},
    )
    assert summary["clamav"]["enabled"] is True
    assert summary["clamav"]["files_scanned"] == 1


# ---------------------------------------------------------------------------
# Wiring tests: dashboard _extract_single_server
# ---------------------------------------------------------------------------

def test_dashboard_single_server_forwards_clamav_config(tmp_path, monkeypatch):
    """_extract_single_server passes clamav_config kwarg to run_extract."""
    captured: Dict[str, Any] = {}

    def fake_run_extract(*a, **kw):
        captured.update(kw)
        return {
            "totals": {"files_downloaded": 0, "bytes_downloaded": 0},
            "files": [], "errors": [], "timed_out": False, "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr("gui.utils.extract_runner.run_extract", fake_run_extract)
    monkeypatch.setattr(
        "gui.components.dashboard.create_quarantine_dir",
        lambda *a, **kw: tmp_path,
    )

    from gui.components.dashboard import DashboardWidget
    obj = object.__new__(DashboardWidget)
    obj.db_reader = None

    server = {
        "ip_address": "1.2.3.4",
        "accessible_shares_list": "pub",
        "auth_method": "anonymous",
    }
    clamav_config = {"enabled": True, "backend": "clamscan"}

    obj._extract_single_server(
        server, 50, 200, 300, 10, "allow_only", [], [], None,
        threading.Event(), clamav_config,
    )

    assert "clamav_config" in captured
    assert captured["clamav_config"] == clamav_config


# ---------------------------------------------------------------------------
# Wiring tests: batch.py _execute_extract_target
# ---------------------------------------------------------------------------

def test_batch_execute_extract_forwards_clamav_config_from_options(tmp_path, monkeypatch):
    """_execute_extract_target forwards options['clamav_config'] to run_extract."""
    captured: Dict[str, Any] = {}

    def fake_run_extract(*a, **kw):
        captured.update(kw)
        return {
            "totals": {"files_downloaded": 0, "bytes_downloaded": 0},
            "files": [], "errors": [], "timed_out": False, "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr("gui.utils.extract_runner.run_extract", fake_run_extract)
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.create_quarantine_dir",
        lambda *a, **kw: tmp_path,
    )

    from gui.components.server_list_window.actions.batch import ServerListWindowBatchMixin
    obj = object.__new__(ServerListWindowBatchMixin)
    obj.settings_manager = None
    obj.active_jobs = {"job1": {"dialog": None, "total": 1}}
    obj.window = MagicMock()
    obj._handle_extracted_update = lambda *a, **kw: None
    obj._update_batch_status_dialog = lambda *a, **kw: None

    clamav_config = {"enabled": True, "backend": "clamscan"}
    options = {
        "max_total_size_mb": 200,
        "max_file_size_mb": 50,
        "max_files_per_target": 10,
        "max_time_seconds": 300,
        "max_directory_depth": 3,
        "included_extensions": [],
        "excluded_extensions": [],
        "download_delay_seconds": 0,
        "connection_timeout": 30,
        "download_path": str(tmp_path),
        "clamav_config": clamav_config,
    }
    target = {
        "ip_address": "1.2.3.4",
        "host_type": "S",
        "row_key": "1",
        "shares": ["pub"],
        "auth_method": "anonymous",
    }

    obj._execute_extract_target("job1", target, options, threading.Event())

    assert "clamav_config" in captured
    assert captured["clamav_config"] == clamav_config


# ---------------------------------------------------------------------------
# Wiring tests: batch_operations.py _launch_extract_workflow
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regression: dashboard _execute_batch_extract must forward non-empty clamav_cfg
# ---------------------------------------------------------------------------

def test_execute_batch_extract_forwards_nonempty_clamav_cfg(tmp_path, monkeypatch):
    """_execute_batch_extract must pass clamav_config from config file to _extract_single_server."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"clamav": {"enabled": True, "backend": "clamscan"}}),
        encoding="utf-8",
    )

    captured_clamav: list = []

    def fake_extract_single_server(self_unused, server, *args):
        # clamav_config is the last positional arg
        captured_clamav.append(args[-1])
        return {"ip_address": server.get("ip_address"), "action": "extract", "status": "success", "notes": "0 file(s)"}

    monkeypatch.setattr(
        "gui.components.dashboard.DashboardWidget._extract_single_server",
        fake_extract_single_server,
    )

    # Stub Tkinter widgets so no display is needed
    fake_toplevel = MagicMock()
    fake_label = MagicMock()
    fake_bar = MagicMock()
    fake_bar.__setitem__ = lambda self, k, v: None
    fake_button = MagicMock()
    monkeypatch.setattr("gui.components.dashboard.tk.Toplevel", lambda *a, **kw: fake_toplevel)
    monkeypatch.setattr("gui.components.dashboard.tk.Label", lambda *a, **kw: fake_label)
    monkeypatch.setattr("gui.components.dashboard.ttk.Progressbar", lambda *a, **kw: fake_bar)
    monkeypatch.setattr("gui.components.dashboard.tk.Button", lambda *a, **kw: fake_button)

    from gui.components.dashboard import DashboardWidget
    obj = object.__new__(DashboardWidget)
    obj.parent = MagicMock()
    obj.theme = MagicMock()
    obj.db_reader = None
    obj.settings_manager = MagicMock()
    obj.settings_manager.get_setting = lambda key, default=None: {
        "extract.batch_max_workers": 1,
        "extract.max_file_size_mb": 50,
        "extract.max_total_size_mb": 200,
        "extract.max_time_seconds": 300,
        "extract.max_files_per_target": 10,
        "extract.extension_mode": "allow_only",
        "backend.config_path": str(config_file),
    }.get(key, default)

    servers = [{"ip_address": "1.2.3.4", "accessible_shares_list": "pub", "auth_method": "anonymous"}]
    obj._execute_batch_extract(servers)

    assert captured_clamav, "_extract_single_server was not called"
    forwarded = captured_clamav[0]
    assert isinstance(forwarded, dict), f"Expected dict, got {forwarded!r}"
    assert forwarded.get("enabled") is True, f"Expected enabled=True, got {forwarded!r}"


def test_launch_extract_workflow_injects_clamav_config(tmp_path, monkeypatch):
    """_launch_extract_workflow loads clamav config once and injects into options."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"clamav": {"enabled": True, "backend": "clamscan"}}), encoding="utf-8")

    captured_options: Dict[str, Any] = {}

    def fake_start_batch_job(job_type, targets, options):
        captured_options.update(options)

    dummy_dialog_result = {
        "worker_count": 1,
        "download_path": str(tmp_path),
        "max_file_size_mb": 50,
        "max_total_size_mb": 200,
        "max_time_seconds": 300,
        "max_files_per_target": 10,
        "max_directory_depth": 3,
        "download_delay_seconds": 0,
        "included_extensions": [],
        "excluded_extensions": [],
        "connection_timeout": 30,
        "extension_mode": "allow_only",
    }

    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch_operations.BatchExtractSettingsDialog",
        MagicMock(return_value=MagicMock(show=MagicMock(return_value=dummy_dialog_result))),
    )

    from gui.components.server_list_window.actions.batch_operations import ServerListWindowBatchOperationsMixin
    obj = object.__new__(ServerListWindowBatchOperationsMixin)
    obj.window = MagicMock()
    obj.theme = MagicMock()
    obj.settings_manager = MagicMock()
    obj._start_batch_job = fake_start_batch_job
    obj._get_config_path = lambda: str(config_file)
    obj._open_config_editor = lambda: None

    targets = [{"ip_address": "1.2.3.4"}]
    obj._launch_extract_workflow(targets)

    assert "clamav_config" in captured_options
    assert captured_options["clamav_config"].get("enabled") is True


# ---------------------------------------------------------------------------
# C4 seam tests: promotion routing
# ---------------------------------------------------------------------------

def _run_extract_c4(tmp_path, monkeypatch, verdict: str, **extra_clamav):
    """Helper: run run_extract with a mocked scan verdict and routing roots in tmp_path."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    scan_result = _make_scan_result(verdict, backend="clamscan",
                                    error="scan-error" if verdict == "error" else None)
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: scan_result,
    )
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    clamav_config = {
        "enabled": True,
        "backend": "clamscan",
        "extracted_root": str(tmp_path / "extracted"),
        **extra_clamav,
    }
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config=clamav_config,
    )
    return summary, download_dir


def test_c4_clean_file_promoted_to_extracted(tmp_path, monkeypatch):
    summary, download_dir = _run_extract_c4(tmp_path, monkeypatch, "clean")
    cv = summary["clamav"]
    assert cv["promoted"] == 1
    assert cv["clean"] == 1
    assert cv["errors"] == 0
    target = tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "a.txt"
    assert target.exists(), f"expected file at {target}"
    assert not (download_dir / "pub" / "a.txt").exists()
    assert summary["files"][0]["saved_to"] == str(target)


def test_c4_infected_file_moved_to_known_bad(tmp_path, monkeypatch):
    summary, download_dir = _run_extract_c4(tmp_path, monkeypatch, "infected")
    cv = summary["clamav"]
    assert cv["known_bad_moved"] == 1
    assert cv["infected"] == 1
    target = tmp_path / "quarantine" / "known_bad" / "1.2.3.4" / "20260328" / "pub" / "a.txt"
    assert target.exists(), f"expected file at {target}"
    assert not (download_dir / "pub" / "a.txt").exists()
    assert summary["files"][0]["saved_to"] == str(target)


def test_c4_error_verdict_stays_in_quarantine(tmp_path, monkeypatch):
    summary, download_dir = _run_extract_c4(tmp_path, monkeypatch, "error")
    cv = summary["clamav"]
    assert cv["errors"] == 1
    assert cv["promoted"] == 0
    # File stays at original quarantine location
    expected = str(download_dir / "pub" / "a.txt")
    assert summary["files"][0]["saved_to"] == expected


def test_c4_move_failure_recorded_in_error_items(tmp_path, monkeypatch):
    """safe_move raises → error surfaced in error_items; scan counter still increments."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    clean_result = _make_scan_result("clean", backend="clamscan")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: clean_result,
    )
    monkeypatch.setattr(
        "gui.utils.extract_runner.safe_move",
        lambda src, dest: (_ for _ in ()).throw(OSError("disk full")),
    )
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan",
                       "extracted_root": str(tmp_path / "extracted")},
    )
    cv = summary["clamav"]
    assert cv["clean"] == 1
    assert cv["promoted"] == 0
    assert cv["errors"] == 1
    assert len(cv["error_items"]) == 1
    assert "disk full" in cv["error_items"][0]["error"]
    # File stays at original quarantine path (fail-open)
    assert (download_dir / "pub" / "a.txt").exists()


def test_c4_resolve_raises_caught_in_accumulator(tmp_path, monkeypatch):
    """resolve_promotion_dest raises → inner try/except catches it; scan counter still increments."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    clean_result = _make_scan_result("clean", backend="clamscan")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: clean_result,
    )
    monkeypatch.setattr(
        "gui.utils.extract_runner.resolve_promotion_dest",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("path mismatch")),
    )
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan",
                       "extracted_root": str(tmp_path / "extracted")},
    )
    cv = summary["clamav"]
    # Scan counter still increments (inner try/except prevents outer-seam bypass)
    assert cv["clean"] == 1
    assert cv["promoted"] == 0
    assert cv["errors"] == 1
    assert len(cv["error_items"]) == 1
    assert "path mismatch" in cv["error_items"][0]["error"]


def test_c4_collision_resolved(tmp_path, monkeypatch):
    """Pre-existing file at destination gets _1 suffix."""
    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    clean_result = _make_scan_result("clean", backend="clamscan")
    monkeypatch.setattr(
        "shared.clamav_scanner.ClamAVScanner.scan_file",
        lambda self, path: clean_result,
    )
    # Pre-create the expected destination
    target = tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "a.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already here")

    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    summary = run_extract(
        "1.2.3.4", ["pub"],
        download_dir=download_dir,
        username="", password="",
        max_total_bytes=0, max_file_bytes=0, max_file_count=0,
        max_seconds=0, max_depth=3,
        allowed_extensions=[], denied_extensions=[],
        delay_seconds=0, connection_timeout=5,
        clamav_config={"enabled": True, "backend": "clamscan",
                       "extracted_root": str(tmp_path / "extracted")},
    )
    cv = summary["clamav"]
    assert cv["promoted"] == 1
    assert cv["errors"] == 0
    # Collision: file saved as a_1.txt
    assert summary["files"][0]["saved_to"].endswith("a_1.txt")
    assert (tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "a_1.txt").exists()
    assert target.read_bytes() == b"already here"
