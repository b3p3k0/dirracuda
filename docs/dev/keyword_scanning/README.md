# Keyword Scanning Planning Workspace

This workspace covers the planned Sherlock feature: optional keyword and
wildcard highlighting for high-risk exposure clues found in existing probe
snapshots.

This folder is for planning, RA review, Claude prompts, and documentation only.
It does not authorize coding, schema changes, migrations, or runtime edits by
the planning agent.

For follow-on Sherlock passes, approval of a plan authorizes the PA/RA to update
this planning packet and present the next Claude prompt only. It does not
authorize the PA/RA to implement runtime code directly.

## Operating Model

1. The PA/RA writes or updates one small task card at a time.
2. Claude first receives a planning prompt for that card and returns a plan.
3. The RA reviews Claude's plan for scope, risks, guardrails, and tests.
4. After HI approval, Claude executes the approved implementation in this
   worktree before sending a completion report.
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
- Default colors are high `#ff4d4d`, med `#ffa31a`, low `#ffff80`, stored as
  hex strings and edited through color swatches/native chooser controls.
- The pattern list must be fixed-height and scrollable.
- C0 accepted matcher decisions: C1 matches full normalized snapshot paths and
  individual path segments, includes share/container names when available, uses
  a pure canonical path-entry input shape, and defers config-store persistence
  until the UI/settings card.
- V2 color-highlighting decisions: User1/User2/User3 are optional visual tags,
  not new severities. Custom patterns may carry one tag. If a fresh finding has
  a usable user-tag color, that color wins row tint; risk text remains
  HIGH/MED/LOW severity text. Empty user colors are saved but visually inactive.
- V2 pattern management decision: the embedded pattern table moves to a tall
  modal dialog. Dialog edits are staged in the Sherlock tab and persist only
  when the existing Sherlock Save button is clicked.
- C14 swatch decision: visible hex fields and `...` buttons are replaced by
  clickable swatches. User1/User2/User3 also get Clear controls so empty user
  colors remain reachable from the UI.
- Pattern-manager improvement decisions: built-ins may be disabled or deleted,
  but editing a built-in creates a prefilled custom copy rather than modifying
  the code-defined built-in. `Restore Built-ins` restores code defaults and
  clears built-in disabled/deleted state while leaving customs alone.
- Pattern filtering uses a search box plus Category/Severity/User Tag/Enabled
  facets. JSON is the first export format and exports the full staged list, not
  only the currently filtered rows.

## Source Anchors

- NIST SP 800-122 supports risk-based treatment of PII and confidentiality
  impact decisions: https://csrc.nist.gov/pubs/sp/800/122/final
- Python `fnmatch` provides shell-style wildcards distinct from regex:
  https://docs.python.org/3/library/fnmatch.html
- Tk `ttk.Treeview` supports row/item tags suitable for highlighting:
  https://docs.python.org/3/library/tkinter.ttk.html
- Tk `ttk.Combobox` supports editable dropdowns for category selection/new
  category entry: https://docs.python.org/3/library/tkinter.ttk.html
- Tk `ttk.Treeview` supports extended selection for Ctrl/Shift multi-select:
  https://docs.python.org/3/library/tkinter.ttk.html
- Tk ships a built-in `colorchooser` dialog:
  https://docs.python.org/3/library/tkinter.colorchooser.html
- Tk `filedialog.asksaveasfilename` provides native Save As dialogs:
  https://docs.python.org/3/library/dialog.html
- Python `json` is the pattern export format for exact settings-shaped data:
  https://docs.python.org/3/library/json.html
- SQLite additive table creation is via `CREATE TABLE IF NOT EXISTS`:
  https://www.sqlite.org/lang_createtable.html
- SQLite additive column work is via `ALTER TABLE ... ADD COLUMN`:
  https://www.sqlite.org/lang_altertable.html
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
