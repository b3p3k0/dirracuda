# Card 2: Reddit Client + Parser — Implementation Plan

## Context

Card 1 delivered the sidecar DB layer (`redseek/models.py`, `redseek/store.py`) with all three tables, schema guard, CRUD, and wipe. Card 2 adds the network fetch layer (`client.py`) and the target extraction/normalization layer (`parser.py`). These are pure data-pipeline components — no GUI, no service orchestration (that is Card 3). Card 2 must be exercisable standalone via unit tests with no real network calls.

---

## 1. Constraint Summary

- Use `urllib.request` only — no `requests` dependency. Project pattern: `shared/http_browser.py:20`, `commands/http/verifier.py:13`.
- 429 contract (locked): `fetch_page` raises `RateLimitError`; `fetch_posts` propagates it uncaught; service (Card 3) owns reporting and partial-result handling.
- 1-second minimum delay between paginated requests (sleep inside `fetch_posts` loop, not in `fetch_page`).
- Max 3 pages per run enforced in the client as a hard cap.
- `top` mode appends `?t=week` (default top window = week, per task card context).
- Parser output is deterministic: same input always produces same `target_normalized` and `dedupe_key`.
- Dedupe key: `sha1(post_id + "|" + target_normalized)` — defined in `01-ARCHITECTURE.md`.
- Do not touch `redseek/models.py`, `redseek/store.py`, or any scan/GUI/CLI production file. Test files under `shared/tests/` are allowed additions.
- All Reddit data is untrusted — parser must sanitize; never eval, never execute.
- `[deleted]` and `[removed]` selftext must be treated as empty (skip body extraction).

---

## 2. File Touch List

| Action | File |
|--------|------|
| NEW | `redseek/client.py` |
| NEW | `redseek/parser.py` |
| NEW | `shared/tests/test_redseek_client.py` |
| NEW | `shared/tests/test_redseek_parser.py` |
| NO TOUCH | `redseek/models.py`, `redseek/store.py`, `redseek/__init__.py` |
| NO TOUCH | Any shared/commands/gui/cli **production** file |

---

## 3. Step-by-Step Implementation Plan

### Step 1 — Define client-internal types in `client.py`

Two private dataclasses (client-internal only; not exported to `models.py`, not stored):

```python
@dataclass
class PageResult:
    posts: list[dict]       # raw Reddit post data dicts
    next_after: str | None  # pagination cursor; None if last page

@dataclass
class FetchResult:
    posts: list[dict]       # all posts across all fetched pages
    pages_fetched: int
```

Two exceptions:

```python
class RateLimitError(Exception):
    """Raised on HTTP 429. Propagates to service layer — never silently swallowed."""

class FetchError(Exception):
    """All other network, decode, or shape errors."""
```

`FetchResult` has no `rate_limited` field — 429 always raises, never returns a partial success object.

### Step 2 — Implement `fetch_page()` in `client.py`

Signature:

```python
def fetch_page(
    sort: str,              # "new" or "top"
    after: str | None = None,
    timeout: int = 20,
) -> PageResult:
```

Logic:
1. Build base URL: `https://www.reddit.com/r/opendirectories/{sort}.json`.
2. Build query params with `urllib.parse.urlencode`:
   - `top` sort: always include `t=week`; if `after` is not None also include `after` and `count=25`.
   - `new` sort: if `after` is not None include `after` and `count=25`; no `t` param.
3. Set `User-Agent: dirracuda:reddit_ingest:v1.0` header.
4. Open with `urllib.request.urlopen(req, timeout=timeout)`.
5. On `urllib.error.HTTPError` with code 429 → raise `RateLimitError("HTTP 429")`.
6. On other `urllib.error.HTTPError` → raise `FetchError(f"HTTP {e.code}")`.
7. On `urllib.error.URLError` (network, DNS, timeout) → raise `FetchError(str(e.reason))`.
8. Decode body bytes as UTF-8; on `UnicodeDecodeError` → raise `FetchError("decode error")`. Parse JSON; on `json.JSONDecodeError` → raise `FetchError("malformed JSON")`.
9. Validate top-level shape: payload must have keys `["data"]["children"]` (a list) and `["data"]["after"]`. If shape is wrong → raise `FetchError("unexpected response shape")`.
10. Extract post dicts: `[child["data"] for child in children if child.get("kind") == "t3"]`.
11. Return `PageResult(posts=posts, next_after=payload["data"]["after"])`. (`after` may be `None` when Reddit returns JSON null — check with `is None`.)

### Step 3 — Implement `fetch_posts()` in `client.py`

Signature:

```python
def fetch_posts(
    sort: str,
    max_pages: int = 3,
    timeout: int = 20,
) -> FetchResult:
```

