# Keymaster Lessons Learned

Date: 2026-04-25
Status: initial seed; append during implementation

## Guardrails To Carry Forward

1. Fix root causes, not symptom suppression.
2. Keep config writes surgical and preserve unrelated keys; avoid stale snapshot clobber.
3. Use runtime schema checks (columns/indexes) for sidecar safety.
4. Keep apply logic centralized so button/context/double-click cannot drift.
5. Preserve existing experimental and dashboard contracts; no regressions outside Keymaster scope.
6. Keep UI hot paths responsive and avoid blocking operations on the Tk thread.
7. Add focused regression tests for every discovered edge case before closing the card.
8. Check line counts before and after every code card; stop and modularize if touched files exceed 1700 lines.
9. Treat external key-health checks as background work; never block the Tk thread for network calls.
10. Never include raw API key values in UI status, errors, or logs.
11. For multi-key API checks, add pacing/retry handling and a per-item retry action to reduce false errors from transient throttling.
12. Secure-mode default changes must be reflected in tests immediately; old plaintext assumptions will fail silently until CRUD paths are exercised.
13. In secure mode, always pass session key material into store CRUD/list operations or encrypted rows will appear empty and downstream flows (apply/check) will misbehave.
14. Duplicate detection can stay deterministic without plaintext by storing a keyed fingerprint and enforcing uniqueness on that stable lookup value.
15. Legacy plaintext migration must be guarded by live schema/runtime checks and run only after verified unlock so data conversion is deterministic and recoverable.
16. Forgotten-passphrase handling should remain destructive-only unless a true recovery mechanism exists; half-recovery flows create false safety assumptions.

## Known Risks

1. Plaintext key storage is practical for local tooling but has local-machine exposure risk.
2. Config path resolution drift can cause "applied key to wrong config" if not tested.
3. Duplicated persistence helpers across modules can reintroduce config overwrite bugs.

## Secure storage recovery — 2026-10-13

- Recovery must not require the secret being recovered from. Reset has an
  explicit resulting mode and deletes every provider and passphrase metadata
  in one caller-owned transaction.
- Session key shape is insufficient validation. Check current passphrase
  ownership and derived subkeys before encrypted reads or writes; reset and
  reconfiguration invalidate previously unlocked sessions.
- Numeric metadata needs a work bound as well as a type check. Reject damaged
  KDF iteration values before starting expensive derivation.
- Separate RA review of KSR1 confirmed rollback through injected partial
  writes and stale-session rejection across connections; the original reported
  password rejection has not been reproduced.
- Storage policy and operation feedback need separate labels. Explicit setup,
  unlock, enable, and destructive-disable actions remove the ambiguity of a
  checkbox that prompts for a password while trying to turn protection off.
- Discard late background results by generation when deleting cached data.
  Clearing the visible rows alone does not stop a pending completion from
  restoring stale credit results.
- Test real Tk focus and confirmation fields, not just mocked dialog returns.
  Xvfb coverage caught initial focus landing on the Toplevel instead of the
  passphrase entry during this card.
- Audit transitions as well as opening states. The real-entrypoint exercise
  caught a clipped Enable secure storage label after reset even though static
  setup screenshots passed. Reflow action rows when their text changes, and
  compare allocated widths with requested widths in real Tk tests.
- Keep verification claims bounded: this effort used Linux/Xvfb, synthetic
  stores, automated Tk interactions, and screenshot inspection. It did not
  reset the live store or establish the cause of the original password report.
