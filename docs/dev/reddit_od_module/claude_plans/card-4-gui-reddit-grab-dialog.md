# Card 4 Plan: GUI Reddit Grab Dialog + Dashboard Hook

## Context

Cards 1–3 delivered: `redseek` package scaffold, sidecar DB, JSON client, parser, and
ingestion service (`run_ingest`). Card 4 exposes that service to the analyst via a new
dashboard button and modal dialog. The service entry point is clean:

```python
run_ingest(options: IngestOptions, db_path=None) -> IngestResult  # never raises
```

---

## 1. Constraint Summary

1. Reddit Grab runs off the Tk main thread — network + DB work must NOT block the GUI.
2. Reddit Grab does NOT use `scan_manager`, lock files, or the SMB/FTP/HTTP scan pipeline.
3. `reddit_grab_button` must be disabled while a SMB/FTP/HTTP scan is running (add to `_update_scan_button_state` guards).
4. During a Reddit Grab, SMB/FTP/HTTP scan buttons are **not** affected — ingest is independent.
5. Only one Reddit Grab at a time — disable `reddit_grab_button` for the grab duration.
6. `replace_cache` is a destructive wipe; no confirmation dialog required (covered by checkbox label).
7. Dialog validates input before calling the callback — invalid `max_posts` is rejected in the dialog, not the worker.
8. `run_ingest` already validates `sort`, `max_posts`, `max_pages`, `subreddit` — dialog validation is a UX guard only.
9. No changes to `scan_manager`, `backend_interface`, or any SMB/FTP/HTTP path.
10. No CLI path for Reddit ingest in this card.

---

## 2. File Touch List

| File | Action | Reason |
|------|--------|--------|
| `gui/components/reddit_grab_dialog.py` | **NEW** | Dialog class + `show_reddit_grab_dialog()` |
| `gui/components/dashboard.py` | **MODIFY** | Button, handler, worker, result display, lock integration, theme refresh |
| `gui/tests/test_dashboard_reddit_wiring.py` | **NEW** | Focused tests for Reddit button/handler/worker behavior |

No other files touched. `redseek/service.py` is called as a library — no changes needed.

---

## 3. Step-by-Step Implementation Plan

### Step 1 — `gui/components/reddit_grab_dialog.py` (new file)

#### Class `RedditGrabDialog`

```
__init__(parent, grab_start_callback)
    - build Toplevel modal
    - 5 option widgets (see layout below)
    - [Run Grab] [Cancel] buttons

_validate() -> IngestOptions | None
    - sort: read StringVar, must be "new" or "top"
    - max_posts: parse IntVar, guard 1 <= n <= 200, show messagebox on failure
    - parse_body, include_nsfw, replace_cache: BooleanVars

_on_run()
    - options = _validate()
    - if options is None: return (stays open)
    - grab_start_callback(options)
    - self.dialog.destroy()

_on_cancel()
    - self.dialog.destroy()

show() -> None
    - self.dialog.wait_window()
```

Dialog layout:
```
+-----------------------------------+
| Reddit Grab                       |
|-----------------------------------|
| Sort:          [new      v]       |
| Max posts:     [50        ]       |
| Parse body:    [x]                |
| Include NSFW:  [x]                |
| Replace cache: [ ]                |
|                                   |
|   [Run Grab]        [Cancel]      |
+-----------------------------------+
```

Defaults: sort=new, max_posts=50, parse_body=True, include_nsfw=False, replace_cache=False.

#### Module-level function

```python
def show_reddit_grab_dialog(
    parent: tk.Widget,
    grab_start_callback: Callable[[IngestOptions], None],
) -> None:
    dialog = RedditGrabDialog(parent, grab_start_callback)
    dialog.show()
```

No return value needed (unlike scan dialogs which return "start"/"cancel" — here the
callback already carries the intent).

---

### Step 2 — `gui/components/dashboard.py` additions

#### 2a — `__init__` additions (~line 176)

