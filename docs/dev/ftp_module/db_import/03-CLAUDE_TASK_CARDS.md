# Claude Task Cards - DB Import Workstream (S/F Dual Rows)

Use one card at a time unless HI explicitly approves parallel execution.

---

## Card 1: Protocol-Specific State Tables + Migration

Goal:
Add FTP-specific state tables so flags/probe/extracted are independent from SMB.

Primary files:
- `shared/db_migrations.py`
- `tools/db_schema.sql`
- `shared/tests/` (new migration tests)

Scope:
1. Create `ftp_user_flags` table keyed by `ftp_servers.id`.
2. Create `ftp_probe_cache` table keyed by `ftp_servers.id`.
3. Add idempotent missing-column migrations for `ftp_probe_cache` parity fields (`extracted`, `rce_status`, `rce_verdict_summary`).
4. Keep existing SMB data path unchanged.

Definition of done:
1. Existing DB upgrades with no user action.
2. Fresh DB includes new tables.
3. Running migrations repeatedly is safe.

Prompt seed:

```text
Implement Card 1 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Constraints:
- Additive and idempotent migrations only.
- No destructive table rewrites.
- Preserve SMB behavior and data.
Deliver:
- changed files
- migration notes
- test run summary
```

---

## Card 2: Unified List Query API (UNION ALL S/F)

Goal:
Expose one read API returning two rows for same IP when both SMB and FTP exist.

Primary files:
- `gui/utils/database_access.py`

Scope:
1. Add protocol-aware host list reader (`S` rows from SMB, `F` rows from FTP).
2. Ensure row identity supports duplicate IP across protocols.
3. Include protocol-specific favorite/avoid/probe/extracted fields.
4. Keep legacy SMB readers for compatibility where needed.

Definition of done:
1. UI consumer can render `S 1.2.3.4` and `F 1.2.3.4`.
2. No collisions when same IP exists in both tables.

Prompt seed:

```text
Implement Card 2 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Build a UNION ALL host list for SMB + FTP.
- Return host_type ('S' or 'F') in each row.
- Keep existing SMB methods backward compatible.
Deliver:
- changed files
- returned row schema
- validation notes
```

---

## Card 2.5: Timestamp Canonicalization (One DB Format)

Goal:
Standardize DB timestamp writes so SMB and FTP use one canonical format and list/query logic does not rely on mixed-format handling.

Primary files:
- `tools/db_manager.py`
- `shared/config.py`
- `gui/components/server_list_window/actions/batch_status.py`
- `shared/db_migrations.py`
- targeted tests in `gui/tests` and/or `shared/tests`

Scope:
1. Define one canonical storage format for DB timestamps: `YYYY-MM-DD HH:MM:SS` (UTC).
2. Update SMB write paths that currently use Python `isoformat()` (`T` separator) so they write canonical DB format.
3. Keep FTP write paths on `CURRENT_TIMESTAMP` (already canonical DB format).
4. Add idempotent startup migration that normalizes existing `T` timestamps in:
   - `smb_servers.first_seen`
   - `smb_servers.last_seen`
   - `ftp_servers.first_seen`
   - `ftp_servers.last_seen`
5. Ensure merge/import paths normalize incoming source timestamps before write.
6. Keep query-side `datetime(...)` normalization for safety during transition.

Definition of done:
1. New writes from SMB + FTP are in canonical DB format (no `T`).
2. Existing DB data is auto-normalized on startup with no user action.
3. Dual-row recent filtering and ordering behave the same or better.
4. No regressions in existing SMB/FTP workflows.

Prompt seed:

```text
Implement Card 2.5 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.

Objective:
Unify SMB+FTP DB timestamp storage to a single canonical format:
YYYY-MM-DD HH:MM:SS (UTC, SQLite-friendly).

Requirements:
- Replace SMB-side isoformat writes used for DB fields (first_seen/last_seen) with canonical DB timestamp output.
- Keep FTP writes on CURRENT_TIMESTAMP.
- Add additive/idempotent migration in shared/db_migrations.py that normalizes existing rows containing 'T' in smb_servers/ftp_servers first_seen/last_seen.
- Normalize import/merge timestamp writes so external ISO strings do not re-introduce mixed formats.
- Keep query-side datetime(...) ordering/filtering intact.

Tests:
- Add/extend tests proving:
  1) new writes do not contain 'T'
  2) migration converts existing 'T' timestamps
  3) repeated migrations are safe
  4) recent_scan_only behavior remains correct with normalized timestamps

Deliver:
- changed files
- before/after timestamp examples from DB rows
- test run summary with pass/fail counts
- explicit note of any unresolved edge cases
```

---

## Card 3: Protocol-Specific Write Helpers

Goal:
Make favorite/avoid/probe/extracted writes target the correct protocol tables.

Primary files:
- `gui/utils/database_access.py`
- any minimal callsite updates needed in server list actions

Scope:
1. Add protocol-aware upsert methods for user flags and probe cache.
2. Preserve existing SMB-only methods as wrappers or compatibility shims.
3. Ensure FTP row actions never write into SMB state tables.

Definition of done:
1. `S` row updates only SMB state tables.
2. `F` row updates only FTP state tables.

Prompt seed:

```text
Implement Card 3 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Add protocol-aware upsert methods.
- Keep compatibility with existing SMB callsites.
- No cross-protocol state writes.
Deliver:
- changed files
- short method map old->new behavior
- test notes
```

---

## Card 4: Server List UI Genericization + Type Column

Goal:
Render S/F rows in one browser and remove SMB-only wording where inappropriate.

Primary files:
- `gui/components/server_list_window/window.py`
- `gui/components/server_list_window/table.py`
- `gui/components/server_list_window/details.py`
- `gui/components/server_list_window/filters.py`

Scope:
1. Add a `Type` column (S/F).
2. Update row selection logic so identity is not IP-only.
3. Genericize titles/labels and details text for dual protocol context.
4. Keep SMB workflows intact.

Definition of done:
1. Same IP can appear twice with distinct row actions.
2. Favorite/avoid/probe/extracted visuals follow per-row protocol state.

Prompt seed:

```text
Implement Card 4 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Add Type column for S/F.
- Prevent duplicate-IP row collisions in selection/actions.
- Genericize SMB-specific UI text where needed.
Deliver:
- changed files
- before/after columns
- manual checks run
```

---

## Card 5: Action Routing + Deletion Semantics

Goal:
Route browse/probe/extract/delete by row protocol with no accidental cross-delete.

Primary files:
- `gui/components/server_list_window/actions/batch_operations.py`
- `gui/components/server_list_window/actions/batch_status.py`
- any minimal related helpers

Scope:
1. Route `S` rows to SMB action path.
2. Route `F` rows to FTP action path.
3. Delete only selected protocol row (`S` delete != `F` delete).
4. Preserve batch stability.

Definition of done:
1. Selecting `F` row never opens SMB browser.
2. Deleting `S` row does not remove FTP row for same IP.

Prompt seed:

```text
Implement Card 5 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Protocol-aware routing for browse/probe/extract/delete.
- Enforce per-row deletion semantics.
- No SMB regressions.
Deliver:
- changed files
- routing table
- regression notes
```

---

## Card 6: QA, Docs, and Handoff

Goal:
Close the loop with tests + operator/developer docs for the new dual-row model.

Primary files:
- `docs/dev/ftp_module/db_import/` updates
- `docs/dev/ftp_module/SUMMARY.md` (if needed)
- `README.md` (if needed)
- tests in `gui/tests` and `shared/tests` as required

Scope:
1. Add/extend tests for duplicate-IP dual-row rendering and per-protocol state isolation.
2. Document S/F behavior and deletion semantics.
3. Capture known limitations and follow-ups.

Definition of done:
1. Behavior is documented with examples.
2. Test evidence provided.

Prompt seed:

```text
Implement Card 6 from docs/dev/ftp_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Add tests for S/F dual-row and protocol-isolated flags/probe/extracted.
- Update docs for operator expectations.
- List residual risks clearly.
Deliver:
- changed files
- test summary
- open follow-ups
```
