"""
Tests for _HttpScanOptionsMixin (Slice 12A extraction).

All mixin methods are exercised via HttpScanDialog (which inherits from the mixin)
using the same _make_dialog pattern as test_ftp_scan_options_mixin.py.

Requires a display (run under xvfb-run -a) because tk.BooleanVar / tk.StringVar /
tk.IntVar need a Tk root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Tk fixture (module-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


# ---------------------------------------------------------------------------
# Helper: build HttpScanDialog without creating the Toplevel window
# ---------------------------------------------------------------------------

def _make_dialog(tk_root, config_path=None, callback=None, settings_manager=None):
    """Instantiate HttpScanDialog with _create_dialog patched out."""
    from gui.components.http_scan_dialog import HttpScanDialog

    if callback is None:
        callback = MagicMock()
    if config_path is None:
        config_path = "/nonexistent/conf/config.json"

    with patch.object(HttpScanDialog, "_create_dialog"):
        dlg = HttpScanDialog(
            parent=tk_root,
            config_path=config_path,
            scan_start_callback=callback,
            settings_manager=settings_manager,
        )
    dlg.dialog = MagicMock()
    return dlg


# ===========================================================================
# TestHttpScanOptionsMixin
# ===========================================================================

class TestHttpScanOptionsMixin:

    # --- 1. _load_config_defaults ---

    def test_load_config_defaults_reads_config_values(self, tmp_path, tk_root):
        """Concurrency/timeout vars are populated from a valid config.json."""
        cfg = {
            "http": {
                "discovery": {"max_concurrent_hosts": 12},
                "verification": {
                    "connect_timeout": 8,
                    "request_timeout": 20,
                },
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        dlg = _make_dialog(tk_root, config_path=str(config_file))

        assert dlg.discovery_concurrency_var.get() == "12"
        assert dlg.connect_timeout_var.get() == "8"
        assert dlg.request_timeout_var.get() == "20"

    def test_load_config_defaults_fallbacks_on_invalid_config(self, tk_root):
        """Hardcoded defaults are used when the config path does not exist."""
        dlg = _make_dialog(tk_root, config_path="/nonexistent/does_not_exist.json")

        assert dlg.discovery_concurrency_var.get() == "10"
        assert dlg.connect_timeout_var.get() == "5"
        assert dlg.request_timeout_var.get() == "15"

    # --- 2. _load_initial_values ---

    def test_load_initial_values_with_settings_manager(self, tk_root):
        """All dialog vars are restored from settings manager when provided."""
        sm = MagicMock()
        stored = {
            "http_scan_dialog.max_shodan_results": 500,
            "http_scan_dialog.api_key_override": "KEY123",
            "http_scan_dialog.country_code": "DE",
            "http_scan_dialog.custom_filters": "org:TestISP",
            "http_scan_dialog.discovery_max_concurrent_hosts": 15,
            "http_scan_dialog.connect_timeout": 6,
            "http_scan_dialog.request_timeout": 25,
            "http_scan_dialog.allow_insecure_tls": False,
            "http_scan_dialog.verbose": True,
            "http_scan_dialog.bulk_probe_enabled": True,
            "http_scan_dialog.region_africa": True,
            "http_scan_dialog.region_asia": False,
            "http_scan_dialog.region_europe": True,
            "http_scan_dialog.region_north_america": False,
            "http_scan_dialog.region_oceania": True,
            "http_scan_dialog.region_south_america": False,
        }
        sm.get_setting.side_effect = lambda key, default=None: stored.get(key, default)

        dlg = _make_dialog(tk_root, settings_manager=sm)

        assert dlg.max_results_var.get() == 500
        assert dlg.api_key_var.get() == "KEY123"
        assert dlg.country_var.get() == "DE"
        assert dlg.custom_filters_var.get() == "org:TestISP"
        assert dlg.discovery_concurrency_var.get() == "15"
        assert dlg.connect_timeout_var.get() == "6"
        assert dlg.request_timeout_var.get() == "25"
        assert dlg.allow_insecure_tls_var.get() is False
        assert dlg.verbose_var.get() is True
        assert dlg.bulk_probe_enabled_var.get() is True
        assert dlg.africa_var.get() is True
        assert dlg.asia_var.get() is False
        assert dlg.europe_var.get() is True
        assert dlg.north_america_var.get() is False
        assert dlg.oceania_var.get() is True
        assert dlg.south_america_var.get() is False

    def test_load_initial_values_handles_settings_exceptions(self, tk_root):
        """Dialog does not crash when settings manager raises on get_setting."""
        sm = MagicMock()
        sm.get_setting.side_effect = RuntimeError("settings unavailable")

        # Should not raise
        dlg = _make_dialog(tk_root, settings_manager=sm)

        # Defaults from _load_config_defaults remain in place
        assert dlg.discovery_concurrency_var.get() == "10"
        assert dlg.connect_timeout_var.get() == "5"

    # --- 3. _persist_dialog_state ---

    def test_persist_dialog_state_writes_expected_keys(self, tk_root):
        """set_setting is called with the correct key/value pairs for all fields."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.max_results_var.set(750)
        dlg.discovery_concurrency_var.set("8")
        dlg.connect_timeout_var.set("7")
        dlg.request_timeout_var.set("20")
        dlg.api_key_var.set("  MY_KEY  ")
        dlg.country_var.set("us")
        dlg.custom_filters_var.set("port:80")
        dlg.allow_insecure_tls_var.set(False)
        dlg.verbose_var.set(True)
        dlg.bulk_probe_enabled_var.set(False)
        dlg.africa_var.set(True)
        dlg.asia_var.set(False)
        dlg.europe_var.set(True)
        dlg.north_america_var.set(False)
        dlg.oceania_var.set(True)
        dlg.south_america_var.set(False)

        dlg._persist_dialog_state()

        calls = {c.args[0]: c.args[1] for c in sm.set_setting.call_args_list}
        assert calls["http_scan_dialog.max_shodan_results"] == 750
        assert calls["http_scan_dialog.discovery_max_concurrent_hosts"] == 8
        assert calls["http_scan_dialog.connect_timeout"] == 7
        assert calls["http_scan_dialog.request_timeout"] == 20
        assert calls["http_scan_dialog.api_key_override"] == "MY_KEY"
        assert calls["http_scan_dialog.country_code"] == "US"
        assert calls["http_scan_dialog.custom_filters"] == "port:80"
        assert calls["http_scan_dialog.allow_insecure_tls"] is False
        assert calls["http_scan_dialog.verbose"] is True
        assert calls["http_scan_dialog.bulk_probe_enabled"] is False
        assert calls["http_scan_dialog.region_africa"] is True
        assert calls["http_scan_dialog.region_asia"] is False
        assert calls["http_scan_dialog.region_europe"] is True

    def test_persist_dialog_state_ignores_out_of_range_ints(self, tk_root):
        """Concurrency value of 0 (below minimum=1) is NOT written to settings."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.discovery_concurrency_var.set("0")  # below minimum

        dlg._persist_dialog_state()

        written_keys = {c.args[0] for c in sm.set_setting.call_args_list}
        assert "http_scan_dialog.discovery_max_concurrent_hosts" not in written_keys

    def test_persist_dialog_state_no_settings_manager_noop(self, tk_root):
        """_persist_dialog_state returns silently when _settings_manager is None."""
        dlg = _make_dialog(tk_root, settings_manager=None)
        # Should not raise
        dlg._persist_dialog_state()

    def test_persist_dialog_state_swallow_settings_exceptions(self, tk_root):
        """Exceptions raised by set_setting do not propagate out of persist."""
        sm = MagicMock()
        sm.set_setting.side_effect = OSError("disk full")

        dlg = _make_dialog(tk_root, settings_manager=sm)
        # Should not raise
        dlg._persist_dialog_state()

    # --- 9. Constant contract ---

    def test_constant_contract_reexported_in_dialog_module(self, tk_root):
        """Constants are importable from http_scan_dialog and _build_scan_options uses them."""
        from gui.components.http_scan_dialog import (
            _CONCURRENCY_UPPER,
            _TIMEOUT_UPPER,
            HttpScanDialog,
        )

        assert _CONCURRENCY_UPPER == 256
        assert _TIMEOUT_UPPER == 300

        # Call _build_scan_options with valid vars to confirm no NameError.
        dlg = _make_dialog(tk_root)
        dlg.discovery_concurrency_var.set("10")
        dlg.connect_timeout_var.set("5")
        dlg.request_timeout_var.set("15")
        dlg.max_results_var.set(1000)
        dlg.custom_filters_var.set("")
        dlg.api_key_var.set("")
        dlg.verbose_var.set(False)
        dlg.allow_insecure_tls_var.set(True)
        dlg.bulk_probe_enabled_var.set(False)
        dlg.country_var.set("")
        dlg.africa_var.set(False)
        dlg.asia_var.set(False)
        dlg.europe_var.set(False)
        dlg.north_america_var.set(False)
        dlg.oceania_var.set(False)
        dlg.south_america_var.set(False)

        result = dlg._build_scan_options()

        assert "discovery_max_concurrent_hosts" in result
        assert "connect_timeout" in result
        assert "request_timeout" in result
        assert result["discovery_max_concurrent_hosts"] == 10
        assert result["connect_timeout"] == 5
        assert result["request_timeout"] == 15
