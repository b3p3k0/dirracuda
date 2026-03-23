"""
Targeted unit tests for _DashboardBulkOpsMixin helper methods.

These tests exercise logic that is independently testable after extraction
into dashboard_bulk_ops.py.  All tests use DashboardWidget.__new__() to
instantiate without triggering __init__, mirroring the pattern established
in test_dashboard_bulk_ops.py.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


# --------------------------------------------------------------------------- #
# _build_probe_notes                                                           #
# --------------------------------------------------------------------------- #

def test_build_probe_notes_no_rce():
    """With RCE disabled, note contains only share count."""
    dash = DashboardWidget.__new__(DashboardWidget)
    result = dash._build_probe_notes(
        share_count=3,
        enable_rce=False,
        issue_detected=False,
        analysis={},
        result={},
    )
    assert "3 share(s)" in result
    assert "RCE" not in result
    assert "Indicators" not in result


def test_build_probe_notes_with_rce():
    """With RCE enabled and rce_analysis present, RCE status is appended."""
    dash = DashboardWidget.__new__(DashboardWidget)
    # _handle_rce_status_update is not defined on plain DashboardWidget;
    # the try/except in _build_probe_notes must swallow the AttributeError.
    result = dash._build_probe_notes(
        share_count=2,
        enable_rce=True,
        issue_detected=False,
        analysis={},
        result={"rce_analysis": {"rce_status": "safe"}, "ip_address": "10.0.0.1"},
    )
    assert "2 share(s)" in result
    assert "RCE: safe" in result


def test_build_probe_notes_with_indicators():
    """When issue_detected is True, indicator count is appended."""
    dash = DashboardWidget.__new__(DashboardWidget)
    result = dash._build_probe_notes(
        share_count=1,
        enable_rce=False,
        issue_detected=True,
        analysis={"matches": ["ransom.txt", "DECRYPT.html"]},
        result={},
    )
    assert "1 share(s)" in result
    assert "Indicators detected (2)" in result


def test_build_probe_notes_rce_update_failure_silent():
    """_handle_rce_status_update exceptions must be silently swallowed."""
    dash = DashboardWidget.__new__(DashboardWidget)

    def _exploding_rce_update(ip, status):
        raise RuntimeError("simulated UI update failure")

    dash._handle_rce_status_update = _exploding_rce_update

    # Must not raise — the try/except in _build_probe_notes guards this call
    result = dash._build_probe_notes(
        share_count=1,
        enable_rce=True,
        issue_detected=False,
        analysis={},
        result={"rce_analysis": {"rce_status": "flagged"}, "ip_address": "192.168.1.1"},
    )
    assert "RCE: flagged" in result
