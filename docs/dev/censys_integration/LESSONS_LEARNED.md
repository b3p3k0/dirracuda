# Censys Integration Lessons Learned

Date: 2026-05-14
Status: seed guardrails, append per completed card

## Carry-Forward Guardrails

1. Fix root causes, not symptoms.
2. Keep module isolation strict: experimental sidecar only unless HI explicitly expands scope.
3. Treat token handling as elevated-risk code; PAT never appears raw in logs, dialogs, or exceptions.
4. Keep config writes surgical; avoid clobbering unrelated JSON keys.
5. Use nested CenQL clauses for service-specific logic to avoid cross-service false positives.
6. Guard schema/data operations with runtime checks (required columns/indexes/FKs), never assumptions.
7. Keep network I/O off the Tk main thread.
8. Route all main-DB promotion through shared sidecar promotion helpers.
9. Check touched file line counts each card and stop for modularization when any touched file exceeds 1700 lines.
10. Run targeted tests first; only expand to wider regression when risk justifies it.
11. Keep docs synced at each closeout, especially `README.md` and `docs/TECHNICAL_REFERENCE.md`.
12. If blocked, provide exact human unblock commands with expected output.

## Card Log Template

Append this block after each card:

```text
Card: Cx
Date:
What failed / almost failed:
Root cause:
Guardrail added:
Tests added/updated:
Residual risk:
```

## Card Log

```text
Card: C9
Date: 2026-05-15
What failed / almost failed: plan initially called settings_manager.config.* which doesn't exist; also planned a redundant context injection that experimental_features_dialog.py already does at line 74
Root cause: settings_manager is a SettingsManager with no .config attribute — load_config(sm.get_smbseek_config_path()) is the correct call; context injection was already centralized in the dialog
Guardrail added: always use load_config() to obtain SMBSeekConfig from settings_manager; verify context injection point before adding redundant wiring; catch ValueError from get_censys_org_id() separately from PAT errors; guard frame.after() in workers against widget destruction; load config once and reuse (no redundant disk reads)
Tests added/updated: D1–D9 (credit estimate, balance success/error, PAT/org-id error paths, thread-guard)
Residual risk: live balance fetch silently skipped if PAT removed between tab open and worker completion; acceptable for this card

Card: C10.z
Date: 2026-05-18
What failed / almost failed: free-tier API constraints made query-endpoint UX appear broken even when PAT/balance surfaces looked healthy
Root cause: free-tier Censys API entitlements are lookup-only; candidate-list query endpoints required by discovery runs are not generally available
Guardrail added: suspend Censys UI surfaces until entitlement-aware candidate generation is designed; retain backend module/config contract unchanged for controlled reactivation
Tests added/updated: experimental registry/context absence checks; App Config hidden-Censys validation/preservation coverage
Residual risk: stale docs or dormant code confusion if suspension status is not kept explicit in future cards
```
