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
14. Web dependencies belong in `experimental/webui/requirements-web.txt` unless HI explicitly folds
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

## C12 — Launch Diagnostics and Inline Failure State

26. After moving package code under `experimental/webui`, service launch must
    use module execution (`python -m experimental.webui.server`) instead of
    direct script-path execution. Script execution can fail import resolution
    for `experimental.webui.*` and exit immediately with no visible UI signal.

27. A startup failure should not silently collapse to `Stopped`. Preserve a
    readable inline state (`Failed: <reason>`) with bounded diagnostics (exit
    code + short stderr fragment or timeout reason) so operators can self-debug
    without hunting console output.

## O1 — Authentication Anti-Automation

28. For lockout enforcement, track failures on a composite `(account, IP)` key
    (`account:{username}:ip:{client_ip}`), not a global IP key. This keeps the
    O1 account+IP requirement while avoiding NAT-wide lockout side effects.

29. A successful authentication should clear all lockout rows for the account
    (`DELETE ... WHERE account = ?`). This aligns with NIST guidance to
    disregard failed-attempt counters after successful authentication.

30. Startup behavior and runtime behavior must be explicit and mode-aware:
    remote mode fails closed when lockout storage is unavailable; localhost mode
    may start degraded with a no-op limiter, but must expose degraded state via
    health and logs.

31. Do not swallow lockout-storage runtime errors in limiter primitives.
    Re-raise a typed runtime error and let the HTTP handler enforce
    fail-closed/degraded behavior by mode.

32. Keep the module-level `health()` helper contract stable for scaffold tests.
    Add operational fields (like `rate_limiter`) in the route handler payload,
    not by changing the pure helper return shape.

33. If `/config` gains new security fields, the API model and both config
    surfaces (web page and desktop dialog) must be updated together in the same
    card to avoid save-path regressions (`422` or silent overwrite).

34. Separate system faults from user-input rejections with distinct exception
    types. `BlocklistUnavailableError(RuntimeError)` (infrastructure fault →
    503) must not be a subclass of `ValueError` (policy rejection → 400) so
    HTTP handlers and desktop dialogs can catch them separately. A single
    `except ValueError` that also catches system faults routes operator-visible
    errors to users and hides them from logs.

35. Lock the username during credential rotation. Allowing the username to
    change during a "rotate password" flow creates a race where a stale key
    accumulates in the credential store. Rotation should only mutate the
    hash/salt for the existing account; username changes require a separate
    explicit create/delete flow.

36. Ship static JS files from day one. Inline scripts in templates create CSP
    debt: each inline block needs its own hash when O3 adds a Content-Security-
    Policy header. Moving the script to a static file before O3 costs one extra
    commit; deferring costs a per-template audit and a round of hash-generation
    work during CSP hardening.

37. Treat a blocklist with fewer entries than the compliance minimum as
    unavailable, not as a valid degraded state. A truncated-but-readable file
    that passes only 2999 passwords silently fails ASVS V6.2.4. Enforce
    `BLOCKLIST_MIN_SIZE` at load time and return `None` (→ fail-closed) for
    any undersized result.

## O3 — Strict CSP + Security Headers

38. Starlette/FastAPI middleware ordering: `@app.middleware("http")` inserts
    each new middleware at position 0, making the last-registered the outermost
    wrapper. Define the security-headers middleware **after** the allowlist
    middleware so it wraps the allowlist check and applies headers to 403
    early-return responses as well as normal responses.

39. Jinja conditional class attributes cleanly replace conditional inline style
    attributes: `class="status-warn{% if not cfg.remote_enabled %} hidden{% endif %}"`.
    The Jinja expression renders once at response time; no server-side style
    injection is needed. This is compatible with `style-src 'self'` in CSP.

40. For JS-controlled element visibility, use `classList.add/remove('hidden')`
    rather than `element.style.display = 'none'/''}`. Setting an empty-string
    inline style removes the override but the CSS class still applies; the element
    re-hides unexpectedly. With classList the intent is explicit on both sides.

41. Pass server-rendered values to JS via `data-*` attributes on existing DOM
    elements instead of Jinja interpolation inside `<script>` blocks. This is
    required to eliminate inline scripts entirely. Example: active scan task ID
    stored as `data-task-id="..."` on `#active-info` and read in `dashboard.js`
    via `element.dataset.taskId`.

