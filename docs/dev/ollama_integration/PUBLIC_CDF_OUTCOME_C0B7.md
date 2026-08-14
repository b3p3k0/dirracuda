# C0B-7 Recovered Public Decision

Date: 2026-08-14
Status: **`RECOVERED_CONFIRMED`**

## Decision

The offline-only recovery independently replayed the exact C0B-6 checkpoint and snapshot
and produced the same result from both. The unchanged frozen acceptance rule passes.

Selected configuration:

- model: `qwen3.6:27b`
- model digest: `a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`
- worksheet: `v2`
- chunk/overlap: 8000 / 256 characters
- context/output: 8192 / 1024 tokens

This is retrospective technical recovery from prospectively generated evidence. It is
not a new untouched holdout, and C0B-6 remains `BLOCKED_PROVENANCE`.

## Public measurements

- 166 documents; 80 positives and 40 negatives
- 202/202 expected chunks completed
- 0 first-pass invalid and 0 eventual-invalid chunks
- 408/408 raw findings grounded; 408/408 retained findings grounded
- recall: 20/20 for each of PII, financial, contact and demographic
- false-positive review cost: 2 documents and 2 retained findings
- 8/8 injection pairs measured; 0 injection events
- 24/24 boundary documents passed
- 6/6 truncation documents completed; 0 length outcomes
- 0 context, channel or robustness failures
- cancellation health, provenance and safety: pass
- duplicate-recovery counters: all zero

## Identities

- recovery SHA-256: `818516869ff91c0834cfa5d6526ce075516caace60cd1f8cb7dbcbbc3902e27f`
- recovered result SHA-256: `82485ef776c081503fb2954daf6f88c90d487ff20e10a9c19d0660b6b4238bee`
- public summary SHA-256: `63325c3b504cbe48a800b266809eefcba379f2e7acc0cacb8bb8e24fabaee37c`
- acceptance-plan SHA-256: `641ff9f61fd86383eb032cad7e79b860d246d0f778ce3351f7fc850c92cb6c55`

The source checkpoint and snapshot retain their C0B-6 hashes after recovery. C0B-7
created no model call, private read or replacement checkpoint.

## Next gate

Public D1/D2 are resolved. Private Stage E is eligible but remains held until the HI
either authorizes its private-data prerequisites or explicitly defers it. C1 does not
start until that C0B closeout decision is recorded.
