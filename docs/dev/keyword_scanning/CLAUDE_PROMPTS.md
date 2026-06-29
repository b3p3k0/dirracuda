# Sherlock Claude Prompts

Use these prompts one at a time. Each card starts with a planning-only prompt.
The RA reviews that plan before sending the implementation prompt.

## Global Preamble For Every Claude Prompt

```text
You are implementing one supervised card for Dirracuda Sherlock.

Read first:
- AGENTS.md
- CLAUDE.md
- README.md relevant sections
- docs/TECHNICAL_REFERENCE.md relevant sections
- docs/dev/keyword_scanning/README.md
- docs/dev/keyword_scanning/SPEC.md
- docs/dev/keyword_scanning/TASK_CARDS.md
- docs/dev/keyword_scanning/RISK_REGISTER.md

Hard constraints:
- Do one card only.
- Do not commit.
- Preserve GUI -> CLI subprocess boundaries.
- Sherlock must never download files, read file contents, authenticate, or
  trigger network probing.
- Use additive, guarded schema changes only when the card explicitly calls for
  schema work.
- Check line counts before and after touched files.
- If any touched file exceeds 1700 lines, pause and propose modularization.
- Report Issue, Root cause, Fix, Files changed, Validation run, Result, and HI
  test needed.
```

## C1 Planning Prompt

```text
Plan C1 only: pure Sherlock matcher and settings model.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- modules/files to add or touch
- matching behavior for substring, wildcard, case sensitivity, disabled patterns
- canonical path-entry input shape for the pure matcher
- adapters from already-loaded normalized snapshot rows and raw snapshot
  dictionaries, with no DB reads in the matcher
- matching against both full normalized paths and individual path segments
- share/container name inclusion when present
- preservation of display casing while case-folding only for comparisons
- severity precedence and hit count
- color validation and default colors
- built-in pattern structure
- matcher purity guardrail test to prevent filesystem, database, network, or
  protocol coupling
- tests to add
- file-size risks

Accepted C0 decisions to carry forward:
- MD-1: match full normalized paths and individual path segments.
- MD-2: include share/container names in the normalized match target when
  present.
- MD-3: C1 defines a pure canonical matcher input shape; DB reads belong to
  later cards.
- MD-4: C1 handles pure defaults and validation only; config-store registration
  and persistence belong to C3 unless the RA explicitly revises ownership.
```

## C1 Implementation Prompt

```text
Implement approved C1 only.

Do not add GUI or DB persistence in this card.
Run targeted matcher/settings tests and `git diff --check`.
Report exact commands and results.
```

## C2 Planning Prompt

```text
Plan C2 only: persistence contract for latest Sherlock summaries and capped hit
details.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- additive tables/migrations
- runtime schema guards
- store/read helper shape
- stale snapshot handling
- capped hit details with total hit count preserved
- tests on minimal and current schemas
- file-size risks
```

## C2 Implementation Prompt

```text
Implement approved C2 only.

Keep schema changes additive and guarded. Do not touch UI.
Run migration/store tests and `git diff --check`.
Report exact commands and results.
```

## C3 Planning Prompt

```text
Plan C3 only: Accessories tab titled Sherlock.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- tab registration
- settings load/save
- fixed-height scrollable pattern table
- hex color inputs and optional built-in color chooser
- invalid color/pattern error handling
- Xvfb/default-size visual validation
- tests and file-size risks
```

## C3 Implementation Prompt

```text
Implement approved C3 only.

Keep this to the Accessories tab and settings surface.
Run GUI tests, Xvfb dialog check, and `git diff --check`.
Report exact commands and results.
```

## C4 Planning Prompt

```text
Plan C4 only: Server List Risk column and standalone Scan Sherlock Selected.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- Risk column placement and blank clear/no-hit behavior
- row tint via Treeview tags
- selected-host standalone Sherlock scan flow
- reusable scan-and-persist helper shape for C5 post-probe reuse
- missing snapshot skip counts
- no network guarantee
- tests and file-size risks
```

## C4 Implementation Prompt

```text
Implement approved C4 only.

Do not add post-probe hooks or Web UI work in this card.
Run Server List table/action tests and `git diff --check`.
Report exact commands and results.
```

## C5 Planning Prompt

```text
Plan C5 only: optional post-probe Sherlock hook.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- where the hook runs after snapshot persistence
- how it reuses the standalone matcher/store path
- how probe status and ransomware indicators remain unchanged
- tests for enabled/disabled/failure paths
- file-size risks
```

## C5 Implementation Prompt

```text
Implement approved C5 only.

Do not change Sherlock UI or Web UI in this card.
Run post-probe/dashboard/detail tests and `git diff --check`.
Report exact commands and results.
```

