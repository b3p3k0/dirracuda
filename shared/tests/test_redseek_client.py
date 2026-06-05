"""
Unit tests for redseek/client.py.

All tests mock urllib.request.urlopen. No live Reddit calls.
"""

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
    fetch_search_page,
    fetch_search_posts,
)


def _mock_resp(body: bytes) -> MagicMock:
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = body
    return m


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs=None, fp=None)


def _feed(entries: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/opendirectories</title>
  {entries}
</feed>
""".encode("utf-8")


def _entry(
    post_id: str = "t3_abc123",
    title: str = "Open directory",
    author: str = "/u/testuser",
    published: str = "2026-06-04T15:42:25+00:00",
    content: str = '<div><p>Index at <a href="https://files.example.net/pub/">files</a></p></div>',
) -> str:
    return f"""
  <entry>
    <id>{post_id}</id>
    <title>{title}</title>
    <published>{published}</published>
    <updated>{published}</updated>
    <author><name>{author}</name></author>
    <link href="https://www.reddit.com/r/opendirectories/comments/abc123/open_directory/" />
    <content type="html">{content}</content>
  </entry>
"""


def test_fetch_page_parses_atom_entry_to_internal_post_shape():
    resp = _mock_resp(_feed(_entry()))
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_page("new")

    assert isinstance(result, PageResult)
    assert result.next_after is None
    assert len(result.posts) == 1
    post = result.posts[0]
    assert post["id"] == "abc123"
    assert post["title"] == "Open directory"
    assert post["author"] == "testuser"
    assert post["created_utc"] == pytest.approx(1780587745.0)
    assert post["over_18"] is False
    assert post["subreddit"] == "opendirectories"


def test_fetch_page_preserves_anchor_href_in_selftext():
    resp = _mock_resp(_feed(_entry(content='<p>See <a href="ftp://files.example.net/pub/">mirror</a></p>')))
    with patch("urllib.request.urlopen", return_value=resp):
        post = fetch_page("new").posts[0]
    assert "mirror" in post["selftext"]
    assert "ftp://files.example.net/pub/" in post["selftext"]


def test_fetch_page_skips_entry_without_required_fields():
    entries = _entry(post_id="") + _entry(post_id="t3_ok")
    resp = _mock_resp(_feed(entries))
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_page("new")
    assert [p["id"] for p in result.posts] == ["ok"]


def test_fetch_page_uses_rss_endpoint():
    resp = _mock_resp(_feed(""))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new")
    req = mock_open.call_args[0][0]
    assert "/r/opendirectories/new.rss" in req.full_url
    assert ".json" not in req.full_url
    assert "limit=100" in req.full_url


def test_fetch_page_top_sort_includes_t_param():
    resp = _mock_resp(_feed(""))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("top", top_window="month")
    req = mock_open.call_args[0][0]
    assert "/r/opendirectories/top.rss" in req.full_url
    assert "t=month" in req.full_url


def test_fetch_page_ignores_after_param_for_rss():
    resp = _mock_resp(_feed(""))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new", after="t3_cursor")
    req = mock_open.call_args[0][0]
    assert "after=" not in req.full_url


def test_fetch_posts_accepts_max_pages_but_fetches_one_snapshot():
    call_count = 0

    def side_effect(req, timeout=20):
        nonlocal call_count
        call_count += 1
        return _mock_resp(_feed(_entry(post_id=f"t3_p{call_count}")))

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = fetch_posts("new", max_pages=3)

    assert call_count == 1
    assert result.pages_fetched == 1
    assert isinstance(result, FetchResult)
    assert [p["id"] for p in result.posts] == ["p1"]


def test_fetch_posts_requests_and_trims_to_requested_snapshot_limit():
    entries = "".join(_entry(post_id=f"t3_p{i}") for i in range(3))
    resp = _mock_resp(_feed(entries))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        result = fetch_posts("new", max_posts=2)

    req = mock_open.call_args[0][0]
    assert "limit=2" in req.full_url
    assert [post["id"] for post in result.posts] == ["p0", "p1"]


def test_fetch_posts_accepts_full_100_entry_snapshot():
    entries = "".join(_entry(post_id=f"t3_p{i}") for i in range(100))
    resp = _mock_resp(_feed(entries))
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_posts("new", max_posts=100)

    assert len(result.posts) == 100


def test_fetch_posts_invalid_sort_raises_value_error():
    with pytest.raises(ValueError, match="sort"):
        fetch_posts("hot")


def test_fetch_posts_invalid_max_pages_raises_value_error():
    with pytest.raises(ValueError, match="max_pages"):
        fetch_posts("new", max_pages=4)


def test_fetch_search_page_uses_search_rss_with_query_and_restrict_sr():
    resp = _mock_resp(_feed(""))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_search_page("ftp files", "new")
    req = mock_open.call_args[0][0]
    assert "/r/opendirectories/search.rss" in req.full_url
    assert "q=ftp+files" in req.full_url or "q=ftp%20files" in req.full_url
    assert "restrict_sr=1" in req.full_url
    assert "sort=new" in req.full_url
    assert "limit=100" in req.full_url


def test_fetch_search_page_top_includes_t_param():
    resp = _mock_resp(_feed(""))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_search_page("music", "top", top_window="year")
    req = mock_open.call_args[0][0]
    assert "t=year" in req.full_url


def test_fetch_search_posts_accepts_max_pages_but_fetches_one_snapshot():
    call_count = 0

    def side_effect(req, timeout=20):
        nonlocal call_count
        call_count += 1
        return _mock_resp(_feed(_entry(post_id="t3_search1")))

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = fetch_search_posts("ftp", "new", max_pages=2)

    assert call_count == 1
    assert result.pages_fetched == 1
    assert [p["id"] for p in result.posts] == ["search1"]


def test_fetch_search_posts_empty_query_raises_value_error():
    with pytest.raises(ValueError, match="query"):
        fetch_search_posts("", "new")


def test_fetch_posts_rejects_limit_above_rss_snapshot_cap():
    with pytest.raises(ValueError, match="max_posts"):
        fetch_posts("new", max_posts=101)


def test_fetch_page_429_raises_rate_limit_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(RateLimitError):
            fetch_page("new")


def test_fetch_page_403_raises_fetch_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(403)):
        with pytest.raises(FetchError, match="HTTP 403"):
            fetch_page("new")


def test_fetch_page_url_error_raises_fetch_error():
    err = urllib.error.URLError("connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(FetchError, match="connection refused"):
            fetch_page("new")


def test_fetch_page_decode_error_raises_fetch_error():
    resp = _mock_resp(b"\xff\xfe\x80\x81 invalid utf-8")
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="decode error"):
            fetch_page("new")


def test_fetch_page_malformed_xml_raises_fetch_error():
    resp = _mock_resp(b"<feed><entry>")
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(FetchError, match="malformed RSS/Atom"):
            fetch_page("new")


def test_fetch_search_posts_propagates_rate_limit_error():
    with patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(RateLimitError):
            fetch_search_posts("query", "new", max_pages=1)
