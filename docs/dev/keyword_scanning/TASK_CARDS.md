# Sherlock Task Cards

Each card is executed independently. Claude must first return a plan for the
specific card. The RA reviews that plan before any implementation prompt is
approved.

## C0 - Contract Freeze

Goal: Confirm this planning packet is complete and ready for execution.

Deliverables:
- Review `README.md`, `SPEC.md`, `ROADMAP.md`, `RISK_REGISTER.md`,
  `ASCII_SKETCHES.md`, and `CLAUDE_PROMPTS.md`.
- Identify missing decisions or contradictions.
- No runtime code changes.

Validation:
- `git diff -- docs/dev/keyword_scanning`
- File-size check for docs in this folder.

## C1 - Matcher And Settings Model

Goal: Add pure Sherlock pattern matching and settings validation.

Expected implementation shape:
- New small module for pattern data, built-ins, pure path-entry adapters,
  matching, and color validation.
- Define a canonical Sherlock path-entry shape so the matcher is not coupled to
  database rows.
- Support adapters from already-loaded normalized snapshot rows and raw snapshot
  dictionaries, without performing storage reads in the matcher.
- Match full normalized paths and individual path segments, including
  share/container names when available.
- Preserve display casing in returned hits while applying case folding only for
  comparisons when ignore-case is enabled.
- No DB writes, DB reads, config persistence, or GUI construction in this card.
- Config-store registration and save/load behavior belong to C3 unless the RA
  explicitly revises ownership.

Acceptance:
- Plain substring, wildcard, case-sensitive, and case-insensitive matching.
- Disabled patterns ignored.
- Highest severity and hit count computed.
- No content/download/network code.
- Purity guardrail test prevents accidental imports or calls into database,
  filesystem, network, or protocol layers.

Validation:
- Matcher unit tests.
- Color validation tests.
- `git diff --check`.

## C2 - Persistence Contract

Goal: Persist latest Sherlock summaries and capped hit details.

Expected implementation shape:
- Additive primary DB tables via guarded migration.
- Thin DatabaseReader helpers or separate store helper bound into DatabaseReader.
- Runtime guards for every table/column read.

Acceptance:
- Latest summary replaces previous result for same host/protocol.
- Stored `snapshot_id` prevents stale display.
- Missing snapshot or missing Sherlock tables degrades safely.
- Hit details are capped while total count is preserved.

Validation:
- Migration tests.
- Store tests on SMB, FTP, HTTP rows.
- Minimal-schema regression tests.

## C3 - Accessories Sherlock Tab

Goal: Add the `Sherlock` Accessories tab for settings and pattern management.

Expected implementation shape:
- New `gui/components/experimental_features/sherlock_tab.py`.
- Register tab in the existing feature registry.
- Fixed-height scrollable pattern table.
- Hex color text inputs plus optional built-in `colorchooser` button.

Acceptance:
- Default colors load.
- Valid colors save; invalid colors are rejected.
- Built-ins can be disabled/restored.
- Custom patterns can be added/edited/disabled.
- Dialog default size shows all major controls.

Validation:
- GUI unit tests.
- Xvfb dialog open/screenshot check.
- Messagebox/focus guardrail tests.

## C4 - Server List Display And Standalone Scan

Goal: Add Risk column and standalone Sherlock scan for selected existing hosts.

Expected implementation shape:
- Server List table gains alert-only `Risk` column.
- Row tags/tints use saved severity colors.
- Standalone action loads latest snapshots and runs matcher only.
- Factor the scan-and-persist path into a reusable helper so C5 can call the
  same behavior after probe snapshots are saved.

Acceptance:
- Findings show `HIGH n`, `MED n`, or `LOW n`.
- Clear/no-hit/no-snapshot/stale rows show blank Risk cells.
- Missing snapshots are skipped and counted.
- No network work is triggered.

Validation:
- Table display tests.
- Batch action tests.
- File-size checks before/after touched Server List files.

## C5 - Post-Probe Hook

Goal: Optionally run Sherlock after probe snapshot persistence.

Expected implementation shape:
- Hook after successful snapshot ID is available.
- Reuse the same matcher/store path as standalone scan.
- Do not alter probe status, indicator matches, or extraction state.
- Mirror the global `Run after probe` flag in Start Scan runtime controls when
  accepted by HI; it must preserve the rest of the Sherlock settings shard.

