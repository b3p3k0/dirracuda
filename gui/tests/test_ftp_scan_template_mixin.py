"""
Tests for _FtpScanTemplateMixin (Slice 9A extraction).

All mixin methods are exercised via FtpScanDialog (which inherits from the mixin)
using the same _make_dialog pattern as test_ftp_scan_dialog.py.

Requires a display (run under xvfb-run -a) because tk.BooleanVar / tk.StringVar /
tk.IntVar need a Tk root.
"""

from __future__ import annotations

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
# TestFtpScanTemplateMixin
# ===========================================================================

class TestFtpScanTemplateMixin:

    # --- 1. Method resolution ---

    def test_methods_resolve_on_dialog(self, tk_root):
        """All 9 extracted methods are accessible on FtpScanDialog instances."""
        from gui.components.ftp_scan_dialog import FtpScanDialog
        dlg = _make_dialog(tk_root)
        for name in (
            "_create_template_toolbar",
            "_refresh_template_toolbar",
            "_handle_template_selected",
            "_get_selected_template_name",
            "_prompt_save_template",
            "_delete_selected_template",
            "_capture_form_state",
            "_apply_form_state",
            "_apply_template_by_slug",
        ):
            assert hasattr(dlg, name), f"missing method: {name}"
            qualname = getattr(FtpScanDialog, name).__qualname__
            assert "_FtpScanTemplateMixin" in qualname, (
                f"{name} qualname {qualname!r} does not point to mixin"
            )

    # --- 2. _capture_form_state returns expected keys ---

    def test_capture_form_state_keys(self, tk_root):
        """_capture_form_state returns all 12 expected keys."""
        dlg = _make_dialog(tk_root)
        state = dlg._capture_form_state()
        expected = {
            "custom_filters",
            "country_code",
            "regions",
            "max_results",
            "api_key_override",
            "discovery_concurrency",
            "access_concurrency",
            "connect_timeout",
            "auth_timeout",
            "listing_timeout",
            "verbose",
            "bulk_probe_enabled",
        }
        assert set(state.keys()) == expected

    def test_capture_form_state_regions_subkeys(self, tk_root):
        """regions dict within captured state has the 6 expected continent keys."""
        dlg = _make_dialog(tk_root)
        state = dlg._capture_form_state()
        assert set(state["regions"].keys()) == {
            "africa", "asia", "europe", "north_america", "oceania", "south_america"
        }

    # --- 3. _capture_form_state / _apply_form_state round-trip ---

    def test_capture_apply_roundtrip(self, tk_root):
        """Values set before capture are restored by apply."""
        dlg = _make_dialog(tk_root)

        dlg.country_var.set("DE")
        dlg.europe_var.set(True)
        dlg.max_results_var.set(42)
        dlg.discovery_concurrency_var.set("8")
        dlg.access_concurrency_var.set("4")
        dlg.connect_timeout_var.set("20")
        dlg.auth_timeout_var.set("15")
        dlg.listing_timeout_var.set("30")
        dlg.verbose_var.set(True)
        dlg.bulk_probe_enabled_var.set(True)
        dlg.custom_filters_var.set("port:21")
        dlg.api_key_var.set("testkey")

        state = dlg._capture_form_state()

        # Reset to defaults
        dlg.country_var.set("")
        dlg.europe_var.set(False)
        dlg.max_results_var.set(1000)
        dlg.verbose_var.set(False)

        dlg.region_status_label = MagicMock()
        dlg._apply_form_state(state)

        assert dlg.country_var.get() == "DE"
        assert dlg.europe_var.get() is True
        assert dlg.max_results_var.get() == 42
        assert dlg.discovery_concurrency_var.get() == "8"
        assert dlg.access_concurrency_var.get() == "4"
        assert dlg.connect_timeout_var.get() == "20"
        assert dlg.auth_timeout_var.get() == "15"
        assert dlg.listing_timeout_var.get() == "30"
        assert dlg.verbose_var.get() is True
        assert dlg.bulk_probe_enabled_var.get() is True
        assert dlg.custom_filters_var.get() == "port:21"
        assert dlg.api_key_var.get() == "testkey"

    def test_apply_form_state_calls_update_region_status(self, tk_root):
        """_apply_form_state always calls _update_region_status."""
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        with patch.object(dlg, "_update_region_status") as mock_update:
            dlg._apply_form_state({})
        mock_update.assert_called_once()

    def test_apply_form_state_ignores_invalid_max_results(self, tk_root):
        """_apply_form_state skips max_results if value cannot be cast to int."""
        dlg = _make_dialog(tk_root)
        dlg.region_status_label = MagicMock()
        original = dlg.max_results_var.get()
        dlg._apply_form_state({"max_results": "not-a-number"})
        assert dlg.max_results_var.get() == original

    # --- 4. _refresh_template_toolbar — no templates path ---

    def test_refresh_no_templates_disables_dropdown_and_delete(self, tk_root):
        """When no templates exist the dropdown is disabled and delete button disabled."""
        dlg = _make_dialog(tk_root)
        dlg.template_store = MagicMock()
        dlg.template_store.list_templates.return_value = []
        dlg.template_dropdown = MagicMock()
        dlg.delete_template_button = MagicMock()

        dlg._refresh_template_toolbar()

        dlg.template_dropdown.configure.assert_called_with(state="disabled", values=["No templates saved"])
        import tkinter as tk
        dlg.delete_template_button.configure.assert_called_with(state=tk.DISABLED)
        assert dlg._selected_template_slug is None

    # --- 5. _refresh_template_toolbar — with templates, selects slug ---

    def test_refresh_with_templates_selects_slug(self, tk_root):
        """When matching slug provided the dropdown shows its name and delete is enabled."""
        dlg = _make_dialog(tk_root)

        tpl = MagicMock()
        tpl.name = "My Scan"
        tpl.slug = "my-scan"

        dlg.template_store = MagicMock()
        dlg.template_store.list_templates.return_value = [tpl]
        dlg.template_dropdown = MagicMock()
        dlg.delete_template_button = MagicMock()

        dlg._refresh_template_toolbar(select_slug="my-scan")

        assert dlg.template_var.get() == "My Scan"
        assert dlg._selected_template_slug == "my-scan"
        import tkinter as tk
        dlg.delete_template_button.configure.assert_called_with(state=tk.NORMAL)

    def test_refresh_with_templates_no_slug_shows_placeholder(self, tk_root):
        """When no slug provided the placeholder is shown and delete is disabled."""
        dlg = _make_dialog(tk_root)

        tpl = MagicMock()
        tpl.name = "My Scan"
        tpl.slug = "my-scan"

        dlg.template_store = MagicMock()
        dlg.template_store.list_templates.return_value = [tpl]
        dlg.template_dropdown = MagicMock()
        dlg.delete_template_button = MagicMock()

        dlg._refresh_template_toolbar()

        assert dlg.template_var.get() == dlg.TEMPLATE_PLACEHOLDER_TEXT
        assert dlg._selected_template_slug is None
        import tkinter as tk
        dlg.delete_template_button.configure.assert_called_with(state=tk.DISABLED)

    # --- 6. _handle_template_selected — applies slug ---

    def test_handle_template_selected_applies_slug(self, tk_root):
        """Selecting a valid template label triggers _apply_template_by_slug."""
        dlg = _make_dialog(tk_root)
        dlg._template_label_to_slug = {"My Scan": "my-scan"}
        dlg.template_var.set("My Scan")
        dlg.delete_template_button = MagicMock()

        with patch.object(dlg, "_apply_template_by_slug") as mock_apply:
            dlg._handle_template_selected()

        mock_apply.assert_called_once_with("my-scan")
        assert dlg._selected_template_slug == "my-scan"

    def test_handle_template_selected_placeholder_clears_slug(self, tk_root):
        """Selecting the placeholder resets the selected slug without applying."""
        dlg = _make_dialog(tk_root)
        dlg._template_label_to_slug = {}
        dlg.template_var.set(dlg.TEMPLATE_PLACEHOLDER_TEXT)
        dlg.delete_template_button = MagicMock()
        dlg._selected_template_slug = "stale-slug"

        with patch.object(dlg, "_apply_template_by_slug") as mock_apply:
            dlg._handle_template_selected()

        mock_apply.assert_not_called()
        assert dlg._selected_template_slug is None

    # --- 7. _prompt_save_template — whitespace name shows warning ---

    def test_prompt_save_template_whitespace_name_shows_warning(self, tk_root):
        dlg = _make_dialog(tk_root)

        with (
            patch("gui.components.ftp_scan_template_mixin.simpledialog.askstring", return_value="   "),
            patch("gui.components.ftp_scan_template_mixin.messagebox.showwarning") as mock_warn,
        ):
            dlg._prompt_save_template()

        mock_warn.assert_called_once()
        assert "cannot be empty" in mock_warn.call_args[0][1]

    def test_prompt_save_template_none_return_is_noop(self, tk_root):
        """Cancelling the askstring dialog (returns None) does nothing."""
        dlg = _make_dialog(tk_root)

        with (
            patch("gui.components.ftp_scan_template_mixin.simpledialog.askstring", return_value=None),
            patch("gui.components.ftp_scan_template_mixin.messagebox.showwarning") as mock_warn,
        ):
            dlg._prompt_save_template()

        mock_warn.assert_not_called()

    # --- 8. _prompt_save_template — overwrite path declined ---

    def test_prompt_save_template_overwrite_declined_does_not_save(self, tk_root):
        """If user declines overwrite confirmation, save_template is not called."""
        dlg = _make_dialog(tk_root)
        dlg.template_store = MagicMock()
        dlg.template_store.load_template.return_value = MagicMock()  # existing template

        with (
            patch("gui.components.ftp_scan_template_mixin.simpledialog.askstring", return_value="existing"),
            patch("gui.components.ftp_scan_template_mixin.messagebox.askyesno", return_value=False),
        ):
            dlg._prompt_save_template()

        dlg.template_store.save_template.assert_not_called()

    # --- 9. _delete_selected_template — no selection ---

    def test_delete_no_selection_shows_info(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg._selected_template_slug = None

        with patch("gui.components.ftp_scan_template_mixin.messagebox.showinfo") as mock_info:
            dlg._delete_selected_template()

        mock_info.assert_called_once_with("Delete Template", "No template selected.")

    # --- 10. _delete_selected_template — confirmed delete ---

    def test_delete_confirmed_calls_store_and_refreshes(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg._selected_template_slug = "foo"
        dlg.template_var.set("Foo Template")
        dlg.template_store = MagicMock()
        dlg.template_store.delete_template.return_value = True

        with (
            patch("gui.components.ftp_scan_template_mixin.messagebox.askyesno", return_value=True),
            patch("gui.components.ftp_scan_template_mixin.messagebox.showinfo"),
            patch.object(dlg, "_refresh_template_toolbar") as mock_refresh,
        ):
            dlg._delete_selected_template()

        dlg.template_store.delete_template.assert_called_once_with("foo")
        mock_refresh.assert_called_once()

    def test_delete_cancelled_does_not_call_store(self, tk_root):
        dlg = _make_dialog(tk_root)
        dlg._selected_template_slug = "bar"
        dlg.template_var.set("Bar Template")
        dlg.template_store = MagicMock()

        with patch("gui.components.ftp_scan_template_mixin.messagebox.askyesno", return_value=False):
            dlg._delete_selected_template()

        dlg.template_store.delete_template.assert_not_called()
