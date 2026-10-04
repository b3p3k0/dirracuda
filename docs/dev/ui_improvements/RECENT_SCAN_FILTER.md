# Server List: Most Recent Scan Only filter

Status: plan approved by HI 2026-10-04 (chat). Cards C1 and C2 authorized to
completion. Push stays with HI.

## Goal

Add a `Most Recent Scan Only` tickbox to the Server List `Filters ▾` popover.
It shows only hosts from the last scan run. One run can use one provider or
several (for example Shodan + SearXNG). Users can combine it with every other
filter.

## HI decisions (2026-10-04)

| # | Decision |
|---|---|
| D1 | "Last scan" = the most recent desktop provider-queue run. Its start and end times are recorded to a state file. |
| D2 | If no window is recorded (fresh install, CLI-only scans), the tickbox is disabled with a visible reason. |
| D3 | Remove the Date dropdown's `Since Last Scan` option. |
| D4 | Remove all older "recent scan" work in the Server List that this replaces. Note second- and third-order effects. |

## Findings that shaped the plan

- Host rows have no scan/session ID. `last_seen` (UTC, `CURRENT_TIMESTAMP`) is
  the only link to a scan. Adding a session column is a schema change (Hard
  Stop) and was rejected (option C).
- Every desktop scan goes through `gui/components/dashboard_provider_queue.py`:
  `start_provider_queue` → `launch_next_provider` / `complete_provider` →
  `_finish_provider_queue`, or `cancel_provider_queue`.
