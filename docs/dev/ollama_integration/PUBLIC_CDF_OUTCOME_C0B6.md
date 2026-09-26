# C0B-6 Public Confirmation Outcome

Date: 2026-08-14
Status: **Verified `BLOCKED_PROVENANCE`; no C0B-6 quality decision**

## Outcome

The sole C0B-6 child at source commit `c7d5eda` completed all 240 planned calls. Both
fresh F lanes passed their stage gates and the corrected C44 lane completed. Final
acceptance then failed closed with origin `acceptance_derivation`.

The checkpoint, owner-only snapshot and receipt all pass independent structural and
semantic replay. C0B-6 is immutable and cannot be resumed or reclassified.

## Root cause and handoff

The inherited join compared the JSON decision payload hash with the frozen hash of the
complete database decision row. A legacy validator then treated dictionary insertion
order as category order although stored canonical JSON sorts object keys. Neither defect
changes a model response, metric or lane aggregate; both occur after every planned call
is durable.

The HI authorized C0B-7 as an offline-only recovery. It pins these exact artifacts,
preserves this terminal, makes no model calls and applies the unchanged acceptance rule
to independently replayed evidence.
