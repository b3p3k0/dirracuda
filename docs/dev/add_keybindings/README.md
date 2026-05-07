# Add Keybindings Workspace

Date: 2026-05-07  
Status: Phase 1A shipped; Phase 2 browser/viewer implementation in progress

## Purpose

Introduce a consistent, focus-safe keyboard-accessibility contract across core Dirracuda dialogs/windows and dashboard actions.

## Phase 1 Scope (This Workspace)

- Shared keybinding utility + shared shortcut-hint helper
- Dashboard Alt shortcuts and actionable dashboard dialogs
- Unified scan flow dialogs (launch/preflight/results/dork editor)
- App Config + DB Tools dialogs
- Server List main window + Server Detail popup
- Running Tasks window
- Shared operational dialogs: Batch Extract Settings, Batch Summary, ClamAV Results

## Explicit Deferrals

- Legacy protocol-specific scan dialogs
- Additional server-list child dialogs beyond Server Detail popup

## Phase 2 Scope (Current)

- SMB/FTP/HTTP browser windows
- File/image viewer windows
- Shared helper and behavior test coverage for browser/viewer shortcut contract

## Artifacts

- `SPEC.md` — decision-complete behavior contract
- `ROADMAP.md` — implementation sequencing and phase boundaries
- `TASK_CARDS.md` — surgical execution cards
- `CLAUDE_PROMPTS.md` — downstream implementation/review prompts
- `KBD_QUICKREF.md` — living shortcut source-of-truth
- `VALIDATION_PLAN.md` — automated + HI keyboard validation
- `LESSONS_LEARNED.md` — carry-forward guardrails
- `OPEN_QUESTIONS.md` — remaining unresolved items
- `RISK_REGISTER.md` — primary execution risks and mitigations
