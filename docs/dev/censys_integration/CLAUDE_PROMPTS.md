# Claude Prompt Pack - Censys Integration

Date: 2026-05-14
Canonical workspace: `docs/dev/censys_integration/`

Use one card at a time from `TASK_CARDS.md`.

## 1) Initial Plan Prompt (Card-Specific)

```text
Read first:
- docs/dev/censys_integration/README.md
- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/ROADMAP.md
- docs/dev/censys_integration/TASK_CARDS.md
- docs/dev/censys_integration/VALIDATION_PLAN.md
- docs/dev/censys_integration/RISK_REGISTER.md
- docs/dev/censys_integration/LESSONS_LEARNED.md
- docs/dev/censys_integration/FIELD_QUERY_MATRIX.md

Then implement Card C{N} only.

Hard rules:
1. Confirm/reproduce issue first.
2. Surgical edits only; no broad refactors.
3. Preserve behavior outside card scope.
4. Use Censys Platform v3 only; no Legacy Search API fallback.
5. Use PAT/Bearer auth only.
6. Keep module experimental-sidecar only.
7. No commits.

Report format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + steps)

Also include touched-file line counts before/after with rubric classification.
If any touched file exceeds 1700 lines, stop and provide modularization plan.
```

## 2) Plan Critique Prompt (No Code)

```text
Critique your Card C{N} implementation plan before coding.

Return only:
1) Hidden regressions likely from this plan
2) Unproven assumptions from current code
3) Mandatory missing tests for this card
4) Minimal-scope fallback if plan is too broad

Use concrete file/function references.
No code changes in this step.
```

## 3) Regression Review Prompt (Post-Implementation)

```text
Perform a regression-first review for Card C{N}.

Check:
- Existing experimental tabs still work (SearXNG/Reddit/Dorkbook/Keymaster)
- Censys tab behavior matches SPEC
- Secret handling does not expose PAT
- Sidecar schema checks guard runtime drift
- No cross-impact to core SMB/FTP/HTTP scan workflows

Return:
1) Findings ordered by severity
2) File references for each finding
3) Missing test coverage
4) PASS/FAIL recommendation for this card
```

## 4) Blocker Escalation Prompt

```text
You are blocked on Card C{N}. Do not guess.

Return exactly:
1) Blocker reason
2) Exact command(s) HI can run to unblock
3) Expected output/result
4) Minimal fallback if unblock is not possible
```

## 5) Card Completion Prompt

```text
Card C{N} is implemented. Produce closeout evidence.

Required:
- Exact commands run
- Command outcomes (PASS/FAIL)
- Touched file line counts before/after + rubric
- Residual risks/assumptions
- HI manual checks with step-by-step actions
- Completion block:
  AUTOMATED / MANUAL / OVERALL

No commit.
```

## 6) RA Review Output Prompt

```text
Review this completed card using docs/dev/censys_integration/RISK_REGISTER.md and the code-review guide.

Output structure:
Summary:
- acceptable | needs revision | needs HI decision

High-Risk Issues:
- security/data-loss/broken behavior/destructive concerns

Required Fixes:
- must-fix items before acceptance

Suggested Improvements:
- useful but non-blocking

Evidence:
- tests/commands/files reviewed

Open Questions:
- bounded questions for HI/implementer
```
