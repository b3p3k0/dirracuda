# Reddit OD Module: V3 Claude Supervisor Prompts

Use one card at a time. No card bundling without explicit HI approval.

Read first for any V3 card:
1. `docs/dev/reddit_od_module/14-V3_SPEC.md`
2. `docs/dev/reddit_od_module/15-V3_ASCII_SKETCHES.md`
3. `docs/dev/reddit_od_module/16-V3_TASK_CARDS.md`
4. `docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md`

## Prompt A: Plan One V3 Card (No Code)

```text
You are working on Reddit OD Module V3.

Read first:
- docs/dev/reddit_od_module/14-V3_SPEC.md
- docs/dev/reddit_od_module/15-V3_ASCII_SKETCHES.md
- docs/dev/reddit_od_module/16-V3_TASK_CARDS.md
- docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md

Task:
Produce a plan only for Card <V3-N>. No code changes.

Rules:
1. Respect locked scope exactly (r/opendirectories-only, search scoped, user submitted-only).
2. One card only; do not pull in future card work.
3. Keep changes surgical and reversible.
4. No commits.

Output:
1) Concrete file touch list
2) Step-by-step implementation plan
3) Risks / assumptions / possible regressions
4) Exact validation commands
5) PASS/FAIL acceptance gates
```

## Prompt B: Implement One V3 Card

```text
Implement Card <V3-N> from docs/dev/reddit_od_module/16-V3_TASK_CARDS.md.

Follow:
- docs/dev/reddit_od_module/14-V3_SPEC.md
- docs/dev/reddit_od_module/15-V3_ASCII_SKETCHES.md
- docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md

Execution requirements:
1. Confirm card scope before edits.
2. Smallest safe change set for this card only.
3. Add/adjust tests for all introduced behavior branches.
4. Run the card validation commands exactly and report outcomes.
5. Do not commit.
6. Report touched-file line counts before/after using rubric in task cards.

Response format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)
```

## Prompt C: QAQC Review (No New Features)

```text
Review the completed Card <V3-N> implementation with code-review priority:
1. Bugs / regressions
2. Contract violations vs V3 spec
3. Missing test coverage for risky branches
4. Input validation and fallback behavior safety

Do not add new features.
If fixes are required, list by severity with file/line references.
Then provide:
- Required fixes before acceptance
- Optional hardening items
- Validation additions (if any)
```

## Prompt D: Blocker Escalation

```text
You are blocked on Card <V3-N>. Do not guess.

Return exactly:
1. Blocker reason
2. Exact command(s) HI can run to unblock
3. Expected output/result from those commands
4. Minimal fallback path if unblock is not possible
```

## Prompt E: Card Closeout Summary

```text
Card <V3-N> is complete. Prepare closeout evidence.

Include:
1. Exact commands run
2. Command outcomes (PASS/FAIL)
3. Touched file line counts before/after + rubric rating
4. Residual risks/assumptions
5. Manual HI checks with step-by-step actions

No commit.
```

## Kickoff Prompt (First handoff to Claude: plan V3-1)

```text
You are joining an in-progress repo and planning V3 work for the Reddit OD module.

Read first:
- docs/dev/reddit_od_module/14-V3_SPEC.md
- docs/dev/reddit_od_module/15-V3_ASCII_SKETCHES.md
- docs/dev/reddit_od_module/16-V3_TASK_CARDS.md
- docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md

Task:
Plan Card V3-1 only (Top Window Expansion). Plan only, no code edits.

You must cover:
1. Exact file touch list
2. Required API/option shape updates (client/service/dialog)
3. Legacy compatibility for old top-state key
4. Test deltas with specific test names
5. Validation command list
6. Risks and rollback strategy

Constraints:
- One card only
- Surgical and reversible
- Do not commit

Return in this structure:
- Card summary
- File touch list
- Implementation steps
- Risks and assumptions
- Validation commands
- PASS/FAIL gates
```
