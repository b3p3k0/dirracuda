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
