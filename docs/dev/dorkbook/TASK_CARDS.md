# Dorkbook v1 - Task Cards

Date: 2026-04-19  
Execution model: one small card at a time, explicit PASS/FAIL evidence.

## Global Rules (All Cards)

1. Reproduce/confirm issue first.
2. Apply smallest safe fix (surgical edits only).
3. Run targeted validation for touched components.
4. Report exact commands with PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. If blocked, report blocker + exact human unblock commands + expected result.
7. Check touched file line counts before and after edits.
8. For UI-affecting cards: update/confirm `ASCII_SKETCHES.md` first.

## File Size Rubric (Required on touched files)

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:
- If a touched file exceeds 1700 lines, pause and provide modularization plan before continuing.

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

## C0 - Contract + Sketch Freeze (Plan Only)

Goal:
1. Freeze runtime contracts and UI sketches before edits.

Definition of done:
1. `SPEC.md`, `ROADMAP.md`, `TASK_CARDS.md`, `ASCII_SKETCHES.md` complete.
2. Validation command set frozen.
3. No code edits.

Validation:
```bash
rg -n "experimental_features|registry|open_se_dork_results_db|open_reddit_post_db" gui/components gui/dashboard -g '*.py'
rg -n "sidecar|db_path|init_db|open_connection" experimental -g '*.py'
```

HI test needed:
- No.

---

## C1 - Sidecar Backend

Issue:
No Dorkbook sidecar schema/CRUD exists.

Scope:
1. Add `experimental/dorkbook/models.py`.
2. Add `experimental/dorkbook/store.py`.
3. Seed + refresh built-ins by stable key.
4. Duplicate guard and read-only protection.

Validation:
```bash
python3 -m py_compile experimental/dorkbook/models.py experimental/dorkbook/store.py
./venv/bin/python -m pytest shared/tests/test_dorkbook_store.py -q
```

HI test needed:
- No.

---

## C2 - Dorkbook Window Scaffold

Issue:
No dedicated Dorkbook window exists.

Scope:
1. Add singleton modeless window.
2. Add SMB/FTP/HTTP tabs.
3. Add list view, current-tab search, built-in italic styling.
4. Persist geometry + active tab.

Validation:
```bash
python3 -m py_compile gui/components/dorkbook_window.py
./venv/bin/python -m pytest gui/tests/test_dorkbook_window.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open Dorkbook.
2. Confirm 3 tabs and built-in rows appear.
3. Confirm built-in rows are italic.

---

## C3 - CRUD + Safety UX

Issue:
Need full add/edit/delete/copy flow with safety rules.

Scope:
1. Add modal add/edit dialog.
2. Hide edit/delete for built-ins.
3. Copy query-only payload.
4. Delete confirm with session-only mute checkbox.

Validation:
```bash
python3 -m py_compile gui/components/dorkbook_window.py
./venv/bin/python -m pytest gui/tests/test_dorkbook_window.py shared/tests/test_dorkbook_store.py -q
```

HI test needed:
- Yes.
- Steps:
1. Add custom row, edit it, copy query, delete it.
2. Confirm built-in cannot be edited/deleted.
3. Confirm delete mute suppresses prompts until app restart.

---

## C4 - Experimental Wiring

Issue:
Dorkbook not reachable from Experimental dialog.

Scope:
1. Add `gui/components/experimental_features/dorkbook_tab.py`.
2. Register `Dorkbook` in experimental feature registry.
3. Add dashboard experimental callback + launcher.
4. Ensure singleton focus behavior on repeated opens.

Validation:
```bash
python3 -m py_compile \
  gui/components/experimental_features/dorkbook_tab.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py gui/tests/test_dorkbook_window.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open Experimental dialog and confirm Dorkbook tab is present.
2. Open Dorkbook, close Experimental dialog, verify Dorkbook stays open.
3. Click Open Dorkbook again and confirm focus returns to existing window.

---

## C5 - Tests + Docs

Issue:
Need operator/developer docs and regression coverage.

Scope:
1. Update README experimental section.
2. Update technical reference for Dorkbook architecture/settings/sidecar.
3. Finalize dorkbook workspace docs + prompt pack.

Validation:
```bash
./venv/bin/python -m pytest \
  shared/tests/test_dorkbook_store.py \
  gui/tests/test_dorkbook_window.py \
  gui/tests/test_experimental_features_dialog.py -q
rg -n "Dorkbook|dorkbook.db|Open Dorkbook" README.md docs/TECHNICAL_REFERENCE.md docs/dev/dorkbook/
```

HI test needed:
- Yes.
- Steps:
1. Read Dorkbook docs.
2. Confirm click path and behavior match runtime UI.

---

## C6 - Validation + Closeout

Issue:
Need final evidence and risk accounting.

Scope:
1. Run focused validation suite.
2. Capture file line counts before/after touched files.
3. Publish `VALIDATION_REPORT.md`.

Validation:
```bash
python3 -m py_compile \
  experimental/dorkbook/models.py \
  experimental/dorkbook/store.py \
  gui/components/dorkbook_window.py \
  gui/components/experimental_features/dorkbook_tab.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest \
  shared/tests/test_dorkbook_store.py \
  gui/tests/test_dorkbook_window.py \
  gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:
- Yes (final sign-off).

