# Keymaster v1 Task Cards

Date: 2026-04-25
Execution model: one small card at a time, explicit PASS/FAIL evidence

## Global Rules (All Cards)

1. Reproduce or confirm issue first.
2. Apply smallest safe fix (surgical edits only).
3. Run targeted validation for touched components.
4. Report exact commands with PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. If blocked, report blocker plus exact human unblock commands and expected result.
7. Check touched file line counts before and after edits.
8. Use `./venv/bin/python ...` for pytest and py_compile commands.

## File Size Rubric (Required on touched files)

1. `<=1200`: excellent
2. `1201-1500`: good
3. `1501-1800`: acceptable
4. `1801-2000`: poor
5. `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:
1. If any touched file exceeds 1700 lines, stop and propose modularization plan before continuing.

## Completion Semantics (Required)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Required Response Format (Per Card)

1. Issue:
2. Root cause:
3. Fix:
4. Files changed:
5. Validation run:
6. Result:
7. HI test needed? (yes/no + short steps)

---

## C0 - Contract and Decisions Freeze (Plan Only)

Goal:
1. Confirm locked product decisions are encoded before code edits.

Definition of done:
1. `SPEC.md` and `OPEN_QUESTIONS.md` reflect the resolved HI decisions.
2. Validation command set is frozen.
3. No production code edits.

Validation:
```bash
rg -n "Open Keymaster|keymaster|open_keymaster|experimental_features" gui/components gui/dashboard -g '*.py'
rg -n "shodan.api_key|_persist_shodan_api_key_to_config|config_path" gui -g '*.py'
```

HI test needed:
1. No.

---

## C1 - Sidecar Store + Tests

Issue:
No key manager persistence layer exists.

Scope:
1. Add `experimental/keymaster/models.py`.
2. Add `experimental/keymaster/store.py`.
3. Add focused tests in `shared/tests/test_keymaster_store.py`.

Validation:
```bash
./venv/bin/python -m py_compile experimental/keymaster/models.py experimental/keymaster/store.py
./venv/bin/python -m pytest shared/tests/test_keymaster_store.py -q
```

HI test needed:
1. No.

---

## C2 - Keymaster Window Scaffold + CRUD

Issue:
No operator UI exists for key CRUD.

Scope:
1. Add `gui/components/keymaster_window.py` singleton modeless window.
2. Add Add/Edit/Delete actions and context menu parity.
3. Implement key preview format as first4 + asterisks + last4 in table view.
4. Keep API key input masked in modal (no reveal toggle in v1).
5. Add focused tests in `gui/tests/test_keymaster_window.py`.

Validation:
```bash
./venv/bin/python -m py_compile gui/components/keymaster_window.py
./venv/bin/python -m pytest gui/tests/test_keymaster_window.py -q
```Implement C5 only from docs/dev/keymaster/TASK_CARDS.md (Docs + Technical Reference).

Scope:
1) Update README.md experimental section with Keymaster entry and usage summary.
2) Update docs/TECHNICAL_REFERENCE.md:
   - module map includes experimental/keymaster
   - experimental dialog registry includes Keymaster
   - apply behavior contract (writes shodan.api_key for future scans)
3) Sync docs/dev/keymaster/* if any wording drift remains after implementation.
4) No code changes outside docs.

Validation:
- rg -n "Keymaster|keymaster.db|Open Keymaster|shodan.api_key" README.md docs/TECHNICAL_REFERENCE.md docs/dev/keymaster/
- (Optional sanity) ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q -k keymaster

Rules:
- No commit.
- Surgical edits only.
- Report in required format:
  - Issue:
  - Root cause:
  - Fix:
  - Files changed:
  - Validation run:
  - Result:
  - HI test needed? (yes/no + short steps)
- Include touched file line counts before/after with rubric.


HI test needed:
1. Yes.
2. Open Keymaster and verify Add/Edit/Delete works for sample rows.

---

## C3 - Unified Apply + Config Persistence

Issue:
Active key switching is manual and error-prone.

Scope:
1. Implement one shared apply function in Keymaster window.
2. Wire double-click, context `Apply`, and button `Apply` to same path.
3. Persist selected key to active `shodan.api_key` with safe targeted write.
4. Enforce contract that running scans keep start-time key while applied key affects future scans.
5. Add regression tests for populate+persist behavior and failure paths.

Validation:
```bash
./venv/bin/python -m py_compile gui/components/keymaster_window.py
./venv/bin/python -m pytest \
  gui/tests/test_keymaster_window.py \
  gui/tests/test_dashboard_api_key_gate.py -q