```python
self.reddit_grab_button = None   # follows ftp_scan_button / http_scan_button pattern
self._reddit_grab_running = False
```

#### 2b — Import addition (top of file, with other gui.components imports)

```python
from gui.components.reddit_grab_dialog import show_reddit_grab_dialog
from redseek.service import IngestOptions, IngestResult, run_ingest
```

#### 2c — `_build_header_section()` — add button after `self.scan_button.pack()`

```python
self.reddit_grab_button = tk.Button(
    left_actions,
    text="Reddit Grab",
    command=self._handle_reddit_grab_button_click,
)
self.theme.apply_to_widget(self.reddit_grab_button, "button_secondary")
self.reddit_grab_button.pack(side=tk.LEFT, padx=(0, 5))
```

Place it immediately after the `scan_button` pack line, before `servers_button`.

#### 2d — `_refresh_theme_cached_colors()` — add reddit_grab_button to button list (line 413)

The method iterates an explicit `tuple` of `getattr(self, <name>, None)` references. Add
`getattr(self, "reddit_grab_button", None)` to that tuple. Required — not optional.

#### 2e — `_update_scan_button_state()` — add reddit_grab_button to all 6 branches

Follow the exact `ftp_scan_button` / `http_scan_button` pattern already present:

- `"idle"` → `state=NORMAL` (only if not `self._reddit_grab_running`)
- all other states (`"disabled_external"`, `"scanning"`, `"stopping"`, `"retry"`, `"error"`) → `state=DISABLED`

For the `"idle"` case, the guard is:
```python
if self.reddit_grab_button is not None:
    state = tk.DISABLED if self._reddit_grab_running else tk.NORMAL
    self.reddit_grab_button.config(state=state)
```

All other states: `state=DISABLED` unconditionally (same pattern as ftp/http).

#### 2e — `_handle_reddit_grab_button_click()`

Calls `_check_external_scans()` to refresh stale state before the idle check.
Does NOT call `_maybe_warn_mock_mode_persistence()` — that warning is about backend mock
scan results not being written, which is irrelevant to sidecar-backed Reddit ingest.

```python
def _handle_reddit_grab_button_click(self) -> None:
    if self._reddit_grab_running:
        return
    self._check_external_scans()
    if self.scan_button_state != "idle":
        return
    show_reddit_grab_dialog(
        parent=self.parent,
        grab_start_callback=self._handle_reddit_grab_start,
    )
```

Reference guard pattern: `_handle_ftp_scan_button_click` (line 2781) and
`_handle_http_scan_button_click` (line 2796).

#### 2f — `_handle_reddit_grab_start(options: IngestOptions)`

Second gate: re-checks external scans and requires `scan_button_state == "idle"` before
committing to run. This closes the race window where scan state changes while the dialog
is open — the callback would otherwise start ingest regardless.

```python
def _handle_reddit_grab_start(self, options: IngestOptions) -> None:
    # Second state check — dialog may have been open while scan state changed.
    self._check_external_scans()
    if self.scan_button_state != "idle" or self._reddit_grab_running:
        return

    self._reddit_grab_running = True
    if self.reddit_grab_button is not None:
        self.reddit_grab_button.config(state=tk.DISABLED)
    self._log_status_event(
        f"Reddit Grab started (sort={options.sort}, max_posts={options.max_posts})"
    )
    threading.Thread(
        target=self._reddit_grab_worker,
        args=(options,),
        daemon=True,
    ).start()
```

#### 2g — `_reddit_grab_worker(options: IngestOptions)` (runs on background thread)

Wrapped in `try/except` so an unexpected exception in `run_ingest` never strands
`_reddit_grab_running=True` or leaves the button permanently disabled:

