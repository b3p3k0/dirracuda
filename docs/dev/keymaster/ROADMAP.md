# Keymaster v1 Roadmap

Date: 2026-04-25
Execution model: one card at a time with explicit PASS/FAIL evidence

## Objective 0: Contract Freeze

Outcome:
1. Scope, data contract, UI contract, and validation gates are locked.

Tasks:
1. Confirm resolved decisions log in `OPEN_QUESTIONS.md`.
2. Freeze `SPEC.md`, `ASCII_SKETCHES.md`, and `FLOW_CHARTS.md`.
3. Freeze validation command set in `TASK_CARDS.md`.

## Objective 1: Sidecar Backend

Outcome:
1. Keymaster sidecar store is stable with schema guardrails.

Tasks:
1. Add `experimental/keymaster/models.py`.
2. Add `experimental/keymaster/store.py`.
3. Add schema checks and duplicate-key protections.
4. Add focused store tests.

## Objective 2: Keymaster Window

Outcome:
1. Singleton modeless window supports CRUD and selection UX.

Tasks:
1. Add `gui/components/keymaster_window.py`.
2. Add list view and action row.
3. Add Add/Edit modal and Delete confirm flow.
4. Add context menu parity.

## Objective 3: Unified Apply + Config Persistence

Outcome:
1. Double-click/context/button all route to one apply function.

Tasks:
1. Implement one shared apply handler.
2. Persist selected key to active `shodan.api_key`.
3. Update `last_used_at` on successful apply.
4. Add tests for success/failure and path resolution.

## Objective 4: Experimental Wiring

Outcome:
1. Keymaster is reachable from Experimental dialog and dashboard helper routing.

Tasks:
1. Add `gui/components/experimental_features/keymaster_tab.py`.
2. Register tab in experimental feature registry.
3. Add `open_keymaster(...)` helper in dashboard experimental bridge.
4. Add wiring tests.

## Objective 5: Docs + Regression Hardening

Outcome:
1. Runtime contract and docs are in sync with implementation.

Tasks:
1. Update README and technical reference with Keymaster entry.
2. Add short operator usage notes.
3. Ensure regression tests capture known risky edges.

## Objective 6: Validation Closeout

Outcome:
1. Final evidence and residual risks are documented.

Tasks:
1. Run targeted compile + pytest suites from `venv`.
2. Record exact commands and PASS/FAIL.
3. Capture touched-file line counts with rubric.
4. Publish validation summary.
