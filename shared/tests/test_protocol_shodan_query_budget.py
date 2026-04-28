"""Budget-cap tests for FTP/HTTP Shodan discovery helpers."""

from __future__ import annotations

import sys
import types

from commands.ftp import shodan_query as ftp_shodan_query
from commands.http import shodan_query as http_shodan_query


class _OutputStub:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []
        self.success_messages = []
        self.verbose_messages = []

    def info(self, message: str):
        self.info_messages.append(message)

    def warning(self, message: str):
        self.warning_messages.append(message)

    def error(self, message: str):
        self.error_messages.append(message)

    def success(self, message: str):
        self.success_messages.append(message)

    def print_if_verbose(self, message: str):
        self.verbose_messages.append(message)


class _ConfigStub:
    def __init__(
        self,
        *,
        ftp_budget: int = 1,
        http_budget: int = 1,
        ftp_max_results: int = 1000,
        http_max_results: int = 1000,
        global_max_results: int = 1000,
    ):
        self.ftp_budget = ftp_budget
        self.http_budget = http_budget
        self.ftp_max_results = ftp_max_results
        self.http_max_results = http_max_results
        self.global_max_results = global_max_results

    def resolve_target_countries(self, country):
        return [country] if country else []

    def get_ftp_config(self):
        return {
            "shodan": {
                "query_components": {
                    "base_query": 'port:21 "230 Login successful"',
                    "additional_exclusions": [],
                },
                "query_limits": {"max_results": self.ftp_max_results},
            }
        }

    def get_http_config(self):
        return {
            "shodan": {
                "query_components": {"base_query": 'http.title:"Index of /"'},
                "query_limits": {"max_results": self.http_max_results},
            }
        }

    def get_shodan_config(self):
        return {
            "query_limits": {
                "max_results": self.global_max_results,
                "ftp_max_query_credits_per_scan": self.ftp_budget,
                "http_max_query_credits_per_scan": self.http_budget,
            }
        }

    def get_shodan_api_key(self):
        return "TEST_KEY"


class _WorkflowStub:
    def __init__(self, config):
        self.config = config
        self.output = _OutputStub()


def _install_shodan_module(monkeypatch, api_stub):
    class _ApiError(Exception):
        pass

    class _ShodanClient:
        def __init__(self, _api_key):
            pass

        def search(self, query, **kwargs):
            return api_stub.search(query, **kwargs)

    shodan_mod = types.ModuleType("shodan")
    shodan_mod.APIError = _ApiError
    shodan_mod.Shodan = _ShodanClient
    monkeypatch.setitem(sys.modules, "shodan", shodan_mod)


def test_query_ftp_shodan_uses_single_page_when_budget_is_one(monkeypatch):
    class _ApiStub:
        def __init__(self):
            self.calls = []

        def search(self, _query, **kwargs):
            self.calls.append(kwargs)
            return {
                "matches": [
                    {"ip_str": "10.0.0.1", "port": 21},
                    {"ip_str": "10.0.0.2", "port": 21},
                ]
            }

    api = _ApiStub()
    _install_shodan_module(monkeypatch, api)

    workflow = _WorkflowStub(_ConfigStub(ftp_budget=1, ftp_max_results=1000))
    candidates = ftp_shodan_query.query_ftp_shodan(workflow)

    assert len(candidates) == 2
    assert len(api.calls) == 1
    assert api.calls[0].get("page") == 1
    assert "limit" not in api.calls[0]


def test_query_ftp_shodan_respects_budgeted_page_cap(monkeypatch):
    class _ApiStub:
        def __init__(self):
            self.calls = []

        def search(self, _query, **kwargs):
            self.calls.append(kwargs)
            page = int(kwargs.get("page", 1))
            base = (page - 1) * 100
            matches = [{"ip_str": f"10.0.1.{idx}", "port": 21} for idx in range(base, base + 100)]
            return {"matches": matches}

    api = _ApiStub()
    _install_shodan_module(monkeypatch, api)

    workflow = _WorkflowStub(_ConfigStub(ftp_budget=3, ftp_max_results=250))
    candidates = ftp_shodan_query.query_ftp_shodan(workflow)

    assert len(candidates) == 250
    assert len(api.calls) == 3


def test_query_http_shodan_uses_single_page_when_budget_is_one(monkeypatch):
    class _ApiStub:
        def __init__(self):
            self.calls = []

        def search(self, _query, **kwargs):
            self.calls.append(kwargs)
            return {
                "matches": [
                    {"ip_str": "20.0.0.1", "port": 80, "http": {"title": "Index of /"}},
                    {"ip_str": "20.0.0.2", "port": 443, "http": {"title": "Index of /"}},
                ]
            }

    api = _ApiStub()
    _install_shodan_module(monkeypatch, api)

    workflow = _WorkflowStub(_ConfigStub(http_budget=1, http_max_results=1000))
    candidates = http_shodan_query.query_http_shodan(workflow)

    assert len(candidates) == 2
    assert len(api.calls) == 1
    assert api.calls[0].get("page") == 1
    assert "limit" not in api.calls[0]
