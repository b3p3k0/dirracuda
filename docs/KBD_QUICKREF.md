# Keyboard Quick Reference (Dirracuda)

Updated: 2026-05-07

## Global (Any Focused Window)

| Context | Shortcut | Action |
|---|---|---|
| App-global | `Ctrl+Q` / `Cmd+Q` | Quit app (normal running-task confirmation flow) |
| App-global | `Ctrl+H` / `Cmd+H` | Open User Manual |
| App-global | `Ctrl+T` / `Cmd+T` | Toggle theme |

## Dashboard

| Context | Shortcut | Action |
|---|---|---|
| Dashboard | `Alt+1` | Start Scan |
| Dashboard | `Alt+2` | Servers |
| Dashboard | `Alt+3` | DB Tools |
| Dashboard | `Alt+4` | Experimental |
| Dashboard | `Alt+5` | Config |
| Dashboard | `Alt+6` | About |
| Dashboard | `Alt+7..0` | Reserved no-op (consumed) |

## Shared Dialog/Window Contract

| Context | Shortcut | Action |
|---|---|---|
| Core dialogs | `Esc` | Close/cancel via existing safe handler |
| Core dialogs | `Enter` | Primary action by default |
| Core dialogs | `Ctrl+S` / `Cmd+S` | Save/apply where supported |
| Core dialogs | `Ctrl+W` / `Cmd+W` | Close non-destructive window/dialog |
| Multiline `Text` | `Enter` | Insert newline (do not submit) |
| Multiline `Text` | `Ctrl+Enter` / `Cmd+Enter` | Submit where explicitly supported |
| List/tree views | `Enter` / `KP_Enter` | Same behavior as existing double-click/open action |

## Browser Windows (SMB/FTP/HTTP)

| Context | Shortcut | Action |
|---|---|---|
| Browser | `Enter` / `KP_Enter` | Open selected row |
| Browser | `BackSpace` / `Alt+Up` | Navigate parent/up |
| Browser | `F5` / `Ctrl+R` / `Cmd+R` | Refresh current view |
| Browser | `Esc` / `Ctrl+W` / `Cmd+W` | Close browser window |

## Viewer Windows (File/Image)

| Context | Shortcut | Action |
|---|---|---|
| Viewer | `Esc` / `Ctrl+W` / `Cmd+W` | Close viewer |
| Viewer (save-enabled) | `Ctrl+S` / `Cmd+S` | Save to quarantine |

## Notes

- Legacy protocol-specific scan dialogs are deprecated and excluded from active shortcut expansion.
- User-configurable keymaps are not part of the current plan.
