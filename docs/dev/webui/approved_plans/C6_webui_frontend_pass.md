# C6 – Web UI Frontend Pass

## Context

C5 delivered `webui/db.py`, results/export routes, `results.html`, tests, and doc updates on `feature/secure-webui`. The four existing templates are functional but bare: no shared layout, no CSS, no sidebar nav, no config page. C6 brings all pages into line with `docs/dev/webui/ASCII_SKETCHES.md` — shared layout with nav, readable tables, mobile reflow at ~390px, keyboard-visible focus states, and status/error regions — without adding any framework or build step.

## Scope

**New files:**
- `webui/static/style.css` — all CSS (single file; no framework, no CDN)
- `webui/templates/base.html` — shared Jinja2 layout (nav sidebar, csrf meta, logout script)
- `webui/templates/config.html` — config settings page
- `webui/tests/test_pages.py` — page-render and auth-protection tests

**Modified files:**
- `webui/app.py` — mount `StaticFiles` at `/static/`, add `GET /config` + `POST /config`
- `webui/templates/login.html` — link to `style.css`, style the login form
- `webui/templates/dashboard.html` — extend `base.html`, implement dashboard ASCII layout
- `webui/templates/scans.html` — extend `base.html`, protocol checkboxes, queue table
- `webui/templates/results.html` — extend `base.html`, share summary section, mobile card reflow

## Step-by-step implementation

### 1. `webui/static/style.css`

Create the `webui/static/` directory and write a single CSS file. Sections:

1. **CSS variables** — `--bg`, `--bg2`, `--border`, `--text`, `--text-dim`, `--accent`, `--error`, `--ok`; use a dark neutral palette to match the tool's existing Tk theme.
2. **Reset / base** — box-sizing, margin/padding zero, body background/color/font.
3. **Focus states** — `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` globally. No `:focus` suppression.
4. **Layout** — `.layout` is a flex row. `.nav` is a fixed-width (160px) left column. `.main` fills the rest. No JS needed.
5. **Nav styles** — vertical list, no bullets, `.nav-brand` bold, `.nav-user` small at bottom.
6. **Login page** — `.login-wrap` centers the form on a standalone page (no sidebar).
7. **Tables** — compact padding (`4px 8px`), `border-collapse: collapse`, `thead` with a bottom border, `tbody tr:hover` highlight, `overflow-x: auto` wrapper.
8. **Buttons** — minimal, styled consistently. `.btn-link` for inline text-link buttons (logout).
9. **Status regions** — `.status-ok`, `.status-error`, `.status-warn`, `.status-info` classes with distinct left-border colors and `role="status"` / `aria-live` usage in HTML.
10. **Mobile breakpoint `@media (max-width: 480px)`:**
    - `.layout` switches to `flex-direction: column`
    - `.nav` becomes a horizontal top bar
    - Results table rows replaced by `.card` elements (CSS `.card { display: block; margin-bottom: 8px; padding: 8px; border: 1px solid var(--border); }`)
    - Form inputs and selects go `width: 100%`

Estimated: ~220 lines.

### 2. `webui/templates/base.html`

Jinja2 base template:
- `<head>`: charset, viewport, `<meta name="csrf-token">`, `{% block title %}`, stylesheet link to `/static/style.css`.
- `<body><div class="layout">`: left `.nav` with brand, links (Dashboard, Scans, Results, Export→/results, Config), and `nav-user` showing `{{ session.username }} | Logout` when session present.
- `<main class="main">{% block content %}{% endblock %}</main>`
- Logout button uses `fetch('/logout', ...)` via a small inline `<script>` block.
- `{% block scripts %}{% endblock %}` before `</body>`.

Estimated: ~55 lines.

### 3. `webui/templates/login.html`

Extend **nothing** (login has no nav sidebar). Instead:
- Add `<link rel="stylesheet">` for `/static/style.css`.
- Wrap form in `<div class="login-wrap">`.
- Match ASCII sketch: `<h1>Dirracuda Web UI</h1>`, label+input pairs for username/password, `[Sign in]` button.
- Keep the existing fetch-based submit script unchanged.

Before: 50 lines → After: ~60 lines.

### 4. `webui/templates/dashboard.html`

`{% extends "base.html" %}`, `{% block content %}`:
- Service section: mode string (Localhost only / Remote) and URL `http://{{ cfg.bind_address }}:{{ cfg.port }}` — pass `cfg` from the route context.
- Active Scan: show "none" if `qs.active` is None, or show task ID + protocol + status if running.
- Recent Tasks table: Time / Protocol / Status — populated from `qs.queued` list. JS polls `/api/scans/{task_id}` for the active task status every 3 s; stops polling on terminal state.

