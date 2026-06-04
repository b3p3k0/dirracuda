# C10 - Reddit Primary DB Planning

Status: Done
Last updated: 2026-06-04

## D1 Decision: Primary schema expansion — APPROVED
`redseek.store.init_db(primary_db_path)` creates Reddit runtime tables in the primary DB.
Additive, idempotent (`CREATE TABLE IF NOT EXISTS`). No FK links to main protocol tables.

## D2 Decision: replace_cache scope — APPROVED (Option B: state_only)
`replace_cache_scope: Literal["full", "state_only"] = "full"` added to `IngestOptions`.
Primary-DB callers (Start Scan, Reddit Grab, WebUI) always pass `replace_cache_scope="state_only"`.
`wipe_all` is unreachable from any primary-DB call path.

## D3 Decision: Current-run sync scope — APPROVED
Sync scoped to `_probe_candidate_keys` from current-run `IngestResult`. No all-DB scans.

## D4 Decision: Browser clear/add guards — APPROVED
`allow_promotion=False` hides "Add to dirracuda DB" and "Clear DB" at build time;
call-time guards provide a second layer of defense.

## Objective

Move new Reddit/Redseek runtime writes to the active primary DB context, matching the C9 SearXNG contract:

- New runs should not write to `~/.dirracuda/data/experimental/reddit_od.db`.
- `reddit_posts`, `reddit_targets`, and `reddit_ingest_state` remain the runtime table names for compatibility.
- Parsed SMB/FTP/HTTP targets should sync into the main protocol tables during run completion.
- Standard Reddit browser mode should hide `Add to dirracuda DB` because new rows are already synced.
- Legacy sidecar browsing remains available for old data.

## Current Local Evidence

- `gui/components/dashboard_scan.py` still selects `get_paths().reddit_od_db_file` before calling `run_ingest(...)`.
- `experimental/webui/app.py` still calls `run_ingest(options)` without `request.app.state.db_path`.
- `gui/components/reddit_browser_window.py` still always exposes promotion controls when callbacks are supplied.
- `experimental/redseek/store.py` validates sidecar-shaped tables and can initialize `reddit_posts`, `reddit_targets`, and `reddit_ingest_state` at any injected DB path.
- `experimental/redseek/service.py` already accepts `db_path`, uses injected store helpers, and has current-run candidate keys via `_probe_candidate_keys`.

## External Reality Check

- Reddit's API docs describe listing pagination with `after`, `before`, `limit`, and `count`; plan changes must not invent page-number behavior.
- Reddit Developer Terms allow access only under Reddit's terms/docs, reserve audit/update rights, and restrict excessive/abusive use.
- Reddit Data API Terms require use through documented access info, compliance with limits, and acknowledge Reddit may set/enforce API limits.
- SQLite docs reinforce that foreign-key enforcement is connection state (`PRAGMA foreign_keys`) and WAL creates side files; primary DB use must keep existing runtime-state checks.

Sources:
- https://www.reddit.com/dev/api/
- https://redditinc.com/policies/developer-terms
- https://redditinc.com/policies/data-api-terms
- https://www.sqlite.org/foreignkeys.html
- https://www.sqlite.org/pragma.html
- https://www.sqlite.org/wal.html

## Decisions Needed Before Implementation

1. **Primary runtime tables:** Confirm it is acceptable for `redseek.store.init_db(primary_db)` to create Reddit runtime tables in the primary DB. This is not a destructive migration, but it is a schema-contract expansion.
2. **Replace-cache semantics:** Decide whether `replace_cache=True` should wipe Reddit runtime tables in the primary DB. Current sidecar semantics wipe all Reddit tables before fetch; doing that in primary DB is more visible and deserves explicit HI approval.
3. **Current-run sync scope:** Prefer syncing only current-run target keys already attached to `IngestResult` as `_probe_candidate_keys`. If duplicates are ignored, the helper should still account for selected/current-run keys deterministically.
4. **Browser scope:** Standard Reddit browser should read primary DB runtime rows in normal mode, while `[Legacy] Sidecar Data` should pass the historical sidecar path and keep promotion enabled.
5. **Unsupported targets:** Unknown-protocol/unresolved targets should be skipped with explicit counts, not silently dropped.

