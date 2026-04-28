"""Focused tests for unified scan dialog validation and UI state behavior."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.unified_scan_dialog as unified_scan_dialog
import gui.components.scan_dialog_layout as scan_dialog_layout
from gui.components.unified_scan_dialog import UnifiedScanDialog


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _DialogStub:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _CheckboxStub:
    def __init__(self):
        self.state = tk.NORMAL

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class _LabelStub:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _GrabDialogStub:
    def __init__(self, *, fail_wait_visibility: bool = False, fail_grab_set: bool = False):
        self.fail_wait_visibility = fail_wait_visibility
        self.fail_grab_set = fail_grab_set
        self.wait_visibility_calls = 0
        self.grab_set_calls = 0

    def wait_visibility(self):
        self.wait_visibility_calls += 1
        if self.fail_wait_visibility:
            raise RuntimeError("not viewable yet")

    def grab_set(self):
        self.grab_set_calls += 1
        if self.fail_grab_set:
            raise tk.TclError("grab failed")


def _make_dialog(*, show_rce_controls: bool = True) -> UnifiedScanDialog:
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
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
    dlg.config_path = Path("/tmp/config.json")
    dlg.show_rce_controls = show_rce_controls
    dlg.theme = object()
    dlg.dialog = _DialogStub()
    dlg.protocol_cost_label = _LabelStub()
    dlg.protocol_results_label = _LabelStub()
    dlg.result = None
    dlg._persist_dialog_state = lambda: None
    dlg._get_all_selected_countries = lambda _manual: ([], "")
    return dlg


def test_sync_skip_indicator_extract_state_unified_follows_bulk_extract_toggle():
    dlg = _make_dialog()
    dlg.skip_indicator_extract_checkbox = _CheckboxStub()

    dlg.bulk_extract_enabled_var.set(False)
    dlg._sync_skip_indicator_extract_state()
    assert dlg.skip_indicator_extract_checkbox.state == tk.DISABLED

    dlg.bulk_extract_enabled_var.set(True)
    dlg._sync_skip_indicator_extract_state()
    assert dlg.skip_indicator_extract_checkbox.state == tk.NORMAL


def test_sync_skip_indicator_extract_state_legacy_layout_follows_bulk_extract_toggle():
    class _LegacyStub:
        pass

    dlg = _LegacyStub()
    dlg.bulk_extract_enabled_var = _Var(False)
    dlg.skip_indicator_extract_checkbox = _CheckboxStub()

    scan_dialog_layout._sync_skip_indicator_extract_state(dlg)
    assert dlg.skip_indicator_extract_checkbox.state == tk.DISABLED

    dlg.bulk_extract_enabled_var.set(True)
    scan_dialog_layout._sync_skip_indicator_extract_state(dlg)
    assert dlg.skip_indicator_extract_checkbox.state == tk.NORMAL


def test_build_scan_request_requires_at_least_one_protocol():
    dlg = _make_dialog()
    dlg.protocol_smb_var.set(False)
    dlg.protocol_ftp_var.set(False)
    dlg.protocol_http_var.set(False)

    with pytest.raises(ValueError, match="Select at least one protocol"):
        dlg._build_scan_request()


def test_start_invalid_protocol_selection_shows_error(monkeypatch):
    dlg = _make_dialog()
    dlg.protocol_smb_var.set(False)
    dlg.protocol_ftp_var.set(False)
    dlg.protocol_http_var.set(False)
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
    assert "Select at least one protocol" in calls[0][0][1]
    assert started["count"] == 0
    assert dlg.dialog.destroyed is False


def test_start_valid_request_invokes_callback(monkeypatch):
    dlg = _make_dialog()
    captured = {}
    dlg.scan_start_callback = lambda payload: captured.setdefault("payload", payload)

    monkeypatch.setattr("gui.components.unified_scan_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.load_query_budget_state",
        lambda **_kwargs: {
            "smb_max_query_credits_per_scan": 2,
            "ftp_max_query_credits_per_scan": 3,
            "http_max_query_credits_per_scan": 4,
            "min_usable_hosts_target": 50,
        },
    )
    monkeypatch.setattr("gui.components.unified_scan_dialog.run_preflight", lambda *_args, **_kwargs: _args[3])

    dlg._start()

    assert "max_shodan_results" not in captured["payload"]
    assert captured["payload"]["smb_max_query_credits_per_scan"] == 2
    assert captured["payload"]["ftp_max_query_credits_per_scan"] == 3
    assert captured["payload"]["http_max_query_credits_per_scan"] == 4
    assert dlg.dialog.destroyed is True


def test_build_scan_request_forces_rce_disabled_when_controls_hidden():
    dlg = _make_dialog(show_rce_controls=False)
    dlg.rce_enabled_var.set(True)

    payload = dlg._build_scan_request()

    assert payload["rce_enabled"] is False


def test_no_live_max_results_clamp_method_exists():
    assert not hasattr(UnifiedScanDialog, "_validate_max_results")


def test_protocol_estimate_lines_show_selected_protocols_only(monkeypatch):
    dlg = _make_dialog()
    dlg.protocol_smb_var.set(True)
    dlg.protocol_ftp_var.set(True)
    dlg.protocol_http_var.set(False)

    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.load_query_budget_state",
        lambda **_kwargs: {
            "smb_max_query_credits_per_scan": 3,
            "ftp_max_query_credits_per_scan": 5,
            "http_max_query_credits_per_scan": 1,
            "min_usable_hosts_target": 50,
        },
    )

    dlg._refresh_protocol_estimate_lines()

    assert dlg.protocol_cost_label.text == "Est. cost: ~8 credits"
    assert dlg.protocol_results_label.text == "Est. initial results: SMB ~300   FTP ~500"


def test_cost_estimate_help_text_includes_key_contract_lines():
    dlg = _make_dialog()

    help_text = dlg._build_cost_estimate_help_text()

    assert "One API credit typically yields roughly 100 search results." in help_text
    assert "Query Budget sets how many credits each protocol can use per scan." in help_text
    assert "Initial Shodan search returns a list of candidates, not results." in help_text


def test_cost_estimate_help_click_routes_to_dialog_renderer(monkeypatch):
    dlg = _make_dialog()
    calls = {"count": 0}

    monkeypatch.setattr(
        dlg,
        "_show_cost_estimate_help_dialog",
        lambda: calls.__setitem__("count", calls["count"] + 1),
    )

    dlg._on_cost_estimate_help_clicked()

    assert calls["count"] == 1


def test_opening_cost_estimate_help_does_not_mutate_scan_request(monkeypatch):
    dlg = _make_dialog()
    monkeypatch.setattr(
        dlg,
        "_show_cost_estimate_help_dialog",
        lambda: None,
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.load_query_budget_state",
        lambda **_kwargs: {
            "smb_max_query_credits_per_scan": 2,
            "ftp_max_query_credits_per_scan": 3,
            "http_max_query_credits_per_scan": 4,
            "min_usable_hosts_target": 50,
        },
    )

    before = dlg._build_scan_request()
    dlg._on_cost_estimate_help_clicked()
    after = dlg._build_scan_request()

    assert before == after


def test_try_grab_dialog_swallows_visibility_and_grab_errors():
    dlg = _make_dialog()
    stub = _GrabDialogStub(fail_wait_visibility=True, fail_grab_set=True)

    dlg._try_grab_dialog(stub)

    assert stub.wait_visibility_calls == 1
    assert stub.grab_set_calls == 1


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
