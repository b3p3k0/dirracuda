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
