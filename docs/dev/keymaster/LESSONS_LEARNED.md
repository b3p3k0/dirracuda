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

## Known Risks

1. Plaintext key storage is practical for local tooling but has local-machine exposure risk.
2. Config path resolution drift can cause "applied key to wrong config" if not tested.
3. Duplicated persistence helpers across modules can reintroduce config overwrite bugs.
