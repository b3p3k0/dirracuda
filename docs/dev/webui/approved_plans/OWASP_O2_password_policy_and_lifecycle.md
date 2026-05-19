# Approved Plan — OWASP O2: Password Policy + Credential Lifecycle

Prepared by: DA
Status: Approved and implemented
Revision: 5 (post-review R4 findings addressed)
Implementation completed: 2026-05-13

---

## Context

O1 (brute-force anti-automation) is complete and committed on `feature/secure-webui`. O2 closes three related gaps identified in `OWASP_SCOPE_MATRIX.md`:

1. `set_password()` accepts any string — no length floor, no common-password rejection.
2. No web endpoint for authenticated credential rotation exists.
3. The desktop credentials dialog calls `set_password()` without verifying the existing credential.

Standards grounding:
- **NIST SP 800-63B §3.1.1.2**: min 15 chars (single-factor), blocklist required, no composition rules
- **OWASP ASVS v5 V6.2.1/V6.2.3/V6.2.4/V6.2.5**: length recommendation, change requires current + new, blocklist ≥ top-3000, no composition rules
- **OWASP ASVS v5 V6.2.12**: breached-password checking — see waiver below
- **OWASP Auth Cheat Sheet**: 15-char min without MFA, require current password on change

---

## ASVS V6.2.12 Disposition (Breached-Password Checking)

**V6.2.12** requires checking submitted passwords against a set of breached passwords (e.g. HIBP breach corpus).

**Decision: DEFER with explicit waiver.**

Rationale: HIBP's breach corpus is ~10 GB of SHA-1 hashes. Bundling even a subset is impractical at this scale. The k-anonymity API path adds an outbound network dependency to the auth write path, which conflicts with the tool's offline-first architecture and raises privacy concerns (password hash prefix leakage to external service). The top-10000 common-password list satisfies V6.2.4 (common passwords) and covers the bulk of breach-overlap risk in practice.

**Waiver entry W-005** added to `docs/dev/webui/OWASP_WAIVER_REGISTER.md`:
- Scope: ASVS V6.2.12 breached-password checking
- Compensating controls: min-15 policy + top-10000 common-password blocklist (V6.2.4), account+IP lockout (O1), PBKDF2 600k iterations
- Review date: 2026-08-15 (aligned with existing waiver review cycle)
- Owner: HI + RA

---

## Review Findings Addressed (R3–R5)

**[HIGH] ASVS V6.2.12 not addressed**: Added explicit waiver entry W-005.

**[MEDIUM] Desktop rotation ambiguous with multiple credential keys**: If `len(get_credential_usernames()) != 1`, block rotation with a clear operator message. See Step 6.

**[MEDIUM] Blocklist-unavailable routed through user-validation (ValueError → 400)**: Separated into two exception types. `BlocklistUnavailableError(RuntimeError)` covers system faults and routes to 503 + operator log. `ValueError` covers policy rejections and routes to 400.

**[MEDIUM] Blocklist load underspecified for non-exists() failures**: Full read path wrapped in `try/except OSError` returning `None` on any OS-level failure.

**[MEDIUM] O2 closeout process items missing**: Added mandatory closeout section.

**[MEDIUM] `_load_blocklist()` treats any readable file as valid, even if empty or truncated**: Enforce `BLOCKLIST_MIN_SIZE = 3000` — return `None` if loaded set has fewer than 3000 entries.

**[LOW] Provenance text in pwlist.txt treated as candidate password**: Provenance moves to `docs/licenses/pwlist-provenance.txt` only.

**[LOW] ASVS V6.2.9 (≥64 chars accepted) has no regression test**: Added `test_set_password_64_chars_accepted`.

**[LOW] `_on_save_credentials_dialog` has no guard for `_multi_cred_error` state**: Added defensive guard at top of method + direct-call test.

**HI decisions resolved:**
- Nav label: **"Account"** confirmed.
- Bootstrap policy: **same policy applies from day one** confirmed; no bootstrap exception.

---

## Issue

**Gap 1 — No password policy:** `auth.py::set_password()` accepts any input.

**Gap 2 — No web credential-change endpoint:** No `/account` route or `/api/auth/change-password` endpoint exists.

**Gap 3 — Desktop dialog writes without verification:** `webui_tab.py::_on_save_credentials_dialog()` calls `set_password()` directly with no current-credential check.

---

## Root Cause

`auth.py::set_password()` was scoped to "hash and store correctly," not "validate acceptability." No web change-password surface existed; the only write path was the desktop bootstrap flow.

---

## Implementation

### Step 1 — Bundle the blocklist

**File:** `experimental/webui/pwlist.txt` (new, ~10000 lines)

Source: SecLists top-10000 common passwords (MIT license). Satisfies ASVS V6.2.4. Provenance in `docs/licenses/pwlist-provenance.txt`.

