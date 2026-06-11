# RA Runbook

Role: Codex RA after HI approves the PA planning pack

## Start Of Wave

1. State role as RA.
2. Run C0.
3. Compare current repo truth with the approved planning baseline.
4. Stop for HI if unrelated changes make a card unsafe or materially obsolete.
5. Start one fresh Claude DA instance for one card. Its first phase is
   implementation planning for that card only.

## DA Plan Review

Review Claude's proposed plan for:

- exact compliance with the named card;
- correct root cause;
- complete caller inventory;
- no forbidden dependency/schema/auth/CI changes;
- no GUI-to-workflow boundary violation;
- deterministic failure behavior;
- adversarial and regression tests;
- touched-file line-count risk;
- rollback and documentation impact;
- absence of unapproved product decisions.

Return findings first, ordered by severity. Claude revises until blockers are
closed. HI locks any product tradeoff. Save only the approved revision under
`approved_plans/`.

## Execution Authorization

HI/RA approval in the UI releases the same Claude DA instance directly into
implementation. Do not send a second handoff prompt and do not relabel Claude
as PA or transition it between roles. Claude implements only the approved
revision and then returns the required card report without committing.

## Implementation Review

1. Inspect `git status` and diff before trusting the DA summary.
2. Confirm only approved files and necessary tests/docs changed.
3. Check root cause and security invariant in code.
4. Look for bypasses, hidden fallbacks, broad catches, or duplicated policy.
5. Confirm line counts.
6. Run the DA's exact commands.
7. Add broader tests when blast radius warrants.
8. Review README and Technical Reference.
9. Record findings in severity order.
10. Reject the card while any blocking finding remains.

## Exception-Batch Review

For E01-E12:

- Verify every assigned X-ID is present exactly once in the ledger.
- Read the complete try block and caller, not just the except line.
- Challenge `intentional-silent` rationale when failure could leave unsafe
  permissions, partial files, stale state, or false success.
- Reject debug logs containing secrets, raw untrusted content, or high-volume
  loop noise.
- Require a behavior test for each `should-surface` change.
- Prevent opportunistic changes to unassigned handlers.

## Card Close

A card closes only when:

- approved plan and implementation agree;
- automated status is honest;
- required manual test has a result;
- docs are synchronized;
- residual risk is recorded;
- tracker is updated;
- no required session is still running.

After close, tell HI:

```text
Card <ID> is accepted. Before starting <next ID>, I suggest committing
<ID> if you want this checkpoint preserved.
```

Do not commit until HI says exactly `commit`.

## Escalation

Stop and return to HI when:

- C2 cannot safely separate pinned IP from SNI/certificate identity;
- a requirement or schema/auth/CI change appears necessary;
- a touched file exceeds 1700 lines without an approved modularization plan;
- a fix changes public CLI/stdout behavior;
- a newly discovered vulnerability materially changes card order or scope;
- full tests reveal an unrelated regression that cannot be isolated safely.
