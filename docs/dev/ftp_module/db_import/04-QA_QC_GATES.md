# QA/QC Gates - DB Import Workstream

This checklist is used after each Claude card and before merge decisions.

## Gate A: Migration Safety

1. Startup migration runs automatically with no user prompts.
2. Re-running startup migration is idempotent.
3. Existing SMB data remains intact.
4. Existing FTP discovery/browse data remains intact.

## Gate B: Dual-Row Correctness

1. Same IP in both protocol tables renders as two rows:
   - `S <ip>`
   - `F <ip>`
2. Row identity uses protocol + row id, not IP-only.
3. Sorting/filtering does not collapse or overwrite duplicate-IP rows.

## Gate C: Protocol State Isolation

1. Marking `S` row favorite does not mark `F` row favorite.
2. Marking `F` row avoid does not mark `S` row avoid.
3. Probe/extracted/rce status changes are protocol-specific.

## Gate D: Action Routing

1. Browse action:
   - `S` row -> SMB browser
   - `F` row -> FTP browser
2. SMB-only actions must not run against `F` row unless explicitly supported.
3. Batch actions on mixed selection behave predictably and report unsupported operations cleanly.

## Gate E: Deletion Semantics

1. Deleting selected `S` row removes SMB row only.
2. Deleting selected `F` row removes FTP row only.
3. No accidental cross-protocol deletion by IP.

## Gate F: Regression Baseline

1. SMB scan launch and SMB server browser still work.
2. FTP scan launch and FTP server picker/browser still work.
3. No new failures in targeted tests used for this feature.

## Gate G: Timestamp Consistency

1. New DB writes for `first_seen` / `last_seen` use one canonical format:
   - `YYYY-MM-DD HH:MM:SS`
2. Startup migration normalizes legacy rows with ISO `T` separators.
3. Re-running migration does not re-modify already-normalized rows.
4. Recent filtering and ordering produce stable results across mixed historical data.

## Required Evidence from Implementer

1. File list changed.
2. Exact tests run + pass/fail counts.
3. Manual validation notes for Gates B-G.
4. Any skipped checks and why.