```

HI test needed:
1. Yes.
2. Save two test keys, apply one using each of the 3 paths, confirm `conf/config.json` key changes each time.

---

## C4 - Experimental Wiring

Issue:
Keymaster must be discoverable through existing experimental workflow.

Scope:
1. Add `gui/components/experimental_features/keymaster_tab.py`.
2. Register feature in `gui/components/experimental_features/registry.py`.
3. Add open helper in `gui/components/dashboard_experimental.py`.
4. Extend relevant experimental dialog tests.

Validation:
```bash
./venv/bin/python -m py_compile \
  gui/components/experimental_features/keymaster_tab.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:
1. Yes.
2. Open Experimental dialog, confirm `Keymaster` tab appears and opens/focuses singleton window.

---

## C5 - Docs + Technical Reference

Issue:
New feature needs operator/developer docs and architecture notes.

Scope:
1. Update `README.md` experimental section.
2. Update `docs/TECHNICAL_REFERENCE.md` module map and flow.
3. Sync keymaster workspace docs with final implementation.

Validation:
```bash
rg -n "Keymaster|keymaster.db|Open Keymaster" README.md docs/TECHNICAL_REFERENCE.md docs/dev/keymaster/
```

HI test needed:
1. Yes.
2. Confirm docs reflect real click path and behavior.

---

## C6 - Validation + Closeout

Issue:
Need final evidence and risk accounting for handoff.

Scope:
1. Run focused compile + pytest suite for keymaster changes.
2. Capture touched-file line-count rubric before/after.
3. Publish concise validation summary.

Validation:
```bash
./venv/bin/python -m py_compile \
  experimental/keymaster/models.py \
  experimental/keymaster/store.py \
  gui/components/keymaster_window.py \
  gui/components/experimental_features/keymaster_tab.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest \
  shared/tests/test_keymaster_store.py \
  gui/tests/test_keymaster_window.py \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_api_key_gate.py -q
```

HI test needed:
1. Yes (final sign-off).

---

## C7 - Shodan Query Credits Visibility (Startup Check + Recheck All)

Issue:
Need quick visibility of remaining Shodan query credits per stored key to reduce trial-and-error during testing.

Scope:
1. Add `Query Credits` column to Keymaster table UI.
2. On Keymaster window startup, run one non-blocking credit check for all stored SHODAN keys.
3. Add `Recheck All` and `Recheck Selected` actions in Keymaster (button + context menu) for broad and isolated checks.
4. Keep apply behavior unchanged (button/right-click/double-click still only apply selected key to config).
5. Use Shodan API status data from `api.info()` (`/api-info`) and display only query credits.
6. Keep credits runtime-only (in-memory); no sidecar schema changes in this card.
7. Add focused tests in `gui/tests/test_keymaster_window.py`.

Display contract:
1. Success: show numeric query credits (stringified integer).
2. Invalid key/auth failure: show `Invalid key`.
3. Other API/network failures: show `Error`.
4. Not yet checked: show `Not checked`.
5. While running refresh: show `Checking...` for rows being checked.

Safety/behavior rules:
1. UI must remain responsive; no blocking network calls on Tk main thread.
2. UI updates must occur on Tk thread (use `after` for cross-thread updates).
3. No API key values in logs/status text/errors.
4. One refresh job at a time; repeated clicks during active refresh should not spawn concurrent refresh storms.

Validation:
```bash
./venv/bin/python -m py_compile gui/components/keymaster_window.py
./venv/bin/python -m pytest gui/tests/test_keymaster_window.py -q
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q -k keymaster
```

HI test needed:
1. Yes.
2. Open Keymaster with at least two saved keys.
3. Confirm `Query Credits` populates shortly after window opens.
4. Click `Recheck All` and confirm values/states update again.
5. Confirm Apply flows still work unchanged.
