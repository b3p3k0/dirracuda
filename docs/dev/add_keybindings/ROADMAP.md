# Keyboard Accessibility Roadmap

Date: 2026-05-07

## Phase 1 (Shipped)

1. Shared keybinding infrastructure + contract tests.
2. Dashboard + canonical scan-flow dialog contract.
3. Admin/ops surfaces, server list/detail, and running tasks coverage.
4. App-global `Ctrl/Cmd+Q/H/T` and helper-text polish.
5. Docs sync + lessons/risk updates.

## Phase 2 (Current)

Cutline:
- Include: SMB/FTP/HTTP browser windows and file/image viewers.
- Exclude: legacy scan dialogs, protocol-deprecated paths, configurable keymaps.

Implementation sequence:
1. Add/refresh keyboard source-of-truth docs (`KBD_QUICKREF.md`, phase docs updates).
2. Extend shared helper contract for browser navigation and viewer shortcuts.
3. Wire shared browser helper in FTP/HTTP core path and SMB custom path.
4. Wire viewer helper in file/image viewers (including save-if-available behavior).
5. Add/extend targeted helper + browser/viewer behavior tests.
6. Run surgical compile/pytest validation and HI keyboard-only checks.
7. Sync `README.md`, `docs/TECHNICAL_REFERENCE.md`, and lessons learned.

## Phase 3 (Later)

Potential future work:
1. Remove deprecated legacy scan dialogs entirely (no parity expansion).
2. Optional additional keyboard polish for non-canonical/deferred windows if still retained.
