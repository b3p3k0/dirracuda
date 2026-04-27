"""Regression tests for dashboard post-scan bulk operation routing."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub for environments where dependency is unavailable.
if "impacket" not in sys.modules:
    impacket_mod = types.ModuleType("impacket")
    impacket_smb_mod = types.ModuleType("impacket.smb")
    impacket_smb_mod.SMB2_DIALECT_002 = object()
    impacket_smbconn_mod = types.ModuleType("impacket.smbconnection")
    impacket_smbconn_mod.SMBConnection = object

    class _SessionError(Exception):
        pass

    impacket_smbconn_mod.SessionError = _SessionError
    impacket_mod.smb = impacket_smb_mod

    sys.modules["impacket"] = impacket_mod
    sys.modules["impacket.smb"] = impacket_smb_mod
    sys.modules["impacket.smbconnection"] = impacket_smbconn_mod

from gui.components.dashboard import DashboardWidget


class _StubParent:
    def after(self, _ms, _fn, *_args):
        return None


class _FakeTheme:
    def apply_to_widget(self, *_args, **_kwargs):
        return None

    def apply_theme_to_application(self, *_args, **_kwargs):
        return None


class _FakeSettingsManager:
    def get_setting(self, _key, default=None):
        return default


class _FakeParentModal:
    def after(self, _ms, _fn, *_args):
        return None

    def wait_window(self, _dialog):
        return None


class _FakeWidget:
    def __init__(self, *_args, **kwargs):
        self.command = kwargs.get("command")
        self.text = kwargs.get("text", "")

    def pack(self, *_args, **_kwargs):
        return None

    def config(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    configure = config


class _FakeProgressbar(_FakeWidget):
    def __init__(self, *_args, **kwargs):
        super().__init__(*_args, **kwargs)
        self.value = 0
        self.started = False
        self.stopped = False

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def start(self, *_args, **_kwargs):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakeDialog:
    created = []

    def __init__(self, *_args, **_kwargs):
        self.exists = True
        self.protocol_handlers = {}
        self.after_callbacks = []
        self.destroy_calls = 0
        self.grab_release_calls = 0
        _FakeDialog.created.append(self)

    def title(self, *_args, **_kwargs):
        return None

    def geometry(self, *_args, **_kwargs):
        return None

    def transient(self, *_args, **_kwargs):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        self.grab_release_calls += 1

    def protocol(self, name, callback):
        self.protocol_handlers[name] = callback

    def update_idletasks(self):
        return None

    def withdraw(self):
        return None

    def deiconify(self):
        return None

    def lift(self):
        return None

    def focus_force(self):
        return None

    def after(self, _ms, callback, *args):
        self.after_callbacks.append(getattr(callback, "__name__", repr(callback)))
        callback(*args)
        return len(self.after_callbacks)

    def winfo_exists(self):
        return 1 if self.exists else 0

    def destroy(self):
        self.destroy_calls += 1
        self.exists = False

    def trigger_close(self):
        callback = self.protocol_handlers.get("WM_DELETE_WINDOW")
        if callback:
            callback()


class _FakeEvent:
    def __init__(self):
        self._is_set = False

    def set(self):
        self._is_set = True

    def is_set(self):
        return self._is_set


class _FakeEventFactory:
    def __init__(self):
        self.instances = []

    def __call__(self):
        instance = _FakeEvent()
        self.instances.append(instance)
        return instance


class _ImmediateThread:
    def __init__(self, target=None, daemon=False):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target()


def _make_probe_test_dash() -> DashboardWidget:
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = _FakeParentModal()
    dash.theme = _FakeTheme()
    dash.settings_manager = _FakeSettingsManager()
    dash.current_scan_options = {}
    dash._probe_single_server = lambda server, *_args, **_kwargs: {
        "ip_address": server.get("ip_address"),
        "protocol": "SMB",
        "action": "probe",
        "status": "success",
        "notes": "ok",
    }
    dash._protocol_label_from_host_type = lambda _host_type: "SMB"
    return dash


def _patch_fake_probe_dialog_stack(monkeypatch):
    _FakeDialog.created.clear()
    event_factory = _FakeEventFactory()

    monkeypatch.setattr("gui.components.dashboard.tk.Toplevel", _FakeDialog)
    monkeypatch.setattr("gui.components.dashboard.tk.Label", _FakeWidget)
    monkeypatch.setattr("gui.components.dashboard.tk.Button", _FakeWidget)
    monkeypatch.setattr("gui.components.dashboard.ttk.Progressbar", _FakeProgressbar)
    monkeypatch.setattr(
        "gui.components.dashboard.threading",
        types.SimpleNamespace(Thread=_ImmediateThread, Event=event_factory),
    )

    return event_factory


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
                    "anon_accessible": 0,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.22",
                    "host_type": "F",
                    "protocol_server_id": 22,
                    "anon_accessible": 1,
                    "accessible_shares": 0,
                    "probe_status": "unprobed",
                    "indicator_matches": 0,
                },
                {
                    "ip_address": "203.0.113.33",
                    "host_type": "F",
                    "protocol_server_id": 33,
                    "anon_accessible": 1,
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


def test_protocol_label_helpers():
    dash = DashboardWidget.__new__(DashboardWidget)
    assert dash._protocol_label_from_host_type("S") == "SMB"
    assert dash._protocol_label_from_host_type("F") == "FTP"
    assert dash._protocol_label_from_host_type("H") == "HTTP"
    assert dash._protocol_label_from_host_type("X") == "Unknown"
    assert dash._protocol_label_for_result({"protocol": "ftp", "host_type": "S"}) == "FTP"
    assert dash._protocol_label_for_result({"host_type": "H"}) == "HTTP"


def test_extract_single_server_ftp_routes_protocol_runner(monkeypatch, tmp_path):
    import threading

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.db_reader = MagicMock()
    dash.db_reader.upsert_extracted_flag_for_host = MagicMock()

    monkeypatch.setattr(
        "gui.components.dashboard.create_quarantine_dir",
        lambda ip, purpose, base_path=None: tmp_path / ip / "20260421",
    )

    captured = {}

    def _fake_run_ftp_extract(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "totals": {"files_downloaded": 2, "bytes_downloaded": 1048576},
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr(
        "gui.components.dashboard.protocol_extract_runner.run_ftp_extract",
        _fake_run_ftp_extract,
    )

    result = dash._extract_single_server(
        {"ip_address": "198.51.100.10", "host_type": "F", "port": 2121, "protocol_server_id": 77},
        max_file_mb=10,
        max_total_mb=100,
        max_time=5,
        max_files=5,
        extension_mode="download_all",
        included_extensions=[],
        excluded_extensions=[],
        quarantine_base_path=None,
        cancel_event=threading.Event(),
        clamav_config={},
    )

    assert result["status"] == "success"
    assert result["protocol"] == "FTP"
    assert "2 file(s), 1.0 MB" in result["notes"]
    assert captured["args"][0] == "198.51.100.10"
    assert captured["kwargs"]["port"] == 2121
    dash.db_reader.upsert_extracted_flag_for_host.assert_called_once()
    args, kwargs = dash.db_reader.upsert_extracted_flag_for_host.call_args
    assert args[:3] == ("198.51.100.10", "F", True)
    assert kwargs["protocol_server_id"] == 77
    assert kwargs["port"] == 2121


def test_extract_single_server_http_resolves_endpoint_metadata(monkeypatch, tmp_path):
    import threading

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.db_reader = MagicMock()
    dash.db_reader.get_http_server_detail.return_value = {
        "port": 443,
        "scheme": "https",
        "probe_host": "cdn.example.org",
        "probe_path": "/public/",
    }
    dash.db_reader.upsert_extracted_flag_for_host = MagicMock()

    monkeypatch.setattr(
        "gui.components.dashboard.create_quarantine_dir",
        lambda ip, purpose, base_path=None: tmp_path / ip / "20260421",
    )

    captured = {}

    def _fake_run_http_extract(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "totals": {"files_downloaded": 1, "bytes_downloaded": 2048},
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr(
        "gui.components.dashboard.protocol_extract_runner.run_http_extract",
        _fake_run_http_extract,
    )

    result = dash._extract_single_server(
        {
            "ip_address": "203.0.113.7",
            "host_type": "H",
            "protocol_server_id": 91,
            "_http_allow_insecure_tls": False,
        },
        max_file_mb=10,
        max_total_mb=100,
        max_time=5,
        max_files=5,
        extension_mode="download_all",
        included_extensions=[],
        excluded_extensions=[],
        quarantine_base_path=None,
        cancel_event=threading.Event(),
        clamav_config={},
    )

    assert result["status"] == "success"
    assert result["protocol"] == "HTTP"
    assert captured["args"][0] == "203.0.113.7"
    assert captured["kwargs"]["port"] == 443
    assert captured["kwargs"]["scheme"] == "https"
    assert captured["kwargs"]["request_host"] == "cdn.example.org"
    assert captured["kwargs"]["start_path"] == "/public/"
    assert captured["kwargs"]["allow_insecure_tls"] is False
    dash.db_reader.upsert_extracted_flag_for_host.assert_called_once()
    args, kwargs = dash.db_reader.upsert_extracted_flag_for_host.call_args
    assert args[:3] == ("203.0.113.7", "H", True)
    assert kwargs["protocol_server_id"] == 91
    assert kwargs["port"] == 443


def test_probe_single_server_ftp_snapshot_path_from_dispatch(monkeypatch):
    """FTP probe persistence should not depend on legacy snapshot_path values."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()

    minimal_snapshot = {"shares": [{"directories": [], "root_files": []}]}
    captured = {}

    def _fake_dispatch(*args, **kwargs):
        captured["kwargs"] = kwargs
        return minimal_snapshot

    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        _fake_dispatch,
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
    assert captured["kwargs"]["max_depth"] == 1
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["snapshot_path"] is None


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
    """HTTP probe persistence should not depend on legacy snapshot_path values."""
    import threading
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash.db_reader = MagicMock()
    dash.db_reader.get_http_server_detail.return_value = {"port": 80, "scheme": "http"}

    minimal_snapshot = {"shares": [{"directories": [], "root_files": []}]}
    captured = {}

    def _fake_dispatch(*args, **kwargs):
        captured["kwargs"] = kwargs
        return minimal_snapshot

    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        _fake_dispatch,
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
    assert captured["kwargs"]["max_depth"] == 1
    call_kwargs = dash.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["snapshot_path"] is None


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


