# Task Cards - Keyboard Accessibility (Phase 1 + Phase 2)

Date: 2026-05-07  
Execution model: one small issue/card at a time, explicit PASS/FAIL evidence.

## Global Rules

1. Reproduce/confirm before changing behavior.
2. Smallest safe fix only.
3. Run targeted validation for touched components.
4. Report exact commands + PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. Check touched file line counts before/after.

## File Size Rubric

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:
- If a touched file exceeds 1700 lines, pause and propose modularization before proceeding.

## Required Completion Semantics

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
- HI test needed? (yes/no + exact steps)

---

## C1 - Shared Keybinding Utility + Contract Tests

Scope:
1. Add centralized keyboard helper module.
2. Add helper-level contract tests (submit/close/save/tree/dashboard-alt).

Validation:
```bash
python3 -m py_compile gui/utils/keybindings.py gui/tests/test_keybindings_contract.py
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py -q
```

---

## C2 - Dashboard Keyboard Contract

Scope:
1. Wire `Alt+1..6` and reserved `Alt+7..0`; remove `Alt+T`.
2. Wire app-global `Ctrl/Cmd+Q`, `Ctrl/Cmd+H`, `Ctrl/Cmd+T`.
3. Apply keybindings to dashboard actionable dialogs (About, API key prompt).
4. Add dashboard/global keybinding tests.

Validation:
```bash
python3 -m py_compile dirracuda gui/dashboard/widget.py gui/tests/test_dirracuda_dashboard_keybindings.py
./venv/bin/python -m pytest gui/tests/test_dirracuda_dashboard_keybindings.py -q
```

---

## C3 - Unified Scan Flow Keybindings

Scope:
1. Unified Scan dialog contract + hint.
2. Preflight probe/summary dialog contracts + hints.
3. Scan results contract + hint.
4. Discovery Dorks editor contract + hint.

Validation:
```bash
python3 -m py_compile \
  gui/components/unified_scan_dialog.py \
  gui/components/scan_preflight.py \
  gui/components/scan_results_dialog.py \
  gui/components/scan_dork_editor_dialog.py
./venv/bin/python -m pytest \
  gui/tests/test_scan_dialog_nonblocking_singleton.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_scan_preflight_probe_depth.py \
  gui/tests/test_scan_results_dialog.py -q
```

---

## C4 - Admin/Ops Dialog Keybindings

Scope:
1. App Config + DB Tools keybinding contracts.
2. Running Tasks Enter-reopen and close shortcuts.
3. Batch Extract Settings, Batch Summary, ClamAV Results contracts + hints.

Validation:
```bash
python3 -m py_compile \
  gui/components/app_config_dialog.py \
  gui/components/db_tools_dialog.py \
  gui/components/running_tasks_window.py \
  gui/components/batch_extract_dialog.py \
  gui/components/batch_summary_dialog.py \
  gui/components/clamav_results_dialog.py
./venv/bin/python -m pytest \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_db_tools_dialog.py \
  gui/tests/test_server_list_running_tasks_integration.py \
  gui/tests/test_clamav_results_dialog.py -q
```

---

## C5 - Server List Main + Detail Popup Keybindings

Scope:
1. Server List main window contract (including tree Enter behavior).
2. Server Detail popup contract with multiline-notes-safe Enter behavior.

Validation:
```bash
python3 -m py_compile \
  gui/components/server_list_window/window.py \
  gui/components/server_list_window/details.py
./venv/bin/python -m pytest \
  gui/tests/test_server_list_running_tasks_integration.py \
  gui/tests/test_server_list_details_probe_section.py \
  gui/tests/test_server_list_card4.py -q
```

---

## C6 - Documentation + Final Validation

Scope:
1. Update README + Technical Reference keybinding docs.
2. Run final targeted regression for changed areas.
3. Update lessons learned and risk/open-questions docs.

Validation:
```bash
python3 -m py_compile dirracuda gui/dashboard/widget.py gui/components/*.py gui/components/server_list_window/*.py gui/utils/keybindings.py
./venv/bin/python -m pytest \
  gui/tests/test_keybindings_contract.py \
  gui/tests/test_dirracuda_dashboard_keybindings.py \
  gui/tests/test_scan_results_dialog.py \
  gui/tests/test_scan_preflight_probe_depth.py \
  gui/tests/test_server_list_running_tasks_integration.py \
  gui/tests/test_clamav_results_dialog.py -q
```

---

## P2-C1 - Browser/Viewer Contract Helpers + Quickref

Scope:
1. Add `KBD_QUICKREF.md`.
2. Extend `gui/utils/keybindings.py` with browser/viewer shortcut helpers.
3. Add helper-level contract tests for new helpers.

Validation:
```bash
python3 -m py_compile \
  gui/utils/keybindings.py \
  gui/tests/test_keybindings_contract.py
./venv/bin/python -m pytest \
  gui/tests/test_keybindings_contract.py -q
```

---

## P2-C2 - Browser Window Wiring (FTP/HTTP/SMB)

Scope:
1. Wire browser helper in `gui/browsers/core.py` and `gui/browsers/smb_browser.py`.
2. Add/update browser behavior tests for Enter/up/refresh/close contract.

Validation:
```bash
python3 -m py_compile \
  gui/browsers/core.py \
  gui/browsers/smb_browser.py \
  gui/tests/test_browser_viewer_keybindings.py
./venv/bin/python -m pytest \
  gui/tests/test_browser_viewer_keybindings.py \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_smb_virtual_root.py -q
```

---

## P2-C3 - Viewer Wiring (File/Image)

Scope:
1. Wire viewer helper in file/image viewers.
2. Ensure `Ctrl/Cmd+S` only binds when save callback is available.
3. Add/update viewer behavior tests.

Validation:
```bash
python3 -m py_compile \
  gui/components/file_viewer_window.py \
  gui/components/image_viewer_window.py \
  gui/tests/test_browser_viewer_keybindings.py
./venv/bin/python -m pytest \
  gui/tests/test_browser_viewer_keybindings.py -q
```
