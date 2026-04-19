# Dorkbook v1 Validation Report

Date: 2026-04-19  
Status: Implementation + adversarial hardening complete (manual HI pending)

## Automated Validation

Command 1:

```bash
python3 -m py_compile \
  experimental/dorkbook/__init__.py \
  experimental/dorkbook/models.py \
  experimental/dorkbook/store.py \
  gui/components/dorkbook_window.py \
  gui/components/experimental_features/dorkbook_tab.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py \
  gui/components/experimental_features_dialog.py \
  gui/tests/test_dorkbook_window.py \
  shared/tests/test_dorkbook_store.py \
  gui/tests/test_experimental_features_dialog.py
```

Result: PASS

Command 2:

```bash
./venv/bin/python -m pytest \
  shared/tests/test_dorkbook_store.py \
  gui/tests/test_dorkbook_window.py \
  gui/tests/test_experimental_features_dialog.py -q
```

Result: PASS (`53 passed`)

Command 3:

```bash
./venv/bin/python -m pytest \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

Result: PASS (`43 passed`)

Command 4 (new regression edges):

```bash
./venv/bin/python -m pytest \
  shared/tests/test_dorkbook_store.py::test_upsert_builtin_pack_skips_conflicting_builtin_update \
  shared/tests/test_dorkbook_store.py::test_upsert_builtin_pack_skips_conflicting_builtin_insert \
  gui/tests/test_dorkbook_window.py::test_show_dorkbook_window_handles_constructor_failure -q
```

Result: PASS (`3 passed`)

## Manual HI Validation

Status: PENDING

Checklist:
1. Open Experimental dialog and confirm `Dorkbook` tab exists.
2. Launch Dorkbook and close Experimental dialog; confirm Dorkbook remains open.
3. Open Dorkbook again; confirm existing window is focused (no duplicate).
4. In one protocol tab:
   - confirm built-in row is italic
   - confirm built-in hides Edit/Delete
5. Add custom row, edit, copy, delete.
6. Confirm delete prompt mute checkbox suppresses prompts until restart.
7. Restart app and confirm delete prompt appears again.

## File Size Rubric

See issue closeout response for before/after counts and rubric classification.

## Residual Risks / Assumptions

1. Built-in italic style uses `Treeview` tag font behavior; visual rendering can vary slightly by platform theme engine.
2. Delete-confirm mute is session-only by design (in-memory flag reset on restart).
3. Built-in refresh collisions are now skipped to preserve startup availability and existing custom data; conflicting builtin text will not apply until conflict is resolved.
4. v1 intentionally does not include direct "apply to scan/config" integration; clipboard is the only transfer path.

---

```text
AUTOMATED: PASS
MANUAL:    PENDING
OVERALL:   PENDING
```
