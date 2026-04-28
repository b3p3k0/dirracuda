"""Tests for shared query budget state helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from gui.components.query_budget_dialog import load_query_budget_state, persist_query_budget_state


class _CfgStub:
    def __init__(self, shodan_cfg):
        self._shodan_cfg = shodan_cfg

    def get_shodan_config(self):
        return self._shodan_cfg


def test_load_query_budget_state_prefers_gui_settings_over_config(monkeypatch):
    monkeypatch.setattr(
        "gui.components.query_budget_dialog.load_config",
        lambda _path=None: _CfgStub(
            {
                "query_limits": {
                    "max_results": 1000,
                    "smb_max_query_credits_per_scan": 2,
                    "ftp_max_query_credits_per_scan": 3,
                    "http_max_query_credits_per_scan": 4,
                    "min_usable_hosts_target": 55,
                }
            }
        ),
    )

    sm = MagicMock()
    stored = {
        "query_budget.smb_max_query_credits_per_scan": 5,
        "query_budget.ftp_max_query_credits_per_scan": 6,
        "query_budget.http_max_query_credits_per_scan": 7,
    }
    sm.get_setting.side_effect = lambda key, default=None: stored.get(key, default)

    state = load_query_budget_state(settings_manager=sm, config_path="/tmp/config.json")

    assert state["smb_max_query_credits_per_scan"] == 5
    assert state["ftp_max_query_credits_per_scan"] == 6
    assert state["http_max_query_credits_per_scan"] == 7
    assert state["min_usable_hosts_target"] == 55


def test_load_query_budget_state_supports_legacy_smb_budget_key(monkeypatch):
    monkeypatch.setattr(
        "gui.components.query_budget_dialog.load_config",
        lambda _path=None: _CfgStub(
            {
                "query_limits": {
                    "max_results": 1000,
                    "max_query_credits_per_scan": 9,
                    "min_usable_hosts_target": 50,
                }
            }
        ),
    )

    state = load_query_budget_state(settings_manager=None, config_path=None)

    assert state["smb_max_query_credits_per_scan"] == 9
    assert state["ftp_max_query_credits_per_scan"] == 1
    assert state["http_max_query_credits_per_scan"] == 1


def test_persist_query_budget_state_clamps_and_writes_values():
    sm = MagicMock()

    persist_query_budget_state(
        sm,
        {
            "smb_max_query_credits_per_scan": 0,
            "ftp_max_query_credits_per_scan": 2,
            "http_max_query_credits_per_scan": "3",
        },
    )

    sm.set_setting.assert_has_calls(
        [
            call("query_budget.smb_max_query_credits_per_scan", 1),
            call("query_budget.ftp_max_query_credits_per_scan", 2),
            call("query_budget.http_max_query_credits_per_scan", 3),
        ],
        any_order=False,
    )
