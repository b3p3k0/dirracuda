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
