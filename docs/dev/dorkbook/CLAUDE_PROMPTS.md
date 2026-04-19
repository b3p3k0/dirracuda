# Claude Prompt Pack - Dorkbook v1

Date: 2026-04-19

Use one card at a time from `TASK_CARDS.md`.

## 1) Card Implementation Prompt

```text
Read these first:
- docs/dev/dorkbook/SPEC.md
- docs/dev/dorkbook/ASCII_SKETCHES.md
- docs/dev/dorkbook/ROADMAP.md
- docs/dev/dorkbook/TASK_CARDS.md
- docs/dev/dorkbook/OPEN_QUESTIONS.md

Implement Card C{N} only.

Rules:
- Confirm/reproduce first.
- Surgical edits only.
- Preserve behavior outside card scope.
- No commits.
- For UI cards: update/confirm ASCII_SKETCHES.md first; if UI differs, revise sketch before code edits.

Report format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + steps)

Also include touched-file line counts before/after with rubric:
<=1200 excellent, 1201-1500 good, 1501-1800 acceptable, 1801-2000 poor, >2000 unacceptable.
If any touched file >1700, stop and provide modularization plan before continuing.
```

## 2) Plan Critique Prompt (No Code)

```text
Critique your Card C{N} plan.

Return only:
1. Hidden regressions
2. Unproven assumptions from current code
3. Missing mandatory tests
4. Minimal-scope alternative if current plan is too broad

Use concrete file/function references.
No code edits.
```

## 3) Regression Audit Prompt

```text
Run a regression audit for Card C{N}.

Check:
- Experimental tab wiring remains stable
- Dorkbook singleton lifecycle behavior
- Sidecar schema/runtime guard integrity
- Built-in read-only enforcement
- Delete mute session behavior

Return:
- Findings by severity
- File references
- Missing tests
- PASS/FAIL recommendation
```

## 4) Blocker Escalation Prompt

```text
Blocked on Card C{N}. Do not guess.

Return exactly:
1. Blocker reason
2. Exact human unblock command(s)
3. Expected output/result
4. Minimal fallback path
```

## 5) Card Completion Prompt

```text
Card C{N} is implemented. Provide closeout evidence.

Required:
- Exact commands run
- PASS/FAIL outcomes
- Touched file line counts before/after + rubric
- Residual risks/assumptions
- HI manual checks
- AUTOMATED / MANUAL / OVERALL status block

No commit.
```

