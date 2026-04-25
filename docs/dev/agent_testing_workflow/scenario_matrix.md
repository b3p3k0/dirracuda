# Scenario Matrix (Wave 1 + Wave 4)

| ID | Scenario | Preconditions | Action | Expected invariants |
|---|---|---|---|---|
| S1 | Probe monitor hide/reopen/finalize | Probe batch job registered in shared registry | Hide monitor, reopen via Running Tasks callback, finalize job | Task remains listed while active, reopen callback works, task is removed on terminal state |
| S2 | Extract monitor cancel + terminal cleanup | Extract batch job registered | Cancel via task callback (possibly repeated), finalize | Cancel event is set, state transitions to `cancelling`, removal is idempotent on finalize |
| S3 | Pry mixed-selection guardrail | Selected targets include SMB + FTP row | Trigger pry action | Batch launch is blocked and warning is shown; no pry job starts |
| S4 | Shared task count synchronization | Dashboard and server-list both bound to same registry | Create and remove task | Both buttons display same count and enabled/disabled state transitions |
| S5 | App close cancel path | Dashboard reports active/queued work | User rejects close confirmation | App window remains open; no cancel request is issued |
| S6 | App close confirm path | Dashboard reports active/queued work | User confirms close confirmation | Cancel request is issued, monitors are torn down, app closes |
| S7 | Dashboard scan-task lifecycle | Dashboard scan task hooks available | queued -> running -> waiting-next -> clear | Single scan task entry follows transitions and is removed on clear |
| S8 | Dashboard post-scan probe monitor lifecycle | Post-scan probe worker running | hide monitor, reopen via callback, cancel via callback | Hide does not cancel, reopen restores visibility, cancellation event is set, task is removed on finish |
| S9 | Dashboard post-scan extract monitor lifecycle | Post-scan extract worker running | hide monitor, reopen via callback, cancel via callback | Hide does not cancel, reopen restores visibility, cancellation event is set, task is removed on finish |
| S10 | SE dork probe task lifecycle (success) | SE dork rows selected | run probe to completion | create/update/remove task sequence occurs; reopen callback remains callable; no orphan task remains |
| S11 | SE dork probe task cleanup (failure) | SE dork rows selected | force probe write failure | task entry is removed on failure path and failure status is surfaced |
| S12 | Dashboard scan cancel idempotency + no duplicate task | Queued scan task created | queue task twice, cancel twice, clear terminal state | one task entry only, repeat cancel is safe, terminal clear removes task |
| S13 | Close-confirm race with scan finishing in-flight | App close starts with active work | confirm close while active flag drops during shutdown loop | cancel requested once, no forced termination path, app closes cleanly |
| S14 | ScanManager lock cleanup on startup | stale/corrupt lock file exists | instantiate ScanManager | stale/corrupt lock removed, valid live lock preserved |
| S15 | ScanManager start admission contract (SMB/FTP/HTTP) | manager idle or already active | call start methods | rejects when active, successful start sets running state + protocol metadata |
| S16 | ScanManager start failure cleanup contract | lock created then backend init fails | call start methods | returns False, clears active state, removes lock, reports error progress |
| S17 | ScanManager interrupt/cleanup contract | scan running with backend handle | interrupt then cleanup | cancellation state set, terminate callback invoked, cleanup clears lock/log/active state |
| S18 | Startup DB-unification UI parity (main + dirracuda) | startup result payload available | run `_handle_db_unification_result` on both entrypoints | keep/discard prompt applied, failure warning + retry parity, dialog exceptions are non-blocking |

| D1 | Probe cache DB-first read precedence | DB snapshot exists for host/protocol | load probe result | DB payload returned, file-cache loader not called |
| D2 | Probe cache fallback on DB miss/error | DB snapshot missing or DB read fails | load probe result | per-protocol file-cache fallback used deterministically |
| D3 | Startup backfill idempotency | Legacy cache file exists | run backfill twice | first run imports and marks complete; second run returns already_done |
| D4 | Targeted sidecar unresolved handling | Sidecar rows have unresolved hosts | run targeted sidecar import | unresolved rows skipped, report rows written, run remains successful |
| D5 | Startup unification failure signaling | backfill/import helpers raise | run startup unification + UI result handler | non-blocking failure payload, pending warning state set, retry path available |
| D6 | ScanManager lifecycle event-order fuzz | seeded action stream over start/interrupt/cleanup/lock calls | run fast/heavy fuzz | no orphan active state, no stale lock after terminal cleanup, repeated cancellation remains safe |

## Invariant Checklist
- No orphan running-task entries after terminal states.
- Active task snapshots always carry reopen and cancel callbacks.
- Hide/close monitor never implies silent cancellation.
- Callback-driven cancellation is idempotent.
- Running-task lifecycle stays active+queued only (no completed retention).
- ScanManager terminal cleanup always clears lock file + active flag.
- Startup migration UI paths remain non-blocking even when prompt/retry helpers raise.

## Fuzz dimensions
- Event order permutations around `hide`, `reopen`, `cancel`, `progress`, `finish`, and `clear`.
- Event order permutations around scan actions and app-close decisions (`queue`, `run`, `wait`, `cancel`, `close_confirm`, `close_cancel`, `clear`).
- Event order permutations around ScanManager lifecycle actions (`start_*`, `interrupt`, `cleanup`, lock create/remove, active checks).
- Deterministic seeds (fixed set) for repeatability.
- Heavy lane extends seed count and step count; same invariants as fast lane.
