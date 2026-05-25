# Integrate Experimental Features Into Main - LESSONS LEARNED

Status: Seeded
Last updated: 2026-05-25

## Carry Forward

1. Promote behavior, not folder names, first. A safe adapter can graduate features before risky package moves.
2. Treat Censys as suspended unless HI explicitly re-activates it in writing.
3. Keep Start Scan backward compatible while adding providers; do not regress SMB/FTP/HTTP.
4. Runtime schema checks are required on every sidecar/main DB bridge path.
5. Sidecar imports should fail per-row, not all-or-nothing, so operators can recover incrementally.
6. Provider validation must be explicit and actionable; implicit coercion causes silent bad scans.
7. Keep subprocess execution argument-list based and `shell=False` to avoid injection regressions.
8. Avoid global config sprawl; provider-specific settings belong near provider workflows.
9. Rename/IA changes require synchronized docs/tests in the same card to prevent drift.
10. When reviewing DA work, provide findings and constraints, then let DA choose fix details.
11. Promotion is not desktop-only: WebUI contracts must be updated in the same wave, not deferred.
12. Legacy sidecar DB browsing should not remain in WebUI after promotion; keep migration messaging clear and desktop-owned.
13. One-time migration prompts must be stateful (`not_started`, `deferred`, `completed`, `failed`) to avoid operator fatigue and ambiguous behavior.
14. Defer means defer: once operator chooses `No defer`, do not auto-prompt again; require explicit manual migration trigger.
15. File-size hard-stop modularization rule applies to production code, not test/docs files (which should still be kept practical).

## Additions During Execution

Append new lessons after each completed card:
- What failed or nearly failed.
- Which guardrail prevented recurrence.
- What to enforce in future cards.
