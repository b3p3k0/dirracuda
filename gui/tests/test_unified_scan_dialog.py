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
from gui.components.scan_provider_options import (
    REDDIT_MAX_REMINDER,
    SEARXNG_MAX_REMINDER,
    SEARXNG_PACING_REMINDER,
    load_reddit_settings,
    load_searxng_settings,
)


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
    dlg.searxng_instance_url_var = _Var("http://searxng.example.com")
    dlg.searxng_query_var = _Var("inurl:index.of")
    dlg.searxng_max_results_var = _Var("50")
    dlg.reddit_mode_var = _Var("feed")
    dlg.reddit_sort_var = _Var("new")
    dlg.reddit_top_window_var = _Var("week")
    dlg.reddit_max_posts_var = _Var("50")
    dlg.reddit_query_var = _Var("")
    dlg.reddit_username_var = _Var("")
    dlg.reddit_parse_body_var = _Var(True)
    dlg.reddit_include_nsfw_var = _Var(False)
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


def test_provider_maximum_reminders_are_exact():
    assert SEARXNG_MAX_REMINDER == "Maximum: 1,000 unique results per run."
    assert SEARXNG_PACING_REMINDER == (
        "Large runs are automatically paced to protect upstream engines."
    )
    assert REDDIT_MAX_REMINDER == "Maximum: 100 posts per RSS snapshot."


def test_provider_saved_caps_are_coerced_to_current_maximums():
    dlg = _make_dialog()

    class _Settings:
        values = {
            "unified_scan_dialog.searxng_max_results": 5000,
            "unified_scan_dialog.reddit_max_posts": 200,
        }

        def get_setting(self, key, default=None):
            return self.values.get(key, default)

    settings = _Settings()
    load_searxng_settings(dlg, settings)
    load_reddit_settings(dlg, settings)

    assert dlg.searxng_max_results_var.get() == "1000"
    assert dlg.reddit_max_posts_var.get() == "100"


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


def test_build_scan_request_searxng_only_is_valid(monkeypatch):
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    request = dlg._build_scan_request()
    assert "searxng" in request["providers"]
    assert "shodan" not in request["providers"]
    assert request["protocols"] == []
    assert request["searxng_instance_url"] == "http://searxng.example.com"


def test_build_scan_request_reddit_feed_valid(monkeypatch):
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("feed")
    request = dlg._build_scan_request()
    assert "reddit" in request["providers"]
    assert request["reddit_mode"] == "feed"
    assert request["reddit_parse_body"] is True
    assert request["reddit_include_nsfw"] is False
    assert request["reddit_replace_cache"] is False if "reddit_replace_cache" in request else True


def test_build_scan_request_reddit_search_valid(monkeypatch):
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("search")
    dlg.reddit_query_var.set("open directories")
    request = dlg._build_scan_request()
    assert request["reddit_mode"] == "search"
    assert request["reddit_query"] == "open directories"


def test_build_scan_request_reddit_search_missing_query_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("search")
    dlg.reddit_query_var.set("")
    with pytest.raises(ValueError, match="search mode requires a query"):
        dlg._build_scan_request()


def test_build_scan_request_reddit_user_mode_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("user")
    with pytest.raises(ValueError, match="Select feed or search"):
        dlg._build_scan_request()


def test_build_scan_request_reddit_invalid_mode_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("invalid_mode")
    with pytest.raises(ValueError, match="Invalid Reddit mode"):
        dlg._build_scan_request()


def test_build_scan_request_reddit_includes_all_required_fields(monkeypatch):
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_parse_body_var.set(True)
    dlg.reddit_include_nsfw_var.set(False)
    dlg.reddit_sort_var.set("top")
    dlg.reddit_top_window_var.set("month")
    dlg.reddit_max_posts_var.set("80")
    request = dlg._build_scan_request()
    for field in ("reddit_parse_body", "reddit_include_nsfw", "reddit_sort", "reddit_top_window", "reddit_max_posts"):
        assert field in request, f"Missing field: {field}"
    assert request["reddit_sort"] == "top"
    assert request["reddit_top_window"] == "month"
    assert request["reddit_max_posts"] == 80


