"""
Tests for Slice 13D extraction: _UnifiedScanDialogOptionsMixin.

Categories:
  1. TestLoadConfigDefaults  — no settings manager; reads config JSON file
  2. TestLoadInitialValues   — settings manager integration
  3. TestPersistDialogState  — settings manager write-back
  4. TestContractGuards      — MRO ownership + constant re-export
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
# Helper: build UnifiedScanDialog without creating the Toplevel window
# ---------------------------------------------------------------------------

def _make_dialog(tk_root, config_path=None, callback=None, settings_manager=None):
    from gui.components.unified_scan_dialog import UnifiedScanDialog

    if callback is None:
        callback = MagicMock()
    if config_path is None:
        config_path = "/nonexistent/conf/config.json"

    with patch.object(UnifiedScanDialog, "_create_dialog"):
        dlg = UnifiedScanDialog(
            parent=tk_root,
            config_path=config_path,
            scan_start_callback=callback,
            settings_manager=settings_manager,
        )
    dlg.dialog = MagicMock()
    return dlg


# ===========================================================================
# 1. TestLoadConfigDefaults
# ===========================================================================

class TestLoadConfigDefaults:

    def test_reads_config_values(self, tk_root):
        config = {"discovery": {"max_concurrent_hosts": 20}, "connection": {"timeout": 30}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            path = f.name

        dlg = _make_dialog(tk_root, config_path=path)
        # __init__ already called _load_config_defaults; re-call to verify
        dlg._load_config_defaults()
        assert dlg.shared_concurrency_var.get() == "20"
        assert dlg.shared_timeout_var.get() == "30"

    def test_fallbacks_on_invalid_json(self, tk_root):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json}")
            path = f.name

        dlg = _make_dialog(tk_root)
        dlg.config_path = Path(path)
        dlg._load_config_defaults()
        assert dlg.shared_concurrency_var.get() == "10"
        assert dlg.shared_timeout_var.get() == "10"

    def test_fallbacks_on_missing_file(self, tk_root):
        dlg = _make_dialog(tk_root, config_path="/nonexistent/path/config.json")
        dlg._load_config_defaults()
        assert dlg.shared_concurrency_var.get() == "10"
        assert dlg.shared_timeout_var.get() == "10"

    def test_clamps_concurrency_to_minimum_one(self, tk_root):
        config = {"discovery": {"max_concurrent_hosts": 0}, "connection": {"timeout": 0}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            path = f.name

        dlg = _make_dialog(tk_root)
        dlg.config_path = Path(path)
        dlg._load_config_defaults()
        assert dlg.shared_concurrency_var.get() == "1"
        assert dlg.shared_timeout_var.get() == "1"


# ===========================================================================
# 2. TestLoadInitialValues
# ===========================================================================

class TestLoadInitialValues:

    def _settings_returning(self, mapping: dict):
        sm = MagicMock()
        sm.get_setting.side_effect = lambda key, default=None: mapping.get(key, default)
        return sm

    def test_loads_all_vars_from_settings_manager(self, tk_root):
        sm = self._settings_returning({
            "unified_scan_dialog.protocol_smb": False,
            "unified_scan_dialog.protocol_ftp": True,
            "unified_scan_dialog.protocol_http": False,
            "unified_scan_dialog.max_shodan_results": 500,
            "unified_scan_dialog.custom_filters": "port:445",
            "unified_scan_dialog.country_code": "DE",
            "unified_scan_dialog.shared_concurrency": 8,
            "unified_scan_dialog.shared_timeout_seconds": 20,
            "unified_scan_dialog.verbose": True,
            "unified_scan_dialog.bulk_probe_enabled": True,
            "unified_scan_dialog.bulk_extract_enabled": True,
            "unified_scan_dialog.bulk_extract_skip_indicators": False,
            "unified_scan_dialog.rce_enabled": True,
            "unified_scan_dialog.security_mode": "legacy",
            "unified_scan_dialog.allow_insecure_tls": False,
            "unified_scan_dialog.region_europe": True,
        })
        dlg = _make_dialog(tk_root, settings_manager=sm)
        # _load_initial_values already ran in __init__
        assert dlg.protocol_smb_var.get() is False
        assert dlg.protocol_ftp_var.get() is True
        assert dlg.country_var.get() == "DE"
        assert dlg.shared_concurrency_var.get() == "8"
        assert dlg.shared_timeout_var.get() == "20"
        assert dlg.verbose_var.get() is True
        assert dlg.rce_enabled_var.get() is True
        assert dlg.security_mode_var.get() == "legacy"
        assert dlg.allow_insecure_tls_var.get() is False
        assert dlg.europe_var.get() is True

    def test_handles_settings_exceptions_gracefully(self, tk_root):
        sm = MagicMock()
        sm.get_setting.side_effect = RuntimeError("boom")
        # Should not raise
        dlg = _make_dialog(tk_root, settings_manager=sm)
        # vars keep their default values
        assert dlg.protocol_smb_var.get() is True

    def test_no_settings_manager_is_noop(self, tk_root):
        dlg = _make_dialog(tk_root, settings_manager=None)
        # defaults still set from _load_config_defaults
        assert dlg.shared_concurrency_var.get() == "10"

    def test_safety_ensures_at_least_one_protocol(self, tk_root):
        sm = self._settings_returning({
            "unified_scan_dialog.protocol_smb": False,
            "unified_scan_dialog.protocol_ftp": False,
            "unified_scan_dialog.protocol_http": False,
        })
        dlg = _make_dialog(tk_root, settings_manager=sm)
        # All three should be reset to True
        assert dlg.protocol_smb_var.get() is True
        assert dlg.protocol_ftp_var.get() is True
        assert dlg.protocol_http_var.get() is True

    def test_invalid_security_mode_falls_back_to_cautious(self, tk_root):
        sm = self._settings_returning({"unified_scan_dialog.security_mode": "bogus"})
        dlg = _make_dialog(tk_root, settings_manager=sm)
        assert dlg.security_mode_var.get() == "cautious"


# ===========================================================================
# 3. TestPersistDialogState
# ===========================================================================

class TestPersistDialogState:

    def test_writes_expected_keys(self, tk_root):
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)
        dlg.country_var.set("US")
        dlg.custom_filters_var.set("port:445")
        dlg._persist_dialog_state()

        set_calls = {c.args[0]: c.args[1] for c in sm.set_setting.call_args_list}
        assert set_calls["unified_scan_dialog.country_code"] == "US"
        assert set_calls["unified_scan_dialog.custom_filters"] == "port:445"
        assert "unified_scan_dialog.protocol_smb" in set_calls
        assert "unified_scan_dialog.verbose" in set_calls
        assert "unified_scan_dialog.security_mode" in set_calls

    def test_ignores_out_of_range_concurrency(self, tk_root):
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)
        dlg.shared_concurrency_var.set("999")  # > _CONCURRENCY_UPPER (256)
        sm.reset_mock()
        dlg._persist_dialog_state()

        set_calls = {c.args[0] for c in sm.set_setting.call_args_list}
        assert "unified_scan_dialog.shared_concurrency" not in set_calls

    def test_ignores_out_of_range_timeout(self, tk_root):
        sm = MagicMock()
        dlg = _make_dialog(tk_root, settings_manager=sm)
        dlg.shared_timeout_var.set("999")  # > _TIMEOUT_UPPER (300)
        sm.reset_mock()
        dlg._persist_dialog_state()

        set_calls = {c.args[0] for c in sm.set_setting.call_args_list}
        assert "unified_scan_dialog.shared_timeout_seconds" not in set_calls

    def test_no_settings_manager_is_noop(self, tk_root):
        dlg = _make_dialog(tk_root, settings_manager=None)
        # Should not raise
        dlg._persist_dialog_state()

    def test_swallows_set_setting_exceptions(self, tk_root):
        sm = MagicMock()
        sm.set_setting.side_effect = RuntimeError("disk full")
        dlg = _make_dialog(tk_root, settings_manager=sm)
        # Should not raise
        dlg._persist_dialog_state()


# ===========================================================================
# 4. TestContractGuards
# ===========================================================================

class TestContractGuards:

    def test_mro_ownership(self):
        from gui.components.unified_scan_dialog import UnifiedScanDialog
        qualname = getattr(UnifiedScanDialog, "_load_initial_values").__qualname__
        assert "_UnifiedScanDialogOptionsMixin" in qualname, (
            f"Expected _load_initial_values to be owned by _UnifiedScanDialogOptionsMixin "
            f"via MRO, but __qualname__ is: {qualname!r}"
        )

    def test_constant_contract(self):
        from gui.components.unified_scan_dialog import _CONCURRENCY_UPPER, _TIMEOUT_UPPER
        assert _CONCURRENCY_UPPER == 256
        assert _TIMEOUT_UPPER == 300
