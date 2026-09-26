# S-A — Stability backend: abandon API, local reports dir, output default/persistence, clear errors

- Type: backend code card. codex implements, Claude validates hard (real venv).
- Part of the approved lifecycle-stability pass. Foundation for S-B (GUI abandon + close flow).

## Deliverables

1. **`service.abandon_run(run_id, *, path=None) -> None`** (new): wraps `store.abandon_run`
   (which already refuses a lease-held run and requires a resumable state, setting the run to
   `abandoned`). Map `AnalystStoreError` to a content-free `AnalystServiceError` with a distinct
   code (e.g. `ServiceFailure.ABANDON`). Export it. This is what the GUI Abandon action and the
   close flow will call for resumable runs.

2. **`shared/path_service.py`: add `analyst_reports_dir`** to `DirracudaPaths`, created owner-only
   0700 by `get_paths()` like the other private dirs. Location:
   `~/.dirracuda/data/experimental/analyst_reports/` (sibling of `analyst_db_file`). Add a
   path-service test asserting it exists at that path with mode 0700.

3. **Default report output to that local dir** (operator pivot; supersedes CONTRACT s12's
   `<source_dir>/_analyst` default):
   - `service.create_directory_run` / `create_manifest_run` / `_run_output_root`: when no explicit
     output base is given, use `get_paths().analyst_reports_dir` as the base (per-host/run nesting
     under it unchanged). An explicitly supplied output base is still honored. Reading sources from
     CIFS is unaffected.

4. **Clear, specific output-directory errors** (replace the single catch-all `contract` message):
   - In `_require_existing_directory` / `create_directory_run`, distinguish and raise
     distinct content-free `ServiceFailure` codes for: output path is a symlink or not a directory
     or missing (`OUTPUT_INVALID`); output filesystem cannot enforce owner-only permissions
     (detect by creating the target dir 0700 then re-stat; if it did not stick -> `OUTPUT_UNSAFE_FS`).
     Keep the model-identity failure as its own code. (The GUI map is updated in S-B.)
   - Keep the owner-only WRITE/READ checks fail-closed; the goal is a clear early error + a safe
     default, not accepting unsafe output.

## Constraints
- Backend only (no GUI in this card). Content-free errors. Files: `experimental/analyst/service.py`,
  `shared/path_service.py`, and tests in `shared/tests`. `store.abandon_run` unchanged. Files < 1700.

## Acceptance (Claude validates, real venv)
1. New tests: `service.abandon_run` abandons a resumable run and refuses a lease-held / non-resumable
   one (content-free); `get_paths().analyst_reports_dir` exists at the expected path, mode 0700;
   a run created with no output base lands under `analyst_reports_dir`; an explicit base is honored;
   a symlink output base raises `OUTPUT_INVALID`.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
3. `git diff` touches only service.py, path_service.py, tests.
