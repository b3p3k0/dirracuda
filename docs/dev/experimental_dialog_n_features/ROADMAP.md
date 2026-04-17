# Experimental Dialog + Features Roadmap

Date: 2026-04-17
Execution model: one card at a time, explicit PASS/FAIL evidence

## Baseline

Current experimental entrypoints are distributed:
1. Start Scan dialog -> Reddit Grab
2. Server List header -> Reddit Post DB

Goal is to converge these into one dashboard-level Experimental dialog with tab-per-feature composition.

## Objective 0: Contracts + Baseline Freeze

Outcome:
- Runtime call paths and patch-sensitive seams are documented before edits.

Tasks:
1. Inventory current Reddit button wiring in Dashboard/Scan Dialog/Server List.
2. Inventory tests and patch paths likely to break with callback/signature moves.
3. Freeze validation command set and manual HI checklist.

## Objective 1: Experimental Dialog Foundation

Outcome:
- New Experimental dialog is available from dashboard via dedicated button.

Tasks:
1. Add dashboard `Experimental` button between `DB Tools` and `Config`.
2. Add dialog shell with `ttk.Notebook`.
3. Add feature-registry pattern to keep interface fluid.
4. Add one-time experimental notice with dismiss checkbox and persisted preference.

## Objective 2: Reddit Tab Migration

Outcome:
- Reddit workflows are launched from the Reddit tab in Experimental dialog.

Tasks:
1. Wire `Open Reddit Grab` to existing dashboard Reddit flow.
2. Wire `Open Reddit Post DB` using callback path that preserves add-to-db when possible.
3. Keep scan-state safety checks unchanged for ingestion launch.

## Objective 3: Legacy Button Removal

Outcome:
- UI clutter reduced in the same implementation pass; no duplicate entrypoints remain.

Tasks:
1. Remove Reddit button from Start Scan dialog immediately after Experimental Reddit actions are wired.
2. Remove Reddit button from Server List header immediately after Experimental Reddit actions are wired.
3. Update affected tests in the same pass.

## Objective 4: placeholder Feature Scaffold

Outcome:
- A second tab/module exists to prove add/remove feature workflow.

Tasks:
1. Add `experimental/placeholder/` placeholder module scaffold.
2. Add `placeholder` tab with non-functional placeholder content.
3. Ensure registry-driven tab rendering includes it.

## Objective 5: Validation + Docs Closeout

Outcome:
- Feature lands with explicit evidence and clear docs.

Tasks:
1. Run focused automated validation.
2. Run manual HI checks for experimental dialog and migrated actions.
3. Update README experimental entrypoint text.
4. Publish final PASS/FAIL report and residual risks.

## Exit Criteria

1. Dashboard has one clear `Experimental` entrypoint.
2. Reddit workflows reachable only via Experimental dialog.
3. Tab-per-feature architecture implemented (`Reddit`, `placeholder`).
4. Existing behavior outside requested scope preserved.
5. Validation evidence includes exact commands and PASS/FAIL.
