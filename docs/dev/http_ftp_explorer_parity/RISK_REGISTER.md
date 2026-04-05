# HTTP/FTP Explorer Parity — Risk Register

Date: 2026-04-04

| ID | Risk | Likelihood | Impact | Mitigation / Control |
|---|---|---|---|---|
| R1 | UI parity implemented but runtime behavior unchanged | Medium | High | Separate C1 (UI/persistence) and C2 (runtime) with explicit behavior assertions and targeted tests. |
| R2 | Worker concurrency introduces race/cancel regressions in FTP downloads | Medium | High | Keep cancellation event shared, use bounded queues, add targeted cancellation-path tests. |
| R3 | HTTP behavior appears to support large-file split when it does not | High | Medium | Keep large control visible but disabled + explicit in-window note + docs updates in README/Tech Ref. **Mitigated (C3):** HTTP large-file control disabled in UI with explanatory note; README.md and TECHNICAL_REFERENCE.md now explicitly state that large-file routing is inactive for HTTP in the current release. |
| R4 | Shared settings keys accidentally diverge by protocol | Medium | Medium | Use only `file_browser.download_worker_count` and `file_browser.download_large_file_mb`; assert in tests. |
| R5 | ClamAV integration regresses under new worker model | Medium | High | Re-run existing browser ClamAV tests and verify accum/post-processing behavior in C2 validation. |
| R6 | Performance regressions or UI stalls on large selections | Medium | High | Use worker thread model with status updates via Tk-safe callbacks; avoid UI-thread blocking logic. |
| R7 | SMB behavior drift due shared-code edits | Low | High | Keep SMB-specific code path untouched; include SMB-adjacent regression checks for affected test modules. |
| R8 | Over-scope creep (HEAD/content-length for HTTP) delays delivery | Medium | Medium | Explicitly defer HTTP large-file split logic; record as follow-up instead of in-scope implementation. |

## Deferred Follow-Up (Not in This Workstream)

1. HTTP large-file split activation via reliable size metadata strategy (e.g., optional HEAD preflight with fail-safe fallback).
2. Protocol-specific telemetry to compare effective throughput by worker count.
