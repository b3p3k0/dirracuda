# Claude Prompt Pack - Experimental Dialog + Features

Date: 2026-04-17

Use one card at a time from `TASK_CARDS.md`.

## 1) Initial Plan Prompt (Card-specific)

```text
Read these first:
- docs/dev/experimental_dialog_n_features/SPEC.md
- docs/dev/experimental_dialog_n_features/ASCII_SKETCHES.md
- docs/dev/experimental_dialog_n_features/ROADMAP.md
- docs/dev/experimental_dialog_n_features/TASK_CARDS.md
- docs/dev/experimental_dialog_n_features/OPEN_QUESTIONS.md

Then implement Card C{N} only.

Rules:
- Reproduce/confirm the issue first.
- Surgical edits only; no broad refactors.
- Preserve behavior outside card scope.
- Guard runtime state checks (scan-idle/reddit-running/callback availability).
- Preserve Reddit add-to-DB flow when opening Reddit Post DB from new Experimental path.
- Keep `Experimental` button positioned between `DB Tools` and `Config`.
- Implement one-time experimental warning with persisted `Don't show again`.
- Remove legacy Reddit entrypoint buttons in the same implementation pass when migration card requires it.
- No commits.

Report format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + steps)

Also include touched-file line counts before/after and classify with rubric:
<=1200 excellent, 1201-1500 good, 1501-1800 acceptable, 1801-2000 poor, >2000 unacceptable.
If any touched file >1700, stop and present modularization plan before continuing.
```

## 2) Plan Critique Prompt (Use before coding if plan quality is uncertain)

```text
Review your own proposed Card C{N} plan for risks.

Return only:
1. Hidden regressions likely from this plan
2. Assumptions not yet proven from current code
3. Tests that are missing but should be mandatory for this card
4. Minimal-scope alternative if your current plan is too broad

Keep each item concrete with file/function references.
No code changes in this step.
```

## 3) Regression-Focused Review Prompt (Post-implementation)

```text
Perform a regression audit for Card C{N} implementation.

Check:
- Start Scan behavior unaffected except intended Reddit button removal
- Server List behavior unaffected except intended Reddit button removal
- Experimental dialog launches and tab wiring works
- Reddit Grab scan-idle guard still enforced
- Reddit Post DB add-to-dirracuda flow still available when callback context exists

Return:
- Findings ordered by severity
- File references for each finding
- Missing test coverage
- Final PASS/FAIL recommendation for this card
```

## 4) Blocker Escalation Prompt

```text
You are blocked on Card C{N}. Do not guess.

Return exactly:
1. Blocker reason
2. Exact command(s) HI can run to unblock
3. Expected output/result from those commands
4. Minimal fallback path if unblock is not possible
```

## 5) Card Completion Prompt

```text
Card C{N} is implemented. Produce closeout evidence.

Required:
- Exact commands run
- Command outcomes (PASS/FAIL)
- Touched file line counts before/after + rubric rating
- Residual risks/assumptions
- HI manual checks with step-by-step actions
- AUTOMATED / MANUAL / OVERALL status block

No commit.
```
