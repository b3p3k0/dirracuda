"""C5.1: Quick Scan dialog wiring for the Sherlock run-after-probe control.

Headless method tests (no Tk root) via UnifiedScanDialog.__new__, mirroring
test_unified_scan_dialog_validation.py. Verifies the toggle persists to the shared
sherlock shard and that opening the settings window refreshes the checkbox from the
shard without re-persisting (refresh uses var.set(), which does not fire the command).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.unified_scan_dialog as unified_scan_dialog
from gui.components.unified_scan_dialog import UnifiedScanDialog


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _DialogStub:
    pass


def test_toggle_persists_enabled_to_shared_setting(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = object()
    dlg.sherlock_run_after_probe_var = _Var(True)

    captured = {}
    monkeypatch.setattr(
        unified_scan_dialog,
        "set_run_after_probe",
        lambda sm, enabled: captured.update(sm=sm, enabled=enabled) or True,
    )

    dlg._on_sherlock_run_after_probe_toggled()

    assert captured["sm"] is dlg._settings_manager
    assert captured["enabled"] is True


def test_toggle_persists_disabled_to_shared_setting(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = object()
    dlg.sherlock_run_after_probe_var = _Var(False)

    captured = {}
    monkeypatch.setattr(
        unified_scan_dialog,
        "set_run_after_probe",
        lambda sm, enabled: captured.update(enabled=enabled) or True,
    )

    dlg._on_sherlock_run_after_probe_toggled()

    assert captured["enabled"] is False


def test_open_settings_blocks_then_refreshes_from_shard(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = object()
    dlg.dialog = _DialogStub()
    dlg.sherlock_run_after_probe_var = _Var(False)

    order = []

    def _open(parent, sm):
        order.append("opened")
        assert parent is dlg.dialog
        assert sm is dlg._settings_manager

    def _load(sm):
        order.append("reloaded")
        return types.SimpleNamespace(run_after_probe=True)

    def _must_not_persist(*_a, **_k):
        raise AssertionError("refresh must not re-persist the setting")

    monkeypatch.setattr(unified_scan_dialog, "open_sherlock_settings_window", _open)
    monkeypatch.setattr(unified_scan_dialog, "load_sherlock_settings", _load)
    monkeypatch.setattr(unified_scan_dialog, "set_run_after_probe", _must_not_persist)

    dlg._open_sherlock_settings()

    # Window opens (blocking) first, then the checkbox refreshes from the shard.
    assert order == ["opened", "reloaded"]
    assert dlg.sherlock_run_after_probe_var.get() is True