## C6 Planning Prompt

```text
Plan C6 only: desktop details and Web UI read-only Sherlock display.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- desktop details summary
- Web UI row badge/details shape
- no Web editing/action endpoint
- helper modules to avoid large-file growth
- `experimental/webui/db.py` starts at 1687 lines, so this card should keep
  that file at near-zero net growth
- tests and file-size risks
```

## C6 Implementation Prompt

```text
Implement approved C6 only.

Do not add Web UI mutation endpoints.
Run desktop details tests, Web UI API/static tests, and `git diff --check`.
Report exact commands and results.
```

## C7 Planning Prompt

```text
Plan C7 only: validation, visual QA, docs, and lessons learned closeout.

Return a concise implementation plan. Do not edit files.

The plan must cover:
- targeted test matrix
- Xvfb visual checks
- README and technical reference sync
- lessons learned update
- final file-size audit
```

## C7 Implementation Prompt

```text
Implement approved C7 only.

Run the final targeted validation set, update docs, update lessons learned, and
report PASS/FAIL with exact commands.
Do not commit.
```

## C8 Planning Prompt

```text
Plan C8 only: pure Sherlock User color tags model.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- user color settings: User1/User2/User3 default empty; valid values empty or
  #RRGGBB; existing severity colors still require #RRGGBB
- stable color-tag tokens: none/user1/user2/user3, with unknown tokens
  degrading to no tag
- optional color_tag on custom patterns and matcher hits
- serialization/backward compatibility for existing Sherlock settings
- matcher behavior: severity precedence and hit count unchanged
- purity guardrails: no DB, GUI, filesystem, network, or protocol coupling
- tests and file-size risks

Do not include DB migrations, GUI changes, Server List/Web display changes, or
probe summary work in this card.
```

## C8 Implementation Prompt

```text
Implement approved C8 only.

Keep this card pure model/settings/matcher work. Do not touch DB migrations,
GUI, Server List/Web display, or batch summaries.
Run targeted Sherlock model/serialize/matcher/purity tests and `git diff --check`.
Report exact commands and results. Do not commit.
```

## C9 Planning Prompt

```text
Plan C9 only: Sherlock tag persistence and display contract.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- additive nullable columns: sherlock_results.display_color_tag and
  sherlock_hits.color_tag
- guarded runtime reads/writes for every touched column
- selected display tag rule: highest-severity tagged hit; ties preserve matcher
  order; store token independent of whether color is currently configured
- legacy/partial schema fallback to severity-only display
- shape changes for result/detail/risk summary readers
- tests on minimal/current/partial schemas
- file-size risks, especially existing near-limit DB/Web files

Do not change Accessories UI or batch summary UI in this card.
```

## C9 Implementation Prompt

```text
Implement approved C9 only.

Keep schema changes additive and guarded. Do not change Accessories UI or batch
summary UI.
Run persistence/risk-summary/Web-read helper tests and `git diff --check`.
Report exact commands and results. Do not commit.
```

## C10 Planning Prompt

```text
Plan C10 only: Sherlock Accessories user colors and pattern manager dialog.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- main Sherlock tab layout: severity colors row, User1/User2/User3 row, Manage
  Patterns button, Save/status
- user color validation and colorchooser behavior
- tall modal Sherlock Patterns dialog with scrollable Treeview and moved
  Add/Edit/Enable-Disable/Delete/Restore controls
- Add/Edit dialog Color tag dropdown for custom patterns
- staged edit behavior: dialog changes remain in memory until main Save
- Xvfb/default-size visual checks
- tests and file-size risks

Do not change DB migrations or probe summary behavior in this card.
```

## C10 Implementation Prompt

```text
Implement approved C10 only.

Keep this to Sherlock settings UI/pattern manager behavior. Do not change DB
migrations or probe summaries.
Run Sherlock tab/dialog tests, Xvfb checks, GUI guardrails, and `git diff --check`.
Report exact commands and results. Do not commit.
```

## C11 Planning Prompt

```text
Plan C11 only: existing Sherlock display surfaces use User tint.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- shared tint/risk display helper to avoid duplicated precedence logic
- Server List Risk tint: User color wins when configured, otherwise severity
  color; Risk text remains HIGH/MED/LOW
- desktop details display of per-hit user tag labels when available
- Web UI read-only badges/details using the same tint contract and no mutation
  routes
- blank/stale/no-hit quiet contract
- tests and file-size risks

Do not add probe batch summary Risk column in this card.
```

## C11 Implementation Prompt

```text
Implement approved C11 only.

Do not add probe batch summary Risk column in this card.
Run Server List, desktop details, Web UI read-only tests, and `git diff --check`.
Report exact commands and results. Do not commit.
```

