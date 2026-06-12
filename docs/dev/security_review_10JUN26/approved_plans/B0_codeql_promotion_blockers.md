# B0: CodeQL Promotion Blockers

Status: APPROVED

Approvals: HI approved implementation on 2026-06-12; Codex is implementing under
the established RA/HI workflow.

## Objective

Clear the 16 CodeQL findings blocking promotion PR #12 without changing sound
cryptographic behavior or broadening the current exception-audit wave.

## Confirmed Root Cause

Fourteen Web UI routes return text taken directly from caught exceptions. Two
hashing alerts classify high-entropy API keys as passwords even though the
operations are non-password fingerprints with separate, bounded purposes.

## Non-Goals

- No changes to Keymaster encryption, KDF, HMAC, or Shodan cache hashing.
- No audit of background-job payloads in this card.
- No dependency, schema, authentication-flow, CI, or public CLI changes.
- No promotion-branch merge, alert dismissal, push, or commit without the
  required separate approval.

## Behavior And Interfaces

- Password policy failures use a `PasswordPolicyError(ValueError)` carrying a
  fixed reason code. The route maps known codes to fixed operator messages.
- Unexpected route exceptions never contribute text to HTTP responses or new
  logs. Diagnostics identify the operation and exception class only.
- Existing domain-specific status contracts remain intact.
- Keymaster provider query input remains case-insensitive and returns a fixed
  422 response for unsupported providers.
- An AST guard rejects exception-derived values in direct Web UI HTTP
  responses in `app.py` and `*_routes.py`.

## Expected Files

- `experimental/webui/app.py`
- `experimental/webui/auth.py`
- `experimental/webui/keymaster_routes.py`
- Focused Web UI route tests plus a new response guardrail test
- `docs/TECHNICAL_REFERENCE.md`
- Security-review plan and execution tracker

## Edge And Failure Cases

- Known password-policy failures remain useful without serializing arbitrary
  `ValueError` text.
- Pydantic remains the owner of request-model 422 responses.
- Dorkbook and Keymaster domain exceptions retain their established 403, 404,
  and 409 behavior.
- Sentinel exception text must be absent from response bodies and touched logs.

## Validation

```bash
./venv/bin/python -m pytest experimental/webui/tests -q
./venv/bin/python -m pytest
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
git diff --check
```

The known order-dependent daemon/Tkinter failure must pass in isolation and
must not gain a new failure mode.

## Line-Count Risk

`experimental/webui/app.py` and `docs/TECHNICAL_REFERENCE.md` are below the
1,700-line stop gate. Keep edits local; do not use this card for extraction or
reorganization.

## Rollback

Revert the single B0 implementation commit. Alert dismissals and promotion
branch changes are separate approved actions and must be reversed separately.
