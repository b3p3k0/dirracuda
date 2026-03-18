"""
Tests for FtpScanDialog + dashboard wiring + scan_manager FTP overrides.

Categories:
  1. Dialog option-build (types, defaults, empty optional fields, country)
     — require a Tk root; run under xvfb-run or a real display.
  2. scan_manager config override plumbing
     — pure unit test, no Tk required.
  3. Dashboard wiring
     — pure unit test, no Tk required (patches DashboardWidget methods directly).
"""

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ---------------------------------------------------------------------------
# Tk fixture — shared by all dialog tests in this module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tk_root():
    """Create a hidden Tk root for the duration of the module."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


# ---------------------------------------------------------------------------
# Helper: build a FtpScanDialog without actually creating the Toplevel window
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
    # Provide a mock dialog widget so _start() / _cancel() don't crash on
    # dialog.destroy() calls in tests that exercise those paths.
    dlg.dialog = MagicMock()
    return dlg


# ===========================================================================
# 1. Dialog option-build tests
# ===========================================================================

class TestFtpScanDialogOptionBuild:

    def test_scan_options_keys(self, tk_root):
        """_build_scan_options() returns all expected keys."""
        dlg = _make_dialog(tk_root)
        opts = dlg._build_scan_options()
        expected_keys = {
            "country",
            "max_shodan_results",
            "api_key_override",
            "custom_filters",
            "discovery_max_concurrent_hosts",
            "access_max_concurrent_hosts",
            "connect_timeout",
            "auth_timeout",
            "listing_timeout",
            "verbose",
            "bulk_probe_enabled",
        }
        assert set(opts.keys()) == expected_keys

    def test_scan_options_types(self, tk_root):
        """Every field has the correct Python type."""
        dlg = _make_dialog(tk_root)
        opts = dlg._build_scan_options()
        # country is None (global) or str
        assert opts["country"] is None or isinstance(opts["country"], str)
        assert isinstance(opts["max_shodan_results"], int)
        assert opts["api_key_override"] is None or isinstance(opts["api_key_override"], str)
        assert isinstance(opts["custom_filters"], str)
        assert isinstance(opts["discovery_max_concurrent_hosts"], int)
        assert isinstance(opts["access_max_concurrent_hosts"], int)
        assert isinstance(opts["connect_timeout"], int)
        assert isinstance(opts["auth_timeout"], int)
        assert isinstance(opts["listing_timeout"], int)
        assert isinstance(opts["verbose"], bool)
        assert isinstance(opts["bulk_probe_enabled"], bool)

    def test_defaults(self, tk_root):
        """Defaults match the spec when no config file exists."""
        dlg = _make_dialog(tk_root)
        opts = dlg._build_scan_options()
        # No country selected → global scan
        assert opts["country"] is None
        assert opts["max_shodan_results"] == 1000
        assert opts["custom_filters"] == ""
        assert opts["verbose"] is False
        assert opts["discovery_max_concurrent_hosts"] == 10
        assert opts["access_max_concurrent_hosts"] == 4
        assert opts["connect_timeout"] == 5
        assert opts["auth_timeout"] == 10
        assert opts["listing_timeout"] == 15
        assert opts["bulk_probe_enabled"] is False

    def test_country_passed_through(self, tk_root):
        """Manual country entry is passed through correctly."""
        dlg = _make_dialog(tk_root)
        dlg.country_var.set("US,GB")
        opts = dlg._build_scan_options()
        # Countries are deduplicated and sorted
        assert opts["country"] in ("GB,US", "US,GB")
        assert set(opts["country"].split(",")) == {"US", "GB"}

    def test_global_scan_country_is_none(self, tk_root):
        """Empty country field + no regions → country=None (not '')."""
        dlg = _make_dialog(tk_root)
        dlg.country_var.set("")
        opts = dlg._build_scan_options()
        assert opts["country"] is None

    def test_optional_api_key_empty(self, tk_root):
        """Blank API key field → api_key_override=None."""
        dlg = _make_dialog(tk_root)
        dlg.api_key_var.set("")
        opts = dlg._build_scan_options()
        assert opts["api_key_override"] is None

    def test_optional_api_key_filled(self, tk_root):
        """Non-blank API key field → api_key_override=<value>."""
        dlg = _make_dialog(tk_root)
        dlg.api_key_var.set("MY_API_KEY")
        opts = dlg._build_scan_options()
        assert opts["api_key_override"] == "MY_API_KEY"

    def test_custom_filters_passed_through(self, tk_root):
        """Custom Shodan filters are included in scan options unchanged."""
        dlg = _make_dialog(tk_root)
        dlg.custom_filters_var.set('org:"Example ISP" has_screenshot:true')
        opts = dlg._build_scan_options()
        assert opts["custom_filters"] == 'org:"Example ISP" has_screenshot:true'

    def test_config_backed_defaults(self, tmp_path, tk_root):
        """Concurrency / timeout defaults are loaded from config.json when present."""
        cfg = {
            "ftp": {
                "discovery": {"max_concurrent_hosts": 7},
                "access": {"max_concurrent_hosts": 2},
                "verification": {
                    "connect_timeout": 3,
                    "auth_timeout": 8,
                    "listing_timeout": 20,
                },
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        from gui.components.ftp_scan_dialog import FtpScanDialog
        with patch.object(FtpScanDialog, "_create_dialog"):
            dlg = FtpScanDialog(
                parent=tk_root,
                config_path=str(config_file),
                scan_start_callback=MagicMock(),
            )
        dlg.dialog = MagicMock()

        opts = dlg._build_scan_options()
        assert opts["discovery_max_concurrent_hosts"] == 7
        assert opts["access_max_concurrent_hosts"] == 2
        assert opts["connect_timeout"] == 3
        assert opts["auth_timeout"] == 8
        assert opts["listing_timeout"] == 20

    def test_loads_saved_values_from_settings_manager(self, tk_root):
        """Dialog restores last-used FTP values when settings manager is provided."""
        sm = MagicMock()
        stored = {
            "ftp_scan_dialog.max_shodan_results": 321,
            "ftp_scan_dialog.api_key_override": "SAVED_KEY",
            "ftp_scan_dialog.custom_filters": "org:SavedISP",
            "ftp_scan_dialog.country_code": "US,GB",
            "ftp_scan_dialog.discovery_max_concurrent_hosts": 12,
            "ftp_scan_dialog.access_max_concurrent_hosts": 6,
            "ftp_scan_dialog.connect_timeout": 7,
            "ftp_scan_dialog.auth_timeout": 11,
            "ftp_scan_dialog.listing_timeout": 21,
            "ftp_scan_dialog.verbose": True,
            "ftp_scan_dialog.bulk_probe_enabled": True,
            "ftp_scan_dialog.region_asia": True,
            "ftp_scan_dialog.region_europe": True,
        }
        sm.get_setting.side_effect = lambda key, default=None: stored.get(key, default)

        dlg = _make_dialog(tk_root, settings_manager=sm)
        opts = dlg._build_scan_options()

        assert opts["max_shodan_results"] == 321
        assert opts["api_key_override"] == "SAVED_KEY"
        assert opts["custom_filters"] == "org:SavedISP"
        assert opts["discovery_max_concurrent_hosts"] == 12
        assert opts["access_max_concurrent_hosts"] == 6
        assert opts["connect_timeout"] == 7
        assert opts["auth_timeout"] == 11
        assert opts["listing_timeout"] == 21
        assert opts["verbose"] is True
        assert opts["bulk_probe_enabled"] is True
        assert opts["country"] in ("GB,US", "US,GB")
        assert dlg.asia_var.get() is True
        assert dlg.europe_var.get() is True

    def test_persists_values_to_settings_manager_on_build(self, tk_root):
        """_build_scan_options saves FTP dialog selections for the next run."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.country_var.set("US")
        dlg.max_results_var.set(250)
        dlg.api_key_var.set("TMP_KEY")
        dlg.custom_filters_var.set('country:US org:"Tmp ISP"')
        dlg.discovery_concurrency_var.set("9")
        dlg.access_concurrency_var.set("3")
        dlg.connect_timeout_var.set("4")
        dlg.auth_timeout_var.set("8")
        dlg.listing_timeout_var.set("13")
        dlg.verbose_var.set(True)
        dlg.bulk_probe_enabled_var.set(True)
        dlg.asia_var.set(True)

        dlg._build_scan_options()

        expected_calls = [
            call("ftp_scan_dialog.max_shodan_results", 250),
            call("ftp_scan_dialog.api_key_override", "TMP_KEY"),
            call("ftp_scan_dialog.custom_filters", 'country:US org:"Tmp ISP"'),
            call("ftp_scan_dialog.country_code", "US"),
            call("ftp_scan_dialog.discovery_max_concurrent_hosts", 9),
            call("ftp_scan_dialog.access_max_concurrent_hosts", 3),
            call("ftp_scan_dialog.connect_timeout", 4),
            call("ftp_scan_dialog.auth_timeout", 8),
            call("ftp_scan_dialog.listing_timeout", 13),
            call("ftp_scan_dialog.verbose", True),
            call("ftp_scan_dialog.bulk_probe_enabled", True),
            call("ftp_scan_dialog.region_asia", True),
        ]
        sm.set_setting.assert_has_calls(expected_calls, any_order=False)


