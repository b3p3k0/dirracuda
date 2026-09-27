# Self-hosted Search Aggregators Workspace

The `Self-hosted Search` feature supports SearXNG and DeGoog. The workspace path
and internal `se_dork` / `searxng` identifiers stay unchanged for compatibility.

C7 is complete; automated checks and HI testing passed. See [WORK_NOTES.md](WORK_NOTES.md). C1–C6 and `claude_plans/`
record the original SearXNG implementation; their sidecar-only and manual
promotion assumptions were superseded by the primary-DB integration.

## Current contract

- One manually entered instance URL; no public-instance discovery or failover.
- SearXNG: `/config`, then `/search?format=json` with `pageno`.
- DeGoog: `/api/search-tabs`, then native `/api/search` with `page` and `type=web`.
- A base URL is detected once per run. Explicit search endpoint URLs also work.
- Search results share the existing classification, persistence, and probe flow.
- New runs write to the active primary DB. Legacy sidecar browsing stays available.
- Desktop Accessories, Start Scan, and Web UI use the same service.

DeGoog needs enabled web engines. No format toggle is required. Pagination stops
at 10 pages for DeGoog and 40 for SearXNG; the requested result cap remains a
ceiling. API-key-protected DeGoog search is not supported in this integration.

See [SPEC.md](SPEC.md), the [operator instructions](../../../README.md#self-hosted-search),
and the [DeGoog API reference](https://degoog-org.github.io/docs/api.html).

## Setup Notes From Live Testing

We hit this exact issue during setup:
1. `/search?q=hello` returned `200` HTML.
2. `/search?q=hello&format=json` returned `403`.

Root cause was SearXNG format policy. Non-HTML formats were not enabled.

### Required SearXNG config

In `settings.yml`, enable non-HTML output formats:

```yaml
search:
  formats:
    - html
    - json
    - csv
    - rss
```

Restart SearXNG after changing `settings.yml`.

### Validation commands

Run on the SearXNG host:

```bash
curl -sS -D - 'http://127.0.0.1:8090/search?q=hello&format=json' -o /tmp/sx.json | head -n 20
python3 - <<'PY'
import json
j=json.load(open('/tmp/sx.json'))
print('results_len=', len(j.get('results', [])))
PY
```

Run from a remote client (Dirracuda workstation):

```bash
curl -sS -D - 'http://192.168.1.20:8090/search?q=site:%2A%20intitle:%22index%20of%20/%22&format=json' -o /tmp/sx.json | head -n 20
python3 - <<'PY'
import json
j=json.load(open('/tmp/sx.json'))
print('results_len=', len(j.get('results', [])))
print('first_url=', (j.get('results') or [{}])[0].get('url'))
PY
```

Expected:
1. HTTP 200
2. `Content-Type: application/json`
3. Non-empty `results` for broad queries

## Workspace Files

- `SPEC.md`
- `ROADMAP.md`
- `TASK_CARDS.md`
- `ASCII_SKETCHES.md`
- `CLAUDE_PROMPTS.md`
- `OPEN_QUESTIONS.md`
