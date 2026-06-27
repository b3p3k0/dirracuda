"""C5 tests for the optional post-probe Sherlock hook.

Section A unit-tests gui.utils.sherlock_post_probe.run_sherlock_after_probe in
isolation: the run_after_probe enable gate, fail-closed defaults, and the
guarantee that a Sherlock failure never propagates. Section B wires the hook into
real probe-persist paths (Server List bulk probe, Manual Add probe-before-add,
Dashboard post-scan probe) and proves: it fires after a successful snapshot, it
is skipped when snapshot_id is None, and a Sherlock error never fails the probe.
"""

import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so importing the dashboard works headless.
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

from gui.utils import sherlock_post_probe as spp
from gui.utils.sherlock_post_probe import run_sherlock_after_probe
from gui.utils.sherlock_scan import SherlockScanSummary


class _FakeSettings:
    """Settings manager double whose 'sherlock' shard is a plain dict."""

    def __init__(self, sherlock=None, raise_on_get=False):
        self._sherlock = sherlock if sherlock is not None else {}
        self._raise = raise_on_get

    def get_setting(self, key, default=None):
        if self._raise:
            raise RuntimeError("settings unavailable")
        if key == "sherlock":
            return self._sherlock
        return default


_READER = object()  # opaque non-None sentinel; the hook never touches it here


# --------------------------------------------------------------------------- #
# Section A: helper unit tests
# --------------------------------------------------------------------------- #

def test_enabled_calls_scan_with_single_target(monkeypatch):
    captured = {}

    def fake_scan(db_reader, settings, targets):
        captured["db_reader"] = db_reader
        captured["settings"] = settings
        captured["targets"] = list(targets)
        return SherlockScanSummary(scanned=1, with_findings=1)

    monkeypatch.setattr(spp, "run_sherlock_scan", fake_scan)

    summary = run_sherlock_after_probe(
        _FakeSettings({"run_after_probe": True}),
        _READER,
        "1.1.1.1",
        "H",
        protocol_server_id=9,
        port=8080,
    )

    assert captured["db_reader"] is _READER
    assert captured["settings"].run_after_probe is True
    assert captured["targets"] == [
        {"ip_address": "1.1.1.1", "host_type": "H", "protocol_server_id": 9, "port": 8080}
    ]
    assert summary.with_findings == 1


def test_disabled_does_not_scan(monkeypatch):
    scan = MagicMock()
    monkeypatch.setattr(spp, "run_sherlock_scan", scan)

    result = run_sherlock_after_probe(
        _FakeSettings({"run_after_probe": False}), _READER, "1.1.1.1", "S"
    )

    assert result is None
    scan.assert_not_called()


def test_missing_settings_manager_noops(monkeypatch):
    scan = MagicMock()
    monkeypatch.setattr(spp, "run_sherlock_scan", scan)

    result = run_sherlock_after_probe(None, _READER, "1.1.1.1", "S")

    assert result is None
    scan.assert_not_called()


def test_settings_load_error_noops(monkeypatch):
    scan = MagicMock()
    monkeypatch.setattr(spp, "run_sherlock_scan", scan)

    result = run_sherlock_after_probe(
        _FakeSettings(raise_on_get=True), _READER, "1.1.1.1", "S"
    )

    assert result is None
    scan.assert_not_called()


def test_missing_reader_noops(monkeypatch):
    scan = MagicMock()
    monkeypatch.setattr(spp, "run_sherlock_scan", scan)

    result = run_sherlock_after_probe(
        _FakeSettings({"run_after_probe": True}), None, "1.1.1.1", "S"
    )

    assert result is None
    scan.assert_not_called()


