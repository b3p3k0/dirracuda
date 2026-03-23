"""
Tests for options/state methods extracted from scan_dialog.py into
_ScanDialogOptionsMixin (Slice 7C refactor).

Exercises the methods via ScanDialog (the concrete class) with _create_dialog
patched out, so no real Toplevel is created but all __init__ state is present.

Requires a Tk root (xvfb on headless Linux).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Tk fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


# ---------------------------------------------------------------------------
# Helper: build ScanDialog without creating the Toplevel window
# ---------------------------------------------------------------------------

def _make_dialog(tk_root):
    """Instantiate ScanDialog with _create_dialog patched out."""
    from gui.components.scan_dialog import ScanDialog

    with patch.object(ScanDialog, "_create_dialog"):
        dlg = ScanDialog(
            parent=tk_root,
            config_path="/nonexistent/conf/config.json",
            config_editor_callback=MagicMock(),
            scan_start_callback=MagicMock(),
            settings_manager=None,
        )
    dlg.dialog = MagicMock()
    return dlg


# ===========================================================================
# TestBuildScanOptions
# ===========================================================================

_EXPECTED_KEYS = {
    'country',
    'max_shodan_results',
    'recent_hours',
    'rescan_all',
    'rescan_failed',
    'api_key_override',
    'custom_filters',
    'discovery_max_concurrent_hosts',
    'access_max_concurrent_hosts',
    'rate_limit_delay',
    'share_access_delay',
    'security_mode',
    'verbose',
    'rce_enabled',
    'bulk_probe_enabled',
    'bulk_extract_enabled',
    'bulk_extract_skip_indicators',
}


class TestBuildScanOptions:

    def test_returns_all_expected_keys(self, tk_root):
        dlg = _make_dialog(tk_root)
        result = dlg._build_scan_options(country_param="US")
        assert set(result.keys()) == _EXPECTED_KEYS

    def test_country_param_propagated(self, tk_root):
        dlg = _make_dialog(tk_root)
        result = dlg._build_scan_options(country_param="DE")
        assert result['country'] == "DE"

    def test_security_mode_invalid_coerced_to_cautious(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.security_mode_var.set("invalid")
        result = dlg._build_scan_options(country_param=None)
        assert result['security_mode'] == "cautious"

    def test_security_mode_legacy_preserved(self, tk_root):
        dlg = _make_dialog(tk_root)
        # Patch the confirmation dialog so the trace allows the mode change
        with patch("gui.components.scan_dialog.messagebox.askokcancel", return_value=True):
            dlg.security_mode_var.set("legacy")
        result = dlg._build_scan_options(country_param=None)
        assert result['security_mode'] == "legacy"

    def test_persists_to_settings_manager_when_present(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg._settings_manager = MagicMock()
        dlg._build_scan_options(country_param="US")
        assert dlg._settings_manager.set_setting.called

    def test_settings_manager_exception_does_not_raise(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg._settings_manager = MagicMock()
        dlg._settings_manager.set_setting.side_effect = RuntimeError("boom")
        # Should not raise
        result = dlg._build_scan_options(country_param="US")
        assert isinstance(result, dict)

    def test_empty_api_key_returns_none(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.api_key_var.set("   ")
        result = dlg._build_scan_options(country_param=None)
        assert result['api_key_override'] is None

    def test_nonempty_api_key_preserved(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.api_key_var.set("mykey123")
        result = dlg._build_scan_options(country_param=None)
        assert result['api_key_override'] == "mykey123"

    def test_recent_hours_empty_string_returns_none(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.recent_hours_var.set("")
        result = dlg._build_scan_options(country_param=None)
        assert result['recent_hours'] is None

    def test_recent_hours_numeric_string_converted(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.recent_hours_var.set("48")
        result = dlg._build_scan_options(country_param=None)
        assert result['recent_hours'] == 48


# ===========================================================================
# TestLoadInitialValues
# ===========================================================================

class TestLoadInitialValues:

    def test_no_settings_manager_is_noop(self, tk_root):
        dlg = _make_dialog(tk_root)
        assert dlg._settings_manager is None
        # Should not raise
        dlg._load_initial_values()

    def test_hydrates_max_results_from_settings_manager(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        sm.get_setting.side_effect = lambda key, default=None: {
            'scan_dialog.max_shodan_results': 500,
        }.get(key, default)
        dlg._settings_manager = sm
        dlg._load_initial_values()
        assert dlg.max_results_var.get() == 500

    def test_hydrates_security_mode_from_settings_manager(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        sm.get_setting.side_effect = lambda key, default=None: {
            'scan_dialog.security_mode': 'legacy',
        }.get(key, default)
        dlg._settings_manager = sm
        # Patch the confirmation dialog so the trace allows the mode change
        with patch("gui.components.scan_dialog.messagebox.askokcancel", return_value=True):
            dlg._load_initial_values()
        assert dlg.security_mode_var.get() == "legacy"

    def test_invalid_security_mode_not_set(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.security_mode_var.set("cautious")
        sm = MagicMock()
        sm.get_setting.side_effect = lambda key, default=None: {
            'scan_dialog.security_mode': 'badvalue',
        }.get(key, default)
        dlg._settings_manager = sm
        dlg._load_initial_values()
        # "badvalue" is not in ("cautious", "legacy") so var should stay "cautious"
        assert dlg.security_mode_var.get() == "cautious"

    def test_settings_manager_exception_falls_back_gracefully(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        sm.get_setting.side_effect = RuntimeError("storage failure")
        dlg._settings_manager = sm
        # Should not raise
        dlg._load_initial_values()

    def test_region_vars_hydrated(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        sm.get_setting.side_effect = lambda key, default=None: {
            'scan_dialog.region_europe': True,
            'scan_dialog.region_africa': False,
        }.get(key, default)
        dlg._settings_manager = sm
        dlg._load_initial_values()
        assert dlg.europe_var.get() is True


# ===========================================================================
# TestPersistQuickSettings
# ===========================================================================

class TestPersistQuickSettings:

    def test_no_settings_manager_returns_early(self, tk_root):
        dlg = _make_dialog(tk_root)
        assert dlg._settings_manager is None
        # Should not raise
        dlg._persist_quick_settings()

    def test_persists_valid_concurrency_and_security(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        dlg._settings_manager = sm
        dlg.discovery_concurrency_var.set("4")
        dlg.access_concurrency_var.set("2")
        dlg.security_mode_var.set("cautious")
        dlg._persist_quick_settings()
        persisted_keys = {c.args[0] for c in sm.set_setting.call_args_list}
        assert 'scan_dialog.discovery_max_concurrency' in persisted_keys
        assert 'scan_dialog.access_max_concurrency' in persisted_keys
        assert 'scan_dialog.security_mode' in persisted_keys

    def test_out_of_range_concurrency_not_persisted(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        dlg._settings_manager = sm
        dlg.discovery_concurrency_var.set("999")  # above _concurrency_upper_limit (256)
        dlg._persist_quick_settings()
        persisted_keys = {c.args[0] for c in sm.set_setting.call_args_list}
        assert 'scan_dialog.discovery_max_concurrency' not in persisted_keys

    def test_invalid_concurrency_string_not_persisted(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        dlg._settings_manager = sm
        dlg.discovery_concurrency_var.set("abc")
        dlg._persist_quick_settings()
        persisted_keys = {c.args[0] for c in sm.set_setting.call_args_list}
        assert 'scan_dialog.discovery_max_concurrency' not in persisted_keys

    def test_exception_in_settings_manager_does_not_raise(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        sm.set_setting.side_effect = OSError("disk full")
        dlg._settings_manager = sm
        dlg.discovery_concurrency_var.set("4")
        dlg.access_concurrency_var.set("2")
        # Should not raise
        dlg._persist_quick_settings()

    def test_security_mode_invalid_coerced_to_cautious_on_persist(self, tk_root):
        dlg = _make_dialog(tk_root)
        sm = MagicMock()
        dlg._settings_manager = sm
        dlg.security_mode_var.set("garbage")
        dlg._persist_quick_settings()
        persisted = {c.args[0]: c.args[1] for c in sm.set_setting.call_args_list}
        assert persisted.get('scan_dialog.security_mode') == "cautious"
