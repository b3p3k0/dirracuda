# Card CM — Merge imports host-scoped data

Status: approved by HI 2026-10-04 (plan `PLAN.md`, decisions Q2 + Q3). DA card.
Repo: dirracuda · Branch: development · Python: `./venv/bin/python`

## Context (stands alone)

DB Tools → Merge (`gui/utils/db_tools_engine_merge_methods.py::merge_database`)
imports another dirracuda SQLite DB into the active one. Today it merges the
three server tables (building `smb_id_mapping`, `ftp_id_mapping`,
`http_id_mapping`: source id → target id), then access rows, share_credentials,
file_manifests, vulnerabilities and failure_logs, all inside one
`BEGIN IMMEDIATE` transaction that rolls back on any exception.

It does NOT import the tables below. A later card exports host subsets into
a DB file that people merge, so Merge must carry these tables.

## Scope

Add import steps for these tables. Put them in a NEW module
`gui/utils/db_tools_engine_merge_host_data_methods.py`, bound to
`DBToolsEngine` the same way `bind_db_tools_engine_merge_methods` binds the
existing ones (see `gui/utils/db_tools_engine.py`). `merge_database` gains only
the call sites (after the access/credentials/manifests steps, before failure
logs) and progress messages. Do not refactor existing importers.

Schema reference (current, from `shared/db_migrations.py`):

- `probe_snapshots(id PK, snapshot_hash UNIQUE, host_type, ip_address, port, protocol_server_id, run_at, source, raw_snapshot_json, created_at)`
- `probe_snapshot_entries(id PK, snapshot_id FK, share_name, entry_kind, path, parent_path, is_truncated, metadata_json, created_at)`
- `probe_snapshot_errors(id PK, snapshot_id FK, share_name, message, created_at)`
- `probe_snapshot_rce(snapshot_id FK, rce_status, verdict_summary, analysis_json, created_at)`
- `host_probe_cache` / `ftp_probe_cache` / `http_probe_cache` (PK `server_id` → smb/ftp/http servers; columns differ per protocol; all have `last_probe_at`, `snapshot_path`, `latest_snapshot_id`)
- `host_user_flags` / `ftp_user_flags` / `http_user_flags(server_id PK, favorite, avoid, notes, updated_at)`
- `sherlock_results(id PK, host_type, protocol_server_id, ip_address, port, snapshot_id, highest_severity, total_hit_count, detail_count, truncated, scanned_at, updated_at, display_color_tag)`, UNIQUE `(host_type, protocol_server_id)`
- `sherlock_hits(id PK, result_id FK, severity, category, label, pattern, display_path, created_at, color_tag)`

`host_type` → id map: determine the exact `host_type` values used in
`probe_snapshots` / `sherlock_results` for SMB/FTP/HTTP by reading the code
that writes them (`gui/utils/database_access_write_methods.py`,
`gui/utils/database_access_sherlock_methods.py`). Do not guess.

## Rules (invariants — do not weaken)

1. **probe_snapshots.** First verify how `snapshot_hash` is computed. If it is
   content-only (does not include host identity), match on
   `(snapshot_hash, host_type, mapped protocol_server_id)`. If the hash
   already exists for a different host, skip and add a warning. Otherwise:
   if the hash exists on the target, reuse the target id (insert nothing). Else
   insert with `protocol_server_id` remapped. Rows whose host is not in the
   id map are skipped. Build `snapshot_id_map` (source id → target id).
   Copy `ip_address`/`port` from the target server row, not from the source.
2. **Snapshot children.** Insert entries/errors/rce only for snapshots inserted
   in this merge, with `snapshot_id` remapped.
3. **Probe cache.** For each mapped server: no target row → insert. Target row
   exists → apply `strategy` (`MergeConflictStrategy`) comparing
   `last_probe_at` (`KEEP_NEWER`: source wins only if strictly newer;
   `KEEP_SOURCE`: source wins; `KEEP_CURRENT`: target wins). Use
   `self._parse_timestamp`. `latest_snapshot_id` is remapped through
   `snapshot_id_map`; unmapped → NULL. **`snapshot_path` is never read from
   the source**: on insert set NULL, on update keep the target value. Copy
   only columns present on both sides.
4. **User flags (HI Q3: combine, never lose).** No target row → insert the
   source row. Target row exists → `favorite = t OR s`, `avoid = t OR s`;
   notes: target empty/NULL → source; equal (after strip) → unchanged;
   both non-empty and different → `target + "\n--- merged YYYY-MM-DD ---\n" + source`
   (today's local date). `updated_at` = CURRENT_TIMESTAMP when anything
   changed. A flag is never cleared by a merge.
5. **Sherlock.** Key `(host_type, mapped protocol_server_id)`. No target row →
   insert, plus its hits. Target row exists → apply `strategy` on
   `scanned_at`. When the source wins, update the result row and **replace**
   its hits (delete target hits for that result, insert source hits). When
   the target wins, touch nothing. `snapshot_id` remapped; unmapped → NULL.
   `ip_address`/`port` from the target server row.
6. Every new step skips with a `result.warnings` entry when the table or the
   required columns are missing on either side. Follow the pattern of the
   existing `_table_has_required_columns` checks.
7. All new steps run inside the existing transaction. A failure in any new
   step must roll back the whole merge, including servers.
8. `MergeResult` (`gui/utils/db_tools_engine.py`) gains int counters:
   `snapshots_imported`, `probe_cache_imported`, `user_flags_merged`,
   `sherlock_results_imported`. `preview_merge` reports source counts for
   these tables for the hosts that would be merged. The DB Tools merge summary
   (`gui/components/db_tools_dialog.py`) shows the new counters.
9. Do not change existing importers, existing tests, guardrail tests, schema,
   or migrations. Do not touch `requirements.txt`.

## Tests (new file `gui/tests/test_db_tools_merge_host_data.py`)

Build source and target DBs with `shared.db_migrations.run_migrations` in
`tmp_path`. Arrange the target so its server ids differ from the source ids
(to prove the remap).

- each table round-trips with correct remapped ids
- snapshot dedupe: the same snapshot already on the target → no duplicate, cache and sherlock point to the target id
- snapshot children copied only for newly inserted snapshots
- cache: all three strategies; `snapshot_path` never comes from the source
- flags: OR semantics; all four notes cases; a flag is never cleared
- sherlock: all three strategies; hits replaced only when the source wins; unmapped snapshot → NULL
- missing table on the source, then on the target → warning, merge still succeeds
- an injected failure in a new step (monkeypatch) → full rollback, target unchanged (server count, flags, snapshots)

## Done when

```
./venv/bin/python -m pytest gui/tests/test_db_tools_merge_host_data.py -v
./venv/bin/python -m pytest gui/tests -k "merge or db_tools" -q
./venv/bin/python -m pytest shared/tests -q
```

All pass in the real venv. Report the exact commands and output, the list of
changed files, and the answer to the `snapshot_hash` question with its file:line.
Do not commit.