def test_scan_exception_is_contained(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("matcher/store failure")

    monkeypatch.setattr(spp, "run_sherlock_scan", boom)

    # Must not raise; returns None.
    result = run_sherlock_after_probe(
        _FakeSettings({"run_after_probe": True}), _READER, "1.1.1.1", "S"
    )
    assert result is None


def test_honest_noop_summary_passed_through(monkeypatch):
    summary = SherlockScanSummary(scanned=0, skipped_no_snapshot=1, not_persisted=1)
    monkeypatch.setattr(spp, "run_sherlock_scan", lambda *a, **k: summary)

    result = run_sherlock_after_probe(
        _FakeSettings({"run_after_probe": True}), _READER, "1.1.1.1", "S"
    )
    assert result is summary


# --------------------------------------------------------------------------- #
# Section B: integration at real probe-persist sites
# --------------------------------------------------------------------------- #

class _FakeProbeReader:
    """Minimal reader for probe-persist paths. snapshot upsert returns a fixed id
    (or None to exercise the guard); cache upsert is recorded."""

    def __init__(self, snapshot_id=5):
        self._snapshot_id = snapshot_id
        self.cache_calls = 0

    def upsert_probe_snapshot_for_host(self, *a, **k):
        return self._snapshot_id

    def upsert_probe_cache_for_host(self, *a, **k):
        self.cache_calls += 1
        return None


# ---- Manual Add: _persist_manual_add_probe_result (the C5-added 4th site) ---- #

def _manual_add_host(reader, settings_manager=None):
    from gui.components.server_list_window.actions.batch_operations import (
        ServerListWindowBatchOperationsMixin,
    )

    host = ServerListWindowBatchOperationsMixin.__new__(
        ServerListWindowBatchOperationsMixin
    )
    host.db_reader = reader
    host.settings_manager = settings_manager
    return host


def _manual_add_args():
    payload = {"host_type": "S", "ip_address": "203.0.113.5"}
    upsert_result = {"protocol_server_id": 77}
    probe_result = {
        "status": "clean",
        "indicator_matches": 0,
        "snapshot_path": None,
        "snapshot_payload": {"shares": [{"share": "S", "root_files": []}]},
        "port": 445,
    }
    return payload, upsert_result, probe_result


def test_manual_add_hook_fires_with_identity(monkeypatch):
    import gui.components.server_list_window.actions.batch_operations as bo

    captured = {}

    def fake_hook(settings_manager, db_reader, ip, host_type, **kwargs):
        captured["args"] = (ip, host_type)
        captured["kwargs"] = kwargs

    monkeypatch.setattr(bo, "run_sherlock_after_probe", fake_hook)

    reader = _FakeProbeReader(snapshot_id=12)
    host = _manual_add_host(reader)
    host._persist_manual_add_probe_result(*_manual_add_args())

    assert reader.cache_calls == 1
    assert captured["args"] == ("203.0.113.5", "S")
    assert captured["kwargs"]["protocol_server_id"] == 77
    assert captured["kwargs"]["port"] == 445


def test_manual_add_hook_skipped_when_snapshot_id_none(monkeypatch):
    import gui.components.server_list_window.actions.batch_operations as bo

    hook = MagicMock()
    monkeypatch.setattr(bo, "run_sherlock_after_probe", hook)

    reader = _FakeProbeReader(snapshot_id=None)
    host = _manual_add_host(reader)
    host._persist_manual_add_probe_result(*_manual_add_args())

    assert reader.cache_calls == 1  # persist still happened
    hook.assert_not_called()


def test_manual_add_sherlock_error_does_not_break_persist(monkeypatch):
    """Real hook + inner run_sherlock_scan raising: persist must still complete."""
    def boom(*a, **k):
        raise RuntimeError("sherlock blew up")

    monkeypatch.setattr(spp, "run_sherlock_scan", boom)

    reader = _FakeProbeReader(snapshot_id=3)
    host = _manual_add_host(reader, settings_manager=_FakeSettings({"run_after_probe": True}))
    # Should not raise.
    host._persist_manual_add_probe_result(*_manual_add_args())
    assert reader.cache_calls == 1


# ---- Server List bulk probe: _execute_probe_target (SMB branch) ---- #

def _server_list_host(reader, settings_manager=None):
    from gui.components.server_list_window.actions.batch import (
        ServerListWindowBatchMixin,
    )

    host = ServerListWindowBatchMixin.__new__(ServerListWindowBatchMixin)
    host.db_reader = reader
    host.settings_manager = settings_manager
    host.indicator_patterns = []
    host.all_servers = []
    host._handle_probe_status_update = lambda *a, **k: None
    return host


def test_server_list_smb_probe_fires_hook(monkeypatch):
    import gui.components.server_list_window.actions.batch as batch

    monkeypatch.setattr(
        batch, "dispatch_probe_run", lambda *a, **k: {"shares": [{"share": "s1"}]}
    )
    monkeypatch.setattr(
        batch.probe_patterns,
        "attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    hook = MagicMock()
    monkeypatch.setattr(batch, "run_sherlock_after_probe", hook)

    reader = _FakeProbeReader(snapshot_id=8)
    host = _server_list_host(reader)
    target = {
        "ip_address": "198.51.100.9",
        "host_type": "S",
        "shares": ["s1"],
        "auth_method": "anonymous",
        "row_key": "rk1",
    }
    result = host._execute_probe_target("job", target, {"limits": {}}, threading.Event())

    assert result["status"] == "success"
    hook.assert_called_once()
    assert hook.call_args[0][2] == "198.51.100.9"
    assert hook.call_args[0][3] == "S"


def test_server_list_smb_probe_skips_hook_when_snapshot_id_none(monkeypatch):
    import gui.components.server_list_window.actions.batch as batch

    monkeypatch.setattr(
        batch, "dispatch_probe_run", lambda *a, **k: {"shares": [{"share": "s1"}]}
    )
    monkeypatch.setattr(
        batch.probe_patterns,
        "attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    hook = MagicMock()
    monkeypatch.setattr(batch, "run_sherlock_after_probe", hook)

    reader = _FakeProbeReader(snapshot_id=None)
    host = _server_list_host(reader)
    target = {
        "ip_address": "198.51.100.9",
        "host_type": "S",
        "shares": ["s1"],
        "auth_method": "anonymous",
        "row_key": "rk1",
    }
    result = host._execute_probe_target("job", target, {"limits": {}}, threading.Event())

    assert result["status"] == "success"
    hook.assert_not_called()


# ---- Dashboard post-scan probe: probe_single_server (FTP branch) ---- #

def _dashboard():
    from gui.components.dashboard import DashboardWidget

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.indicator_patterns = []
    dash._protocol_label_from_host_type = lambda _ht: "FTP"
    return dash


def test_dashboard_ftp_probe_fires_hook(monkeypatch):
    import gui.components.dashboard_batch_ops as dbo

    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **k: {"shares": [{"directories": [], "root_files": []}]},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    hook = MagicMock()
    monkeypatch.setattr(dbo, "run_sherlock_after_probe", hook)

    dash = _dashboard()
    dash.db_reader = _FakeProbeReader(snapshot_id=4)

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.1", "host_type": "F", "port": 21},
        max_dirs=2, max_files=5, timeout_seconds=3, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    hook.assert_called_once()
    assert hook.call_args[0][2] == "10.0.0.1"
    assert hook.call_args[0][3] == "F"
    assert hook.call_args[1]["port"] == 21


def test_dashboard_ftp_probe_skips_hook_when_snapshot_id_none(monkeypatch):
    import gui.components.dashboard_batch_ops as dbo

    monkeypatch.setattr(
        "gui.components.dashboard.dispatch_probe_run",
        lambda *a, **k: {"shares": [{"directories": [], "root_files": []}]},
    )
    monkeypatch.setattr(
        "gui.components.dashboard.probe_patterns.attach_indicator_analysis",
        lambda snap, patterns: {"is_suspicious": False, "matches": []},
    )
    hook = MagicMock()
    monkeypatch.setattr(dbo, "run_sherlock_after_probe", hook)

    dash = _dashboard()
    dash.db_reader = _FakeProbeReader(snapshot_id=None)

    result = dash._probe_single_server(
        {"ip_address": "10.0.0.1", "host_type": "F", "port": 21},
        max_dirs=2, max_files=5, timeout_seconds=3, cancel_event=threading.Event(),
    )

    assert result["status"] == "success"
    hook.assert_not_called()


# --------------------------------------------------------------------------- #
# Section C: set_run_after_probe writer (C5.1 prescan toggle backing)
# --------------------------------------------------------------------------- #

class _RWSettings:
    """Settings manager double backed by an in-memory 'sherlock' shard."""

    def __init__(self, sherlock=None, raise_on_set=False):
        self._shard = sherlock if sherlock is not None else {}
        self._raise_on_set = raise_on_set
        self.saved = None

    def get_setting(self, key, default=None):
        if key == "sherlock":
            return self._shard
        return default

    def set_setting(self, key, value):
        if self._raise_on_set:
            raise RuntimeError("save failed")
        if key == "sherlock":
            self.saved = value
        return True


def test_set_run_after_probe_enables_and_preserves_other_settings():
    shard = {
        "run_after_probe": False,
        "ignore_case": True,
        "colors": {"high": "#123456", "med": "#abcdef", "low": "#0f0f0f"},
        "custom_patterns": [
            {"key": "k1", "category": "Custom", "label": "L", "pattern": "*secret*",
             "severity": "high", "enabled": True}
        ],
    }
    sm = _RWSettings(sherlock=shard)
    assert spp.set_run_after_probe(sm, True) is True
    assert sm.saved["run_after_probe"] is True
    # Unrelated settings survive the round-trip (no clobber).
    assert sm.saved["colors"]["high"] == "#123456"
    assert any(p["key"] == "k1" for p in sm.saved["custom_patterns"])


def test_set_run_after_probe_can_disable():
    sm = _RWSettings(sherlock={"run_after_probe": True})
    assert spp.set_run_after_probe(sm, False) is True
    assert sm.saved["run_after_probe"] is False


def test_set_run_after_probe_none_manager_returns_false():
    assert spp.set_run_after_probe(None, True) is False


def test_set_run_after_probe_save_error_returns_false():
    sm = _RWSettings(raise_on_set=True)
    assert spp.set_run_after_probe(sm, True) is False
