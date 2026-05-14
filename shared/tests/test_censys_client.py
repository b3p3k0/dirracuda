from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.censys_discovery.client import CensysClient, _redact
from experimental.censys_discovery.models import (
    AUTH_FORBIDDEN,
    AUTH_UNAUTHORIZED,
    CLIENT_ERROR,
    NETWORK_ERROR,
    NOT_FOUND,
    QUERY_INVALID,
    RESPONSE_PARSE_ERROR,
    SERVER_ERROR,
    CreditBalance,
    SearchPage,
)

_BASE = "https://fake.censys.io"
_PAT  = "test-pat-abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk() -> CensysClient:
    return CensysClient(pat=_PAT, base_url=_BASE)


def _mock_response(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _jb(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def _http_err(code: int, reason: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=f"{_BASE}/v3/global/search/query",
        code=code,
        msg=reason,
        hdrs={},
        fp=io.BytesIO(b""),
    )


def _search_envelope(hits=None, next_token=None, total=1.0) -> bytes:
    return _jb({"result": {
        "hits": hits or [],
        "next_page_token": next_token,
        "total_hits": total,
    }})


def _simple_hit(ip="1.2.3.4", port=21, proto="FTP", trans="TCP") -> dict:
    return {
        "host_v1": {
            "resource": {"ip": ip},
            "matched_services": [{"port": port, "protocol": proto, "transport_protocol": trans}],
        }
    }


# ---------------------------------------------------------------------------
# Auth / error-code tests
# ---------------------------------------------------------------------------

def test_search_query_400_returns_query_invalid():
    with patch("urllib.request.urlopen", side_effect=_http_err(400)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == QUERY_INVALID


def test_search_query_401_returns_auth_unauthorized():
    with patch("urllib.request.urlopen", side_effect=_http_err(401)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == AUTH_UNAUTHORIZED


def test_search_query_403_returns_auth_forbidden():
    with patch("urllib.request.urlopen", side_effect=_http_err(403)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == AUTH_FORBIDDEN


def test_search_query_404_returns_not_found():
    with patch("urllib.request.urlopen", side_effect=_http_err(404)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == NOT_FOUND


def test_search_query_422_returns_query_invalid():
    with patch("urllib.request.urlopen", side_effect=_http_err(422)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == QUERY_INVALID


def test_search_query_500_returns_server_error():
    with patch("urllib.request.urlopen", side_effect=_http_err(500)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == SERVER_ERROR


def test_search_query_unmapped_4xx_returns_client_error():
    with patch("urllib.request.urlopen", side_effect=_http_err(429)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == CLIENT_ERROR


def test_search_query_network_error_returns_network_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == NETWORK_ERROR


def test_pat_not_in_error_message_on_401():
    with patch("urllib.request.urlopen", side_effect=_http_err(401, reason=f"Bearer {_PAT}")):
        result = _mk().search_query("q")
    assert _PAT not in result.error.message


def test_bearer_auth_header_sent():
    captured = []

    def _capture(req, timeout=None):
        captured.append(req)
        return _mock_response(_search_envelope())

    with patch("urllib.request.urlopen", side_effect=_capture):
        _mk().search_query("host.services:(protocol=FTP and port=21)")

    assert len(captured) == 1
    assert captured[0].get_header("Authorization") == f"Bearer {_PAT}"


# ---------------------------------------------------------------------------
# Request-shape / payload-key tests
# ---------------------------------------------------------------------------

def _capture_body(method_call) -> dict:
    captured = []

    def _effect(req, timeout=None):
        captured.append(req)
        return _mock_response(_search_envelope())

    with patch("urllib.request.urlopen", side_effect=_effect):
        method_call()

    return json.loads(captured[0].data.decode("utf-8"))


def test_search_query_payload_uses_query_key():
    body = _capture_body(lambda: _mk().search_query("myquery"))
    assert "query" in body
    assert body["query"] == "myquery"


def test_search_query_payload_uses_page_size_key():
    body = _capture_body(lambda: _mk().search_query("q", page_size=50))
    assert "page_size" in body
    assert body["page_size"] == 50


def test_search_query_payload_uses_page_token_key():
    body = _capture_body(lambda: _mk().search_query("q", page_token="cursor123"))
    assert "page_token" in body
    assert body["page_token"] == "cursor123"


def test_search_query_no_page_token_when_none():
    body = _capture_body(lambda: _mk().search_query("q", page_token=None))
    assert "page_token" not in body


def test_search_query_fields_list_sent():
    body = _capture_body(lambda: _mk().search_query("q"))
    assert "fields" in body
    assert "host.services.port" in body["fields"]


def _capture_aggregate_body(client_call) -> dict:
    captured = []

    def _effect(req, timeout=None):
        captured.append(req)
        return _mock_response(_jb({"result": {}}))

    with patch("urllib.request.urlopen", side_effect=_effect):
        client_call()

    return json.loads(captured[0].data.decode("utf-8"))


def test_search_aggregate_payload_keys():
    body = _capture_aggregate_body(
        lambda: _mk().search_aggregate("q", field="host.services.port", number_of_buckets=5)
    )
    assert "query" in body
    assert "field" in body
    assert "number_of_buckets" in body
    assert body["number_of_buckets"] == 5


def test_search_aggregate_no_q_key():
    body = _capture_aggregate_body(lambda: _mk().search_aggregate("q", field="f"))
    assert "q" not in body


def test_search_aggregate_no_num_buckets_key():
    body = _capture_aggregate_body(lambda: _mk().search_aggregate("q", field="f"))
    assert "num_buckets" not in body


# ---------------------------------------------------------------------------
# Response-parsing tests
# ---------------------------------------------------------------------------

def test_search_query_success_returns_items():
    body = _search_envelope(hits=[_simple_hit()])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert isinstance(result.data, SearchPage)
    assert isinstance(result.data.items, list)


def test_search_query_empty_hits_returns_empty_list():
    body = _search_envelope(hits=[])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.items == []


def test_parse_missing_result_key():
    body = _jb({})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == RESPONSE_PARSE_ERROR


def test_parse_missing_hits_key():
    body = _jb({"result": {"next_page_token": None, "total_hits": 0}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.items == []


def test_parse_next_page_token_absent():
    body = _jb({"result": {"hits": [], "total_hits": 0}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.next_cursor is None


def test_parse_malformed_json_body():
    with patch("urllib.request.urlopen", return_value=_mock_response(b"not-json")):
        result = _mk().search_query("q")
    assert not result.ok
    assert result.error.reason_code == RESPONSE_PARSE_ERROR


def test_parse_next_page_token_extracted():
    body = _search_envelope(next_token="abc")
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.next_cursor == "abc"


def test_parse_hit_ip_from_host_v1_resource():
    body = _search_envelope(hits=[_simple_hit(ip="9.8.7.6")])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.data.items[0].ip_address == "9.8.7.6"


def test_parse_hit_services_from_host_v1():
    hit = {
        "host_v1": {
            "resource": {"ip": "1.1.1.1"},
            "matched_services": [{"port": 21, "protocol": "FTP", "transport_protocol": "TCP"}],
        },
        "matched_services": [{"port": 22, "protocol": "SSH", "transport_protocol": "TCP"}],
    }
    body = _search_envelope(hits=[hit])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.data.items[0].port == 21
    assert result.data.items[0].protocol == "FTP"


def test_parse_hit_missing_host_v1_uses_defaults():
    body = _search_envelope(hits=[{"no_host_v1": True}])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    item = result.data.items[0]
    assert item.ip_address == ""
    assert item.port == 0


def test_parse_hit_banner_from_resource_services():
    hit = {
        "host_v1": {
            "resource": {
                "ip": "2.2.2.2",
                "services": [
                    {"port": 21, "protocol": "FTP", "transport_protocol": "TCP",
                     "banner": "Welcome to FTP"},
                ],
            },
            "matched_services": [{"port": 21, "protocol": "FTP", "transport_protocol": "TCP"}],
        }
    }
    body = _search_envelope(hits=[hit])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.data.items[0].banner == "Welcome to FTP"


def test_parse_hit_scan_time_from_resource_services():
    hit = {
        "host_v1": {
            "resource": {
                "ip": "3.3.3.3",
                "services": [
                    {"port": 21, "protocol": "FTP", "transport_protocol": "TCP",
                     "observed_at": "2024-01-01T00:00:00Z"},
                ],
            },
            "matched_services": [{"port": 21, "protocol": "FTP", "transport_protocol": "TCP"}],
        }
    }
    body = _search_envelope(hits=[hit])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.data.items[0].scan_time == "2024-01-01T00:00:00Z"


def test_parse_hit_identity_from_matched_services():
    hit = {
        "host_v1": {
            "resource": {
                "ip": "4.4.4.4",
                "services": [
                    {"port": 21, "protocol": "FTP", "transport_protocol": "TCP",
                     "banner": "FTP Banner"},
                    {"port": 80, "protocol": "HTTP", "transport_protocol": "TCP",
                     "banner": "HTTP Banner"},
                ],
            },
            "matched_services": [{"port": 21, "protocol": "FTP", "transport_protocol": "TCP"}],
        }
    }
    body = _search_envelope(hits=[hit])
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    item = result.data.items[0]
    assert item.port == 21
    assert item.banner == "FTP Banner"


def test_parse_total_hits_float_coerced_to_int():
    body = _search_envelope(hits=[], total=42.0)
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.total_hits == 42
    assert isinstance(result.data.total_hits, int)


def test_parse_total_hits_absent_returns_none():
    body = _jb({"result": {"hits": []}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.total_hits is None


def test_parse_next_page_token_fallback_to_links_next():
    body = _jb({"result": {"hits": [], "links": {"next": "cursor_abc"}, "total_hits": 0}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.ok
    assert result.data.next_cursor == "cursor_abc"


def test_parse_pagination_prefers_next_page_token_over_links():
    body = _jb({"result": {
        "hits": [],
        "next_page_token": "tok1",
        "links": {"next": "tok2"},
        "total_hits": 0,
    }})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_query("q")
    assert result.data.next_cursor == "tok1"


# ---------------------------------------------------------------------------
# Credit-endpoint tests
# ---------------------------------------------------------------------------

def test_get_user_credits_success_returns_credit_balance():
    body = _jb({"result": {"balance": 250, "resets_at": "2024-06-01"}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().get_user_credits()
    assert result.ok
    assert isinstance(result.data, CreditBalance)
    assert result.data.balance == 250
    assert result.data.resets_at == "2024-06-01"


def test_get_user_credits_401():
    with patch("urllib.request.urlopen", side_effect=_http_err(401)):
        result = _mk().get_user_credits()
    assert not result.ok
    assert result.error.reason_code == AUTH_UNAUTHORIZED


def test_get_user_credits_parse_error_missing_result():
    body = _jb({})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().get_user_credits()
    assert not result.ok
    assert result.error.reason_code == RESPONSE_PARSE_ERROR


def test_get_user_credits_parse_error_missing_balance():
    body = _jb({"result": {}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().get_user_credits()
    assert not result.ok
    assert result.error.reason_code == RESPONSE_PARSE_ERROR


def _capture_url(method_call) -> str:
    captured = []

    def _effect(req, timeout=None):
        captured.append(req)
        return _mock_response(_jb({}))

    with patch("urllib.request.urlopen", side_effect=_effect):
        method_call()

    return captured[0].get_full_url()


def test_get_user_credits_usage_sends_start_date():
    url = _capture_url(lambda: _mk().get_user_credits_usage("2024-01-01"))
    assert "start_date=2024-01-01" in url


def test_get_user_credits_usage_sends_granularity_default():
    url = _capture_url(lambda: _mk().get_user_credits_usage("2024-01-01"))
    assert "granularity=daily" in url


def test_get_user_credits_usage_sends_optional_end_date():
    url = _capture_url(lambda: _mk().get_user_credits_usage("2024-01-01", end_date="2024-01-31"))
    assert "end_date=2024-01-31" in url


def test_get_user_credits_usage_omits_end_date_when_none():
    url = _capture_url(lambda: _mk().get_user_credits_usage("2024-01-01"))
    assert "end_date" not in url


def test_get_org_credits_success_returns_credit_balance():
    body = _jb({"result": {
        "balance": 500,
        "uid": "org-uid-123",
        "credit_expirations": [],
        "auto_replenish_config": {},
    }})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().get_org_credits("org-123")
    assert result.ok
    assert isinstance(result.data, CreditBalance)
    assert result.data.balance == 500


def test_get_org_credits_404_returns_not_found():
    with patch("urllib.request.urlopen", side_effect=_http_err(404)):
        result = _mk().get_org_credits("bad-org")
    assert not result.ok
    assert result.error.reason_code == NOT_FOUND


def test_get_org_credits_403():
    with patch("urllib.request.urlopen", side_effect=_http_err(403)):
        result = _mk().get_org_credits("org-123")
    assert not result.ok
    assert result.error.reason_code == AUTH_FORBIDDEN


def test_get_org_credits_usage_sends_start_date():
    url = _capture_url(lambda: _mk().get_org_credits_usage("org-123", "2024-01-01"))
    assert "start_date=2024-01-01" in url


def test_get_org_credits_usage_sends_granularity_default():
    url = _capture_url(lambda: _mk().get_org_credits_usage("org-123", "2024-01-01"))
    assert "granularity=daily" in url


def test_get_org_credits_usage_sends_optional_end_date():
    url = _capture_url(
        lambda: _mk().get_org_credits_usage("org-123", "2024-01-01", end_date="2024-01-31")
    )
    assert "end_date=2024-01-31" in url


def test_get_org_credits_usage_omits_end_date_when_none():
    url = _capture_url(lambda: _mk().get_org_credits_usage("org-123", "2024-01-01"))
    assert "end_date" not in url


def test_search_aggregate_with_org_id_sends_query_param():
    captured = []

    def _effect(req, timeout=None):
        captured.append(req)
        return _mock_response(_jb({"result": {}}))

    with patch("urllib.request.urlopen", side_effect=_effect):
        _mk().search_aggregate("q", field="f", org_id="org-xyz")

    assert "organization_id=org-xyz" in captured[0].get_full_url()


def test_search_aggregate_success():
    body = _jb({"result": {"buckets": [{"key": "FTP", "count": 5}]}})
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        result = _mk().search_aggregate("q", field="host.services.protocol")
    assert result.ok


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

def test_redact_replaces_pat_in_message():
    assert _redact("token=abc123", "abc123") == "token=<redacted>"


def test_redact_empty_pat_guard():
    assert _redact("hello world", "") == "hello world"


def test_redact_empty_text_guard():
    assert _redact("", "secret") == ""
