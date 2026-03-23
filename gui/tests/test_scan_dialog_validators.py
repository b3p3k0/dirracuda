"""
Unit tests for gui/components/scan_dialog_validators.py.

Extracted from scan_dialog.py (Slice 7B refactor).
No Tkinter dependency — no display or xvfb required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.scan_dialog_validators import parse_positive_int


class TestParsePositiveInt:

    # --- happy path ---

    def test_valid_value_in_range(self):
        assert parse_positive_int("5", "Field", minimum=0, maximum=10) == 5

    def test_at_minimum(self):
        assert parse_positive_int("0", "Delay", minimum=0, maximum=3600) == 0

    def test_at_maximum(self):
        assert parse_positive_int("10", "Field", minimum=0, maximum=10) == 10

    def test_default_minimum_is_zero(self):
        # minimum defaults to 0 — distinct from unified_scan_validators (min=1)
        assert parse_positive_int("0", "Delay", maximum=100) == 0

    def test_maximum_none_no_upper_bound(self):
        # maximum=None means no upper limit
        assert parse_positive_int("99999", "Field") == 99999

    def test_delay_field_accepts_zero(self):
        # rate_limit_delay and share_access_delay use minimum=0
        assert parse_positive_int("0", "Rate limit delay (seconds)", minimum=0, maximum=3600) == 0

    # --- error paths ---

    def test_empty_string_raises_required(self):
        with pytest.raises(ValueError, match="required"):
            parse_positive_int("", "Concurrency", maximum=256)

    def test_non_numeric_raises_whole_number(self):
        with pytest.raises(ValueError, match="whole number"):
            parse_positive_int("abc", "Concurrency", maximum=256)

    def test_float_string_raises_whole_number(self):
        with pytest.raises(ValueError, match="whole number"):
            parse_positive_int("1.5", "Concurrency", maximum=256)

    def test_below_minimum_raises(self):
        with pytest.raises(ValueError, match="at least"):
            parse_positive_int("0", "Concurrency", minimum=1, maximum=256)

    def test_above_maximum_raises(self):
        with pytest.raises(ValueError, match="or less"):
            parse_positive_int("257", "Concurrency", minimum=0, maximum=256)
