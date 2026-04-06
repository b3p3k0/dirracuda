# Dirracuda Feature Spec: Reddit Ingestion Module (“redseek”) — JSON Endpoint Version

## Overview

This feature introduces a new ingestion source for Dirracuda that pulls user-submitted targets from Reddit (`r/opendirectories`) using publicly accessible JSON endpoints.

This is **NOT a scan source**. It is a **feed ingestion system**.

Key principles:

* No integration with existing scan pipeline
* No automatic probe or extract
* Fully isolated data store
* Explicit user-driven exploration only
* No API keys required

---

## Why JSON Endpoints (Design Decision)

Instead of using Reddit’s official API (PRAW), this module uses public JSON endpoints:

```
https://www.reddit.com/r/opendirectories/new.json
https://www.reddit.com/r/opendirectories/top.json
```

### Rationale

* No API key required
* No OAuth complexity
* Structured JSON (no HTML scraping)
* Lower friction for users
* Works well for low-volume ingestion

### Caveats (must be documented in README)

* Unofficial access method (subject to change)
* Soft rate limiting (HTTP 429 possible)
* Not guaranteed long-term stability
* Not suitable for high-frequency scraping
* Limited pagination depth

---

## Goals

* Provide a frictionless ingestion source (no setup required)
* Maintain strict separation from verified scan results
* Support incremental ingestion
* Keep UI impact minimal

---

## Non-Goals (MVP)

* No historical archive scraping
* No comment parsing
* No auto-probe / auto-extract
* No merging into main server DB
* No high-frequency polling

---

## Architecture

### Module Layout

```text
experimental/redseek/
  client.py        # JSON endpoint client
  parser.py        # target extraction + normalization
  models.py        # data structures
  store.py         # SQLite interaction
  service.py       # orchestration layer
  ui_dialog.py     # Reddit Grab dialog
  ui_browser.py    # Reddit Post DB viewer
  explorer_bridge.py
```

---

## Data Model

### reddit_posts

```sql
reddit_posts
------------
post_id PRIMARY KEY
post_title
post_author
post_created_utc
is_nsfw INTEGER
had_targets INTEGER
last_seen_at DATETIME
```

---

### reddit_targets

```sql
reddit_targets
--------------
id PRIMARY KEY
post_id (FK)
target_raw
target_normalized
host
protocol
notes
parse_confidence
created_at DATETIME
dedupe_key UNIQUE
```

---

### reddit_ingest_state

```sql
reddit_ingest_state
-------------------
subreddit PRIMARY KEY
last_post_created_utc REAL
last_post_id TEXT
last_scrape_time DATETIME
```

---

## Ingestion Workflow

### Core Logic

```text
GET subreddit JSON feed (sorted new)

for each post:
  if post.created_utc <= last_seen:
      STOP ingestion

  extract targets

  if none:
      store post (had_targets=0)
  else:
      store post (had_targets=1)
      store targets

update ingest state
```

---

## JSON Endpoint Handling

### Request Example

```python
GET https://www.reddit.com/r/opendirectories/new.json

headers = {
    "User-Agent": "dirracuda:reddit_ingest:v1.0"
}
```

---

### Pagination

Use:

```text
data["data"]["after"]
```

---

### Rate Limiting Strategy (REQUIRED)

```text
- 1–2 second delay between requests
- Max 2–3 pages per run
- Abort on HTTP 429
```

---

## Target Extraction Rules

### Sources

* Post title
* Selftext body

---

### Patterns

* http://
* https://
* ftp://
* IP:port
* raw IPv4
* bare domains

---

### Processing Pipeline

```text
raw text
  → regex extraction
  → cleanup (strip punctuation/markdown)
  → normalization
  → validation
  → classification
  → dedupe
```

---

### Confidence Levels

| Level  | Criteria              |
| ------ | --------------------- |
| High   | Full URL with scheme  |
| Medium | Hostname or host:port |
| Low    | Raw IP                |

---

## Handling “No Value” Posts

Posts with no valid targets:

* Stored in `reddit_posts`
* Marked `had_targets = 0`
* Not inserted into `reddit_targets`

---

## Incremental Scraping Strategy

### Required Behavior

* Only ingest new posts
* Stop early when encountering known posts

### Matching Logic

```text
if post.created_utc <= last_post_created_utc:
    STOP
```

Use BOTH:

* `created_utc`
* `post_id`

---

## UI Integration

### Dashboard

```text
[ Start Scan ] [ Reddit Grab ]
```

---

### Reddit Grab Dialog

```text
+-----------------------------------+
| Reddit Grab                       |
|-----------------------------------|
| Sort:        [new v]              |
| Max posts:    [50 ]               |
| Parse body:   [x]                 |
| Include NSFW: [x]                 |
| Replace cache:[x]                 |
|                                   |
| [Run Grab]     [Cancel]           |
+-----------------------------------+
```

---

### Reddit Post Browser

Accessible via:

```text
[ Reddit Post DB ]
```

---

### Layout

```text
+--------------------------------------------------------------------------------+
| Target       Proto  Conf  Author   NSFW  Notes           Date                   |
+--------------------------------------------------------------------------------+

[Open in Explorer] [Open Reddit Post] [Refresh] [Clear DB]
```

---

## Explorer Integration

| Input Type      | Behavior          |
| --------------- | ----------------- |
| Full URL        | Open directly     |
| Host + protocol | Construct URL     |
| Host only       | Prompt or disable |

---

## Deduplication Strategy

```text
normalized_target + post_id
```

---

## Safety Model

* All Reddit data is untrusted
* No automatic actions
* Explorer is user-triggered only

---

## Known Limitations (IMPORTANT — include in README)

* Reddit JSON endpoints are unofficial
* Endpoint behavior may change without notice
* Data availability is limited (not full history)
* Rate limits may apply
* Some posts contain no usable targets
* Data quality depends entirely on user-submitted content

---

## README Disclaimer (RECOMMENDED)

```text
Dirracuda’s Reddit ingestion feature uses publicly accessible JSON endpoints
to retrieve posts from r/opendirectories.

No authentication is required, and only publicly available data is accessed.

This method is not part of Reddit’s official API and may change or break at any time.
Users should treat all ingested data as unverified and potentially unsafe.
```

---

## Future Enhancements

* Optional PRAW integration (advanced mode)
* Comment parsing
* Multi-source ingestion
* Target scoring system
* Grouping repeated targets across posts

---

## Final Design Summary

* Zero-config ingestion (no API keys)
* Uses Reddit JSON endpoints
* Incremental, low-frequency scraping
* Fully isolated data store
* Manual exploration only

---

## TL;DR (for devs)

* Use JSON endpoints, not PRAW
* No auth required
* Store posts + targets separately
* Implement incremental ingestion
* Rate limit aggressively
* Keep feature isolated

---