Acceptance:
- Sherlock runs only when setting is enabled.
- Sherlock does not run when probe fails or has no snapshot.
- Existing probe summaries remain unchanged except optional Sherlock notes.

Validation:
- Dashboard post-scan tests.
- Detail-probe tests.
- Server List probe tests.
- Runtime-toggle persistence/layout tests if the Start Scan control is present.

## C6 - Details And Web UI Read-Only Display

Goal: Show Sherlock summaries and hit details outside the table.

Expected implementation shape:
- Desktop details include Sherlock summary/details when available.
- Web UI result rows include read-only risk badges.
- Web UI details include read-only Sherlock hit summary.
- New helper modules keep large files from growing past guardrails.

Acceptance:
- Web UI has no Sherlock editing or action endpoint.
- Badges/details render from persisted data.
- Stale/no-hit rows remain quiet in list view.

Validation:
- Web UI results API tests.
- Web UI frontend static tests.
- Desktop details tests.

## C7 - Closeout, Visual QA, Docs

Goal: Validate the integrated feature and update docs.

Deliverables:
- Targeted tests from earlier cards.
- Xvfb visual checks for touched dialogs.
- README update.
- Technical reference update.
- Lessons learned update.
- Final PASS/FAIL report.

Validation:
- Targeted pytest commands from cards.
- `git diff --check`.
- File-size check for touched files.

## C8 - User Color Tags Model

Goal: Add pure Sherlock settings/model/matcher support for User1/User2/User3
visual tags without DB, GUI, or display-surface changes.

Expected implementation shape:
- Extend pure Sherlock model/settings with optional user colors and pattern
  color-tag tokens.
- User colors default to empty string and validate as empty or `#RRGGBB`.
- Custom pattern serialization preserves optional `color_tag`; built-ins remain
  untagged/read-only by default.
- Matcher hits carry the matched pattern's color tag.
- Severity precedence and hit count remain exactly as V1.

Acceptance:
- Existing V1 settings load safely with empty user colors and untagged patterns.
- Bad user-color values fall back or reject according to caller context without
  corrupting settings.
- Unknown color-tag tokens degrade to no tag.
- Matcher purity stays intact.

Validation:
- Settings/model/serialize tests.
- Matcher tests for tagged and untagged patterns.
- Purity tests.
- `git diff --check`.

## C9 - Tag Persistence And Display Contract

Goal: Persist per-hit color tags and a selected result display tag through
guarded, additive DB changes.

Expected implementation shape:
- Add nullable `display_color_tag` to `sherlock_results`.
- Add nullable `color_tag` to `sherlock_hits`.
- Store the selected display tag from matched hits using highest-severity tagged
  hit; ties preserve matcher order.
- Risk summary/detail readers include display tag and hit color tags when
  columns exist, while legacy/partial schemas degrade to severity-only output.
- Keep Web UI DB helper growth out of `experimental/webui/db.py`.

Acceptance:
- Existing rows without tag columns still display severity-only Risk.
- New rows preserve tag data across store/read.
- Runtime guards cover every table/column touched.
- No scan/probe/content behavior changes.

Validation:
- Migration/persistence tests on minimal/current/partial schemas.
- Risk summary tests for display tag selection.
- Web UI read-helper tests if touched.
- `git diff --check`.

## C10 - Sherlock Settings UI Pattern Manager

Goal: Add User color inputs and move pattern management into a tall popup dialog
with staged edits.

Expected implementation shape:
- Main Sherlock tab shows High/Med/Low row, User1/User2/User3 row, and
  `Manage Patterns...`.
- Embedded pattern table and pattern action buttons move into `Sherlock
  Patterns`.
- Pattern manager table includes a `User Tag` column and remains scrollable with
  visible scrollbar, mouse wheel, and arrow navigation.
- Add/Edit dialog includes `Color tag` dropdown for custom patterns only.
- Pattern manager edits stay staged until main Sherlock Save persists settings.

Acceptance:
- Empty user colors save.
- Invalid non-empty user colors are rejected before save.
- Built-ins cannot be edited or assigned user tags.
- Default-size Accessories and pattern manager dialogs show all controls.

Validation:
- Sherlock tab/pattern manager GUI tests.
- Settings persistence tests.
- Xvfb screenshots for Accessories and pattern manager.
- Messagebox/focus/theme guardrails.
- `git diff --check`.

## C11 - Existing Display Surfaces Use User Tint

Goal: Apply persisted user-tag tinting to existing Sherlock display surfaces.

