"""Tests for cap-driven max-result derivation in unified dashboard scan routing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard_scan import build_protocol_scan_options


def test_build_protocol_scan_options_derives_windows_from_candidate_caps():
    common = {
        "country": "US",
        "max_shodan_results": 100,  # per-protocol caps are authoritative when present
        "shared_concurrency": 9,
        "shared_timeout_seconds": 12,
        "smb_max_shodan_results_per_scan": 1000,
        "ftp_max_shodan_results_per_scan": 1000,
        "http_max_shodan_results_per_scan": 1000,
    }

    smb_opts = build_protocol_scan_options("smb", common)
    ftp_opts = build_protocol_scan_options("ftp", common)
    http_opts = build_protocol_scan_options("http", common)

    assert smb_opts["max_shodan_results"] == 1000
    assert ftp_opts["max_shodan_results"] == 1000
    assert http_opts["max_shodan_results"] == 1000
    assert smb_opts["smb_max_query_credits_per_scan"] == 10
    assert ftp_opts["ftp_max_query_credits_per_scan"] == 10
    assert http_opts["http_max_query_credits_per_scan"] == 10
    assert "custom_filters" not in smb_opts
    assert "custom_filters" not in ftp_opts
    assert "custom_filters" not in http_opts


def test_build_protocol_scan_options_defaults_cap_to_one_page():
    common = {"country": None}

    smb_opts = build_protocol_scan_options("smb", common)
    ftp_opts = build_protocol_scan_options("ftp", common)
    http_opts = build_protocol_scan_options("http", common)

    assert smb_opts["max_shodan_results"] == 100
    assert ftp_opts["max_shodan_results"] == 100
    assert http_opts["max_shodan_results"] == 100


def test_build_protocol_scan_options_uses_shared_legacy_cap_when_present():
    common = {"country": None, "max_shodan_results": 750}

    smb_opts = build_protocol_scan_options("smb", common)
    ftp_opts = build_protocol_scan_options("ftp", common)
    http_opts = build_protocol_scan_options("http", common)

    assert smb_opts["max_shodan_results"] == 750
    assert ftp_opts["max_shodan_results"] == 750
    assert http_opts["max_shodan_results"] == 750
    assert smb_opts["smb_max_query_credits_per_scan"] == 8
    assert ftp_opts["ftp_max_query_credits_per_scan"] == 8
    assert http_opts["http_max_query_credits_per_scan"] == 8
