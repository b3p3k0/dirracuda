# SearXNG Dork Module Spec

Date: 2026-04-18
Status: Approved and implemented (C1–C6 complete)
Scope: Experimental tab module for SearXNG-driven dork collection and HTTP open-index verification

## Problem Statement

Manual dorking workflow today:
1. Run ad-hoc queries like `site:* intitle:"index of /"`.
2. Open many noisy search results.
3. Manually decide which links are real open directory listings.

Pain points:
1. Slow and repetitive triage.
2. High false-positive volume (forums, docs, unrelated pages).
3. Direct corporate search scraping is blocked often and brittle to maintain.

## Locked Decisions

1. Backend source is SearXNG only in v1.
2. User enters one instance URL manually.
3. No searx.space import in v1.
4. Single instance only (no failover pool).
5. Default instance URL is `http://192.168.1.20:8090`.
6. Reuse existing HTTP probe/verification code path for classification.
7. Replace Experimental `placeholder` tab with this module.

## Goals

1. Add a SearXNG Dorking tab to Experimental UI.
2. Validate SearXNG connectivity and JSON capability before running.
3. Run dork query via `/search?format=json`.
4. Store raw results and classification outcomes in a sidecar DB.
5. Classify candidate URLs as likely open-index vs noise using existing HTTP logic.
6. Keep module reversible and isolated from core SMB/FTP/HTTP scan workflows.

## Non-Goals (v1)

1. No direct Google/DDG/Bing HTML scraping.
2. No public-instance auto-discovery/import.
3. No multi-instance routing/failover.
4. No background crawling beyond bounded verification/probe checks.
5. No automatic bulk promotion into main Dirracuda DB.

## UX Contract

## Entry Point

Dashboard -> Experimental -> SearXNG Dorking tab

## Primary Actions

1. Test Instance
   1. Checks `/config` reachability.
   2. Checks `/search?q=hello&format=json` returns `200` JSON.
2. Run Dork Search
   1. Requires successful instance test first.
   2. Executes query against instance.
   3. Persists run and result rows.
   4. Optionally performs bounded verification/classification.
3. Open Dork Results DB
   1. Opens module result browser window for review/export/promotion.

## Inputs

1. Instance URL (text, persisted).
2. Query template text (default `site:* intitle:"index of /"`, persisted).
3. Max results / page cap (persisted).
4. Verification toggle (use existing HTTP probe path).

## Outputs

1. Run summary: fetched, deduped, verified, classified counts.
2. Per-row verdict:
   1. `OPEN_INDEX`
   2. `MAYBE`
   3. `NOISE`
   4. `ERROR`
3. Reason code for non-open outcomes.

## Architecture

```text
SearXNG Dorking Tab (GUI)
  -> SearXNG client (experimental/se_dork/client.py)
  -> service/orchestrator (experimental/se_dork/service.py)
  -> sidecar store (experimental/se_dork/store.py)
  -> classifier bridge (existing HTTP verifier/probe path)
  -> results browser UI (gui/components/se_dork_browser_window.py)
```

## Existing Code Reuse

1. HTTP verifier primitives:
   - `commands/http/verifier.py::try_http_request`
   - `commands/http/verifier.py::validate_index_page`
2. HTTP probe runner (if deeper snapshot needed):
   - `gui/utils/http_probe_runner.py::run_http_probe`
3. Experimental tab registry/dialog wiring:
   - `gui/components/experimental_features/registry.py`
   - `gui/components/experimental_features_dialog.py`

## Sidecar Storage Contract

Path:
- `~/.dirracuda/se_dork.db`

Minimum tables:
1. `dork_runs`
   1. `run_id` (PK)
   2. `started_at`, `finished_at`
   3. `instance_url`
   4. `query`
   5. `max_results`
   6. `fetched_count`, `deduped_count`, `verified_count`
   7. `status`, `error_message`
2. `dork_results`
   1. `result_id` (PK)
   2. `run_id` (FK -> dork_runs)
   3. `url` (indexed)
   4. `title`, `snippet`
   5. `source_engine`, `source_engines_json`
   6. `verdict` (`OPEN_INDEX|MAYBE|NOISE|ERROR`)
   7. `reason_code`
   8. `http_status`
   9. `checked_at`

Deduping rule:
1. Normalize URL and dedupe by normalized URL per run.

## Runtime Safety Rules

1. Hard timeout for instance test and search fetch.
2. Hard cap on result processing count.
3. Verification runs bounded and cancellable.
4. No destructive writes to main DB from search run path.
5. Promotion into main DB must remain explicit user action.

## Failure Taxonomy (minimum)

1. `instance_unreachable`
2. `instance_non_json`
3. `instance_format_forbidden` (typically missing `search.formats: json`)
4. `search_http_error`
5. `search_parse_error`
6. `verify_timeout`
7. `verify_connect_fail`
8. `verify_not_index`

## Acceptance Criteria

1. SearXNG Dorking tab replaces placeholder tab in Experimental dialog.
2. Instance test clearly passes/fails with actionable message.
3. Dork run returns and stores JSON result rows.
4. Existing HTTP verification path is called for classification.
5. Results view shows verdict + reason per URL.
6. Settings persist across dialog reopen/app restart.
7. README and technical docs explain SearXNG `format=json` setup and 403 troubleshooting.

