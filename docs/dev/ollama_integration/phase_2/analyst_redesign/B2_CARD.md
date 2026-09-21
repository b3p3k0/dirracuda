# B2 — Delete run (print-jobs style)

Status: HELD for DA (codex). One card. Commit per card, no push. Builds on B1 (committed).

## Problem / goal

Abandon only stops a stuck run; there is no way to remove a finished run and its report artifacts,
so the Runs list grows without bound. Add a **Delete** action that removes a terminal run's DB rows
and on-disk artifacts — like clearing a finished print job. Multi-select is supported (the Runs
Treeview is already `selectmode="extended"`).

Only terminal runs (`complete`, `abandoned`) are deletable, and never one whose worker still holds
the GPU lease (mirror the `abandon_run` guards).

## Changes

### 1. `store.delete_run(run_id, *, path=None) -> str` — `experimental/analyst/store.py`

Model it on `abandon_run` (store.py:514-562). One `run_immediate` transaction. Returns the deleted
run's `output_root` (captured inside the txn) so the service layer can remove disk artifacts.

Inside the operation, in order:
1. `SELECT state,output_root FROM analyst_runs WHERE run_id=?`. If `None` →
   `AnalystStoreError("Analyst run does not exist")`.
2. Guard state: `RunState(str(row["state"])) not in TERMINAL_RUN_STATES` →
   `AnalystStoreError("Analyst run is not deletable")` (import `TERMINAL_RUN_STATES` from `.state`).
3. Guard lease: `SELECT 1 FROM analyst_gpu_lease WHERE slot=1 AND run_id=?` — if present →
   `AnalystStoreError("cannot delete a run with an active worker lease")`.
4. `conn.execute("PRAGMA defer_foreign_keys=ON")` — defers FK checks to commit so the cyclic
   RESTRICT pair `analyst_chunks.accepted_attempt_id ↔ analyst_model_attempts.chunk_id` can both be
   removed in one transaction.
5. Delete all run-scoped rows (every statement parameterized by `run_id`). Chunk/file-scoped rows
   use subqueries:
   - `DELETE FROM analyst_model_findings WHERE chunk_id IN (SELECT c.chunk_id FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=?)`
   - `DELETE FROM analyst_model_attempts WHERE chunk_id IN (SELECT c.chunk_id FROM analyst_chunks c JOIN analyst_files f ON f.file_id=c.file_id WHERE f.run_id=?)`
   - `DELETE FROM analyst_chunks WHERE file_id IN (SELECT file_id FROM analyst_files WHERE run_id=?)`
   - `DELETE FROM analyst_detector_hits WHERE file_id IN (SELECT file_id FROM analyst_files WHERE run_id=?)`
   - `DELETE FROM analyst_provenance_units WHERE file_id IN (SELECT file_id FROM analyst_files WHERE run_id=?)`
   - `DELETE FROM analyst_ollama_contacts WHERE run_id=?`
   - `DELETE FROM analyst_read_exposures WHERE run_id=?`
   - `DELETE FROM analyst_read_contact WHERE run_id=?`
   - `DELETE FROM analyst_read WHERE run_id=?`
   - `DELETE FROM analyst_ollama_schedule WHERE run_id=?`
   - `DELETE FROM analyst_inventory_exclusions WHERE run_id=?`
   - `DELETE FROM analyst_files WHERE run_id=?`
6. `cursor = conn.execute("DELETE FROM analyst_runs WHERE run_id=? AND state IN ('complete','abandoned')", (run_id,))`;
   if `cursor.rowcount != 1` → `AnalystStoreError("Analyst run changed during delete")`.
7. Return `str(output_root)`.

Do NOT touch `analyst_gpu_lease` (endpoint singleton), `analyst_discovery_contact`, or
`analyst_discovered_model` (endpoint-scoped). Validate `run_id` up front with `_require_text` as
`abandon_run` does. Add `delete_run` to `store.__all__`.

### 2. `service.delete_run(run_id, *, path=None) -> None` — `experimental/analyst/service.py`

- Add `DELETE = "delete"` to the `ServiceFailure` enum.
- Wrapper:
  ```
  def delete_run(run_id, *, path=None):
      try:
          output_root = store_delete_run(run_id, path=path)
      except AnalystStoreError:
          raise AnalystServiceError(ServiceFailure.DELETE) from None
      _remove_report_dir(output_root, run_id)
      _remove_run_logs(run_id)
  ```
  (import `delete_run as store_delete_run` from `.store`, matching the existing
  `abandon_run as abandon_stored_run` alias style; expose `delete_run` in `service.__all__`.)
