# Reddit OD Module: V3 Spec

Date: 2026-04-17
Status: Draft for HI/CTO review
Scope: Expand redseek ingestion modes while staying r/opendirectories-only

## Problem Statement

Current redseek ingestion is useful but narrow:
1. Feed source is fixed to r/opendirectories (good, keep it).
2. Sort supports only `new` and `top`, with `top` hardcoded to `t=week`.
3. No first-class query mode for keyword triage.
4. No first-class user-submitted ingestion mode.

This limits analyst workflows when triaging seasonal spikes, author-specific posts, or targeted keyword hunts.

## Locked Decisions

1. Source remains fixed to `r/opendirectories` for V3.
2. Search remains subreddit-scoped only (`restrict_sr=1`).
3. Username mode ingests submitted posts only (`/user/<username>/submitted.json`), not comments.
4. Sort options for new modes are `new` and `top` only.
5. Top windows include: `hour`, `day`, `week`, `month`, `year`, `all`.
6. Persist last-used mode inputs (mode/sort/top window/query/username and existing toggles).
7. Deliver one feature card at a time (no merged implementation cards unless HI requests it).

## Goals

1. Add top-window control for feed ingestion.
2. Add keyword search mode inside r/opendirectories.
3. Add username submitted-post mode.
4. Keep parser/store safety behavior unchanged (dedupe, bounded pages, no auto network actions beyond fetch).
5. Preserve sidecar isolation (`~/.dirracuda/reddit_od.db`) and avoid coupling to main scan pipeline.

## Non-Goals

1. No global Reddit search in V3.
2. No comment ingestion in V3.
3. No API-key/OAuth migration in V3.
4. No merge into primary dirracuda DB ingestion pipeline.
5. No auto-probe or speculative browsing actions.

## Endpoint Capability Matrix

### Feed mode
- Endpoint: `/r/opendirectories/{sort}.json`
- Sort: `new|top`
- Top window: `t=hour|day|week|month|year|all` (top only)

### Search mode (subreddit only)
- Endpoint: `/r/opendirectories/search.json`
- Required params:
  - `q=<query>`
  - `restrict_sr=1`
  - `sort=<new|top>`
- Optional param:
  - `t=<hour|day|week|month|year|all>` when sort is top

### User mode
- Endpoint: `/user/<username>/submitted.json`
- Params:
  - `sort=<new|top>`
  - `t=<hour|day|week|month|year|all>` when sort is top

## Data Availability from Listing JSON

These listing endpoints expose more fields than V1 currently stores. Common useful fields include:
- identity/linking: `id`, `permalink`, `url`, `domain`, `subreddit`
- author/context: `author`, flair fields, `over_18`
- ranking/signals: `score`, `num_comments`, `upvote_ratio`
- text payloads: `title`, `selftext`
- timing: `created_utc`

V3 does not require schema expansion for these fields. Existing parser and target storage may continue to rely on title/selftext plus existing post fields.

## Ingestion Semantics by Mode

### Feed new
- Keep existing cursor-stop behavior based on `(created_utc, post_id)`.

### Feed top
- Use selected top window (`t=`).
- Dedupe-based ingestion (no cursor-stop assumption).
- Persist state by top-window key so windows do not collide.

### Search
- Require non-empty query.
- Dedupe-based ingestion (no cursor-stop assumption).
- Bounded by `max_pages` and `max_posts` as today.

### User submitted
- Require valid username input.
- Dedupe-based ingestion (no cursor-stop assumption).
- Bounded by `max_pages` and `max_posts` as today.

## Ingest-State Keying (Compatibility + Future Safety)

Current state table key is `(subreddit, sort_mode)`.
V3 key strategy:
1. `new` stays `new`.
2. feed top uses `top:<window>` (for example `top:week`, `top:month`).
3. Search and User modes use explicit mode-prefixed keys to avoid collisions.

Compatibility rule for first rollout:
- For feed `top:week`, read fallback from legacy `top` key if `top:week` is absent.
- Save forward using `top:week`.

This avoids a schema migration while preserving historical continuity.

## UI Contract (Reddit Grab Dialog)

1. Add mode selector: `Feed | Search | User`.
2. Keep subreddit fixed and visible as read-only context (`opendirectories`).
3. Keep sort selector: `new|top`.
4. Add top-window selector enabled only when sort is top.
5. Show query field only in Search mode.
6. Show username field only in User mode.
7. Keep existing toggles and max-post validation.
8. Save and restore last-used values on reopen.

## Acceptance Criteria

### Feature 1: Top window expansion
1. Top requests include selected `t=` value.
2. Week remains default for backward-friendly behavior.
3. Existing `new` mode behavior remains unchanged.

### Feature 2: Search mode
1. Search query required; empty query blocked with clear message.
2. Requests include `restrict_sr=1` and `sort in {new, top}`.
3. Search ingest stores/dedupes targets without regressions.

### Feature 3: User submitted mode
1. Username required and validated for basic format.
2. Requests go to `/user/<username>/submitted.json`.
3. Ingest path remains stable with existing parser/dedupe behavior.

### Feature 4: Settings persistence
1. Mode, sort, top window, query, username restore on reopen.
2. Existing settings behavior for parse/include_nsfw/replace_cache/max_posts remains intact.

## Validation Strategy (Card-Oriented)

1. Unit tests for client URL/params and input validation.
2. Service tests for mode dispatch and bounds.
3. UI validation for dialog field visibility + value persistence.
4. Focused regression suites for existing Reddit wiring and browser behavior.