```python
def _reddit_grab_worker(self, options: IngestOptions) -> None:
    try:
        result = run_ingest(options)   # documented never-raises; defensive wrap anyway
    except Exception as exc:
        # Synthesise an error-shaped IngestResult so the completion path is always clean.
        result = IngestResult(
            sort=options.sort,
            subreddit=options.subreddit,
            pages_fetched=0,
            posts_stored=0,
            posts_skipped=0,
            targets_stored=0,
            targets_deduped=0,
            parse_errors=0,
            stopped_by_cursor=False,
            stopped_by_max_posts=False,
            replace_cache_done=False,
            rate_limited=False,
            error=f"unexpected error: {exc}",
        )
    self.parent.after(0, self._on_reddit_grab_done, result)
```

`_on_reddit_grab_done` is always scheduled — `_reddit_grab_running` is always reset.

#### 2h — `_on_reddit_grab_done(result: IngestResult)` (runs on main thread)

```python
def _on_reddit_grab_done(self, result: IngestResult) -> None:
    self._reddit_grab_running = False
    if self.reddit_grab_button is not None and self.scan_button_state == "idle":
        self.reddit_grab_button.config(state=tk.NORMAL)

    if result.error:
        detail = f"Error: {result.error}"
        if result.rate_limited:
            detail = f"Rate limited (HTTP 429). {detail}"
        if result.replace_cache_done:
            detail += "\nNote: cache was wiped before the failure."
        self._log_status_event(f"Reddit Grab failed: {result.error}")
        messagebox.showerror("Reddit Grab Failed", detail, parent=self.parent)
    else:
        stop_reason = ""
        if result.stopped_by_cursor:
            stop_reason = " (stopped at known cursor)"
        elif result.stopped_by_max_posts:
            stop_reason = " (max posts reached)"
        summary = (
            f"sort={result.sort}{stop_reason}\n"
            f"Pages fetched: {result.pages_fetched}\n"
            f"Posts stored: {result.posts_stored}  "
            f"Skipped: {result.posts_skipped}\n"
            f"Targets stored: {result.targets_stored}  "
            f"Deduped: {result.targets_deduped}"
        )
        if result.replace_cache_done:
            summary += "\nCache replaced before run."
        self._log_status_event(
            f"Reddit Grab done — {result.posts_stored} posts, "
            f"{result.targets_stored} targets"
        )
        messagebox.showinfo("Reddit Grab Complete", summary, parent=self.parent)
```

---

### Step 3 — `gui/tests/test_dashboard_reddit_wiring.py` (new file)

Pattern: follow `test_dashboard_scan_dialog_wiring.py` — use `DashboardWidget.__new__()` to
construct a stub instance without building real Tk widgets.

Three focused test groups:

#### Group A — Button state integration with `_update_scan_button_state`

```
test_reddit_grab_button_disabled_while_scanning
    - Set reddit_grab_button to a mock button, set scan_button_state="scanning"
    - Call _update_scan_button_state("scanning")
    - Assert mock.config called with state=DISABLED

test_reddit_grab_button_enabled_on_idle_when_not_running
    - Set _reddit_grab_running=False, set scan_button_state="idle"
    - Call _update_scan_button_state("idle")
    - Assert mock.config called with state=NORMAL

test_reddit_grab_button_stays_disabled_on_idle_if_grab_running
    - Set _reddit_grab_running=True
    - Call _update_scan_button_state("idle")
    - Assert mock.config called with state=DISABLED
```

#### Group B — Click handler calls `_check_external_scans` before opening dialog

```
test_click_handler_calls_check_external_scans
    - Monkeypatch _check_external_scans and show_reddit_grab_dialog
    - Set scan_button_state="idle", _reddit_grab_running=False
    - Call _handle_reddit_grab_button_click()
    - Assert _check_external_scans called before show_reddit_grab_dialog

test_click_handler_does_not_open_dialog_if_not_idle_after_check
    - _check_external_scans side-effect: sets scan_button_state="scanning"
    - Call _handle_reddit_grab_button_click()
    - Assert show_reddit_grab_dialog NOT called

test_click_handler_does_not_open_dialog_if_grab_already_running
    - Set _reddit_grab_running=True
    - Call _handle_reddit_grab_button_click()
    - Assert _check_external_scans NOT called, show_reddit_grab_dialog NOT called
```