### Step 2 — Password policy in `auth.py`

Added `BlocklistUnavailableError(RuntimeError)`, `PASSWORD_MIN_LENGTH = 15`, `BLOCKLIST_MIN_SIZE = 3000`, `_load_blocklist()`, `validate_password_policy()`, and `get_credential_usernames()`. Updated `set_password()` to call `validate_password_policy()` before PBKDF2.

Exception routing:
- `BlocklistUnavailableError` — infrastructure fault → 503 (web) / operator error message (desktop)
- `ValueError` — user-facing policy rejection → 400 (web) / status error (desktop)

### Step 3 — Web change-password endpoint in `app.py`

Added `GET /account` (serves `account.html`) and `POST /api/auth/change-password` (verifies current password, validates new password, routes both exception types, returns 200/400/401/403/503).

### Step 4 — New account.html template + static JS

`experimental/webui/templates/account.html` — extends `base.html`, Change Password form, no inline scripts.
`experimental/webui/static/account.js` — CSRF-aware, POSTs to `/api/auth/change-password`, O3 CSP-compatible.

### Step 5 — Add "Account" nav link in `base.html`

Added `<li><a href="/account"...>Account</a></li>` after "Config".

### Step 6 — Desktop credentials dialog in `webui_tab.py`

Three dialog paths: bootstrap (no creds), rotation (one cred), multi-cred error (multiple creds). Rotation path shows username as read-only label, requires current password. `_on_save_credentials_dialog()` has defensive `_multi_cred_error` guard at top. Both `BlocklistUnavailableError` and `ValueError` handled separately.

### Step 7 — Tests

Added 14 tests to `test_auth.py`, 10 tests to `test_login.py`, 1 test to `test_experimental_features_dialog.py`. Updated 5 existing GUI dialog tests for new method signature. Updated existing `test_auth.py` tests to use policy-compliant passwords.

---

## Files Changed

| File | Action | Notes |
|---|---|---|
| `experimental/webui/pwlist.txt` | CREATE | Data asset; ~10000 lines |
| `docs/licenses/pwlist-provenance.txt` | CREATE | Provenance note |
| `experimental/webui/auth.py` | MODIFY | 117 → 169 lines |
| `experimental/webui/app.py` | MODIFY | ~527 → ~566 lines |
| `experimental/webui/templates/account.html` | CREATE | ~30 lines |
| `experimental/webui/static/account.js` | CREATE | ~46 lines |
| `experimental/webui/templates/base.html` | MODIFY | +Account nav link |
| `gui/components/experimental_features/webui_tab.py` | MODIFY | 861 → ~985 lines |
| `experimental/webui/tests/test_auth.py` | MODIFY | +14 new tests, updated existing |
| `experimental/webui/tests/test_login.py` | MODIFY | +10 new tests |
| `gui/tests/test_experimental_features_dialog.py` | MODIFY | +1 new test, updated 5 existing |
| `docs/dev/webui/OWASP_WAIVER_REGISTER.md` | MODIFY | +W-005 entry |

---

## Validation Gates (all passed)

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_auth.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_auth.py experimental/webui/tests/test_login.py -q
./venv/bin/python -m pytest experimental/webui/tests -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

Results: 32 auth, 37 login, 306 webui-suite, 70 GUI dialog — all passed.

---

## Risks and Assumptions

| ID | Risk | Disposition |
|---|---|---|
| R-01 | `pwlist.txt` absent at runtime | Fail-closed: `BlocklistUnavailableError` raised; routes to 503 (web) / operator message (desktop). |
| R-02 | Existing weak credentials remain verifiable | `verify_password()` unchanged. Users with pre-policy passwords can still log in and change via the new endpoint. |
| R-03 | Multiple stored credentials block desktop rotation | By design. Operator message directs to CLI management. |
| R-04 | ASVS V6.2.12 (breached-password check) not fully implemented | Explicit waiver W-005 with compensating controls. Revisited at 2026-08-15 review. |
| R-05 | SecLists license | MIT per seclists.dev; provenance in `docs/licenses/pwlist-provenance.txt`. |

---

## Standards Citations

- OWASP ASVS v5 V6.2.1: "minimum of 15 characters is strongly recommended"
- OWASP ASVS v5 V6.2.3: "password change functionality requires the user's current and new password"
- OWASP ASVS v5 V6.2.4: "checked against an available set of, at least, the top 3000 passwords"
- OWASP ASVS v5 V6.2.5: "passwords of any composition can be used, without rules limiting the type of characters permitted"
- OWASP ASVS v5 V6.2.12: breached-password checking — deferred, waiver W-005
- NIST SP 800-63B §3.1.1.2: min 15 chars (single-factor); blocklist required; no composition rules; no forced periodic rotation
- OWASP Auth Cheat Sheet: 15-char min without MFA; current password verification required for change flow
