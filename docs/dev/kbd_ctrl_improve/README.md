# Keyboard Control Enhancement Workspace

Date: 2026-06-30
Status: PA/RA planning scaffold; no rewrite approved

## Purpose

This workspace supervises the next keyboard-control pass for Dirracuda. The
first keyboard track already shipped shared helpers, dashboard shortcuts, core
dialog shortcuts, browser shortcuts, viewer shortcuts, and quick-reference docs.
This pass is an enhancement/audit track, not a first implementation pass.

## PA/RA Boundary

Codex is acting as PA/RA here:

- create and maintain planning docs in this folder
- help HI decide scope, risks, acceptance, and sequencing
- review Claude/DA plans before code work
- review Claude/DA diffs after code work
- make only small review fixes when that is clearly safer than a kickback

Codex will not execute the rewrite after plan approval. Claude/DA owns coding
cards after HI and PA/RA approve each card plan.

## Current Reality

- Runtime entrypoint is `./dirracuda`; `gui/main.py` is legacy/import-only.
- Existing keyboard helpers live in `gui/utils/keybindings.py`.
- Browser/viewer keyboard tests already pass: `16 passed` from
  `gui/tests/test_keybindings_contract.py` and
  `gui/tests/test_browser_viewer_keybindings.py`.
- `dirracuda` is exactly 1700 lines. Any DA plan that touches it needs a
  no-growth explanation or a modularization step.
- Existing docs disagree in one place: root `docs/KBD_QUICKREF.md` and runtime
  map dashboard `Alt+2` to Database, while old `docs/dev/add_keybindings/SPEC.md`
  maps `Alt+2` to Servers. Runtime wins; docs need cleanup.
- Existing browser navigation shortcuts bind parent/up to `BackSpace` and
  `Alt+Up`. The next review should verify these do not hijack editable focused
  widgets such as spinboxes.

## Artifacts

- `SPEC.md` - decision-complete behavior contract
- `ROADMAP.md` - sequencing and gates
- `TASK_CARDS.md` - one-card-at-a-time work packets for Claude/DA
- `VALIDATION_PLAN.md` - automated and HI keyboard-only checks
- `RISK_REGISTER.md` - likely failure modes and mitigations
- `LESSONS_LEARNED.md` - carry-forward notes
- `CLAUDE_PROMPTS.md` - plan/review prompts for downstream work

## Sources Checked

- Project docs/code: `README.md`, `AGENTS.md`, `CLAUDE.md`,
  `docs/TECHNICAL_REFERENCE.md`, `docs/KBD_QUICKREF.md`,
  `docs/dev/add_keybindings/*`, `gui/utils/keybindings.py`, browser/viewer tests.
- W3C WCAG Understanding 2.1.1 Keyboard:
  https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html
- Python Tkinter docs:
  https://docs.python.org/3/library/tkinter.html
- Tcl/Tk `bind` manual:
  https://www.tcl-lang.org/man/tcl8.6/TkCmd/bind.htm
- Tcl/Tk `focus` manual:
  https://www.tcl-lang.org/man/tcl8.6/TkCmd/focus.htm
- Current doc style guide:
  https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/WRITING_SOP.md
- Code review guide:
  https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/REVIEW_SOP.md

Note: the originally supplied root-level style-guide URL returns 404. The
current file is under `agent_sops/`.