#### Group C — Worker exception path always resets state

```
test_worker_exception_schedules_done_callback
    - Monkeypatch run_ingest to raise RuntimeError
    - Monkeypatch self.parent.after to capture calls
    - Call _reddit_grab_worker(options) directly (on test thread, no real thread needed)
    - Assert parent.after was called with (0, self._on_reddit_grab_done, <result>)
    - Assert result.error is not None

test_on_reddit_grab_done_resets_running_flag
    - Set _reddit_grab_running=True
    - Construct an error IngestResult
    - Call _on_reddit_grab_done(result) directly
    - Assert _reddit_grab_running == False

test_on_reddit_grab_done_re_enables_button_on_idle
    - Set _reddit_grab_running=True, scan_button_state="idle"
    - Set reddit_grab_button to a mock
    - Call _on_reddit_grab_done with success result
    - Assert mock.config called with state=NORMAL
```

All tests in this file use `monkeypatch` (pytest fixture) for isolation.
`messagebox` calls are monkeypatched to no-ops (same approach as existing dashboard tests).

---

## 4. Threading / Dispatcher Plan

| Phase | Thread | Mechanism |
|-------|--------|-----------|
| Button click → open dialog | Main | Direct call, `wait_window()` blocks in modal loop |
| `_handle_reddit_grab_start` | Main | Sets flag, disables button, spawns thread |
| `_reddit_grab_worker` | Background daemon thread | Calls `run_ingest()` (blocking) |
| Marshal result to UI | Background→Main | `self.parent.after(0, callback, result)` |
| `_on_reddit_grab_done` | Main | Re-enables button, shows messagebox |

`self.parent.after(0, fn, arg)` is the pattern already used throughout `dashboard.py`
(see `_reset_scan_status`, `_launch_next_queued_scan`). No `UIDispatcher` instance
exists on the dashboard widget, so this is the correct pattern.

`run_ingest` blocks at most: `max_pages * (network_timeout + 1s)` ≈ 3 × 21 = ~63s worst
case. The worker is daemon so it exits with the app.

