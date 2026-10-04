# Subset DB Export from Server List — Plan

Status: draft plan (PA). Awaiting HI approval. No code authorized.
Date: 2026-10-04 · Branch: development

## Goal

From the Server List, select hosts (for example, filtered to "hosts with
ebooks") and save them to a separate SQLite DB that can be shared. The
receiver loads it through the existing DB Tools → Merge.

## Locked decisions (HI, 2026-10-04)

| # | Decision |
|---|---|
| D1 | Build method: **allowlist copy**. Create an empty DB with the current schema. Copy only the selected hosts and the allowlisted tables. Leave every other table empty. |
| D2 | Content per host: server rows, access/shares, file manifests, probe snapshots, user flags + notes, Sherlock results/hits, Analyst results, **credentials**. |
| D3 | Entry: a right-click "💾 Save Selected to Database…" item **and** an `Export ▾` toolbar button (Selected / Shown × CSV / JSON / ZIP / Database). This button also surfaces the CSV/JSON export, which today you can reach only with Ctrl+E. |

## End state

1. You select rows in the Server List (Ctrl/Shift multi-select, after any filter).
2. You choose Save Selected to Database (right-click or `Export ▾`).
3. A standard Save As dialog opens. The default name is `dirracuda_subset_YYYYMMDD_HHMMSS.db`, with a `.db` filter.
4. A confirm step shows the host count per protocol and a "Include saved credentials" checkbox (checked, per D2) with a one-line warning.
5. A background worker writes to `<dest>.tmp`, then renames it to `<dest>` atomically. A progress dialog uses the cancel and `after()` polling rules in CLAUDE.md.
6. A summary shows the hosts and rows copied per table, the file path, and the size.
7. Opening the result in DB Tools → Merge on another install works with no extra steps.

## Guardrails

- Never write to the active DB path. Refuse it, including symlink and realpath matches.
- Open the source DB read-only (`mode=ro` URI).
- Every table in the live schema must be classified in one registry: `INCLUDE` (with a host-key rule) or `EXCLUDE` (with a reason). A test fails on any table that is not classified. This blocks silent leaks when new tables are added later.
- `scan_sessions`: copy only the sessions that copied rows reference.
- No schema change to the primary DB. No migration.

## Cards

| Card | Scope | Files (expected) |
|---|---|---|
| **C0 — verify** (PA, no code) | For every candidate table, record how it keys to a host: `server_id` FK, IP, or `(host_type, id)`. Confirm which tables Merge imports today, in particular probe snapshots, sherlock, analyst, flags and notes. Update this plan with the registry table. If Merge drops tables that we export, choose here: extend Merge (separate card) or accept that the export is only partly consumable. | this file |
| **C1 — engine** | `export_subset(output_path, host_keys, include_credentials, progress_cb)` on DBToolsEngine. Table registry. Allowlist copy via `ATTACH` + `INSERT … SELECT` with ID remap-free copies (same IDs, empty target). Temp-file + atomic rename. Tests: per-table row counts, credentials toggle, unclassified-table guardrail, active-path refusal, receiver Merge round-trip. | new `gui/utils/db_tools_engine_subset_methods.py`, `db_tools_engine.py` (bind), `shared/tests/` or `gui/tests/test_subset_export*.py` |
| **C2 — Server List UI** | Context-menu item. `Export ▾` button. Save As + confirm + progress + summary dialogs (`safe_messagebox`, `ensure_dialog_focus`, theme named styles). Worker thread only updates state. | `server_list_window/window.py`, `server_list_window/export.py`, gui tests |
| **C3 — closeout** (RA) | Docs (README, TECHNICAL_REFERENCE), Xvfb screenshot, full gui + shared suites run separately. | docs |

`window.py` is at 1236 lines. C2 puts new dialog code in `export.py`, so the window gains only the wiring.

## C0 results (2026-10-04)

Method: built a fresh DB with `shared/db_migrations.run_migrations` and read
`pragma table_info` / `pragma foreign_key_list` for each table. Read
`merge_database` in `gui/utils/db_tools_engine_merge_methods.py`.

### Finding F1 — Analyst is not in the primary DB

Analyst state lives in its own sidecar (`get_paths().analyst_db_file`). It has
an exact versioned schema with an `application_id` check
(`experimental/analyst/db_schema.py`). We cannot put it into a primary-schema
subset file.

### Finding F2 — Merge drops a large part of the export

`merge_database` imports these tables: servers (3), access (3),
share_credentials, file_manifests, vulnerabilities, failure_logs. It does
**not** import: probe_snapshots (+ entries/errors/rce), `*_probe_cache`,
`*_user_flags` (flags + notes), or sherlock_results/hits. The file listing that
backs "hosts with ebooks" is in probe_snapshots. If the receiver merges, they
lose it. If they open the subset file as their active DB
(`database_setup_dialog` / `app_config_dialog` already support this), they
keep everything.

### Table registry (proposed)

Target creation: `run_migrations(tmp_path)` on an empty file. Then `ATTACH`
the source read-only and run `INSERT … SELECT` over the shared column names.
IDs are kept as they are, so FKs stay valid without a remap.

| Table | Rule | Host key |
|---|---|---|
| smb_servers, ftp_servers, http_servers | INCLUDE | `id` in selection |
| share_access, ftp_access, http_access | INCLUDE | `server_id` |
| share_credentials | INCLUDE if checkbox | `server_id` |
| file_manifests, vulnerabilities | INCLUDE | `server_id` |
| host_user_flags, ftp_user_flags, http_user_flags | INCLUDE | `server_id` |
| host_probe_cache, ftp_probe_cache, http_probe_cache | INCLUDE, `snapshot_path` → NULL (local absolute path) | `server_id` |
| probe_snapshots | INCLUDE | `(host_type, protocol_server_id)` |
| probe_snapshot_entries/errors/rce | INCLUDE | `snapshot_id` |
| sherlock_results | INCLUDE | `(host_type, protocol_server_id)` |
| sherlock_hits | INCLUDE | `result_id` |
| scan_sessions | INCLUDE referenced only | ids used by copied rows |
| failure_logs | EXCLUDE | scan noise, keyed by IP only |
| extract_run_summaries | EXCLUDE | local download activity |
| app_migration_state/reports | EXCLUDE (target writes its own) | — |
| reddit_*, dork_*, censys_* (if present) | EXCLUDE | discovery-run provenance |
| any other table | test FAILS until classified | — |

### HI decisions after C0 (2026-10-04)

| # | Decision |
|---|---|
| Q1 | **Analyst is out of v1.** This replaces the Analyst part of D2. |
| Q2 | **Fix Merge first.** A new card, CM, extends Merge before the export cards. |

## Revised card order

CM → C1 → C2 → C3. One card at a time. Each card is accepted before the next one starts.

### CM — Merge learns the host-scoped tables

New satellite `gui/utils/db_tools_engine_merge_host_data_methods.py`, bound the same way as the existing merge methods. `db_tools_engine_merge_methods.py` is at 1319 lines, so it gains only the call sites. Every new step runs inside the existing `BEGIN IMMEDIATE` transaction, after the server ID maps are built. Every new step is skipped with a warning when either side lacks the table or the required columns (the same pattern as today).

| Table | Match key on target | Conflict rule |
|---|---|---|
| probe_snapshots | `snapshot_hash` (globally UNIQUE) | Hash exists: reuse the target id, insert nothing. Otherwise insert, with `protocol_server_id` remapped through the protocol's id map. Build `snapshot_id_map`. |
| probe_snapshot_entries/errors/rce | — | Insert only for newly inserted snapshots, with `snapshot_id` remapped. |
| `*_probe_cache` (PK `server_id`) | mapped `server_id` | No row: insert. Row exists: apply `strategy` on `last_probe_at` (KEEP_NEWER / SOURCE / CURRENT). Always remap `latest_snapshot_id`. Never import `snapshot_path`: keep the target value, or NULL on insert. |
| `*_user_flags` (PK `server_id`) | mapped `server_id` | See **Q3**. |
| sherlock_results (UNIQUE `host_type, protocol_server_id`) | mapped key | No row: insert. Row exists: apply `strategy` on `scanned_at`. When the source wins, replace that result's hits. Remap `snapshot_id`; unmapped → NULL (renders as stale, as designed). |
| sherlock_hits | `result_id` | Copied with their result. |

Also in CM:
- `MergeResult` gains counters: snapshots, probe_cache, flags, sherlock.
- `preview_merge` reports them.
- The DB Tools merge summary shows them.

Tests: a round trip for each table, each strategy on the cache and on sherlock, snapshot hash dedupe, id remap when the target ids differ, rollback on a mid-merge failure, and a missing table on either side.

DA must verify: is `snapshot_hash` content-only, or does it include host identity? If it is content-only, two hosts with the same listing share one row. Then the dedupe rule must also match `(host_type, protocol_server_id)`.

### C1–C3

These stay as written above, with Analyst removed. The C1 round-trip test now asserts **full** fidelity through Merge.

### Decision needed from HI

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q3 | Flags and notes when the target already has a row for that host | (a) The target's flags win. Fill the target's notes only when they are empty. (b) Apply `strategy` on `updated_at`, like the cache. (c) Flags: OR (a favorite stays a favorite). Notes: append the source notes under a `--- merged <date> ---` line when they differ. | (c). It never loses anyone's notes and never removes a flag. (b) can overwrite local notes silently. |
