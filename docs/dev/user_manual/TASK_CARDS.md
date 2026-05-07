# User Manual Task Cards

## C1 - Manual Dialog Foundation

- Create manual module with singleton open/focus behavior.
- Build two-pane layout and markdown rendering pipeline.

Validation:

- `python3 -m py_compile gui/components/help_manual_dialog.py`

## C2 - Entry Point Wiring

- Route global Help shortcut to manual opener.
- Add About dialog `User Manual` button flow.
- Keep compatibility wrapper for previous stub API.

Validation:

- `python3 -m py_compile gui/components/global_shortcuts.py gui/dashboard/widget.py gui/components/help_stub_dialog.py`

## C3 - Tests

- Add parser/renderer helper tests.
- Add manual open/focus and shortcut behavior tests.
- Add About flow behavior test.

Validation:

- `./venv/bin/python -m pytest gui/tests/test_help_manual_dialog.py gui/tests/test_dashboard_user_manual.py gui/tests/test_dirracuda_dashboard_keybindings.py -q`

## C4 - Docs Sync

- Promote quickref to `docs/KBD_QUICKREF.md`.
- Replace README keyboard details with link-out.
- Add history placeholder and planning artifacts.

Validation:

- `python3 -m py_compile dirracuda`
