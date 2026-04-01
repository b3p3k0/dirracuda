"""Tests for HTTP Shodan base-query configuration behavior."""

from __future__ import annotations

from commands.http.shodan_query import build_http_query
from shared.config import SMBSeekConfig


class _ConfigStub:
    def __init__(self, http_cfg):
        self._http_cfg = http_cfg

    def get_http_config(self):
        return self._http_cfg


class _WorkflowStub:
    def __init__(self, http_cfg):
        self.config = _ConfigStub(http_cfg)


def test_build_http_query_uses_configured_base_query():
    workflow = _WorkflowStub(
        {
            "shodan": {
                "query_components": {"base_query": 'http.html:"Directory Listing"'},
                "query_limits": {"max_results": 10},
            }
        }
    )

    query = build_http_query(workflow, ["US"], "port:8080")

    assert query.startswith('http.html:"Directory Listing"')
    assert "port:8080" in query
    assert "country:US" in query


def test_build_http_query_falls_back_when_query_blank():
    workflow = _WorkflowStub(
        {
            "shodan": {
                "query_components": {"base_query": "   "},
                "query_limits": {"max_results": 10},
            }
        }
    )

    query = build_http_query(workflow, [], None)

    assert query == 'http.title:"Index of /"'


def test_build_http_query_falls_back_when_query_missing():
    workflow = _WorkflowStub({"shodan": {"query_limits": {"max_results": 10}}})

    query = build_http_query(workflow, [], None)

    assert query == 'http.title:"Index of /"'


def test_http_config_defaults_include_query_components_base_query():
    cfg = SMBSeekConfig.__new__(SMBSeekConfig)
    cfg.config_file = "unused"
    cfg.config = {}

    http_cfg = cfg.get_http_config()

    assert http_cfg["shodan"]["query_components"]["base_query"] == 'http.title:"Index of /"'