- On-disk removal helpers (best-effort, never follow symlinks, never touch anything but the run's
  own artifacts):
  ```
  def _remove_report_dir(output_root, run_id):
      path = Path(output_root)
      if not path.is_absolute():
          return
      try:
          info = path.lstat()
      except OSError:
          return
      if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
          return
      if info.st_uid != os.getuid():
          return
      if run_id[:12] not in path.name:      # sanity: the run's own per-run dir
          return
      shutil.rmtree(path, ignore_errors=True)

  def _remove_run_logs(run_id):
      logs_dir = get_paths().analyst_logs_dir
      try:
          logs = tuple(logs_dir.glob(f"{run_id}-*.log"))
      except OSError:
          return
      for log in logs:
          try:
              info = log.lstat()
              if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                  continue
              if info.st_uid != os.getuid():
                  continue
              log.unlink()
          except OSError:
              continue
  ```
  Add any missing imports (`shutil`, `stat`, `os`, `Path`, `get_paths`) — most are already present.
  Disk removal runs only AFTER the DB delete succeeds (so a failed delete leaves files intact).

### 3. GUI — `gui/components/experimental_features/analyst_tab.py`

- Add a **Delete** danger button after Abandon (built near analyst_tab.py:283):
  ```
  self._delete_btn = tk.Button(run_controls, text="Delete", state="disabled",
      command=self._delete_selected)
  self._theme.apply_to_widget(self._delete_btn, "button_danger")
  self._delete_btn.pack(side=tk.LEFT, padx=(0, 7))
  ```
- Add `_selected_summaries()` (all selected, not just the first):
  ```
  def _selected_summaries(self):
      selected = set(self._runs.selection())
      return [item for item in self._summaries if item.run_id in selected]
  ```
- Enablement in `_on_selection` (analyst_tab.py:1193-1223): compute delete-enablement for the whole
  selection and set it in BOTH the `item is None` early-return branch and the main branch:
  ```
  from experimental.analyst.state import RunState, TERMINAL_RUN_STATES
  chosen = self._selected_summaries()
  deletable = bool(chosen) and all(s.state in TERMINAL_RUN_STATES for s in chosen)
  self._delete_btn.configure(state="normal" if deletable else "disabled")
  ```
- `_delete_selected` (mirror the `_run_service_action` worker-thread pattern, but for a list and with
  a confirm + per-run loop):
  ```
  def _delete_selected(self):
      if self._busy:
          return
      chosen = [s for s in self._selected_summaries() if s.state in TERMINAL_RUN_STATES]
      if not chosen:
          return
      count = len(chosen)
      if not safe_messagebox.askyesno(
          "Analyst",
          f"Delete {count} report(s)? This removes the report files and cannot be undone.",
          parent=self.frame.winfo_toplevel(),
      ):
          return
      run_ids = [s.run_id for s in chosen]
      self._set_busy(True, "Deleting…")

      def work():
          failed = 0
          for run_id in run_ids:
              try:
                  from experimental.analyst.service import delete_run
                  delete_run(run_id)
              except Exception:
                  failed += 1
          self._schedule(lambda: self._finish_delete(run_ids, failed))

      threading.Thread(target=work, daemon=True).start()

  def _finish_delete(self, run_ids, failed):
      registry = self._context.get("running_tasks_registry") or get_running_task_registry()
      for run_id in run_ids:
          registry.remove_task(f"analyst:{run_id}")
      total = len(run_ids)
      deleted = total - failed
      if failed:
          message = f"Deleted {deleted} of {total}; {failed} failed."
      else:
          message = f"Deleted {deleted} report(s)."
      self._finish_action(failed == 0, message)
  ```
  (`_finish_action` already re-runs `_refresh_runs` and shows an error box on failure.)

## Tests

- `shared/tests/test_analyst_b2.py`: create a completed run with children across the tables, call
  `store.delete_run`, assert every run-scoped table has zero rows for that run_id AND the
  `analyst_runs` row is gone (and the cyclic-FK delete commits cleanly). Assert delete refuses a
  non-terminal run and a lease-held run. Assert `store.delete_run` returns the `output_root`.
  Service-level: `service.delete_run` removes a real per-run output dir + `<run_id>-*.log` and does
  NOT follow a symlinked output_root / one whose name lacks `run_id[:12]`.
- `gui/tests/test_analyst_b2.py` (xvfb, `gui_smoke`): Delete enabled only when all selected runs are
  terminal (disabled if any active/resumable selected); confirming calls `service.delete_run` for
  each selected run and triggers a refresh; declining does nothing.
- Existing suites stay green: `./venv/bin/python -m pytest shared/tests -k analyst -q` and
  `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` (run areas separately).

## Out of scope

B3 (large-dir visibility), B4 (closeout). No "clear all" bulk button (multi-select covers it). Do
not change run-state semantics or the lease.
