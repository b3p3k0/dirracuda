# Session Checklist - Browser Merge Supervisor

Use this at the start and end of each card cycle.

## Start-of-Card

1. Confirm active card ID and scope boundaries.
2. Confirm no broad refactor is included accidentally.
3. Confirm runtime path to be exercised (entrypoint -> worker -> DB/probe -> UI readback).
4. Confirm targeted tests to run for this card.
5. Confirm manual Gate B steps for HI.

## During Review

1. Is this a root-cause fix or a symptom patch?
2. Any protocol-isolation risk (S/F/H bleed)?
3. Any legacy compatibility risk?
4. Any type/path coercion assumptions not validated?
5. Any UI hot-path performance risk?
6. If SMB card touches navigation: do root/share state transitions remain deterministic?

## End-of-Card Report Template

```text
Issue:
Root cause:
Fix:
Files changed:
Validation run:
Result:
HI test needed? (yes/no + short steps)
AUTOMATED: PASS|FAIL
MANUAL: PASS|FAIL|PENDING
OVERALL: PASS|FAIL|PENDING
```

## If Blocked

Always report:
1. Why blocked.
2. Exact command(s) HI should run.
3. Expected output/result.