The `/dashboard` route needs to pass `cfg` and queue summary to the template. Use the existing `ScanQueue.queue_status()` method ([`webui/tasks.py:475`](webui/tasks.py#L475)) which returns `{"active": task_dict|None, "queued": [task_dict, ...]}`. No new method needed.

Minimal route change:
```python
queue = request.app.state.scan_queue
cfg_ = request.app.state.cfg
return templates.TemplateResponse(request, "dashboard.html", {
    "session": session, "cfg": cfg_, "qs": queue.queue_status()
})
```

Before: 27 lines → After: ~90 lines.

### 5. `webui/templates/scans.html`

`{% extends "base.html" %}`, `{% block content %}`:
- Protocol checkboxes (SMB / FTP / HTTP), all checked by default — matching ASCII sketch.
- Country input (free text, comma-separated).
- Max results input (default 100).
- Rescan: two checkboxes (all / failed) — note: current CLI doesn't expose max-results/rescan, so these fields are present in the UI but the JS simply omits unsupported fields from the API payload (no breakage).
- `[x] Run probe on verified hosts after scan` checkbox — **SMB only**. `ScanRequest` validates that `run_probe_after_scan` is only `true` when `protocol == "smb"` ([`webui/tasks.py:338`](webui/tasks.py#L338)). The JS must enforce this: when iterating checked protocols, send `run_probe_after_scan: probeChecked && proto === 'smb'` for each request. FTP and HTTP always get `false`.
- `[Queue Scan]` button — JS iterates over checked protocols and posts one `ScanRequest` per protocol.
- Queue table: ID / Protocol / State / Progress / Action, populated by polling `/api/scans/{task_id}` for each submitted task. A simple JS array tracks submitted task IDs; polling refreshes every 3 s.
- Status region `<div id="status" role="status" aria-live="polite">`.

The `ScanRequest` model accepts one `protocol` at a time — the existing API is unchanged.

Before: 85 lines → After: ~140 lines.

### 6. `webui/templates/results.html`

`{% extends "base.html" %}`, `{% block content %}`:
- Protocol tabs (SMB / FTP / HTTP) — keep current button-based design, add active class via JS.
- Filter bar: country input, Load/Prev/Next buttons.
- Host table with dynamic columns (existing JS kept).
- **Share summary section** (new): `<div id="share-summary">` below the table; clicking a row populates it via JS using the actual API field names:
  - SMB: `r.share_names` (array), `r.accessible_shares` (count) — [`webui/db.py:126`](webui/db.py#L126)
  - FTP: `r.accessible_dirs` (count), `r.anon_accessible` (bool) — [`webui/db.py:205`](webui/db.py#L205)
  - HTTP: `r.dir_count`, `r.file_count` — [`webui/db.py:303`](webui/db.py#L303)
- **Mobile card reflow**: add `data-label` attributes to each `<td>` so CSS can show them as card rows. At `max-width: 480px` CSS hides the `<thead>` and switches `<tr>` to `display: block` using `.results-table tr` + `[data-label]::before { content: attr(data-label) }`. This is the standard CSS-only responsive table technique.
- Export button and status kept in place.

Before: 195 lines → After: ~260 lines (well under 1700).

### 7. `webui/templates/config.html`

`{% extends "base.html" %}`, `{% block content %}`:
- Form fields matching ASCII sketch: bind address, port, remote access toggle, TLS enabled, allow insecure remote, TLS cert/key paths, allowlist (comma-separated CIDRs), idle timeout (minutes), absolute timeout (hours).
- When remote access checkbox changes to enabled, show an inline `<p class="status-warn">` warning.
- `[Save]` button — JS fetch POST to `/config` with JSON body and CSRF header.
- Status region for save success/error.

Estimated: ~100 lines.

### 8. `webui/app.py` — new routes + StaticFiles

**Additions:**

```python
from fastapi.staticfiles import StaticFiles
_STATIC_DIR = Path(__file__).parent / "static"
# inside create_app(), after templates line:
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
```

**GET /config:**
```python
@app.get("/config", response_class=HTMLResponse)
async def _config_page(request, session=Depends(get_session)):
    return templates.TemplateResponse(request, "config.html", {
        "session": session, "cfg": request.app.state.cfg
    })
```

**POST /config:**
- Origin check + CSRF validation.
- Parse JSON body into a `_ConfigUpdateRequest` Pydantic model (fields: bind_address, port, remote_enabled, tls_enabled, tls_cert, tls_key, tls_allow_insecure_remote, allowed_cidrs `list[str]`, session_timeout_idle_min `int`, session_timeout_absolute_hr `int`).
- **Explicit unit conversion** ([`webui/config.py:42`](webui/config.py#L42) stores seconds): `cfg.session_timeout_idle = body.session_timeout_idle_min * 60`, `cfg.session_timeout_absolute = body.session_timeout_absolute_hr * 3600`. UI labels must say "minutes" and "hours" respectively so users never guess the unit.
- Construct `WebUIConfig`, call `validate(cfg)`, call `save_config(cfg, path=request.app.state.config_path)`.
- On success: `JSONResponse({"ok": True, "note": "Changes take effect on restart."})`.
- On `WebUIConfigError`: `JSONResponse({"error": str(exc)}, status_code=400)`.

**Test isolation for config writes**: `create_app()` gains an optional `config_path=None` parameter stored in `app.state.config_path`. The POST handler passes this to `save_config()`. Tests set `config_path=tmp_path / "webui.json"` — no monkeypatching of global state required and real user config (`~/.dirracuda/conf/webui.json`) is never touched during tests.

Import additions: `StaticFiles`, `save_config`, `validate`, `WebUIConfigError` from `webui.config`.

Before: 278 lines → After: ~330 lines.

### 9. `webui/tests/test_pages.py`

Tests:

```
test_login_page_renders_unauthenticated
test_health_unprotected
test_dashboard_redirects_unauthenticated
test_scans_redirects_unauthenticated
test_results_redirects_unauthenticated
test_config_redirects_unauthenticated
test_dashboard_renders_authenticated
test_scans_renders_authenticated
test_results_renders_authenticated
test_config_renders_authenticated
test_config_post_requires_csrf
test_config_post_saves_valid_config          # verify idle_min*60 and abs_hr*3600 conversion
test_config_post_unit_conversion             # submit idle_min=30,abs_hr=8 → assert idle=1800,abs=28800
test_config_post_rejects_invalid_bind
test_config_post_writes_to_tmp_path_not_home # confirm real ~/.dirracuda path never touched
```

Fixtures: same pattern as `test_login.py` — `creds(tmp_path)`, `cfg_no_tls`, `client(creds, cfg_no_tls, tmp_path)` passes `config_path=tmp_path / "webui.json"` to `create_app()`, `logged_in_client`. No new dependencies; no writes to real user config.

Estimated: ~130 lines.

## Critical files

| File | Action | Before | After (est.) |
|------|--------|--------|--------------|
| `webui/app.py` | modify | 278 | ~330 |
| `webui/templates/login.html` | modify | 50 | ~60 |
| `webui/templates/dashboard.html` | modify | 27 | ~90 |
| `webui/templates/scans.html` | modify | 85 | ~140 |
| `webui/templates/results.html` | modify | 195 | ~260 |
| `webui/templates/base.html` | create | — | ~55 |
| `webui/templates/config.html` | create | — | ~100 |
| `webui/static/style.css` | create | — | ~220 |
| `webui/tests/test_pages.py` | create | — | ~130 |

All under the 1700-line rubric.

## Reused existing functions

- `webui.config.save_config(cfg, path=None)` — [`webui/config.py:285`](webui/config.py#L285)
- `webui.config.validate(cfg)` — [`webui/config.py:181`](webui/config.py#L181)
- `webui.config.WebUIConfigError` — [`webui/config.py:23`](webui/config.py#L23)
- `webui.dependencies.get_session`, `same_origin`, `validate_csrf` — existing dep injection
- `ScanQueue.get_task()`, `ScanQueue.submit()`, `ScanQueue.queue_status()` — existing queue API ([`webui/tasks.py:475`](webui/tasks.py#L475))
- `Jinja2Templates` already configured in `create_app()`

## Auth preservation

All new routes (`/config` GET and POST) use `Depends(get_session)` which raises `AuthRequired` → redirects to `/login`. No unauthenticated content leak.

## Validation

Primary:
```bash
./venv/bin/python -m pytest webui/tests/test_pages.py -q
```

Risk-based regressions (shared templates/nav touched):
```bash
./venv/bin/python -m pytest webui/tests/test_login.py webui/tests/test_scan_routes.py webui/tests/test_results.py webui/tests/test_export.py -q
```

## HI test steps

1. `./venv/bin/python -m webui.server` → open `http://127.0.0.1:5480/login`
2. Login → confirm nav sidebar appears (Dashboard / Scans / Results / Export / Config)
3. Visit /scans → confirm protocol checkboxes, queue table visible at full width
4. Resize browser to ~390px → confirm no horizontal scroll on Scans, Results, Config
5. Visit /results, load SMB tab → confirm host table renders; confirm card layout at 390px
6. Visit /config → confirm form fields pre-filled with current config values
7. Tab through all form inputs → confirm focus ring visible on each
8. Submit a bad config value → confirm error message in status region
9. Logout → confirm redirect to login
