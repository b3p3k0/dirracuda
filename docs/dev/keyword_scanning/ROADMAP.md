# Sherlock Roadmap

## C0 - Contract Freeze

Freeze the scope, source anchors, runtime constraints, task cards, and review
loop. No runtime code.

Exit criteria:
- This planning workspace exists.
- Scope and non-goals are explicit.
- Claude prompts are ready for one-card-at-a-time execution.

## C1 - Matcher And Settings Model

Build pure matching and settings helpers, with no GUI and no DB writes.

Exit criteria:
- Pattern model and validation are deterministic.
- Snapshot-path adapters never read content, storage, network, or protocol
  clients from the matcher.
- Color and pattern validation tests pass.

## C2 - Persistence Contract

Add additive DB tables and DatabaseReader store helpers for latest Sherlock
summaries and capped hit rows.

Exit criteria:
- Migrations are idempotent and runtime guarded.
- Missing tables/columns degrade safely.
- Store tests cover stale snapshot and latest-result replacement.

## C3 - Accessories Sherlock Tab

Add the `Sherlock` tab for settings, colors, and pattern management.

Exit criteria:
- Pattern list is fixed-height and scrollable.
- Color hex inputs validate before save.
- Dialog opens at default size under Xvfb with visible controls.

## C4 - Server List Display And Standalone Scan

Add Risk column, row tinting, and `Scan Sherlock Selected` for existing hosts.

Exit criteria:
- Findings show high/med/low text and tint.
- Clear/no-hit/no-snapshot rows show blank Risk cells.
- Standalone scan skips missing snapshots with counts.
- The scan-and-persist helper is reusable by C5.

## C5 - Post-Probe Hook

Run Sherlock after successful probe snapshot persistence when enabled.

Exit criteria:
- Sherlock runs only after snapshots exist.
- It never triggers network work.
- Probe behavior and ransomware indicator semantics remain unchanged.

## C6 - Details And Web UI Read-Only Display

Expose Sherlock summaries and hit details in desktop details and Web UI results.

Exit criteria:
- Web UI is read-only for Sherlock.
- No auth, CSRF, or action surface is added.
- Web UI DB helpers stay below file-size guardrails via helper modules.

## C7 - Closeout, Visual QA, Docs

Run targeted validation, Xvfb dialog checks, README/technical docs sync, and
lessons learned update.

Exit criteria:
- Validation output is captured.
- README and technical reference match runtime behavior.
- Lessons learned record new guardrails and pitfalls.

Status: complete. Targeted matrix (220 Sherlock tests) passed. Full-suite
sanity found one pre-existing tkinter import-ordering artifact in
`test_daemon_cli.py`, not a Sherlock failure. Default-size Xvfb screenshots were
captured for the Accessories tab, Quick Scan row, Server List Risk column, and
detail popup. README, `AGENTS.md`, and `docs/TECHNICAL_REFERENCE.md` were synced;
`CLAUDE.md` was updated locally but remains gitignored. Sherlock V1 closed.

## C8 - User Color Tags Model

Add pure settings/model/matcher support for optional User1/User2/User3 visual
tags. No DB, GUI, or batch-summary work.

Exit criteria:
- User colors validate as empty or `#RRGGBB`.
- Custom patterns and hits can carry optional color-tag tokens.
- Severity precedence and hit counts remain unchanged.

## C9 - Tag Persistence And Display Contract

Persist per-hit color tags and the selected result display tag additively.
Expose guarded read shapes for existing display surfaces.

Exit criteria:
- Nullable tag columns migrate idempotently.
- Legacy/partial schemas degrade to severity-only display.
- Store/read helpers preserve total hit count and capped details.

## C10 - Sherlock Settings UI Pattern Manager

Add User color inputs to the Sherlock tab and move pattern management into a
tall modal dialog with staged edits.

Exit criteria:
- Main tab no longer embeds the pattern table.
- Pattern manager has scrollable table, actions, and color-tag dropdown for
  custom patterns.
- Xvfb/default-size screenshots show no clipping.

## C11 - Existing Display Surfaces Use User Tint

Apply the user-tag tint contract to Server List, desktop details, and Web UI
read-only Sherlock displays.

Exit criteria:
- Risk text remains severity-based.
- User tint wins when configured; otherwise severity tint remains.
- Blank/stale/no-hit rows remain quiet.

