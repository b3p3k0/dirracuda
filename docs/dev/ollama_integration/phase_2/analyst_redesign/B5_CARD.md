# B5 — Report viewer opens the SELECTED run (drop the redundant picker)

Status: HELD for DA (codex). One card. Commit per card, no push. Builds on B1–B3b (committed).

## Problem / goal

"Open report" always shows the most-recent report, not the one selected in the Analyst tab: the tab
calls `show_analyst_report_window(...)` with no run_id, and the window auto-selects its dropdown
index 0 (newest). The dropdown duplicates the tab's own runs list and adds maintenance for little
value.

Fix + simplify: pass the SELECTED run to the viewer and open exactly that report. Remove the run
picker (combobox + Refresh + list load) from the viewer entirely. Flow becomes: select a completed
run in the tab → Open report → read → close. **Keep the B1 Retry affordance and the read/facts/
export/copy blocks.**

## Changes

### 1. `gui/components/analyst_report_window.py`

- `show_analyst_report_window(parent, run_id=None, *, db_path=None) -> AnalystReportWindow` — return
  the instance (already does).
- `AnalystReportWindow.__init__(self, parent, run_id=None, *, db_path=None)`: store
  `self._run_id = run_id` and `self.db_path`; build; then `if run_id is not None: self._open_run(run_id)`
  (a None run_id leaves the default empty "Select a completed report." state — the current
  `_clear_report` placeholders).
- Header row (currently `_build_run_picker`): REMOVE the `_run_box` combobox, `_run_var`, the
  "Completed report:" label, the Refresh button, and the `<<ComboboxSelected>>` bind. Keep a thin
  header row that hosts the **Retry** button (hidden by default), now
  `self._retry_btn = tk.Button(row, text="Retry", command=lambda: self._open_run(self._run_id))`.
  Keep the existing status (`_status_var`) and changed (`_changed_var`) labels.
- REMOVE `_load_runs`, `_on_run_selected`, `_open_selected`, and the `self._runs` list attribute.
- ADD `_open_run(self, run_id)`:
  ```
  def _open_run(self, run_id):
      if run_id is None:
          return
      self._hide_retry()
      self._status_var.set("Opening report…")
      self._set_report_actions(False)
      try:
          from experimental.analyst.service import read_report_json
          report, changed = read_report_json(run_id, path=self.db_path)
      except AnalystServiceError as exc:
          if exc.code is ServiceFailure.BUSY:
              self._status_var.set(
                  "Report is busy — the analysis is still writing. Click Retry."
              )
              self._show_retry()
              return
          self._show_legacy_run()
          return
      except Exception:
          self._show_legacy_run()
          return
      self._run_id = run_id
      self._show_report(report, changed=changed)
  ```
- ADD a public reuse entry `def open_run(self, run_id): self._open_run(run_id)` so the tab can load a
  newly-selected run into an already-open window.
- Leave `_show_report` (keeps its `_hide_retry()`), `_show_legacy_run`, `_clear_report`,
  `_show_retry`/`_hide_retry`, `_export_report`, `_copy_report`, `_apply_fact_filter` unchanged.
- `report_browser.list_completed_reports` STAYS (still exported/tested); it is simply no longer used
  by the window.

### 2. `gui/components/experimental_features/analyst_tab.py`

- `_open_reports` (analyst_tab.py:1404-1416): open the SELECTED completed run, reusing an open window:
  ```
  def _open_reports(self):
      from experimental.analyst.state import RunState
      item = self._selected_summary()
      if item is None or item.state is not RunState.COMPLETE:
          return
      from gui.components.analyst_report_window import show_analyst_report_window
      existing = self._report_window
      if existing is not None and existing.window is not None:
          try:
              if existing.window.winfo_exists():
                  existing.open_run(item.run_id)
                  existing.window.lift()
                  existing.window.focus_force()
                  return
          except Exception:
              pass
      self._report_window = show_analyst_report_window(
          self.frame.winfo_toplevel(), item.run_id,
      )
  ```
- Move Open-report enablement from `_finish_refresh` to per-selection in `_on_selection`:
  - REMOVE the `self._reports_btn.configure(state=... any complete ...)` block in `_finish_refresh`
    (analyst_tab.py:1163-1169). `_finish_refresh` already calls `self._on_selection()` at the end,
    so enablement follows the current selection.
  - In `_on_selection`, set `self._reports_btn` in BOTH branches: disabled in the `item is None`
    early-return branch, and in the main branch
    `self._reports_btn.configure(state="normal" if item.state is RunState.COMPLETE else "disabled")`
    (RunState is already imported at the top of `_on_selection`).

## Tests

- `gui/tests/test_analyst_r7.py`: fixture `view` — remove the `_load_runs` monkeypatch; construct
  `AnalystReportWindow(root)` (run_id=None → builds empty). In `test_changed_badge_and_legacy_run_state`
  replace `view._runs = [...]; view._open_selected(0)` with `view._open_run("b" * 32)` (read_report_json
  raising) — the legacy assertions are unchanged.
- `gui/tests/test_analyst_b1.py` (window tests): rewrite the two busy tests around `_open_run`:
  a busy read (`read_report_json` → `AnalystServiceError(ServiceFailure.BUSY)`) shows the busy status
  + Retry, preserves a previously shown report, and does NOT show "Legacy run"; invoking Retry
  re-calls `_open_run` (now returning a report) hides Retry and shows the report; a genuine failure
  still shows legacy. Update the fixture/constructor to the new signature. (Shared
  `shared/tests/test_analyst_b1.py` — report_browser/service busy surfacing — is unchanged.)
- `gui/tests/test_analyst_c13.py`: in the `_finish_refresh` test (~line 260), drop the
  `button_states == [{"state": "disabled"}]` assertion (that global enable moved to `_on_selection`,
  which the test stubs); keep the row-insert, status, and interval assertions.
- ADD `gui/tests/test_analyst_b5.py` (xvfb, `gui_smoke`): `_open_reports` opens the SELECTED run —
  monkeypatch `show_analyst_report_window` to capture the run_id; with a COMPLETE summary selected,
  assert the captured run_id equals the selection (NOT index 0 / newest); Open-report is disabled
  when the selected run is not complete; the reuse path calls `open_run(new_run_id)` on an existing
  window.
- Suites green: `./venv/bin/python -m pytest shared/tests -k analyst -q` and
  `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` (run areas separately).

## Out of scope

Everything else. Do not remove `list_completed_reports`. Do not change the read/facts rendering,
export, or copy. Keep the window modeless + reused.
