# Approved Plan — OWASP O3: Security Headers + Strict CSP

Card: O3 (OWASP alignment, experimental/webui)  
Role: DA (implementation only)  
Date: 2026-05-13  
Status: Approved planning doc (standalone execution spec)

## Context

O1 and O2 are complete. Web UI authentication controls now exist, but response-layer hardening is still missing:

- no centralized security-header middleware,
- multiple templates still include inline `<script>` blocks,
- multiple templates still include inline `style=` attributes.

This leaves the current UI below the desired best-effort ASVS L2 hardening posture and blocks strict CSP rollout.

Per `docs/dev/webui/OWASP_TASK_CARDS.md`, O3 is a MUST card with this scope:

1. centralized security headers,
2. strict CSP with no inline scripts,
3. inline JS refactor to static files,
4. inline style-attribute removal/replacement,
5. HTTPS-only HSTS,
6. frame defense + nosniff + referrer policy + sensitive cache behavior.

## Root Cause

The Web UI was built card-by-card for functionality first (C-series, then O1/O2). Templating patterns used inline script and style snippets for speed, and no central response-hardening middleware was introduced in those earlier cards. O3 closes that architectural gap.

## Standards Mapping

Primary standards references for this card:

- OWASP HTTP Headers Cheat Sheet  
  https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html
- OWASP CSP Cheat Sheet  
  https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html
- OWASP WSTG header-misconfiguration guidance (HSTS effectiveness over HTTPS only)  
  https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/14-Test_Other_HTTP_Security_Header_Misconfigurations
- OWASP Scope Matrix O3 requirement baseline  
  `docs/dev/webui/OWASP_SCOPE_MATRIX.md`

## Current-State Inventory (pre-O3)

Inline scripts and style attributes currently exist in templates, including:

- `experimental/webui/templates/base.html`
- `experimental/webui/templates/login.html`
- `experimental/webui/templates/dashboard.html`
- `experimental/webui/templates/scans.html`
- `experimental/webui/templates/results.html`
- `experimental/webui/templates/config.html`
- `experimental/webui/templates/account.html` (no inline script, but has inline styles)

The DA must assume strict CSP will fail until all inline script/style dependencies are removed or replaced.

## Implementation Plan

### Step 1 — Central Security-Header Middleware

File: `experimental/webui/app.py`

Add one centralized middleware that applies security headers consistently to HTML and JSON responses.

Header contract:

