import sys
import time
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
        return {"query_limits": {"max_results": 5}}

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
        time.sleep(0.05)
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


def test_query_shodan_uses_paged_minimal_field_requests():
    op = _OpStub()

    ips, query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert ips == {"10.20.30.1", "10.20.30.2"}
    assert "country:US" in query
    call_kwargs = op.shodan_api.calls[0]
    assert call_kwargs.get("page") == 1
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


def test_query_shodan_keeps_partial_results_when_later_page_parse_fails(monkeypatch):
    op = _OpStub()

    class _ApiError(Exception):
        pass

    class _Config150(_ConfigStub):
        def get_shodan_config(self):
            return {"query_limits": {"max_results": 150}}

    class _SecondPageFails:
        def __init__(self):
            self.calls = 0

        def search(self, _query, **kwargs):
            self.calls += 1
            if kwargs.get("page") == 2:
                raise _ApiError("Unable to parse JSON response")
            matches = []
            for i in range(100):
                matches.append(
                    {
                        "ip_str": f"10.20.30.{i}",
                        "location": {"country_name": "United States", "country_code": "US"},
                        "org": "Example ISP",
                        "isp": "Example ISP",
                    }
                )
            return {"matches": matches}

    op.config = _Config150()
    op.shodan_api = _SecondPageFails()

    monkeypatch.setattr(shodan_query.shodan, "APIError", _ApiError, raising=False)

    ips, _query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert len(ips) == 100
    assert any("using 100 results collected so far" in msg.lower() for msg in op.output.warning_messages)


def test_query_shodan_keeps_partial_results_when_cursor_times_out(monkeypatch):
    op = _OpStub()

    class _ApiError(Exception):
        pass

    class _Config150(_ConfigStub):
        def get_shodan_config(self):
            return {"query_limits": {"max_results": 150}}

    class _SecondPageCursorTimeout:
        def search(self, _query, **kwargs):
            if kwargs.get("page") == 2:
                raise _ApiError("Search cursor timed out. Restart the search query from page 1.")
            matches = []
            for i in range(100):
                matches.append(
                    {
                        "ip_str": f"10.30.40.{i}",
                        "location": {"country_name": "United States", "country_code": "US"},
                        "org": "Example ISP",
                        "isp": "Example ISP",
                    }
                )
            return {"matches": matches}

    op.config = _Config150()
    op.shodan_api = _SecondPageCursorTimeout()

    monkeypatch.setattr(shodan_query.shodan, "APIError", _ApiError, raising=False)

    ips, _query = shodan_query.query_shodan(op, country="US", custom_filters="")

    assert len(ips) == 100
    assert any("paging interrupted" in msg.lower() for msg in op.output.warning_messages)


def test_build_targeted_query_keeps_org_exclusions():
    op = _OpStub()
    query = shodan_query.build_targeted_query(op, countries=["US"], custom_filters="")

    assert '-org:"Acme ISP"' in query
