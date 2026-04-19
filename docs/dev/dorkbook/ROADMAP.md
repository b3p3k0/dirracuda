# Dorkbook v1 Roadmap

Date: 2026-04-19  
Execution model: one card at a time, explicit PASS/FAIL evidence

## Objective 0: Freeze Contracts + Sketches

Outcome:
1. Implementation boundaries and UI sketches are locked before code edits.

Tasks:
1. Confirm experimental wiring seams.
2. Confirm sidecar schema/runtime guard approach.
3. Freeze required validation commands.
4. Freeze `ASCII_SKETCHES.md` as the UI source of truth.

## Objective 1: Sidecar Backend

Outcome:
1. Dorkbook sidecar DB is stable and seeded with built-ins.

Tasks:
1. Add models/constants/errors.
2. Add store init/open/schema guard and CRUD.
3. Add built-in upsert/refresh policy.

## Objective 2: Dorkbook Window Scaffold

Outcome:
1. Singleton modeless Dorkbook window with 3 protocol tabs.

Tasks:
1. Add main window and protocol tabs.
2. Add list view + current-tab search.
3. Add built-in italic rendering + action visibility rules.

## Objective 3: CRUD + Safety

Outcome:
1. Add/Edit/Delete/Copy workflow is complete and safe.

Tasks:
1. Add modal add/edit dialog.
2. Enforce duplicate guard + read-only built-ins.
3. Add delete confirmation with session mute checkbox.
4. Ensure copy uses query-only payload.

## Objective 4: Experimental Wiring

Outcome:
1. Dorkbook is reachable from Experimental tab and independent from dialog lifecycle.

Tasks:
1. Add `Dorkbook` experimental tab module.
2. Add registry entry and dashboard bridge callback.
3. Verify repeated open focuses existing singleton.

## Objective 5: Tests + Docs

Outcome:
1. Feature is covered by focused tests and operator/dev docs.

Tasks:
1. Add sidecar/store tests.
2. Add window/singleton/wiring tests.
3. Update README and technical reference.
4. Keep dorkbook workspace docs synchronized.

## Objective 6: Validation Closeout

Outcome:
1. Final evidence and residual risks are documented.

Tasks:
1. Run targeted compile + pytest suites.
2. Record command outputs as PASS/FAIL.
3. Record touched-file line counts with rubric.
4. Publish `VALIDATION_REPORT.md`.

