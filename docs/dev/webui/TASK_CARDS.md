# Web UI Task Cards

Cards are written for Claude as DA, supervised by Codex as RA. Claude should
implement exactly one card per prompt unless HI/RA explicitly expands scope.

Every card must:

- read `docs/dev/webui/README.md`, `SPEC.md`, `ARCHITECTURE.md`,
  `SECURITY_MODEL.md`, `ASCII_SKETCHES.md`, and this file
- confirm active branch and status
- preserve existing desktop behavior
- check touched file line counts before and after
- run targeted validation
- not commit

## C0 - Contract Freeze And Baseline

Issue:
Prepare for implementation by recording the actual current contracts.

Scope:

- No product code changes.
- Create `docs/dev/webui/BASELINE_CONTRACTS.md`.

Tasks:

1. Record `git status --short --branch`.
2. Record current Experimental feature tab order from
   `gui/components/experimental_features/registry.py`.
3. Record likely touched file line counts.
4. Confirm `./dirracuda` is canonical and `gui/main.py` is shim-only.
5. Confirm existing test commands and whether `xvfb-run` is available.
6. Run baseline focused tests:
   - `./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q`
   - `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`
7. Document any pre-existing failures with exact output summary.

Acceptance:

- `BASELINE_CONTRACTS.md` exists.
- Baseline commands and results are recorded.
- Any blockers have exact unblock steps.

## C1 - Web UI Dependency And Package Scaffold

Issue:
Add the minimum package skeleton for a disabled web UI.

Scope:

- `experimental/webui/requirements-web.txt`
- new `experimental/webui/` package
- tests for import/app factory only

Tasks:

1. Add FastAPI, Uvicorn, and Jinja2 dependencies to `experimental/webui/requirements-web.txt`.
   Do not add web-only dependencies to `requirements.txt`.
2. Add `experimental/webui/__init__.py`, `experimental/webui/app.py`, `experimental/webui/server.py`.
3. Add an app factory with `/health`.
4. Add minimal config defaults but do not start service from desktop GUI.
5. Add tests that import the package and verify the app factory/health handler.
   If route tests require adding `httpx`, stop and ask RA before changing
   dependency scope.

Acceptance:

- `from experimental.webui.app import create_app` works.
- `/health` returns a safe payload without auth-sensitive details.
- No desktop GUI behavior changes.

Validation:

```bash
./venv/bin/python -m py_compile experimental/webui/__init__.py experimental/webui/app.py experimental/webui/server.py
./venv/bin/python -m pytest experimental/webui/tests -q
```

## C2 - Web UI Config And Credential Store

Issue:
The service needs secure local config and credentials before any protected UI.

Scope:

- `experimental/webui/config.py`
- `experimental/webui/auth.py`
- tests

Tasks:

1. Implement `webui.json` load/validate/default creation.
2. Implement remote-mode validation:
   - TLS enabled by default
   - localhost may explicitly disable TLS
   - non-loopback requires `remote_enabled=true` and CIDR allowlist
   - non-loopback without TLS requires explicit insecure override
3. Implement credential file handling for `webui_creds.json`.
4. Hash/verify passwords with PBKDF2-HMAC-SHA256, unique salts, constant-time
   comparison, and at least 600,000 iterations.
5. Ensure config/credential writes are atomic and mode-restricted.
6. Add tests for defaults, invalid remote configs, hash verification, wrong
   password, and permission mode best effort.

Acceptance:

- Service refuses protected startup when credentials are missing.
- Remote unsafe configs fail closed.
- TLS defaults are safe, but operator opt-out is supported explicitly.
- No secrets are logged or printed.

Validation:

```bash
./venv/bin/python -m py_compile experimental/webui/config.py experimental/webui/auth.py
./venv/bin/python -m pytest experimental/webui/tests/test_config.py experimental/webui/tests/test_auth.py -q
```

## C3 - Sessions, Login, CSRF, And Minimal Pages

Issue:
Protected browser pages need auth and CSRF before scan actions exist.

Scope:

- `experimental/webui/app.py`
- `experimental/webui/auth.py`
- templates/static
- tests

Tasks:

1. Add login/logout.
2. Add server-side session store with idle and absolute timeout.
3. Set session cookie flags correctly.
4. Add CSRF token generation and validation for mutating requests.
5. Add minimal dashboard page behind auth.
6. Add safe user-facing error handling.

Acceptance:

- Anonymous dashboard request redirects or returns 401.
- Login with valid credentials creates a session.
- Logout invalidates the session.
- Idle and absolute expiry are enforced server-side.
- Mutating requests without CSRF fail.

Validation:

```bash
./venv/bin/python -m py_compile experimental/webui/app.py experimental/webui/auth.py
./venv/bin/python -m pytest experimental/webui/tests/test_sessions.py experimental/webui/tests/test_csrf.py -q
```

