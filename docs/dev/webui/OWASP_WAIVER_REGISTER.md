# OWASP Waiver Register (Web UI)

Last updated: 2026-05-13
Owner: VanDelay Security Group
Review cadence: quarterly or at next auth-scope expansion, whichever comes first

## Waiver Rules

A waiver is valid only when all fields are present:

- scope item,
- rationale,
- risk statement,
- compensating controls,
- review date,
- owner.

## Active Waivers

| ID | Scope Item | Decision | Rationale | Risk | Compensating Controls | Review Date | Owner |
|---|---|---|---|---|---|---|---|
| W-001 | MFA for web UI auth | WAIVE | Product model is single-admin/self-hosted operator workflow; MFA intentionally out-of-scope for this wave. | Single-factor auth raises takeover risk if password is compromised. | Strong password policy (min 15 + blocklist), account+IP lockout/backoff, CSRF, strict origin checks, secure session cookie flags, TLS-by-default remote mode. | 2026-08-15 | HI + RA |
| W-002 | Credential store keychain/encryption migration | DEFER | Current model stores salted PBKDF2 hashes in local config area; migration adds platform-specific complexity not required for immediate OWASP wave. | Local file disclosure could expose password hashes for offline attack. | Enforce mode `0600` on write (atomic rename via `_atomic_write_json`); read-path file-mode verification in `auth._load_creds()` (via `_check_creds_permissions()`): refuses to load a credential file whose mode is not exactly `0600` on POSIX, raises `CredentialError` blocking all credential operations until repaired; `check_credential_store()` preflight for callers that must surface the error before calling `verify_password()`; operator hardening guidance in docs; no plaintext secret logging. | 2026-08-15 | HI + RA |
| W-003 | Concurrent session management UI (list/revoke sessions) | DEFER | Single-admin operational model reduces practical benefit in this wave. | Session theft cannot be revoked from an in-app multi-session console. | Short idle/absolute session timeouts, logout invalidation, lockout/backoff and brute-force controls, planned revisit if multi-user support lands. | 2026-08-15 | HI + RA |
| W-004 | Session persistence across restart | DEFER | Current in-memory store is operationally acceptable for local admin workflows. | Users are logged out on process restart; no cross-restart revocation metadata. | Documented behavior, explicit operational expectation, secure re-auth flow with stronger password/lockout controls. | 2026-08-15 | HI + RA |
| W-005 | ASVS V6.2.12 breached-password checking | DEFER | HIBP breach corpus is ~10 GB of SHA-1 hashes; bundling a subset is impractical. The k-anonymity API path adds an outbound network dependency to the auth write path, conflicts with offline-first architecture, and leaks password hash prefixes to an external service. | Passwords not matched against full breach corpus; users could select a breached-but-uncommon password that passes the top-10000 blocklist. | min-15 policy + top-10000 common-password blocklist (ASVS V6.2.4), account+IP lockout (O1), PBKDF2 600k iterations. | 2026-08-15 | HI + RA |

## Closed / Resolved Waivers

None yet in this wave.

## Notes

- Any future remote multi-user mode invalidates current assumptions and requires full waiver re-review.
- If deployment model changes to internet-facing service, all deferred items must be re-scored before go-live.
