# Card N Implementation Plan Template

Status: draft | approved | implemented
Date: YYYY-MM-DD
Depends on: Card X (if any)

---

## Context

- Card objective:
- Why this card exists now:
- What must remain untouched:

---

## 1. Pre-Revision Reality Check

Record before proposing edits:

1. `pwd` output
2. `git status --short`
3. `git log -n 5 --oneline`
4. baseline test command + result
5. known expected failures (if any)

---

## 2. 5W Plan

- Who: implementation owner(s)
- What: exact behavior change
- Where: exact files/functions
- When: sequence of edits and validation gates
- Why: rationale and risk tradeoffs

---

## 3. Proposed Design

- Architecture sketch (ASCII if needed)
- Data flow
- Parser/output contract assumptions
- Explicit non-goals

---

## 4. File/Function Change Plan

| File | Function/Class | Change Type | Risk | Notes |
|---|---|---|---|---|
| path | symbol | add/modify | low/med/high | short note |

---

## 5. Edge Cases and Failure Modes

1. Edge case:
2. Failure mode:
3. Mitigation:

Include protocol-collision checks and zero-result behavior when applicable.

---

## 6. Validation Plan

## Gate A - Automated

1. Tests/commands:
2. Expected outcomes:

## Gate B - Manual (HI)

1. Exact UI/runtime checks:
2. Expected outcomes:

Use explicit closure labels:

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

---

## 7. Risks, Assumptions, Open Questions

- Risk:
- Assumption:
- Open question for HI:

---

## 8. Claude Execution Prompt (Copy/Paste)

```text
Implement Card N from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.

Constraints:
- Preserve SMB/FTP behavior.
- Additive/idempotent migrations only.
- Minimal, focused edits; no broad refactors.

Deliver:
- changed files
- concise diff summary
- test commands + pass/fail counts
- manual validation checklist for HI
- residual risks/assumptions
```

