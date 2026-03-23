"""Regression tests for dashboard post-scan bulk operation routing."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


class _StubParent:
    def after(self, _ms, _fn, *_args):
        return None


def test_http_post_scan_bulk_probe_uses_http_host_type_filter(monkeypatch):
    """HTTP scans must gather H rows (not SMB S rows) for post-scan bulk probe."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = _StubParent()
    dash._show_scan_results = MagicMock()
    dash._reset_scan_status = MagicMock()
    dash._show_batch_summary = MagicMock()
    dash._execute_batch_probe = MagicMock(return_value=[])
    dash._execute_batch_extract = MagicMock(return_value=[])

    captured = {}

    def _fake_get_servers_for_bulk_ops(skip_indicator_extract=True, host_type_filter=None):
        captured["skip_indicator_extract"] = skip_indicator_extract
        captured["host_type_filter"] = host_type_filter
        return {
            "probe": [{"ip_address": "203.0.113.10", "host_type": "H"}],
            "extract": [],
        }

    def _fake_run_background_fetch(title, message, fetch_fn):
        return fetch_fn(), None

    monkeypatch.setattr("gui.components.dashboard_bulk_ops.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setattr("gui.components.dashboard_bulk_ops.messagebox.showinfo", lambda *a, **k: None)

    dash._get_servers_for_bulk_ops = _fake_get_servers_for_bulk_ops
    dash._run_background_fetch = _fake_run_background_fetch

    dash._run_post_scan_batch_operations(
        {
            "bulk_probe_enabled": True,
            "bulk_extract_enabled": False,
            "bulk_extract_skip_indicators": True,
        },
        {
            "protocol": "http",
            "hosts_scanned": 1,
        },
    )

    assert captured["host_type_filter"] == "H"
    dash._execute_batch_probe.assert_called_once()
    probe_targets = dash._execute_batch_probe.call_args[0][0]
    assert len(probe_targets) == 1
    assert probe_targets[0]["host_type"] == "H"


def test_get_servers_for_bulk_ops_probe_includes_zero_accessibility_rows():
    """Probe target selection should not require accessible_shares > 0."""
    dash = DashboardWidget.__new__(DashboardWidget)

    class _StubReader:
        def get_protocol_server_list(
            self,
            limit=5000,
            offset=0,
            country_filter=None,
            recent_scan_only=True,
        ):
            return ([
                {
                    "ip_address": "203.0.113.55",
                    "host_type": "H",
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                }
            ], 1)

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(skip_indicator_extract=True, host_type_filter="H")

    assert len(result["probe"]) == 1
    assert result["probe"][0]["ip_address"] == "203.0.113.55"
    # Extract remains accessibility-gated.
    assert result["extract"] == []


def test_get_servers_for_bulk_ops_probe_includes_zero_accessibility_rows_smb():
    """SMB probe target selection should not require accessible_shares > 0."""
    dash = DashboardWidget.__new__(DashboardWidget)

    class _StubReader:
        def get_protocol_server_list(
            self,
            limit=5000,
            offset=0,
            country_filter=None,
            recent_scan_only=True,
        ):
            return ([
                {
                    "ip_address": "203.0.113.99",
                    "host_type": "S",
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                }
            ], 1)

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(skip_indicator_extract=True, host_type_filter="S")

    assert len(result["probe"]) == 1
    assert result["probe"][0]["ip_address"] == "203.0.113.99"
    assert result["extract"] == []


def test_get_servers_for_bulk_ops_probe_excludes_ftp_non_accessible_rows():
    """FTP probe target selection should require anon_accessible truthy."""
    dash = DashboardWidget.__new__(DashboardWidget)

    class _StubReader:
        def get_protocol_server_list(
            self,
            limit=5000,
            offset=0,
            country_filter=None,
            recent_scan_only=True,
        ):
            return ([
                {
                    "ip_address": "203.0.113.10",
                    "host_type": "F",
                    "anon_accessible": 1,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.11",
                    "host_type": "F",
                    "anon_accessible": 0,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
            ], 2)

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(skip_indicator_extract=True, host_type_filter="F")

    probe_ips = {row["ip_address"] for row in result["probe"]}
    assert probe_ips == {"203.0.113.10"}
    assert result["extract"] == []


def test_get_servers_for_bulk_ops_probe_includes_ftp_anon_accessible_with_zero_shares():
    """FTP anon_accessible rows remain probe-eligible even when shares are zero."""
    dash = DashboardWidget.__new__(DashboardWidget)

    class _StubReader:
        def get_protocol_server_list(
            self,
            limit=5000,
            offset=0,
            country_filter=None,
            recent_scan_only=True,
        ):
            return ([
                {
                    "ip_address": "203.0.113.12",
                    "host_type": "F",
                    "anon_accessible": 1,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                }
            ], 1)

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(skip_indicator_extract=True, host_type_filter="F")

    assert len(result["probe"]) == 1
    assert result["probe"][0]["ip_address"] == "203.0.113.12"
    assert result["extract"] == []
