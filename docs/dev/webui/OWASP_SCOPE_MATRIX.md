# OWASP Scope Matrix (Web UI)

Last updated: 2026-05-12
Owner: VanDelay Security Group / Dirracuda Web UI
Status: scope frozen for OWASP alignment wave O1-O5

## Purpose

This matrix maps each finding from `docs/dev/webui/OWASP_interim_report.md` to:

- current code evidence,
- disposition (`MUST`, `DEFER`, `WAIVE`),
- implementation target card,
- validation evidence.

This document is the baseline for execution and final audit notes.

## Disposition Summary

| Finding / Gap | Current Evidence | Disposition | Target |
|---|---|---|---|
| Login brute-force controls absent | Login handler has direct `verify_password` check with no attempt tracking: `experimental/webui/app.py` | MUST | O1 |
| Generic login response hygiene | Already generic 401 message in login failure path: `experimental/webui/app.py` | MUST (retain + strengthen timing behavior) | O1 |
| Password min-length + blocklist | `set_password()` currently accepts weak passwords: `experimental/webui/auth.py` | MUST | O2 |
| Authenticated password change requiring current password | No web route for credential rotation currently | MUST | O2 |
| Desktop credential rotation current-password gate | Desktop credentials dialog currently writes new credential directly: `gui/components/experimental_features/webui_tab.py` | MUST | O2 |
| Security headers (CSP/HSTS/XFO/nosniff/referrer/cache) | No centralized security-header middleware in app | MUST | O3 |
| Strict CSP (`script-src 'self'`) | Templates include inline `<script>` and inline `style=` attributes: `experimental/webui/templates/*.html` | MUST | O3 |
| Credential file permission enforcement | Atomic writes already set `0600` via `_atomic_write_json`: `experimental/webui/config.py`; needs explicit verification/read-path checks | MUST (hardening/verification) | O4 |
| Architecture + threat model governance docs | Web UI docs exist but OWASP-alignment-specific architecture/threat boundary update is incomplete | MUST | O4 |
| Credential store migration to keychain/encryption | Local JSON hashed credentials at `~/.dirracuda/conf/webui_creds.json` | DEFER (waiver + compensating controls) | O4 docs |
| Concurrent session management UI | Session store supports current-session lifecycle only; no user-visible session list/revocation | DEFER (waiver) | O4 docs |
| Session persistence across restart | Session store is in-memory only: `experimental/webui/sessions.py` | DEFER (waiver) | O4 docs |
| MFA requirement from ASVS L2 | Product decision: no MFA in this program | WAIVE (explicit) | O4 docs |

## Backfill: Post-C9/C10+ Undocumented Behavior (Reality Sync)

The following behaviors are implemented in code but were not fully captured in earlier CX card summaries and must be treated as part of current baseline:

1. Web UI preference storage consent + allowlisted localStorage keys (`experimental/webui/static/prefs.js`, templates).
2. Dashboard Shodan balance endpoint + cache behavior (`experimental/webui/shodan_balance.py`, `experimental/webui/app.py`).
3. Results details accordion and API surface (`/api/results/details`) in `experimental/webui/app.py` and `experimental/webui/templates/results.html`.
4. Remote-mode allowlist middleware with `remote_enabled` gate (`experimental/webui/app.py`).
5. Desktop Web UI tab with service launch/status/start/stop/restart flows (`gui/components/experimental_features/webui_tab.py`, `experimental/webui/service_control.py`).
6. Server CLI overrides for `--host`, `--port`, `--config` (`experimental/webui/server.py`).

These behaviors are included in OWASP execution testing to avoid drift between docs and runtime behavior.

## Standards Inputs (Current)

Primary standards references used for scope decisions:

- OWASP ASVS v5 V6 authentication requirements (password lifecycle, anti-automation, MFA requirement and rationale):
  - https://raw.githubusercontent.com/OWASP/ASVS/master/5.0/en/0x15-V6-Authentication.md
- OWASP Authentication Cheat Sheet (generic failures, lockout threshold/window, exponential lockout guidance):
  - https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Authentication_Cheat_Sheet.md
- OWASP HTTP Headers Cheat Sheet (CSP, HSTS, XFO, nosniff, referrer policy, cache-control):
  - https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/HTTP_Headers_Cheat_Sheet.md
- OWASP CSP Cheat Sheet (header-based CSP delivery and strict policy considerations):
  - https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Content_Security_Policy_Cheat_Sheet.md
- OWASP WSTG header misconfiguration notes (HSTS effectiveness only over HTTPS):
  - https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/14-Test_Other_HTTP_Security_Header_Misconfigurations
- NIST SP 800-63B (blocklist requirement, no composition rules, rate-limiting requirement):
  - https://pages.nist.gov/800-63-4/sp800-63b.html

## Exit Criteria

1. Every MUST item has implemented code + tests + validation evidence in `OWASP_VALIDATION_REPORT.md`.
2. Every DEFER/WAIVE item is listed in `OWASP_WAIVER_REGISTER.md` with review date and compensating controls.
3. `README.md` and `docs/TECHNICAL_REFERENCE.md` reflect actual security behavior.
