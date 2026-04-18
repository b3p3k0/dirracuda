# Plan: Card V3-2 — Search Mode (Subreddit-scoped)

## Context

V3-1 added top-window expansion for feed mode. V3-2 adds keyword search mode scoped to r/opendirectories using `/r/opendirectories/search.json`. Analysts need to triage posts by keyword without leaving the subreddit. Dedupe-based ingest semantics apply (no cursor-stop). Search is the only mode added in this card — User mode (V3-3) is out of scope.

---

## 1. Card Summary

Add `mode=search` to the ingest pipeline end-to-end:
- New client functions for the search endpoint with `restrict_sr=1`
- `IngestOptions.mode` + `IngestOptions.query` fields (backward-compatible defaults)
- `_run_search` worker in service (mirrors `_run_top` dedupe semantics)
- Mode selector + query field in the dialog (Feed | Search; query hidden when mode=feed)
- Tests for all new branches in client, service, and dialog

---

## 2. File Touch List

| File | Change type |
|---|---|
| `experimental/redseek/client.py` | Add `_SEARCH_URL`, `fetch_search_page`, `fetch_search_posts` |
| `experimental/redseek/service.py` | Add `mode`/`query` to `IngestOptions`; mode validation; `_run_search` worker; fetch dispatch |
| `gui/components/reddit_grab_dialog.py` | Add mode selector, query field, `_on_mode_changed`, update `_validate` |
| `experimental/redseek/models.py` | Comment-only: update `source_sort` and `sort_mode` comments to include search values |
| `shared/tests/test_redseek_client.py` | New test block for search client functions |
| `shared/tests/test_redseek_service.py` | New test block for search mode dispatch and worker |
| `gui/tests/test_reddit_grab_dialog.py` | Update `_make_dialog`; add search-mode dialog test block |

`experimental/redseek/models.py` gets comment-only updates (no logic change).

Current line counts (all well under 1200 "excellent" threshold):
- client.py: 153 → ~215 | service.py: 489 → ~570 | dialog: 232 → ~275
- models.py: 38 → 38 (comment edits only)
- test_client: 362 → ~450 | test_service: 724 → ~810 | test_dialog: 124 → ~200

---

## 3. Implementation Steps

### Step 1 — `experimental/redseek/client.py`

Add constant:
```python
_SEARCH_URL = "https://www.reddit.com/r/opendirectories/search.json"
```

Add `fetch_search_page(query, sort, after, timeout, top_window)`:
- Required params: `q=query`, `restrict_sr=1`, `sort=sort`
- Add `t=top_window` only when `sort == "top"`
- Add `after=after`, `count=25` only when `after is not None`
- Same HTTP error / decode / JSON / shape handling as `fetch_page`
- Returns `PageResult`

Add `fetch_search_posts(query, sort, max_pages, timeout, top_window)`:
- Validates: `sort in {"new", "top"}`, `1 <= max_pages <= 3`
- Same pagination loop with inter-page `time.sleep(1)` as `fetch_posts`
- Returns `FetchResult`

No changes to existing `fetch_page` / `fetch_posts`.

---

### Step 2 — `experimental/redseek/service.py`

**Import**: add `fetch_search_posts` to the `from experimental.redseek.client import ...` line.

**`IngestOptions`** — append two fields with defaults (backward-compatible):
```python
mode: str = "feed"   # "feed" | "search"
query: str = ""      # required non-empty when mode="search"
```

**`run_ingest` validation block** — add after existing sort/max_posts/max_pages/subreddit checks:
```python
if options.mode not in {"feed", "search"}:
    return _error_result(options, False, error=f"invalid mode: {options.mode!r}")
if options.mode == "search" and not options.query.strip():
    return _error_result(options, False, error="query is required for search mode")
```

**`run_ingest` fetch phase** — replace single `fetch_posts(...)` call with:
```python
if options.mode == "search":
    fetch_result = fetch_search_posts(
        options.query.strip(), options.sort,
        max_pages=options.max_pages, top_window=options.top_window,
    )
else:
    fetch_result = fetch_posts(
        options.sort, max_pages=options.max_pages, top_window=options.top_window,
    )
```

**`run_ingest` dispatch** — add search branch before existing new/top dispatch:
```python
if options.mode == "search":
    return _run_search(options, fetch_result, db_path, now_str, replace_cache_done)
if options.sort == "new":
    return _run_new(...)
return _run_top(...)
```

