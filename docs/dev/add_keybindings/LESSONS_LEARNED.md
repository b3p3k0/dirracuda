# Lessons Learned - Keyboard Accessibility

Date: 2026-05-07  
Status: seed (update as cards close)

## Guardrails To Carry Forward

1. Keep keybindings in shared helpers; do not reintroduce per-dialog ad-hoc drift.
2. Preserve multiline `Text` Enter behavior by default; submit via Ctrl/Cmd+Enter where needed.
3. Reuse existing close/cancel/save handlers; never bypass destructive safeguards.
4. Keep bindings scoped to owning windows/dialogs unless a shortcut is explicitly app-global by design.
5. Prefer lightweight footer hints over noisy button-text churn.
6. For list/tree surfaces, map Enter to existing double-click/open behavior for parity.
7. Dashboard/global contracts must remain explicit and test-covered (`Alt+1..6`, reserved `Alt+7..0`, global `Ctrl/Cmd+Q/H/T`).
8. Add helper-level contract tests whenever keybinding policy expands.
9. Browser keyboard additions should be wired once in shared browser machinery; avoid per-protocol drift unless SMB-specific behavior requires it.
10. Viewer save shortcuts must only bind when a real save callback exists.
