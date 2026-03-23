"""Tests for _DashboardScanOrchestrationMixin.

Covers queued multi-protocol scan management and protocol-option building.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


def _make_dash():
    """Create a minimal DashboardWidget stub bypassing __init__."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash._queued_scan_active = False
    dash._queued_scan_protocols = []
    dash._queued_scan_common_options = None
    dash._queued_scan_current_protocol = None
    dash._queued_scan_failures = []
    dash.scan_button_state = "idle"
    dash.config_path = None
    dash._check_external_scans = MagicMock()
    dash._reset_log_output = MagicMock()
    dash._update_scan_button_state = MagicMock()
    dash._show_scan_progress = MagicMock()
    dash._monitor_scan_completion = MagicMock()
    dash._start_ftp_scan = MagicMock(return_value=True)
    dash._start_http_scan = MagicMock(return_value=True)
    sm = MagicMock()
    sm.is_scanning = False
    sm.start_scan.return_value = True
    dash.scan_manager = sm
    bi = MagicMock()
    bi.backend_path = "/fake/path"
    dash.backend_interface = bi
    return dash


# ---------------------------------------------------------------------------
# _start_unified_scan
# ---------------------------------------------------------------------------

def test_start_unified_scan_single_protocol_starts_directly():
    """Single protocol bypasses queue and calls scan start immediately."""
    dash = _make_dash()
    dash._start_new_scan = MagicMock(return_value=True)

    dash._start_unified_scan({"protocols": ["smb"], "country": "US"})

    dash._start_new_scan.assert_called_once()
    assert dash._queued_scan_active is False


def test_start_unified_scan_multi_protocol_queues_and_launches_first():
    """Multi-protocol request sets queue active and pops the first protocol."""
    dash = _make_dash()
    launched = []

    def _fake_start_new_scan(opts):
        launched.append("smb")
        return True

    dash._start_new_scan = _fake_start_new_scan

    dash._start_unified_scan({"protocols": ["smb", "ftp"], "country": "DE"})

    # Queue was activated and first protocol was launched
    assert "smb" in launched
    # FTP remains queued (not yet launched, waiting for completion callback)
    assert dash._queued_scan_active is True
    assert "ftp" in dash._queued_scan_protocols


def test_start_unified_scan_no_protocol_shows_error():
    """Empty protocol list triggers showerror and returns without starting."""
    dash = _make_dash()
    dash._start_new_scan = MagicMock()

    with patch("gui.components.dashboard_scan_orchestration.messagebox.showerror") as mock_err:
        dash._start_unified_scan({"protocols": []})

    mock_err.assert_called_once()
    dash._start_new_scan.assert_not_called()
    assert dash._queued_scan_active is False


# ---------------------------------------------------------------------------
# _launch_next_queued_scan
# ---------------------------------------------------------------------------

def test_launch_next_queued_scan_empty_queue_clears_state():
    """Empty queue without failures clears state silently."""
    dash = _make_dash()
    dash._queued_scan_active = True
    dash._queued_scan_protocols = []
    dash._queued_scan_failures = []

    dash._launch_next_queued_scan()

    assert dash._queued_scan_active is False


def test_launch_next_queued_scan_empty_queue_with_failures_shows_warning():
    """Empty queue with recorded failures shows a warning dialog."""
    dash = _make_dash()
    dash._queued_scan_active = True
    dash._queued_scan_protocols = []
    dash._queued_scan_failures = [{"protocol": "ftp", "reason": "refused"}]

    with patch("gui.components.dashboard_scan_orchestration.messagebox.showwarning") as mock_warn:
        dash._launch_next_queued_scan()

    mock_warn.assert_called_once()
    assert dash._queued_scan_active is False


# ---------------------------------------------------------------------------
# _handle_queued_scan_completion
# ---------------------------------------------------------------------------

def test_handle_queued_scan_completion_cancelled_clears_queue():
    """Cancelled status stops queue and clears all state."""
    dash = _make_dash()
    dash._queued_scan_active = True
    dash._queued_scan_protocols = ["ftp"]
    dash._queued_scan_current_protocol = "smb"

    with patch("gui.components.dashboard_scan_orchestration.messagebox.showinfo"):
        dash._handle_queued_scan_completion({"status": "cancelled"})

    assert dash._queued_scan_active is False
    assert dash._queued_scan_protocols == []


def test_handle_queued_scan_completion_inactive_is_noop():
    """When queue is not active, completion callback does nothing."""
    dash = _make_dash()
    dash._queued_scan_active = False
    dash._launch_next_queued_scan = MagicMock()

    dash._handle_queued_scan_completion({"status": "success", "success": True})

    dash._launch_next_queued_scan.assert_not_called()


# ---------------------------------------------------------------------------
# _build_protocol_scan_options
# ---------------------------------------------------------------------------

def test_build_protocol_scan_options_smb_includes_security_mode():
    """SMB options must carry the security_mode key from common options."""
    dash = _make_dash()
    opts = dash._build_protocol_scan_options("smb", {"security_mode": "legacy", "country": "US"})
    assert opts["security_mode"] == "legacy"


def test_build_protocol_scan_options_smb_rejects_invalid_security_mode():
    """Invalid security_mode falls back to 'cautious'."""
    dash = _make_dash()
    opts = dash._build_protocol_scan_options("smb", {"security_mode": "INVALID"})
    assert opts["security_mode"] == "cautious"


def test_build_protocol_scan_options_ftp_has_connect_timeout():
    """FTP options use shared_timeout_seconds for connect_timeout."""
    dash = _make_dash()
    opts = dash._build_protocol_scan_options("ftp", {"shared_timeout_seconds": 15})
    assert opts["connect_timeout"] == 15
    assert opts["auth_timeout"] == 15
    assert opts["listing_timeout"] == 15


def test_build_protocol_scan_options_http_has_allow_insecure_tls():
    """HTTP options carry allow_insecure_tls from common options."""
    dash = _make_dash()
    opts = dash._build_protocol_scan_options("http", {"allow_insecure_tls": False})
    assert opts["allow_insecure_tls"] is False


def test_build_protocol_scan_options_concurrency_clamped():
    """Concurrency values outside [1, 256] are clamped."""
    dash = _make_dash()
    opts_low = dash._build_protocol_scan_options("smb", {"shared_concurrency": 0})
    assert opts_low["discovery_max_concurrent_hosts"] == 1

    opts_high = dash._build_protocol_scan_options("ftp", {"shared_concurrency": 9999})
    assert opts_high["discovery_max_concurrent_hosts"] == 256
