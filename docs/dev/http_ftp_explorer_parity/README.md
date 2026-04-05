# HTTP/FTP Explorer Parity Workspace

Date: 2026-04-04  
Status: C0–C3 complete

## Objective

Close the remaining unified explorer parity gap:

- FTP/HTTP explorer currently does not expose `worker count` and `large files limit` fields.
- SMB explorer already supports those controls and has tuned download behavior.

This workspace contains the execution contract and Claude-ready task cards.

## Locked Decisions

1. Parity target: **UI + behavior**.
2. Persistence source: shared settings keys under `file_browser.*`.
3. Large-file semantics target: SMB-style threshold routing.
4. HTTP exception for this phase: **worker-count parity only**.
5. HTTP still shows `large files limit`, but control is disabled with explicit operator note.
6. Limitation must be documented in both `README.md` and `docs/TECHNICAL_REFERENCE.md`.
7. Scope discipline: one small card at a time, explicit PASS/FAIL gates.
8. No commits unless HI explicitly says: `commit`.

## Root-Cause Snapshot

- SMB uses a dedicated browser UI + threaded download path with worker tuning and large-file split.
- FTP/HTTP use `UnifiedBrowserCore` UI and protocol-local sequential download loops.
- HTTP browser listings do not currently expose reliable per-file size metadata for SMB-style pre-routing by size without additional request overhead.

## Document Map

1. `SPEC_DRAFT.md` — decision-complete implementation spec.
2. `TASK_CARDS.md` — execution cards (Claude-ready).
3. `RISK_REGISTER.md` — runtime, UX, and regression risk controls.
4. `README.md` (this file) — status and contract summary.

## Working Model

- Codex role: planner/supervisor/QA reviewer.
- Claude role: per-card implementation worker.
- HI role: priority/acceptance/risk owner + manual runtime validation.

## Completion Labels (Required in Card Reports)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Card Status

1. C0: complete (planning only)
2. C1: complete
3. C2: complete
4. C3: complete
