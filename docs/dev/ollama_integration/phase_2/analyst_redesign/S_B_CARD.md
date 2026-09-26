# S-B — Stability GUI: Abandon action, close-flow fix, output persistence/default, error map

- Type: GUI code card. codex implements, Claude validates against the REAL app lifecycle (xvfb).
- Depends on: S-A (service.abandon_run, analyst_reports_dir, OUTPUT_INVALID/OUTPUT_UNSAFE_FS/ABANDON).
- Fixes the operator-reported mess: tasks survive restart with no way to clear them; Cancel greyed;
  app-close lags / re-fires the dialog / returns to the app; output folder not persisted.

## Design (approved: abandon Analyst runs on close)

- Any non-terminal Analyst run hydrates as a Running Task. Its registry action must always work:
  - ACTIVE (running / cancel_requested / finalizing / interrupted+paused_resource): action =
    `service.cancel_run` (signal the worker).
  - RESUMABLE (interrupted / cancelled_pending_resume / ready, no live worker): action =
    `service.abandon_run` (discard). So `registry.cancel_all()` actually clears them.
- App-close "stop all and exit?" -> Yes: cancel active workers + abandon resumable runs (via the
  existing request_cancel loop, which now clears them) -> `has_active_or_queued_work()` becomes
  False -> close proceeds. Nothing lingers.

## Deliverables

1. **`gui/utils/analyst_tasks.py`**: `apply_analyst_task_hydration` takes an additional `abandon`
   factory. For each non-terminal run set `cancel_callback` to `cancel(run_id)` when ACTIVE and
   `abandon(run_id)` when RESUMABLE (never None for a non-terminal run), so Running Tasks always has
   a working "stop/dismiss" and `cancel_all()` clears resumable runs.

2. **`gui/components/experimental_features/analyst_tab.py`**:
   - Add an **Abandon** button. Wire to `service.abandon_run(run_id)` off the Tk thread; on success
     refresh runs and remove the task from the registry; on `AnalystServiceError` show
     `safe_messagebox` (content-free).
   - `_on_selection` enablement by selected run state: Cancel enabled only for ACTIVE; Resume and
     Abandon enabled only for RESUMABLE; all disabled otherwise.
   - Pass the new `abandon` factory into `apply_analyst_task_hydration` (alongside reopen/cancel).
   - **Output folder**: default the field to `get_paths().analyst_reports_dir` (not auto-filled from
     the source). Load a persisted `analyst.output_folder` on build if present; save it on Save and
     on Analyze. Remove the source-dir auto-fill of output.
   - **Error map** `_CREATE_FAILURE_MESSAGES`: add `output_invalid` -> "Output folder must be an
     existing local directory (not a symlink)."; `output_unsafe_fs` -> "Output folder can't keep
     reports private (e.g. a network/CIFS mount). Choose a local folder."; `abandon` -> "Could not
     abandon the run." Keep `contract` for the model-identity case.

3. **`./dirracuda` `_on_closing`**: add a re-entrancy guard — a `self._closing` flag set on first
   entry; if already closing, return immediately (ignore repeat WM_DELETE clicks) so the dialog
   cannot re-fire / bounce back to the app during the wait loop. Keep the existing cancel/terminate
   loop and final `root.destroy()`.

## Tests
- `gui/tests/test_analyst_sb.py` (MagicMock / withdrawn root): Abandon button enabled only for a
  resumable selection and calls `service.abandon_run`; Cancel enabled only for active; hydration
  sets an abandon cancel_callback for a resumable run and a cancel callback for an active run.
- Close-flow MECHANISM test: with a hydrated resumable Analyst task in a real RunningTaskRegistry,
  `dashboard.request_cancel_active_or_queued_work()` (or `registry.cancel_all()`) drives the
  abandon so the task is removed and `has_active_or_queued_work()` returns False (proves no hang).
- Re-entrancy: a second `_on_closing` while `_closing` is set returns without a second dialog.
- Output persistence: Save writes `analyst.output_folder`; a fresh tab loads it; default is
  `analyst_reports_dir` when unset.

## Constraints
- safe_messagebox, ensure_dialog_focus, named SMBSeekTheme styles, no worker-thread widget teardown.
  `./dirracuda` is the runtime entrypoint (allowed). Files: `analyst_tab.py`, `analyst_tasks.py`,
  `./dirracuda`, and tests. Do NOT change the worker/finalize/schema. Files < 1700 lines.

## Acceptance (Claude validates)
1. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k "analyst or messagebox_guardrail or theme_style_guardrail" -q` => 0 failed.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
3. Real-lifecycle checks by Claude (xvfb): (a) tab builds; Abandon enabled for a resumable run and
   clears it; (b) a headless `./dirracuda` launch with a resumable run in the DB closes cleanly
   (exits, no hang) when close is invoked; (c) output folder persists across a rebuild.
