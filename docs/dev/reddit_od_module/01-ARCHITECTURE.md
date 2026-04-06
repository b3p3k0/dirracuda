# Reddit OD Module: Architecture

Date: 2026-04-05
Scope: POC, isolated feed ingestion

## High-Level Design
The Reddit module is a sidecar subsystem named `redseek`.

```text
GUI (Dashboard) -> Reddit Grab Dialog -> experimental.redseek.service
                                    -> experimental.redseek.client (Reddit JSON)
                                    -> experimental.redseek.parser
                                    -> experimental.redseek.store (sidecar SQLite)

GUI (Reddit Post DB Browser) -> experimental.redseek.store reads
                             -> explorer_bridge (manual, user-triggered)
```

## Isolation Boundary
1. No writes to main scan tables (`smb_*`, `ftp_*`, `http_*`).
2. No reuse of scan manager execution path for network probing.
3. Sidecar DB file only for Reddit module data.
4. Explorer remains explicit user action from Reddit browser UI.

## Proposed Module Layout
```text
experimental/redseek/
  __init__.py
  client.py
  parser.py
  models.py
  store.py
  service.py
  explorer_bridge.py

gui/components/
  reddit_grab_dialog.py
  reddit_browser_window.py
```

## Sidecar DB
Proposed path: `~/.dirracuda/reddit_od.db`

### Schema v1

`reddit_posts`
- `post_id TEXT PRIMARY KEY`
- `post_title TEXT NOT NULL`
- `post_author TEXT`
- `post_created_utc REAL NOT NULL`
- `is_nsfw INTEGER NOT NULL DEFAULT 0`
- `had_targets INTEGER NOT NULL DEFAULT 0`
- `source_sort TEXT NOT NULL` (`new` or `top`)
- `last_seen_at TEXT NOT NULL`

`reddit_targets`
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `post_id TEXT NOT NULL`
- `target_raw TEXT NOT NULL`
- `target_normalized TEXT NOT NULL`
- `host TEXT`
- `protocol TEXT` (`http`, `https`, `ftp`, or `unknown`)
- `notes TEXT`
- `parse_confidence TEXT` (`high`, `medium`, `low`)
- `created_at TEXT NOT NULL`
- `dedupe_key TEXT NOT NULL UNIQUE`
- FK `post_id -> reddit_posts(post_id)`

`reddit_ingest_state`
- `subreddit TEXT NOT NULL`
- `sort_mode TEXT NOT NULL`
- `last_post_created_utc REAL`
- `last_post_id TEXT`
- `last_scrape_time TEXT`
- PRIMARY KEY `(subreddit, sort_mode)`

## Ingestion Semantics

### Shared
1. Request `https://www.reddit.com/r/<subreddit>/<sort>.json` with explicit User-Agent.
2. Respect delays of 1-2 seconds between page fetches.
3. Abort run immediately on HTTP 429.
4. Limit to max 3 pages per run.
5. Parse title and optional selftext body.

### `new` Mode
1. Read newest-first pages.
2. Stop early when post is older than saved cursor, using tuple compare `(created_utc, post_id)`.
3. Update ingest cursor only if run reaches write phase successfully.

### `top` Mode
1. Read first N pages (`N <= 3`) with no early-stop assumption.
2. Use DB dedupe (`post_id`, `dedupe_key`) to avoid duplicate writes.
3. Update `last_scrape_time`; keep cursor fields for audit but do not rely on them for early stop.

Reason: `top` ordering is score-based, not strict chronological order.

## Parsing + Normalization Pipeline
1. Extract candidates from title and optional body.
2. Clean markdown/punctuation wrappers.
3. Normalize representation (scheme case, host case, slash cleanup).
4. Validate host/IP shape and optional port bounds.
5. Classify protocol and confidence.
6. Build deterministic dedupe key: `sha1(post_id + "|" + target_normalized)`.

## Explorer Bridge Rules
1. Full URL opens directly.
2. Host + protocol constructs URL directly.
3. Host only uses inference:
   - infer `http` for explicit `:80`
   - infer `https` for explicit `:443`
   - infer `ftp` for explicit `:21`
4. If still unknown, show protocol-pick prompt before opening.

## Safety + Performance
1. Network work runs off the Tk main thread.
2. UI updates marshal back on Tk thread only.
3. Do not execute any remote action except optional user-triggered browser open.
4. Cap per-run page count and parser input size to avoid runaway memory use.