def test_execute_batch_probe_destroys_stale_progress_dialog_before_new_run(monkeypatch):
    """A stale Bulk Probe dialog handle must be cleaned before creating a new one."""
    _patch_fake_probe_dialog_stack(monkeypatch)
    dash = _make_probe_test_dash()

    stale_dialog = _FakeDialog()
    dash._bulk_probe_progress_dialog = stale_dialog

    results = dash._execute_batch_probe([{"ip_address": "198.51.100.1", "host_type": "S"}])

    assert results and results[0]["status"] == "success"
    assert stale_dialog.destroy_calls == 1
    assert stale_dialog.grab_release_calls >= 1
    assert dash._bulk_probe_progress_dialog is None


def test_execute_batch_probe_wm_delete_window_hides_without_cancel(monkeypatch):
    """Window close should hide monitor dialog but not cancel active probe job."""
    event_factory = _patch_fake_probe_dialog_stack(monkeypatch)
    dash = _make_probe_test_dash()

    dash._execute_batch_probe([{"ip_address": "198.51.100.2", "host_type": "S"}])

    assert len(event_factory.instances) >= 1
    cancel_event = event_factory.instances[0]
    assert cancel_event.is_set() is False

    dialog = _FakeDialog.created[-1]
    dialog.trigger_close()
    assert cancel_event.is_set() is False


