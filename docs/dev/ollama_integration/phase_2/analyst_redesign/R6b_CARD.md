# R6b — Batch export of selected runs

- Date: 2026-09-18
- Type: GUI + helper code card. codex implements, Claude validates (xvfb).
- Depends on: R5 (render), R6a (multi-select Runs + disabled "Export selected..." button).
- UI_CONTRACT.md section 4.

## Goal

Let the operator select many runs in the Runs list and export all their reports at once via one
dialog. Enable the "Export selected..." button; add the export dialog and a testable export
helper.

## Deliverables

### 1. Export helper — `experimental/analyst/report_export.py` (new)
- `export_reports(reports, *, formats, layout, include, dest_dir) -> ExportResult` where
  `reports` is a sequence of (label, report_dict) already loaded/validated; `formats` a subset of
  {"md","json","txt","csv"}; `layout` in {"per_report","combined"}; `include` a subset of
  {"read","facts"}; `dest_dir` a directory. It renders via `report_render` (md/txt/html/csv) and
  `report_json.dumps_report` for json, honoring `include` (read-only / facts-only trims). Writes
  owner-only files; per_report writes `<label>.<ext>` (sanitize label to a safe filename, dedupe
  collisions); combined writes one `analyst-reports.<ext>` concatenation per format with clear
  separators. Returns counts + the written paths. Pure logic + bounded file IO; testable with
  tmp_path. No Tk.
- `include` trimming: "read"-only omits the facts table/array; "facts"-only omits the read block.
  At least one of read/facts must be selected (else a validation error).

### 2. Export dialog + wiring — `analyst_tab.py`
- Enable "Export selected..." when >=1 run is selected. On click open a Toplevel (grab_set +
  ensure_dialog_focus) matching UI_CONTRACT section 4: Format checkboxes (Markdown default, JSON
  default, Plain text, CSV facts), Layout radios (one file per report default / one combined
  file), Include checkboxes (The read default, The facts default), Folder (+Browse), Cancel /
  Export.
- On Export: for each selected run load `service.read_report_json(run_id)`; skip legacy runs
  without report.json and report how many were skipped; call `report_export.export_reports(...)`;
  show a result via safe_messagebox: "Exported N reports to <dir>." (and "skipped M legacy runs"
  if any). Do the load+write off the Tk thread; post the result back on the Tk thread with
  `after(...)`; the dialog is torn down only on the Tk thread.

### 3. Tests
- `shared/tests/test_analyst_r6b.py`: export_reports writes per-report md+json for 2 reports;
  combined layout writes one file per format with both reports; include=read-only omits facts and
  include=facts-only omits the read; label sanitization + collision dedup; empty include is
  rejected; csv facts are spreadsheet-guarded.
- `gui/tests/test_analyst_r6b.py`: the button enables on selection; the dialog builds with the
  section-4 controls (grab + ensure_dialog_focus); a fake selection with one legacy run reports
  the skip; Export calls export_reports with the chosen options. MagicMock / withdrawn root.

## Constraints
- report_export.py has no Tk and no DB (it takes already-loaded report dicts). Owner-only writes
  to a user-chosen dir. Escaping/guards come from report_render (values already safe).
- Conventions: safe_messagebox, ensure_dialog_focus, named styles, no worker-thread teardown.
- Only new report_export.py, analyst_tab.py, and the two new tests. Do NOT change worker/finalize/
  schema. Files < 1700 lines.

## Acceptance (Claude validates)
1. `./venv/bin/python -m pytest shared/tests/test_analyst_r6b.py -q` passes.
2. `xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_analyst_r6b.py -q` passes.
3. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` and
   `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
