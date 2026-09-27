# Self-hosted Search Aggregators Spec

The desktop and Web UI feature is named **Self-hosted Search**. It supports one
operator-supplied SearXNG or DeGoog instance at a time.

## Transport

| Backend | Reachability | Search | Page parameter | Page cap |
|---|---|---|---|---|
| SearXNG | `/config` | `/search?format=json` | `pageno` | 40 |
| DeGoog | `/api/search-tabs` | `/api/search?type=web` | `page` | 10 |

A base URL first follows the existing SearXNG path. Only `/config` HTTP 404
triggers DeGoog detection; authentication, rate-limit, and connection failures
must not silently switch backends. DeGoog metadata must be an object with a
`tabs` list. Explicit `/api/search` skips the SearXNG check. Reverse-proxy path
prefixes are preserved. URLs must use HTTP(S) without query strings or fragments.

Explicit **Test** checks reachability and runs `q=hello`. Normal **Run** checks
reachability only, then validates JSON with the real first-page query. There is
no extra upstream test query or repeated provider detection during pagination.
Search responses must be JSON objects containing a `results` list.

DeGoog's `snippet`, `source`, and `sources` map to the existing store's `content`,
`engine`, and `engines` input fields. Its native JSON requires no format setting.
API-key authentication is outside this card; Test reports a clear limitation for
DeGoog 401/403. SearXNG format-policy guidance remains SearXNG-specific.

## Runtime and storage

- Preserve saved `se_dork` / `searxng` settings, provider IDs, routes, and schema.
- `run_dork_search` uses the active primary DB passed by desktop/Web UI callers.
- Reachability happens before DB setup; the run row commits before search.
- Process each page through storage, existing HTTP classification, retention,
  and optional probe before fetching the next page. Network work holds no DB lock.
- Keep pacing, HTTP 429 retry limits, cancellation, deduplication, and completed
  page durability. SearXNG `unresponsive_engines` handling remains supported;
  DeGoog does not provide that same diagnostic contract.
- Default result cap is 500; maximum 1,000. Neither is a yield guarantee.
- Retained rows sync to the main HTTP tables; legacy sidecar browsing remains.

## Surfaces and validation

Accessories, Start Scan, progress/results dialogs, Running Tasks, and the Web UI
use the shared feature name. Internal routes such as `/scans/searxng` remain
stable. The feature description names both supported backends.

Automated tests mock all network calls and use temporary databases. Cover both
request dialects, detection failure, malformed data, native metadata persistence,
page limits, retries, cancellation, and existing UI/storage contracts. Live
checks are separate and explicitly authorized; they must not write the user's DB.

References: [DeGoog API](https://degoog-org.github.io/docs/api.html),
[SearXNG API](https://docs.searxng.org/dev/search_api.html).
