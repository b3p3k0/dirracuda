# B1 — Blank report: read retry + honest error surface

Status: HELD for DA (codex) implementation. One card. Commit per card, no push.

## Problem (root cause, confirmed)

The Analyst sidecar SQLite runs in DELETE journal mode (readers and writers are mutually
exclusive), with a 250 ms busy timeout and **no retry on the report viewer's read paths** (only the
write path retries, via `store.run_immediate`). While a run/session is actively writing (lease
heartbeats + state writes), a viewer read hits `SQLITE_BUSY` (`database is locked`), the exception
is **swallowed**, and the viewer keeps its placeholder (blank) values on screen. When the DB is
quiet the same read succeeds, so reports appear intermittently. Data is never lost — the viewer
always reads the correct per-run `output_root/report.json`.

Worse, busy and genuine-legacy failures currently collapse to the **same** swallowed exception, so
the viewer shows the misleading "Legacy run - re-run to view a read." on a mere lock collision.

## Goal

The viewer must (1) wait out a short write lock via bounded retry instead of failing, and (2) when
a read still can't complete because the DB is busy, show an honest "busy — retrying" state with a
**Retry** button — never a blank report and never the false "Legacy run" message.

Do NOT change the journal mode (stays DELETE) and do NOT weaken the owner-only 0600 / same-file /
PRAGMA guards. (Operator chose retry over WAL.)

## Changes

### 1. Read-path retry helper — `experimental/analyst/store.py`

- Add constants near the existing `BUSY_TIMEOUT_MS`/`TRANSACTION_ATTEMPTS`/`TRANSACTION_BACKOFF_SECONDS`
  (store.py:49-51):
  - `READ_BUSY_TIMEOUT_MS = 2000`
  - `READ_ATTEMPTS = 6`
  - `READ_BACKOFF_SECONDS = (0.05, 0.10, 0.15, 0.20, 0.25)`  (len == READ_ATTEMPTS-1)
- Parametrize the connection open so read opens can use the longer busy timeout:
  - `_connect(path, *, read_only, validate, busy_timeout_ms: int = BUSY_TIMEOUT_MS)`
    (currently store.py:532). Use `busy_timeout_ms` for both the `sqlite3.connect(timeout=...)`
    (store.py:544) and the `PRAGMA busy_timeout=` in `_configure_connection`.
  - `_configure_connection(conn, *, read_only, busy_timeout_ms: int = BUSY_TIMEOUT_MS)`
    (currently store.py:640). The `("busy_timeout", str(BUSY_TIMEOUT_MS), BUSY_TIMEOUT_MS)` tuple
    entry (store.py:652) must compare against the **passed** `busy_timeout_ms`, not the constant, so
    the strict PRAGMA verification still passes. Every other PRAGMA/guard stays exactly as-is.
  - `open_connection(path=None, *, read_only=False, busy_timeout_ms: int = BUSY_TIMEOUT_MS)`
    (store.py:134) — forward the timeout to `_connect`. Default keeps existing behavior; write
    callers are unchanged.
- Add a read-only retry runner mirroring `run_immediate` (store.py:141-169) but with no
  `BEGIN IMMEDIATE`/`COMMIT` (read-only), using `READ_ATTEMPTS`/`READ_BACKOFF_SECONDS` and opening
  with `busy_timeout_ms=READ_BUSY_TIMEOUT_MS`:
  ```
  def run_read(operation: Transaction[_T], *, path: Path | None = None) -> _T:
      """Run one read-only query with bounded whole-operation busy retry."""
      last_busy = None
      for attempt in range(READ_ATTEMPTS):
          conn = None
          try:
              conn = open_connection(path, read_only=True, busy_timeout_ms=READ_BUSY_TIMEOUT_MS)
              return operation(conn)
          except sqlite3.Error as exc:
              if not _is_primary_busy(exc):
                  raise
              last_busy = exc
          finally:
              if conn is not None:
                  conn.close()
          if attempt < len(READ_BACKOFF_SECONDS):
              time.sleep(READ_BACKOFF_SECONDS[attempt])
      raise AnalystStoreBusy("Analyst sidecar remained busy after bounded read retry") from last_busy
  ```
  Export `run_read` in `store.__all__`.

### 2. Route the report-viewer reads through `run_read`

Wrap the read-only DB access (open + SELECT) of each viewer read function so it retries on busy and
raises `AnalystStoreBusy` when the budget is exhausted. Keep every existing validation/decode and
the `finally: conn.close()` semantics (now handled inside `run_read`).

