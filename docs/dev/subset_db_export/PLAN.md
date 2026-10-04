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

## Open items

- C0 output may add a Merge-extension card.
- Analyst data may be large (chunks/files). C0 decides between the full copy and the results-only copy (runs + findings, without chunks).
