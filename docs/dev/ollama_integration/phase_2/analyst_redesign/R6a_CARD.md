# R6a — Simplified launch + Advanced dialog + Runs "Result" risk column

- Date: 2026-09-18
- Type: GUI code card. codex implements, Claude validates (xvfb).
- Depends on: R3c (report.json), R7 (report view). UI_CONTRACT.md sections 1, 2, 5.
- Note: batch export (Export selected...) is a separate card R6b. R6a wires the Runs multi-select
  and the buttons but the batch export dialog itself is R6b (a placeholder/disabled "Export
  selected..." is fine until R6b).

## Goal

Reshape the Accessories -> Analyst tab to the read-first launcher: one folder input, a Read
(Quick/Full) choice, an Analyze button, and an Advanced... dialog holding everything else. Change
the Runs "Coverage" column to "Result" (risk when done, percent when active).

## Deliverables

### 1. service.py — surface risk in the Runs list (additive)
- `list_run_summaries` LEFT JOINs `analyst_read` and `AnalystRunSummary` gains
  `risk_level: str | None` (HIGH/MED/LOW or None). Read-only query; no new writes.
- Add a `@property result_label` on AnalystRunSummary: `"● HIGH risk"` etc. when risk_level is
  set; else the existing progress/percent string for active runs; else `"-"`.

### 2. gui/components/experimental_features/analyst_tab.py — launch simplification
Per UI_CONTRACT.md section 1:
- Top-level inputs ONLY: **Folder** (path + Browse), **Name** (optional, auto-fills from folder
  basename), **Read** radio (Quick look / Full read), **Analyze** button, **Advanced...** button.
- Map Read -> the existing stored mode value: Quick look -> "fast", Full read -> "deep" (keep the
  backend/DB mode values unchanged so R3c's mapping still yields read_mode quick/full).
- Move to the **Advanced dialog** (a Toplevel, grab_set + ensure_dialog_focus): Output folder,
  Source (a folder / from a saved scan + the manifest combo + Reload), Model server (Local
  selected; Remote controls present but DISABLED with a "later card" note), Model (the current
  static qualified-model label for now; the dropdown is R4), and the "Offer a quick review after
  an extraction" toggle (moved from the main tab, same setting key).
- Runs Treeview: rename the `progress` column heading "Coverage" -> "Result"; fill it from
  `item.result_label`. Enable multi-select (`selectmode="extended"`) and a "Select all" control.
  Keep Open report / Resume / Cancel. Add an "Export selected..." button that is present but
  disabled with a tooltip "batch export (R6b)" until R6b lands.
- Keep the launch path: Analyze still calls service.create_and_launch / create_manifest_and_launch
  off the Tk thread exactly as today (GUI -> service -> detached worker). Do not change the worker.

### 3. Tests — gui/tests/test_analyst_r6a.py
- service: a completed run with an analyst_read row surfaces risk_level and result_label
  "● HIGH risk"; an active run shows the percent/progress; a run with neither shows "-".
- tab: the main form exposes exactly Folder/Name/Read/Analyze/Advanced (assert the advanced-only
  fields are NOT on the main form); Name auto-fills from the folder; opening Advanced builds the
  dialog with output/source/model-server/model/offer controls; Quick->fast / Full->deep mapping;
  the Runs column heading is "Result"; multi-select is enabled. Use MagicMock/withdrawn-root as the
  existing analyst tab tests do; a gui_smoke may build under xvfb.

## Constraints
- Conventions: safe_messagebox, ensure_dialog_focus on the Advanced Toplevel, named SMBSeekTheme
  styles only, no worker-thread widget teardown. GUI -> service -> subprocess boundary unchanged.
- Only service.py (additive risk_level + result_label), analyst_tab.py, and the new test. Do NOT
  change the worker/finalize/schema. Files < 1700 lines.

## Acceptance (Claude validates)
1. `xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_analyst_r6a.py -q` passes.
2. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` => 0 failed (guardrails green).
3. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
4. A manual xvfb smoke builds the tab + opens Advanced without exceptions.
