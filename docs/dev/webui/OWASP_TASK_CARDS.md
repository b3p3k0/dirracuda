# OWASP Task Cards (Execution Backlog)

Last updated: 2026-05-12
Mode: one card at a time, confirm -> fix -> validate -> report -> wait

## O0 - Reconcile and Freeze (PA-only, no product code)

Issue:
Freeze scope and decisions before implementation.

Tasks:
1. Build scope matrix and waiver register from interim report + current code evidence.
2. Backfill undocumented post-C9/C10+ behavior into matrix baseline.
3. Create approved plan files under `docs/dev/webui/approved_plans/OWASP_Ox_<slug>.md`.
4. Confirm startup context (entrypoint, test commands, file-size constraints).

Acceptance:
- `OWASP_SCOPE_MATRIX.md` exists with MUST/DEFER/WAIVE mapping.
- `OWASP_WAIVER_REGISTER.md` exists with review dates.
- O1-O5 approved plan files exist.

## O1 - Authentication Anti-Automation

Issue:
No brute-force protections on login.

Tasks:
1. Add persistent account+IP lockout/backoff state store durable across restart.
2. Enforce threshold, observation window, exponential backoff.
3. Keep generic auth failure responses for wrong user/wrong password/locked cases.
4. Apply in localhost and remote modes.
5. Add `auth` tuning block in `webui.json` and wire desktop/web config dialogs.

Acceptance:
- Repeated failures trigger lockout/backoff.
- Lockout state survives restart.
- Failure response remains generic (no enumeration leak).

Validation:
- `./venv/bin/python -m pytest experimental/webui/tests/test_login.py -q`
- `./venv/bin/python -m pytest experimental/webui/tests/test_config.py -q`

## O2 - Password Policy + Credential Lifecycle

Issue:
Weak password acceptance and no authenticated change-password flow.

Tasks:
1. Enforce min password length 15.
2. Enforce blocklist check against bundled top-password list.
3. Preserve passphrase-friendly policy (no forced composition rules).
4. Add authenticated CSRF-protected change-password endpoint requiring current password.
5. Update desktop credentials dialog: require current password when rotating existing creds; preserve bootstrap for first set.

Acceptance:
- Weak/common passwords rejected.
- Change-password flow requires current credential and CSRF.
- Desktop rotation path enforces current-password verification when credential already exists.

Validation:
- `./venv/bin/python -m pytest experimental/webui/tests/test_auth.py experimental/webui/tests/test_login.py -q`
- `xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q`

## O3 - Strict CSP + Security Headers

Issue:
Missing security headers and inline script/style CSP conflicts.

Tasks:
1. Add centralized response-security-header middleware.
2. Enforce strict CSP (no `unsafe-inline` scripts).
3. Refactor inline template scripts to static JS files.
4. Remove inline style attributes or replace with classes.
5. Add HSTS only for HTTPS responses.
6. Add frame defense, nosniff, referrer policy, and sensitive cache-control.

Acceptance:
- CSP, XFO, nosniff, referrer, cache headers present as expected.
- HSTS only present in HTTPS context.
- All pages render without inline script/style dependency.

Validation:
- `./venv/bin/python -m pytest experimental/webui/tests/test_pages.py experimental/webui/tests/test_login.py -q`

## O4 - Credential Store Hardening + Security Docs

Issue:
Need explicit credential-file hardening checks and threat-model/doc updates.

Tasks:
1. Enforce/verify safe credential file permissions on write and read paths.
2. Update threat model/trust boundaries and operator caveats.
3. Update `README.md` and `docs/TECHNICAL_REFERENCE.md` for new security behavior.
4. Update waiver register with final deferred/waived rationale.

Acceptance:
- Credential store file permission checks implemented and tested.
- Security model and operator docs match code behavior.

Validation:
- `./venv/bin/python -m pytest experimental/webui/tests/test_auth.py -q`

## O5 - Final Regression + Evidence Pack

Issue:
Need auditable evidence for implemented controls and residual risk.

Tasks:
1. Run targeted and full required gates.
2. Produce `docs/dev/webui/OWASP_VALIDATION_REPORT.md` with commands + PASS/FAIL.
3. Update `docs/dev/webui/LESSONS_LEARNED.md` with carry-forward guardrails.
4. Reconcile README/Technical Reference to current behavior one final time.

Required Gate Commands:
- `./venv/bin/python -m pytest experimental/webui/tests -q`
- `xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q`
- `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`