- `experimental/analyst/report_browser.py`
  - `list_completed_reports` (report_browser.py:132-150) — replace the manual
    `open_connection(...)/try/finally` with `run_read(lambda conn: <the SELECT + tuple build>, path=path)`.
  - `open_completed_report` (report_browser.py:153-192) — same, wrapping the SELECT; keep the
    manifest verify (`verify_completed_report`) and the post-check outside/after as today.
  - `load_completed_inventory_page` / `load_completed_detector_page` / `load_completed_model_page`
    (report_browser.py:195-293) — wrap each SELECT block in `run_read`. Preserve the existing
    `except (ValueError, TypeError, KeyError, OverflowError) -> ReportStateError` decode guard
    (move it inside the operation or keep it around the `run_read` call — either is fine as long as
    `AnalystStoreBusy` is NOT caught by it).
- `experimental/analyst/report.py`
  - `open_completed_report_relaxed` (report.py:280-317) — route the `open_connection`/SELECT block
    (report.py:289-305) through `run_read`. Critically, add `except AnalystStoreBusy: raise`
    **before** the existing `except BaseException:` (report.py:316) so a busy result propagates as
    `AnalystStoreBusy` instead of being masked into `ReportFailure.STATE`.
- `experimental/analyst/service.py`
  - Add `BUSY = "busy"` to the `ServiceFailure` enum (service.py:59-70).
  - `read_report_json` (service.py:710-726) — add `except AnalystStoreBusy: raise
    AnalystServiceError(ServiceFailure.BUSY) from None` **before** the catch-all
    `except Exception: -> ServiceFailure.REPORT` (service.py:725), so the GUI can tell busy from a
    genuine report failure. (Import `AnalystStoreBusy` from `.store`.)

### 3. Honest error surface + Retry — `gui/components/analyst_report_window.py`

- Imports: `from experimental.analyst.store import AnalystStoreBusy` and
  `from experimental.analyst.service import AnalystServiceError, ServiceFailure` (keep the existing
  inline `read_report_json` import or lift it up).
- Add a **Retry** button in the run-picker row next to "Refresh" (built in `_build_run_picker`,
  analyst_report_window.py:56-82). Keep a handle (`self._retry_btn`); it is hidden by default
  (`pack_forget()` / don't pack) and its `command=self._load_runs`. Add helpers
  `_show_retry()` (packs it) and `_hide_retry()` (pack_forget) — call `_hide_retry()` at the start
  of `_load_runs` and on any successful load path.
- `_load_runs` (analyst_report_window.py:240-266): split the `except`:
  - `except AnalystStoreBusy:` → set `_run_var`/`_status_var` to a busy message
    ("Reports are busy — the analysis is still writing. Click Retry."), call `_show_retry()`, and
    return WITHOUT clobbering a previously shown report (do not `_clear_report`).
  - `except Exception:` → keep the existing "Completed reports are unavailable." path.
- `_open_selected` (analyst_report_window.py:273-286): catch `AnalystServiceError` and branch on
  `.code`:
  - `ServiceFailure.BUSY` → set a busy status ("Report is busy — the analysis is still writing.
    Click Retry."), `_show_retry()`, return without `_show_legacy_run()` / without clearing a shown
    report.
  - any other code (or other `Exception`) → keep `_show_legacy_run()` (genuine legacy/validation).
  On the success path (`_show_report`) call `_hide_retry()`.
- Reserve `_LEGACY_MESSAGE` for real legacy/validation misses only, never for busy.

## Tests

- `experimental/analyst/tests` (shared area): a `run_read` retry test — hold the write lock
  (open a `BEGIN IMMEDIATE` on a second connection, or monkeypatch to raise `SQLITE_BUSY` on the
  first N attempts) and assert `run_read` retries then succeeds, and that it raises
  `AnalystStoreBusy` when the lock never clears. A test that `open_completed_report_relaxed` and
  `read_report_json` surface busy as `AnalystStoreBusy` / `ServiceFailure.BUSY` respectively (not
  `STATE`/`REPORT`).
- `gui/tests` (xvfb): a report-window test that, when `list_completed_reports` / `read_report_json`
  raise busy, the window shows the busy status + Retry button and does NOT blank a prior report or
  show the "Legacy run" message; and that a genuine failure still shows legacy/unavailable.
- Full existing analyst suites stay green:
  `./venv/bin/python -m pytest shared/tests -k analyst -q` and
  `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` (run areas separately).

## Out of scope

WAL, delete (B2), large-dir visibility (B3). Do not touch journal mode or the owner-only/file
guards. Do not alter the write path's retry.
