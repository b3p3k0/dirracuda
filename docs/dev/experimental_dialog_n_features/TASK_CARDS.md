# Experimental Dialog + Features - Task Cards (Claude-Ready)

Date: 2026-04-17
Execution model: one small issue/card at a time, explicit PASS/FAIL evidence.

## Global Rules (All Cards)

1. Reproduce/confirm issue first.
2. Apply smallest safe fix (surgical edits only).
3. Run targeted validation for touched components.
4. Report exact commands with PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. If blocked, report blocker + exact human unblock commands + expected result.
7. Check touched file line counts before and after edits.

## File Size Rubric (Required on touched files)

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:
- If a touched file exceeds 1700 lines, pause and add a modularization/refactor plan before continuing.

## Completion Semantics (Required)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Required Response Format (Per Card)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

---

## C0 - Contract Freeze + Baseline (Plan Only)

Goal:
Lock runtime contracts and test seams before implementation.

Scope:
1. Inventory current entrypoint wiring for Reddit experimental actions.
2. Confirm patch-sensitive tests and callback paths.
3. Freeze card-level validation commands and manual HI checks.

Deliverables:
1. `docs/dev/experimental_dialog_n_features/SPEC.md`
2. `docs/dev/experimental_dialog_n_features/ASCII_SKETCHES.md`
3. `docs/dev/experimental_dialog_n_features/ROADMAP.md`
4. `docs/dev/experimental_dialog_n_features/TASK_CARDS.md`
5. `docs/dev/experimental_dialog_n_features/CLAUDE_PROMPTS.md`
6. `docs/dev/experimental_dialog_n_features/OPEN_QUESTIONS.md`

Validation:
```bash
rg -n "reddit_grab_callback|Reddit Grab \(EXP\)|Reddit Post DB \(EXP\)" gui/dashboard/widget.py gui/components/unified_scan_dialog.py gui/components/server_list_window/window.py
rg -n "_handle_reddit_grab_button_click|show_reddit_browser_window|open_add_record_dialog" gui -g '*.py'
```

HI test needed:
- No.

---

## C1 - Experimental Dialog Scaffold + Dashboard Button

Issue:
No centralized UI surface for experimental features.

Scope:
1. Add dashboard `Experimental` button between `DB Tools` and `Config`.
2. Add `experimental_features_dialog` with `ttk.Notebook`.
3. Add feature registry scaffold and tab-render contract.
4. Create initial tabs:
   - Reddit (action stubs acceptable in this card)
   - placeholder (placeholder content)
5. Add one-time experimental warning with `Don't show again` dismiss behavior.

Primary touch targets:
1. `gui/dashboard/widget.py`
2. `gui/components/dashboard.py` (if patch-safe re-export adjustments required)
3. `gui/components/experimental_features_dialog.py` (new)
4. `gui/components/experimental_features/__init__.py` (new)
5. `gui/components/experimental_features/registry.py` (new)
6. `gui/components/experimental_features/reddit_tab.py` (new)
7. `gui/components/experimental_features/placeholder_tab.py` (new)

Definition of done:
1. Experimental dialog opens from dashboard.
2. Tab-per-feature rendering works from registry.
3. Experimental warning + dismiss preference is functional.
4. No behavior change yet to legacy Reddit entrypoints (migration happens next card).

Validation:
```bash
python3 -m py_compile gui/dashboard/widget.py gui/components/experimental_features_dialog.py gui/components/experimental_features/registry.py gui/components/experimental_features/reddit_tab.py gui/components/experimental_features/placeholder_tab.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open dashboard and click `Experimental`.
2. Confirm dialog appears with tabs `Reddit` and `placeholder`.

---

## C2 - Reddit Tab Action Wiring + Promotion-Path Preservation

Issue:
Reddit workflows must move to Experimental dialog without losing practical capability.

Scope:
1. Wire Reddit tab `Open Reddit Grab` -> existing dashboard Reddit grab handler.
2. Wire Reddit tab `Open Reddit Post DB` -> reddit browser launch.
3. Preserve add-to-db promotion when server-list context is available.
4. If context unavailable, degrade gracefully with explicit user message.
5. Remove legacy Reddit entrypoint buttons in the same implementation pass:
   - Start Scan `Reddit Grab (EXP)`
   - Server List `Reddit Post DB (EXP)`
6. Remove related plumbing that is now unused, but keep patch-safe compatibility where required.

Primary touch targets:
1. `gui/dashboard/widget.py`
2. `gui/components/dashboard.py` (if patch-safe re-export adjustments required)
3. `dirracuda`
4. `gui/main.py`
5. `gui/components/reddit_browser_window.py` (only if callback/UX handling needs small adaptation)
6. `gui/tests/test_dashboard_reddit_wiring.py`
7. `gui/tests/test_reddit_browser_window.py`

Definition of done:
1. Reddit tab launches both actions successfully.
2. Existing scan-idle gating for Reddit Grab remains intact.
3. Add-to-db remains usable when callback context exists.
4. Legacy Reddit buttons are removed with no transition overlap.

Validation:
```bash
python3 -m py_compile gui/dashboard/widget.py dirracuda gui/main.py gui/components/reddit_browser_window.py gui/components/unified_scan_dialog.py gui/components/server_list_window/window.py
./venv/bin/python -m pytest gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_reddit_browser_window.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_server_list_card4.py -q
```

HI test needed:
- Yes.
- Steps:
1. Experimental -> Reddit -> Open Reddit Grab; confirm dialog opens and run can start only when idle.
2. Experimental -> Reddit -> Open Reddit Post DB; confirm browser opens.
3. From Reddit browser, test `Add to dirracuda DB` on one row and confirm Add Record flow still works.
4. Confirm Start Scan and Server List no longer show legacy Reddit buttons.

---

## C3 - Post-Removal Hardening (No Legacy Reintroduction)

Issue:
After same-pass removal, regressions can reintroduce duplicate entrypoints or break scan/server behaviors.

Scope:
1. Verify legacy Reddit entrypoints remain removed.
2. Harden tests to guard against accidental reintroduction.
3. Ensure scan/server baseline behavior remains unchanged post-removal.

Primary touch targets:
1. `gui/tests/test_dashboard_scan_dialog_wiring.py`
2. `gui/tests/test_dashboard_reddit_wiring.py`
3. `gui/tests/test_server_list_card4.py`
4. optional minimal code touch only if regression discovered

Definition of done:
1. Tests explicitly assert no legacy Reddit buttons in Start Scan/Server List surfaces.
2. Reddit workflows remain reachable from Experimental dialog.
3. No regression in Start Scan normal behavior.

Validation:
```bash
python3 -m py_compile gui/components/unified_scan_dialog.py gui/components/server_list_window/window.py gui/dashboard/widget.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_server_list_card4.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open Start Scan dialog and verify `Reddit Grab (EXP)` is absent.
2. Open Server List and verify `Reddit Post DB (EXP)` is absent.
3. Confirm Experimental dialog still provides both Reddit actions.

