# Owned backend checks — 2026-09-27

Both supplied instances were reachable through production backend detection.
The API-only run requested every shipped Self-hosted Search dork (25 page-one
queries) and page two of the broad default on each backend. All 52 responses
were HTTP 200 with a results list. No returned target URLs were fetched,
no files were downloaded, and no application results database was written.

**Live yield: LIMITED / unverified.** SearXNG returned zero results and
reported Brave rate limiting, DuckDuckGo/Startpage CAPTCHA, Karmasearch access
denial, and Wikipedia HTTP errors. DeGoog returned 0–15 results, with the
nonempty rows attributed to Wikipedia. A diagnostic response to the same broad
query exposed `engineTimings`: Brave Search `status: rate_limited`,
`httpStatus: 429`, `errorReason: Brave Search upstream returned HTTP 429`.

The initial harness version recorded HTTP/shape success but did not classify
SearXNG engine failures as failures and did not inspect DeGoog `engineTimings`.
The fast catalog runs completed before that diagnostic was incorporated.
No further live requests were made after inspection. The committed harness
now records generic upstream errors, stops on CAPTCHA/429/rate-limit signals
in either metadata format, and preserves HTTP Retry-After. Mock regressions
cover those cases. Initial PASS labels therefore mean response shape only,
not useful search yield. Results below retain the observed counts and timing.

## Commands

Executed with the supplied URLs; placeholders below keep private LAN names out
of tracked documentation. Set each variable to the corresponding owned URL.

```bash
./venv/bin/python scripts/check_dorkbook_backends.py --instance-url "$SEARXNG_URL" --confirm-live --all --output /tmp/dorkbook-searxng.json
./venv/bin/python scripts/check_dorkbook_backends.py --instance-url "$DEGOOG_URL" --confirm-live --all --output /tmp/dorkbook-degoog.json
```

Detection used `/config` (SearXNG) and `/api/search-tabs` (DeGoog). Search used
`/search?format=json&q=...&pageno=...` and `/api/search?type=web&q=...&page=...`.
The additional DeGoog diagnostic used the broad default on page 1 and inspected
only response keys/metadata, not result URLs or contents.

## Observed responses

All SearXNG rows had the upstream error families described above. DeGoog
per-query engine errors were not retained by the initial harness; the extra
broad-query diagnostic establishes throttling, not a per-query error census.

| Query | Page | SearXNG count / seconds | DeGoog count / seconds |
|---|---:|---:|---:|
| `intitle:"Index of /"` | 1 | 0 / 0.476 | 15 / 0.215 |
| `intitle:"Index of /" "ebooks"` | 1 | 0 / 0.119 | 0 / 0.244 |
| `intitle:"Index of /" ".epub"` | 1 | 0 / 0.11 | 0 / 0.126 |
| `intitle:"Index of /" ".mobi"` | 1 | 0 / 0.468 | 0 / 0.133 |
| `intitle:"Index of /" ".pdf"` | 1 | 0 / 0.106 | 15 / 0.175 |
| `intitle:"Index of /" ".cbz"` | 1 | 0 / 0.098 | 0 / 0.131 |
| `intitle:"Index of /" ".m4b"` | 1 | 0 / 0.854 | 0 / 0.149 |
| `intitle:"Index of /" "movies"` | 1 | 0 / 0.094 | 15 / 0.195 |
| `intitle:"Index of /" "series"` | 1 | 0 / 0.108 | 15 / 0.216 |
| `intitle:"Index of /" ".mkv"` | 1 | 0 / 0.336 | 0 / 0.139 |
| `intitle:"Index of /" "documentaries"` | 1 | 0 / 0.092 | 0 / 0.372 |
| `intitle:"Index of /" "music"` | 1 | 0 / 0.096 | 15 / 0.207 |
| `intitle:"Index of /" ".flac"` | 1 | 0 / 0.388 | 1 / 0.176 |
| `intitle:"Index of /" ".mp3"` | 1 | 0 / 0.094 | 2 / 0.13 |
| `intitle:"Index of /" "photos"` | 1 | 0 / 0.093 | 12 / 0.211 |
| `intitle:"Index of /" ".jpg"` | 1 | 0 / 0.332 | 0 / 0.141 |
| `intitle:"Index of /" "wallpapers"` | 1 | 0 / 0.096 | 0 / 0.13 |
| `intitle:"Index of /" ".svg"` | 1 | 0 / 0.094 | 1 / 0.151 |
| `intitle:"Index of /" ".iso"` | 1 | 0 / 0.235 | 15 / 0.148 |
| `intitle:"Index of /" "releases"` | 1 | 0 / 0.097 | 5 / 0.209 |
| `intitle:"Index of /" "mods"` | 1 | 0 / 0.1 | 0 / 0.181 |
| `intitle:"Index of /" ".stl"` | 1 | 0 / 0.178 | 0 / 0.163 |
| `intitle:"Index of /" "downloads"` | 1 | 0 / 0.101 | 1 / 0.131 |
| `intitle:"Directory listing"` | 1 | 0 / 0.093 | 0 / 0.13 |
| `intitle:"Index of"` | 1 | 0 / 0.945 | 15 / 0.225 |
| `intitle:"Index of /"` | 2 | 0 / 0.085 | 15 / 0.136 |

## Retest when upstream recovers

Allow upstream cooldown or resolve the configured engine access/CAPTCHA issue
on the owned instance. Do not rotate identities to evade limits. Run the
commands above again. Expected healthy result: backend identified, HTTP 200
with a results list and no upstream-error metadata. Zero results are still a
valid query outcome. The harness exits nonzero and reports errors while the
instance is limited; this does not block HI testing of the library or defaults.

References: [SearXNG API](https://docs.searxng.org/dev/search_api.html),
[DeGoog API](https://degoog-org.github.io/docs/api.html).
