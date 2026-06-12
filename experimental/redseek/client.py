"""
Anonymous Reddit RSS/Atom client for the redseek module.

Reddit's unauthenticated ``.json`` listing endpoints now return HTTP 403 for
direct script access. This client uses public Atom/RSS feeds instead and maps
entries into the raw-post dict shape that ``service.py`` already consumes.

RSS contract:
  - one anonymous feed snapshot per request; no Reddit JSON cursor pagination
  - ``max_pages`` is accepted for compatibility but effective pages fetched is 1
  - no OAuth, cookies, browser token reuse, or HTML scraping
"""

from __future__ import annotations

import datetime
import html
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from experimental.redseek.models import DEFAULT_MAX_POSTS, MAX_POSTS

_BASE_URL = "https://www.reddit.com/r/opendirectories/{sort}.rss"
_SEARCH_URL = "https://www.reddit.com/r/opendirectories/search.rss"
_USER_AGENT = "dirracuda:reddit_rss_ingest:v1.0"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class RateLimitError(Exception):
    """HTTP 429. Propagates to service layer — never silently swallowed."""


class FetchError(Exception):
    """Network failure, decode error, malformed XML, or unexpected feed shape."""


@dataclass
class PageResult:
    posts: list          # list[dict] — raw Reddit-like post data dicts
    next_after: Optional[str]  # RSS has no JSON cursor; always None


@dataclass
class FetchResult:
    posts: list          # list[dict] — posts from one RSS snapshot
    pages_fetched: int


