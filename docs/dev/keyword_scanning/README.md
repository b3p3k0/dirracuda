# Keyword Scanning Planning Workspace

This workspace covers the planned Sherlock feature: optional keyword and
wildcard highlighting for high-risk exposure clues found in existing probe
snapshots.

This folder is for planning, RA review, Claude prompts, and documentation only.
It does not authorize coding, schema changes, migrations, or runtime edits by
the planning agent.

## Operating Model

1. The PA/RA writes or updates one small task card at a time.
2. Claude first receives a planning prompt for that card and returns a plan.
3. The RA reviews Claude's plan for scope, risks, guardrails, and tests.
4. After HI approval, Claude receives the implementation prompt for that card.
5. The RA reviews Claude's diff, test output, file sizes, and docs impact.
6. After HI accepts a completed card, the RA commits that accepted card locally.
7. The next card does not start until the accepted card is committed.

## Current Decisions

- Feature codename: Sherlock.
- Sherlock never downloads files, reads file contents, authenticates, or probes
  by itself.
- Matching is against probe snapshot paths only.
- Users can run deeper probes to improve snapshot coverage; Sherlock does not
  expand coverage itself.
- V1 supports plain keywords, case sensitivity, and simple `*` / `?`
  wildcards. Regex/query languages are out of scope.
- Fuzzy matching is deferred.
- The Risk column is alert-only: high/med/low findings show text, clear/no-hit
  rows remain blank.
- Default colors are high `#ff4d4d`, med `#ffa31a`, low `#ffff80`, with user
  editable hex text fields.
- The pattern list must be fixed-height and scrollable.
- C0 accepted matcher decisions: C1 matches full normalized snapshot paths and
  individual path segments, includes share/container names when available, uses
  a pure canonical path-entry input shape, and defers config-store persistence
  until the UI/settings card.

## Source Anchors

- NIST SP 800-122 supports risk-based treatment of PII and confidentiality
  impact decisions: https://csrc.nist.gov/pubs/sp/800/122/final
- Python `fnmatch` provides shell-style wildcards distinct from regex:
  https://docs.python.org/3/library/fnmatch.html
- Tk `ttk.Treeview` supports row/item tags suitable for highlighting:
  https://docs.python.org/3/library/tkinter.ttk.html
- Tk ships a built-in `colorchooser` dialog:
  https://docs.python.org/3/library/tkinter.colorchooser.html
- SQLite additive table creation is via `CREATE TABLE IF NOT EXISTS`:
  https://www.sqlite.org/lang_createtable.html
- Xvfb provides a virtual framebuffer for GUI checks without display hardware:
  https://xorg.freedesktop.org/archive/X11R7.7/doc/man/man1/Xvfb.1.xhtml

## Repo Guardrails

- Runtime GUI entrypoint remains `./dirracuda`.
- GUI must keep the subprocess boundary to CLI/workflow code.
- Config uses `SMBSeekConfig`, `ConfigStore`, and `shared.path_service`.
- GUI messageboxes use `gui.utils.safe_messagebox`.
- Any `Toplevel` using `grab_set()` must finish with `ensure_dialog_focus()`.
- Additive DB work must guard by real runtime table/column presence.
- Touched files over 1700 lines require a modularization pause.
- Claude must not commit. Accepted cards are locally committed by the RA after
  HI approval; pushes remain HI-owned.