def test_build_scan_request_searxng_missing_instance_url_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    dlg.searxng_instance_url_var.set("")
    with pytest.raises(ValueError, match="instance URL is required"):
        dlg._build_scan_request()


def test_build_scan_request_searxng_missing_query_raises():
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    dlg.searxng_query_var.set("")
    with pytest.raises(ValueError, match="search query is required"):
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


def test_start_searxng_only_skips_preflight(monkeypatch):
    """SearXNG-only launch must not invoke Shodan preflight (M5)."""
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_searxng_var.set(True)
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("preflight must not be called for SearXNG-only")),
    )
    captured = {}
    dlg.scan_start_callback = lambda payload: captured.setdefault("payload", payload)
    errors = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    dlg._start()
    assert errors == [], f"Unexpected error dialogs: {errors}"
    assert "payload" in captured
    assert dlg.dialog.destroyed is True


def test_start_shodan_provider_calls_preflight(monkeypatch):
    """Shodan provider must still invoke preflight (M5 complement)."""
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(True)
    dlg.theme = None
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    preflight_called = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda _dlg, _theme, _sm, request, _desc: preflight_called.append(True) or request,
    )
    dlg.scan_start_callback = lambda _: None
    dlg._start()
    assert preflight_called == [True]


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


def test_apply_form_state_country_codes_win_over_regions():
    dlg = _make_dialog()
    dlg._apply_form_state(
        {
            "country_code": "US,CA",
            "regions": {
                "africa": True,
                "asia": True,
            },
        }
    )

    assert dlg.country_var.get() == "US,CA"
    assert dlg.africa_var.get() is False
    assert dlg.asia_var.get() is False


def test_build_scan_request_rejects_mixed_country_and_region_targeting():
    dlg = _make_dialog()
    dlg.country_var.set("US")
    dlg.africa_var.set(True)
    dlg._get_all_selected_countries = (
        UnifiedScanDialog._get_all_selected_countries.__get__(
            dlg,
            UnifiedScanDialog,
        )
    )

    with pytest.raises(ValueError, match="either individual country codes or region"):
        dlg._build_scan_request()


def test_capture_form_state_includes_reddit_options_block():
    dlg = _make_dialog()
    dlg.reddit_mode_var.set("search")
    dlg.reddit_sort_var.set("top")
    dlg.reddit_query_var.set("open dirs")
    state = dlg._capture_form_state()
    assert "reddit_options" in state
    opts = state["reddit_options"]
    assert opts["mode"] == "search"
    assert opts["sort"] == "top"
    assert opts["query"] == "open dirs"
    for key in ("top_window", "max_posts", "username", "parse_body", "include_nsfw"):
        assert key in opts, f"Missing reddit_options key: {key}"


def test_apply_form_state_restores_reddit_options():
    dlg = _make_dialog()
    state = {
        "reddit_options": {
            "mode": "user",
            "sort": "new",
            "top_window": "day",
            "max_posts": "30",
            "query": "some query",
            "username": "reddit_user",
            "parse_body": False,
            "include_nsfw": True,
        }
    }
    dlg._apply_form_state(state)
    assert dlg.reddit_mode_var.get() == "feed"
    assert dlg.reddit_username_var.get() == ""
    assert dlg.reddit_parse_body_var.get() is False
    assert dlg.reddit_include_nsfw_var.get() is True


def test_start_reddit_only_skips_preflight(monkeypatch):
    """Reddit-only launch must not invoke Shodan preflight."""
    dlg = _make_dialog()
    dlg.provider_shodan_var.set(False)
    dlg.provider_reddit_var.set(True)
    dlg.reddit_mode_var.set("feed")
    monkeypatch.setattr("gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.run_preflight",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("preflight must not be called for Reddit-only")),
    )
    captured = {}
    dlg.scan_start_callback = lambda payload: captured.setdefault("payload", payload)
    errors = []
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.messagebox.showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    dlg._start()
    assert errors == [], f"Unexpected error dialogs: {errors}"
    assert "payload" in captured
    assert dlg.dialog.destroyed is True
