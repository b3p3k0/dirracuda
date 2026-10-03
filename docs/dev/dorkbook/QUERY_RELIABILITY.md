# Self-hosted dork reliability — 2026-09-28

Status: investigation/fix in progress. Full live catalog comparison is blocked;
no built-in or saved query has been changed, and this round is not complete.

## October 2 follow-up and pending release wording

HI now reports useful results in both the DeGoog browser and Dirracuda using
`intitle "index of /"` (quotes included, no colon), plus results from some shipped
dorks. This supports the earlier upstream diagnosis; it does not establish
full-catalog coverage or equivalence with `intitle:"Index of /"`. Backend health
has not been rechecked by the agent since the September 28 diagnostics below.

Approved wording to publish **after live validation, before push**:

> Self-hosted Search results depend on your backend's enabled search engines,
> available indexes, and upstream restrictions. Dorkbook's shipped dorks are
> starting points to explore and adapt—they've been tested against the maintainer's
> own metasearch instances but aren't guaranteed to work everywhere, every time.
> Few or no results can reflect upstream restrictions, limited coverage, or query
> syntax—not necessarily a problem with Dorkbook. If results are unexpectedly
> sparse, try the same query directly in your backend's browser interface and
> check logs for engine errors or rate limits.

**Pending:** test every shipped Self-hosted Search dork against both owned
backends, record configuration/date/results, and assess useful results before
publishing the testing claim. If evidence does not support it, revise the claim.
README carries the caveat without that claim until then and links to the expanded
[troubleshooting section](../../SELF_HOSTED_SEARCH.md#troubleshooting-sparse-or-empty-results).
The earlier unquoted-comparison experiment remains available; no global quote
removal or catalog rewrite is implied by this documentation decision.

## Confirmed findings

HI reported that `intitle:"Index of /"` returned no useful directories, while
`intitle:Index of /` worked in the DeGoog browser.

The application does not add extra quotes. The trace is selected Dorkbook query
-> `apply_default()` -> GUI event/StringVar -> Start Scan request -> RunOptions
-> `backends.search_url()` -> HTTP GET. Boundary validation trims surrounding
whitespace. URL encoding occurs once, with query content preserved. The API
echoed the exact quoted expression in both owned-backend diagnostic responses.
Special characters, literal plus/percent/ampersand, Unicode, and intentional
quotes are covered by mocked transport tests.

Phrase quotes affect upstream matching; removing them changes the query's
meaning. This is a candidate catalog experiment, not a transport repair.
Blindly removing quotes would alter custom queries and could break Shodan syntax.
Saved defaults contain query text without built-in provenance, so a future
catalog correction must not silently rewrite those preferences.

## Live evidence and limits

Both owned URLs passed production backend detection. One quoted broad query was
sent to each API. No result URLs were followed or application DB records written.
Requests stopped after the reported upstream limits.

| Backend | Raw hits | Observed upstream state |
|---------|----------|-------------------------|
| DeGoog | 15 | Brave Search: `rate_limited`, HTTP 429; Wikipedia: OK. Sample titles were Wikipedia topics such as Refractive index and Index Librorum Prohibitorum. |
| SearXNG | 0 | Brave: too many requests; DuckDuckGo: CAPTCHA; Startpage: suspended/CAPTCHA; Karmasearch: access denied; Wikipedia: HTTP error. |

DeGoog `/api/engines` reports Brave and Wikipedia enabled by default, DuckDuckGo
disabled, and the local DeGoog engine enabled. HI's working browser engine names
are still needed to check whether browser preferences differ. DeGoog can cache
per-engine failures; the response does not establish a fresh upstream attempt.
No cache clearing, forced retries, engine overrides, or backend settings changes
were performed during this investigation.

Coverage: **1/25 shipped page-one queries attempted per backend**. The unquoted
comparison and remaining catalog queries are **NOT RUN** due to upstream limits.
This is not a claim that the current catalog works or that dequoting fixes it.

## Confirmed production fix

The production `_throttle_engines()` reader handled SearXNG metadata only.
DeGoog's HTTP-200 response could therefore hide its upstream rate-limit state.
It now recognizes `engineTimings.status == rate_limited` and HTTP status 429,
feeding existing bounded retries, productive-page backoff, and cancellation.
Malformed metadata and numeric timing values are not mistaken for rate limits.

The optional live checker now supports `--compare-unquoted`, pairing each
shipped dork with an explicitly labeled unquoted candidate. It never changes
library/default data. It separates `API_OK` from `yield` (empty, directory title
clues, or no such clues), records the backend's echoed query, and lists remaining
cases when a limit stops the run. Title clues do not verify open directories.
Representative selection uses stable keys. All-catalog comparison plans 51
checks per backend: 25 shipped, 25 candidates, and page two of the broad dork.

## Validation and unblock steps

Mocked regression checks: **223 passed** for query preservation, response metadata, bounded
retry exhaustion, partial results, cancellation, and live-check reporting.
The broader suite exposed one existing cancellation-rollup assertion using the
old label `SearXNG Query`; updated it to the current `Self-hosted Search Query`.

```bash
./venv/bin/python -m pytest -q shared/tests/test_se_dork_degoog.py shared/tests/test_se_dork_service.py shared/tests/test_dorkbook_backend_check.py shared/tests/test_dorkbook_defaults.py gui/tests/test_dorkbook_events.py experimental/se_dork/tests/test_se_dork_cancellation.py experimental/se_dork/tests/test_se_dork_maturity.py experimental/se_dork/tests/test_se_dork_runtime_policy.py shared/tests/test_se_dork_client.py
git diff --check
```

To unblock the required live comparison:

1. Identify the engine names on the useful unquoted browser results. Compare
   browser selections with the API defaults above; do not assume the same set.
2. Restore usable general-purpose engine access on each owned instance (resolve
   the reported CAPTCHA/access problem or allow upstream cooldown). A working
   instance homepage and Wikipedia-only hits do not establish directory-search
   health. Do not rotate identities or force repeated upstream attempts.
3. Set `DEGOOG_URL` and `SEARXNG_URL` to the HI-provided instances and run:

```bash
./venv/bin/python scripts/check_dorkbook_backends.py --instance-url "$DEGOOG_URL" --confirm-live --all --compare-unquoted --output /tmp/dorkbook-degoog-comparison.json
./venv/bin/python scripts/check_dorkbook_backends.py --instance-url "$SEARXNG_URL" --confirm-live --all --compare-unquoted --output /tmp/dorkbook-searxng-comparison.json
```

Expected healthy completion: `coverage_complete: true`, 51 checks, no
`UPSTREAM_LIMITED`/`UPSTREAM_ERROR` entries, and `remaining_checks: []`. Empty
results remain possible; compare per-query counts, engine sources, and title
clues before choosing new built-in wording. Actual directory verification is a
separate scan stage. A limited run exits nonzero and preserves its partial report.

README and Self-hosted Search reference were reviewed and updated for the
confirmed transport and throttling behavior. Catalog wording remains pending
live evidence. No schema, authentication, dependency, or backend config changes.

References: [SearXNG API](https://docs.searxng.org/dev/search_api),
[DeGoog API and engine selection](https://degoog-org.github.io/docs/api.html),
[Brave search operators](https://search.brave.com/help/operators),
[DeGoog engine cache implementation](https://github.com/degoog-org/degoog/blob/main/src/server/search/engine-cache.ts).