## C4 - Scan Queue And CLI Subprocess Runner

Issue:
The web UI must launch scans without reimplementing scanning logic.

Scope:

- `experimental/webui/tasks.py`
- scan schemas/routes/templates
- tests

Tasks:

1. Add scan request model with strict validation.
2. Build CLI command lists for SMB/FTP/HTTP using known entrypoints.
3. Launch with `subprocess.Popen(..., shell=False)`.
4. Maintain one active scan and a FIFO queue.
5. Support a `run_probe_after_scan` option where existing CLI/GUI behavior can
   safely express it.
6. Capture stdout/stderr.
7. Parse progress conservatively. Unknown lines become log detail, not UI panic.
8. Add cancel.
9. Add tests for command construction, validation rejection, queue sequencing,
   cancel state, and no `shell=True`.

Acceptance:

- Valid scan request queues a task, including probe-after-scan intent when set.
- Invalid scan request returns 400.
- Only one task runs at a time.
- Cancel terminates the child process path.
- No shell command strings are used.

Validation:

```bash
./venv/bin/python -m py_compile experimental/webui/tasks.py experimental/webui/app.py
./venv/bin/python -m pytest experimental/webui/tests/test_tasks.py experimental/webui/tests/test_scan_routes.py -q
```

HI test needed:

- Run local web service in mock-safe mode if available.
- Queue a small scan request using test config.
- Verify status changes from queued to running to terminal state.

## C5 - Results Summaries And Database Export

Issue:
The first web UI needs useful read-only output without browser file access.

Scope:

- `experimental/webui/db.py`
- results/export routes/templates
- tests

Tasks:

1. Add protocol host summary readers for SMB/FTP/HTTP.
2. Use parameterized queries.
3. Guard optional columns/tables by runtime schema inspection.
4. Add pagination/filter inputs with explicit bounds.
5. Include share/directory summary fields where the existing DB/probe state has
   them.
6. Add copy endpoint strings.
7. Add DB export action using a controlled export path and safe filenames.
8. Do not expose the browser file explorer or target file downloads.

Acceptance:

- Results pages load against current DB.
- Results readers tolerate minimal legacy DB shapes in tests.
- Share/directory summaries appear when present and degrade cleanly when absent.
- Export creates a DB copy and returns only that artifact.

Validation:

```bash
./venv/bin/python -m py_compile experimental/webui/db.py experimental/webui/app.py
./venv/bin/python -m pytest experimental/webui/tests/test_results.py experimental/webui/tests/test_export.py -q
```

## C6 - Web UI Frontend Pass

Issue:
The server-rendered UI needs enough polish to be usable without becoming a SPA.

Scope:

- templates
- static CSS/JS
- route tests where practical

Tasks:

1. Implement pages from `ASCII_SKETCHES.md`.
2. Keep tables dense and readable.
3. Add mobile reflow for results, scan queue, and config forms.
4. Add keyboard-visible focus states.
5. Add status regions for progress/errors.
6. Avoid heavy JS and any build step.

Acceptance:

- Login, Dashboard, Scans, Results, Export, Config pages render.
- Text does not overflow in common desktop/mobile widths.
- Phone-width layouts remain useful: task rows and host/share summaries are
  readable without horizontal page scrolling.
- No unauthenticated content leak.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
```

HI test needed:

- Open local web UI in a browser.
- Verify login, navigation, scan page, results page, and config page layout.

## C7 - Desktop Experimental Web UI Tab And Control Dialog

Issue:
The desktop app needs a small, repo-consistent launch point for web UI controls.

Scope:

- `gui/components/experimental_features/registry.py`
- new `gui/components/experimental_features/webui_tab.py`
- new `gui/components/webui_control_dialog.py`
- service control helper, likely `experimental/webui/service_control.py` or a GUI utility if
  local patterns argue for it
- dashboard context wiring
- focused GUI tests

Tasks:

1. Add `Web UI` tab after `Reddit` and before `Dorkbook`.
2. Keep the tab simple: short description and `Open Web UI Control` button.
3. Add a control dialog for status/start/stop/open browser/copy URL.
4. Service start/stop must work after closing and reopening the desktop app when
   ownership is verifiable through health checks, pidfile, or systemd status.
5. Use existing theme helpers and button styles.
6. Use safe messagebox/dialog helpers where applicable.
7. Do not redesign `ExperimentalFeaturesDialog`.
8. Add tests for registry tab order and button callback.

Acceptance:

- Existing tabs still exist.
- Tab order is exactly `SearXNG`, `Reddit`, `Web UI`, `Dorkbook`, `Keymaster`.
- Clicking the button invokes the supplied context callback.
- Control dialog does not rely only on in-memory process state.
- Existing experimental tests pass.

Validation:

```bash
./venv/bin/python -m py_compile \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/webui_tab.py \
  gui/components/webui_control_dialog.py
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:

- Launch `./dirracuda`.
- Open Experimental.
- Confirm Web UI tab appears between Reddit and Dorkbook.
- Click `Open Web UI Control`.

## C8 - Remote Mode And Service Packaging

Issue:
Remote support is a v1 goal, but must be off unless deliberately configured.

Scope:

- server startup validation
- optional systemd unit template/docs
- installer hook only if explicitly approved during card start
- tests

Tasks:

1. Enforce remote startup rules in the actual server entrypoint.
2. Add explicit startup messages for bind URL and mode.
3. Add allowlist middleware/checks.
4. Add TLS default-on behavior and cert/key validation.
5. Add explicit insecure override handling for operators who disable TLS.
6. Add systemd unit template if HI confirms systemd is in v1 execution scope.
7. Document manual remote setup.

Acceptance:

- TLS is enabled by default.
- Localhost mode can explicitly disable TLS.
- Non-loopback without TLS fails unless explicit insecure override is set.
- Non-loopback without allowlist fails.
- Allowlist blocks disallowed clients in tests.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_remote_mode.py -q
```

HI test needed:

- Start localhost mode.
- Start remote mode with test TLS/allowlist on an isolated network or VM.
- Confirm disallowed source is blocked.

## C9 - Docs, Regression, And Closeout

Issue:
Runtime docs must match the shipped web UI.

Scope:

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `docs/dev/webui/*`
- tests/regression

Tasks:

1. Update root README with web UI setup, defaults, and warnings.
2. Update Technical Reference with architecture, entrypoint, config, auth, and
   scan boundary.
3. Update `LESSONS_LEARNED.md` with any new guardrails.
4. Remove stale planning claims that implementation disproved.
5. Run targeted and wider validation.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed:

- Full manual smoke: desktop tab, control dialog, web login, queue scan, view
  results, export DB, remote mode warning/config.

---

## Active Wave: C29-C35 (Experimental Feature Exposure)

This section supersedes older roadmap assumptions for active execution.
Follow one card at a time. Do not merge card scopes.

### Wave Completion Status

| Card | Status | Commit(s) | Date |
|------|--------|-----------|------|
| C29 | SHIPPED | 23faba4, 5702957 | 2026-05-24 |
| C30 | SHIPPED | 5702957 | 2026-05-24 |
| C31 | SHIPPED | 8bbdebb | 2026-05-24 |
| C32 | SHIPPED | 3c6d1a4 | 2026-05-24 |
| C33 | SHIPPED | 2caadd1 | 2026-05-24 |
| C34 | SHIPPED | b890903 | 2026-05-24 |
| C35 | IN PROGRESS | — | 2026-05-24 |

### Wave Guardrails (Mandatory)

- Preserve canonical entrypoints: `./dirracuda` (GUI) and `./venv/bin/python -m experimental.webui.server` (WebUI service).
- Keep existing auth/session/CSRF/same-origin protections on all mutating routes.
- Preserve `shell=False` subprocess safety.
- Keep `/api/scans*` compatibility while expanding shared queue behavior.
- Check touched-file line counts before and after each card.
- Line-count rubric:
  - `<=1200` excellent
  - `1201-1500` good
  - `1501-1800` acceptable
  - `1801-2000` poor
  - `>2000` unacceptable unless HI explicitly approves and docs justify it
- If any touched file exceeds 1700 lines, stop and propose modularization before continuing.
- No commits unless HI explicitly says `commit`.

### C29 - IA/Nav Cutover + Export Page

Issue:
WebUI navigation and route map still reflect flat/legacy scan surfaces.

Root cause:
Original `/scans`-centric layout predates experimental module exposure requirements.

Scope:

- Left nav IA cutover.
- Route cutover to nested `scans/*` and `extras/*` paths.
- Move export controls from `/results` to dedicated `/export` page.

Requirements:

1. `Scans` becomes toggle-only parent in left nav.
2. Children under Scans: `shodan`, `searxng`, `reddit`.
3. Canonical Shodan route is `/scans/shodan` only.
4. Add `Extras` nav group between `Export` and `Config`.
5. Children under Extras: `dorkbook`, `keymaster`.
6. `/scans` and `/extras` root routes return 404.
7. Add `/export` page and move export controls there.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
```

HI test needed:

- Login and verify nav order/groups.
- Verify dropdown behavior on desktop and phone width.

### C30 - Shared Queue Generalization (Runs + Probes)

Issue:
Queue model is scan-task specific and does not represent experimental run/probe jobs.

Root cause:
`ScanQueue` and route usage are scoped to SMB/FTP/HTTP scan submission paths.

Scope:

- Introduce shared job representation for runs + probes.
- Maintain `/api/scans*` compatibility for Shodan submit flow.

Requirements:

1. Add shared queue APIs for cross-page job visibility.
2. Include run + probe jobs in global queue snapshots.
3. Exclude promotions from queue.
4. Preserve deterministic cancellation/status behavior.
5. Preserve existing Shodan flow compatibility.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_tasks.py experimental/webui/tests/test_scan_routes.py -q
```

HI test needed:

- Submit mixed tasks from multiple pages.
- Confirm queue persistence across navigation/refresh.

### C31 - SearXNG Web Flow (Run/Results/Probe/Promote)

Issue:
No complete WebUI surface for SearXNG discovery workflow.

Root cause:
Feature remains desktop-only despite reusable service/store primitives.

Scope:

- `/scans/searxng` UI + API.
- Run/results/probe/promote behavior.
- Shared queue integration for run/probe.

Requirements:

1. Add preflight/run/results endpoints.
2. Add row + bulk probe actions.
3. Add row + bulk promote actions.
4. Enforce same-origin + CSRF on mutating endpoints.
5. Use helper text for desktop-only capabilities not in this wave.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_searxng_routes.py -q
```

HI test needed:

- Run query.
- Probe one+many rows.
- Promote one+many rows.
- Confirm run/probe queue entries appear in shared queue.

### C32 - Reddit Web Flow (Run/Results/Probe/Promote)

Issue:
No complete WebUI surface for Reddit ingestion workflow.

Root cause:
Feature remains desktop-only despite reusable service/store primitives.

Scope:

- `/scans/reddit` UI + API.
- Run/results/probe/promote behavior.
- Shared queue integration for run/probe.

Requirements:

1. Support `feed`, `search`, and `user` modes with validation parity.
2. Add row + bulk probe actions.
3. Add row + bulk promote actions.
4. Enforce same-origin + CSRF on mutating endpoints.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_reddit_routes.py -q
```

HI test needed:

- Run each mode at least once.
- Probe one+many rows.
- Promote one+many rows.
- Verify shared queue behavior.

### C33 - Dorkbook Web + Desktop Consistency

Issue:
Dorkbook web exposure is missing and persistence behavior is inconsistent across surfaces.

Root cause:
Desktop behavior uses populate-then-save semantics not aligned with desired immediate-persist contract.

Scope:

- `/extras/dorkbook` page.
- Immediate persist in web and desktop behavior alignment.

Requirements:

1. Add manage + prefill web flow.
2. Persist immediately to canonical discovery config.
3. Update desktop behavior to same contract.
4. Add helper text where desktop still has additional capabilities.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_dorkbook_routes.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_dorkbook_window.py -q
```

HI test needed:

- Apply same recipe in web and desktop.
- Confirm immediate shared config update.

### C34 - Keymaster Web MVP (Unlock + Manage + Apply)

Issue:
Keymaster day-to-day operations are desktop-only.

Root cause:
No WebUI page/API for unlock/session/key CRUD/apply path.

Scope:

- `/extras/keymaster` page.
- Unlock/manage/apply web flow.
- Explicit defer of secure-mode toggle/reset.

Requirements:

1. Implement unlock flow.
2. Implement key CRUD + apply.
3. Enforce same-origin + CSRF on mutating endpoints.
4. Do not leak key material in logs/responses.
5. Add helper text directing deferred controls to desktop.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_keymaster_routes.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_sessions.py experimental/webui/tests/test_csrf.py -q
```

HI test needed:

- Unlock.
- Add/edit/delete key.
- Apply key and verify config update.

### C35 - Docs/Parity Matrix/Lessons + Regression Closeout

Issue:
Docs and planning artifacts drift after feature/IA cutover work.

Root cause:
Existing docs describe older route and feature scope.

Scope:

- Update README, technical reference, and webui planning artifacts.
- Run regression gates and record exact outcomes.

Requirements:

1. Update `README.md` and `docs/TECHNICAL_REFERENCE.md` to implementation truth.
2. Update `docs/dev/webui/TASK_CARDS.md`, `ROADMAP.md`, `LESSONS_LEARNED.md`, `FEATURE_PARITY_MATRIX.md`.
3. Record exact pass/fail command output summaries.

Validation:

```bash
./venv/bin/python -m pytest experimental/webui/tests -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed:

- Full desktop + web smoke for nav/routes and experimental flows.

### RA Acceptance Checklist (Before Accepting Any Card)

- Card stayed within single-card scope.
- Root cause stated and plausible.
- Fix is minimal and reversible.
- No hidden redirects/shims beyond explicit card requirements.
- Validation commands run exactly as declared.
- PASS/FAIL reported honestly.
- Touched-file line-count report included.
- If behavior changed, README + `docs/TECHNICAL_REFERENCE.md` reviewed and updated.
- No commit created unless HI explicitly said `commit`.
