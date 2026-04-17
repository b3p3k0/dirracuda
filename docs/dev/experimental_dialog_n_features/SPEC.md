# Experimental Dialog + Features Spec

Date: 2026-04-17
Status: Draft for HI review
Scope: GUI entrypoint consolidation for experimental features

## Problem Statement

Experimental features are currently exposed through scattered UI entrypoints:
1. `Start Scan` dialog includes `Reddit Grab (EXP)`.
2. `Server List` header includes `Reddit Post DB (EXP)`.

This increases UI clutter and creates an expansion problem as more experimental features are added.

## Goals

1. Add a dashboard-level `Experimental` button that opens a dedicated experimental features dialog.
2. Use tab-per-feature UX so adding/removing features is low-friction.
3. Keep existing Reddit module behavior reachable from the new dialog.
4. Add a second feature scaffold named `placeholder` (placeholder tab/module for future work).
5. Preserve Reddit `Add to dirracuda DB` promotion flow.
6. Add a one-time experimental notice with a dismiss checkbox (`Don't show again`) for operator clarity.
7. Remove legacy Reddit experimental buttons from Start Scan and Server List surfaces immediately (no overlap period).
8. Preserve behavior outside this scope.

## Non-Goals

1. No redesign of scan pipeline behavior.
2. No changes to Reddit ingestion parsing/storage logic.
3. No schema migration changes in main DB or reddit sidecar DB.
4. No broad redesign of Server List or Start Scan dialogs beyond removing experimental button clutter.

## Current State Evidence

1. Dashboard launches Start Scan with Reddit callback:
   - `gui/dashboard/widget.py` (`_show_quick_scan_dialog` passes `reddit_grab_callback`).
2. Start Scan dialog renders `Reddit Grab (EXP)` when callback exists:
   - `gui/components/unified_scan_dialog.py` (`_create_button_panel`, `_open_reddit_grab`).
3. Server List renders `Reddit Post DB (EXP)` in header:
   - `gui/components/server_list_window/window.py` (`_create_header`).
4. Reddit browser supports "Add to dirracuda DB" only when `add_record_callback` is provided:
   - `gui/components/reddit_browser_window.py` (`show_reddit_browser_window(..., add_record_callback=...)`).

## Proposed UX Contract

1. Dashboard header gains `Experimental` button between `DB Tools` and `Config`.
2. `Experimental` opens a modal dialog containing a `ttk.Notebook` with one tab per feature.
3. Initial tabs:
   - `Reddit`
   - `placeholder`
4. Reddit tab actions:
   - `Open Reddit Grab`
   - `Open Reddit Post DB`
5. Dialog shows an informational warning on first open:
   - text to effect: "These features are experimental and may be flaky."
   - checkbox: `Don't show again`
   - persisted preference in GUI settings.
6. Legacy entrypoint removals:
   - Remove `Reddit Grab (EXP)` from Start Scan dialog.
   - Remove `Reddit Post DB (EXP)` from Server List header.

## Proposed Architecture

### Component layout

```text
gui/components/experimental_features_dialog.py
  - Dialog shell + tab notebook
  - Feature registry consumption

gui/components/experimental_features/
  - __init__.py
  - registry.py            # feature list + metadata
  - reddit_tab.py          # tab builder + actions
  - placeholder_tab.py    # placeholder tab builder

experimental/placeholder/
  - __init__.py            # placeholder module scaffold
```

### Feature registry contract

Use a small registry object per tab to keep the interface fluid:

```text
feature_id: str
label: str
build_tab(parent, context) -> tk.Widget
is_enabled(context) -> bool
```

This allows future add/remove without dialog rewrites.

## Behavior Preservation Requirement

### Reddit "Add to dirracuda DB" path

Risk: If Reddit browser is opened from dashboard directly, `add_record_callback` may be missing, disabling promotion flow.

Proposed preservation strategy:
1. Allow dashboard drill-down callback to return the server list window instance when opening/reusing `server_list`.
2. Experimental Reddit tab obtains that server list instance and opens Reddit browser with:
   - `parent=server_window.window`
   - `add_record_callback=server_window.open_add_record_dialog`
3. If server window cannot be acquired, open Reddit browser without callback and show explicit limitation message.

This preserves current capability while still removing the Server List header button clutter.

## UI/Runtime State Rules

1. `Experimental` button is permanent and always visible in the dashboard header.
2. `Open Reddit Grab` action must preserve existing idle-state gating:
   - no launch if scan state is non-idle
   - no launch if a Reddit grab run is already active
3. `Open Reddit Post DB` can remain available during scans (read-only browsing + manual actions).

## File/Module Size Discipline

Execution cards must enforce line-count rubric checks before/after edits on touched files:

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `2000+`: unacceptable unless explicitly justified

Any touched file exceeding 1700 lines requires an immediate refactor plan before further feature work.

## Acceptance Criteria

1. Dashboard shows `Experimental` button and dialog opens.
2. Dialog renders tab-per-feature (`Reddit`, `placeholder`).
3. Dialog includes experimental notice + dismiss checkbox persisted across restarts.
4. Reddit tab launches both existing Reddit workflows.
5. Start Scan no longer exposes `Reddit Grab (EXP)`.
6. Server List no longer exposes `Reddit Post DB (EXP)` in header.
7. Reddit browser add-to-db promotion remains functional when callback context is available.
8. No regression in scan start/stop and server-list baseline interactions.
9. Docs updated to reflect new entrypoint path.

## Validation Strategy

1. Targeted unit tests for new dialog + wiring.
2. Existing Reddit dashboard/browser tests updated and passing.
3. Focused GUI manual HI flow for:
   - Experimental dialog discoverability
   - Reddit Grab launch
   - Reddit Post DB launch
   - add-to-db promotion path
4. Explicit PASS/FAIL reporting per card with exact commands.
