"""
C2 — Core Provider Registry For Start Scan
Tests for provider-selection scaffolding in UnifiedScanDialog.

Coverage:
- Provider var resolution (shodan/searxng/reddit, no censys)
- Launch guards (no provider, non-shodan provider)
- Template capture/apply with providers block
- Backward compat: stubs without provider vars default to shodan
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.unified_scan_dialog import UnifiedScanDialog


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _DialogStub:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class _LabelStub:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]


def _make_dialog() -> UnifiedScanDialog:
    """Stub dialog with provider vars — mirrors what __init__ would produce."""
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.provider_shodan_var = _Var(True)
    dlg.provider_searxng_var = _Var(False)
    dlg.provider_reddit_var = _Var(False)
    dlg.protocol_smb_var = _Var(True)
    dlg.protocol_ftp_var = _Var(False)
    dlg.protocol_http_var = _Var(False)
    dlg.smb_max_results_var = _Var("100")
    dlg.ftp_max_results_var = _Var("100")
    dlg.http_max_results_var = _Var("100")
    dlg.shared_concurrency_var = _Var("10")
    dlg.shared_timeout_var = _Var("10")
    dlg.country_var = _Var("")
    dlg.security_mode_var = _Var("cautious")
    dlg.verbose_var = _Var(False)
    dlg.bulk_probe_enabled_var = _Var(False)
    dlg.bulk_extract_enabled_var = _Var(False)
    dlg.skip_indicator_extract_var = _Var(True)
    dlg.allow_insecure_tls_var = _Var(True)
    dlg.africa_var = _Var(False)
    dlg.asia_var = _Var(False)
    dlg.europe_var = _Var(False)
    dlg.north_america_var = _Var(False)
    dlg.oceania_var = _Var(False)
    dlg.south_america_var = _Var(False)
    dlg._settings_manager = None
    dlg.config_path = Path("/tmp/config.json")
    dlg.dialog = _DialogStub()
    dlg.protocol_cost_label = _LabelStub()
    dlg.protocol_results_label = _LabelStub()
    dlg.region_status_label = None
    dlg.result = None
    dlg._persist_dialog_state = lambda: None
    dlg._get_all_selected_countries = lambda _manual: ([], "")
    return dlg


# ---------------------------------------------------------------------------
# Censys guard
# ---------------------------------------------------------------------------

def test_no_censys_provider_var():
    assert not hasattr(UnifiedScanDialog, "provider_censys_var")


def test_censys_not_in_resolve_providers_output():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(True)
    dlg.provider_searxng_var.set(True)
    dlg.provider_reddit_var.set(True)
    result = dlg._resolve_selected_providers()
    assert "censys" not in result


# ---------------------------------------------------------------------------
# Provider resolver
# ---------------------------------------------------------------------------

def test_resolve_providers_shodan_only():
    dlg = _make_dialog()
    assert dlg._resolve_selected_providers() == ["shodan"]


def test_resolve_providers_all_three():
    dlg = _make_dialog()
    dlg.provider_searxng_var.set(True)
    dlg.provider_reddit_var.set(True)
    result = dlg._resolve_selected_providers()
    assert result == ["shodan", "searxng", "reddit"]


def test_resolve_providers_empty_when_none():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    assert dlg._resolve_selected_providers() == []


def test_resolve_providers_searxng_only():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    assert dlg._resolve_selected_providers() == ["searxng"]


def test_resolve_providers_backward_compat_no_attrs():
    """Stubs without provider vars (pre-C2 test stubs) default to ['shodan']."""
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    result = dlg._resolve_selected_providers()
    assert result == ["shodan"]


# ---------------------------------------------------------------------------
# _build_scan_request — provider validation
# ---------------------------------------------------------------------------

def test_build_scan_request_includes_providers_key(monkeypatch):
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    dlg = _make_dialog()
    request = dlg._build_scan_request()
    assert "providers" in request
    assert request["providers"] == ["shodan"]


def test_build_scan_request_no_provider_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    with pytest.raises(ValueError, match="at least one discovery provider"):
        dlg._build_scan_request()


def test_build_scan_request_non_shodan_provider_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    with pytest.raises(ValueError, match="not yet available"):
        dlg._build_scan_request()


def test_build_scan_request_reddit_only_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    with pytest.raises(ValueError, match="not yet available"):
        dlg._build_scan_request()


# ---------------------------------------------------------------------------
# _start — error dialog behavior
# ---------------------------------------------------------------------------

def test_start_no_provider_shows_showerror(monkeypatch):
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    calls = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )
    dlg._start()
    assert len(calls) == 1
    assert calls[0][0] == "Invalid Input"
    assert "at least one discovery provider" in calls[0][1]
    assert dlg.dialog.destroyed is False


def test_start_searxng_only_shows_not_available_error(monkeypatch):
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    calls = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )
    dlg._start()
    assert len(calls) == 1
    assert "not yet available" in calls[0][1]
    assert dlg.dialog.destroyed is False


# ---------------------------------------------------------------------------
# Template capture/apply — providers block
# ---------------------------------------------------------------------------

def test_capture_form_state_includes_providers_block():
    dlg = _make_dialog()
    state = dlg._capture_form_state()
    assert "providers" in state
    assert state["providers"]["shodan"] is True
    assert state["providers"]["searxng"] is False
    assert state["providers"]["reddit"] is False


def test_capture_form_state_no_censys_in_providers():
    dlg = _make_dialog()
    state = dlg._capture_form_state()
    assert "censys" not in state.get("providers", {})


def test_apply_form_state_restores_providers():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg._apply_form_state({"providers": {"shodan": True, "searxng": False, "reddit": False}})
    assert dlg.provider_shodan_var.get() is True
    assert dlg.provider_searxng_var.get() is False
    assert dlg.provider_reddit_var.get() is False


def test_apply_form_state_defaults_shodan_true_when_providers_absent():
    """Templates saved before C2 (no providers key) restore with Shodan=True."""
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg._apply_form_state({})
    assert dlg.provider_shodan_var.get() is True


def test_apply_form_state_providers_roundtrip():
    dlg = _make_dialog()
    captured = dlg._capture_form_state()
    dlg.provider_shodan_var.set(False)
    dlg._apply_form_state(captured)
    assert dlg.provider_shodan_var.get() is True
    assert dlg.provider_searxng_var.get() is False
    assert dlg.provider_reddit_var.get() is False
