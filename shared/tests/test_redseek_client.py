"""
Unit tests for redseek/client.py.

All tests mock urllib.request.urlopen. No real network calls.
429 contract: fetch_page raises RateLimitError; fetch_posts propagates it uncaught.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from experimental.redseek.client import (
    FetchError,
    FetchResult,
    PageResult,
    RateLimitError,
    fetch_page,
    fetch_posts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_payload(posts: list, after=None) -> bytes:
    """Build a minimal Reddit JSON response body."""
    return json.dumps({
        "data": {
            "children": [{"kind": "t3", "data": p} for p in posts],
            "after": after,
        }
    }).encode("utf-8")


def _mock_resp(body: bytes) -> MagicMock:
    """Create a mock usable as a urllib context manager."""
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = body
    return m


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs=None, fp=None)


# ---------------------------------------------------------------------------
# fetch_page — 429 handling
# ---------------------------------------------------------------------------

def test_fetch_page_429_raises_rate_limit_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(RateLimitError):
            fetch_page("new")


# ---------------------------------------------------------------------------
# fetch_page — other HTTP errors
# ---------------------------------------------------------------------------

def test_fetch_page_503_raises_fetch_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(503)):
        with pytest.raises(FetchError, match="HTTP 503"):
            fetch_page("new")


def test_fetch_page_404_raises_fetch_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        with pytest.raises(FetchError, match="HTTP 404"):
            fetch_page("new")


# ---------------------------------------------------------------------------
# fetch_page — network errors
# ---------------------------------------------------------------------------

def test_fetch_page_url_error_raises_fetch_error():
    err = urllib.error.URLError("connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(FetchError):
            fetch_page("new")


# ---------------------------------------------------------------------------
# fetch_page — decode errors
# ---------------------------------------------------------------------------

def test_fetch_page_decode_error_raises_fetch_error():
    resp = _mock_resp(b"\xff\xfe\x80\x81 invalid utf-8")
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="decode error"):
            fetch_page("new")


# ---------------------------------------------------------------------------
# fetch_page — malformed / unexpected JSON
# ---------------------------------------------------------------------------

def test_fetch_page_malformed_json_raises_fetch_error():
    resp = _mock_resp(b"not json")
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="malformed JSON"):
            fetch_page("new")


def test_fetch_page_missing_children_key_raises_fetch_error():
    body = json.dumps({"data": {"after": None}}).encode("utf-8")
    resp = _mock_resp(body)
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="unexpected response shape"):
            fetch_page("new")


def test_fetch_page_children_not_list_raises_fetch_error():
    body = json.dumps({"data": {"children": "oops", "after": None}}).encode("utf-8")
    resp = _mock_resp(body)
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="unexpected response shape"):
            fetch_page("new")


def test_fetch_page_missing_data_key_raises_fetch_error():
    body = json.dumps({"kind": "Listing"}).encode("utf-8")
    resp = _mock_resp(body)
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="unexpected response shape"):
            fetch_page("new")


# ---------------------------------------------------------------------------
# fetch_page — successful responses
# ---------------------------------------------------------------------------

def test_fetch_page_returns_correct_posts():
    posts = [{"id": "abc", "title": "Test post"}]
    resp = _mock_resp(_make_payload(posts, after="t3_next"))
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_page("new")
    assert isinstance(result, PageResult)
    assert len(result.posts) == 1
    assert result.posts[0]["id"] == "abc"
    assert result.next_after == "t3_next"


def test_fetch_page_next_after_none_on_last_page():
    resp = _mock_resp(_make_payload([], after=None))
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_page("new")
    assert result.next_after is None


def test_fetch_page_filters_non_t3_children():
    """Non-t3 children (comments, etc.) must not appear in posts."""
    payload = json.dumps({
        "data": {
            "children": [
                {"kind": "t3", "data": {"id": "p1"}},
                {"kind": "t1", "data": {"id": "c1"}},
                {"kind": "more", "data": {"id": "m1"}},
            ],
            "after": None,
        }
    }).encode("utf-8")
    resp = _mock_resp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_page("new")
    assert len(result.posts) == 1
    assert result.posts[0]["id"] == "p1"


# ---------------------------------------------------------------------------
# fetch_page — URL construction
# ---------------------------------------------------------------------------

def test_fetch_page_top_sort_includes_t_week():
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("top")
    req = mock_open.call_args[0][0]
    assert "t=week" in req.full_url


def test_fetch_page_new_sort_does_not_include_t_week():
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new")
    req = mock_open.call_args[0][0]
    assert "t=week" not in req.full_url


def test_fetch_page_after_param_included_in_url():
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new", after="t3_abc")
    req = mock_open.call_args[0][0]
    assert "after=t3_abc" in req.full_url


def test_fetch_page_no_after_param_on_first_page():
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new", after=None)
    req = mock_open.call_args[0][0]
    assert "after" not in req.full_url


# ---------------------------------------------------------------------------
# fetch_posts — input validation
# ---------------------------------------------------------------------------

def test_fetch_posts_invalid_sort_raises_value_error():
    with pytest.raises(ValueError, match="sort"):
        fetch_posts("hot")


def test_fetch_posts_invalid_sort_rising_raises_value_error():
    with pytest.raises(ValueError, match="sort"):
        fetch_posts("rising")


def test_fetch_posts_max_pages_zero_raises_value_error():
    with pytest.raises(ValueError, match="max_pages"):
        fetch_posts("new", max_pages=0)


def test_fetch_posts_max_pages_four_raises_value_error():
    with pytest.raises(ValueError, match="max_pages"):
        fetch_posts("new", max_pages=4)


# ---------------------------------------------------------------------------
# fetch_posts — 429 propagation
# ---------------------------------------------------------------------------

def test_fetch_posts_propagates_rate_limit_error():
    """RateLimitError must propagate uncaught — no FetchResult on 429."""
    with patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(RateLimitError):
            fetch_posts("new", max_pages=1)


# ---------------------------------------------------------------------------
# fetch_posts — max_pages cap
# ---------------------------------------------------------------------------

def test_fetch_posts_max_pages_cap_enforced():
    """fetch_posts must not make more calls to urlopen than max_pages."""
    call_count = 0

    def side_effect(req, timeout=20):
        nonlocal call_count
        call_count += 1
        # Always return a next cursor so pagination would continue without cap
        body = _make_payload([{"id": f"p{call_count}"}], after="cursor")
        return _mock_resp(body)

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("experimental.redseek.client.time.sleep"):
            result = fetch_posts("new", max_pages=2)

    assert call_count == 2
    assert result.pages_fetched == 2
    assert isinstance(result, FetchResult)


# ---------------------------------------------------------------------------
# fetch_posts — pagination stops on next_after=None
# ---------------------------------------------------------------------------

def test_fetch_posts_stops_on_no_next_after():
    """If next_after is None on first page, do not make a second request."""
    call_count = 0

    def side_effect(req, timeout=20):
        nonlocal call_count
        call_count += 1
        return _mock_resp(_make_payload([{"id": "p1"}], after=None))

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = fetch_posts("new", max_pages=3)

    assert call_count == 1
    assert result.pages_fetched == 1


# ---------------------------------------------------------------------------
# fetch_posts — sleep pacing
# ---------------------------------------------------------------------------

def test_fetch_posts_sleep_between_pages_not_before_first():
    """sleep(1) must be called between pages, but NOT before the first request."""
    sleep_calls = []

    def urlopen_side_effect(req, timeout=20):
        nonlocal sleep_calls
        call_n = len(sleep_calls)
        after = "cursor" if call_n < 2 else None
        return _mock_resp(_make_payload([{"id": f"p{call_n}"}], after=after))

    with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
        with patch("experimental.redseek.client.time.sleep", side_effect=sleep_calls.append):
            result = fetch_posts("new", max_pages=3)

    # 3 pages fetched → 2 sleeps (between page 1→2 and 2→3, not before 1)
    assert result.pages_fetched == 3
    assert len(sleep_calls) == 2
    assert all(s == 1 for s in sleep_calls)


# ---------------------------------------------------------------------------
# fetch_posts — aggregates posts across pages
# ---------------------------------------------------------------------------

def test_fetch_posts_aggregates_posts_across_pages():
    responses = [
        _make_payload([{"id": "p1"}, {"id": "p2"}], after="cursor1"),
        _make_payload([{"id": "p3"}], after=None),
    ]
    idx = 0

    def side_effect(req, timeout=20):
        nonlocal idx
        resp = _mock_resp(responses[idx])
        idx += 1
        return resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("experimental.redseek.client.time.sleep"):
            result = fetch_posts("new", max_pages=3)

    assert result.pages_fetched == 2
    assert len(result.posts) == 3
    assert [p["id"] for p in result.posts] == ["p1", "p2", "p3"]
