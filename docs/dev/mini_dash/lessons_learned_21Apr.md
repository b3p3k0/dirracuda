# Lessons Learned - Mini Dash v2

Date: 2026-04-21

## Guardrails
- Do not let long-running work become invisible; every active task needs a monitor reopen path.
- Prefer root-cause fixes: introduce shared task registry instead of adding one-off scan-only reopen logic.
- Keep close UX autonomy-first: warn and confirm, then cancel gracefully, retry once, then force terminate.
- Keep compatibility-first extractions: preserve dashboard shim patch paths and existing method signatures.
- Keep UI hot paths responsive: run heavy probe/extract work on background threads; UI updates via Tk-safe callbacks.
- Use explicit cancellation hooks and runtime-state checks (`is_scanning`, queue flags, task registry) before shutdown actions.
- During active layout tuning cycles, keep minimum-size enforcement disabled so UX sizing can be calibrated quickly.

## Refactor Notes
- Dashboard compact layout reclaimed space by removing embedded live output panel.
- Scan output remains color-preserving and non-modal in a dedicated dialog.
- Probe/extract progress monitors are now non-modal and hide/reopen capable, tracked through shared registry.

## Future Follow-up
- Plug server-list batch jobs into `RunningTaskRegistry` using the same callback contract.
- Add optional task filtering/sorting in Running Tasks window as module count grows.
- Add a compact status chip for active queued protocol counts in dashboard footer.

## Wave 1 Follow-through (Legacy Monitor Cutover)
- Shared registry must be process-wide (`get_running_task_registry`) to avoid dashboard-local blind spots.
- Legacy monitor buttons that bypass shared registry create split-brain UX and should be removed, not shimmed.
- Reopen callbacks must target live dialog instances by runtime job id (not singleton latest-dialog pointers).
- Close flow should cancel through shared registry callbacks so non-scan tasks stop deterministically.

## Wave 1.1 Follow-through (Server List Reopen Fix)
- Dialog liveness checks must support wrapper-style dialogs (`dialog.window.winfo_exists()`), not only raw Tk widgets.
- Reopen regression test coverage should include hidden wrapper dialogs to protect task-manager double-click behavior.
- Server-list footer control should align with canonical task UX: `Running Tasks` launcher instead of local `Stop Batch`.
