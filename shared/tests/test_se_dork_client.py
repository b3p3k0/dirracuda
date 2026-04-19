"""
Unit tests for experimental.se_dork.client.run_preflight.

All HTTP calls are mocked via unittest.mock.patch on urllib.request.urlopen.
No network access required.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.se_dork.client import run_preflight
from experimental.se_dork.models import (
    INSTANCE_FORMAT_FORBIDDEN,
    INSTANCE_NON_JSON,
    INSTANCE_UNREACHABLE,
    SEARCH_HTTP_ERROR,
    SEARCH_PARSE_ERROR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(body: bytes, status: int = 200):
    """Context-manager-compatible response mock."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _json_body(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def _side_effects(*responses):
    """
    Return a side_effect list for sequential urlopen calls.
    Each element is either a response mock or an exception instance.
    """
    calls = iter(responses)

    def _effect(url, timeout=None):
        val = next(calls)
        if isinstance(val, BaseException):
            raise val
        return val

    return _effect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_preflight_success():
    config_resp = _mock_response(b"{}", 200)
    search_resp = _mock_response(_json_body({"results": []}), 200)

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, search_resp)):
        result = run_preflight("http://sx:8090")

    assert result.ok is True
    assert result.reason_code is None
    assert "OK" in result.message


def test_preflight_instance_unreachable_on_config():
    err = urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=_side_effects(err)):
        result = run_preflight("http://bad-host:9999")

    assert result.ok is False
    assert result.reason_code == INSTANCE_UNREACHABLE
    assert "connection refused" in result.message.lower() or "reach" in result.message.lower()


def test_preflight_instance_unreachable_on_search():
    config_resp = _mock_response(b"{}", 200)
    err = urllib.error.URLError("timed out")

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, err)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == INSTANCE_UNREACHABLE


def test_preflight_format_forbidden():
    config_resp = _mock_response(b"{}", 200)
    http_err = urllib.error.HTTPError(
        url="http://sx:8090/search?q=hello&format=json",
        code=403,
        msg="Forbidden",
        hdrs={},
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, http_err)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == INSTANCE_FORMAT_FORBIDDEN


def test_preflight_format_forbidden_hint_contains_settings_yml():
    config_resp = _mock_response(b"{}", 200)
    http_err = urllib.error.HTTPError(
        url="http://sx:8090/search?q=hello&format=json",
        code=403,
        msg="Forbidden",
        hdrs={},
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, http_err)):
        result = run_preflight("http://sx:8090")

    assert "search.formats" in result.message


def test_preflight_search_http_error():
    config_resp = _mock_response(b"{}", 200)
    http_err = urllib.error.HTTPError(
        url="http://sx:8090/search?q=hello&format=json",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, http_err)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == SEARCH_HTTP_ERROR
    assert "500" in result.message


def test_preflight_non_json():
    config_resp = _mock_response(b"{}", 200)
    search_resp = _mock_response(b"not-json-at-all", 200)

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, search_resp)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == INSTANCE_NON_JSON


def test_preflight_search_parse_error_missing_results_key():
    config_resp = _mock_response(b"{}", 200)
    search_resp = _mock_response(_json_body({"query": "hello"}), 200)

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, search_resp)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == SEARCH_PARSE_ERROR


def test_preflight_search_parse_error_results_not_list():
    config_resp = _mock_response(b"{}", 200)
    search_resp = _mock_response(_json_body({"results": "oops"}), 200)

    with patch("urllib.request.urlopen", side_effect=_side_effects(config_resp, search_resp)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == SEARCH_PARSE_ERROR


def test_preflight_trailing_slash_stripped():
    """Trailing slash on instance URL must not produce double-slash paths."""
    config_resp = _mock_response(b"{}", 200)
    search_resp = _mock_response(_json_body({"results": []}), 200)
    captured_urls = []

    def _capture(url, timeout=None):
        captured_urls.append(url)
        if "config" in url:
            return config_resp
        return search_resp

    with patch("urllib.request.urlopen", side_effect=_capture):
        run_preflight("http://sx:8090/")

    assert all("//" not in u.replace("http://", "") for u in captured_urls)


def test_preflight_config_http_error():
    http_err = urllib.error.HTTPError(
        url="http://sx:8090/config",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=_side_effects(http_err)):
        result = run_preflight("http://sx:8090")

    assert result.ok is False
    assert result.reason_code == INSTANCE_UNREACHABLE
    assert "404" in result.message