Expected implementation shape:
- Server List Risk row tint uses configured user color when `display_color_tag`
  is present and configured; otherwise severity tint.
- Desktop details and Web UI read-only details expose user tag labels for hits
  when available.
- Web UI badges use the same color-selection contract as desktop.
- Shared display helper preferred over duplicating tint precedence logic.

Acceptance:
- Risk text remains `HIGH n`, `MED n`, or `LOW n`.
- Empty/missing user color falls back to severity color.
- Blank/stale/no-hit rows remain blank and untinted.
- Web UI remains read-only with no Sherlock mutation route.

Validation:
- Server List Risk tests.
- Desktop details tests.
- Web UI API/static tests.
- `git diff --check`.

## C12 - Probe Summary Risk Highlighting

Goal: Add Sherlock Risk column and row tint to existing probe batch summaries
when post-probe Sherlock finds fresh Risk rows.

Expected implementation shape:
- Reuse persisted/returned Sherlock display data after the post-probe hook.
- Add optional Risk column support to the shared batch summary dialog.
- Add row tint tags driven by the same user-tag-wins fallback contract.
- Preserve current summary columns when no row has fresh Risk.
- CSV export includes Risk only when the Risk column is visible.

Acceptance:
- Dashboard/provider probe summaries and Server List probe summaries show Risk
  only when post-probe Sherlock produces findings.
- Probe status, ransomware indicator behavior, and extraction state do not
  change.
- No extra network/probe/content work is triggered by summary display.

Validation:
- Batch summary dialog tests.
- Dashboard/provider queue summary tests.
- Server List batch probe summary tests.
- Xvfb screenshot of probe summary with Risk.
- `git diff --check`.

## C13 - V2 Closeout, Visual QA, Docs

Goal: Close out V2 color highlighting with validation evidence and docs sync.

Deliverables:
- Targeted validation matrix across C8-C12.
- Xvfb screenshots for Sherlock tab, pattern manager, and probe summary.
- README/technical reference updates if runtime behavior changed.
- Lessons learned update.
- Final file-size audit and PASS/FAIL report.

Validation:
- Targeted pytest commands from C8-C12.
- GUI guardrails.
- `git diff --check`.
- File-size check for touched files.

## C14 - Color Swatch Picker Polish

Goal: Replace the Sherlock tab's visible hex-entry plus `...` color-picker
controls with compact clickable swatches.

Deliverables:
- Main Sherlock tab shows fixed-size swatch buttons for High/Med/Low and
  User1/User2/User3 colors.
- Clicking a swatch opens Tk's native color chooser and keeps storing the
  selected lowercase `#rrggbb` in the existing settings vars.
- User colors show `None` when empty and include a Clear control that restores
  the saved empty-string value.
- Severity colors remain required and cannot be cleared.
- Saved `sherlock.json` wire format remains unchanged.
- README/technical docs are updated only where visible hex-field wording would
  become misleading.

Validation:
- Sherlock tab tests for swatch click, chooser cancel, user Clear, no `...`
  buttons, existing save validation, and invalid value rejection.
- Accessories geometry/default-size Xvfb screenshot showing all six controls
  without clipping and no visible hex strings in the color rows.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`, and runtime
  file-size check for `sherlock_tab.py`.

## C15 - Built-In Lifecycle And Copy

Goal: Let analysts trim or clone built-in patterns without losing a stable
restore target.

Deliverables:
- Built-ins can be deleted from the staged list and stay hidden after Save.
- Built-ins are not directly overwritten; Edit on a built-in opens the Add flow
  prefilled from that built-in and saves a new custom pattern.
- Add a `Copy` button for exactly one selected row; it opens Add prefilled from
  the selected built-in or custom pattern.
- Persist deleted built-ins additively as `builtin_deleted`, keeping existing
  `builtin_disabled` behavior distinct.
- `Restore Built-ins` clears disabled/deleted built-in state and restores code
  defaults while preserving custom patterns.

Validation:
- Serialization tests for legacy settings, disabled built-ins, deleted
  built-ins, and restore semantics.
- GUI tests for built-in edit-as-copy, Copy, built-in delete, custom delete, and
  staged-only behavior until the main Save.
- Xvfb/default-size check of the action row with the new Copy button.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`, and file-size audit.

## C16 - Category Combobox

Goal: Reduce category misspellings while still allowing analysts to create new
categories.

