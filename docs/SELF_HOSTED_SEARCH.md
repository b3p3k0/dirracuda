# Self-hosted Search Aggregators

The **Self-hosted Search** feature supports SearXNG and DeGoog through the shared
`experimental/se_dork` pipeline. See the [operator guide](../README.md#self-hosted-search)
for setup and the [C7 workspace](dev/searxng_dork_module/README.md) for task notes.

## Backend contract

| Backend | Reachability | Search | Pagination |
|---|---|---|---|
| SearXNG | `/config` | `/search?format=json` | `pageno`, up to 40 pages |
| DeGoog | `/api/search-tabs` | `/api/search?type=web` | `page`, up to 10 pages |

`client.run_reachability_check()` tries the existing SearXNG route first. HTTP
404 triggers a DeGoog metadata check; connection, authentication, and rate-limit
failures do not switch providers. A valid DeGoog metadata object has a `tabs`
list. An explicit `/api/search` URL skips the SearXNG check. Both APIs support
instance URLs with reverse-proxy path prefixes.

`PreflightResult.search_endpoint` carries the resolved endpoint to the fetch
loop. Detection runs once, without an upstream search. Explicit Test adds a
`q=hello` search; a normal run validates JSON on its real first-page query.

`backends.py` builds provider-specific query parameters and maps DeGoog
`snippet`/`source`/`sources` to the existing store's `content`/`engine`/`engines`
contract. No schema migration is required. Provider IDs, saved settings, route
names (`/scans/searxng`, `/api/searxng/*`), and provenance identifiers retain
their existing names for compatibility.

DeGoog uses native JSON without a compatibility toggle. API-key authentication
is not supported by this integration. Both transports keep TLS certificate
verification enabled. SearXNG's `unresponsive_engines` and DeGoog's
`engineTimings` (`rate_limited` or HTTP status 429) both feed the shared bounded
retry/pacing policy, including HTTP-200 responses. Direct HTTP 429 handling is
unchanged. Timing values such as 429 ms are not treated as HTTP status codes.

Queries remain text through Apply, the scan form, and RunOptions; only surrounding
whitespace is trimmed. `backends.search_url()` URL-encodes `q` once. It does not
wrap the query in extra quotes or strip intentional phrase syntax. Browser engine
selections can differ from an instance's API defaults, so compare engine metadata
as well as query text when troubleshooting.

References: [DeGoog API](https://degoog-org.github.io/docs/api.html),
[SearXNG API](https://docs.searxng.org/dev/search_api.html).

## Troubleshooting sparse or empty results

A reachable instance does not guarantee working upstream search engines. Result
counts and relevance depend on the enabled engines, their indexes, query syntax,
and upstream restrictions. Shipped dorks are starting points to explore and adapt.

1. Run the exact query in the same instance's browser interface, preserving
   quotes and punctuation. If both searches are sparse, check the backend's
   engine errors and server logs for HTTP 429, CAPTCHA, access denial, or timeouts.
   Allow cooldown or resolve the reported backend problem before retrying.
2. Compare the engines supplying browser results with the API's enabled engines.
   Browser preferences can differ from API defaults. A page of unrelated results
   from one surviving engine does not mean the other engines are working.
3. Review **Live Scan Output** for fetched and retained counts. Dirracuda keeps
   confirmed open indexes; ordinary pages, stale links, or inaccessible results
   can leave no retained directories even when the search returned hits.
4. Once engines are working, try a broader query or change one operator at a
   time. Quotes affect phrase matching, and operator support varies by engine.
   Keep useful variants as custom dorks; Dirracuda preserves intentional quotes.

For API diagnostics, SearXNG reports per-engine failures in
`unresponsive_engines`; DeGoog reports them in `engineTimings`. An HTTP-200 search
response can still contain upstream failures. Check these fields alongside the
result list, rather than treating HTTP success as proof of a healthy search.

The [SearXNG search API](https://docs.searxng.org/dev/search_api.html) explains
engine-specific query syntax; the [DeGoog API guide](https://degoog-org.github.io/docs/api.html)
documents engine selection for API requests.

## Runtime tables and pacing

The self-hosted search module (`experimental/se_dork`) now writes runtime workflow tables into the active primary DB context (same DB path used by the running GUI/WebUI session).

**Storage contract**: `run_dork_search` persists `dork_runs`/`dork_results` in the active primary DB path and auto-syncs retained HTTP/HTTPS rows into main protocol host tables during run completion. Each page commits raw rows, performs classification and probing without an open SQLite transaction, then commits verdict/probe state before fetching the next page. Main protocol-table sync still runs once at completion. Manual promotion is not required for new runs. Successful runs append a Shodan-style rollup to Live Scan Output. Standalone runs keep the result popup; multi-provider Start Scan runs suppress it while the serial provider queue continues.

**Upstream pacing contract**: page 1 runs immediately. The next-page deadline starts when a response arrives. Storing, classifying, filtering, and optional probing run sequentially and consume that window; the service sleeps only for the remainder. Normal deadlines use ±20% jitter around 2 seconds for pages 2–5, 4 seconds for pages 6–10, 6 seconds for pages 11–20, and 8 seconds for pages 21–40. A non-empty response remains productive when `unresponsive_engines` reports 403/429, access denied, rate limit, CAPTCHA, or Cloudflare conditions. Such pages use 10, 20, and then at most 30 seconds for consecutive affected pages; a clean page resets normal pacing. Empty throttled pages use a run-wide retry ladder. Direct HTTP 429 responses consume the same retry budget and honor `Retry-After` within 1–300 seconds. Completed pages remain durable when a later fetch fails and finish as a partial run with a warning. Zero-row exhaustion returns a structured run error.

**Runtime policy (C11A)**: `RunOptions` exposes three clamped fields used at the service layer.

| Field | Default | Clamp | Effect |
|---|---|---|---|
| `request_timeout` | 15 s | 5–60 | Timeout for reachability check and each fetch call |
| `short_retry_delay` | 30 s | 5–60 | First hard-retry cooldown (early and mature runs) |
| `long_retry_delay` | 180 s | 60–300 | Second hard-retry cooldown (early runs only) |

A run becomes **mature** after 5 productive pages or 50 unique URLs (whichever comes first). A productive page adds at least one unique URL and completes the full persist/classify/retain/probe pipeline. Early runs allow two retry slots (short then long); mature runs allow only one (short). Web UI callers use the service defaults.

**Live validation harness (C11D)**: `scripts/live_test_searxng.py` is an opt-in script for end-to-end testing of the SearXNG dork pipeline against a real instance. It requires `--confirm-live` before any network access. A `tempfile.mkdtemp()` directory holds the run DB; the primary database is never opened. After the run, the script asserts stage ordering, SQLite structural integrity, and DB/RunResult field consistency, then deletes the temp directory. Pass `--keep-db` to retain it for debugging. Use `--cancel-after-classify N` for deterministic cancellation at the classified-page boundary (no human Ctrl+C needed). Pytest may import the script's helper functions; it must never execute live behavior. The `--confirm-live` gate prevents any network call, temp-directory creation, or service invocation even when the module is imported.

**Live Scan Output semantic coloring (C11C)**: Search and Reddit progress lines, provider-queue transitions, and completion rollups are colored at display time in `gui/components/log_semantic_color.py::colorize_for_display`, which is called inside `append_log_line` before text is inserted into the Tk Text widget. `log_history` always stores the original input, so C11C adds no ANSI escapes to Copy All and history ordering is unaffected. Pre-existing Shodan subprocess ANSI (raw CLI stdout) passes through unchanged. The feature reuses the existing ANSI tag infrastructure (`ansi_fg_bright_blue` / `_green` / `_yellow` / `_red`) and theme-backed colors already configured in `dashboard_logs.configure_log_tags`. `_log_status_event(message)` signature is unchanged; all callers and test doubles continue to work. Rollup coloring requires `"\n" in line` to prevent misclassification of a standalone `SUMMARY_TITLE` heading emitted by Shodan. The classifier is an exact-allowlist of known Self-hosted Search/Reddit/queue message prefixes (plus legacy SearXNG prefixes) — generic keywords are not used.

**Search tuning controls (C11B)**: Start Scan exposes the three fields above as themed sliders in the Self-hosted Search provider row. Values persist through GUI settings (`unified_scan_dialog.searxng_request_timeout`, `..._short_retry_delay`, `..._long_retry_delay`) and scan templates (`searxng_options.request_timeout`, `..._short_retry_delay`, `..._long_retry_delay`). The scan request carries them as `searxng_request_timeout`, `searxng_short_retry_delay`, `searxng_long_retry_delay`. Dashboard code (`gui/components/dashboard_searxng_scan.py`) reads these keys and coerces them with the same half-up step-snapping helper before constructing `RunOptions`; out-of-range or malformed values fall back to the field defaults. The service layer then clamps again independently. Web UI continues using `RunOptions` defaults.

**`dork_runs.status` values**: `running`, `done`, `error`, `cancelled`. Status `cancelled` is set when the caller signals the optional `cancel_event: threading.Event` passed to `run_dork_search`. Cancellation is not an error: `error_message` is null, and the run row remains in the primary DB. The primary-table sync still runs for cancelled runs that created a `run_id`, preserving any retained rows.

Legacy sidecar files (for example `~/.dirracuda/data/experimental/se_dork.db`) may still exist for historical browsing/migration paths, but they are no longer the default write target for new search runs.

Tables:
- `dork_runs` — one row per dork search run (`run_id` PK), with `instance_url`, `query`, `max_results`, `fetched_count`, `deduped_count`, `verified_count`, `status`, `error_message`, `started_at`, `finished_at`
- `dork_results` — one row per candidate URL per run (`result_id` PK), FK `run_id → dork_runs(run_id)`; deduped per run on `UNIQUE(run_id, url_normalized)`; stores `url`, `url_normalized`, `title`, `snippet`, `source_engine`, `source_engines_json`, `verdict`, `reason_code`, `http_status`, `checked_at`, probe summary fields, and optional `probe_snapshot_json` for full probe-tree carry-forward

Verdict values: `OPEN_INDEX`, `MAYBE`, `NOISE`, `ERROR`.

URL normalization (`store.normalize_url`): scheme and netloc lowercased; path case preserved; trailing slash stripped from path; query string and fragment dropped.

## Desktop and Web UI flow

Start New Scan is the only desktop launch surface. Both providers use the
primary DB; the `experimental/se_dork` package name and settings keys remain
stable for compatibility.

```text
Dashboard -> Start New Scan -> Self-hosted Search -> Start Scan
  -> dashboard_provider_queue -> dashboard_searxng_scan.start_searxng_scan
  -> run_dork_search(options, db_path=primary_db) on a worker thread
  -> reachability check, then JSON validation using the actual first-page query
  -> fetches up to 500 unique URLs by default (1,000 maximum)
  -> writes dork_runs + dork_results in the active primary DB
  -> sync_run_to_main_db(run_id, db_path=primary_db) upserts retained HTTP targets
  -> Live Scan Output rollup; result popup for a search-only run
  -> review/probe/browse current targets in Server List
```

Historical sidecar entry path:

```text
Dashboard -> Database -> [Legacy] Sidecar Data -> Self-hosted Search Results
  -> SeDorkBrowserWindow (reads the historical sidecar DB)
  -> stored metadata/probe details and manual promotion remain available
```

Desktop Start New Scan has no standalone Test or Open Results DB buttons.
The Web UI keeps its existing scan page and `/api/searxng/*` routes.

Explicit SearXNG Test/preflight checks (`experimental/se_dork/client.py`):
1. GET `/config` — reachability probe
2. GET `/search?q=hello&format=json` — JSON capability check; HTTP 403 maps to `INSTANCE_FORMAT_FORBIDDEN` (fix: enable `search.formats: [json]` in SearXNG `settings.yml`)

Normal SearXNG Run uses only the `/config` reachability step before opening the run row. The actual page-1 query validates JSON search support, avoiding a redundant `q=hello` request that would otherwise fan out to upstream engines before every run. The WebUI queues `/api/searxng/run` directly; `/api/searxng/preflight` remains available as an explicit compatibility/test endpoint.