## C12 Planning Prompt

```text
Plan C12 only: probe batch summary Sherlock Risk highlighting.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- how post-probe Sherlock display data reaches summary rows without extra
  network/probe/content work
- optional Risk column in shared batch summary dialog
- row tint tags using the same User-color-wins fallback contract
- preserving existing summary layout when no row has fresh Risk
- CSV export including Risk only when visible
- dashboard/provider and Server List batch summary coverage
- Xvfb/default-size visual check for a Risk-highlighted summary
- tests and file-size risks

Do not change Sherlock settings UI or DB schema in this card.
```

## C12 Implementation Prompt

```text
Implement approved C12 only.

Do not change Sherlock settings UI or DB schema in this card.
Run batch summary, dashboard/provider, Server List summary tests, Xvfb visual
check, and `git diff --check`.
Report exact commands and results. Do not commit.
```

## C13 Planning Prompt

```text
Plan C13 only: V2 color-highlighting closeout.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

The plan must cover:
- targeted validation matrix across C8-C12
- Xvfb screenshots for Sherlock tab, pattern manager, and probe summary
- README/technical reference sync
- lessons learned update
- final file-size audit
- handling any known unrelated full-suite flakes distinctly from V2 failures

Do not add product behavior in this card.
```

## C13 Implementation Prompt

```text
Implement approved C13 only.

Run the final targeted validation set, update docs/lessons learned, capture
visual QA evidence, and report PASS/FAIL with exact commands.
Do not commit.
```

## C14 Planning Prompt

```text
Plan C14 only: Sherlock color swatch picker polish.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

Source anchor: Tk's built-in color chooser is `tkinter.colorchooser.askcolor`;
official docs: https://docs.python.org/3/library/tkinter.colorchooser.html

The plan must cover:
- replacing visible hex Entry controls and `...` buttons in the Sherlock tab
  color rows with fixed-size swatch buttons
- using Tk's built-in colorchooser.askcolor from the swatch buttons
- preserving the existing underlying hex-string StringVars and sherlock.json
  wire format
- severity colors remaining required and non-clearable
- User1/User2/User3 showing `None` when empty and providing Clear controls
- defensive rendering for invalid internal values while Save still rejects them
- README/technical-reference wording updates only where visible hex-field text
  would become misleading
- Xvfb/default-size visual QA of Accessories -> Sherlock at the current
  655px-safe dialog width

Do not change matcher, DB schema/migrations, persistence readers/writers, scan
flow, risk-display behavior, Web UI behavior, or pattern-manager behavior in
this card.

Run Sherlock tab tests, Accessories geometry tests, `pytest -k sherlock`, GUI
guardrails, `git diff --check`, and a runtime file-size audit for
`sherlock_tab.py`.
Report exact commands and results. Do not commit.
```

## C14 Implementation Prompt

```text
Implement approved C14 only.

Replace the Sherlock tab color controls with swatch-based picker controls per
the accepted C14 plan. Keep the saved settings contract unchanged and avoid any
non-UI Sherlock behavior changes.

Run the approved targeted tests, Xvfb/default-size visual check, guardrails,
`git diff --check`, and file-size audit. Report PASS/FAIL with exact commands.
Do not commit.
```

## C15 Planning Prompt

```text
Plan C15 only: Sherlock Pattern Manager built-in lifecycle + Copy.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

Workflow reminder: Codex is PA/RA, Claude is DA. Do not commit. The RA commits
only after HI accepts completed work.

The plan must cover:
- built-ins becoming deletable from the staged pattern list
- built-ins remaining code-defined and not directly overwritten
- Edit on a built-in opening the Add flow prefilled from that built-in, saving
  as a new custom pattern with a new key
- a `Copy` button that is enabled/valid only for exactly one selected row and
  opens Add prefilled from the selected built-in or custom
- additive settings serialization for deleted built-ins as `builtin_deleted`,
  distinct from existing `builtin_disabled`
- legacy Sherlock settings loading unchanged when `builtin_deleted` is absent
- `Restore Built-ins` clearing deleted/disabled built-in state and restoring
  code defaults while preserving custom patterns
- staged-only behavior: manager changes do not persist until the main Sherlock
  Save writes the settings shard
- README/technical-reference wording updates only if behavior would otherwise
  be misleading
- Xvfb/default-size visual QA showing the action row with Copy

Do not implement category combobox, multi-select, filters, export, matcher
changes, DB schema/migrations, risk-display changes, Web UI changes, or scan
flow changes in this card.

Run Sherlock serialization/settings tests, Sherlock tab tests, Accessories
geometry tests, `pytest -k sherlock`, GUI guardrails, `git diff --check`, and a
runtime file-size audit for touched production files.
Report exact commands and results. Do not commit.
```

