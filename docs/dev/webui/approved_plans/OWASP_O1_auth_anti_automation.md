# Approved Plan — OWASP O1 Authentication Anti-Automation

Approved date: 2026-05-13
Revision: 4 (final, post-HI review rounds)

## Root Cause

`app.py::_login_submit` calls `verify_password()` with no attempt tracking.
No lockout state exists, and in-memory state would not survive restart.
Unlimited password guesses possible in both localhost and remote mode.

## Standards Grounding

- OWASP ASVS v5 V6.3.1 — brute-force / credential-stuffing controls (MUST, L2)
- OWASP ASVS v5 V6.1.1 — authentication design documentation
- NIST SP 800-63B §5.2.2 — per-subscriber rate limiting; counter reset after success
- OWASP Authentication Cheat Sheet — generic failure messages (best-practice hardening; not a mandated ASVS L2 control)

## Files Changed

| File | Change |
|---|---|
| `experimental/webui/rate_limiter.py` | CREATE — RateLimiter + NullRateLimiter, SQLite-backed |
| `experimental/webui/config.py` | MODIFY — AuthConfig dataclass + auth block in WebUIConfig |
| `experimental/webui/app.py` | MODIFY — wire rate limiter, update _ConfigUpdateRequest, update _health route handler |
| `experimental/webui/templates/config.html` | MODIFY — add 4 auth-tuning form fields |
| `experimental/webui/tests/test_rate_limiter.py` | CREATE — unit tests for RateLimiter and NullRateLimiter |
| `experimental/webui/tests/test_login.py` | MODIFY — lockout integration tests |
| `experimental/webui/tests/test_config.py` | MODIFY — auth block config tests |
| `experimental/webui/tests/test_scaffold.py` | MODIFY — startup-failure behavior tests |
| `gui/components/experimental_features/webui_tab.py` | MODIFY — auth rate-limiting section in config dialog |

## Key Design Decisions

**Lockout model:** Composite account+IP key: `account:{username}:ip:{client_ip}`.
- Failure from (admin, IP1) does not affect (admin, IP2) or (other, IP1).
- `record_success(account)` deletes ALL rows WHERE account = username (all IPs cleared).
- Exponential backoff: `min(base * 2^(lockout_count-1), max)`.

**Startup behavior by mode:**
- `remote_enabled=True`: `RateLimiterInitError` at init → create_app raises → server startup fails.
- `remote_enabled=False`: error → `NullRateLimiter()` assigned; server starts in degraded mode.

**NullRateLimiter:** No-op implementation with same interface. `health_check()` returns `"error"`.

**Health endpoint:** `health()` function unchanged (preserves test_scaffold.py contract).
`_health()` route handler augmented to add `"rate_limiter": rl.health_check()`.

**Generic 401 for all auth-state failures:** wrong password, locked, unknown user all return
`{"error": "Invalid username or password."}` with no Retry-After header.
CSRF/origin 403 failures remain distinct (request-integrity, not auth-state).

**Config schema:** New `auth` block in webui.json with 4 fields:
- `lockout_threshold` (default 5, range 3–20)
- `lockout_window_sec` (default 900, range 60–3600)
- `lockout_base_duration_sec` (default 300, range 30–3600)
- `lockout_max_duration_sec` (default 3600, range 300–86400)
- Cross-field: max >= base enforced.
- Backward compatible: missing `auth` key loads AuthConfig() defaults.

**DB file:** `~/.dirracuda/state/webui_ratelimit.db`, mode 0600, default journal mode
(no WAL — eliminates -wal/-shm sidecar files).

**Config API:** New auth fields are `Optional[int] = None` in `_ConfigUpdateRequest`;
None values fall back to existing `cfg.auth.*` (prevents 422 regression on old form payloads).

## Residual Risks

- R-01: Timing oracle (locked path faster than PBKDF2 path). Accept for O1; future hardening wave.
- R-08: Distributed spray across rotating IPs can dilute per-pair threshold. Future adaptive global IP-rate wave.

## Acceptance Gates

```bash
./venv/bin/python -m pytest experimental/webui/tests/test_login.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_config.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
./venv/bin/python -m pytest experimental/webui/tests/ -q
```

PASS requires: no unrelated regressions; lockout state survives restart; all new tests pass;
401 generic body for all auth-state failures; 0600 on ratelimit DB; no WAL sidecar files;
remote startup fails when DB unavailable; localhost degrades gracefully.