def test_execute_batch_probe_completion_path_avoids_worker_destroy_after(monkeypatch):
    """Probe completion should close dialog without scheduling dialog.destroy via after()."""
    _patch_fake_probe_dialog_stack(monkeypatch)
    dash = _make_probe_test_dash()

    dash._execute_batch_probe([{"ip_address": "198.51.100.3", "host_type": "S"}])

    dialog = _FakeDialog.created[-1]
    assert "ui_tick" in dialog.after_callbacks
    assert "destroy" not in dialog.after_callbacks
    assert dialog.destroy_calls >= 1


def test_execute_batch_probe_passes_configured_depth_to_probe_worker(monkeypatch):
    _patch_fake_probe_dialog_stack(monkeypatch)

    class _DepthSettings(_FakeSettingsManager):
        def get_setting(self, key, default=None):
            if key == "probe.max_depth_levels":
                return 3
            return default

    captured = {}
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = _FakeParentModal()
    dash.theme = _FakeTheme()
    dash.settings_manager = _DepthSettings()
    dash.current_scan_options = {}
    dash._protocol_label_from_host_type = lambda _host_type: "SMB"

    def _probe_single_server(server, _max_dirs, _max_files, _timeout_seconds, max_depth, _enable_rce, _cancel_event):
        captured["max_depth"] = max_depth
        return {
            "ip_address": server.get("ip_address"),
            "protocol": "SMB",
            "action": "probe",
            "status": "success",
            "notes": "ok",
        }

    dash._probe_single_server = _probe_single_server

    results = dash._execute_batch_probe([{"ip_address": "198.51.100.4", "host_type": "S"}])

    assert results and results[0]["status"] == "success"
    assert captured["max_depth"] == 3


def test_run_background_fetch_closes_via_ui_poll_without_destroy_after(monkeypatch):
    """Background fetch modal should close from UI poll path, not worker-thread after()."""
    _FakeDialog.created.clear()
    monkeypatch.setattr("gui.components.dashboard.tk.Toplevel", _FakeDialog)
    monkeypatch.setattr("gui.components.dashboard.tk.Label", _FakeWidget)
    monkeypatch.setattr("gui.components.dashboard.ttk.Progressbar", _FakeProgressbar)
    monkeypatch.setattr(
        "gui.components.dashboard.threading",
        types.SimpleNamespace(Thread=_ImmediateThread),
    )

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = _FakeParentModal()
    dash.theme = _FakeTheme()

    result, error = dash._run_background_fetch(
        title="Preparing",
        message="Loading servers...",
        fetch_fn=lambda: {"ok": True},
    )

    assert result == {"ok": True}
    assert error is None
    dialog = _FakeDialog.created[-1]
    assert "poll_done" in dialog.after_callbacks
    assert "destroy" not in dialog.after_callbacks
    assert dialog.destroy_calls >= 1
