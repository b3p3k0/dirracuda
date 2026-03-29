"""Focused tests for unified scan dialog max-results validation behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.unified_scan_dialog import UnifiedScanDialog


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _EntryStub:
    def __init__(self):
        self.focused = False
        self.selected = None

    def focus_set(self):
        self.focused = True

    def select_range(self, start, end):
        self.selected = (start, end)


class _DialogStub:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def _make_dialog(max_results: str) -> UnifiedScanDialog:
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.max_results_var = _Var(max_results)
    dlg.shared_concurrency_var = _Var("10")
    dlg.shared_timeout_var = _Var("10")
    dlg.protocol_smb_var = _Var(True)
    dlg.protocol_ftp_var = _Var(False)
    dlg.protocol_http_var = _Var(False)
    dlg.country_var = _Var("")
    dlg.security_mode_var = _Var("cautious")
    dlg.custom_filters_var = _Var("")
    dlg.verbose_var = _Var(False)
    dlg.bulk_probe_enabled_var = _Var(False)
    dlg.bulk_extract_enabled_var = _Var(False)
    dlg.skip_indicator_extract_var = _Var(True)
    dlg.rce_enabled_var = _Var(False)
    dlg.allow_insecure_tls_var = _Var(True)
    dlg._settings_manager = None
    dlg.theme = object()
    dlg.dialog = _DialogStub()
    dlg.max_results_entry = _EntryStub()
    dlg.result = None
    dlg._persist_dialog_state = lambda: None
    dlg._get_all_selected_countries = lambda _manual: ([], "")
    return dlg


@pytest.mark.parametrize("raw", ["", "0", "-1", "1001", "abc"])
def test_build_scan_request_rejects_invalid_max_results_without_mutating(raw):
    dlg = _make_dialog(raw)

    with pytest.raises(ValueError, match="Max Shodan Results"):
        dlg._build_scan_request()

    assert dlg.max_results_var.get() == raw


def test_start_invalid_max_results_shows_error_and_refocuses(monkeypatch):
    dlg = _make_dialog("")
    started = {"count": 0}
    dlg.scan_start_callback = lambda _payload: started.__setitem__("count", started["count"] + 1)

    calls = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("run_preflight must not be called")),
    )

    dlg._start()

    assert len(calls) == 1
    assert calls[0][0][0] == "Invalid Input"
    assert "Max Shodan Results" in calls[0][0][1]
    assert started["count"] == 0
    assert dlg.max_results_entry.focused is True
    assert dlg.max_results_entry.selected is not None
    assert dlg.dialog.destroyed is False


def test_start_valid_max_results_invokes_callback(monkeypatch):
    dlg = _make_dialog("250")
    captured = {}
    dlg.scan_start_callback = lambda payload: captured.setdefault("payload", payload)

    monkeypatch.setattr("gui.components.unified_scan_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.unified_scan_dialog.run_preflight", lambda *_args, **_kwargs: _args[3])

    dlg._start()

    assert captured["payload"]["max_shodan_results"] == 250
    assert dlg.dialog.destroyed is True


def test_no_live_max_results_clamp_method_exists():
    assert not hasattr(UnifiedScanDialog, "_validate_max_results")
