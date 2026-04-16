"""
Reddit JSON endpoint client for the redseek module.

Fetches posts from r/opendirectories using public JSON endpoints.
No API key required. Uses urllib.request (stdlib only; no external deps).

429 contract:
  fetch_page raises RateLimitError on HTTP 429 — no retry, no swallow.
  fetch_posts propagates RateLimitError uncaught; no FetchResult on 429.
  Service layer (Card 3) owns error reporting.

Rate-limit pacing:
  1-second delay between pages (not before the first request).
  Hard cap: max_pages <= 3.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

_BASE_URL = "https://www.reddit.com/r/opendirectories/{sort}.json"
_USER_AGENT = "dirracuda:reddit_ingest:v1.0"


class RateLimitError(Exception):
    """HTTP 429. Propagates to service layer — never silently swallowed."""


class FetchError(Exception):
    """Network failure, decode error, or unexpected response shape."""


@dataclass
class PageResult:
    posts: list          # list[dict] — raw Reddit post data dicts (kind=t3 only)
    next_after: Optional[str]  # pagination cursor; None if this is the last page


@dataclass
class FetchResult:
    posts: list          # list[dict] — all posts across all fetched pages
    pages_fetched: int


def fetch_page(
    sort: str,
    after: Optional[str] = None,
    timeout: int = 20,
) -> PageResult:
    """
    Fetch one page of posts from r/opendirectories.

    sort:    "new" or "top"
    after:   pagination cursor from a prior PageResult.next_after (None = first page)
    timeout: seconds before network timeout

    Raises:
        RateLimitError: HTTP 429 received
        FetchError:     any other HTTP error, network failure, decode error,
                        or unexpected response shape
    """
    params: dict = {}
    if sort == "top":
        params["t"] = "week"
    if after is not None:
        params["after"] = after
        params["count"] = "25"

    url = _BASE_URL.format(sort=sort)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimitError("HTTP 429") from e
        raise FetchError(f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(str(e.reason)) from e

    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FetchError("decode error") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError("malformed JSON") from e

    try:
        children = payload["data"]["children"]
        if not isinstance(children, list):
            raise TypeError
    except (KeyError, TypeError):
        raise FetchError("unexpected response shape")

    posts = [child["data"] for child in children if child.get("kind") == "t3"]
    next_after = payload["data"].get("after")  # JSON null → Python None

    return PageResult(posts=posts, next_after=next_after)


def fetch_posts(
    sort: str,
    max_pages: int = 3,
    timeout: int = 20,
) -> FetchResult:
    """
    Fetch up to max_pages pages of posts from r/opendirectories.

    sort:      "new" or "top"
    max_pages: 1–3 (hard cap enforced)
    timeout:   seconds per page request

    Raises:
        ValueError:      invalid sort or max_pages
        RateLimitError:  HTTP 429 (propagated uncaught from fetch_page)
        FetchError:      network or response errors (propagated uncaught)
    """
    if sort not in {"new", "top"}:
        raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
    if not (1 <= max_pages <= 3):
        raise ValueError(f"max_pages must be 1–3, got {max_pages}")

    all_posts: list = []
    after: Optional[str] = None
    pages = 0

    while pages < max_pages:
        if pages > 0:
            time.sleep(1)
        page = fetch_page(sort, after, timeout)
        all_posts.extend(page.posts)
        pages += 1
        if page.next_after is None:
            break
        after = page.next_after

    return FetchResult(posts=all_posts, pages_fetched=pages)
