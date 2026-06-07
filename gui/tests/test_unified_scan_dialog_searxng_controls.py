"""C11B — SearXNG tuning controls in UnifiedScanDialog.

Coverage:
- Default DoubleVar values for the three tuning controls.
- Bounds: minimum, maximum, below-min, above-max.
- Fractional and non-numeric coercion via load/apply paths.
- Settings round-trip: persist then reload.
- Template capture includes all three keys in searxng_options.
- Template apply sets vars; missing keys fall back to defaults.
- _build_scan_request propagates keys when SearXNG is selected.
- Keys absent when only Shodan is selected.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.scan_provider_options import (
    SEARXNG_LONG_RETRY_DEFAULT,
    SEARXNG_LONG_RETRY_MAX,
    SEARXNG_LONG_RETRY_MIN,
    SEARXNG_SHORT_RETRY_DEFAULT,
    SEARXNG_SHORT_RETRY_MAX,
    SEARXNG_SHORT_RETRY_MIN,
    SEARXNG_TIMEOUT_DEFAULT,
    SEARXNG_TIMEOUT_MAX,
    SEARXNG_TIMEOUT_MIN,
    load_searxng_settings,
    persist_searxng_settings,
    apply_searxng_form_state,
)
from gui.components.unified_scan_dialog import UnifiedScanDialog


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _DblVar(_Var):
    """Thin DoubleVar stub that stores a float."""

    def get(self) -> float:
        return float(self._value) if self._value is not None else 0.0

    def set(self, value) -> None:
        self._value = float(value)


class _StrVar(_Var):
    def get(self) -> str:
        return str(self._value) if self._value is not None else ""

    def set(self, value) -> None:
        self._value = str(value)


class _DialogStub:
    def destroy(self) -> None:
        pass


class _LabelStub:
    def configure(self, **kwargs) -> None:
        pass


class _Settings:
    def __init__(self, overrides=None) -> None:
        self._store: Dict[str, Any] = dict(overrides or {})

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._store[key] = value


def _make_dialog() -> UnifiedScanDialog:
    """Build a stub dialog with only the attributes the tests exercise."""
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.provider_shodan_var = _Var(True)
    dlg.provider_searxng_var = _Var(False)
    dlg.provider_reddit_var = _Var(False)
    dlg.searxng_instance_url_var = _StrVar("http://searxng.example.com")
    dlg.searxng_query_var = _StrVar("inurl:index.of")
    dlg.searxng_max_results_var = _StrVar("500")
    dlg.searxng_request_timeout_var = _DblVar(15.0)
    dlg.searxng_short_retry_delay_var = _DblVar(30.0)
    dlg.searxng_long_retry_delay_var = _DblVar(180.0)
    dlg.reddit_mode_var = _Var("feed")
    dlg.reddit_sort_var = _Var("new")
    dlg.reddit_top_window_var = _Var("week")
    dlg.reddit_max_posts_var = _Var("100")
    dlg.reddit_query_var = _Var("")
    dlg.reddit_username_var = _Var("")
    dlg.reddit_parse_body_var = _Var(True)
    dlg.reddit_include_nsfw_var = _Var(False)
    dlg.protocol_smb_var = _Var(True)
    dlg.protocol_ftp_var = _Var(False)
    dlg.protocol_http_var = _Var(False)
    dlg.smb_max_results_var = _StrVar("100")
    dlg.ftp_max_results_var = _StrVar("100")
    dlg.http_max_results_var = _StrVar("100")
    dlg.shared_concurrency_var = _StrVar("10")
    dlg.shared_timeout_var = _StrVar("10")
    dlg.country_var = _StrVar("")
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
# Default values
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_request_timeout_default(self):
        dlg = _make_dialog()
        assert dlg.searxng_request_timeout_var.get() == 15.0

    def test_short_retry_default(self):
        dlg = _make_dialog()
        assert dlg.searxng_short_retry_delay_var.get() == 30.0

    def test_long_retry_default(self):
        dlg = _make_dialog()
        assert dlg.searxng_long_retry_delay_var.get() == 180.0


# ---------------------------------------------------------------------------
# Load from settings (bounds and coercion)
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def _load(self, overrides):
        dlg = _make_dialog()
        sm = _Settings(overrides)
        load_searxng_settings(dlg, sm)
        return dlg

    def test_minimums_accepted(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": 5,
            "unified_scan_dialog.searxng_short_retry_delay": 5,
            "unified_scan_dialog.searxng_long_retry_delay": 60,
        })
        assert dlg.searxng_request_timeout_var.get() == 5.0
        assert dlg.searxng_short_retry_delay_var.get() == 5.0
        assert dlg.searxng_long_retry_delay_var.get() == 60.0

    def test_maximums_accepted(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": 60,
            "unified_scan_dialog.searxng_short_retry_delay": 60,
            "unified_scan_dialog.searxng_long_retry_delay": 300,
        })
        assert dlg.searxng_request_timeout_var.get() == 60.0
        assert dlg.searxng_short_retry_delay_var.get() == 60.0
        assert dlg.searxng_long_retry_delay_var.get() == 300.0

    def test_below_min_clamped(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": 2,
            "unified_scan_dialog.searxng_short_retry_delay": 2,
            "unified_scan_dialog.searxng_long_retry_delay": 30,
        })
        assert dlg.searxng_request_timeout_var.get() == 5.0
        assert dlg.searxng_short_retry_delay_var.get() == 5.0
        assert dlg.searxng_long_retry_delay_var.get() == 60.0

    def test_above_max_clamped(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": 100,
            "unified_scan_dialog.searxng_short_retry_delay": 100,
            "unified_scan_dialog.searxng_long_retry_delay": 500,
        })
        assert dlg.searxng_request_timeout_var.get() == 60.0
        assert dlg.searxng_short_retry_delay_var.get() == 60.0
        assert dlg.searxng_long_retry_delay_var.get() == 300.0

    def test_fractional_string_coerced_step1(self):
        dlg = self._load({"unified_scan_dialog.searxng_request_timeout": "15.7"})
        assert dlg.searxng_request_timeout_var.get() == 16.0

    def test_fractional_string_coerced_step5(self):
        dlg = self._load({"unified_scan_dialog.searxng_short_retry_delay": "32.7"})
        assert dlg.searxng_short_retry_delay_var.get() == 35.0

    def test_non_numeric_uses_default(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": "bad",
            "unified_scan_dialog.searxng_short_retry_delay": "bad",
            "unified_scan_dialog.searxng_long_retry_delay": "bad",
        })
        assert dlg.searxng_request_timeout_var.get() == float(SEARXNG_TIMEOUT_DEFAULT)
        assert dlg.searxng_short_retry_delay_var.get() == float(SEARXNG_SHORT_RETRY_DEFAULT)
        assert dlg.searxng_long_retry_delay_var.get() == float(SEARXNG_LONG_RETRY_DEFAULT)

    def test_none_uses_default(self):
        dlg = self._load({
            "unified_scan_dialog.searxng_request_timeout": None,
            "unified_scan_dialog.searxng_short_retry_delay": None,
            "unified_scan_dialog.searxng_long_retry_delay": None,
        })
        assert dlg.searxng_request_timeout_var.get() == float(SEARXNG_TIMEOUT_DEFAULT)
        assert dlg.searxng_short_retry_delay_var.get() == float(SEARXNG_SHORT_RETRY_DEFAULT)
        assert dlg.searxng_long_retry_delay_var.get() == float(SEARXNG_LONG_RETRY_DEFAULT)

    def test_absent_key_uses_default(self):
        dlg = self._load({})
        assert dlg.searxng_request_timeout_var.get() == float(SEARXNG_TIMEOUT_DEFAULT)
        assert dlg.searxng_short_retry_delay_var.get() == float(SEARXNG_SHORT_RETRY_DEFAULT)
        assert dlg.searxng_long_retry_delay_var.get() == float(SEARXNG_LONG_RETRY_DEFAULT)


# ---------------------------------------------------------------------------
# Settings round-trip: persist then reload
# ---------------------------------------------------------------------------

class TestSettingsRoundTrip:
    def test_round_trip(self):
        dlg = _make_dialog()
        dlg.searxng_request_timeout_var.set(20.0)
        dlg.searxng_short_retry_delay_var.set(45.0)
        dlg.searxng_long_retry_delay_var.set(240.0)
        sm = _Settings()
        persist_searxng_settings(dlg, sm)

        dlg2 = _make_dialog()
        load_searxng_settings(dlg2, sm)
        assert dlg2.searxng_request_timeout_var.get() == 20.0
        assert dlg2.searxng_short_retry_delay_var.get() == 45.0
        assert dlg2.searxng_long_retry_delay_var.get() == 240.0

    def test_persisted_values_are_integers(self):
        dlg = _make_dialog()
        dlg.searxng_request_timeout_var.set(20.0)
        sm = _Settings()
        persist_searxng_settings(dlg, sm)
        stored = sm.get_setting("unified_scan_dialog.searxng_request_timeout")
        assert isinstance(stored, int)
        assert stored == 20


# ---------------------------------------------------------------------------
# Template capture
# ---------------------------------------------------------------------------

class TestTemplateCapture:
    def test_capture_includes_tuning_keys(self):
        dlg = _make_dialog()
        state = dlg._capture_form_state()
        opts = state.get("searxng_options", {})
        assert "request_timeout" in opts
        assert "short_retry_delay" in opts
        assert "long_retry_delay" in opts

    def test_capture_values_match_vars(self):
        dlg = _make_dialog()
        dlg.searxng_request_timeout_var.set(25.0)
        dlg.searxng_short_retry_delay_var.set(40.0)
        dlg.searxng_long_retry_delay_var.set(120.0)
        opts = dlg._capture_form_state()["searxng_options"]
        assert opts["request_timeout"] == 25
        assert opts["short_retry_delay"] == 40
        assert opts["long_retry_delay"] == 120

    def test_capture_values_are_integers(self):
        dlg = _make_dialog()
        opts = dlg._capture_form_state()["searxng_options"]
        assert isinstance(opts["request_timeout"], int)
        assert isinstance(opts["short_retry_delay"], int)
        assert isinstance(opts["long_retry_delay"], int)


# ---------------------------------------------------------------------------
# Template apply (apply_searxng_form_state)
# ---------------------------------------------------------------------------

class TestTemplateApply:
    def test_apply_sets_vars(self):
        dlg = _make_dialog()
        apply_searxng_form_state(dlg, {
            "instance_url": "http://x",
            "query": "q",
            "max_results": "500",
            "request_timeout": 20,
            "short_retry_delay": 45,
            "long_retry_delay": 240,
        })
        assert dlg.searxng_request_timeout_var.get() == 20.0
        assert dlg.searxng_short_retry_delay_var.get() == 45.0
        assert dlg.searxng_long_retry_delay_var.get() == 240.0

    def test_apply_missing_keys_uses_defaults(self):
        dlg = _make_dialog()
        apply_searxng_form_state(dlg, {"instance_url": "http://x", "query": "q"})
        assert dlg.searxng_request_timeout_var.get() == float(SEARXNG_TIMEOUT_DEFAULT)
        assert dlg.searxng_short_retry_delay_var.get() == float(SEARXNG_SHORT_RETRY_DEFAULT)
        assert dlg.searxng_long_retry_delay_var.get() == float(SEARXNG_LONG_RETRY_DEFAULT)

    def test_apply_out_of_range_clamped(self):
        dlg = _make_dialog()
        apply_searxng_form_state(dlg, {
            "request_timeout": 999,
            "short_retry_delay": -5,
            "long_retry_delay": 999,
        })
        assert dlg.searxng_request_timeout_var.get() == float(SEARXNG_TIMEOUT_MAX)
        assert dlg.searxng_short_retry_delay_var.get() == float(SEARXNG_SHORT_RETRY_MIN)
        assert dlg.searxng_long_retry_delay_var.get() == float(SEARXNG_LONG_RETRY_MAX)


# ---------------------------------------------------------------------------
# Scan request propagation
# ---------------------------------------------------------------------------

class TestScanRequest:
    def _make_searxng_dialog(self):
        dlg = _make_dialog()
        dlg.provider_searxng_var = _Var(True)
        dlg.provider_shodan_var = _Var(False)
        dlg.searxng_instance_url_var = _StrVar("http://searxng.example.com")
        dlg.searxng_query_var = _StrVar("intitle:index")
        return dlg

    def test_scan_request_includes_tuning_keys(self):
        dlg = self._make_searxng_dialog()
        req = dlg._build_scan_request()
        assert "searxng_request_timeout" in req
        assert "searxng_short_retry_delay" in req
        assert "searxng_long_retry_delay" in req

    def test_scan_request_values_match_vars(self):
        dlg = self._make_searxng_dialog()
        dlg.searxng_request_timeout_var.set(25.0)
        dlg.searxng_short_retry_delay_var.set(40.0)
        dlg.searxng_long_retry_delay_var.set(120.0)
        req = dlg._build_scan_request()
        assert req["searxng_request_timeout"] == 25
        assert req["searxng_short_retry_delay"] == 40
        assert req["searxng_long_retry_delay"] == 120

    def test_scan_request_omits_keys_when_no_searxng(self):
        dlg = _make_dialog()
        dlg.provider_shodan_var = _Var(True)
        dlg.provider_searxng_var = _Var(False)
        req = dlg._build_scan_request()
        assert "searxng_request_timeout" not in req
        assert "searxng_short_retry_delay" not in req
        assert "searxng_long_retry_delay" not in req