class _ContentExtractor(HTMLParser):
    """Extract visible text plus href targets from Reddit Atom content HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower().split(":", 1)[-1]
        if tag_name == "a":
            for key, value in attrs:
                attr_name = key.lower().split(":", 1)[-1]
                if attr_name == "href" and value:
                    self.hrefs.append(html.unescape(value))

    def handle_data(self, data: str) -> None:
        text = " ".join((data or "").split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        seen = set()
        hrefs = []
        for href in self.hrefs:
            if href not in seen:
                seen.add(href)
                hrefs.append(href)
        return " ".join([*self.parts, *hrefs]).strip()


def _request_feed(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimitError("HTTP 429") from e
        raise FetchError(f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(str(e.reason)) from e


def _text(node: Optional[ET.Element], tag: str) -> str:
    if node is None:
        return ""
    child = node.find(f"{_ATOM_NS}{tag}")
    if child is None:
        child = node.find(tag)
    return str(child.text or "") if child is not None else ""


def _author_name(entry: ET.Element) -> Optional[str]:
    author = entry.find(f"{_ATOM_NS}author")
    if author is None:
        author = entry.find("author")
    raw = _text(author, "name")
    if not raw:
        return None
    text = raw.strip()
    if text.lower().startswith("/u/"):
        text = text[3:].strip()
    elif text.lower().startswith("u/"):
        text = text[2:].strip()
    return text or None


def _entry_link(entry: ET.Element) -> str:
    for link in list(entry.findall(f"{_ATOM_NS}link")) + list(entry.findall("link")):
        href = str(link.attrib.get("href") or "").strip()
        if href:
            return href
    return ""


def _parse_created_utc(entry: ET.Element) -> Optional[float]:
    raw = _text(entry, "published") or _text(entry, "updated")
    if not raw:
        return None
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return float(dt.timestamp())


def _entry_content_text(entry: ET.Element) -> str:
    content = entry.find(f"{_ATOM_NS}content")
    if content is None:
        content = entry.find("content")
    raw = ""
    if content is not None:
        raw = str(content.text or "")
        if not raw.strip() and list(content):
            raw = " ".join(
                ET.tostring(child, encoding="unicode", method="html")
                for child in list(content)
            )
    if not raw:
        return ""
    parser = _ContentExtractor()
    try:
        parser.feed(html.unescape(raw))
        parser.close()
    except Exception:
        return " ".join(html.unescape(raw).split())
    return parser.text()


def _entry_id(entry: ET.Element) -> str:
    raw = _text(entry, "id").strip()
    if raw.lower().startswith("t3_"):
        raw = raw[3:]
    return raw


def _entry_to_post(entry: ET.Element) -> Optional[dict]:
    post_id = _entry_id(entry)
    created_utc = _parse_created_utc(entry)
    if not post_id or created_utc is None:
        return None
    link = _entry_link(entry)
    return {
        "id": post_id,
        "created_utc": created_utc,
        "title": _text(entry, "title"),
        "author": _author_name(entry),
        "selftext": _entry_content_text(entry),
        "over_18": False,
        "subreddit": "opendirectories",
        "permalink": link,
        "url": link,
    }


def _parse_feed(raw: bytes) -> list[dict]:
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FetchError("decode error") from e
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise FetchError("malformed RSS/Atom") from e

    entries = root.findall(f"{_ATOM_NS}entry")
    if not entries:
        entries = root.findall("entry")
    return [post for entry in entries if (post := _entry_to_post(entry)) is not None]


def fetch_page(
    sort: str,
    after: Optional[str] = None,
    timeout: int = 20,
    top_window: str = "week",
    max_posts: int = DEFAULT_MAX_POSTS,
) -> PageResult:
    """
    Fetch one RSS snapshot from r/opendirectories.

    ``after`` is accepted for compatibility with the old JSON client but RSS
    does not expose a cursor, so it is ignored and ``next_after`` is always
    ``None``.
    """
    if sort not in {"new", "top"}:
        raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
    if not (1 <= max_posts <= MAX_POSTS):
        raise ValueError(f"max_posts must be 1-{MAX_POSTS}, got {max_posts}")

    params: dict[str, str] = {"limit": str(max_posts)}
    if sort == "top":
        params["t"] = top_window

    url = _BASE_URL.format(sort=sort)
    url = f"{url}?{urllib.parse.urlencode(params)}"

    posts = _parse_feed(_request_feed(url, timeout))
    return PageResult(posts=posts, next_after=None)


def fetch_posts(
    sort: str,
    max_pages: int = 3,
    timeout: int = 20,
    top_window: str = "week",
    max_posts: int = DEFAULT_MAX_POSTS,
) -> FetchResult:
    """
    Fetch one anonymous RSS snapshot from r/opendirectories.

    ``max_pages`` retains the historical 1–3 validation contract, but RSS
    feeds have no JSON ``after`` cursor. The returned ``pages_fetched`` is 1.
    """
    if sort not in {"new", "top"}:
        raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
    if not (1 <= max_pages <= 3):
        raise ValueError(f"max_pages must be 1–3, got {max_pages}")

    page = fetch_page(
        sort=sort,
        after=None,
        timeout=timeout,
        top_window=top_window,
        max_posts=max_posts,
    )
    return FetchResult(posts=page.posts[:max_posts], pages_fetched=1)


def fetch_search_page(
    query: str,
    sort: str,
    after: Optional[str] = None,
    timeout: int = 20,
    top_window: str = "week",
    max_posts: int = DEFAULT_MAX_POSTS,
) -> PageResult:
    """
    Fetch one subreddit-scoped RSS search snapshot from r/opendirectories.

    ``after`` is accepted for compatibility and ignored because RSS has no
    cursor pagination.
    """
    q = str(query or "").strip()
    if not q:
        raise ValueError("query is required")
    if sort not in {"new", "top"}:
        raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
    if not (1 <= max_posts <= MAX_POSTS):
        raise ValueError(f"max_posts must be 1-{MAX_POSTS}, got {max_posts}")

    params: dict[str, str] = {
        "q": q,
        "restrict_sr": "1",
        "sort": sort,
        "limit": str(max_posts),
    }
    if sort == "top":
        params["t"] = top_window
    url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    posts = _parse_feed(_request_feed(url, timeout))
    return PageResult(posts=posts, next_after=None)


def fetch_search_posts(
    query: str,
    sort: str,
    max_pages: int = 3,
    timeout: int = 20,
    top_window: str = "week",
    max_posts: int = DEFAULT_MAX_POSTS,
) -> FetchResult:
    """
    Fetch one anonymous RSS search snapshot from r/opendirectories.

    ``max_pages`` retains the historical 1–3 validation contract, but RSS
    feeds have no JSON ``after`` cursor. The returned ``pages_fetched`` is 1.
    """
    if sort not in {"new", "top"}:
        raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
    if not (1 <= max_pages <= 3):
        raise ValueError(f"max_pages must be 1–3, got {max_pages}")

    page = fetch_search_page(
        query=query,
        sort=sort,
        after=None,
        timeout=timeout,
        top_window=top_window,
        max_posts=max_posts,
    )
    return FetchResult(posts=page.posts[:max_posts], pages_fetched=1)
