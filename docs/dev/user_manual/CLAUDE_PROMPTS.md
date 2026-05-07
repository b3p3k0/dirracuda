# Claude Execution Prompts (User Manual Phase 1)

## Prompt A - Manual module

Implement `gui/components/help_manual_dialog.py` with:
- `open_help_manual_dialog(parent, *, theme=None)` singleton open/focus
- two-pane Tk layout (left Treeview nav, right Text renderer)
- markdown support: headings, lists, code blocks, links, images, pipe tables
- safe fallback messaging for missing docs/images

## Prompt B - Wiring

Wire:
- global `Ctrl/Cmd+H` path to manual opener
- About dialog `User Manual` button: close About then open manual
- preserve compatibility via `open_help_stub_dialog(...)` alias

## Prompt C - Tests + docs

Add targeted tests for helper parsing/link/image scale behavior and manual wiring.
Promote keyboard quickref to `docs/KBD_QUICKREF.md`, simplify README keyboard
section to link-out, and add `docs/HISTORY.md` placeholder.
