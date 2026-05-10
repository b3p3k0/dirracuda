# Web UI Lessons Learned

Seeded before implementation. Append after every major card.

## Carry Forward

1. Inspect the actual UI before sketching changes. The Experimental dialog is a
   `ttk.Notebook`; do not replace it with a left-nav pattern.
2. Put the `Web UI` tab between `Reddit` and `Dorkbook`, not at the end.
3. Keep the desktop tab small. Put operational controls in a launched control
   dialog.
4. Use the existing CLI subprocess boundary for v1 scans. Direct workflow calls
   are tempting, but they add cancellation and behavior-drift risk.
5. Remote support is a v1 goal, but remote exposure is not a default.
6. Auth/session/token work is security-sensitive. Keep it boring, testable, and
   easy to review.
7. Do not add bearer API tokens until browser sessions are stable.
8. Guard every DB read against real runtime schema state.
9. Validate coercion explicitly. Weird strings should fail, not become dangerous
   defaults.
10. Use ASCII sketches for every UI surface before implementation.
11. Service control must survive desktop app restarts. Use health checks plus
    pidfile/systemd state, not only in-memory process handles.
12. Share/directory summaries are v1 web UI scope. The file explorer and target
    downloads are not.
13. Mobile is v1 scope. A desktop-only web UI misses how operators actually use
    browser dashboards.
14. Web dependencies belong in `webui/requirements-web.txt` unless HI explicitly folds
    them into the main runtime.
15. Web scan launch should keep using strict request validation plus argv-list
    subprocess calls with explicit `shell=False`; never let browser input become
    shell syntax or loosely coerced scan options.

## C5 — Results Summaries and Database Export

16. Export must use `VACUUM INTO` (not `backup()`). `export_database()` in
    `gui/utils/db_tools_engine_maintenance_methods.py` uses `VACUUM INTO` — that
    is the product export contract (clean, defragmented copy). `quick_backup()`
    uses the backup API and is a separate operation. Web UI export must match
    the same contract.

17. Open the export source DB with `mode=rw` (no-create), not the default
    `mode=rwc`. SQLite's default `connect(path)` creates a new empty file when
    the path is absent; `VACUUM INTO` then succeeds against it, silently
    exporting nothing. `sqlite3.connect(f"file:{path}?mode=rw", uri=True)`
    refuses to create and raises `OperationalError` instead, which propagates
    to a 500 response with no artifact written.

18. Runtime schema guards must cover columns, not just tables. Inspect
    `PRAGMA table_info(table)` for every optional column used in a SELECT —
    especially when joining probe-cache or access tables that may have schema
    drift across DB versions. A table being present does not guarantee its
    columns are present.

19. Export artifact filenames are generated-only (timestamp + random suffix).
    Download endpoints must enforce two layers: (1) allowlist regex matching
    only the generated filename pattern (`_EXPORT_FILENAME_RE`), (2)
    directory-containment via `Path.resolve()` + `relative_to()`. Neither
    alone is sufficient — the regex blocks non-artifact filenames; the
    containment check blocks symlinks that escape the export dir.

## C8 — Remote Mode

20. Validate at server startup via `load_config()` (which calls `validate()`),
    not just at config save time. The config rules already existed — `server.py`
    simply wasn't calling them before starting uvicorn.

21. Allowlist middleware must be gated on `cfg.remote_enabled=True`. An
    unconditional check breaks all existing tests: Starlette's `TestClient`
    defaults `scope["client"]` to `("testclient", 50000)`, which is not a valid
    IP and would be blocked by any CIDR check. Gate on `remote_enabled` so
    localhost-mode tests run without modification.

22. Extract startup validations that need unit tests into pure helpers (e.g.,
    `_check_remote_tls(cfg, bind)`) that return an error string or `None`.
    This makes them testable without mocking uvicorn or catching `SystemExit`.

23. Propagate `config_path` through `server.run()` → `create_app()` so the
    `/config` save endpoint writes to the file the server actually loaded,
    not the hardcoded default path.

## C9 — Docs and Closeout

24. `webui.json` is not created automatically at server startup. `load_config()`
    returns safe in-memory defaults when the file is absent. The file is written
    only when `save_config()` is called — typically on the first `/config` POST.
    Docs that say "created on first run" are inaccurate; say "used when absent."

25. `server.py` exposes three CLI flags: `--host`, `--port`, and `--config`.
    `--host`/`--port` override the loaded config (re-validated after merge);
    `--config` selects the webui.json path passed through to `create_app()`.
    Any doc or test that references server CLI args should include all three.
