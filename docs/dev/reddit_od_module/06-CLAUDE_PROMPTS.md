# Reddit OD Module: Claude Supervisor Prompts

Use these prompts in sequence. Keep one card in flight at a time.

## Prompt A: Plan-Only (No Code)

```text
You are implementing a new isolated Reddit ingestion POC in this repo.
Read these files first:
- docs/dev/reddit_od_module/SPEC.md
- docs/dev/reddit_od_module/00-LOCKED_DECISIONS.md
- docs/dev/reddit_od_module/01-ARCHITECTURE.md
- docs/dev/reddit_od_module/02-ROADMAP.md
- docs/dev/reddit_od_module/03-TASK_CARDS.md
- docs/dev/reddit_od_module/04-RISK_REGISTER.md
- docs/dev/reddit_od_module/05-VALIDATION_PLAN.md

Task: produce a plan only for Card 1, no code changes.

Constraints:
- Sidecar DB only; no writes to main Dirracuda scan tables.
- GUI-only module in this phase.
- Keep changes surgical and reversible.
- Root-cause fixes only; no symptom suppression.
- Guard schema operations by runtime state checks.
- Do not commit.

Output format:
1) Proposed file touch list
2) Step-by-step implementation plan
3) Risks/blockers/bad assumptions
4) Exact validation commands with expected outputs
5) PASS/FAIL gates
```

## Prompt B: Implement One Card

```text
Implement Card <N> from docs/dev/reddit_od_module/03-TASK_CARDS.md.
Read and follow:
- docs/dev/reddit_od_module/00-LOCKED_DECISIONS.md
- docs/dev/reddit_od_module/01-ARCHITECTURE.md
- docs/dev/reddit_od_module/04-RISK_REGISTER.md
- docs/dev/reddit_od_module/05-VALIDATION_PLAN.md

Execution rules:
1. Reproduce/confirm the specific issue or objective in the card.
2. Apply the smallest safe fix; avoid broad refactors.
3. Run targeted validation for touched components.
4. Report concise PASS/FAIL with exact commands run.
5. Do NOT commit.

Response format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

Also include:
- Risks/assumptions discovered
- Any blocker with exact human unblock commands
```

## Prompt C: Critique + Revise (No Code)

```text
Review the latest implementation/plan for Card <N>.
Do not write code yet.
Identify:
1. Blockers
2. Risky assumptions
3. Hidden compatibility regressions
4. Missing validation evidence
5. Any shortcut that increases downstream debugging burden

Return:
- Findings ordered by severity
- Required fixes before merge to next card
- Validation additions required
```
