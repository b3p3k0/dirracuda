"""Focused tests for probe depth support in scan preflight dialog/controller."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.scan_preflight import ProbeConfigDialog, ScanPreflightController


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _DialogStub:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def test_probe_config_dialog_save_persists_and_clamps_depth(monkeypatch):
    dlg = ProbeConfigDialog.__new__(ProbeConfigDialog)
    dlg.worker_var = _Var("2")
    dlg.max_dirs_var = _Var("3")
    dlg.max_files_var = _Var("5")
    dlg.timeout_var = _Var("10")
    dlg.max_depth_var = _Var("99")
    dlg.settings = MagicMock()
    dlg.dialog = _DialogStub()
    dlg.result = None

    monkeypatch.setattr("gui.components.scan_preflight.messagebox.showerror", lambda *_a, **_k: None)

    dlg._save()

    assert dlg.result["status"] == "ok"
    assert dlg.result["max_depth"] == 3
    dlg.settings.set_setting.assert_any_call("probe.max_depth_levels", 3)
    assert dlg.dialog.destroyed is True


def test_scan_preflight_summary_line_includes_probe_depth(monkeypatch):
    class _FakeProbeConfigDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def show(self):
            return {
                "status": "ok",
                "workers": 2,
                "max_dirs": 3,
                "max_files": 5,
                "timeout": 10,
                "max_depth": 3,
            }

    class _FakeSummaryDialog:
        def __init__(self, _parent, _theme, lines, _base_line):
            self.lines = lines

        def show(self):
            return True

    monkeypatch.setattr("gui.components.scan_preflight.ProbeConfigDialog", _FakeProbeConfigDialog)
    monkeypatch.setattr("gui.components.scan_preflight.SummaryDialog", _FakeSummaryDialog)

    controller = ScanPreflightController(
        parent=object(),
        theme=None,
        settings_manager=None,
        scan_options={
            "bulk_probe_enabled": True,
            "bulk_extract_enabled": False,
            "rce_enabled": False,
        },
        scan_description="test scan",
    )

    result = controller.run()

    assert result is not None
    assert any("depth 3" in line for line in controller.summary_lines)
