# Keyboard Quick Reference (Dirracuda)

Date: 2026-05-07  
Status: Phase 1A shipped; Phase 2 in progress

## App-Global Shortcuts (Any Focused Window)

- `Ctrl/Cmd+Q` quit app (uses existing safe close-confirm flow)
- `Ctrl/Cmd+H` open Help placeholder dialog
- `Ctrl/Cmd+T` toggle theme

## Dashboard Shortcuts

- `Alt+1` Start Scan
- `Alt+2` Servers
- `Alt+3` DB Tools
- `Alt+4` Experimental
- `Alt+5` Config
- `Alt+6` About
- `Alt+7..0` reserved no-op (consumed, not advertised in helper text)

## Shared Dialog Contract

- `Esc` close/cancel via existing safe handler
- `Enter` primary action by default
- `Ctrl/Cmd+S` save/apply where supported
- `Ctrl/Cmd+W` close non-destructive window/dialog
- Multiline safety:
  - Plain `Enter` remains newline in focused `Text`
  - `Ctrl/Cmd+Enter` submits only where explicitly supported
- List/tree parity:
  - `Enter` / `KP_Enter` follows existing double-click open/reopen behavior

## Browser/Viewer Contract (Phase 2)

### Browser Windows (SMB/FTP/HTTP)

- `Enter` / `KP_Enter` open selected row
- `BackSpace` / `Alt+Up` navigate parent/up
- `F5` / `Ctrl/Cmd+R` refresh current view
- `Esc` / `Ctrl/Cmd+W` close browser window

### Viewer Windows (File/Image)

- `Esc` / `Ctrl/Cmd+W` close viewer
- `Ctrl/Cmd+S` save to quarantine only when save callback exists

## Explicit Exclusions

- Legacy protocol-specific scan dialogs are deprecated and not in phase scope
- User-configurable keymaps are out of plan

## Validation Baseline

- `python3 -m py_compile <touched files>`
- `./venv/bin/python -m pytest <targeted tests> -q`

## Docs Sync Targets

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `docs/dev/add_keybindings/LESSONS_LEARNED.md`
