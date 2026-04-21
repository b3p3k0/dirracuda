# Main-DB Unification Wave 1 Notes (2026-04-21)

## Delivered In This Card
- Added normalized probe snapshot persistence in `dirracuda.db`:
  - `probe_snapshots`
  - `probe_snapshot_entries`
  - `probe_snapshot_errors`
  - `probe_snapshot_rce`
- Added `latest_snapshot_id` compatibility link columns on:
  - `host_probe_cache`
  - `ftp_probe_cache`
  - `http_probe_cache`
- Added migration tracking/report tables:
  - `app_migration_state`
  - `app_migration_reports`
- Added extraction summary table:
  - `extract_run_summaries`
- Cut over probe read path to DB-first with file fallback:
  - `load_probe_result_for_host(...)` now checks DB first, then legacy cache files.
- Stopped writing new probe snapshot cache files in active probe workflows.
- Removed runtime dependency on settings/file fallback for server-list probe status.
- Added startup, non-blocking unification orchestration in canonical `dirracuda` entrypoint (and parity in `gui/main.py`):
  - probe cache backfill
  - targeted sidecar import (host entities only)
  - one-time keep/discard prompt for old cache files
  - warning + retry on migration failure
- Replaced extraction summary file-log dependency with DB persistence (file fallback retained for compatibility failures).

## Guardrails Applied
- Public `DatabaseReader`/GUI call signatures preserved.
- No new snapshot file writes after cutover.
- Deterministic upsert behavior for snapshot/import persistence.
- Schema operations guarded by runtime table/column checks when writing probe cache rows.

## Lessons Learned
- Migrations and runtime writes must both tolerate mixed-era schemas; tests intentionally use minimal legacy tables and will fail if writes assume newest columns.
- Keeping `snapshot_path` callable and nullable avoided contract breaks while removing hard dependency on local files.
- Startup migration UX must stay non-blocking: background worker + retry prompt is safer than hard startup failure.
- Sidecar import should skip unresolved host records with explicit reason codes instead of forcing weak/unsafe coercion.

## Remaining Follow-up Candidates
- Expand sidecar import coverage beyond current host-entity subset if needed.
- Add UI surface for viewing migration report rows (`app_migration_reports`) for analyst auditability.
- Decide long-term retention policy for optional raw artifact logs.
