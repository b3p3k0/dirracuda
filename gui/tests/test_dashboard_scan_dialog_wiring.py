"""Tests for dashboard quick-scan dialog callback wiring."""

from __future__ import annotations

import sys
import types
from pathlib import Path

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


class _ScanManagerStub:
    def is_scan_active(self) -> bool:
        return False


def test_show_quick_scan_dialog_passes_query_editor_callback(monkeypatch):
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = object()
    dash.config_path = "/tmp/config.json"
    dash.scan_manager = _ScanManagerStub()
    dash.settings_manager = object()
    dash._rce_unlocked = True
    dash._start_unified_scan = lambda _request: None
    dash.config_editor_callback = lambda _path: None

    captured = {}

    def _fake_show_unified_scan_dialog(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "gui.components.dashboard.show_unified_scan_dialog",
        _fake_show_unified_scan_dialog,
    )
    monkeypatch.setattr("gui.components.dashboard.messagebox.showwarning", lambda *_a, **_k: None)

    dash._show_quick_scan_dialog()

    assert captured["config_editor_callback"] == dash._open_config_editor_from_scan
    assert captured["query_editor_callback"] == dash._open_config_editor
    assert captured["show_rce_controls"] is True


def test_show_quick_scan_dialog_does_not_pass_reddit_grab_callback(monkeypatch):
    captured = {}

    def _fake_show_unified_scan_dialog(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "gui.components.dashboard.show_unified_scan_dialog",
        _fake_show_unified_scan_dialog,
    )
    monkeypatch.setattr("gui.components.dashboard.messagebox.showwarning", lambda *_a, **_k: None)

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = object()
    dash.config_path = "/tmp/config.json"
    dash.scan_manager = _ScanManagerStub()
    dash.settings_manager = object()
    dash._rce_unlocked = False
    dash._start_unified_scan = lambda _request: None
    dash.config_editor_callback = lambda _path: None

    dash._show_quick_scan_dialog()

    # Key must be absent entirely — not merely None — after C2 legacy removal.
    assert "reddit_grab_callback" not in captured
    assert captured["show_rce_controls"] is False
