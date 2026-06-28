"""Tests for the shared Sherlock Risk display helpers (C12)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sherlock import Severity
from gui.utils.sherlock_risk_display import (
    attach_sherlock_risk_to_results,
    resolve_sherlock_risk,
    row_key_for_server,
    sherlock_row_tag,
)


# ── resolve_sherlock_risk (alert-only blank contract) ─────────────────────────

def test_resolve_fresh_finding():
    resolved = resolve_sherlock_risk(
        {"severity": "high", "count": 4, "stale": False, "display_color_tag": "user1"}
    )
    assert resolved == (Severity.HIGH, 4, "user1")


def test_resolve_blank_cases():
    assert resolve_sherlock_risk(None) is None
    assert resolve_sherlock_risk({}) is None
    assert resolve_sherlock_risk({"severity": "high", "count": 4, "stale": True}) is None
    assert resolve_sherlock_risk({"severity": "high", "count": 0}) is None
    assert resolve_sherlock_risk({"severity": "bogus", "count": 3}) is None
    assert resolve_sherlock_risk({"severity": "high", "count": "x"}) is None


def test_sherlock_row_tag_composite():
    assert sherlock_row_tag(Severity.MED, "user2") == "sherlock_med_user2"


# ── row_key_for_server (propagation, not blind reconstruction) ────────────────

def test_row_key_prefers_existing():
    server = {"row_key": "S:7", "host_type": "F", "protocol_server_id": 99}
    assert row_key_for_server(server) == "S:7"


def test_row_key_builds_from_host_type_and_psid():
    assert row_key_for_server({"host_type": "f", "protocol_server_id": 12}) == "F:12"
    assert row_key_for_server({"host_type": "S", "protocol_server_id": "3"}) == "S:3"


def test_row_key_none_when_invalid():
    assert row_key_for_server({"host_type": "S"}) is None
    assert row_key_for_server({"host_type": "X", "protocol_server_id": 5}) is None
    assert row_key_for_server({"host_type": "S", "protocol_server_id": 0}) is None
    assert row_key_for_server({"host_type": "S", "protocol_server_id": "abc"}) is None
    assert row_key_for_server({"protocol_server_id": 5}) is None


# ── attach_sherlock_risk_to_results ───────────────────────────────────────────

class _Reader:
    def __init__(self, mapping):
        self._mapping = mapping

    def get_sherlock_risk_summary_map(self):
        return self._mapping


def test_attach_matches_by_row_key():
    risk = {"severity": "high", "count": 2, "stale": False, "display_color_tag": "none"}
    results = [
        {"row_key": "S:1", "status": "success"},
        {"row_key": "F:2", "status": "success"},
        {"row_key": "H:9", "status": "success"},   # no map entry -> untouched
        {"ip_address": "x", "status": "success"},  # no row_key -> untouched
    ]
    attach_sherlock_risk_to_results(_Reader({"S:1": risk, "F:2": risk}), results)

    assert results[0]["sherlock_risk"] is risk
    assert results[1]["sherlock_risk"] is risk
    assert "sherlock_risk" not in results[2]
    assert "sherlock_risk" not in results[3]


def test_attach_skips_non_success_rows():
    # A failed/cancelled probe writes no new snapshot; an old result for the same
    # host can still be stale=False. It must NOT surface in this run's summary.
    risk = {"severity": "high", "count": 4, "stale": False, "display_color_tag": "none"}
    results = [
        {"row_key": "S:1", "status": "failed"},
        {"row_key": "S:1", "status": "cancelled"},
        {"row_key": "S:1"},  # no status -> treated as not-success
    ]
    attach_sherlock_risk_to_results(_Reader({"S:1": risk}), results)

    assert all("sherlock_risk" not in r for r in results)


def test_attach_degrades_when_method_missing():
    results = [{"row_key": "S:1", "status": "success"}]
    attach_sherlock_risk_to_results(object(), results)  # no method
    assert "sherlock_risk" not in results[0]


def test_attach_degrades_on_read_failure():
    class _Boom:
        def get_sherlock_risk_summary_map(self):
            raise RuntimeError("db gone")

    results = [{"row_key": "S:1", "status": "success"}]
    attach_sherlock_risk_to_results(_Boom(), results)
    assert "sherlock_risk" not in results[0]
