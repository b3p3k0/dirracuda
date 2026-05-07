"""ScanManager Shodan candidate-cap override tests."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock


def test_smb_scan_worker_derives_budget_and_disables_adaptive_stop_from_cap():
    from gui.utils.scan_manager import ScanManager

    manager = ScanManager.__new__(ScanManager)
    manager.backend_interface = MagicMock()
    manager.log_callback = None
    manager._update_progress = MagicMock()
    manager._execute_scan_with_options = MagicMock(return_value={"success": True})
    manager._process_scan_results = MagicMock()
    manager._handle_scan_error = MagicMock()
    manager._cleanup_scan = MagicMock()

    captured = {}

    @contextmanager
    def _fake_override(overrides):
        captured["overrides"] = overrides
        yield

    manager.backend_interface._temporary_config_override = _fake_override

    manager._scan_worker(
        {
            "country": "US",
            "max_shodan_results": 1000,
            "smb_max_query_credits_per_scan": 1,
        }
    )

    q_limits = captured["overrides"]["shodan"]["query_limits"]
    assert q_limits["max_results"] == 1000
    assert q_limits["smb_max_query_credits_per_scan"] == 10
    assert q_limits["max_query_credits_per_scan"] == 10
    assert q_limits["min_usable_hosts_target"] == 1001

