"""Unit tests for SMB credit lab strategy math."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import smb_credit_lab


def test_build_query_limits_strict_one_credit_caps_at_100_results():
    cfg = {"query_limits": {"max_results": 1000}}

    limits = smb_credit_lab._build_query_limits(cfg, smb_credit_lab.STRATEGIES["strict_1_credit"])

    assert limits["max_results"] == 1000
    assert limits["max_query_credits_per_scan"] == 1
    assert limits["effective_limit"] == 100
    assert limits["max_pages"] == 1


def test_build_query_limits_adaptive_three_credit_caps_to_budgeted_window():
    cfg = {"query_limits": {"max_results": 250}}

    limits = smb_credit_lab._build_query_limits(cfg, smb_credit_lab.STRATEGIES["adaptive_3_credit"])

    assert limits["max_query_credits_per_scan"] == 3
    assert limits["effective_limit"] == 250
    assert limits["max_pages"] == 3
    assert limits["min_usable_hosts_target"] == 50


def test_build_query_limits_reference_current_expands_budget_to_cover_max_results():
    cfg = {"query_limits": {"max_results": 250, "max_query_credits_per_scan": 1, "min_usable_hosts_target": 50}}

    limits = smb_credit_lab._build_query_limits(cfg, smb_credit_lab.STRATEGIES["reference_current"])

    assert limits["max_results"] == 250
    assert limits["max_query_credits_per_scan"] == 3
    assert limits["effective_limit"] == 250
    assert limits["max_pages"] == 3
    assert limits["min_usable_hosts_target"] == 251