## Recommended Shape

Keep this close to C9:

- Add `experimental/redseek/main_db_sync.py`.
- Add focused read helper(s) in `experimental/redseek/store.py`, likely by dedupe keys:
  - avoids all-runs scans
  - uses current-run candidate keys already captured by service
  - preserves existing ingest-state behavior
- Map each target row to the existing sidecar promotion prefill shape:
  - SMB -> host type `S`
  - FTP -> host type `F`
  - HTTP/HTTPS -> host type `H`
  - unsupported protocol or missing host -> skipped
- Use `DatabaseReader` and `promote_sidecar_prefills`.
- Return deterministic summary:
  - `selected`
  - `processed`
  - `inserted`
  - `updated`
  - `skipped`
  - `failed`
  - `cancelled`
- Never raise from the sync helper.

## Entry Points To Repoint

- `gui/components/dashboard_scan.py`
  - replace `get_paths().reddit_od_db_file` with active primary DB path
  - call sync helper after successful `run_ingest`
  - log/surface sync totals
  - remove sidecar/manual-promotion completion copy

- `experimental/webui/app.py`
  - pass `db_path=request.app.state.db_path` to `run_ingest`
  - call sync helper after success
  - include sync totals in `/api/jobs/{job_id}` metadata
  - remove sidecar/manual-promotion progress copy

- `gui/components/dashboard_experimental.py`
  - normal `open_reddit_post_db` should pass active primary DB path and `allow_promotion=False`
  - legacy sidecar path should pass `reddit_od.db` and `allow_promotion=True`

- `gui/components/reddit_browser_window.py`
  - add `allow_promotion=True` default, matching SearXNG browser
  - hide `Add to dirracuda DB` when false
  - retain existing promotion code for legacy path

## Test Plan Candidates

- `shared/tests/test_redseek_main_db_sync.py`
  - target mapping by protocol
  - unsupported protocol skip
  - unresolved host skip
  - idempotent repeat sync
  - sync never raises on store/read failure

- `shared/tests/test_redseek_store.py`
  - focused read helper by dedupe keys/full rows

- `shared/tests/test_redseek_service.py`
  - primary `db_path` still persists posts/targets/state
  - `_probe_candidate_keys` still scopes current-run targets
  - replace-cache behavior documented/tested per HI decision

- `gui/tests/test_dashboard_scan.py`
  - Start Scan Reddit uses primary DB path
  - completion copy says primary DB sync, not sidecar

- `gui/tests/test_reddit_browser_window.py`
  - promotion controls hidden when `allow_promotion=False`

- `experimental/webui/tests/test_reddit_routes.py`
  - `/api/reddit/run` passes `app.state.db_path`
  - job metadata includes sync totals

## Risks

- **Primary schema expansion:** Creating Reddit runtime tables in the primary DB is additive, but still changes the primary DB contract.
- **Replace-cache blast radius:** Existing sidecar wipe semantics are acceptable for a sidecar cache; in primary DB they can surprise operators.
- **Duplicate accounting:** Reddit dedupes by `dedupe_key`; sync summaries must distinguish selected/current-run rows from inserted/updated protocol records.
- **Network policy drift:** Reddit can change access rules, rate limits, and endpoint behavior. Keep the implementation conservative and do not broaden fetch behavior in this card.
- **UI file size:** `gui/components/dashboard_scan.py` is already above 1500 lines after C9; any growth should stay small or be extracted.

## Acceptance

C10 implementation should be considered done only when:

- New Start Scan Reddit runs write Reddit runtime tables into the active primary DB context.
- Parsed targets from those runs appear in primary protocol surfaces without manual promotion.
- WebUI Reddit run jobs use `app.state.db_path` and expose sync totals.
- Normal Reddit browser mode hides manual promotion.
- Legacy sidecar Reddit browsing remains available for historical data.
- README and Technical Reference match shipped behavior.
- Targeted tests and quick lane pass, with any failures classified as pre-existing vs introduced.