**`_run_search` worker** — add before `run_ingest`. Mirrors `_run_top` exactly except:
- State key: `f"search:{options.sort}:{options.top_window if options.sort == 'top' else 'na'}:{' '.join(options.query.split()).lower()}"` — encodes sort + window + normalized query to avoid collisions across different sort/window/query permutations. Example: `search:new:na:ftp files`, `search:top:week:ftp files`. No legacy migration needed.
- `source_sort="search"` on `RedditPost` objects (no schema change — TEXT column, no CHECK constraint)
- `stopped_by_cursor=False` always
- `save_ingest_state` called unconditionally (same as `_run_top`)
- `IngestResult.sort` set to `options.sort` — reports the sort used within the search (e.g. `"new"` or `"top"`). Mode context is implicit; no new IngestResult field added in this card.

---

### Step 3 — `gui/components/reddit_grab_dialog.py`

**`__init__`** — add two vars after existing vars:
```python
self.mode_var = tk.StringVar(value="feed")
self.query_var = tk.StringVar(value="")
```

**`_build_dialog`** — shift existing rows down by 1 to make room for mode at row 0:
- Row 0 (NEW): Mode label + `ttk.Combobox(values=["feed", "search"], width=8)`
- Row 1 (was 0): Sort + Top Window (no change to logic)
- Row 2 (NEW, hidden initially): Query label + `tk.Entry(textvariable=self.query_var, width=30)` — stored as `self._query_lbl` and `self._query_entry`; call `grid_remove()` on both after gridding them
- Row 3 (was 1): Max posts
- Rows 4-6 (were 2-4): Checkboxes

Add trace and initial call after sort trace:
```python
self.mode_var.trace_add("write", self._on_mode_changed)
self._on_mode_changed()
```

**`_on_mode_changed`** — new method:
```python
def _on_mode_changed(self, *_) -> None:
    if self.mode_var.get() == "search":
        self._query_lbl.grid()
        self._query_entry.grid()
    else:
        self._query_lbl.grid_remove()
        self._query_entry.grid_remove()
```

**`_validate`** — prepend mode/query validation before existing sort check:
```python
mode = self.mode_var.get().strip()
query = ""
if mode == "search":
    query = self.query_var.get().strip()
    if not query:
        messagebox.showerror("Invalid input", "Search query cannot be empty.", parent=self.dialog)
        return None
```
Pass `mode=mode, query=query` to the `IngestOptions(...)` constructor call at the end.

---

### Step 3b — `experimental/redseek/models.py` (comment-only)

Update `RedditPost.source_sort` comment:
```python
source_sort: str      # "new", "top", or "search"
```

Update `RedditIngestState.sort_mode` comment:
```python
sort_mode: str        # "new", "top:<window>", or "search:<sort>:<window_or_na>:<query>"
```

No logic changes — two comment lines only.

---

### Step 4 — `shared/tests/test_redseek_client.py`

New test block (`# fetch_search_page / fetch_search_posts`) covering:
- URL contains `restrict_sr=1` always
- URL contains `q=<query>` (URL-encoded)
- URL contains `sort=new` / `sort=top`
- `sort=top` adds `t=<window>`; `sort=new` does not add `t=`
- `after` param included when provided; absent on first page
- HTTP 429 → `RateLimitError`
- `fetch_search_posts` invalid sort → `ValueError`
- `fetch_search_posts` max_pages cap enforced
- `fetch_search_posts` propagates `RateLimitError`

Reuse `_make_payload`, `_mock_resp`, `_http_error` helpers already in the file.

---

### Step 5 — `shared/tests/test_redseek_service.py`

New test block (`# search mode`) covering:
- Unknown mode → `error` result
- Empty query → `error` result
- Whitespace-only query → `error` result
- Happy path: posts ingested, `stopped_by_cursor=False`, `error=None`
- Dedupe: second run same posts → no duplicate rows
- State key saved as `search:<sort>:<window_or_na>:<normalized_query>` — test parametrize sort=new (`na`) and sort=top+window=week
- `RateLimitError` during fetch → `rate_limited=True`
- `fetch_search_posts` is called (not `fetch_posts`) when `mode="search"` — monkeypatch both and assert only `fetch_search_posts` fires
- **Inverse guard**: `fetch_posts` is called (not `fetch_search_posts`) when `mode="feed"` — monkeypatch both and assert only `fetch_posts` fires; catches accidental branch regressions

Use existing `_make_raw_post`, `_make_fetch`, `_make_opts` helpers (add `mode`/`query` params to `_make_opts` with defaults).

---

### Step 6 — `gui/tests/test_reddit_grab_dialog.py`

Update `_make_dialog()` to add:
```python
d.mode_var = MagicMock()
d.mode_var.get.return_value = "feed"
d.query_var = MagicMock()
d.query_var.get.return_value = ""
d._query_lbl = MagicMock()
d._query_entry = MagicMock()
```