- `Content-Security-Policy`: strict policy, no `unsafe-inline` in `script-src`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Cache-Control`: sensitive-response no-store behavior
- `Strict-Transport-Security`: set only when request scheme is HTTPS

HSTS rule:

- Add HSTS only when `request.url.scheme == "https"`.
- Do not emit HSTS on HTTP responses.

### Step 2 — CSP Policy Definition

File: `experimental/webui/app.py`

Define a single CSP policy string in one location (constant or helper) and apply it via middleware.

Recommended baseline policy for this app:

- `default-src 'self'`
- `script-src 'self'`
- `style-src 'self'`
- `img-src 'self' data:`
- `font-src 'self'`
- `connect-src 'self'`
- `object-src 'none'`
- `base-uri 'none'`
- `frame-ancestors 'none'`
- `form-action 'self'`

Do not include `unsafe-inline` in `script-src`.

### Step 3 — Inline Script Refactor to Static JS

Files:

- `experimental/webui/templates/base.html`
- `experimental/webui/templates/login.html`
- `experimental/webui/templates/dashboard.html`
- `experimental/webui/templates/scans.html`
- `experimental/webui/templates/results.html`
- `experimental/webui/templates/config.html`
- `experimental/webui/static/*.js`

Approach:

- Move each inline script block into a static JS file in `experimental/webui/static/`.
- Keep responsibilities page-local where practical (e.g., `login.js`, `dashboard.js`, etc.).
- Keep existing `prefs.js` and `account.js` behavior intact.
- Load new scripts with `<script src="/static/<name>.js"></script>`.

### Step 4 — Remove Inline Style Attributes

Files:

- `experimental/webui/templates/*.html` (only touched templates)
- `experimental/webui/static/style.css`

Approach:

- Replace inline `style=` attributes with semantic CSS class names.
- Add required classes to `style.css`.
- Avoid visual regressions; preserve layout intent for desktop and mobile.

### Step 5 — Sensitive Cache-Control Behavior

File: `experimental/webui/app.py`

Apply `Cache-Control` for dynamic/sensitive responses to reduce credential/session data persistence in browser/proxy caches.

Implementation expectation:

- no-store behavior on authenticated HTML pages and authenticated JSON API responses,
- no-store behavior on login and account-related responses,
- no requirement to no-store immutable static assets.

### Step 6 — Tests for Headers + CSP Compatibility

Files (expected):

- `experimental/webui/tests/test_pages.py`
- `experimental/webui/tests/test_login.py`
- optionally a focused header test module if needed

Coverage requirements:

1. Required headers are present on representative HTML routes.
2. Required headers are present on representative JSON/API routes.
3. HSTS is present for HTTPS test client requests.
4. HSTS is absent for HTTP requests.
5. Pages still render after inline script/style migration.
6. No route relies on inline script execution to function at baseline.

## Files to Change (Expected)

| File | Change Type | Why |
|---|---|---|
| `experimental/webui/app.py` | MODIFY | Central middleware, CSP definition, HSTS gating, cache behavior |
| `experimental/webui/templates/base.html` | MODIFY | Remove inline scripts/styles, static JS include |
| `experimental/webui/templates/login.html` | MODIFY | Remove inline script/style |
| `experimental/webui/templates/dashboard.html` | MODIFY | Remove inline scripts/styles |
| `experimental/webui/templates/scans.html` | MODIFY | Remove inline script/style |
| `experimental/webui/templates/results.html` | MODIFY | Remove inline script/style |
| `experimental/webui/templates/config.html` | MODIFY | Remove inline script/style |
| `experimental/webui/templates/account.html` | MODIFY (if needed) | Remove remaining inline style usage |
| `experimental/webui/static/style.css` | MODIFY | Class replacements for removed inline styles |
| `experimental/webui/static/*.js` | CREATE/MODIFY | Extracted script logic from templates |
| `experimental/webui/tests/test_pages.py` | MODIFY | Page/header assertions post-refactor |
| `experimental/webui/tests/test_login.py` | MODIFY | Header and HTTPS/HTTP behavior assertions |

No GUI product code changes are expected in O3.

## Validation Plan

Required commands:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py experimental/webui/tests/test_login.py -q
./venv/bin/python -m pytest experimental/webui/tests -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

Pass criteria:

- all three commands pass,
- no pre-existing pass-to-fail regressions in web UI tests,
- no regressions in desktop experimental dialog tests.

## Acceptance Criteria

O3 is complete only if all conditions below are met:

1. Security headers are applied centrally and appear on HTML/API responses.
2. CSP is strict and does not use inline scripts (`script-src` excludes `unsafe-inline`).
3. No inline `<script>` blocks remain in web UI templates.
4. Inline `style=` usage in touched templates is removed/replaced by CSS classes.
5. HSTS appears on HTTPS responses only, never on HTTP responses.
6. Page behavior and rendering remain functional after refactor.
7. Required test gates pass.

## Risks and Mitigations

| ID | Risk | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|---|
| O3-R1 | CSP blocks required JS after inline migration | Medium | High | Move scripts to static files, add route-level tests for page functionality | Low |
| O3-R2 | Header middleware unintentionally affects static/download behavior | Low | Medium | Scope cache/header logic carefully; include export route checks | Low |
| O3-R3 | HSTS emitted on HTTP by mistake | Low | Medium | Gate strictly on request scheme and assert both HTTP/HTTPS test cases | Low |
| O3-R4 | CSS refactor causes layout regressions | Medium | Medium | Keep class mapping minimal and validate page renders across routes | Low |

## Out of Scope

Do not implement O4/O5 work in this card:

- credential-store permission hardening changes beyond current O3 scope,
- waiver lifecycle changes other than O3 references,
- full final evidence pack (`OWASP_VALIDATION_REPORT.md`) reserved for O5.

## Rollback Plan

If O3 fails validation or introduces unstable behavior:

1. Revert O3 template/static/header commits for this card only.
2. Confirm routes still render with prior behavior.
3. Re-run:
   - `./venv/bin/python -m pytest experimental/webui/tests/test_pages.py experimental/webui/tests/test_login.py -q`
   - `./venv/bin/python -m pytest experimental/webui/tests -q`
4. Re-attempt O3 in smaller sub-steps (middleware first, then per-template JS/CSS migration).

## Mandatory O3 Closeout

Before moving to O4:

1. Update `README.md` with finalized O3 security-header/CSP behavior.
2. Update `docs/TECHNICAL_REFERENCE.md` with exact header contract and HTTPS-only HSTS behavior.
3. Append O3 carry-forwards to `docs/dev/webui/LESSONS_LEARNED.md`.
4. Confirm docs match code behavior (no aspirational wording).

## DA Output Format (for execution run)

DA must report using:

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + exact steps)
