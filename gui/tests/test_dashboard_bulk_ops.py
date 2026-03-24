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

    def _fake_get_servers_for_bulk_ops(
        skip_indicator_extract=True,
        host_type_filter=None,
        scan_start_time=None,
        scan_end_time=None,
    ):
        captured["skip_indicator_extract"] = skip_indicator_extract
        captured["host_type_filter"] = host_type_filter
        captured["scan_start_time"] = scan_start_time
        captured["scan_end_time"] = scan_end_time
        return {
            "probe": [{"ip_address": "203.0.113.10", "host_type": "H"}],
            "extract": [],
        }

    def _fake_run_background_fetch(title, message, fetch_fn):
        return fetch_fn(), None

    monkeypatch.setattr("gui.components.dashboard.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setattr("gui.components.dashboard.messagebox.showinfo", lambda *a, **k: None)

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
            "start_time": "2026-03-24T14:00:00",
            "end_time": "2026-03-24T14:10:00",
        },
    )

    assert captured["host_type_filter"] == "H"
    assert captured["scan_start_time"] == "2026-03-24T14:00:00"
    assert captured["scan_end_time"] == "2026-03-24T14:10:00"
    dash._execute_batch_probe.assert_called_once()
    probe_targets = dash._execute_batch_probe.call_args[0][0]
    assert len(probe_targets) == 1
    assert probe_targets[0]["host_type"] == "H"


def test_get_servers_for_bulk_ops_filters_to_immediate_scan_window_ftp():
    """FTP post-scan probe targets must be constrained to the immediate scan cohort."""
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
                    "ip_address": "203.0.113.11",
                    "host_type": "F",
                    "protocol_server_id": 11,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.22",
                    "host_type": "F",
                    "protocol_server_id": 22,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.33",
                    "host_type": "F",
                    "protocol_server_id": 33,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
            ], 3)

        def get_protocol_scan_cohort_server_ids(self, host_type, scan_start_time, scan_end_time):
            assert host_type == "F"
            assert scan_start_time == "2026-03-24T14:00:00"
            assert scan_end_time == "2026-03-24T14:10:00"
            return {22, 33}

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(
        skip_indicator_extract=True,
        host_type_filter="F",
        scan_start_time="2026-03-24T14:00:00",
        scan_end_time="2026-03-24T14:10:00",
    )

    assert [r["ip_address"] for r in result["probe"]] == ["203.0.113.22", "203.0.113.33"]
    assert result["extract"] == []


def test_get_servers_for_bulk_ops_filters_to_immediate_scan_window_smb():
    """SMB post-scan probe targets must be constrained to the immediate scan cohort."""
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
                    "ip_address": "198.51.100.11",
                    "host_type": "S",
                    "protocol_server_id": 11,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "198.51.100.22",
                    "host_type": "S",
                    "protocol_server_id": 22,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
            ], 2)

        def get_protocol_scan_cohort_server_ids(self, host_type, scan_start_time, scan_end_time):
            assert host_type == "S"
            assert scan_start_time == "2026-03-24T15:00:00"
            assert scan_end_time == "2026-03-24T15:10:00"
            return {22}

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(
        skip_indicator_extract=True,
        host_type_filter="S",
        scan_start_time="2026-03-24T15:00:00",
        scan_end_time="2026-03-24T15:10:00",
    )

    assert [r["ip_address"] for r in result["probe"]] == ["198.51.100.22"]
    assert result["extract"] == []


def test_get_servers_for_bulk_ops_filters_to_immediate_scan_window_http():
    """HTTP post-scan probe targets must be constrained to the immediate scan cohort."""
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
                    "ip_address": "203.0.113.41",
                    "host_type": "H",
                    "protocol_server_id": 41,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.42",
                    "host_type": "H",
                    "protocol_server_id": 42,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
            ], 2)

        def get_protocol_scan_cohort_server_ids(self, host_type, scan_start_time, scan_end_time):
            assert host_type == "H"
            assert scan_start_time == "2026-03-24T16:00:00"
            assert scan_end_time == "2026-03-24T16:10:00"
            return {41}

    dash.db_reader = _StubReader()
    result = dash._get_servers_for_bulk_ops(
        skip_indicator_extract=True,
        host_type_filter="H",
        scan_start_time="2026-03-24T16:00:00",
        scan_end_time="2026-03-24T16:10:00",
    )

    assert [r["ip_address"] for r in result["probe"]] == ["203.0.113.41"]
    assert result["extract"] == []


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


def test_probe_single_server_ftp_snapshot_path_from_dispatch(monkeypatch):
    """snapshot_path for F rows must come from probe_cache_dispatch."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()

    minimal_snapshot = {"shares": [{"directories": [], "root_files": []}]}
    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **kw: minimal_snapshot,
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.get_probe_snapshot_path_for_host",
        lambda ip, ht: "SENTINEL_FTP_PATH",
    )

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.1", "host_type": "F", "port": 21},
        max_dirs=2, max_files=5, timeout_seconds=3,
        enable_rce=False, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["snapshot_path"] == "SENTINEL_FTP_PATH"


def test_probe_single_server_ftp_root_files_only_sets_loose_files_marker(monkeypatch):
    """FTP root-file-only snapshots should persist the loose-file display marker."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()

    minimal_snapshot = {"shares": [{"directories": [], "root_files": ["backup.tar"]}]}
    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **kw: minimal_snapshot,
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.get_probe_snapshot_path_for_host",
        lambda ip, ht: "SENTINEL_FTP_PATH",
    )

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.3", "host_type": "F", "port": 21},
        max_dirs=2, max_files=5, timeout_seconds=3,
        enable_rce=False, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["accessible_dirs_count"] == 1
    assert call_kwargs["accessible_dirs_list"] == "[[loose files]]"


def test_probe_single_server_http_snapshot_path_from_dispatch(monkeypatch):
    """snapshot_path for H rows must come from probe_cache_dispatch."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()
    dash.db_reader.get_http_server_detail.return_value = {"port": 80, "scheme": "http"}

    minimal_snapshot = {"shares": [{"directories": [], "root_files": []}]}
    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **kw: minimal_snapshot,
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.get_probe_snapshot_path_for_host",
        lambda ip, ht: "SENTINEL_HTTP_PATH",
    )

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.2", "host_type": "H"},
        max_dirs=2, max_files=5, timeout_seconds=3,
        enable_rce=False, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["snapshot_path"] == "SENTINEL_HTTP_PATH"


def test_probe_single_server_http_root_files_only_sets_loose_files_marker(monkeypatch):
    """HTTP root-file-only snapshots should expose marker in accessible_dirs_list."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()
    dash.db_reader.get_http_server_detail.return_value = {"port": 80, "scheme": "http"}

    minimal_snapshot = {"shares": [{"directories": [], "root_files": ["index.html"]}]}
    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **kw: minimal_snapshot,
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.get_probe_snapshot_path_for_host",
        lambda ip, ht: "SENTINEL_HTTP_PATH",
    )

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.4", "host_type": "H"},
        max_dirs=2, max_files=5, timeout_seconds=3,
        enable_rce=False, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["accessible_dirs_count"] == 0
    assert call_kwargs["accessible_dirs_list"] == "[[loose files]]"
    assert call_kwargs["accessible_files_count"] == 1