New test block (`# search mode — _on_mode_changed and _validate`) covering:
- `mode=search` → `_query_lbl.grid()` and `_query_entry.grid()` called
- `mode=feed` → `_query_lbl.grid_remove()` and `_query_entry.grid_remove()` called
- `_validate` with `mode=search`, empty query → returns `None` AND `showerror` was called — monkeypatch `gui.components.reddit_grab_dialog.messagebox.showerror` (consistent with `test_dashboard_reddit_wiring.py` string-based setattr pattern); assert call list non-empty
- `_validate` with `mode=search`, non-empty query → returns `IngestOptions` with `mode="search"`, `query="<value>"`
- `_validate` with `mode=feed` → returns `IngestOptions` with `mode="feed"`, `query=""`
- Existing tests unchanged (they use `_make_dialog()` which now returns `mode="feed"` by default)

---

## 4. Risks and Assumptions

| Risk | Mitigation |
|---|---|
| `_make_opts` in test_redseek_service.py doesn't accept `mode`/`query` | Add `mode="feed"`, `query=""` kwargs with defaults; no existing call sites change |
| `_make_options()` in test_dashboard_reddit_wiring.py omits `mode`/`query` | `IngestOptions` dataclass defaults cover this; test constructs IngestResult directly anyway |
| `_make_dialog()` in test_reddit_grab_dialog.py missing `mode_var`/`query_var` | Update `_make_dialog()` in that file; existing tests unaffected — `mode="feed"` default hits no new validation branches |
| `grid_remove()` / `grid()` requires knowing original grid params | Store `_query_lbl` / `_query_entry` refs; call `grid()` with original params at creation; `grid_remove()` preserves them internally |
| `source_sort="search"` in DB — downstream browser/explorer code filters on this field | `source_sort` is stored but the V2 browser does not filter on it (display only). No regression expected. |
| Row number shifts in dialog break existing dialog tests | All existing dialog tests use `__new__` + MagicMock and never test row numbers — safe |
| Empty-query `_validate` test calls `messagebox.showerror` in headless CI | Monkeypatch `gui.components.reddit_grab_dialog.messagebox.showerror` before calling `_validate`; assert call list non-empty |
| State key collisions across sort/window permutations for same query | Resolved: key encodes `search:{sort}:{window_or_na}:{normalized_query}` — all permutations produce distinct keys |

**Assumptions:**
- `open_connection` context manager usage: `_run_search` uses the same `conn = open_connection(db_path)` + manual `.close()` in `finally` pattern as `_run_top` (not `with` — consistent with existing workers)
- Query normalization for state key: `' '.join(query.split()).lower()` — collapses internal whitespace and lowercases. Applied consistently in `_run_search` state key construction and in the corresponding test assertions. The raw `options.query` (after `.strip()`) is passed to `fetch_search_posts`; normalization is state-key-only.
- `IngestResult.sort` is `options.sort` (i.e. `"new"` or `"top"`) for search runs — not `"search"` or `f"search:{options.sort}"`. Mode context is implicit.
- Mode selector label shown as `"Mode:"` in dialog; combobox values are lowercase `"feed"` / `"search"` (consistent with `"new"` / `"top"` in sort)

---

## 5. Validation Commands

```bash
# Syntax check
python3 -m py_compile \
  experimental/redseek/client.py \
  experimental/redseek/service.py \
  gui/components/reddit_grab_dialog.py

# Full targeted test suite
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_reddit_grab_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -v
```

---

## 6. PASS / FAIL Gates

| Gate | PASS condition |
|---|---|
| `py_compile` | Zero syntax errors across all three touched source files |
| `test_redseek_client.py` | All existing tests green; all new `fetch_search_page`/`fetch_search_posts` tests green |
| `test_redseek_service.py` | All existing tests green; all new search-mode tests green |
| `test_reddit_grab_dialog.py` | All existing tests green; new search-mode dialog tests green |
| `test_dashboard_reddit_wiring.py` | All existing tests green (no regressions) |
| `models.py` changes are comment-only | `py_compile` + grep confirms no logic diff |
| No new file exceeds 1200 lines | Confirmed by line count after edits |

HI test (manual):
1. Open Reddit Grab → verify Mode selector shows `feed` / `search`; query field hidden in feed mode.
2. Switch to search mode → query field appears.
3. Click Run Grab with empty query → error dialog, no ingest fired.
4. Enter a query (e.g. `"ftp"`) + sort=new → confirm clean completion + rows in `reddit_od.db`.
5. Re-run same query → `targets_deduped > 0`, no duplicate rows in DB.
