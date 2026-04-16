"""
C1 modularization — focused unit tests for shared utility helpers.

Covers:
  gui.utils.coercion._coerce_bool
  gui.utils.filesize._format_file_size

Also includes a regression lock for DashboardWidget._coerce_bool semantics
(preserved unchanged in C1; differs from the shared utility for int > 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size
from gui.components.dashboard import DashboardWidget


# ---------------------------------------------------------------------------
# _coerce_bool
# ---------------------------------------------------------------------------

class TestCoerceBool:
    # Bool passthrough
    def test_true_passthrough(self):
        assert _coerce_bool(True) is True

    def test_false_passthrough(self):
        assert _coerce_bool(False) is False

    # Integer truthiness
    def test_int_zero_is_false(self):
        assert _coerce_bool(0) is False

    def test_int_one_is_true(self):
        assert _coerce_bool(1) is True

    def test_int_two_is_true(self):
        # Shared utility: bool(2) -> True (differs from dashboard variant)
        assert _coerce_bool(2) is True

    def test_int_negative_is_true(self):
        assert _coerce_bool(-1) is True

    # Float truthiness
    def test_float_zero_is_false(self):
        assert _coerce_bool(0.0) is False

    def test_float_nonzero_is_true(self):
        assert _coerce_bool(1.5) is True

    # String truthy values (case-insensitive, whitespace-stripped)
    def test_str_true(self):
        assert _coerce_bool("true") is True

    def test_str_true_upper(self):
        assert _coerce_bool("TRUE") is True

    def test_str_true_mixed_case_and_space(self):
        assert _coerce_bool("  True  ") is True

    def test_str_1(self):
        assert _coerce_bool("1") is True

    def test_str_yes(self):
        assert _coerce_bool("yes") is True

    def test_str_on(self):
        assert _coerce_bool("on") is True

    # String falsy values
    def test_str_false(self):
        assert _coerce_bool("false") is False

    def test_str_0(self):
        assert _coerce_bool("0") is False

    def test_str_no(self):
        assert _coerce_bool("no") is False

    def test_str_off(self):
        assert _coerce_bool("off") is False

    # None -> default
    def test_none_returns_default_false(self):
        assert _coerce_bool(None) is False

    def test_none_explicit_default_true(self):
        assert _coerce_bool(None, default=True) is True

    # Unrecognised string -> default
    def test_unrecognised_str_returns_default(self):
        assert _coerce_bool("maybe") is False

    def test_unrecognised_str_explicit_default_true(self):
        assert _coerce_bool("maybe", default=True) is True

    # Other types -> default
    def test_dict_returns_default(self):
        assert _coerce_bool({}) is False

    def test_list_returns_default(self):
        assert _coerce_bool([1, 2]) is False


# ---------------------------------------------------------------------------
# _format_file_size
# ---------------------------------------------------------------------------

class TestFormatFileSize:
    def test_zero(self):
        assert _format_file_size(0) == "0 B"

    def test_sub_kb_bytes(self):
        assert _format_file_size(500) == "500 B"

    def test_exactly_1023_bytes(self):
        assert _format_file_size(1023) == "1023 B"

    def test_exactly_1_kb(self):
        assert _format_file_size(1024) == "1.0 KB"

    def test_1_5_kb(self):
        assert _format_file_size(1536) == "1.5 KB"

    def test_1_mb(self):
        assert _format_file_size(1024 * 1024) == "1.0 MB"

    def test_1_6_mb(self):
        # 1.6 * 1024 * 1024 = 1677721.6; rounds to "1.6 MB"
        assert _format_file_size(int(1.6 * 1024 * 1024)) == "1.6 MB"

    def test_1_gb(self):
        assert _format_file_size(1024 ** 3) == "1.0 GB"

    def test_1_tb(self):
        assert _format_file_size(1024 ** 4) == "1.0 TB"


# ---------------------------------------------------------------------------
# Dashboard._coerce_bool — regression lock for preserved semantics
# ---------------------------------------------------------------------------

class TestDashboardCoerceBoolPreservedSemantics:
    """
    DashboardWidget._coerce_bool was NOT changed in C1 (different semantics).
    These tests call the real static method directly so any accidental future
    change to the production method fails loudly here.

    Key divergence from gui.utils.coercion._coerce_bool:
      - No int/float branch: int(2) -> str("2") not in truthy set -> False
      - No `default` parameter: unknown types fall through to str coercion
    """

    def test_true_passthrough(self):
        assert DashboardWidget._coerce_bool(True) is True

    def test_false_passthrough(self):
        assert DashboardWidget._coerce_bool(False) is False

    def test_none_returns_false(self):
        assert DashboardWidget._coerce_bool(None) is False

    def test_int_1_is_true(self):
        assert DashboardWidget._coerce_bool(1) is True

    def test_int_0_is_false(self):
        assert DashboardWidget._coerce_bool(0) is False

    def test_int_2_is_false(self):
        # DIVERGES from shared utility: str("2") not in truthy set
        assert DashboardWidget._coerce_bool(2) is False

    def test_str_true(self):
        assert DashboardWidget._coerce_bool("true") is True

    def test_str_false_is_false(self):
        # "false" not in {"1","true","yes","on"} -> False
        assert DashboardWidget._coerce_bool("false") is False

    def test_str_yes(self):
        assert DashboardWidget._coerce_bool("yes") is True

    def test_str_on(self):
        assert DashboardWidget._coerce_bool("on") is True
