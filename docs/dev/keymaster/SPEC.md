# Keymaster v1 Spec

Date: 2026-04-25
Status: Approved by HI for implementation planning

## Problem

Testing burns through Shodan key query allotments quickly.
Current workflow requires repeated manual edits to `conf/config.json` or scan dialogs.
That causes friction, slows test loops, and increases key-entry mistakes.

## Goal

Introduce an experimental `Keymaster` manager that stores reusable API keys and lets operators switch active keys quickly.

## Scope (v1)

1. Experimental entry point: `Keymaster` tab in Experimental Features dialog.
2. Singleton modeless `Keymaster` window.
3. CRUD for saved keys (Add/Edit/Delete).
4. Apply selected key via three equal paths:
   - double-click row
   - right-click menu action
   - `Apply` button
5. All three paths must call one shared apply function.
6. Apply must both:
   - populate active key target
   - persist it to active config (`shodan.api_key`).

## Non-Goals (v1)

1. No cloud sync.
2. No encryption-at-rest system in this card.
3. No automatic rotation scheduler.
4. No provider-specific network validation calls at save time.

## Locked Decisions

1. Provider scope is strictly Shodan in v1.
2. Keep a lightweight provider contract in data/model layers so future providers can plug in later without redesigning the storage shape.
3. Sidecar DB path: `~/.dirracuda/keymaster.db`.
4. Table stores user-defined key rows only (no built-ins needed).
5. Key uniqueness: block exact trimmed duplicate key values.
6. Row fields:
   - `label` (required)
   - `api_key` (required)
   - `notes` (optional)
   - audit timestamps (`created_at`, `updated_at`, `last_used_at`)
7. Key list display:
   - show label
   - show masked key preview as `first4 + asterisks + last4` (no full-key reveal in table view)
   - show notes
   - show last-used timestamp
8. Delete requires confirmation dialog with no mute option in v1.
9. Keep one selected key as active for UX highlighting by timestamp (`last_used_at`) in v1.

## Runtime Integration Contract

Apply operation writes selected key to active config path, key `shodan.api_key`.

Resolution order for config path:

1. Explicit context path from dashboard call if provided.
2. `settings_manager` key `backend.config_path` when present.
3. `settings_manager.get_smbseek_config_path()` fallback.

This keeps behavior aligned with existing dashboard key gate and app config workflows.

Active-scan behavior contract:

1. Applying a key affects future scans only.
2. If a scan is already running, it continues using the key that was active at scan start (the key accepted at scan launch confirmation time).

## Data Contract

Table: `keymaster_keys`

1. `key_id` INTEGER PK
2. `provider` TEXT NOT NULL (v1 stored as `SHODAN`; field retained for future providers)
3. `label` TEXT NOT NULL
4. `api_key` TEXT NOT NULL
5. `api_key_normalized` TEXT NOT NULL
6. `notes` TEXT NOT NULL DEFAULT ''
7. `created_at` TEXT NOT NULL
8. `updated_at` TEXT NOT NULL
9. `last_used_at` TEXT NULL

Constraints:

1. CHECK provider in (`SHODAN`)
2. UNIQUE (`provider`, `api_key_normalized`)

## UI Contract

1. Experimental `Keymaster` tab:
   - short description
   - one button `Open Keymaster`
2. Keymaster window:
   - no provider picker in v1 UI
   - search field
   - list view columns: Label, Key Preview, Notes, Last Used
   - actions: Add, Apply, Edit, Delete
   - context menu parity: Add, Apply, Edit, Delete
3. Add/Edit modal:
   - Label
   - API Key (masked entry; no reveal toggle in v1)
   - Notes
4. Double-click row behavior:
   - triggers same apply path as `Apply` button and context menu.

## Validation Contract (minimum)

1. Store tests:
   - init/open schema checks
   - duplicate key block
   - CRUD behavior
2. Window tests:
   - singleton open/focus
   - button/context/double-click all call same apply function
   - apply success and failure message paths
3. Experimental wiring tests:
   - registry contains `keymaster` tab
   - dashboard experimental route opens keymaster with parent/settings context
4. Config persistence tests:
   - apply writes `shodan.api_key` without clobbering unrelated config keys.

## Safety and Compatibility Guardrails

1. Root-cause fix only: avoid copy-paste apply logic in 3 handlers; centralize.
2. Schema guarded by runtime column/index checks.
3. Preserve existing Dorkbook/Reddit/SearXNG experimental behavior.
4. Keep config writes surgical (`shodan.api_key` update only).
5. Use existing safe dialog patterns:
   - `safe_messagebox`
   - `ensure_dialog_focus`

## Decision Record

Resolved decisions and rationale are tracked in `OPEN_QUESTIONS.md`.
