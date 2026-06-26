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

Acceptance:
- Sherlock runs only when setting is enabled.
- Sherlock does not run when probe fails or has no snapshot.
- Existing probe summaries remain unchanged except optional Sherlock notes.

Validation:
- Dashboard post-scan tests.
- Detail-probe tests.
- Server List probe tests.

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
