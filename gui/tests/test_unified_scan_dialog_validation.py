"""Focused tests for unified scan dialog max-results validation behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def _make_dialog(max_results: str, *, show_rce_controls: bool = True) -> UnifiedScanDialog:
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
    dlg.show_rce_controls = show_rce_controls
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


def test_build_scan_request_forces_rce_disabled_when_controls_hidden():
    dlg = _make_dialog("250", show_rce_controls=False)
    dlg.rce_enabled_var.set(True)

    payload = dlg._build_scan_request()

    assert payload["rce_enabled"] is False


def test_no_live_max_results_clamp_method_exists():
    assert not hasattr(UnifiedScanDialog, "_validate_max_results")


def test_open_query_editor_opens_scan_dork_editor(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    calls = {"editor": 0}
    dlg._settings_manager = None
    dlg.config_path = Path("/tmp/config.json")
    dlg.dialog = object()
    dlg.query_editor_callback = lambda: (_ for _ in ()).throw(AssertionError("fallback should not run"))
    dlg._open_config_editor = lambda: (_ for _ in ()).throw(AssertionError("fallback should not run"))

    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.show_scan_dork_editor_dialog",
        lambda **_kwargs: calls.__setitem__("editor", calls["editor"] + 1),
    )

    dlg._open_query_editor()

    assert calls["editor"] == 1


def test_open_query_editor_falls_back_to_query_callback_when_editor_fails(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    calls = {"query": 0, "fallback": 0}
    dlg._settings_manager = None
    dlg.config_path = Path("/tmp/config.json")
    dlg.query_editor_callback = lambda: calls.__setitem__("query", calls["query"] + 1)
    dlg._open_config_editor = lambda: calls.__setitem__("fallback", calls["fallback"] + 1)
    dlg.dialog = object()

    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.show_scan_dork_editor_dialog",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    dlg._open_query_editor()

    assert calls["query"] == 1
    assert calls["fallback"] == 0


def test_open_query_editor_falls_back_to_config_editor_when_editor_fails(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = None
    dlg.config_path = Path("/tmp/config.json")
    dlg.query_editor_callback = None
    calls = {"fallback": 0, "warning": 0}
    dlg._open_config_editor = lambda: calls.__setitem__("fallback", calls["fallback"] + 1)
    dlg.dialog = object()

    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.show_scan_dork_editor_dialog",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showwarning",
        lambda *args, **kwargs: calls.__setitem__("warning", calls["warning"] + 1),
    )

    dlg._open_query_editor()

    assert calls["warning"] == 1
    assert calls["fallback"] == 1


def test_open_query_editor_shows_error_if_editor_and_query_fallback_fail(monkeypatch):
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = None
    dlg.config_path = Path("/tmp/config.json")
    dlg.query_editor_callback = lambda: (_ for _ in ()).throw(RuntimeError("fallback boom"))
    dlg._open_config_editor = lambda: (_ for _ in ()).throw(AssertionError("config fallback should not run"))
    dlg.dialog = object()
    calls = {"error": 0}

    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.show_scan_dork_editor_dialog",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.__setitem__("error", calls["error"] + 1),
    )

    dlg._open_query_editor()

    assert calls["error"] == 1


def test_show_unified_scan_dialog_passes_query_editor_callback(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def show(self):
            return "start"

    monkeypatch.setattr("gui.components.unified_scan_dialog.UnifiedScanDialog", _DialogStub)

    result = unified_scan_dialog.show_unified_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
        settings_manager=None,
        config_editor_callback=lambda _path: None,
        query_editor_callback=lambda: None,
    )

    assert result == "start"
    assert callable(captured["query_editor_callback"])