---

## C4 - placeholder Module Scaffold

Issue:
Need explicit second module/tab proving future add/remove scalability.

Scope:
1. Add `experimental/placeholder/` scaffold.
2. Ensure `placeholder` tab sources its metadata/content from module-level scaffold.
3. Keep tab clearly marked as non-functional placeholder.

Primary touch targets:
1. `experimental/placeholder/__init__.py` (new)
2. optional `experimental/placeholder/README.md` (new)
3. `gui/components/experimental_features/placeholder_tab.py`
4. `gui/components/experimental_features/registry.py`

Definition of done:
1. placeholder module exists and is importable.
2. placeholder tab is rendered from registry.
3. No runtime side effects.

Validation:
```bash
python3 -m py_compile experimental/placeholder/__init__.py gui/components/experimental_features/placeholder_tab.py gui/components/experimental_features/registry.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
```

HI test needed:
- No.

---

## C5 - Docs + Regression Hardening

Issue:
Operator/docs still describe old Reddit entrypoints.

Scope:
1. Update README experimental section to new access path.
2. Add/adjust technical notes for new dialog path and feature tabs.
3. Add focused tests for experimental dialog rendering and action routing.

Primary touch targets:
1. `README.md`
2. `docs/TECHNICAL_REFERENCE.md`
3. `gui/tests/test_experimental_features_dialog.py` (new)
4. any touched existing tests for updated entrypoint text/assumptions

Definition of done:
1. Docs match new UX.
2. New dialog tests pass.
3. No stale references to removed Reddit buttons in active docs.

Validation:
```bash
python3 -m py_compile gui/components/experimental_features_dialog.py gui/components/experimental_features/registry.py
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_reddit_browser_window.py -q
rg -n "Reddit Grab \(EXP\)|Reddit Post DB \(EXP\)" README.md docs/TECHNICAL_REFERENCE.md
```

HI test needed:
- Yes.
- Steps:
1. Read README experimental section and confirm click path matches UI.
2. Validate one full Reddit flow from Experimental dialog.

---

## C6 - Final Validation + Evidence Report

Issue:
Need explicit closeout evidence and residual-risk accounting.

Scope:
1. Run final targeted suite for touched dashboard/reddit/scan/server-list flows.
2. Record line-count before/after for touched non-test Python files.
3. Publish validation report in workspace with PASS/FAIL summary.

Primary touch targets:
1. `docs/dev/experimental_dialog_n_features/VALIDATION_REPORT.md` (new)
2. `docs/dev/experimental_dialog_n_features/ROADMAP.md` (status update)
3. `docs/dev/experimental_dialog_n_features/TASK_CARDS.md` (status update)

Definition of done:
1. Automated evidence captured with exact commands.
2. Manual HI checks listed with PASS/FAIL/PENDING.
3. Residual risks and assumptions explicit.

Validation:
```bash
python3 -m py_compile gui/dashboard/widget.py gui/components/unified_scan_dialog.py gui/components/server_list_window/window.py gui/components/experimental_features_dialog.py gui/components/experimental_features/*.py experimental/placeholder/__init__.py
./venv/bin/python -m pytest gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_reddit_browser_window.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_server_list_card4.py gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:
- Yes (final sign-off).

---

## Prompt Seed (Generic)

```text
Implement Card C{N} from docs/dev/experimental_dialog_n_features/TASK_CARDS.md.

Constraints:
- Preserve existing behavior outside the requested card scope.
- Keep edits minimal and reversible.
- Guard runtime-state checks (scan-active, callback availability).
- Preserve Reddit add-to-DB promotion path unless the card explicitly changes it.
- No commits.

Deliver:
- Issue / Root cause / Fix summary
- Files changed
- Exact validation commands + PASS/FAIL
- File line counts before/after (touched files)
- Risks/assumptions
- HI manual test checklist
```
