"""
Tests for UI-builder methods extracted from scan_dialog.py into
_ScanDialogUIBuildersMixin (Slice 7D refactor).

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


# ---------------------------------------------------------------------------
# Helper: collect all widget descendants
# ---------------------------------------------------------------------------

def _all_widgets(widget):
    """Return a flat list of widget and all its descendants."""
    result = [widget]
    try:
        for child in widget.winfo_children():
            result.extend(_all_widgets(child))
    except Exception:
        pass
    return result


# ===========================================================================
# TestMROResolution
# ===========================================================================

_MOVED_METHODS = [
    "_create_accent_heading",
    "_create_custom_filters_option",
    "_create_max_results_option",
    "_create_recent_hours_option",
    "_create_rescan_options",
    "_create_verbose_option",
    "_create_security_mode_option",
    "_create_rce_analysis_option",
    "_create_bulk_probe_option",
    "_create_bulk_extract_option",
    "_create_concurrency_options",
    "_create_rate_limit_options",
    "_create_api_key_option",
]


class TestMROResolution:
    def test_all_moved_methods_resolvable(self):
        from gui.components.scan_dialog import ScanDialog
        for name in _MOVED_METHODS:
            assert hasattr(ScanDialog, name), f"ScanDialog missing {name}"

    def test_moved_methods_not_in_scan_dialog_module(self):
        """Ensure the methods live in the mixin, not re-defined in scan_dialog."""
        import inspect
        from gui.components.scan_dialog import ScanDialog
        from gui.components.scan_dialog_ui_builders_mixin import _ScanDialogUIBuildersMixin

        for name in _MOVED_METHODS:
            method = getattr(ScanDialog, name)
            defining_class = None
            for cls in ScanDialog.__mro__:
                if name in cls.__dict__:
                    defining_class = cls
                    break
            assert defining_class is _ScanDialogUIBuildersMixin, (
                f"{name} should be defined on _ScanDialogUIBuildersMixin, "
                f"got {defining_class}"
            )


# ===========================================================================
# TestOptionBuilders — each *_option builder runs without raising
# ===========================================================================

class TestOptionBuilders:
    """Each option-section builder should construct widgets without error."""

    def test_create_custom_filters_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_custom_filters_option(parent)  # no raise

    def test_create_max_results_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_max_results_option(parent)

    def test_create_recent_hours_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_recent_hours_option(parent)

    def test_create_rescan_options(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_rescan_options(parent)

    def test_create_verbose_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_verbose_option(parent)

    def test_create_rce_analysis_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_rce_analysis_option(parent)

    def test_create_bulk_probe_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_bulk_probe_option(parent)

    def test_create_api_key_option(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_api_key_option(parent)


# ===========================================================================
# TestBulkExtractOption
# ===========================================================================

class TestBulkExtractOption:
    def test_sets_extension_count_label(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_bulk_extract_option(parent)
        assert hasattr(dlg, "extension_count_label"), (
            "extension_count_label not set after _create_bulk_extract_option"
        )

    def test_extension_count_label_is_widget(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_bulk_extract_option(parent)
        # Should be a real Tk label, not None or a mock
        assert hasattr(dlg.extension_count_label, "configure")

    def test_extension_count_label_text_contains_extensions(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_bulk_extract_option(parent)
        text = dlg.extension_count_label.cget("text")
        assert "Extensions:" in text


# ===========================================================================
# TestConcurrencyOptions
# ===========================================================================

class TestConcurrencyOptions:
    def test_runs_without_error(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_concurrency_options(parent)

    def test_registers_validatecommand(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_concurrency_options(parent)
        # dialog.register should have been called (validatecommand setup)
        dlg.dialog.register.assert_called()

    def test_creates_entry_widgets(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_concurrency_options(parent)
        entries = [w for w in _all_widgets(parent) if isinstance(w, tk.Entry)]
        assert len(entries) >= 2, "Expected at least 2 Entry widgets (discovery + access)"


# ===========================================================================
# TestRateLimitOptions
# ===========================================================================

class TestRateLimitOptions:
    def test_runs_without_error(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_rate_limit_options(parent)

    def test_registers_validatecommand(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_rate_limit_options(parent)
        dlg.dialog.register.assert_called()

    def test_creates_entry_widgets(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_rate_limit_options(parent)
        entries = [w for w in _all_widgets(parent) if isinstance(w, tk.Entry)]
        assert len(entries) >= 2, "Expected at least 2 Entry widgets (rate + share)"


# ===========================================================================
# TestSecurityModeOption
# ===========================================================================

class TestSecurityModeOption:
    def test_runs_without_error(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_security_mode_option(parent)

    def test_security_mode_var_exists(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_security_mode_option(parent)
        assert hasattr(dlg, "security_mode_var")

    def test_both_radio_buttons_created(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_security_mode_option(parent)
        radios = [w for w in _all_widgets(parent) if isinstance(w, tk.Radiobutton)]
        assert len(radios) == 2, f"Expected 2 radio buttons (cautious + legacy), got {len(radios)}"
        texts = {r.cget("text") for r in radios}
        assert any("autious" in t for t in texts), "Missing Cautious radio"
        assert any("egacy" in t for t in texts), "Missing Legacy radio"


# ===========================================================================
# TestCustomFiltersOption
# ===========================================================================

class TestCustomFiltersOption:
    def test_helper_link_has_hand_cursor(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_custom_filters_option(parent)
        widgets = _all_widgets(parent)
        hand_labels = [
            w for w in widgets
            if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2"
        ]
        assert len(hand_labels) >= 1, "No label with cursor='hand2' found (helper link missing)"


# ===========================================================================
# TestCrossBoundaryMRO
# ===========================================================================

class TestCrossBoundaryMRO:
    """
    _create_region_selection stays in scan_dialog.py but calls self._create_accent_heading,
    which now lives in _ScanDialogUIBuildersMixin.  Verify the call resolves correctly.
    """

    def test_create_region_selection_calls_moved_accent_heading(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        # Should not raise; internally calls self._create_accent_heading via MRO
        dlg._create_region_selection(parent)

    def test_region_selection_produces_accent_label(self, tk_root):
        import tkinter as tk
        dlg = _make_dialog(tk_root)
        parent = tk.Frame(tk_root)
        dlg._create_region_selection(parent)
        widgets = _all_widgets(parent)
        accent_labels = [
            w for w in widgets
            if isinstance(w, tk.Label) and "Region" in str(w.cget("text"))
        ]
        assert len(accent_labels) >= 1, (
            "_create_region_selection should produce an accent label via _create_accent_heading"
        )
