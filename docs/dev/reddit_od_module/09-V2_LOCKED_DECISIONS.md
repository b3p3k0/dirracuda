# Reddit OD Module: V2 Locked Decisions

Date: 2026-04-06  
Owner: HI + CTO session (late-night planning)

## Context
V1 is complete and committed. V2 focuses on making the Reddit module operationally useful for analysts without fully merging it into the primary scan pipeline.

## Locked Product Decisions

### D-A2: Preview capture uses `reddit_targets.notes`
Decision:
1. Capture first 120 chars of post title and body and store in `reddit_targets.notes`.
2. Do not add new sidecar DB columns for preview fields in V2.

Implementation contract:
1. Notes format is deterministic and one-line:
   - `T:<title_preview> | B:<body_preview>`
2. `title_preview` and `body_preview` are whitespace-normalized then truncated to 120 chars.
3. If one side is empty, only store the available side (no empty markers).
4. This repurposes `notes` for preview in V2 (parser diagnostic note text is not retained in stored rows).

### D-B4: Internal-first open flow with explicit fallback prompt
Decision:
1. Attempt internal explorer first for FTP/HTTP targets.
2. If internal launch cannot proceed, show a prompt explaining why and offer:
   - `Open in system browser`
   - `Copy address`
   - `Cancel`

Implementation contract:
1. No auto-probing to infer protocol.
2. `Copy address` uses the same clipboard behavior style as existing context-menu copy actions.
3. Cancel is silent (no error pop-up cascade).

### D-C1: Reddit browser context-menu promotion uses existing Add Record flow
Decision:
1. Add right-click menu action in Reddit browser: `Add to dirracuda DB`.
2. This action opens the same Add Record logic used by Server List, prefilled where possible.
3. User still confirms in dialog (no one-click write path).

### D-D1: Promotion granularity is host:port only
Decision:
1. Promote only canonical host/port-level data into `dirracuda.db`.
2. Do not store full URL path/query in primary protocol fields in V2.

Implementation contract:
1. Protocol mapping:
   - `http|https` -> HTTP Add Record fields (`H`, `scheme`, `port`)
   - `ftp` -> FTP Add Record fields (`F`, `port`)
2. Unknown protocol without recognized port does not auto-promote.

## Platform Constraints (Carry-Forward)
1. Keep module under `experimental/redseek`.
2. Sidecar DB remains `~/.dirracuda/reddit_od.db`.
3. No scan_manager/backend CLI coupling for Reddit workflows.
4. No auto-probe, no background speculative network actions.
5. No commits by worker agents unless HI explicitly asks.

## Known Technical Reality (Important for V2 carding)
1. Current main DB Add Record path validates `ip_address` as a literal IP.
2. Many Reddit targets are domain-based, not IP-based.
3. V2 must handle this explicitly in UX (clear prompt/error path), not by silent failure.

