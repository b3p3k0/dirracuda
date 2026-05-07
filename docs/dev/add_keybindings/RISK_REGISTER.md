# Risk Register - Keyboard Accessibility Phase 1

Date: 2026-05-07

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| K1 | Duplicate shortcut invocation from overlapping binds | Medium | Medium | Centralize helper usage and consume handled events (`"break"`). |
| K2 | Enter key conflicts in multiline text areas | Medium | High | Focus-aware multiline guard with Ctrl/Cmd+Enter override only where intended. |
| K3 | Regressions in legacy/patch-sensitive dashboard paths | Medium | High | Preserve existing callback seams and add targeted dashboard binding tests. |
| K4 | Shortcut drift across dialogs over time | Medium | Medium | Keep helper module as single source and update contract tests when scope expands. |
| K5 | Platform-specific modifier mismatch (Ctrl vs Command) | Low | Medium | Bind both Ctrl and Command variants for save/close/submit flows. |
| K6 | App-global `bind_all` collisions with local widget shortcuts | Medium | Medium | Limit global scope to `Ctrl/Cmd+Q/H/T`, consume handled events, and keep focused regression tests around root/global wiring. |
| K7 | Browser navigation shortcuts conflict with platform defaults | Medium | Medium | Limit browser contract to non-destructive mappings (`Enter`, `BackSpace/Alt+Up`, refresh, close) and verify behavior in each protocol window. |
