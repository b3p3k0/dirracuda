# Reddit OD Module: V2 Claude Supervisor Prompts

Use one card at a time. Keep scope tight.

## Prompt A: Plan-Only for One V2 Card (No Code)

```text
You are implementing V2 for the Reddit OD experimental module.
Read first:
- docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md
- docs/dev/reddit_od_module/10-V2_ROADMAP.md
- docs/dev/reddit_od_module/11-V2_TASK_CARDS.md
- docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md

Task:
Produce a plan only for Card <V2-N>, no code changes.

Rules:
1. Respect locked decisions exactly (A2, B4, C1, D1).
2. Keep changes surgical and reversible.
3. No refactors unless required for reuse/safety.
4. No commits.

Output:
1) File touch list
2) Step-by-step implementation plan
3) Risks / bad assumptions
4) Exact validation commands
5) PASS/FAIL gates
```

## Prompt B: Implement One V2 Card

```text
Implement Card <V2-N> from docs/dev/reddit_od_module/11-V2_TASK_CARDS.md.
Follow:
- docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md
- docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md

Execution requirements:
1. Make the smallest safe change set for this card only.
2. Add/adjust tests for newly introduced behavior.
3. Run targeted validation commands and report exact results.
4. Do not commit.

Response format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- Manual HI verification needed:
```

## Prompt C: CTO QAQC Pass (No New Features)

```text
Review the completed Card <V2-N> implementation with code-review priority:
1. Bugs and behavioral regressions
2. Contract violations vs locked decisions
3. Missing test coverage for risky branches
4. UI/UX failure modes

Do not add features. If fixes are needed, list them by severity with file/line references.
Then provide:
- Required fixes before accepting this card
- Optional hardening items (explicitly marked optional)
- Validation additions (if any)
```

## Prompt D: Handoff Summary to HI

```text
Prepare a concise handoff summary for HI after Card <V2-N>:
1. What changed
2. What was validated (include command outputs summary)
3. Remaining risks
4. Manual click-path checks HI should run now
```

