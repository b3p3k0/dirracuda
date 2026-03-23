"""
Tests for Slice 4A extractions from unified_scan_dialog.py.

Categories:
  1. TestValidators   — pure-function unit tests; no Tk, no xvfb required.
  2. TestTemplateMixin — mixin behaviour via UnifiedScanDialog (Tk root required).
  3. TestRegionMixin   — mixin behaviour via UnifiedScanDialog (Tk root required).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Tk fixture (shared by mixin tests)
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
    """Instantiate UnifiedScanDialog with _create_dialog patched out."""
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
# 1. TestValidators — no Tk dependency
# ===========================================================================

class TestValidators:

    # --- validate_integer_char ---

    def test_empty_string_is_valid(self):
        from gui.components.unified_scan_validators import validate_integer_char
        assert validate_integer_char("") is True

    def test_digits_only_are_valid(self):
        from gui.components.unified_scan_validators import validate_integer_char
        assert validate_integer_char("0") is True
        assert validate_integer_char("123") is True

    def test_letters_are_invalid(self):
        from gui.components.unified_scan_validators import validate_integer_char
        assert validate_integer_char("a") is False
        assert validate_integer_char("1a") is False

    def test_negative_sign_is_invalid(self):
        from gui.components.unified_scan_validators import validate_integer_char
        assert validate_integer_char("-1") is False

    # --- parse_positive_int ---

    def test_valid_integer(self):
        from gui.components.unified_scan_validators import parse_positive_int
        assert parse_positive_int("5", "Concurrency", maximum=256) == 5

    def test_at_minimum(self):
        from gui.components.unified_scan_validators import parse_positive_int
        assert parse_positive_int("1", "Timeout", minimum=1, maximum=300) == 1

    def test_at_maximum(self):
        from gui.components.unified_scan_validators import parse_positive_int
        assert parse_positive_int("300", "Timeout", minimum=1, maximum=300) == 300

    def test_below_minimum_raises(self):
        from gui.components.unified_scan_validators import parse_positive_int
        with pytest.raises(ValueError, match="at least"):
            parse_positive_int("0", "Concurrency", minimum=1, maximum=256)

    def test_above_maximum_raises(self):
        from gui.components.unified_scan_validators import parse_positive_int
        with pytest.raises(ValueError, match="or less"):
            parse_positive_int("257", "Concurrency", minimum=1, maximum=256)

    def test_empty_string_raises(self):
        from gui.components.unified_scan_validators import parse_positive_int
        with pytest.raises(ValueError, match="required"):
            parse_positive_int("", "Concurrency", maximum=256)

    def test_non_numeric_raises(self):
        from gui.components.unified_scan_validators import parse_positive_int
        with pytest.raises(ValueError, match="whole number"):
            parse_positive_int("abc", "Concurrency", maximum=256)

    # --- parse_and_validate_countries ---

    def test_empty_input_returns_empty(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("")
        assert codes == []
        assert err == ""

    def test_whitespace_only_returns_empty(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("   ")
        assert codes == []
        assert err == ""

    def test_single_code(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("US")
        assert codes == ["US"]
        assert err == ""

    def test_multiple_codes_normalised(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("us,gb,ca")
        assert set(codes) == {"US", "GB", "CA"}
        assert err == ""

    def test_three_letter_code_accepted(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("USA")
        assert codes == ["USA"]
        assert err == ""

    def test_one_letter_code_rejected(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("U")
        assert codes == []
        assert "2-3 characters" in err

    def test_four_letter_code_rejected(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("USAA")
        assert codes == []
        assert "2-3 characters" in err

    def test_digit_in_code_rejected(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries("U1")
        assert codes == []
        assert "only letters" in err

    def test_whitespace_trimmed_around_codes(self):
        from gui.components.unified_scan_validators import parse_and_validate_countries
        codes, err = parse_and_validate_countries(" US , GB ")
        assert set(codes) == {"US", "GB"}
        assert err == ""


# ===========================================================================
# 2. TestTemplateMixin — requires Tk root (run under xvfb-run -a)
# ===========================================================================

class TestTemplateMixin:

    def test_capture_apply_roundtrip(self, tk_root):
        """_capture_form_state / _apply_form_state round-trip preserves all fields."""
        dlg = _make_dialog(tk_root)

        # Set non-default values
        dlg.protocol_smb_var.set(False)
        dlg.protocol_ftp_var.set(True)
        dlg.protocol_http_var.set(False)
        dlg.country_var.set("DE")
        dlg.europe_var.set(True)
        dlg.max_results_var.set(42)
        dlg.shared_concurrency_var.set("7")
        dlg.shared_timeout_var.set("15")
        dlg.verbose_var.set(True)
        dlg.bulk_probe_enabled_var.set(True)
        dlg.bulk_extract_enabled_var.set(True)
        dlg.skip_indicator_extract_var.set(False)
        dlg.rce_enabled_var.set(True)
        dlg.security_mode_var.set("legacy")
        dlg.allow_insecure_tls_var.set(False)

        state = dlg._capture_form_state()

        # Reset to defaults
        dlg.protocol_smb_var.set(True)
        dlg.protocol_ftp_var.set(False)
        dlg.country_var.set("")
        dlg.europe_var.set(False)
        dlg.max_results_var.set(1000)
        dlg.security_mode_var.set("cautious")

        # region_status_label may not exist (dialog not rendered); patch it
        dlg.region_status_label = MagicMock()

        dlg._apply_form_state(state)

        assert dlg.protocol_smb_var.get() is False
        assert dlg.protocol_ftp_var.get() is True
        assert dlg.country_var.get() == "DE"
        assert dlg.europe_var.get() is True
        assert dlg.max_results_var.get() == 42
        assert dlg.shared_concurrency_var.get() == "7"
        assert dlg.shared_timeout_var.get() == "15"
        assert dlg.verbose_var.get() is True
        assert dlg.rce_enabled_var.get() is True
        assert dlg.security_mode_var.get() == "legacy"
        assert dlg.allow_insecure_tls_var.get() is False

    def test_apply_form_state_invalid_security_mode_falls_back(self, tk_root):
        """An unknown security_mode value defaults to 'cautious'."""
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        dlg._apply_form_state({"security_mode": "invalid_value"})
        assert dlg.security_mode_var.get() == "cautious"

    def test_get_selected_template_name_placeholder_returns_none(self, tk_root):
        """When the dropdown shows placeholder text, method returns None."""
        from gui.components.unified_scan_dialog import UnifiedScanDialog
        dlg = _make_dialog(tk_root)
        dlg.template_var.set(UnifiedScanDialog.TEMPLATE_PLACEHOLDER_TEXT)
        assert dlg._get_selected_template_name() is None

    def test_get_selected_template_name_with_name(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.template_var.set("My Template")
        assert dlg._get_selected_template_name() == "My Template"


# ===========================================================================
# 3. TestRegionMixin — requires Tk root (run under xvfb-run -a)
# ===========================================================================

class TestRegionMixin:

    def test_select_all_regions_sets_all_vars(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        # Ensure they start off
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        dlg._select_all_regions()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            assert var.get() is True

    def test_clear_all_regions_clears_all_vars(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(True)
        dlg._clear_all_regions()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            assert var.get() is False

    def test_get_selected_region_countries_none_selected(self, tk_root):
        dlg = _make_dialog(tk_root)
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        assert dlg._get_selected_region_countries() == []

    def test_get_selected_region_countries_europe(self, tk_root):
        from gui.components.unified_scan_dialog import REGIONS
        dlg = _make_dialog(tk_root)
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        dlg.europe_var.set(True)
        result = dlg._get_selected_region_countries()
        assert set(result) == set(REGIONS["Europe"])

    def test_get_all_selected_countries_merges_manual_and_region(self, tk_root):
        dlg = _make_dialog(tk_root)
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        dlg.north_america_var.set(True)
        countries, err = dlg._get_all_selected_countries("DE")
        assert err == ""
        assert "DE" in countries
        # North America countries present
        from gui.components.unified_scan_dialog import REGIONS
        for code in REGIONS["North America"]:
            assert code in countries

    def test_get_all_selected_countries_too_many(self, tk_root):
        """Selecting all regions exceeds _MAX_COUNTRIES and returns an error."""
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(True)
        countries, err = dlg._get_all_selected_countries("")
        assert countries == []
        assert "Too many" in err

    def test_update_region_status_no_selection(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        dlg._update_region_status()
        dlg.region_status_label.configure.assert_called_once_with(text="")

    def test_update_region_status_one_region(self, tk_root):
        from gui.components.unified_scan_dialog import REGIONS
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        for var in (dlg.africa_var, dlg.asia_var, dlg.europe_var,
                    dlg.north_america_var, dlg.oceania_var, dlg.south_america_var):
            var.set(False)
        dlg.europe_var.set(True)
        dlg._update_region_status()
        expected = f"Europe ({len(REGIONS['Europe'])} countries)"
        dlg.region_status_label.configure.assert_called_once_with(text=expected)