Logic:
1. Validate inputs with explicit checks:
   ```python
   if sort not in {"new", "top"}:
       raise ValueError(f"sort must be 'new' or 'top', got {sort!r}")
   if not (1 <= max_pages <= 3):
       raise ValueError(f"max_pages must be 1–3, got {max_pages}")
   ```
   `assert` is not used — asserts are disabled under `-O` and must not be relied on for runtime contract enforcement.
2. Initialize `all_posts: list[dict] = []`, `after: str | None = None`, `pages = 0`.
3. Loop up to `max_pages`:
   - If `pages > 0`: `time.sleep(1)` (pacing between pages; not before the first request).
   - Call `fetch_page(sort, after, timeout)`. Both `RateLimitError` and `FetchError` propagate uncaught — service owns handling.
   - Extend `all_posts` with `page.posts`; increment `pages`.
   - If `page.next_after is None`: break (end of feed).
   - `after = page.next_after`.
4. Return `FetchResult(posts=all_posts, pages_fetched=pages)`.

No 429 catching here. The propagation is intentional.

### Step 4 — Implement `redseek/parser.py` — module-level regex constants

All four patterns compiled once at module level:

```python
import re

# Priority 1: full URLs with explicit scheme
_RE_URL = re.compile(
    r'(?:https?|ftp)://[^\s<>"\')\]\}]+'
)

# Priority 2: host:port — IPv4 or hostname label
_RE_HOST_PORT = re.compile(
    r'\b'
    r'(?:\d{1,3}(?:\.\d{1,3}){3}'           # IPv4
    r'|(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63})'  # hostname
    r':(\d{1,5})'
    r'\b'
)

# Priority 3: raw IPv4 (fallback, no port)
_RE_IPV4 = re.compile(
    r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b'
)

# Priority 4: bare domain (strict — alpha-only TLD, 2–63 chars, must have a dot)
# Uses negative lookbehind to skip email domains (user@example.com).
_RE_BARE_DOMAIN = re.compile(
    r'(?<![a-zA-Z0-9@])(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\b'
)
```

Priority enforcement: scan in order 1 → 4, track matched char spans, skip any match whose span overlaps an already-claimed span.

### Step 5 — Implement pre-extraction cleanup in `parser.py`

