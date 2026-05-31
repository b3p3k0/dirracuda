# Integrate Experimental Features Into Main - LESSONS LEARNED

Status: Seeded
Last updated: 2026-05-29

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
16. Canonical runtime docs can drift even when runtime is stable. After config-path/port migrations, verify all operator and agent docs (`README.md`, `docs/TECHNICAL_REFERENCE.md`, `AGENTS.md`, `CLAUDE.md`, and `experimental/webui/README.md`) still match current WebUI defaults/paths in the same closeout wave (C8), not later.

## Additions During Execution

**C8 (2026-05-29):**
- Drift found across 13 files: port 5480 (5 files), conf/webui.json path (4 files),
  Experimental→Accessories label (README.md, TECHNICAL_REFERENCE.md, CLAUDE.md), stale RCE
  section in CLAUDE.md (module files gone since C3/C7), 6 stale sidecar-browse route rows in
  TECHNICAL_REFERENCE.md (removed from app.py in C6), missing Censys suspension note in AGENTS.md.
- ROADMAP.md card statuses were not updated past C0; each card should close its own status row.
- docs/dev/webui/ active planning docs (non-approved_plans) also drifted on port and config path;
  include them explicitly in any future closeout scope.
- Validation commands in task cards can drift; `gui/tests/test_readme_examples.py` was referenced by C8 but does not exist.
  Add a preflight check that each scripted validation target exists before promoting it to required evidence.

**C9 (2026-05-29):**
- Provider cutovers need entrypoint parity, not just service changes. SearXNG required updates in dashboard scan launch, Accessories tab run flow, and WebUI `/api/searxng/run` to prevent split-write behavior.
- Auto-sync summaries must be deterministic and non-throwing (`selected/processed/inserted/updated/skipped/failed/cancelled`) so UI/job layers can report outcomes without branching on exceptions.
- Primary-backed browser mode should disable obsolete actions rather than silently no-op. Hiding `Add to dirracuda DB` in SearXNG primary mode reduced operator ambiguity while preserving a legacy sidecar path for historical data.

Append new lessons after each completed card:
- What failed or nearly failed.
- Which guardrail prevented recurrence.
- What to enforce in future cards.
