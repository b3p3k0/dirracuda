# Card 3: Ingestion Service (`new` + `top`) — Plan (rev 2)

## Context

Cards 1 and 2 delivered `redseek/{models,store,client,parser}.py` with all primitives validated. Card 3
wires those primitives into a complete orchestration layer (`redseek/service.py`) that:
- fetches raw posts from the Reddit JSON feed
- applies NSFW + max_posts filtering
- writes posts + targets to the sidecar DB via the caller-owned-transaction pattern from `store.py`
- updates the ingest cursor (new mode) or scrape timestamp (top mode)
- returns a structured `IngestResult` suitable for GUI display (Card 4)

No commits. Sidecar-only. GUI-only MVP.

---

## 1. Constraint Summary

1. Sidecar DB only (`~/.dirracuda/reddit_od.db`); zero writes to main `dirracuda.db` schema.
2. `RateLimitError` on 429 must propagate out of `run_ingest` as a clean error result — no partial success object, no silent swallow.
3. `new` cursor is `(created_utc, post_id)` tuple; comparison is `<=` stop rule; cursor advances past NSFW-filtered posts (so skipped posts don't re-appear next run).
4. `top` mode has no early-stop; it uses `upsert_post` / `INSERT OR IGNORE` targets for dedupe; only `last_scrape_time` is the meaningful cursor field.
5. `replace_cache=True` performs a full wipe **before** any fetch; uses existing `wipe_all()` which has its own transaction.
6. Fetch happens **before** opening the main write transaction; DB errors are separate from network errors.
7. Per-post parse anomalies (missing `id` or `created_utc`) are counted and skipped; they do **not** abort the run and do **not** count toward `max_posts`.
8. `max_posts` counts posts **visited that passed meta extraction** (before NSFW filter, after anomaly discard). Parse-anomaly discards do not count toward the cap; NSFW-filtered posts do.
9. Cursor is only advanced if at least one valid new post was visited; no cursor write on empty runs.
10. All new code in `redseek/service.py` and `shared/tests/test_redseek_service.py`; no other files touched.
11. `options.subreddit` is locked to `"opendirectories"` for Card 3. `run_ingest` validates this and returns an error result if mismatched (client URL is hardcoded; subreddit option is not plumbed through).
12. **MVP limitation:** cursor stop is applied during post-processing, not during pagination. All configured pages are fetched before any cursor check; the stop fires as posts are iterated in the write phase. This is acceptable at this scale (≤3 pages × 25 posts). Document; do not work around it in Card 3.
13. `open_connection()` raises `RuntimeError` (schema drift) and `FileNotFoundError` (DB missing), not `sqlite3.Error`. These must be caught explicitly in `_run_new` / `_run_top` and returned as `IngestResult.error`.

---

## 2. File Touch List

| File | Action |
|------|--------|
| `redseek/service.py` | **Create** — orchestration, `IngestOptions`, `IngestResult`, `run_ingest` |
| `shared/tests/test_redseek_service.py` | **Create** — unit tests (all mocked; no network, no real DB path) |

No other files modified.

---

## 3. Step-by-Step Implementation Plan

### Step 1 — Define `IngestOptions` and `IngestResult` in `service.py`

```python
@dataclass
class IngestOptions:
    sort: str           # "new" | "top"
    max_posts: int      # hard loop bound on valid posts visited (1–200)
    parse_body: bool
    include_nsfw: bool
    replace_cache: bool
    max_pages: int = 3               # 1–3, passed to fetch_posts
    subreddit: str = "opendirectories"   # locked to this value in Card 3

@dataclass
class IngestResult:
    sort: str
    subreddit: str
    pages_fetched: int
    posts_stored: int
    posts_skipped: int       # NSFW-filtered or parse-anomaly discards
    targets_stored: int
    targets_deduped: int     # INSERT OR IGNORE skips
    parse_errors: int        # posts stored with had_targets=0 due to parser exception
    stopped_by_cursor: bool  # new mode: early stop fired
    stopped_by_max_posts: bool
    replace_cache_done: bool
    rate_limited: bool
    error: Optional[str]     # None on success
```

Both live in `service.py` (not `models.py` — they are service-layer constructs, only exported to GUI).

### Step 2 — `_error_result(options, replace_cache_done, **kwargs) -> IngestResult` helper

Constructs a zero-count `IngestResult` with all numeric fields at 0 and all boolean stop-flags
`False`. Callers pass only the fields that differ (e.g., `error=`, `rate_limited=True`). Keeps
all early-return paths consistent — avoids drift if `IngestResult` fields are added later.

```python
def _error_result(options: IngestOptions, replace_cache_done: bool, **kwargs) -> IngestResult:
    return IngestResult(
        sort=options.sort,
        subreddit=options.subreddit,
        pages_fetched=0,
        posts_stored=0,
        posts_skipped=0,
        targets_stored=0,
        targets_deduped=0,
        parse_errors=0,
        stopped_by_cursor=False,
        stopped_by_max_posts=False,
        replace_cache_done=replace_cache_done,
        rate_limited=False,
        error=None,
        **kwargs,
    )
```

All early-return paths in `run_ingest`, `_run_new`, and `_run_top` use `_error_result(...)`.

### Step 3 — `_extract_post_meta(raw: dict) -> Optional[dict]` helper

Converts a raw Reddit dict to typed fields. Returns `None` for posts missing `id` or `created_utc`
(these cannot be processed or contribute to the cursor). Safe defaults for all other fields:
- `post_author`: `raw.get("author")` — may be `None`
- `is_nsfw`: `int(bool(raw.get("over_18", False)))`
- `selftext`: `raw.get("selftext")` — may be `None`
- `post_title`: `raw.get("title", "")` — empty string if absent

### Step 3 — `run_ingest(options, db_path=None) -> IngestResult`

Top-level entry point:

```
1. Validate options:
   - sort in {"new", "top"}; else return IngestResult(error="invalid sort")
   - 1 <= max_posts <= 200; else return IngestResult(error="invalid max_posts")
   - 1 <= max_pages <= 3; else return IngestResult(error="invalid max_pages")
   - subreddit == "opendirectories"; else return IngestResult(error="unsupported subreddit ...")
     (client URL is hardcoded; subreddit is locked for Card 3)
2. replace_cache_done = False
   try:
       if replace_cache: wipe_all(db_path); replace_cache_done = True
       init_db(db_path)  # idempotent; ensures DB exists before open_connection
   except (sqlite3.Error, OSError) as e:
       return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))
3. now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
4. try:
       fetch_result = fetch_posts(options.sort, options.max_pages)
   except RateLimitError:
       return IngestResult(..., replace_cache_done=replace_cache_done, rate_limited=True, error="HTTP 429")
   except FetchError as e:
       return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))
5. dispatch: _run_new(..., replace_cache_done) or _run_top(..., replace_cache_done)
6. return IngestResult
```

`replace_cache_done` is computed in `run_ingest` and passed into `_run_new`/`_run_top`, which
forward it into the returned `IngestResult`. This ensures all exit paths (early network error,
successful run) report the correct value.

Fetch before connection open; DB errors and network errors use separate catch blocks.

### Step 4 — `_run_new(options, fetch_result, db_path, now_str, replace_cache_done) -> IngestResult`

```
try:
    conn = open_connection(db_path)
except (RuntimeError, FileNotFoundError, sqlite3.Error) as e:
    return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))

try:
    state = get_ingest_state(conn, options.subreddit, "new")
    cursor_candidate = None   # (created_utc, post_id) of newest valid post visited
    posts_visited = 0         # counts valid-meta posts only (NSFW and written both count)
    stopped_by_cursor = False
    stopped_by_max_posts = False
    posts_stored = posts_skipped = targets_stored = targets_deduped = parse_errors = 0

    for raw in fetch_result.posts:
        meta = _extract_post_meta(raw)
        if meta is None:
            posts_skipped += 1
            continue  # anomaly: does NOT count toward max_posts

        post_utc, post_id = meta["created_utc"], meta["id"]

        # Hard loop bound — checked first; counts valid-meta posts
        if posts_visited >= options.max_posts:
            stopped_by_max_posts = True
            break

        # Cursor stop — only when both cursor fields are non-None; checked after max_posts
        if (state
                and state.last_post_created_utc is not None
                and state.last_post_id is not None
                and (post_utc, post_id) <= (state.last_post_created_utc, state.last_post_id)):
            stopped_by_cursor = True
            break

        posts_visited += 1

        # Always advance cursor candidate (NSFW-filtered posts still advance it)
        if cursor_candidate is None or (post_utc, post_id) > cursor_candidate:
            cursor_candidate = (post_utc, post_id)

        # NSFW filter
        if not options.include_nsfw and meta["is_nsfw"]:
            posts_skipped += 1
            continue

        # Parse targets — on exception, treat as zero targets (store post, flag error)
        try:
            targets = extract_targets(
                post_id, meta["title"], meta["selftext"], options.parse_body, now_str
            )
        except Exception:
            targets = []
            parse_errors += 1

        post_obj = RedditPost(
            post_id=post_id,
            post_title=meta["title"],
            post_author=meta["author"],
            post_created_utc=post_utc,
            is_nsfw=meta["is_nsfw"],
            had_targets=1 if targets else 0,
            source_sort="new",
            last_seen_at=now_str,
        )
        upsert_post(conn, post_obj)
        before = conn.total_changes
        upsert_targets(conn, targets)
        actually_inserted = conn.total_changes - before
        targets_stored += actually_inserted
        targets_deduped += len(targets) - actually_inserted
        posts_stored += 1

    # Save cursor only if we visited at least one valid new post
    if cursor_candidate:
        save_ingest_state(conn, RedditIngestState(
            subreddit=options.subreddit,
            sort_mode="new",
            last_post_created_utc=cursor_candidate[0],
            last_post_id=cursor_candidate[1],
            last_scrape_time=now_str,
        ))

    conn.commit()
    return IngestResult(
        sort="new", subreddit=options.subreddit,
        pages_fetched=fetch_result.pages_fetched,
        posts_stored=posts_stored, posts_skipped=posts_skipped,
        targets_stored=targets_stored, targets_deduped=targets_deduped,
        parse_errors=parse_errors,
        stopped_by_cursor=stopped_by_cursor, stopped_by_max_posts=stopped_by_max_posts,
        replace_cache_done=replace_cache_done, rate_limited=False, error=None,
    )
except sqlite3.Error as e:
    conn.rollback()
    return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))
finally:
    conn.close()
```

### Step 5 — `_run_top(options, fetch_result, db_path, now_str, replace_cache_done) -> IngestResult`

```
try:
    conn = open_connection(db_path)
except (RuntimeError, FileNotFoundError, sqlite3.Error) as e:
    return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))

try:
    posts_visited = 0
    stopped_by_max_posts = False
    posts_stored = posts_skipped = targets_stored = targets_deduped = parse_errors = 0

    for raw in fetch_result.posts:
        meta = _extract_post_meta(raw)
        if meta is None:
            posts_skipped += 1
            continue  # anomaly: does NOT count toward max_posts

        # Hard loop bound — checked first; counts valid-meta posts
        if posts_visited >= options.max_posts:
            stopped_by_max_posts = True
            break

        posts_visited += 1

        if not options.include_nsfw and meta["is_nsfw"]:
            posts_skipped += 1
            continue

        # Parse targets — on exception, treat as zero targets (store post, flag error)
        try:
            targets = extract_targets(
                meta["id"], meta["title"], meta["selftext"], options.parse_body, now_str
            )
        except Exception:
            targets = []
            parse_errors += 1

        post_obj = RedditPost(
            post_id=meta["id"], post_title=meta["title"],
            post_author=meta["author"], post_created_utc=meta["created_utc"],
            is_nsfw=meta["is_nsfw"], had_targets=1 if targets else 0,
            source_sort="top", last_seen_at=now_str,
        )
        upsert_post(conn, post_obj)
        before = conn.total_changes
        upsert_targets(conn, targets)
        actually_inserted = conn.total_changes - before
        targets_stored += actually_inserted
        targets_deduped += len(targets) - actually_inserted
        posts_stored += 1

    # top mode: persist scrape time; cursor fields left None (not relied on for stop)
    save_ingest_state(conn, RedditIngestState(
        subreddit=options.subreddit,
        sort_mode="top",
        last_post_created_utc=None,
        last_post_id=None,
        last_scrape_time=now_str,
    ))

    conn.commit()
    return IngestResult(
        sort="top", subreddit=options.subreddit,
        pages_fetched=fetch_result.pages_fetched,
        posts_stored=posts_stored, posts_skipped=posts_skipped,
        targets_stored=targets_stored, targets_deduped=targets_deduped,
        parse_errors=parse_errors,
        stopped_by_cursor=False, stopped_by_max_posts=stopped_by_max_posts,
        replace_cache_done=replace_cache_done, rate_limited=False, error=None,
    )
except sqlite3.Error as e:
    conn.rollback()
    return IngestResult(..., replace_cache_done=replace_cache_done, error=str(e))
finally:
    conn.close()
```

---

## 4. Ingest-State Algorithm

### `new` mode cursor semantics

**Cursor fields used:** `(last_post_created_utc: float, last_post_id: str)`

**Exact per-post order (locked — Step 4 pseudocode is authoritative):**
1. anomaly discard (`_extract_post_meta` returns `None`) → `posts_skipped++`, `continue`; does not count toward `max_posts`
2. `max_posts` check → if `posts_visited >= max_posts`: `stopped_by_max_posts=True`, `break`
3. cursor stop check → if state and both fields non-None and tuple ≤ cursor: `stopped_by_cursor=True`, `break`
4. `posts_visited += 1`; `cursor_candidate` update
5. NSFW filter → `posts_skipped++`, `continue` (cursor already advanced)
6. parse targets (try/except); on exception: `targets=[]`, `parse_errors += 1`
7. write post + targets; update counts

**Cursor stop guard:**
```python
if (state
        and state.last_post_created_utc is not None
        and state.last_post_id is not None
        and (post_utc, post_id) <= (state.last_post_created_utc, state.last_post_id)):
    stopped_by_cursor = True
    break
```

The `is not None` guard prevents a `TypeError` when cursor fields are absent (legacy row, manual
DB edit, or top-mode state row written with `None` fields that was later re-used as new-mode state).

**Ordering rationale:** `max_posts` is checked before cursor stop so that the user-set budget cap
is the hard authority. If both would fire on the same post, `stopped_by_max_posts=True` is the
reported reason. Cursor stop fires after, ensuring it only fires when max_posts has not already
terminated the loop.

**Tuple comparison semantics:**
- `post_utc < cursor_utc` → definitely old → stop
- `post_utc == cursor_utc` and `post_id <= cursor_post_id` → old or exact match → stop
  - `post_id` is a Reddit base36 ID string; lexicographic ordering is deterministic (not chronological within the same second, but determinism is all that's required here)
  - Two posts cannot share both `created_utc` and `post_id` (Reddit IDs are unique)
- `post_utc > cursor_utc` → new → process

**Cursor candidate tracking:**
- Updated at step 4 (after max_posts + cursor-stop checks, before NSFW and parse)
- NSFW-filtered posts DO advance the cursor candidate (prevents stall on NSFW-heavy windows)
- Parse-anomaly discards (`meta is None`) do NOT advance the cursor candidate (no valid tuple)
- Parser-exception posts DO advance the cursor candidate — the exception is caught at step 6, after cursor_candidate was already updated at step 4; the post is still stored with `had_targets=0`

**Cursor write:** Atomic with the DB writes in the same `conn.commit()`. Committed only if `cursor_candidate is not None`.

**First run (state is None):** stop rule never fires; processes all fetched posts up to `max_posts`.

### `top` mode cursor semantics

**Cursor fields used:** `last_scrape_time` only. `last_post_created_utc` and `last_post_id` are written as `None` (schema permits it; makes it explicit that top state carries no cursor).

**Stop rule:** None. `top` is score-based; no chronological ordering assumption.

**Dedupe:** `upsert_post` ON CONFLICT updates mutable fields only (is_nsfw, had_targets, last_seen_at). `upsert_targets` INSERT OR IGNORE skips on `dedupe_key` collision. `conn.total_changes` delta tracks what was actually inserted.

---

## 5. Replace-Cache Behavior and Transaction Boundaries

### Behavior

`replace_cache=True` calls `wipe_all(db_path)` before any fetch or connection open.

`wipe_all` (already in `store.py:318`):
1. Calls `init_db` first (idempotent)
2. Opens its own connection
3. `DELETE FROM reddit_targets` → `DELETE FROM reddit_posts` → `DELETE FROM reddit_ingest_state` (FK-safe order)
4. Commits; closes connection

`run_ingest` then calls `init_db` again (no-op since tables exist) before proceeding.

### Transaction Boundaries

| Phase | Owner | Commits |
|-------|-------|---------|
| Replace cache wipe | `wipe_all` (own connection) | Yes |
| Schema init | `init_db` (own connection) | Yes |
| Fetch posts | No DB involved | N/A |
| `open_connection` + schema guard | `_run_new` / `_run_top` | No (read-only) |
| Post/target writes + cursor update | Service (manual `commit`) | Yes, once at end of loop |
| DB error rollback | Service (`conn.rollback()`) | Rolls back all writes for the run |

**Key property:** all post+target writes and the cursor update commit atomically. On rollback, the
sidecar DB is in its pre-run state (which may be the post-wipe state if `replace_cache=True`).

**replace_cache is destructive by design — even on fetch failure.** If `replace_cache=True` and
the subsequent fetch raises (network error, 429, etc.), the DB remains wiped. This is intentional:
the wipe is committed before any network IO, and `IngestResult.replace_cache_done=True` signals
this. The caller (GUI) should surface this state to the analyst so they understand the DB is empty.
Do not attempt to restore data on fetch failure — that would require snapshotting, which is out of scope.

---

## 6. Failure / Exception Handling Matrix

| Condition | Where raised | Service behavior | IngestResult |
|-----------|-------------|------------------|--------------|
| `RateLimitError` (HTTP 429) | `fetch_posts` | Caught in `run_ingest`; no write transaction; no post-target writes (`init_db` may have run) | `rate_limited=True`, `error="HTTP 429"` |
| `FetchError` (network/decode/shape) | `fetch_posts` | Caught in `run_ingest`; no write transaction; no post-target writes (`init_db` may have run) | `error=str(e)` |
| `ValueError` (bad options passed to `run_ingest`) | `run_ingest` validation | Return early before any IO | `error="invalid options: ..."` |
| Unsupported `subreddit` value | `run_ingest` validation | Return early | `error="unsupported subreddit ..."` |
| Missing `id` or `created_utc` in post dict | `_extract_post_meta` returns `None` | `posts_skipped += 1`; does NOT count toward max_posts; cursor NOT advanced | Reflected in `posts_skipped` |
| `RuntimeError` from `_check_schema` (schema drift) | `open_connection` | Caught by `(RuntimeError, FileNotFoundError, sqlite3.Error)` before inner try | `error=str(e)` |
| `FileNotFoundError` from `open_connection` | `open_connection` | Caught by `(RuntimeError, FileNotFoundError, sqlite3.Error)` before inner try | `error=str(e)` |
| `sqlite3.Error` from `open_connection` (permissions, locked file) | `open_connection` | Caught by `(RuntimeError, FileNotFoundError, sqlite3.Error)` before inner try | `error=str(e)` |
| `sqlite3.Error` during write phase (FK violation, constraint, etc.) | `upsert_post` / `upsert_targets` / `save_ingest_state` | Caught by inner except; `conn.rollback()`; all run writes discarded | `error=str(e)` |
| Cursor field `None` on state row | State from DB | `is not None` guard on both fields; stop rule skipped if incomplete | Run proceeds as if no cursor |
| `Exception` from `extract_targets` (unexpected) | `parser.py` | Per-post try/except; `targets=[]`; `parse_errors += 1`; post stored with `had_targets=0`; cursor already advanced | `parse_errors` count in result |
| NSFW post when `include_nsfw=False` | Service loop | Skip write; cursor still advances; `posts_skipped += 1` | Reflected in `posts_skipped` |
| `max_posts` reached | Service loop | `stopped_by_max_posts=True`; break | `stopped_by_max_posts=True` |

### MVP limitation (documented, not fixed in Card 3)

**Post-fetch-only stop:** `fetch_posts` fetches all `max_pages` pages before the service loops over
posts. The cursor stop fires during post-processing, not during pagination. Consequence: up to
`max_pages × 25` posts are fetched even if the cursor fires on page 1. At current scale (≤75 posts
per run) this is acceptable. Page-by-page orchestration is a future enhancement.

---

## 7. Targeted Validation Commands

### Syntax check
```bash
./venv/bin/python -m py_compile redseek/service.py
```
Expected: exits 0, no output.

### Service unit tests
```bash
./venv/bin/python -m pytest shared/tests/test_redseek_service.py -v
```
Expected: all tests PASS.

### Full redseek suite
```bash
./venv/bin/python -m pytest shared/tests/test_redseek_store.py shared/tests/test_redseek_client.py shared/tests/test_redseek_parser.py shared/tests/test_redseek_service.py -v
```
Expected: all PASS, no regressions.

### Regression
```bash
./venv/bin/python -m pytest -q --ignore=shared/tests/test_redseek_service.py -x
```
Expected: same pass/fail as baseline; no new failures.

### Import sanity
```bash
./venv/bin/python -c "from redseek.service import run_ingest, IngestOptions, IngestResult; print('OK')"
```
Expected: prints `OK`.

---

## 8. Required Tests in `test_redseek_service.py`

All use `tmp_path` for DB path injection. Network calls mocked via `unittest.mock.patch` on
`redseek.service.fetch_posts`.

| Test | What it verifies |
|------|-----------------|
| `test_new_first_run_no_cursor` | Posts written; cursor saved with newest post's values |
| `test_new_cursor_stops_early` | `stopped_by_cursor=True`; only newer posts written |
| `test_new_tie_break_same_utc` | Same `created_utc`, different `post_id`; only `post_id > cursor_post_id` written |
| `test_new_max_posts_cap` | Loop stops after `max_posts` valid-meta posts; `stopped_by_max_posts=True` |
| `test_new_nsfw_filtered_cursor_advances` | NSFW post not written; cursor still advances past it |
| `test_new_nsfw_included` | NSFW post written when `include_nsfw=True` |
| `test_new_cursor_not_written_on_empty_run` | All posts ≤ cursor; `stopped_by_cursor=True`; cursor row unchanged |
| `test_new_cursor_null_fields_not_applied` | State row has `last_post_created_utc=None`; stop rule not triggered |
| `test_new_parse_anomaly_does_not_count_toward_max_posts` | Anomaly rows: `posts_skipped += 1`, max_posts not decremented |
| `test_new_parser_exception_stores_post_had_targets_0` | `extract_targets` raises; post stored with `had_targets=0`; `parse_errors=1`; run continues |
| `test_top_dedupe_repeat_run` | Same posts twice; no row duplication; `posts_stored` consistent |
| `test_top_targets_deduped_counted` | Second run; `targets_deduped > 0` |
| `test_top_updates_scrape_time_only` | `last_scrape_time` written; `last_post_created_utc` is None |
| `test_replace_cache_wipes_before_run` | DB populated; `replace_cache=True` clears all rows; new posts written |
| `test_rate_limit_error_returns_clean_result` | `RateLimitError` → `rate_limited=True`; no DB writes |
| `test_fetch_error_returns_clean_result` | `FetchError` → `error` set; no DB writes |
| `test_schema_drift_returns_error_result` | `open_connection` raises `RuntimeError` → `error` set; `replace_cache_done` correct; no crash |
| `test_open_connection_sqlite_error_returns_error_result` | `open_connection` raises `sqlite3.OperationalError` → `error` set; no crash |
| `test_replace_cache_done_propagated_on_network_error` | `replace_cache=True`; `fetch_posts` raises `FetchError`; `IngestResult.replace_cache_done=True`; DB is wiped (by design) |
| `test_setup_phase_db_error_returns_clean_result` | `init_db` raises `sqlite3.Error`; `error` set; no crash; `replace_cache_done` accurate |
| `test_unsupported_subreddit_returns_error` | `options.subreddit="pics"` → `error` set before any IO |
| `test_ingest_result_fields_populated` | Smoke: all `IngestResult` fields non-None after clean run |

---

## 9. Risks, Bad Assumptions, Shortcuts to Avoid

**R1: Assuming `new` feed is strictly monotone.**
Reddit's `new` feed is mostly newest-first but clock skew can produce slight re-ordering. The
`(created_utc, post_id)` tuple stops on any post whose full tuple is `<=` cursor. Do not simplify
to `created_utc`-only check.

**R2: Using `cursor.rowcount` after `executemany` to count INSERT OR IGNORE hits.**
`rowcount` behavior after `executemany` with `INSERT OR IGNORE` is unreliable (-1 in some Python
sqlite3 versions). Use `conn.total_changes` delta (before/after the call) instead.

**R3: Incomplete `open_connection` error handling.**
`open_connection` can raise `RuntimeError` (schema drift), `FileNotFoundError` (DB missing), OR
`sqlite3.Error` (permissions, locked file). All three must be caught in the outer guard before
entering the write-phase try/except. The outer guard is `(RuntimeError, FileNotFoundError, sqlite3.Error)`.
Do not rely on the inner `except sqlite3.Error` to catch connection-open failures — it won't, because
the connection object doesn't exist yet and rollback cannot be called.

**R4: Writing a real `(utc, id)` cursor for `top` mode.**
Top-mode cursor fields must be written as `None`. Reusing `new`-mode cursor logic for top would
create a false anchor that breaks future `new`-mode runs sharing the same subreddit.

**R5: Advancing cursor past parse-anomaly discards.**
A post with a missing `id` or `created_utc` has no valid tuple. It must not advance the cursor.
Only posts with valid metadata (non-None `meta`) advance `cursor_candidate`.

**R6: Opening the write connection before the fetch.**
If `fetch_posts` raises, no connection should be open. Fetch first; open connection after.

**R7: NSFW filter before cursor candidate update.**
NSFW-filtered posts must advance the cursor candidate to prevent feed stall. The locked per-post order is:
meta check → max_posts check → cursor stop → `posts_visited += 1` → update `cursor_candidate` → NSFW filter → parse → write.

**R8: Relying on `subreddit` as a passthrough to the client.**
The client URL is hardcoded to `r/opendirectories`. Allowing arbitrary `options.subreddit` values
would write ingest state under a key that doesn't correspond to the data actually fetched. Lock and
validate in Card 3; plumb through client in a later card if needed.

---

## 10. Open Questions

None blocking.
