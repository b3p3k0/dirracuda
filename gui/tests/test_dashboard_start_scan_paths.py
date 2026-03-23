"""Regression tests for dashboard scan-start backend path contract.

Guards two intentional behaviors in _start_new_scan:
  1. backend_path fallback defaults to "." when backend_interface has no backend_path attr.
  2. Diagnostics error message references "cli/smbseek.py" (not root "smbseek").
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


def _make_dash():
    """Create a minimal DashboardWidget stub bypassing __init__."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.scan_button_state = "idle"
    dash.config_path = None
    dash._check_external_scans = MagicMock()
    dash._reset_log_output = MagicMock()
    dash._update_scan_button_state = MagicMock()
    dash._show_scan_progress = MagicMock()
    dash._monitor_scan_completion = MagicMock()

    sm = MagicMock()
    sm.is_scanning = False
    dash.scan_manager = sm
    return dash


def test_start_new_scan_passes_backend_path_to_scan_manager():
    """scan_manager.start_scan must receive the exact string from backend_interface.backend_path."""
    dash = _make_dash()

    bi = MagicMock()
    bi.backend_path = "/some/known/path"
    dash.backend_interface = bi
    dash.scan_manager.start_scan.return_value = True

    dash._start_new_scan({"country": "US"})

    dash.scan_manager.start_scan.assert_called_once()
    _, kwargs = dash.scan_manager.start_scan.call_args
    assert kwargs["backend_path"] == "/some/known/path"


def test_start_new_scan_error_mentions_cli_smbseek_path(monkeypatch, tmp_path):
    """Error dialog must include 'cli/smbseek.py' when that path does not exist."""
    dash = _make_dash()

    missing_backend = tmp_path / "missing_backend"  # never created — guaranteed absent
    bi = MagicMock()
    bi.backend_path = missing_backend
    dash.backend_interface = bi
    dash.scan_manager.start_scan.return_value = False
    dash.scan_manager.is_scanning = False

    captured = {}

    def _capture_showerror(title, msg):
        captured["title"] = title
        captured["msg"] = msg

    monkeypatch.setattr("gui.components.dashboard_scan_orchestration.messagebox.showerror", _capture_showerror)

    dash._start_new_scan({"country": "US"})

    assert "msg" in captured, "messagebox.showerror was not called"
    assert "cli/smbseek.py" in captured["msg"]


def test_start_new_scan_fallback_backend_path_defaults_to_dot_when_missing(monkeypatch):
    """When backend_interface has no backend_path attr, fallback '.' is used.

    Uses a controlled os.path.exists to avoid false-negative when ./cli/smbseek.py
    happens to exist in the repo root.
    """
    dash = _make_dash()

    # Plain object — no backend_path attribute triggers the getattr fallback
    dash.backend_interface = object()
    dash.scan_manager.start_scan.return_value = False
    dash.scan_manager.is_scanning = False

    captured = {}

    def _capture_showerror(title, msg):
        captured["title"] = title
        captured["msg"] = msg

    monkeypatch.setattr("gui.components.dashboard_scan_orchestration.messagebox.showerror", _capture_showerror)

    # Make "." appear to exist but cli/smbseek.py does not — deterministic regardless of cwd
    def _fake_exists(p):
        p = str(p)
        if "cli" in p:
            return False
        if p == ".":
            return True
        return False

    monkeypatch.setattr("gui.components.dashboard_scan_orchestration.os.path.exists", _fake_exists)

    dash._start_new_scan({"country": "US"})

    # Fallback contract: start_scan must have received backend_path="."
    dash.scan_manager.start_scan.assert_called_once()
    _, kwargs = dash.scan_manager.start_scan.call_args
    assert kwargs["backend_path"] == ".", f"Expected '.', got {kwargs['backend_path']!r}"

    # Stable failure path: dialog is shown
    assert "msg" in captured, "messagebox.showerror was not called"

    # cli/smbseek.py must appear in the error — path built from "." fallback
    assert "cli/smbseek.py" in captured["msg"], (
        f"Expected 'cli/smbseek.py' in error message, got: {captured['msg']!r}"
    )
