# Keyboard Accessibility Spec (Phase 1 + Phase 2)

Date: 2026-05-07  
Status: Phase 1A shipped; Phase 2 in progress

## Objective

Improve keyboard accessibility and operator efficiency by standardizing dialog/window keybindings without changing non-keyboard behavior.

## Global Contract

1. Bindings are scoped per owning window/dialog by default.
2. `Esc` triggers existing close/cancel handlers (no destructive bypass).
3. `Enter` triggers primary action by default.
4. Multiline exception: focused `Text` widgets keep newline on plain `Enter`; `Ctrl/Cmd+Enter` submits when dialog supports submit.
5. `Ctrl/Cmd+S` triggers save/apply where dialogs have save/apply semantics.
6. `Ctrl/Cmd+W` closes non-destructive dialogs/windows via existing handlers.
7. Tree/list windows with open/reopen behavior map `Enter` to the existing double-click/open action.
8. Add lightweight footer/inline hint text for discoverability.

## Dashboard Contract

`Alt+1..6` row-major mapping:

1. `Alt+1` → Start Scan  
2. `Alt+2` → Servers  
3. `Alt+3` → DB Tools  
4. `Alt+4` → Experimental  
5. `Alt+5` → Config  
6. `Alt+6` → About

Additional:

- `Alt+7..0` → reserved no-op (bound + consumed, not advertised in helper text)
- App-global shortcuts (intentional `bind_all` usage):
  - `Ctrl/Cmd+Q` → Quit via existing close-confirm flow
  - `Ctrl/Cmd+H` → Help/manual placeholder dialog
  - `Ctrl/Cmd+T` → Theme toggle

## Phase 1 Surface Matrix

- Dashboard root + About + Shodan API key prompt
- Unified Scan dialog
- Scan preflight dialogs (`ProbeConfigDialog`, `SummaryDialog`)
- Scan results dialog
- Discovery Dorks editor dialog
- App Config dialog
- DB Tools dialog
- Server List main window
- Server Detail popup
- Running Tasks window
- Batch Extract Settings dialog
- Batch Summary dialog
- ClamAV results dialog

## Out of Scope (Phase 2+)

- Legacy FTP/HTTP/old scan dialogs (deprecated, removal path)
- Additional Server List child dialogs
- User-configurable keymap settings

## Phase 2 Browser/Viewer Contract

- Browser windows (SMB/FTP/HTTP):
  - `Enter`/`KP_Enter` matches double-click open behavior
  - `BackSpace`/`Alt+Up` navigate parent/up
  - `F5`/`Ctrl/Cmd+R` refresh current view
  - `Esc`/`Ctrl/Cmd+W` close via existing handler
- Viewer windows (file/image):
  - `Esc`/`Ctrl/Cmd+W` close viewer
  - `Ctrl/Cmd+S` save to quarantine only when save callback exists

## Compatibility Rules

- Existing callbacks, persistence paths, and workflow entrypoints are unchanged.
- Existing destructive confirmations remain required.
- Existing non-keyboard interactions (buttons/menu/double-click) remain authoritative.
