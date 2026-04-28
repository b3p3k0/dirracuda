"""Tests for budget-driven max-result derivation in unified dashboard scan routing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard_scan import build_protocol_scan_options


def test_build_protocol_scan_options_derives_windows_from_budgets():
    common = {
        "country": "US",
        "max_shodan_results": 1000,  # legacy field should not drive unified sizing
        "shared_concurrency": 9,
        "shared_timeout_seconds": 12,
        "smb_max_query_credits_per_scan": 3,
        "ftp_max_query_credits_per_scan": 5,
        "http_max_query_credits_per_scan": 1,
    }

    smb_opts = build_protocol_scan_options("smb", common)
    ftp_opts = build_protocol_scan_options("ftp", common)
    http_opts = build_protocol_scan_options("http", common)

    assert smb_opts["max_shodan_results"] == 300
    assert ftp_opts["max_shodan_results"] == 500
    assert http_opts["max_shodan_results"] == 100
    assert "custom_filters" not in smb_opts
    assert "custom_filters" not in ftp_opts
    assert "custom_filters" not in http_opts


def test_build_protocol_scan_options_defaults_budget_to_one_credit():
    common = {"country": None}

    smb_opts = build_protocol_scan_options("smb", common)
    ftp_opts = build_protocol_scan_options("ftp", common)
    http_opts = build_protocol_scan_options("http", common)

    assert smb_opts["max_shodan_results"] == 100
    assert ftp_opts["max_shodan_results"] == 100
    assert http_opts["max_shodan_results"] == 100
