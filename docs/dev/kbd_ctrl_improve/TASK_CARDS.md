# Task Cards - Keyboard Control Enhancement

Date: 2026-06-30
Execution model: one small card at a time, explicit PASS/FAIL evidence.

## Global Rules

1. Reproduce/confirm before changing behavior.
2. State root cause.
3. Apply the smallest safe fix.
4. Run targeted validation for touched components.
5. Report exact commands and PASS/FAIL.
6. Do not commit unless HI explicitly says `commit`.
7. Check touched-file line counts before and after.
8. Pause before implementation if any touched file exceeds 1700 lines.
9. End each card with a `README.md` review.

## File Size Rubric

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

## Required Response Format

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short, exact steps)

---

## C0 - Contract Freeze and Stale-Doc Reconciliation

Scope:
1. Confirm runtime dashboard mapping from `dirracuda`.
2. Confirm root quick reference matches runtime.
3. Update stale old-workspace keyboard docs only if they remain misleading.
4. No runtime behavior changes.

Validation:

```bash
rg -n "Alt\\+2|Alt\\+6|Database|Servers" docs/KBD_QUICKREF.md docs/dev/add_keybindings docs/dev/kbd_ctrl_improve dirracuda gui/dashboard/widget.py
wc -l docs/KBD_QUICKREF.md docs/dev/add_keybindings/*.md docs/dev/kbd_ctrl_improve/*.md
```

Acceptance:
- Runtime mapping is unambiguous.
- Old docs no longer contradict current root quickref.
- `README.md` still points to the quick reference.

---

## C1 - Browser Editable-Focus Shortcut Guard

Scope:
1. Reproduce whether `BackSpace`/`Alt+Up` can navigate while focus is in editable widgets.
2. Add helper-level tests for Entry/Spinbox/Text/Combobox focus.
3. Harden shared browser navigation helper if confirmed.
4. Preserve browser shortcuts when focus is on the tree or normal non-editing widgets.

Validation:

```bash
python3 -m py_compile gui/utils/keybindings.py gui/tests/test_keybindings_contract.py gui/tests/test_browser_viewer_keybindings.py
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py gui/tests/test_browser_viewer_keybindings.py -q
```

Acceptance:
- Editable widgets keep editing behavior.
- Tree/browser shortcuts still dispatch expected actions.
- No per-protocol shortcut drift.

---

## C2 - Browser/Viewer Runtime Wiring Review

Scope:
1. Review FTP/HTTP shared browser path and SMB override path.
2. Verify each path calls the shared browser helper once.
3. Verify viewer save shortcut binds only with a save callback.
4. Add targeted tests only for gaps found in the review.

Validation:

```bash
python3 -m py_compile gui/browsers/core.py gui/browsers/smb_browser.py gui/components/file_viewer_window.py gui/components/image_viewer_window.py
./venv/bin/python -m pytest gui/tests/test_browser_viewer_keybindings.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_smb_browser_window.py gui/tests/test_smb_virtual_root.py -q
```

Acceptance:
- No duplicate invocation path.
- Save shortcut remains conditional.
- Existing protocol browser behavior is preserved.

---

## C3 - Global Binding and Hint Audit

Scope:
1. Confirm app-global `bind_all` remains limited to `Ctrl/Cmd+Q/H/T`.
2. Confirm hint strings match actual bindings.
3. Keep UI text changes minimal.
4. Do not touch `dirracuda` unless the plan explains line-count impact.

Validation:

```bash
rg -n "bind_all|bind_global_app_shortcuts|Alt\\+|Ctrl/Cmd|BackSpace|F5|Cmd\\+S|Ctrl\\+S" dirracuda gui docs
./venv/bin/python -m pytest gui/tests/test_dirracuda_dashboard_keybindings.py gui/tests/test_help_manual_dialog.py gui/tests/test_keybindings_contract.py -q
```

Acceptance:
- Global binding scope is unchanged.
- Hints are accurate.
- No stale shortcut claims remain in active docs.

---

## C4 - Manual Keyboard QA and Docs Closeout

Scope:
1. Run focused automated regression.
2. Walk HI through keyboard-only checks.
3. Update `README.md` only if public behavior changed.
4. Update lessons learned.

Validation:

```bash
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py gui/tests/test_browser_viewer_keybindings.py gui/tests/test_dirracuda_dashboard_keybindings.py -q
wc -l README.md docs/KBD_QUICKREF.md docs/dev/kbd_ctrl_improve/*.md
```

Acceptance:
- Automated result is PASS.
- HI manual result is PASS or clearly PENDING with exact remaining steps.
- README review is recorded.
