# Claude Prompts: Card Execution + RA Review

Use these prompts verbatim or with minimal card-specific edits.

## Prompt A: Execute Next Card

```
You are executing one approved sunset card for Dirracuda under strict scope control.

Context:
- Workspace authority docs: docs/dev/remove_old_tools/README.md, SPEC.md, ROADMAP.md, TASK_CARDS.md, RISK_REGISTER.md, LESSONS_LEARNED.md
- Active branch: development
- Current approved card: <CARD_ID>

Rules:
1) Execute only this card. No unrelated refactors.
2) Preserve behavior outside Pry/RCE scope.
3) Preserve legacy DB compatibility (no destructive migration).
4) Run targeted validation first.
5) Record line counts before/after for every touched file.
6) If any touched file exceeds 1700 lines, pause and provide modularization mini-plan.
7) Do not commit unless HI explicitly says "commit".

Required report format:
1. Issue:
2. Root cause:
3. Fix:
4. Files changed:
5. Validation run:
6. Result:
7. HI test needed? (yes/no + exact steps)
```

## Prompt B: RA Gate Review

```
You are RA reviewing a completed card submission.

Review objectives:
1) Scope compliance with the approved card only.
2) Correctness and compatibility risks.
3) Test adequacy and determinism.
4) Documentation sync requirements for this card.
5) File-size policy enforcement and 1700-line stop rule.

Decision output:
- APPROVE or REJECT
- If REJECT: list exact blocking defects and required remediation steps.
- If APPROVE: list residual risks and the next card allowed.
```

## Prompt C: C0 Inventory Build

```
Build C0 touchpoint matrix for Pry/RCE sunset.

Deliver:
1) Matrix by subsystem: entrypoint/session gates, server list actions, scan/detail dialogs, probe/analyzer pipeline, CLI/workflow, DB access/schema, docs/tests.
2) For each touchpoint include file path, symbol/function names, and removal strategy notes.
3) Explicit non-goals and compatibility boundaries copied from SPEC.md.
4) Validation evidence for matrix completeness via ripgrep commands.
```

## Prompt D: Final C6 Closeout

```
Prepare C6 closeout package.

Must include:
1) README.md and docs/TECHNICAL_REFERENCE.md updates that match final code behavior.
2) Final grep guardrail evidence showing no active Pry/RCE runtime references in target paths.
3) Lessons learned append entries for each completed card.
4) Consolidated PASS/FAIL report and residual risk list.
```