Deliverables:
- Add/Edit Pattern uses an editable `ttk.Combobox(state="normal")` for Category.
- Dropdown values come from current staged pattern categories, de-duplicated
  case-insensitively and sorted.
- Typed new values are allowed; blank category saves as `Custom`.
- New staged categories appear in later Add/Edit dialogs.

Validation:
- Category list helper tests for sort/dedupe behavior.
- GUI tests for existing-category selection, typed-new category save, and blank
  fallback to `Custom`.
- Xvfb/default-size Add/Edit screenshot.
- `git diff --check` and file-size audit.

## C16.5 - Pattern Manager Save And Close

Goal: Let users persist pattern-manager staged edits from inside the manager
dialog without hunting for the parent Sherlock tab's Save button.

Deliverables:
- Add `Save & Close` between `Restore Built-ins` and `Close`.
- `Save & Close` validates and persists the full Sherlock settings shard through
  the existing Sherlock tab save path, then closes the pattern manager only when
  save succeeds.
- Existing `Close` closes without saving, but warns when there are unsaved
  pattern-manager changes and lets the user cancel close.
- Unsaved-change tracking covers Add/Edit/Copy/Delete/Enable-Disable/Restore
  Built-ins in the pattern manager; parent-tab color/option changes can still
  use the parent Save button.
- No new persistence format or settings schema changes.

Validation:
- GUI tests for successful Save & Close, save failure staying open, Close with
  unsaved changes warning/cancel, Close with no changes, and dirty-state marking
  for pattern actions.
- Xvfb/default-size manager screenshot showing `Save & Close` between Restore
  and Close.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`, and file-size audit.

## C17 - Multi-Select And Double-Click

Goal: Make large pattern-list maintenance faster with standard selection
behavior and row double-click editing.

Deliverables:
- Pattern table uses `selectmode="extended"` for Ctrl/Shift multi-select.
- Add `_selected_patterns()` and route batch-capable actions through it.
- `Enable/Disable` flips each selected row individually, including mixed states.
- `Delete` acts on all selected rows; built-ins stage deletion, customs are
  removed.
- Double-click opens Edit for one selected row; built-in double-click follows
  edit-as-copy.

Validation:
- GUI tests for selectmode, batch mixed-state toggle, batch delete, exact-one
  Edit/Copy requirements, and double-click routing.
- Xvfb/default-size manager screenshot with multi-selection.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`, and file-size audit.

## C18 - Search And Faceted Filters

Goal: Let analysts narrow long pattern lists without changing staged data.

Deliverables:
- Add filter row above the table: text search plus Category, Severity, User Tag,
  and Enabled facets.
- Search matches label, category, pattern, severity, tag, and type.
- Facets use exact staged values; `All` disables a facet.
- Filter changes re-render visible rows and clear selection so hidden rows are
  never mutated by actions.

Validation:
- Filter helper tests for search/facet combinations.
- GUI tests for Clear filters, selection clearing, and action behavior after
  filtering.
- Xvfb/default-size manager screenshot with filter row.
- `git diff --check`, GUI guardrails, and file-size audit.

## C19 - JSON Pattern Export

Goal: Export the current staged Sherlock pattern list in a standard, exact
format for review/sharing.

Deliverables:
- Add an `Export` button to the pattern manager.
- Export all staged patterns, not only filtered rows, to JSON through
  `filedialog.asksaveasfilename`.
- Payload includes metadata plus pattern rows with key, type, enabled, severity,
  category, label, pattern, and color_tag.
- Cancel and write errors leave staged data unchanged; errors use
  `safe_messagebox`.
- No import in this pass.

Validation:
- JSON schema/content tests, cancel/no-write test, write-error test.
- Xvfb/default-size manager screenshot showing Export.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`, and file-size audit.

## C20 - Pattern Manager Closeout

Goal: Close out the pattern-manager improvements with validation evidence, docs
sync, and maintainability review.

Deliverables:
- README, technical reference, planning docs, and lessons learned updated where
  runtime behavior changed.
- Xvfb screenshots cover category combobox, multi-select/action row, filters,
  and export button.
- File-size audit for touched runtime files; if `sherlock_tab.py` crosses 1200
  lines, propose/extract a helper module before more pattern-manager work.

Validation:
- Targeted matrix across C15-C19.
- `pytest -k sherlock`, GUI guardrails, `git diff --check`.
- Known unrelated full-suite flakes, if any, reported separately from Sherlock
  failures.