```python
import html
import re

# Markdown link pattern — extracts URL from [text](url) for http, https, ftp
_RE_MD_LINK = re.compile(
    r'\[([^\]]*)\]\(((?:https?|ftp)://[^)]+)\)'
)

# Inline backtick removal
_RE_BACKTICK = re.compile(r'`([^`]+)`')

def _clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = _RE_MD_LINK.sub(r'\2', text)   # keep URL, drop [label]
    text = _RE_BACKTICK.sub(r'\1', text)  # strip backticks
    return text
```

```python
_STRIP_TRAILING = frozenset('.,)]\>\'"')
_STRIP_LEADING  = frozenset('([<')

def _cleanup_candidate(s: str) -> str:
    s = s.rstrip(''.join(_STRIP_TRAILING))
    s = s.lstrip(''.join(_STRIP_LEADING))
    return s
```

### Step 6 — Implement normalization in `parser.py`

```python
def _normalize(candidate: str, kind: str) -> str | None:
    """
    kind: "url" | "host_port" | "ipv4" | "bare_domain"
    Returns normalized string or None if validation fails.
    """
```

Rules by kind:
- `url`: lowercase scheme + host via `urllib.parse.urlparse`; strip auth from netloc (`user:pass@host` → `host`); strip trailing `/` only if path is exactly `/`; reject if resulting host is empty; reject if len > 2048.
- `host_port`: split on last `:`, lowercase host, validate port `1 ≤ p ≤ 65535` (reject if out of range); if host is IPv4, validate each octet `0–255`.
- `ipv4`: validate each of 4 octets `0–255`; reject if any fails.
- `bare_domain`: verify TLD is all-alpha (rejects `v1.2`, `1.0.0`); verify at least one non-TLD label exists; lowercase; reject if any label is all-digit (prevents IP fragments). Email-domain matches are suppressed at the regex level via `(?<![a-zA-Z0-9@])` lookbehind — no additional post-filter needed.

### Step 7 — Implement classification in `parser.py`

```python
def _classify(normalized: str, kind: str) -> tuple[str, str, str]:
    """Returns (protocol, host, confidence)."""
```

| kind | protocol | confidence |
|------|----------|------------|
| `url` starts `https://` | `https` | `high` |
| `url` starts `http://` | `http` | `high` |
| `url` starts `ftp://` | `ftp` | `high` |
| `host_port` port=80 | `http` | `medium` |
| `host_port` port=443 | `https` | `medium` |
| `host_port` port=21 | `ftp` | `medium` |
| `host_port` other port | `unknown` | `medium` |
| `bare_domain` | `unknown` | `medium` |
| `ipv4` | `unknown` | `low` |

Host extraction:
- `url`: `urllib.parse.urlparse(normalized).hostname`
- `host_port`: part before `:`
- `bare_domain`: the normalized string itself
- `ipv4`: the normalized string itself

### Step 8 — Implement dedupe key in `parser.py`

```python
import hashlib

def make_dedupe_key(post_id: str, target_normalized: str) -> str:
    return hashlib.sha1(f"{post_id}|{target_normalized}".encode()).hexdigest()
```

### Step 9 — Implement `extract_targets()` in `parser.py`

Signature:

```python
def extract_targets(
    post_id: str,
    title: str,
    selftext: str | None,
    parse_body: bool,
    created_at: str,     # UTC datetime string ("YYYY-MM-DD HH:MM:SS")
) -> list[RedditTarget]:
```

Logic:
1. Build source text: always include `title`. If `parse_body` and selftext is not None and `selftext.strip() not in {"", "[deleted]", "[removed]"}`: join with `"\n"`.
2. If `len(source) > 102_400` (100KB): truncate to 102_400 chars; set a flag `truncated = True`.
3. Run `_clean_text(source)`.
4. Run regexes in priority order (`_RE_URL`, `_RE_HOST_PORT`, `_RE_IPV4`, `_RE_BARE_DOMAIN`) via `re.finditer`; track claimed spans.
5. For each match: `_cleanup_candidate()` → `_normalize(candidate, kind)` → skip if `None`.
6. For each valid normalized: `_classify()` → `make_dedupe_key()` → construct `RedditTarget`. Set `notes="truncated"` if `truncated` flag is set.
7. Deduplicate list by `dedupe_key` in-order (keep first occurrence); use a `dict` keyed by `dedupe_key`.
8. Return `list(seen.values())`.

---

## 4. Regex/Parsing Contract (exact literals)

### Extraction patterns

```python
_RE_URL = re.compile(
    r'(?:https?|ftp)://[^\s<>"\')\]\}]+'
)

_RE_HOST_PORT = re.compile(
    r'\b'
    r'(?:\d{1,3}(?:\.\d{1,3}){3}'
    r'|(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63})'
    r':(\d{1,5})'
    r'\b'
)

_RE_IPV4 = re.compile(
    r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b'
)

# Negative lookbehind on [@a-zA-Z0-9] prevents matching email domains (user@example.com)
# and avoids extending a prior hostname match. TLD capped at 63 (DNS label max).
_RE_BARE_DOMAIN = re.compile(
    r'(?<![a-zA-Z0-9@])(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\b'
)
```

### Cleanup patterns

```python
_RE_MD_LINK = re.compile(
    r'\[([^\]]*)\]\(((?:https?|ftp)://[^)]+)\)'
)

_RE_BACKTICK = re.compile(r'`([^`]+)`')
```

### Normalization validation rules (exact)

- URL: `len(normalized) > 2048` → drop. Host empty after parse → drop. Auth stripped silently.
- host:port port: `not (1 <= port <= 65535)` → drop.
- IPv4 octet: `not (0 <= octet <= 255)` → drop.
- Bare domain TLD: `not tld.isalpha()` → drop. Any label `label.isdigit()` → drop.

### Confidence table (exact)

```python
_CONFIDENCE = {
    "url":         "high",
    "host_port":   "medium",
    "bare_domain": "medium",
    "ipv4":        "low",
}
```

---

## 5. Error/Timeout/429 Handling Behavior

| Condition | Layer | Behavior |
|-----------|-------|----------|
| HTTP 429 | `fetch_page` | Raise `RateLimitError("HTTP 429")` |
| HTTP 429 | `fetch_posts` | Propagates uncaught — no `FetchResult` returned |
| HTTP 4xx/5xx ≠ 429 | `fetch_page` | Raise `FetchError(f"HTTP {e.code}")` |
| Network/DNS/timeout | `fetch_page` | Raise `FetchError(str(e.reason))` |
| Malformed JSON | `fetch_page` | Raise `FetchError("malformed JSON")` |
| Missing `data.children` | `fetch_page` | Raise `FetchError("unexpected response shape")` |
| `next_after is None` | `fetch_posts` | Stop pagination cleanly, return `FetchResult` |
| Source text > 100KB | `extract_targets` | Truncate, set `notes="truncated"` on all targets from that call |
| Selftext `[deleted]`/`[removed]` | `extract_targets` | Skip body, parse title only |

**429 contract (final):** `fetch_page` raises `RateLimitError`; `fetch_posts` does not catch it; `FetchResult` is never returned on 429. The service layer (Card 3) catches `RateLimitError` and surfaces the error to the GUI as a run failure. The service owns error reporting, not partial-result handling — there are no partial results on 429.

---

## 6. Targeted Validation Commands + Expected Outcomes

### Syntax check

```bash
./venv/bin/python -m py_compile redseek/client.py redseek/parser.py
```
Expected: no output.

### Import check

```bash
./venv/bin/python -c "
from redseek.client import fetch_page, fetch_posts, RateLimitError, FetchError
from redseek.parser import extract_targets, make_dedupe_key
print('OK')
"
```
Expected: `OK`

### Parser unit tests

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_parser.py -v
```
Expected: all PASS. Required test cases:
- Full URL extraction: `http://`, `https://`, `ftp://`
- host:port: numeric IP, hostname
- Raw IPv4 (no port)
- Bare domain extraction
- Markdown `[text](url)` link stripping — http, https, and ftp schemes
- HTML entity unescaping (`&amp;`, `&lt;`)
- Trailing/leading punctuation cleanup
- Duplicate suppression within one post (same `dedupe_key` → one row)
- `[deleted]` and `[removed]` selftext → no body targets extracted
- `parse_body=False` → selftext ignored even if valid
- Same input → same dedupe key across calls (stability)
- Port 0 and port 65536 dropped
- IPv4 octet 256 dropped
- Bare domain with digit-only TLD (`example.123`) dropped
- Email address (`user@example.com`) does not produce `example.com` as a bare-domain target
- URL over 2048 chars dropped
- Source over 100KB → `notes="truncated"` on returned targets

### Client unit tests

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_client.py -v
```
Expected: all PASS. All tests mock `urllib.request.urlopen`. Required test cases:
- 429 → `fetch_page` raises `RateLimitError`
- 429 → `fetch_posts` propagates `RateLimitError` (does not swallow, no `FetchResult` returned)
- Invalid `sort` value → `fetch_posts` raises `ValueError`
- `max_pages=0` and `max_pages=4` → `fetch_posts` raises `ValueError`
- Non-429 HTTP error → `FetchError`
- `URLError` (network) → `FetchError`
- Malformed JSON → `FetchError`
- Missing `data.children` key → `FetchError`
- Valid single page → correct `PageResult.posts` and `next_after`
- `max_pages=2` → at most 2 calls to `urlopen`
- Sleep called once between pages (not before first page)
- `sort="top"` → URL contains `t=week`
- `sort="new"` → URL does not contain `t=week`
- `next_after=None` in response → pagination stops without reaching `max_pages`

### Regression — Card 1 store tests

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_store.py -v
```
Expected: all PASS.

### Regression — full shared test suite

```bash
./venv/bin/python -m pytest shared/tests/ -q
```
Expected: no new failures.

---

## 7. Risks, Blockers, Bad Assumptions, Shortcuts to Avoid

**Risks:**
- `_RE_HOST_PORT` and `_RE_BARE_DOMAIN` will still match some version strings (`v2.1.0`, `2.14.3`). The bare-domain TLD alpha-check and digit-label check mitigate most cases; some false positives remain — confidence `medium` and `low` signal this to the analyst.
- Reddit endpoint shape may drift — all shape validation lives in `fetch_page`; it is the single update point.
- `urllib.request` timeout covers both connect and read; a slow server consumes the full window. Acceptable for MVP.

**Bad assumptions to avoid:**
- Do NOT treat `data["data"]["after"]` as always a string — Reddit returns JSON `null` at end of feed; check `is None`.
- Do NOT assume `selftext` is always present — use `.get("selftext", "")`.
- Do NOT reconstruct the full URL via `urllib.parse.urlunparse` blindly — normalize only scheme+netloc; leave path intact.
- Do NOT dedupe by `target_raw` — two different raw forms can normalize identically. Dedupe key is always `sha1(post_id + "|" + target_normalized)`.
- Do NOT store cleaned auth in `target_raw` — keep original scraped value as-is; auth stripping is normalization only.

**Shortcuts to avoid:**
- Do not use `re.findall` — need `re.finditer` for span tracking across priority levels.
- Do not mock `time.sleep` globally — patch `redseek.client.time.sleep` specifically to avoid polluting other tests.
- Do not put `FetchResult`, `PageResult`, `RateLimitError`, or `FetchError` in `models.py` — they are client-internal and not persisted.
- Do not add `rate_limited` to `FetchResult` — the locked contract is raise-and-propagate, not partial-result.

---

## 8. Open Questions

None blocking.
