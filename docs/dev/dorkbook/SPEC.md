# Dorkbook v1 Spec

Date: 2026-04-19  
Status: Approved for implementation

## Problem

Operators keep personal dork recipes scattered across notes and config edits.  
We need one reusable in-app notebook for protocol-specific dorks.

## Locked Decisions

1. Entry point is Experimental dialog tab `Dorkbook`.
2. Tab content is brief description + one button `Open Dorkbook`.
3. Dorkbook window is singleton + modeless.
4. Dorkbook window remains open when Experimental dialog closes.
5. Sidecar DB path is `~/.dirracuda/dorkbook.db`.
6. Tabs: `SMB`, `FTP`, `HTTP`.
7. Row fields: `nickname` (optional), `query` (required), `notes` (optional).
8. Protocol is internal-only (tab-fixed), not user-editable.
9. Built-ins are read-only, italicized.
10. One built-in per protocol at launch:
   - SMB: `smb authentication: disabled`
   - FTP: `port:21 "230 Login successful"`
   - HTTP: `http.title:"Index of /"`
11. Duplicate rule: block exact trimmed query duplicates within same protocol.
12. Search scope: current tab only.
13. Selection mode: single select.
14. Copy payload: query text only.
15. Built-in edit/delete actions are hidden.
16. Delete requires confirmation; checkbox can mute confirmation until app restart.
17. Persist:
   - window geometry (`windows.dorkbook.geometry`)
   - active protocol tab (`dorkbook.active_protocol_tab`)
18. Built-ins are refreshed by stable key on init (upsert policy).
19. No direct apply-to-scan/app-config behavior in v1 (clipboard workflow only).
20. Built-in refresh collisions against existing protocol/query rows must not fail startup; conflicting builtin change is skipped.
21. Dorkbook launch callback must fail safely with user-visible error messaging (no unhandled exception propagation).

## UI Contract

1. Experimental tab:
   - Header text describing Dorkbook purpose.
   - Single action button: `Open Dorkbook`.
2. Dorkbook main window:
   - Protocol notebook tabs.
   - Search input in each tab.
   - List columns: Nickname, Query, Notes.
   - Action row: Add, Copy, Edit, Delete.
   - Context menu mirrors row actions.
3. Add/Edit modal:
   - Inputs: Nickname, Query, Notes.
   - Inline validation for required query.
4. Empty/no-match states:
   - Empty tab text: no recipes yet.
   - Filtered no-match text: no search matches.

## Data Contract (Sidecar)

Table: `dorkbook_entries`

1. `entry_id` INTEGER PK
2. `protocol` TEXT (`SMB|FTP|HTTP`)
3. `nickname` TEXT
4. `query` TEXT
5. `query_normalized` TEXT (trimmed query)
6. `notes` TEXT
7. `row_kind` TEXT (`builtin|custom`)
8. `builtin_key` TEXT UNIQUE (required for built-ins)
9. `created_at` TEXT
10. `updated_at` TEXT

Indexes/constraints:

1. UNIQUE(`protocol`, `query_normalized`)
2. UNIQUE(`builtin_key`)
3. Checks on protocol + row_kind semantics.
4. Built-in upsert collision policy:
   - If target (`protocol`, `query_normalized`) is already occupied by a different row, skip that builtin upsert.
   - Keep existing rows unchanged in that case (custom rows take precedence over conflicting builtin refresh).

## Safety and Compatibility

1. Use schema guard on open connection.
2. Built-in mutation attempts raise explicit read-only errors.
3. Sidecar is isolated from main `dirracuda.db`.
4. No behavior changes to SMB/FTP/HTTP scan workflows.
