"""
Tests for _FtpScanOptionsMixin (Slice 9B extraction).

All mixin methods are exercised via FtpScanDialog (which inherits from the mixin)
using the same _make_dialog pattern as test_ftp_scan_dialog.py.

Requires a display (run under xvfb-run -a) because tk.BooleanVar / tk.StringVar /
tk.IntVar need a Tk root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
# Helper: build FtpScanDialog without creating the Toplevel window
# ---------------------------------------------------------------------------

def _make_dialog(tk_root, config_path=None, callback=None, settings_manager=None):
    """Instantiate FtpScanDialog with _create_dialog patched out."""
    from gui.components.ftp_scan_dialog import FtpScanDialog

    if callback is None:
        callback = MagicMock()
    if config_path is None:
        config_path = "/nonexistent/conf/config.json"

    with patch.object(FtpScanDialog, "_create_dialog"):
        dlg = FtpScanDialog(
            parent=tk_root,
            config_path=config_path,
            scan_start_callback=callback,
            settings_manager=settings_manager,
        )
    dlg.dialog = MagicMock()
    return dlg


# ===========================================================================
# TestFtpScanOptionsMixin
# ===========================================================================

class TestFtpScanOptionsMixin:

    # --- 1. _load_config_defaults ---

    def test_load_config_defaults_reads_config_values(self, tmp_path, tk_root):
        """Concurrency/timeout vars are populated from a valid config.json."""
        cfg = {
            "ftp": {
                "discovery": {"max_concurrent_hosts": 7},
                "access": {"max_concurrent_hosts": 3},
                "verification": {
                    "connect_timeout": 4,
                    "auth_timeout": 9,
                    "listing_timeout": 18,
                },
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        dlg = _make_dialog(tk_root, config_path=str(config_file))

        assert dlg.discovery_concurrency_var.get() == "7"
        assert dlg.access_concurrency_var.get() == "3"
        assert dlg.connect_timeout_var.get() == "4"
        assert dlg.auth_timeout_var.get() == "9"
        assert dlg.listing_timeout_var.get() == "18"

    def test_load_config_defaults_fallbacks_on_invalid_config(self, tk_root):
        """Hardcoded defaults are used when the config path does not exist."""
        dlg = _make_dialog(tk_root, config_path="/nonexistent/does_not_exist.json")

        assert dlg.discovery_concurrency_var.get() == "10"
        assert dlg.access_concurrency_var.get() == "4"
        assert dlg.connect_timeout_var.get() == "5"
        assert dlg.auth_timeout_var.get() == "10"
        assert dlg.listing_timeout_var.get() == "15"

    # --- 2. _load_initial_values ---

    def test_load_initial_values_with_settings_manager(self, tk_root):
        """All dialog vars are restored from settings manager when provided."""
        sm = MagicMock()
        stored = {
            "ftp_scan_dialog.max_shodan_results": 500,
            "ftp_scan_dialog.api_key_override": "KEY123",
            "ftp_scan_dialog.country_code": "DE",
            "ftp_scan_dialog.custom_filters": "org:TestISP",
            "ftp_scan_dialog.discovery_max_concurrent_hosts": 15,
            "ftp_scan_dialog.access_max_concurrent_hosts": 5,
            "ftp_scan_dialog.connect_timeout": 6,
            "ftp_scan_dialog.auth_timeout": 12,
            "ftp_scan_dialog.listing_timeout": 25,
            "ftp_scan_dialog.verbose": True,
            "ftp_scan_dialog.bulk_probe_enabled": True,
            "ftp_scan_dialog.region_africa": True,
            "ftp_scan_dialog.region_asia": False,
            "ftp_scan_dialog.region_europe": True,
            "ftp_scan_dialog.region_north_america": False,
            "ftp_scan_dialog.region_oceania": True,
            "ftp_scan_dialog.region_south_america": False,
        }
        sm.get_setting.side_effect = lambda key, default=None: stored.get(key, default)

        dlg = _make_dialog(tk_root, settings_manager=sm)

        assert dlg.max_results_var.get() == 500
        assert dlg.api_key_var.get() == "KEY123"
        assert dlg.country_var.get() == "DE"
        assert dlg.custom_filters_var.get() == "org:TestISP"
        assert dlg.discovery_concurrency_var.get() == "15"
        assert dlg.access_concurrency_var.get() == "5"
        assert dlg.connect_timeout_var.get() == "6"
        assert dlg.auth_timeout_var.get() == "12"
        assert dlg.listing_timeout_var.get() == "25"
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
        assert dlg.access_concurrency_var.get() == "4"

    # --- 3. _persist_dialog_state ---

    def test_persist_dialog_state_writes_expected_keys(self, tk_root):
        """set_setting is called with the correct key/value pairs for all fields."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.max_results_var.set(750)
        dlg.discovery_concurrency_var.set("8")
        dlg.access_concurrency_var.set("3")
        dlg.connect_timeout_var.set("7")
        dlg.auth_timeout_var.set("11")
        dlg.listing_timeout_var.set("20")
        dlg.api_key_var.set("  MY_KEY  ")
        dlg.country_var.set("us")
        dlg.custom_filters_var.set("port:21")
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
        assert calls["ftp_scan_dialog.max_shodan_results"] == 750
        assert calls["ftp_scan_dialog.discovery_max_concurrent_hosts"] == 8
        assert calls["ftp_scan_dialog.access_max_concurrent_hosts"] == 3
        assert calls["ftp_scan_dialog.connect_timeout"] == 7
        assert calls["ftp_scan_dialog.auth_timeout"] == 11
        assert calls["ftp_scan_dialog.listing_timeout"] == 20
        assert calls["ftp_scan_dialog.api_key_override"] == "MY_KEY"
        assert calls["ftp_scan_dialog.country_code"] == "US"
        assert calls["ftp_scan_dialog.custom_filters"] == "port:21"
        assert calls["ftp_scan_dialog.verbose"] is True
        assert calls["ftp_scan_dialog.bulk_probe_enabled"] is False
        assert calls["ftp_scan_dialog.region_africa"] is True
        assert calls["ftp_scan_dialog.region_asia"] is False
        assert calls["ftp_scan_dialog.region_europe"] is True

    def test_persist_dialog_state_ignores_out_of_range_ints(self, tk_root):
        """Concurrency value of 0 (below minimum=1) is NOT written to settings."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.discovery_concurrency_var.set("0")  # below minimum

        dlg._persist_dialog_state()

        written_keys = {c.args[0] for c in sm.set_setting.call_args_list}
        assert "ftp_scan_dialog.discovery_max_concurrent_hosts" not in written_keys

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
