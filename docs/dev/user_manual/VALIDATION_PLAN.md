# Validation Plan

Automated:

- Compile touched python files (`python3 -m py_compile ...`).
- Run targeted unit tests for manual helpers/wiring.

HI checks:

1. Press `Ctrl/Cmd+H` from dashboard and from another focused window.
2. Re-trigger `Ctrl/Cmd+H` and confirm same manual window is focused (not duplicated).
3. In About dialog, click `User Manual`; About should close and manual should open.
4. Navigate via left-pane H1/H2 sections and verify right-pane jumps.
5. Resize manual window and confirm inline images remain fit-to-width.
6. Verify README links keyboard users to `docs/KBD_QUICKREF.md`.
