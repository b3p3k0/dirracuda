"""
Tests for region/country methods extracted from scan_dialog.py into
_ScanDialogRegionMixin (Slice 7B refactor).

Exercises the methods via ScanDialog (the concrete class) with _create_dialog
patched out, so no real Toplevel is created but all __init__ state is present.

Requires a Tk root (xvfb on headless Linux).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
# TestGetAllSelectedCountries
# ===========================================================================

class TestGetAllSelectedCountries:

    def test_no_regions_no_manual_returns_empty(self, tk_root):
        dlg = _make_dialog(tk_root)
        # All region vars default to False; empty manual input
        countries, err = dlg._get_all_selected_countries("")
        assert countries == []
        assert err == ""

    def test_one_region_checked_returns_sorted_list(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.europe_var.set(True)
        countries, err = dlg._get_all_selected_countries("")
        assert err == ""
        assert len(countries) > 0
        assert countries == sorted(countries)
        assert "DE" in countries
        assert "FR" in countries

    def test_manual_input_combined_with_region(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.oceania_var.set(True)
        countries, err = dlg._get_all_selected_countries("US")
        assert err == ""
        assert "US" in countries
        assert "AU" in countries
        # No duplicates
        assert len(countries) == len(set(countries))

    def test_invalid_code_non_alpha_returns_error(self, tk_root):
        dlg = _make_dialog(tk_root)
        countries, err = dlg._get_all_selected_countries("U1")
        assert countries == []
        assert err != ""

    def test_invalid_code_single_char_returns_error(self, tk_root):
        dlg = _make_dialog(tk_root)
        countries, err = dlg._get_all_selected_countries("X")
        assert countries == []
        assert err != ""


# ===========================================================================
# TestUpdateRegionStatus
# ===========================================================================

class TestUpdateRegionStatus:

    def test_no_regions_sets_empty_text(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        dlg._update_region_status()
        dlg.region_status_label.configure.assert_called_once_with(text="")

    def test_one_region_text_contains_region_name(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        dlg.africa_var.set(True)
        dlg._update_region_status()
        call_kwargs = dlg.region_status_label.configure.call_args[1]
        assert "Africa" in call_kwargs["text"]

    def test_multiple_regions_text_shows_count(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        dlg.europe_var.set(True)
        dlg.asia_var.set(True)
        dlg._update_region_status()
        call_kwargs = dlg.region_status_label.configure.call_args[1]
        assert "2 regions" in call_kwargs["text"]
