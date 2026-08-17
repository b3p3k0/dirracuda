# Keyboard Control Enhancement Spec

Date: 2026-06-30
Status: Draft for HI approval

## Objective

Make keyboard control boring, predictable, and hard to regress across Dirracuda's
Tkinter surfaces. The next pass should tighten the existing implementation
instead of replacing it.

## Non-Goals

- No configurable keymap.
- No legacy protocol-specific scan dialog parity expansion.
- No broad GUI rewrite.
- No runtime entrypoint changes.
- No dependency changes.

## Operating Constraints

1. `./dirracuda` remains the only runtime entrypoint.
2. GUI scan behavior still crosses the GUI-to-CLI subprocess boundary.
3. Shared shortcut policy stays in `gui/utils/keybindings.py`.
4. Existing close/cancel/save/open callbacks remain authoritative.
5. Destructive confirmations stay in the existing handlers.
6. Direct `tkinter.messagebox` remains banned in GUI components.
7. Dialogs using `grab_set()` still finish with `ensure_dialog_focus(...)`.
8. Touched-file line counts are checked before and after every card.
9. Any touched file over 1700 lines triggers a modularization pause.
10. Tests must not hit Shodan, Censys, or live network hosts.

## Current Baseline Contract

Global:
- `Ctrl/Cmd+Q` quits through the normal running-task confirmation flow.
- `Ctrl/Cmd+H` opens the user manual.
- `Ctrl/Cmd+T` toggles theme.

Dashboard:
- `Alt+1` Start Scan
- `Alt+2` Database
- `Alt+3` Accessories
- `Alt+4` Config
- `Alt+5` About
- `Alt+6..0` reserved no-op

Shared dialogs/windows:
- `Esc` closes or cancels through the existing safe handler.
- `Enter` submits only where the surface has a primary action.
- Plain `Enter` in multiline `Text` inserts a newline.
- `Ctrl/Cmd+Enter` submits only where explicitly supported.
- `Ctrl/Cmd+S` saves/applies where save exists.
- `Ctrl/Cmd+W` closes non-destructive windows.
- Tree/list `Enter` matches double-click/open behavior.

Browser/viewer:
- Browser `Enter/KP_Enter` opens selected row.
- Browser `BackSpace/Alt+Up` navigates parent/up.
- Browser `F5/Ctrl/Cmd+R` refreshes current view.
- Browser `Esc/Ctrl/Cmd+W` closes.
- Viewer `Esc/Ctrl/Cmd+W` closes.
- Viewer `Ctrl/Cmd+S` binds only when a real save callback exists.

## Enhancement Requirements

1. Docs must name the runtime contract once and avoid stale parallel mappings.
2. Browser navigation shortcuts must be focus-safe around editable widgets.
3. Shortcut tests must cover the focus cases that caused or could cause drift.
4. Review must confirm app-global `bind_all` remains limited to `Q/H/T`.
5. New bindings must return `"break"` only when the shortcut was actually handled.
6. No shortcut may bypass disabled/busy state logic already owned by the surface.
7. UI hint text should stay lightweight and match the actual binding contract.

## Flow Charts

### Scoped Shortcut Dispatch

```text
Key event
  |
  v
Focused widget/class binding
  |
  v
Owning window binding
  |
  +-- focus is editable and shortcut would edit text? --> let widget handle it
  |
  +-- shortcut supported and action safe now? ----------> existing callback -> "break"
  |
  +-- unsupported/not safe? ---------------------------> no-op or pass through
```

### Browser Parent Navigation

```text
BackSpace / Alt+Up
  |
  v
Is focus in Entry/Spinbox/Text/Combobox?
  |             |
  | yes         | no
  v             v
Do not navigate  Is browser busy?
                |          |
                | yes      | no
                v          v
              consume?   call existing _on_up()
                         |
                         v
                    current path changes or root no-op
```

### PA/RA and DA Flow

```text
HI picks card
  |
  v
PA/RA prepares prompt and acceptance gates
  |
  v
Claude/DA returns plan
  |
  v
PA/RA + HI review plan
  |
  +-- not ready --> revise plan
  |
  v
Claude/DA codes
  |
  v
PA/RA reviews diff, tests, docs, line counts
  |
  +-- small issue --> PA/RA may patch
  +-- larger issue -> kick back to DA
  |
  v
HI accepts, then commit only when explicitly requested
```

## ASCII UI Mockups

Browser footer/hint should remain short:

```text
[ Up ] [ Refresh ] [ View ] [ Download to Quarantine ] [ Cancel ]
Enter open  |  BackSpace/Alt+Up parent  |  F5/Ctrl+R refresh  |  Esc close
```

Viewer footer/hint should reflect whether save exists:

```text
[ Save to Quarantine ]                                      [ Close ]
Esc/Ctrl+W close  |  Ctrl+S save
```

```text
                                                        [ Close ]
Esc/Ctrl+W close
```

Dashboard hint should match runtime:

```text
Alt+1 Start Scan | Alt+2 Database | Alt+3 Accessories | Alt+4 Config | Alt+5 About
Ctrl+T Theme | Ctrl+H Help | Ctrl+Q Quit
```
