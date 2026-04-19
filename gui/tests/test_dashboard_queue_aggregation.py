"""Queue aggregation tests for multi-protocol dashboard scan completion flows."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components import dashboard_scan


class _RecordingParent:
    def __init__(self, run_1s_callbacks: bool = False) -> None:
        self.calls = []
        self._run_1s_callbacks = run_1s_callbacks

    def after(self, ms, fn, *args):
        self.calls.append((ms, fn, args))
        if self._run_1s_callbacks and ms == 1000:
            fn(*args)
        return None


def _noop_messagebox():
    return SimpleNamespace(
        showinfo=lambda *a, **k: None,
        showwarning=lambda *a, **k: None,
        showerror=lambda *a, **k: None,
    )


def test_handle_queued_scan_completion_aggregates_multi_protocol_results(monkeypatch):
    """Queue completion should show one combined probe summary + one combined scan summary."""
    monkeypatch.setattr(dashboard_scan, "_mb", _noop_messagebox)

    parent = _RecordingParent(run_1s_callbacks=False)
    dash = SimpleNamespace()
    dash.parent = parent
    dash._queued_scan_active = True
    dash._queued_scan_protocols = ["ftp", "http"]
    dash._queued_scan_current_protocol = "smb"
    dash._queued_scan_failures = []
    dash._queued_scan_results = []
    dash._queued_scan_batch_rows = {"probe": [], "extract": []}
    dash._show_batch_summary = MagicMock()
    dash._show_scan_results = MagicMock()
    dash._reset_scan_status = MagicMock()
    dash._launch_next_queued_scan = MagicMock()
    dash._clear_queued_scan_state = MagicMock(
        side_effect=lambda: dashboard_scan.clear_queued_scan_state(dash)
    )

    smb_result = {
        "protocol": "smb",
        "status": "completed",
        "success": True,
        "hosts_scanned": 10,
        "accessible_hosts": 2,
        "shares_found": 3,
        "start_time": "2026-04-19T10:00:00",
        "end_time": "2026-04-19T10:05:00",
        "_batch_summary_payload": {
            "probe": [
                {
                    "ip_address": "198.51.100.10",
                    "protocol": "SMB",
                    "action": "probe",
                    "status": "success",
                    "notes": "ok",
                }
            ],
            "extract": [],
        },
    }
    dashboard_scan.handle_queued_scan_completion(dash, smb_result)

    dash._queued_scan_current_protocol = "ftp"
    dash._queued_scan_protocols = ["http"]
    ftp_result = {
        "protocol": "ftp",
        "status": "completed",
        "success": True,
        "hosts_scanned": 7,
        "accessible_hosts": 1,
        "shares_found": 2,
        "start_time": "2026-04-19T10:05:00",
        "end_time": "2026-04-19T10:11:00",
        "_batch_summary_payload": {
            "probe": [
                {
                    "ip_address": "198.51.100.20",
                    "protocol": "FTP",
                    "action": "probe",
                    "status": "success",
                    "notes": "ok",
                }
            ],
            "extract": [],
        },
    }
    dashboard_scan.handle_queued_scan_completion(dash, ftp_result)

    dash._queued_scan_current_protocol = "http"
    dash._queued_scan_protocols = []
    http_result = {
        "protocol": "http",
        "status": "completed",
        "success": True,
        "hosts_scanned": 4,
        "accessible_hosts": 3,
        "shares_found": 5,
        "start_time": "2026-04-19T10:11:00",
        "end_time": "2026-04-19T10:16:00",
        "_batch_summary_payload": {
            "probe": [
                {
                    "ip_address": "198.51.100.30",
                    "protocol": "HTTP",
                    "action": "probe",
                    "status": "success",
                    "notes": "ok",
                }
            ],
            "extract": [],
        },
    }
    dashboard_scan.handle_queued_scan_completion(dash, http_result)

    dash._show_batch_summary.assert_called_once()
    combined_probe_rows = dash._show_batch_summary.call_args[0][0]
    assert len(combined_probe_rows) == 3
    assert {row["protocol"] for row in combined_probe_rows} == {"SMB", "FTP", "HTTP"}

    dash._show_scan_results.assert_called_once()
    combined_scan = dash._show_scan_results.call_args[0][0]
    assert combined_scan["protocol"] == "multi"
    assert combined_scan["protocols"] == ["smb", "ftp", "http"]
    assert combined_scan["hosts_scanned"] == 21
    assert combined_scan["accessible_hosts"] == 6
    assert combined_scan["shares_found"] == 10
    assert combined_scan["start_time"] == "2026-04-19T10:00:00"
    assert combined_scan["end_time"] == "2026-04-19T10:16:00"
    assert combined_scan["duration_seconds"] == 960.0

    dash._clear_queued_scan_state.assert_called_once()
    # Two queue-advance timers + one delayed reset timer at finalization.
    assert [ms for ms, *_ in parent.calls].count(150) == 2
    assert [ms for ms, *_ in parent.calls].count(5000) == 1


def test_monitor_scan_completion_queues_final_batch_payload_without_dialogs(monkeypatch):
    """Queued final protocol must suppress per-protocol dialogs and pass payload to queue handler."""
    monkeypatch.setattr(dashboard_scan, "_mb", _noop_messagebox)

    parent = _RecordingParent(run_1s_callbacks=True)
    scan_results = {
        "protocol": "http",
        "status": "completed",
        "success": True,
        "hosts_scanned": 3,
        "accessible_hosts": 2,
        "shares_found": 1,
        "start_time": "2026-04-19T11:00:00",
        "end_time": "2026-04-19T11:03:00",
    }
    payload = {"probe": [{"ip_address": "203.0.113.8", "protocol": "HTTP"}], "extract": []}

    dash = SimpleNamespace()
    dash.parent = parent
    dash.scan_button_state = "scanning"
    dash.stopping_started_time = None
    dash.scan_manager = SimpleNamespace(
        is_scanning=False,
        get_scan_results=lambda: dict(scan_results),
    )
    dash._queued_scan_active = True
    dash._queued_scan_protocols = []
    dash.current_scan_options = {"bulk_probe_enabled": True, "bulk_extract_enabled": False}

    dash._update_scan_button_state = MagicMock()
    dash._log_status_event = MagicMock()
    dash._reset_scan_status = MagicMock()
    dash._run_post_scan_batch_operations = MagicMock(return_value=payload)
    dash._show_scan_results = MagicMock()
    dash._refresh_after_scan_completion = MagicMock()
    dash._handle_queued_scan_completion = MagicMock()

    dashboard_scan.monitor_scan_completion(dash)

    dash._run_post_scan_batch_operations.assert_called_once()
    _, kwargs = dash._run_post_scan_batch_operations.call_args
    assert kwargs["show_dialogs"] is False
    assert kwargs["schedule_reset"] is False
    dash._show_scan_results.assert_not_called()

    dash._handle_queued_scan_completion.assert_called_once()
    queued_arg = dash._handle_queued_scan_completion.call_args[0][0]
    assert queued_arg["_batch_summary_payload"] == payload


def test_monitor_scan_completion_nonqueued_keeps_existing_dialog_flags(monkeypatch):
    """Non-queued scans should preserve existing post-scan dialog behavior."""
    monkeypatch.setattr(dashboard_scan, "_mb", _noop_messagebox)

    parent = _RecordingParent(run_1s_callbacks=True)
    scan_results = {
        "protocol": "smb",
        "status": "completed",
        "success": True,
        "hosts_scanned": 5,
        "accessible_hosts": 1,
        "shares_found": 2,
    }

    dash = SimpleNamespace()
    dash.parent = parent
    dash.scan_button_state = "scanning"
    dash.stopping_started_time = None
    dash.scan_manager = SimpleNamespace(
        is_scanning=False,
        get_scan_results=lambda: dict(scan_results),
    )
    dash._queued_scan_active = False
    dash._queued_scan_protocols = []
    dash.current_scan_options = {"bulk_probe_enabled": True, "bulk_extract_enabled": False}

    dash._update_scan_button_state = MagicMock()
    dash._log_status_event = MagicMock()
    dash._reset_scan_status = MagicMock()
    dash._run_post_scan_batch_operations = MagicMock(return_value={"probe": [], "extract": []})
    dash._show_scan_results = MagicMock()
    dash._refresh_after_scan_completion = MagicMock()
    dash._handle_queued_scan_completion = MagicMock()

    dashboard_scan.monitor_scan_completion(dash)

    dash._run_post_scan_batch_operations.assert_called_once()
    _, kwargs = dash._run_post_scan_batch_operations.call_args
    assert kwargs["show_dialogs"] is True
    assert kwargs["schedule_reset"] is True
    dash._handle_queued_scan_completion.assert_not_called()
