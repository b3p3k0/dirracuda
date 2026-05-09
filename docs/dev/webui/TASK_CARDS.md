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

- `requirements-web.txt`
- new `webui/` package
- tests for import/app factory only

Tasks:

1. Add FastAPI, Uvicorn, and Jinja2 dependencies to `requirements-web.txt`.
   Do not add web-only dependencies to `requirements.txt`.
2. Add `webui/__init__.py`, `webui/app.py`, `webui/server.py`.
3. Add an app factory with `/health`.
4. Add minimal config defaults but do not start service from desktop GUI.
5. Add tests that import the package and verify the app factory/health handler.
   If route tests require adding `httpx`, stop and ask RA before changing
   dependency scope.

Acceptance:

- `from webui.app import create_app` works.
- `/health` returns a safe payload without auth-sensitive details.
- No desktop GUI behavior changes.

Validation:

```bash
./venv/bin/python -m py_compile webui/__init__.py webui/app.py webui/server.py
./venv/bin/python -m pytest webui/tests -q
```

## C2 - Web UI Config And Credential Store

Issue:
The service needs secure local config and credentials before any protected UI.

Scope:

- `webui/config.py`
- `webui/auth.py`
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
./venv/bin/python -m py_compile webui/config.py webui/auth.py
./venv/bin/python -m pytest webui/tests/test_config.py webui/tests/test_auth.py -q
```

## C3 - Sessions, Login, CSRF, And Minimal Pages

Issue:
Protected browser pages need auth and CSRF before scan actions exist.

Scope:

- `webui/app.py`
- `webui/auth.py`
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
./venv/bin/python -m py_compile webui/app.py webui/auth.py
./venv/bin/python -m pytest webui/tests/test_sessions.py webui/tests/test_csrf.py -q
```

## C4 - Scan Queue And CLI Subprocess Runner

Issue:
The web UI must launch scans without reimplementing scanning logic.

Scope:

- `webui/tasks.py`
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
./venv/bin/python -m py_compile webui/tasks.py webui/app.py
./venv/bin/python -m pytest webui/tests/test_tasks.py webui/tests/test_scan_routes.py -q
```

HI test needed:

- Run local web service in mock-safe mode if available.
- Queue a small scan request using test config.
- Verify status changes from queued to running to terminal state.

## C5 - Results Summaries And Database Export

Issue:
The first web UI needs useful read-only output without browser file access.

Scope:

- `webui/db.py`
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
./venv/bin/python -m py_compile webui/db.py webui/app.py
./venv/bin/python -m pytest webui/tests/test_results.py webui/tests/test_export.py -q
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
./venv/bin/python -m pytest webui/tests/test_pages.py -q
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
- service control helper, likely `webui/service_control.py` or a GUI utility if
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
./venv/bin/python -m pytest webui/tests/test_remote_mode.py -q
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
./venv/bin/python -m pytest webui/tests -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed:

- Full manual smoke: desktop tab, control dialog, web login, queue scan, view
  results, export DB, remote mode warning/config.