# ===========================================================================
# 2. show_ftp_scan_dialog — cancel path (no Toplevel needed: _create_dialog patched)
# ===========================================================================

class TestShowFtpScanDialogCancel:

    def test_cancel_does_not_call_callback(self, tk_root):
        """Pressing Cancel must not invoke scan_start_callback."""
        from gui.components.ftp_scan_dialog import FtpScanDialog

        callback = MagicMock()

        with patch.object(FtpScanDialog, "_create_dialog"):
            dlg = FtpScanDialog(
                parent=tk_root,
                config_path="/nonexistent/conf/config.json",
                scan_start_callback=callback,
            )
        dlg.dialog = MagicMock()

        dlg._cancel()

        callback.assert_not_called()
        assert dlg.result == "cancel"

    def test_cancel_persists_dialog_state(self, tk_root):
        """Cancel still saves dialog state so values are retained next open."""
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)

        dlg.max_results_var.set(123)
        dlg.country_var.set("us")
        dlg.custom_filters_var.set("org:PersistMe")
        dlg.europe_var.set(True)
        dlg.bulk_probe_enabled_var.set(True)

        dlg._cancel()

        sm.set_setting.assert_any_call("ftp_scan_dialog.max_shodan_results", 123)
        sm.set_setting.assert_any_call("ftp_scan_dialog.country_code", "US")
        sm.set_setting.assert_any_call("ftp_scan_dialog.custom_filters", "org:PersistMe")
        sm.set_setting.assert_any_call("ftp_scan_dialog.region_europe", True)
        sm.set_setting.assert_any_call("ftp_scan_dialog.bulk_probe_enabled", True)