- `Since Last Scan` was broken: it used the scan **end** time as a lower bound
  (so it hid the scan's own rows), compared local time to UTC, read an
  in-memory value lost on restart, and kept rows with no date.

## Old work removed (D4)

| Item | Location | Why it is dead or replaced |
|---|---|---|
| `Since Last Scan` combobox value and its branch in `apply_date_filter` | `server_list_window/filters.py` | Replaced by the new tickbox (D3). |
| `last_scan_time` attribute and its load | `server_list_window/window.py` | Only fed `Since Last Scan`. |
| `ScanManager.get_last_scan_time()` | `gui/utils/scan_manager.py` | Only caller was the Server List. |
| `filter_recent` flag, `on_show_all_toggle` callback, `show_all_button` | `window.py`, `filters.py`, `actions/templates.py::_toggle_show_all_results` | `window_data` is always `{}` (`gui/dashboard/widget.py::_open_drill_down`), so this never ran. |
| `apply_recent_discoveries_filter()` | `actions/templates.py` | Two obsolete `recent_activity` routes in `dirracuda` and `scripts/legacy/xsmbseek_legacy.py` called it; C2 removes those routes. It also passes `recent_discovery_only=` to `get_server_list`, which does not accept it, so calling it raises `TypeError`. |

### Kept on purpose

- `DatabaseReader.get_protocol_server_list(recent_scan_only=...)` and its
  1-hour heuristic. Dashboard post-scan bulk probe/extract
  (`dashboard_batch_ops.py`) uses it, combined with
  `get_protocol_scan_cohort_server_ids`. It is a different feature. Changing it
  would alter bulk-op targeting, so it is out of scope.
- The `All` / `Last 24 Hours` / `Last 7 Days` / `Last 30 Days` date options.

### Second- and third-order effects

| Effect | Handling |
|---|---|
| Saved preferences or templates with `date_filter = "Since Last Scan"` | When loaded, any value not in the combobox list maps to `All`. |
| Window recorded against DB A, then the user switches to DB B | The record stores the resolved DB path. A mismatch counts as "no window" (disabled). |
| Saved pref `most_recent_scan_only = True` but no valid window | Forced to `False` on load, so the filter count and results never claim a filter that is not applied. |
| Cancelled queue: an in-flight provider may still write rows after cancel | The window end is the cancel time. Late rows fall outside it. Documented limitation. |
| App crash mid-queue | Nothing is written, so the previous window stays. |
| CLI and Web UI scans | Not recorded (they do not use the desktop queue). Documented limitation. |
| DB merge/import, or manual host add, during the window | Rows whose `last_seen` falls inside the window show. Accepted. |
| Server List open while a queue finishes | The window is re-read on every data load (open, refresh, DB change). |
| `Has notes` and other filters | The new filter is one more step in `_apply_filters`. Order does not matter. |

## Cards

### C1 — record the last scan window

- New module `gui/utils/last_scan_window.py`:
  - `record_last_scan_window(start_utc, end_utc, db_path, *, providers, cancelled, paths=None)`
    writes `get_paths().state_dir / "last_scan_window.json"`. It writes atomically
    (temp file + `os.replace`) and logs failures without raising. Times are
    UTC `YYYY-MM-DD HH:MM:SS`. The start is floored and the end ceiled to whole
    seconds.
  - `load_last_scan_window(db_path, *, paths=None) -> Optional[dict]` returns
    `{"start", "end", "providers", "cancelled"}`. It returns `None` when the
    file is missing or malformed, a field is invalid, end < start, or the stored
    resolved DB path differs from `db_path`.
- `dashboard_provider_queue.py`:
  - `start_provider_queue` stores `dash._provider_queue_started_at` (UTC) once
    the queue is accepted.
  - `_finish_provider_queue` and `cancel_provider_queue` record the window
    (`cancelled` set to match). They use the dashboard's DB path. A record
    failure never breaks queue teardown.
- Tests: round trip, every `None` path, DB mismatch, no exception on write
  failure, finish and cancel both record, a rejected start records nothing.

### C2 — Server List tickbox and old-work removal

- `filter_dropdown.QUICK_FILTERS` gets `("most_recent_scan_only", "Most Recent Scan Only")`.
  It counts toward the button label.
- When no window exists, the option is disabled, and a muted label in the popover
  reads `No recorded scan yet`. This is a visible line, not a hover tooltip:
  there is no shared tooltip helper, and hover is unreliable on an
  override-redirect popover.
- `window.py`: new `BooleanVar`. `_load_data` loads the window with
  `db_reader.db_path`, sets the option state, and forces the var to `False`
  when there is no window. `_apply_filters` calls a pure
  `filters.apply_recent_scan_filter(servers, window)`.
- `filters.apply_recent_scan_filter`: keeps rows whose `last_seen` is in
  `[start, end]`. It accepts `T` or space, an optional `Z`, an offset, or
  naive (= UTC). Rows with a missing or unparseable `last_seen` are excluded.
- `actions/templates.py`: add the key in all five places (reset → `False`,
  load, persist, capture, apply). Map unknown `date_filter` values to `All`.
- Remove everything in "Old work removed".
- Tests: the pure filter (bounds, formats, missing date), combination with
  another filter, disabled state and forced-false, template/pref round trip,
  legacy `Since Last Scan` → `All`, the combobox no longer lists it, and a
  normal-UI path (tick → rows shrink → untick → rows restore).
- Docs: this file's validation section, `README.md` current task, and user
  docs that mention Server List filters.

## Validation

C2 completed on 2026-10-04. No commit or push.

RA follow-up: load the recorded window before restoring filter preferences.
A saved `most_recent_scan_only = True` now survives the initial panel build
when the active DB has a valid window. The deferred data load still reloads the
window. Two regression cases follow the panel build and deferred initial load,
then check the first filter application and persisted preferences. A valid
window preserves True; no window forces and persists False.

```text
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_server_list_recent_scan_filter.py gui/tests/test_server_list_notes_filter.py gui/tests/test_server_list_card4.py gui/tests/test_last_scan_window.py -q
151 passed in 1.08s

xvfb-run -a ./venv/bin/python -m pytest gui/tests -q -o faulthandler_timeout=60
2469 passed, 8 skipped in 28.82s
```

The sandbox blocked Tk's connection to Xvfb on the first attempt. Both commands
above ran with a working Xvfb display outside the sandbox. The theme-style and
messagebox guardrails passed unchanged.

Before deletion and after implementation, the whole-repo audit used:

```bash
grep -rnE 'Since Last Scan|last_scan_time|get_last_scan_time|filter_recent|on_show_all_toggle|show_all_button|_toggle_show_all_results|apply_recent_discoveries_filter|apply_date_filter' . --exclude-dir=.git --exclude-dir=venv --exclude-dir=__pycache__ --exclude-dir=.pytest_cache
```

| Symbol | Before removal | After removal |
|---|---|---|
| `Since Last Scan` | `filters.py`, `window.py`, this plan | Migration test and removal documentation only |
| `last_scan_time` / `get_last_scan_time` | `filters.py`, `window.py`, `scan_manager.py`, this plan | Historical references in this plan only |
| `filter_recent` | `window.py`, `actions/templates.py`, this plan | Historical references only; unrelated `filter_recent_candidates` functions and tests remain |
| `on_show_all_toggle` / `show_all_button` | `filters.py`, `window.py`, this plan | Historical references in this plan only |
| `_toggle_show_all_results` | `window.py`, `actions/templates.py`, this plan | Historical references in this plan only |
| `apply_recent_discoveries_filter` | `actions/templates.py`, `dirracuda`, `scripts/legacy/xsmbseek_legacy.py`, this plan | Historical references in this plan only |
| `apply_date_filter` | Definition in `filters.py`, caller in `window.py`, this plan | Definition and caller use two arguments; regression tests check the signature |

The audit found two obsolete `recent_activity` routes missed by the original
plan. Both routes and their unused `ServerListWindow` imports were removed.
No existing tests referenced the removed symbols. The new tests cover parsing,
inclusive bounds, notes intersection, hint visibility and live updates, saved
state, legacy dates, data reloads, and the real checkbutton-to-table path.
The `recent_scan_only` database heuristic remains unchanged.

RA real-lifecycle check (Xvfb, real `ServerListWindow` + `DatabaseReader` on a
temp DB, temp `HOME`): no window → option disabled with hint; queue record →
tick shows only the run's 2 of 4 hosts, `Filters (1) ▾`; restart with a fresh
`SettingsManager` → still ticked, same rows; switch to another DB → window
`None`, tick forced off.

## Follow-ups (not in scope)

- `apply_date_filter` compares local `datetime.now()` against UTC `last_seen`,
  so `Last 24 Hours` etc. are skewed by the local UTC offset. Existing bug.
- Web UI Results has its own `Filters ▾`; it has no Most Recent Scan Only yet.
