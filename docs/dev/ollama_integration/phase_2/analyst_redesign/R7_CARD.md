# R7 — Read-first report view (two layers) + Export + Copy

- Date: 2026-09-18
- Type: GUI code card. codex implements, Claude validates (incl. xvfb smoke).
- Depends on: R5 (render + read_report_json). After R7 a user can view a read-first report from
  the existing Accessories -> Analyst tab's "Completed Reports" button (end-to-end testable).

## Goal

Replace the report window's content with the read-first two-layer view sourced from report.json:
the read (Layer 1) leading, the grounded facts table (Layer 2) below, plus Export and Copy. Retire
the U4 accept/reject picker and the DB-paged findings/inventory view (Amendment A1 / UI_CONTRACT
section 5).

## Deliverable — `gui/components/analyst_report_window.py`

Keep: the class/opener signature `show_analyst_report_window(parent, *, db_path=None)` and the
`__init__(self, parent, *, db_path=None)`, the Toplevel lifecycle, `get_theme()` styling, and the
completed-run picker (a combobox of completed runs).

Replace the content (was: coverage card + Findings/Inventory notebook + accept/reject/export of DB
findings) with the read-first view per UI_CONTRACT.md section 3:
- **Run picker**: list completed runs. On select, call `service.read_report_json(run_id,
  path=self.db_path)` -> (report dict, changed: bool). If it raises "no report.json / legacy"
  (a run without report.json), show "Legacy run - re-run to view a read." and disable Export/Copy.
- **Read block** (Layer 1): render from `report["read"]` + `report["run"]`:
  a Risk badge (HIGH/MED/LOW), the host_summary paragraph, `Likely owner`, `Contacts`,
  `Files read` / `Flagged files`, and a TOP EXPOSURES list. Show the exact `unverified_notice`
  line. All values via Tk widget text (never dynamic HTML). Prefix owner/contacts as the JSON
  already labels them.
- **Facts table** (Layer 2): a Treeview with columns Kind | Value | File | Rank, rows from
  `report["facts"]`, with a category filter (All / PII / Financial / Contact / Demographic). A
  `low`-ranked fact shows in facts but the read's top exposures already exclude it.
- **Changed badge**: if `changed` is True, show a visible "changed since saved" badge; still show
  the report (warn-not-block).
- **Export...**: `filedialog.asksaveasfilename` with .md (default), .json, .txt, .csv; write via
  `report_render.render(report, fmt)` (.json writes `report_json.dumps_report(report)`). Hardened
  write is fine to reuse if available, else a plain owner-only write is acceptable here (this is a
  user-chosen destination, not the sealed run dir).
- **Copy**: copy the whole report as Markdown to the clipboard (`render_markdown`) via
  `self.clipboard_clear()` / `self.clipboard_append(...)`.

Conventions (MANDATORY): route messageboxes through `gui.utils.safe_messagebox`; call
`gui.utils.dialog_helpers.ensure_dialog_focus(window, parent)` as the final build step (the file
is missing it today - add it); style via `SMBSeekTheme.apply_to_widget` named styles only; never
touch widgets from a worker thread (reads are quick/synchronous here or via `after`).

## Tests — `gui/tests/test_analyst_r7.py`

Prefer MagicMock for logic where possible; a `gui_smoke`-marked test may build the window under a
withdrawn Tk root (run via xvfb). Cover:
- Read block populates from a synthetic report.json (risk badge text, owner, contacts, exposures,
  the exact unverified_notice).
- Facts filter narrows rows by category.
- Export writes a .md file whose bytes equal `render_markdown(report)`; .json equals
  `dumps_report`.
- Copy places `render_markdown(report)` on the clipboard.
- The changed flag shows the badge; a legacy run (no report.json) shows the legacy message and
  disables Export/Copy.

## Constraints

- Read-only view: no accept/reject, no action on findings, model output is display-only. No raw
  document content beyond report.json's bounded quotes.
- Only `gui/components/analyst_report_window.py` and the new test (+ if strictly needed, a tiny
  additive helper in service.py to list completed runs for the picker - prefer reusing an existing
  lister). Do NOT change the worker/finalize/schema. File < 1700 lines.

## Acceptance (Claude validates)

1. `./venv/bin/python -m pytest gui/tests/test_analyst_r7.py -q` passes (via xvfb-run if it builds
   a real root).
2. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` => 0 failed; messagebox and
   theme guardrail tests still pass.
3. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed (unchanged backend).
4. A manual xvfb smoke builds the window on a synthetic report.json without exceptions.