# ===========================================================================
# 3. scan_manager: FTP config overrides applied under _temporary_config_override
# ===========================================================================

class TestFtpScanManagerOverrides:

    def _make_scan_manager(self):
        """Return a ScanManager with a mocked backend_interface."""
        from gui.utils.scan_manager import ScanManager

        sm = ScanManager.__new__(ScanManager)
        sm.backend_interface = MagicMock()
        sm.log_callback = None

        # Patch _update_progress and _process_scan_results to no-ops
        sm._update_progress = MagicMock()
        sm._process_scan_results = MagicMock()
        sm._handle_scan_error = MagicMock()
        sm._cleanup_scan = MagicMock()

        # _temporary_config_override: capture the overrides passed in
        captured = {}

        @contextmanager
        def _fake_override(overrides):
            captured["overrides"] = overrides
            yield

        sm.backend_interface._temporary_config_override = _fake_override
        sm.backend_interface.run_ftp_scan = MagicMock(return_value={"success": True})
        sm._captured_overrides = captured
        return sm

    def test_config_overrides_applied(self):
        """_ftp_scan_worker passes FTP-specific config overrides correctly."""
        sm = self._make_scan_manager()
        scan_options = {
            "country": "US",
            "max_shodan_results": 500,
            "api_key_override": "TEST_KEY",
            "discovery_max_concurrent_hosts": 8,
            "access_max_concurrent_hosts": 3,
            "connect_timeout": 4,
            "auth_timeout": 9,
            "listing_timeout": 12,
            "verbose": True,
        }
        sm._ftp_scan_worker(scan_options)

        overrides = sm._captured_overrides.get("overrides", {})

        # API key
        assert overrides.get("shodan", {}).get("api_key") == "TEST_KEY"
        # max results
        assert (
            overrides.get("ftp", {})
            .get("shodan", {})
            .get("query_limits", {})
            .get("max_results")
            == 500
        )
        # concurrency
        assert overrides.get("ftp", {}).get("discovery", {}).get("max_concurrent_hosts") == 8
        assert overrides.get("ftp", {}).get("access", {}).get("max_concurrent_hosts") == 3
        # timeouts
        verif = overrides.get("ftp", {}).get("verification", {})
        assert verif.get("connect_timeout") == 4
        assert verif.get("auth_timeout") == 9
        assert verif.get("listing_timeout") == 12

    def test_verbose_passed_to_run_ftp_scan(self):
        """verbose=True from scan_options reaches run_ftp_scan."""
        sm = self._make_scan_manager()
        sm._ftp_scan_worker({"country": None, "verbose": True})
        sm.backend_interface.run_ftp_scan.assert_called_once()
        _, kwargs = sm.backend_interface.run_ftp_scan.call_args
        assert kwargs.get("verbose") is True

    def test_verbose_defaults_false(self):
        """verbose key absent → run_ftp_scan receives verbose=False."""
        sm = self._make_scan_manager()
        sm._ftp_scan_worker({"country": None})
        _, kwargs = sm.backend_interface.run_ftp_scan.call_args
        assert kwargs.get("verbose") is False

    def test_custom_filters_passed_to_run_ftp_scan(self):
        """custom_filters is forwarded to backend run_ftp_scan()."""
        sm = self._make_scan_manager()
        sm._ftp_scan_worker({"country": None, "custom_filters": 'org:"Example ISP"'})
        _, kwargs = sm.backend_interface.run_ftp_scan.call_args
        assert kwargs.get("filters") == 'org:"Example ISP"'

    def test_no_overrides_skips_context_manager(self):
        """Empty scan_options → _temporary_config_override is NOT entered."""
        sm = self._make_scan_manager()
        entered = {"called": False}

        @contextmanager
        def _track_entry(overrides):
            entered["called"] = True
            yield

        sm.backend_interface._temporary_config_override = _track_entry
        sm._ftp_scan_worker({"country": None})
        assert not entered["called"]


