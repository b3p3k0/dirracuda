# SearXNG Dork Module Workspace

Date: 2026-04-18
Status: Complete (C1–C6 shipped)

This folder is the working area for the new Experimental "SearXNG Dorking" module.

## Canonical Conventions

1. Workspace path: `docs/dev/searxng_dork_module/`.
2. Module name: `SearXNG Dork Module`.
3. UI/tab label: `SearXNG Dorking`.

## Locked v1 Scope

1. SearXNG only (no direct Google/DDG/Bing scraping).
2. Single manually entered SearXNG instance URL.
3. No searx.space import or auto-instance discovery in v1.
4. Support non-localhost hosts (LAN/WAN/private endpoints).
5. Default instance URL: `http://192.168.1.20:8090`.
6. Reuse existing HTTP probe path for candidate verification/classification.
7. Replace the Experimental `placeholder` tab with this module.

## Why This Direction

Direct anonymous SERP scraping from corporate search engines is fragile and frequently blocked by anti-bot controls (challenge pages, captcha, turnstile, 429s).

SearXNG gives us:
1. A stable JSON API (`/search?format=json`).
2. User-controlled deployment model (self-hosted recommended).
3. Cleaner app-side integration and clearer failure modes.

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
