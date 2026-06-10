# Claude Supervisor Prompts

Use one prompt at a time. Replace placeholders before sending.

## Universal Header

```text
You are Claude working on Dirracuda under HI/RA supervision.

Repo: /home/kevin/DEV/dirracuda
Branch: development
HI: Kevin
RA: Codex

State your role at the top: PA or DA.

Read before work:
- README.md
- CLAUDE.md
- docs/TECHNICAL_REFERENCE.md
- docs/dev/security_review_10JUN26/README.md
- docs/dev/security_review_10JUN26/SOP_CONSTRAINTS.md
- docs/dev/security_review_10JUN26/FINDINGS_RECONCILIATION.md
- docs/dev/security_review_10JUN26/SPEC.md
- docs/dev/security_review_10JUN26/ARCHITECTURE.md
- docs/dev/security_review_10JUN26/TASK_CARDS.md
- docs/dev/security_review_10JUN26/VALIDATION_PLAN.md
- docs/dev/security_review_10JUN26/RISK_REGISTER.md
- docs/dev/security_review_10JUN26/LESSONS_LEARNED.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_ROLE_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DEVELOPMENT_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_CODE_REVIEW_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md

Hard rules:
- Work on exactly one named card.
- PA sessions produce plans only and edit no product code.
- DA sessions implement only the approved plan.
- Do not commit or push.
- Do not modify requirements, DB schema/migrations, auth, or CI.
- Preserve ./dirracuda as the GUI entrypoint.
- Preserve the GUI-to-CLI subprocess boundary.
- Use SMBSeekConfig for config and get_paths() for user paths.
- Mock all external services and hostile targets.
- Check touched-file line counts before and after.
- Stop if a touched file exceeds 1700 lines and no approved modularization exists.
- Report exact commands and honest PASS/FAIL.
```

## Plan-Only Prompt

```text
Use the Universal Header.

Role: PA
Card: <CARD_ID>

Produce a decision-complete implementation plan for only <CARD_ID> from
docs/dev/security_review_10JUN26/TASK_CARDS.md.

Before planning:
1. Inspect every likely caller and existing focused test.
2. Confirm the issue against current repo truth.
3. Record current branch, commit, status, and likely touched-file line counts.
4. Identify any conflict between current code and the workspace contract.

Plan sections:
- Status and baseline
- Objective
- Confirmed root cause
- Non-goals
- Exact behavior/interfaces
- File-by-file changes
- Edge/failure cases
- Tests and exact commands
- Line-count risk
- Rollback
- DA handoff prompt

Do not edit files. Stop after the plan and wait for HI/RA review.
```

## DA Implementation Prompt

```text
Use the Universal Header.

Role: DA
Card: <CARD_ID>
Approved plan:
docs/dev/security_review_10JUN26/approved_plans/<PLAN_FILE>

Implement exactly the approved plan.

Before editing:
- Confirm branch, commit, and status.
- Confirm/reproduce the issue.
- Report 5-10 relevant constraints.
- Record touched-file line counts.

After editing:
- Run the approved validation exactly.
- Review README.md and docs/TECHNICAL_REFERENCE.md for drift.
- Update docs/dev/security_review_10JUN26/LESSONS_LEARNED.md only for a
  genuinely new guardrail.

Required response:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- Line counts:
- Residual risk:
- HI test needed:
- AUTOMATED:
- MANUAL:
- OVERALL:

Do not commit. Stop after reporting.
```

## RA-Fix Prompt

```text
Use the Universal Header.

Role: DA
Card: <CARD_ID>

Address only these RA findings:
<FINDINGS>

Do not broaden scope. Re-run the affected focused tests plus every validation
command invalidated by the changes. Report the same required response format.
Do not commit.
```

## QA Review Prompt

```text
Use the Universal Header.

Role: PA
Card: <CARD_ID>

Review the current uncommitted implementation against:
- the task card;
- the approved plan;
- SPEC.md and ARCHITECTURE.md;
- existing architecture and tests.

Do not edit files.

Return:
1. Findings first, ordered by severity, with file:line references.
2. Missing or weak tests.
3. Contract drift.
4. Residual risk.
5. Exact additional validation commands.

If no issues are found, say so explicitly and identify remaining test gaps.
```

## Exception Batch Plan Prompt

```text
Use the Universal Header.

Role: PA
Card: <E01-E12>

Also read:
- docs/dev/security_review_10JUN26/EXCEPTION_AUDIT_PLAN.md
- docs/dev/security_review_10JUN26/EXCEPTION_AUDIT_LEDGER.md

Plan only the X-IDs assigned to this batch.

For each X-ID:
- inspect the complete operation and caller;
- propose one classification:
  intentional-silent, should-log-debug, or should-surface;
- state rationale;
- identify privacy-safe context;
- identify focused test/evidence.

Do not propose blanket logging. Do not move another X-ID into scope without
explicit RA approval. Stop after plan output.
```

## Exception Batch DA Prompt

```text
Use the Universal Header.

Role: DA
Card: <E01-E12>
Approved plan:
docs/dev/security_review_10JUN26/approved_plans/<PLAN_FILE>

Implement only the approved classifications and remediations.
Update EXCEPTION_AUDIT_LEDGER.md for every assigned X-ID.

Logging constraints:
- use the owning module's established logger;
- debug only when failure is non-actionable to the user;
- never log secrets, credentials, raw downloaded content, or control-bearing
  untrusted strings;
- avoid per-poll or per-chunk log noise.

Run all approved focused tests and:
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick

Do not commit.
```

## C9 Closeout Prompt

```text
Use the Universal Header.

Role: DA
Card: C9

Reconcile documentation and validation only after C1-C8 and E01-E12 are
accepted.

Update runtime docs to implemented truth, finalize risks and lessons, record
exception totals, and run every final gate in VALIDATION_PLAN.md.

Do not repair unrelated failures without a separate HI/RA decision.
Do not commit.
```
