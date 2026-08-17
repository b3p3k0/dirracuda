# Keyboard Control Enhancement Roadmap

Date: 2026-06-30
Status: Draft

## Phase A - Baseline and Doc Alignment

1. Freeze current shortcut behavior from runtime and tests.
2. Reconcile stale docs from the old keyboard workspace.
3. Record line-count risk before any DA plan touches runtime files.

Exit:
- current contract is named in one source-of-truth doc
- stale `Alt+2` mapping is resolved
- no code behavior changes

## Phase B - Focus-Safety Hardening

1. Add/adjust helper-level tests for editable-widget focus.
2. Harden browser parent/up shortcuts so editing fields are not hijacked.
3. Confirm shortcuts still consume events when they perform the browser action.

Exit:
- tests fail before/with the old focus-hijack behavior
- targeted tests pass after the DA fix
- browser behavior remains unchanged outside editable-focus cases

## Phase C - UI Hint and Runtime Audit

1. Audit dashboard, browser, viewer, and core dialog hint text.
2. Keep hints short and contract-accurate.
3. Confirm no new `bind_all` usage outside global `Ctrl/Cmd+Q/H/T`.

Exit:
- hint text matches runtime
- no broad UI churn
- no entrypoint or subprocess-boundary changes

## Phase D - Closeout

1. Run targeted regression.
2. Review `README.md` and update only if user-facing behavior changed.
3. Update lessons learned.
4. Nudge HI for a commit before starting the next unrelated task.