# ===========================================================================
# 4. Dashboard wiring tests (no Tk required — direct method calls on mocks)
# ===========================================================================

class TestDashboardFtpWiring:

    def _make_dashboard_mock(self, state="idle"):
        """Return a MagicMock shaped like DashboardWidget."""
        mock_self = MagicMock()
        mock_self.scan_button_state = state
        # _check_external_scans() is a no-op by default (state unchanged)
        mock_self._check_external_scans = MagicMock()
        return mock_self

    def test_ftp_button_opens_dialog(self):
        """FTP button click in idle state → show_ftp_scan_dialog called."""
        import gui.components.dashboard as dash_module
        from gui.components.dashboard import DashboardWidget

        mock_self = self._make_dashboard_mock("idle")

        with patch.object(dash_module, "show_ftp_scan_dialog") as mock_dialog:
            DashboardWidget._handle_ftp_scan_button_click(mock_self)

        mock_dialog.assert_called_once()

    def test_ftp_button_not_opened_when_scanning(self):
        """FTP button click while scanning → dialog NOT opened."""
        import gui.components.dashboard as dash_module
        from gui.components.dashboard import DashboardWidget

        mock_self = self._make_dashboard_mock("scanning")

        with patch.object(dash_module, "show_ftp_scan_dialog") as mock_dialog:
            DashboardWidget._handle_ftp_scan_button_click(mock_self)

        mock_dialog.assert_not_called()

    def test_start_ftp_scan_bails_on_race(self):
        """_start_ftp_scan bails when _check_external_scans flips state away from idle."""
        from gui.components.dashboard import DashboardWidget

        mock_self = self._make_dashboard_mock("idle")

        def _flip_to_scanning():
            mock_self.scan_button_state = "scanning"

        mock_self._check_external_scans.side_effect = _flip_to_scanning

        DashboardWidget._start_ftp_scan(mock_self, {"country": None})

        mock_self.scan_manager.start_ftp_scan.assert_not_called()

    def test_start_ftp_scan_proceeds_when_idle(self):
        """_start_ftp_scan proceeds normally when state stays idle after check."""
        from gui.components.dashboard import DashboardWidget

        mock_self = self._make_dashboard_mock("idle")
        mock_self.scan_manager.start_ftp_scan.return_value = True
        mock_self.backend_interface.backend_path = "./ftpseek"

        DashboardWidget._start_ftp_scan(mock_self, {"country": "US"})

        mock_self.scan_manager.start_ftp_scan.assert_called_once()