## C15 Implementation Prompt

```text
Implement approved C15 only.

Add built-in deletion, edit-as-copy, single-row Copy, and additive
`builtin_deleted` settings support per the accepted C15 plan. Keep this to
Sherlock settings/model/serialization and the pattern manager UI; do not add
category combobox, multi-select, filters, export, or non-UI Sherlock behavior.

Run the approved targeted tests, Xvfb/default-size visual check, guardrails,
`git diff --check`, and file-size audit. Report PASS/FAIL with exact commands.
Do not commit.
```

## C16 Planning Prompt

```text
Plan C16 only: Sherlock Pattern Add/Edit category combobox.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

Workflow reminder: Codex is PA/RA, Claude is DA. Do not commit. The RA commits
only after HI accepts completed work.

Source anchor: Tk `ttk.Combobox` supports editable dropdowns using
`state="normal"`; official docs: https://docs.python.org/3/library/tkinter.ttk.html

The plan must cover:
- replacing the free-text Category `Entry` in Add/Edit Pattern with an editable
  `ttk.Combobox(state="normal")`
- populating dropdown values from the current staged pattern categories
- de-duplicating categories case-insensitively, preserving a stable display
  value, and sorting for predictable UI
- allowing typed new category values
- trimming whitespace and falling back to `Custom` only when the category is
  blank on OK
- ensuring newly staged categories appear in later Add/Edit dialogs without
  requiring main Sherlock Save/reopen
- preserving C15 behavior: built-in Edit/Copy opens Add-prefilled and saves a
  new custom row; custom Edit still edits in place
- Xvfb/default-size visual QA of Add/Edit showing the Category combobox

Do not implement multi-select, double-click, filters, export, matcher changes,
DB schema/migrations, risk-display changes, Web UI changes, or scan-flow
changes in this card.

Run Sherlock tab tests, relevant serialization/settings tests if touched,
Accessories geometry tests, `pytest -k sherlock`, GUI guardrails,
`git diff --check`, and a runtime file-size audit for touched production files.
Report exact commands and results. Do not commit.
```

## C16 Implementation Prompt

```text
Implement approved C16 only.

Replace the Pattern Add/Edit Category text entry with an editable combobox fed
by staged pattern categories per the accepted C16 plan. Keep this limited to the
pattern dialog/category behavior and preserve C15 built-in lifecycle semantics.

Run the approved targeted tests, Xvfb/default-size visual check, guardrails,
`git diff --check`, and file-size audit. Report PASS/FAIL with exact commands.
Do not commit.
```

## C16.5 Planning Prompt

```text
Plan C16.5 only: Sherlock Pattern Manager Save & Close.

For this reply, produce a plan and stop. After RA/HI acceptance, execute the
approved plan in this worktree before sending the completion report.

Workflow reminder: Codex is PA/RA, Claude is DA. Do not commit. The RA commits
only after HI accepts completed work.

The plan must cover:
- adding a `Save & Close` button in the Pattern Manager action row between
  `Restore Built-ins` and `Close`
- `Save & Close` using the existing Sherlock tab save/validation path so the
  same settings shard is written and existing color/option/pattern validation is
  preserved
- closing the Pattern Manager only when save succeeds
- existing `Close` still closing without saving when there are no unsaved
  pattern-manager changes
- existing `Close` warning when there are unsaved pattern-manager changes, with
  a cancel path that leaves the manager open
- unsaved-change tracking for Pattern Manager actions: Add, Edit, Copy, Delete,
  Enable/Disable, and Restore Built-ins
- dirty state clearing after successful `Save & Close`
- preserving existing parent-tab Save behavior for the main Sherlock tab
- Xvfb/default-size visual QA showing `Save & Close` between Restore and Close

Do not implement multi-select, double-click, filters, export, matcher changes,
DB schema/migrations, risk-display changes, Web UI changes, or scan-flow
changes in this card.

Run Sherlock tab tests, relevant settings/serialization tests if touched,
Accessories geometry tests, `pytest -k sherlock`, GUI guardrails,
`git diff --check`, and a runtime file-size audit for touched production files.
Report exact commands and results. Do not commit.
```

## C16.5 Implementation Prompt

```text
Implement approved C16.5 only.

Add Pattern Manager `Save & Close` plus unsaved-close warning per the accepted
C16.5 plan. Keep this limited to pattern-manager save/close behavior and
preserve C15/C16 pattern lifecycle/category behavior.

Run the approved targeted tests, Xvfb/default-size visual check, guardrails,
`git diff --check`, and file-size audit. Report PASS/FAIL with exact commands.
Do not commit.
```