42. Use a regex to assert no inline scripts in tests, not a bare string check.
    `<script>` (no attributes) misses `<script type="module">`, `<script nonce="...">`,
    etc. The correct check is `re.compile(r'<script(?![^>]*\bsrc=)[^>]*>')` — any
    `<script>` tag without `src=` is an inline script. Similarly, `' style='` misses
    spacing/case variants; use `re.compile(r'\sstyle\s*=', re.IGNORECASE)`.

43. When migrating inline JS to static files, audit ALL webui test files for
    assertions that checked inline JS content in HTML responses. Test files beyond
    the primary target (`test_pages.py`, `test_login.py`) may also have stale
    inline-content checks — in this case `test_results.py` had two that checked
    for `loadResults();` and `&search=` in the page response text.

## O4 — Credential Store Hardening + Security Docs

44. Put the permission check inside `_load_creds()` so all callers inherit it. But
    audit every upstream call site before merging: `verify_password()` swallows the
    error (returns False), while `set_password()`, `credential_exists()`, and
    `get_credential_usernames()` propagate it. Routes and UI code that call any of
    those must handle `CredentialError` explicitly.

45. `verify_password()` swallows `CredentialError` and returns False — so a route
    that calls `verify_password` before `set_password` will return 401 (wrong
    password) instead of 503 (config error) when permissions are bad. The fix is a
    preflight `check_credential_store()` call before `verify_password`. Add a public
    helper for this pattern rather than exporting the private `_check_creds_permissions`.

46. Test fixtures that write credential files directly (bypassing `set_password()`)
    must explicitly `os.chmod(p, 0o600)` after the write. A 0022 umask produces
    0644, which fails the permission check. Flag any fixture that creates a creds
    file without going through `set_password()`.

47. Permission-check tests that set mode 0644 must be guarded with
    `@pytest.mark.skipif(os.name == "nt", ...)`. Windows chmod does not enforce Unix
    mode bits, so the tests will incorrectly pass (no error raised) on Windows CI.
    Mark the POSIX scope at the test level, not only in docs.

48. `Path.stat` must be patched at the class level, not on an instance.
    `PosixPath` has no writable instance `__dict__`, so `monkeypatch.setattr(p, "stat", ...)`
    will raise AttributeError. Use `monkeypatch.setattr(Path, "stat", ...)` with a
    guard `if self == target_path` to limit the mock scope.

## O5 — Validation

49. When a new field is added to a dataclass that test stubs replicate as
    `types.SimpleNamespace`, the stubs must be updated in the same commit. A missing
    field raises `AttributeError` at the call site, which a broad `except Exception`
    block catches silently — turning a test regression into a false-pass or
    a wrong-exception failure path that looks correct by accident (see test_s11).

50. Gate 3 (`run_agent_testing_workflow.py --lane quick`) runs `scenario or fuzz`
    markers across all of `gui/tests`, not just webui tests. Do a dry-run of Gate 3
    before starting card work to establish a clean baseline; any failures found
    during O5 validation can then be correctly attributed as pre-existing rather
    than card-introduced regressions.

51. Write the validation report only after all gates are green. Writing it
    speculatively from planning-phase outputs risks stale PASS/FAIL entries if a
    gate flips between planning and final execution.

## C22 — Results Host-State Actions

52. For mixed-schema DB compatibility, treat write paths the same way as read
    paths: probe real runtime tables/columns before every mutation and return
    per-target errors instead of crashing the whole action. Optional
    protocol-specific tables (`*_user_flags`, `*_probe_cache`) can drift across
    historical DBs.

53. Keep web-side bulk actions row-scoped and explicitly current-page only.
    Multi-select should reset on reload/filter/pagination changes so stale
    selections cannot accidentally mutate off-screen rows.

54. If product parity requires desktop semantics (like compromised =
    `status=="issue"` OR `indicator_matches>0`), encode that rule in one write
    helper and assert both transitions in tests (`unprobed/0 -> issue/1`,
    `issue/N -> clean/0`) to prevent silent logic drift.
