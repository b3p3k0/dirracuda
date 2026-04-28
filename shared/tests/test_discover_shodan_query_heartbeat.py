import sys
import types


if "shodan" not in sys.modules:
    shodan_stub = types.ModuleType("shodan")
    shodan_stub.Shodan = object
    shodan_stub.APIError = Exception
    sys.modules["shodan"] = shodan_stub

from commands.discover import shodan_query


class _OutputStub:
    def __init__(self) -> None:
        self.info_messages = []
        self.success_messages = []
        self.verbose_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def success(self, message: str) -> None:
        self.success_messages.append(message)

    def print_if_verbose(self, message: str) -> None:
        self.verbose_messages.append(message)

    def warning(self, message: str) -> None:
        self.warning_messages.append(message)

    def error(self, message: str) -> None:
        self.error_messages.append(message)


class _ConfigStub:
    def resolve_target_countries(self, country):
        return [country] if country else []

    def get_shodan_config(self):
        return {
            "query_limits": {
                "max_results": 5,
                "max_query_credits_per_scan": 1,
                "min_usable_hosts_target": 50,
            }
        }

    def get(self, section, key=None, default=None):
        if section == "shodan" and key == "query_components":
            return {
                "base_query": "smb authentication: disabled",
                "product_filter": 'product:"Samba"',
                "additional_exclusions": [],
                "use_organization_exclusions": True,
            }
        return default


class _ShodanApiStub:
    def __init__(self) -> None:
        self.calls = []

    def search(self, _query, **kwargs):
        self.calls.append(kwargs)
        return {
            "matches": [
                {
                    "ip_str": "10.20.30.1",
                    "location": {"country_name": "United States", "country_code": "US"},
                    "org": "Example ISP",
                    "isp": "Example ISP",
                },
                {
                    "ip_str": "10.20.30.2",
                    "location": {"country_name": "United States", "country_code": "US"},
                    "org": "Example ISP",
                    "isp": "Example ISP",
                },
            ]
        }


class _OpStub:
    def __init__(self) -> None:
        self.output = _OutputStub()
        self.config = _ConfigStub()
        self.shodan_api = _ShodanApiStub()
        self.shodan_host_metadata = {}
        self.exclusions = ["Acme ISP"]
        self.stats = {"shodan_results": 0}


def test_query_shodan_uses_budgeted_page_request_for_first_page():
    op = _OpStub()

    ips, query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert ips == {"10.20.30.1", "10.20.30.2"}
    assert "country:US" in query
    assert len(op.shodan_api.calls) == 1
    call_kwargs = op.shodan_api.calls[0]
    assert call_kwargs.get("page") == 1
    assert "limit" not in call_kwargs
    assert call_kwargs.get("minify") is False
    assert call_kwargs.get("fields") == shodan_query.SHODAN_RESULT_FIELDS


def test_query_shodan_returns_empty_set_on_api_parse_error(monkeypatch):
    op = _OpStub()

    class _ApiError(Exception):
        pass

    monkeypatch.setattr(shodan_query.shodan, "APIError", _ApiError, raising=False)

    class _AlwaysFailApiStub:
        def search(self, _query, **kwargs):
            _ = kwargs
            raise _ApiError("Unable to parse JSON response")

    op.shodan_api = _AlwaysFailApiStub()

    ips, query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert ips == set()
    assert "country:US" in query
    assert any("Shodan API error" in msg for msg in op.output.error_messages)


def test_query_shodan_keeps_partial_results_on_cursor_timeout_when_budget_allows_more_pages(monkeypatch):
    op = _OpStub()

    class _ApiError(Exception):
        pass

    class _Config200(_ConfigStub):
        def get_shodan_config(self):
            return {
                "query_limits": {
                    "max_results": 200,
                    "max_query_credits_per_scan": 3,
                    "min_usable_hosts_target": 999,
                }
            }

    class _CursorTimeoutApiStub:
        def search(self, _query, **kwargs):
            if kwargs.get("page") == 2:
                raise _ApiError("Search cursor timed out. Restart the search query from page 1.")
            matches = []
            for idx in range(100):
                matches.append(
                    {
                        "ip_str": f"10.20.30.{idx}",
                        "location": {"country_name": "United States", "country_code": "US"},
                        "org": "Example ISP",
                        "isp": "Example ISP",
                    }
                )
            return {"matches": matches}

    op.config = _Config200()
    op.shodan_api = _CursorTimeoutApiStub()

    monkeypatch.setattr(shodan_query.shodan, "APIError", _ApiError, raising=False)

    ips, _query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert len(ips) == 100
    assert any("paging interrupted" in msg.lower() for msg in op.output.warning_messages)


def test_query_shodan_budget_cap_limits_to_one_page_when_budget_is_one():
    op = _OpStub()

    class _Config1000Budget1(_ConfigStub):
        def get_shodan_config(self):
            return {
                "query_limits": {
                    "max_results": 1000,
                    "max_query_credits_per_scan": 1,
                    "min_usable_hosts_target": 50,
                }
            }

    op.config = _Config1000Budget1()

    _ips, _query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert len(op.shodan_api.calls) == 1
    assert op.shodan_api.calls[0].get("page") == 1


def test_query_shodan_adaptive_stops_early_when_usable_target_hit():
    op = _OpStub()

    class _Config500Budget3Target2(_ConfigStub):
        def get_shodan_config(self):
            return {
                "query_limits": {
                    "max_results": 500,
                    "max_query_credits_per_scan": 3,
                    "min_usable_hosts_target": 2,
                }
            }

    class _PagedApiStub:
        def __init__(self):
            self.calls = []

        def search(self, _query, **kwargs):
            self.calls.append(kwargs)
            matches = []
            for idx in range(100):
                matches.append(
                    {
                        "ip_str": f"10.20.40.{idx}",
                        "location": {"country_name": "United States", "country_code": "US"},
                        "org": "Example ISP",
                        "isp": "Example ISP",
                    }
                )
            return {"matches": matches}

    op.config = _Config500Budget3Target2()
    op.shodan_api = _PagedApiStub()

    _ips, _query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert len(op.shodan_api.calls) == 1
    assert any("adaptive query target reached" in msg.lower() for msg in op.output.info_messages)


def test_build_targeted_query_does_not_embed_org_exclusions():
    op = _OpStub()
    query = shodan_query.build_targeted_query(op, countries=["US"], custom_filters="")

    assert '-org:"Acme ISP"' not in query