## C12 - Probe Summary Risk Highlighting

Show Sherlock Risk column and row tint in probe batch summaries when post-probe
Sherlock produced fresh findings.

Exit criteria:
- Summary layout is unchanged when no row has Risk.
- Risk column and CSV column appear only when visible.
- Dashboard/provider and Server List probe summaries are covered.

## C13 - V2 Closeout

Run targeted validation, visual QA, docs sync, and lessons learned for the V2
color-highlighting pass.

Exit criteria:
- All V2 surfaces have targeted tests.
- Xvfb screenshots cover Sherlock tab, pattern manager, and probe summary.
- README/technical docs and lessons learned match runtime behavior.

Status: complete. Targeted C8-C12 matrix plus `-k sherlock` (269 tests) and the
messagebox/theme GUI guardrails all passed. Full-suite sanity ran 3662 passed
with the single known `test_daemon_cli` tkinter import-ordering artifact, which
passes in isolation — no V2 regression. Default-size Xvfb screenshots were
captured for the Sherlock Accessories tab (severity + User color rows + Manage
Patterns), the pattern manager modal (User Tag column), and a probe batch summary
with the Risk column (User-tag tint vs severity fallback vs blank). README,
`docs/TECHNICAL_REFERENCE.md`, `AGENTS.md`, lessons learned, and the gitignored
`CLAUDE.md` (local only) were synced. File-size audit: all touched runtime files
under the 1700-line guardrail (`dashboard_batch_ops.py` highest at 1581 — near
limit, noted). Sherlock V2 closed.

## C14 - Color Swatch Picker Polish

Replace the Sherlock tab's visible hex-entry plus `...` picker controls with
clickable color swatches. User1/User2/User3 also get Clear controls so optional
user colors can return to the saved empty-string state.

Exit criteria:
- High/Med/Low and User1/User2/User3 render as fixed-size swatch controls.
- Swatches open Tk's native color chooser and preserve the existing saved hex
  string settings contract.
- User color Clear controls restore empty values.
- Default-size visual QA confirms no clipping and no visible hex strings in the
  color rows.

## C15 - Built-In Lifecycle And Copy

Let analysts delete built-ins, copy any row, and edit built-ins through a
prefilled custom-copy flow while keeping code-defined built-ins as the stable
restore target.

Exit criteria:
- `builtin_deleted` persists hidden built-ins separately from disabled built-ins.
- Built-in Edit and Copy create new custom rows.
- Restore Built-ins clears deleted/disabled built-in state and keeps customs.

## C16 - Category Combobox

Replace the Add/Edit free-text category entry with an editable category
combobox populated from staged pattern categories.

Exit criteria:
- Existing categories are selectable.
- New typed categories are accepted.
- Blank category saves as `Custom`.

## C16.5 - Pattern Manager Save And Close

Add an in-dialog `Save & Close` action and unsaved-change warning for closing
the pattern manager without saving.

Exit criteria:
- `Save & Close` persists through the existing Sherlock settings save path and
  closes only after a successful save.
- `Close` warns when pattern-manager changes are unsaved and can be cancelled.
- No settings schema or non-pattern-manager behavior changes.

## C17 - Multi-Select And Double-Click

Add standard Ctrl/Shift multi-select and row double-click editing to the pattern
manager.

Exit criteria:
- Treeview uses extended selection.
- Batch Enable/Disable and Delete operate on selected visible rows.
- Double-click opens Edit; built-ins route to edit-as-copy.

## C18 - Search And Faceted Filters

Add a search row plus Category/Severity/User Tag/Enabled facets above the
pattern table.

Exit criteria:
- Search and facets combine predictably.
- Clear filters restores all staged rows.
- Filter changes clear selection so hidden rows cannot be mutated.

## C19 - JSON Pattern Export

Add JSON export for the full staged pattern list from the pattern manager.

Exit criteria:
- Export writes metadata plus complete pattern rows.
- Export uses native Save As and handles cancel/error safely.
- No import path is added.

## C20 - Pattern Manager Closeout

Run final validation, visual QA, docs sync, lessons learned, and file-size
review for the pattern-manager improvement pass.

Exit criteria:
- C15-C19 targeted matrix is green.
- Xvfb screenshots cover category combobox, multi-select/action row, filters,
  and export.
- Runtime docs match behavior and file-size risk is explicitly handled.