No shared mutable state is written by the worker thread — `run_ingest` is self-contained
and returns an immutable `IngestResult`. The only cross-thread write is `self.parent.after()`
which is queue-safe (Tkinter's `after` queue is thread-safe).

---

## 5. Validation Plan

### Automated (run these commands)

In a headless dev shell or CI, prefix GUI-importing test commands with `xvfb-run -a` to
avoid false negatives from missing display:

```bash
# Syntax check new and modified files (no display needed)
./venv/bin/python -m py_compile gui/components/reddit_grab_dialog.py gui/components/dashboard.py

# New Reddit wiring tests (needs display)
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_dashboard_reddit_wiring.py -v

# Regression: dashboard wiring tests (needs display)
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_dashboard_scan_dialog_wiring.py -v

# Regression: all dashboard tests (needs display)
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests -k "dashboard" -v

# Regression: redseek unit tests — no display needed (no Tk)
./venv/bin/python -m pytest -q shared/tests/test_redseek_service.py shared/tests/test_redseek_client.py shared/tests/test_redseek_parser.py shared/tests/test_redseek_store.py -v

# Full targeted reddit/redseek pass
./venv/bin/python -m pytest -q -k "reddit or redseek" -v
```

Expected: no failures, no regressions.

### Manual HI Gate

**Flow A — Happy path (new)**
1. Launch `./dirracuda`
2. Click "Reddit Grab" — dialog appears
3. Set sort=new, max_posts=25, parse_body=on, include_nsfw=off, replace_cache=off
4. Click "Run Grab"
5. Dialog closes; "Reddit Grab started" appears in log
6. Within ~30s: success messagebox with post/target counts
7. "Reddit Grab" button re-enables

**Flow B — Happy path (top)**
1. Same as A but sort=top
2. Confirm bounded pagination (no runaway)

**Flow C — Replace cache**
1. Run with replace_cache=on
2. Verify success messagebox mentions "Cache replaced before run"

**Flow D — SMB/FTP/HTTP isolation**
1. While Reddit Grab running: confirm Start Scan button state unchanged
2. Click Start Scan → works normally (or rejected only by existing scan lock, not by Reddit grab)
3. While SMB scan running: confirm Reddit Grab button is DISABLED

**Flow E — Input validation**
1. Enter max_posts=0 → "Run Grab" stays blocked, error shown
2. Enter max_posts=201 → same
3. Empty max_posts field → same

---

## 6. Regression Protection List

| Behavior | How protected |
|----------|---------------|
| SMB Start Scan lock/unlock cycle | Not touched; `scan_button_state` FSM is read-only here |
| FTP scan dialog launch | Not touched |
| HTTP scan dialog launch | Not touched |
| Dashboard button enable/disable on scan state | `reddit_grab_button` added with same None-guard pattern — cannot break existing guards |
| `scan_manager` singleton | Not called from Reddit path |
| Sidecar DB isolation | `run_ingest` uses `~/.dirracuda/reddit_od.db` by default; main DB unreachable from this path |
| `_update_scan_button_state` FSM | Additive only — 6 new `if self.reddit_grab_button is not None` blocks |
| Dashboard `__init__` | Two new attribute assignments; no existing attribute renamed or removed |

---

## 7. Risks / Blockers / Shortcuts to Avoid

1. **Don't call `run_ingest` on the main thread.** It sleeps between pages and blocks for up
   to ~60s. Use `threading.Thread` + `self.parent.after(0, ...)`.

2. **Don't use `scan_manager.start_scan()` for the Reddit worker.** The scan manager
   manages lock files and CLI subprocesses — none of which apply here.

3. **Don't toggle `scan_button_state` during a Reddit Grab.** The SMB/FTP/HTTP FSM must
   remain isolated. Use a separate `_reddit_grab_running` boolean.

4. **Don't forget `parent=self.parent` on `messagebox` calls.** Without it, the dialog
   may appear behind the main window.

5. **`replace_cache=True` wipes before fetch.** If the network then fails, the DB is empty.
   The result surface this via `replace_cache_done=True` + `error` — the `_on_reddit_grab_done`
   handler already handles this; don't suppress it.

6. **`_refresh_theme_cached_colors` is a required touch.** The method (line 413) uses an
   explicit `getattr` tuple — `reddit_grab_button` will not be theme-refreshed unless added.
   Missing it causes the button to keep stale colors after a theme toggle.

7. **`show_reddit_grab_dialog` returns `None`** (not "start"/"cancel") — the callback carries
   intent. Don't add a return value.

---

## 8. Open Questions

None blocking. `_refresh_theme_cached_colors` coverage (risk #6) is a trivial read — verify
during implementation, not planning.

---

## Critical File References

- `gui/components/dashboard.py:328` — `_build_header_section()` (button insertion point)
- `gui/components/dashboard.py:176` — `__init__` attribute slot for `ftp_scan_button` (follow pattern)
- `gui/components/dashboard.py:2900` — `_update_scan_button_state()` (add reddit_grab_button guards)
- `gui/components/ftp_scan_dialog.py:1303` — `show_ftp_scan_dialog()` (dialog factory pattern to follow)
- `redseek/service.py:47` — `IngestOptions` dataclass (import from here)
- `redseek/service.py:58` — `IngestResult` dataclass
- `redseek/service.py:380` — `run_ingest()` (public entry point, never raises)
- `gui/utils/ui_dispatcher.py:53` — `UIDispatcher.schedule()` (not used here — `parent.after` is the dashboard's pattern)
- `gui/tests/test_dashboard_scan_dialog_wiring.py` — regression test to keep green; also the structural template for the new test file
- `gui/tests/test_dashboard_reddit_wiring.py` — new test file (Groups A/B/C above)
