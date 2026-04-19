# Claude Prompt Pack - SearXNG Dork Module

Date: 2026-04-18

Canonical:
- Workspace path: `docs/dev/searxng_dork_module/`
- Module name: `SearXNG Dork Module`
- UI/tab label: `SearXNG Dorking`

Use one card at a time from `TASK_CARDS.md`.

## 1) Initial Plan Prompt (Card-specific)

```text
Read these first:
- docs/dev/searxng_dork_module/SPEC.md
- docs/dev/searxng_dork_module/ASCII_SKETCHES.md
- docs/dev/searxng_dork_module/ROADMAP.md
- docs/dev/searxng_dork_module/TASK_CARDS.md
- docs/dev/searxng_dork_module/OPEN_QUESTIONS.md

Then implement Card C{N} only.

Rules:
- Reproduce/confirm the issue first.
- Surgical edits only; no broad refactors.
- Preserve behavior outside card scope.
- Keep v1 constraints locked:
  - SearXNG only
  - manual single instance URL
  - no searx.space import
  - single instance (no failover pool)
  - default URL http://192.168.1.20:8090
  - reuse existing HTTP verification path
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

## 2) Plan Critique Prompt (No Code)

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
- Reddit tab behavior unchanged
- SearXNG Dorking tab behavior matches spec
- SearXNG preflight catches format/json misconfiguration
- Existing HTTP verifier/probe path is used for classification
- No cross-impact to SMB/FTP/HTTP scan workflows

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

## 6) Kickoff Prompt (Plan C1)

```text
You are joining an in-progress repo and planning SearXNG Dork module work.

Read first:
- docs/dev/searxng_dork_module/SPEC.md
- docs/dev/searxng_dork_module/ASCII_SKETCHES.md
- docs/dev/searxng_dork_module/ROADMAP.md
- docs/dev/searxng_dork_module/TASK_CARDS.md
- docs/dev/searxng_dork_module/OPEN_QUESTIONS.md

Task:
Plan Card C1 only (replace placeholder with SearXNG Dorking tab scaffold).
Plan only, no code edits.

Must cover:
1. Exact file touch list
2. UI wiring updates in experimental registry/dialog
3. Test deltas with specific test names
4. Validation command list
5. Risks and rollback strategy

Constraints:
- One card only
- Surgical and reversible
- Do not commit

Return structure:
- Card summary
- File touch list
- Implementation steps
- Risks and assumptions
- Validation commands
- PASS/FAIL gates
```
