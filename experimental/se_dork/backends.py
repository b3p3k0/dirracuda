"""Translate search aggregator URLs and result metadata at the API boundary."""

from urllib.parse import urlencode, urlsplit, urlunsplit


def is_degoog_endpoint(url: str) -> bool:
    return urlsplit(url).path.rstrip("/").endswith("/api/search")


def instance_base(url: str) -> str:
    """Accept a base URL or a supported search endpoint, including subpaths."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Enter an HTTP or HTTPS search instance URL.")
    if parts.query or parts.fragment:
        raise ValueError("Enter the instance URL without query parameters or a fragment.")
    path = parts.path.rstrip("/")
    for suffix in ("/api/search", "/search"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def search_url(url: str, query: str, page: int) -> str:
    base = instance_base(url)
    if is_degoog_endpoint(url):
        return f"{base}/api/search?{urlencode({'q': query, 'type': 'web', 'page': page})}"
    return f"{base}/search?{urlencode({'q': query, 'format': 'json', 'pageno': page})}"


def normalize_results(results: list, *, degoog: bool) -> list:
    """Keep the existing store's content/engine/engines contract for both APIs."""
    if not degoog:
        return results
    normalized = []
    for row in results:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["content"] = row.get("content") or row.get("snippet") or ""
        source = row.get("source") or row.get("engine")
        item["engine"] = source if isinstance(source, str) else ""
        sources = row.get("sources", row.get("engines"))
        item["engines"] = (
            [s for s in sources if isinstance(s, str)]
            if isinstance(sources, list) else ([item["engine"]] if item["engine"] else [])
        )
        normalized.append(item)
    return normalized
