# Claude Prompt Templates

Use these prompts one card at a time. Codex RA should paste the relevant prompt
to Claude, then review the returned diff and validation.

## Universal Header

```text
You are Claude acting as DA for Dirracuda. Codex is RA and will review your work.

Repo: /home/kevin/DEV/dirracuda
Branch: feature/secure-webui

Read before changing files:
- README.md
- CLAUDE.md
- docs/TECHNICAL_REFERENCE.md
- docs/dev/webui/README.md
- docs/dev/webui/SPEC.md
- docs/dev/webui/ARCHITECTURE.md
- docs/dev/webui/SECURITY_MODEL.md
- docs/dev/webui/ASCII_SKETCHES.md
- docs/dev/webui/TASK_CARDS.md
- docs/dev/webui/LESSONS_LEARNED.md

Operating rules:
- Implement only the named card.
- Do not commit.
- Do not change unrelated behavior.
- Preserve ./dirracuda as canonical GUI entrypoint.
- Do not make gui/main.py runnable.
- Put web-only dependencies in webui/requirements-web.txt, not requirements.txt.
- Check line counts before and after touched files.
- If any touched file exceeds 1700 lines, stop and propose modularization.
- Use subprocess argument lists with shell=False for scan execution.
- No secrets in logs or test fixtures.
- Report exact commands run and results.

Response format:
- Issue:
- Root cause / design reason:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed:
```

## C0 Prompt

```text
Use the Universal Header.

Implement C0 from docs/dev/webui/TASK_CARDS.md.

Create docs/dev/webui/BASELINE_CONTRACTS.md. Do not change product code.
Record branch/status, current Experimental tab order, canonical entrypoints,
likely touched file line counts, xvfb availability, and baseline test results.

Run:
- ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
- ./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick

If a command fails, record exact failure summary and whether it appears
pre-existing. Do not fix failures in this card.
```

## C1 Prompt

```text
Use the Universal Header.

Implement C1 from docs/dev/webui/TASK_CARDS.md.

Add the disabled web UI package scaffold and accepted dependencies only:
FastAPI, Uvicorn, Jinja2. Put them in webui/requirements-web.txt. Add a health route
and import/app-factory tests. No auth, scan launch, desktop GUI wiring, or
service control yet.
```

## C2 Prompt

```text
Use the Universal Header.

Implement C2 from docs/dev/webui/TASK_CARDS.md.

Build webui config and credential storage. Use PBKDF2-HMAC-SHA256 with at least
600,000 iterations, unique salts, and constant-time comparison. TLS is enabled
by default. Remote mode must fail closed unless non-loopback bind has explicit
remote_enabled=true and a CIDR allowlist; TLS-off remote mode requires an
explicit insecure override.
```

## C3 Prompt

```text
Use the Universal Header.

Implement C3 from docs/dev/webui/TASK_CARDS.md.

Add login/logout, server-side sessions with idle and absolute timeout, session
cookie flags, CSRF protection, and a minimal protected dashboard. Do not add scan
launch yet.
```

## C4 Prompt

```text
Use the Universal Header.

Implement C4 from docs/dev/webui/TASK_CARDS.md.

Add the scan queue and CLI subprocess runner. Use existing CLI entrypoints,
argument lists, and shell=False. Only one active scan may run at once. Include
the scan-time option to run probe on verified hosts if existing CLI behavior can
represent it safely. Include validation and tests for command construction,
queueing, cancel, and rejection of bad inputs.
```

## C5 Prompt

```text
Use the Universal Header.

Implement C5 from docs/dev/webui/TASK_CARDS.md.

Add read-only SMB/FTP/HTTP host summary pages/endpoints and database export.
Use parameterized SQL and runtime schema guards. Show share/directory summaries
where existing DB/probe data has them. Do not add the browser file explorer or
target file downloads.
```

## C6 Prompt

```text
Use the Universal Header.

Implement C6 from docs/dev/webui/TASK_CARDS.md.

Implement the server-rendered web UI pages from docs/dev/webui/ASCII_SKETCHES.md.
Keep it practical: no SPA, no build step, no decorative landing page. Mobile is
a v1 goal: phone-width layouts must remain usable. Add tests that pages render
and remain protected by auth.
```

## C7 Prompt

```text
Use the Universal Header.

Implement C7 from docs/dev/webui/TASK_CARDS.md.

Add a Web UI tab to the existing Experimental dialog registry. It must appear
between Reddit and Dorkbook. The tab has a short description and one button:
Open Web UI Control. Add the control dialog and focused tests. Service status
must survive desktop app close/reopen through health checks plus pidfile/systemd
state, not only in-memory Tk state. Do not redesign ExperimentalFeaturesDialog.
```

## C8 Prompt

```text
Use the Universal Header.

Implement C8 from docs/dev/webui/TASK_CARDS.md.

Enforce remote mode startup safety and allowlist behavior. Add service packaging
only to the degree confirmed by Codex/HI before you begin this card. TLS is on by
default, but operators can explicitly opt out. Remote mode must be disabled by
default and fail closed on unsafe config unless the insecure override is explicit
and warned.
```

## C9 Prompt

```text
Use the Universal Header.

Implement C9 from docs/dev/webui/TASK_CARDS.md.

Close out docs and regression. Update README.md and docs/TECHNICAL_REFERENCE.md
to match actual implemented behavior. Update docs/dev/webui/LESSONS_LEARNED.md.
Run the validation listed in C9 and report exact results.
```
