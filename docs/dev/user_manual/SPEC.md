# User Manual Spec (Phase 1)

## Objective

Provide an operator-friendly in-app manual that is reachable from keyboard and
GUI, with stable navigation and no external viewer dependency.

## Contract

- `Ctrl/Cmd+H` opens or focuses the shared User Manual window.
- About dialog includes `User Manual` button that closes About then opens manual.
- Manual docs source set:
  - `README.md`
  - `docs/TECHNICAL_REFERENCE.md`
  - `docs/KBD_QUICKREF.md`
- Left navigation depth: H1/H2.
- Right pane markdown support: headings, lists, code blocks, links, images,
  and basic pipe tables.
- Missing docs/images degrade gracefully with inline notices.

## Non-Goals

- Configurable keymaps
- New persistence/config schema
- Legacy protocol scan-dialog keyboard expansion
