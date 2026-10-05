# Concurrent Process Management — PA Plan

**Status:** Approved by HI 2026-10-05. §8 decisions P1–P6 accepted. HI
authorized running all four cards through to final HI test, skipping
intermediate HI tests (SOP exception, HI-granted). DA = `codex` CLI; RA =
Claude (different vendor, satisfies RA ≠ DA on B1/B2). HI owns push.
**Role at authoring:** PA. **Track:** full process.
**Date:** 2026-10-05. **Branch:** `development`.
**Input:** `FINDINGS_AA.md` (commits c0ea63c, 609930d). Decisions D1, D2, D3
and Q1–Q3 are frozen. This plan does not re-open them.

This file stands alone. A DA or RA from any vendor can execute from it with no
other context.

---

## 1. Goal

Make batch probing use the workstation's capacity. Three frozen decisions:

| ID | Decision |
|---|---|
| D1 | One shared probe-worker policy. Default **12**, cap **16**, min 1. User can override. No `cpu_count()` scaling. Server List batch uses the same policy. |
| D2 | SQLite WAL + `busy_timeout` on every read-write primary-DB opener. `DatabaseReader` uses thread-local connections. Drop the process-wide `connection_lock`. HI accepted the DB-config Hard Stop risk. |
| D3 | Pacing sleeps stay configurable. Lower the defaults. |

Out of scope (frozen): parallel share loop inside one host (S3); schema or
engine rework.

---

## 2. Corrections to FINDINGS_AA.md (code beats doc)

PA verified the repo and the live files on 2026-10-05. Five facts in the AA
doc differ from runtime. They do not change any decision. They change card
scope and expected payoff.

| # | AA doc says | Runtime fact | Effect on plan |
|---|---|---|---|
| F1 | Live DB is `journal_mode=delete`. | AA read `~/.dirracuda/dirracuda.db`, a **0-byte legacy stub**. The runtime DB is `~/.dirracuda/data/dirracuda.db` (`resolve_runtime_main_db_path()`). Its header (bytes 18–19 = `02 02`) and a read-only `PRAGMA journal_mode` both say **WAL**. The CLI's `tools/db_manager.py:103` set it. | No on-disk mode change is needed for the live DB. Card B's WAL work makes new or recreated DBs WAL at once and makes the mode explicit. Rollback needs no file action. |
| F2 | Live `busy_timeout=5000`, `synchronous=2`. | Both are per-connection values, not file state. 5000 ms is Python's default `timeout=5.0`. | Card B sets `busy_timeout` explicitly on every RW opener. |
| F3 | Pacing sleeps make the Dashboard batch probe feel serial. | The GUI probe runners (`gui/utils/probe_runner.py`, `ftp_probe_runner.py`, `http_probe_runner.py`) **never sleep**. `rate_limit_delay` paces CLI SMB auth (`commands/discover/auth.py`). `share_access_delay` paces the CLI SMB share loop (`commands/access/operation.py:248`). `download_delay_seconds` paces **extract only** (`gui/utils/extract_runner.py:545`, `protocol_extract_runner.py:391,876`). | Card C speeds CLI SMB scans and extract. It does not speed the Dashboard batch probe. The Dashboard win comes from Cards B1 + A. |
| F4 | Server List batch defaults to 1 worker. | Before CPM-A, the dialog supplied the saved `probe.batch_max_workers` value. CPM-A now shares default 12/cap 16 across the dialog and probe executor; other job types retain default 1/cap 8. | Folded into Card A. |
| F5 | Two worker sites (Dashboard, Server List). | **11** files were listed for CPM-A. Implementation also found a second Redseek runner clamp and Web UI provider selectors/JavaScript fallbacks. | Card A covers all listed and newly found probe-policy sites. |

Also found (new, Blocker-class latent bug, fixed by Card B1):
`_get_connection` (`gui/utils/database_access_core_methods.py:242-284`) is a
`@contextmanager` whose `"locked"` branch **yields a second time**. If a
`locked` error is raised inside the `with` body, the generator yields again and
Python raises `RuntimeError: generator didn't stop after throw()`. The retry
never worked as written.

Verification commands (read-only):

```bash
./venv/bin/python -c "from shared.path_service import *; p=get_paths(); print(resolve_runtime_main_db_path(paths=p, legacy=get_legacy_paths(paths=p)))"
./venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:/home/kevin/.dirracuda/data/dirracuda.db?mode=ro', uri=True); print(c.execute('PRAGMA journal_mode').fetchone())"
```

---

## 3. Card breakdown and landing order

PA decision: **split B into B1 and B2; do not merge A with B.**
Reasons: B carries the Hard Stop and needs an independent RA; A is low risk and
should not inherit that review weight. B1 alone unblocks A. B2 is a mechanical
sweep that is easier to review apart from the connection-lifecycle change.

| Order | Card | Decision | Risk | Depends on | RA ≠ DA required |
|---|---|---|---|---|---|
| 1 | **CPM-B1** — shared pragma helper + `DatabaseReader` thread-local + drop lock | D2 | High | — | **Yes** |
| 2 | **CPM-B2** — apply helper to remaining RW primary-DB openers | D2 | Medium | B1 | **Yes** |
| 3 | **CPM-A** — shared probe-worker policy (12 / 16) | D1 | Low | B1 | Charter default |
| 4 | **CPM-C** — lower pacing defaults; zero means "no wait" | D3 | Low | none | Charter default |

C has no dependency. It may land any time after approval. One card, one
patch. RA accepts a card before the next starts.

---

## 4. CPM-B1 — WAL helper, thread-local `DatabaseReader`, drop the lock

### Scope

1. **New module `shared/db_connection.py`.** Contents:
   - `PRIMARY_DB_BUSY_TIMEOUT_MS = 30000` (matches today's GUI and CLI connect
     timeout of 30 s; not a behavior change for those openers).
   - `configure_primary_connection(conn, *, busy_timeout_ms=PRIMARY_DB_BUSY_TIMEOUT_MS) -> str`.
     Runs, in this order:
     1. `PRAGMA busy_timeout=<ms>`
     2. `PRAGMA journal_mode=WAL` and reads the returned mode.
     If the mode is not `wal`, log one warning (logger, not stdout — the GUI
     parses CLI stdout) and continue. Do not raise: a read-only or network
     filesystem may refuse WAL, and today's code works there. Return the mode.
   - No `synchronous` change (stays at default FULL). No `foreign_keys` change.
     See §8 decision P2.
2. **`gui/utils/database_access.py`.** Replace `self.connection_lock =
   threading.Lock()` with `self._local = threading.local()`.
3. **`gui/utils/database_access_core_methods.py::_get_connection(timeout=30)`.**
   Rewrite:
   - Per-thread cached connection. Create lazily with
     `sqlite3.connect(self.db_path, timeout=timeout, check_same_thread=False)`,
     `row_factory = sqlite3.Row`, then `configure_primary_connection(conn)`.
     Store the path the connection was opened for.
   - Lazy `_local`: use `getattr(self, "_local", None)` and create it if
     missing, so readers built with `object.__new__(DatabaseReader)` still work.
   - Path change: if the cached connection's path differs from `self.db_path`
     (after `set_database_path`), close it on this thread and reopen.
   - Per checkout: `PRAGMA busy_timeout=<timeout*1000>` so the existing
     `timeout=` argument keeps its meaning (e.g. `write_methods.py:1360` passes 5).
   - Depth counter on the thread-local. Only the **outermost** exit cleans up.
   - Cleanup on outermost exit: if `conn.in_transaction`, `conn.rollback()`.
     This keeps today's semantics: today `close()` discards uncommitted work.
     On exception, roll back the same way, then re-raise the **original**
     exception.
   - **Delete** the `"locked"` retry branch (the double-yield bug in §2).
     `busy_timeout` replaces it.
   - **No lock** anywhere on the DB path.
   - Add `close_thread_connection()` — closes this thread's connection only.
     Never close another thread's connection.
4. **`tools/db_manager.py::get_connection`.** Call
   `configure_primary_connection` in place of the bare WAL pragma. Keep
   `foreign_keys=ON` there (unchanged behavior).
5. **`shared/db_migrations.py::run_migrations` (line 47).** Call
   `configure_primary_connection` after connect. This makes a DB that the GUI
   creates first become WAL on first open.
6. **`shared/tests/test_sherlock_persistence.py`** (4 sites, lines 298, 324,
   415, 702). Remove the `reader.connection_lock = ...` lines.
7. Tests in §4 matrix.

### Non-goals

- No change to `mode=ro` openers (they cannot set WAL; they read a WAL DB fine).
- No change to sidecar DBs, Analyst DB, subset-export output (it is
  intentionally DELETE on its own new file), `scripts/`, `tools/` one-off
  scripts.
- No change to `gui/components/app_config_dialog.py` (has HI's uncommitted
  edits; its line 1073 opener is a read-only validation probe).
- No `foreign_keys` change on `DatabaseReader`.

### Acceptance criteria

- `grep -rn "connection_lock" gui shared experimental` returns nothing.
- `_get_connection` contains no `time.sleep`, no second `yield`, no lock.
- A fresh DB opened by `DatabaseReader` or `run_migrations` reads
  `journal_mode=wal` and `busy_timeout=30000`.
- Every row of the B1 test matrix passes, and each new test fails against the
  pre-change code (DA shows this for T5, T6, T8, T13 by running them on a stash
  of the old code, or states why not).
- `./venv/bin/python -m pytest shared/` and `./venv/bin/python -m pytest gui/`
  pass, run separately (known Tk abort flake on combined runs).

### Test matrix (new file `shared/tests/test_db_wal_concurrency.py`)

All tests use `tmp_path`. No real network. Every wait has a timeout so a
regression fails instead of hanging. Subprocess tests use
`subprocess.run([sys.executable, "-c", ...], timeout=60)` — argument list,
no shell.

| # | Area | Setup | Assertion |
|---|---|---|---|
| T1 | Pragmas | Fresh DB via `DatabaseReader` | `journal_mode=='wal'`; `busy_timeout==30000`; after all conns close, file header bytes 18–19 == `02 02` (sticky). |
| T2 | Sticky across openers | Create via `DatabaseReader`; reopen via `DatabaseManager`, plain `sqlite3.connect` | All report `wal`. |
| T3 | Fresh DB via migrations | `run_migrations(new_path)` only | `wal`. |
| T4 | Thread-local identity | Two checkouts on one thread; one on another thread | Same object on one thread; different objects across threads. |
| T5 | No global lock | Thread A holds a checkout and waits on a `threading.Barrier(2, timeout=5)`; thread B enters a checkout and hits the barrier | Both pass the barrier. (Old code deadlocks → `BrokenBarrierError`.) |
| T6 | Read during write (in-process) | Writer thread: `BEGIN IMMEDIATE`, insert, hold on an `Event`. Reader thread (separate `DatabaseReader`) runs `SELECT` | Reader returns within 1 s with the **pre-write** snapshot. After writer commits, a new read sees the row. |
| T7 | Concurrent writers (in-process) | 12 threads × 25 writes through a real `DatabaseReader` write method (DA picks the probe-persist path, e.g. the probe-cache upsert) | No `OperationalError`. Exact final row count. |
| T8a | **GUI/CLI subprocess boundary — concurrent writes** | Subprocess uses `tools.db_manager.DatabaseManager` (the CLI opener) to insert 200 rows in small transactions. Parent concurrently runs 4 `DatabaseReader` threads: 2 readers looping `SELECT count(*)`, 2 writers inserting 100 rows each | Subprocess exit 0; no `database is locked` in its stderr; parent threads raise nothing; final count == 400. |
| T8b | **Subprocess holds the write lock** | Subprocess opens `BEGIN IMMEDIATE`, writes, prints `READY`, sleeps 2 s, commits | Parent read completes in < 0.5 s while the lock is held. Parent write blocks, then succeeds after the commit (within `busy_timeout`). |
| T8c | **Reverse direction** | Parent `DatabaseReader` holds `BEGIN IMMEDIATE` for 2 s; subprocess (`DatabaseManager`) reads then writes | Subprocess read is not blocked; subprocess write succeeds after parent commit; exit 0. |
| T9 | Transaction hygiene | (a) body does DML then raises; (b) body does DML, no commit; (c) nested checkout inside an open txn | (a) original exception surfaces; next checkout on the thread has `in_transaction == False`; a subprocess write succeeds. (b) row is discarded at exit (same as today). (c) outer txn survives the inner exit and commits. |
| T10 | Path switch | Checkout on DB-1; `set_database_path(DB-2)`; checkout | Second checkout queries DB-2. |
| T11 | **Rollback safety** | Use DB under new code (WAL). Then (a) open with a plain per-call `sqlite3.connect` (old-code style) and read/write; (b) close all, run `PRAGMA journal_mode=DELETE` | (a) works; (b) returns `delete`, `integrity_check == ok`, all rows present. Proves a code revert is safe and documents HI's file rollback. |
| T13 | Old retry bug gone | Body raises `sqlite3.OperationalError("database is locked")` | The same `OperationalError` propagates — not `RuntimeError`. |
| T14 | Thread churn | Run a 12-worker `ThreadPoolExecutor` batch of checkouts; shut down; `gc.collect()` | No `ResourceWarning`/leak: count of open conns tracked via a weakref set in the test returns to the UI-thread baseline. |

### Rollback (B1)

| Layer | Action | Who |
|---|---|---|
| Code | `git revert <B1 commit>` | MI may prepare; HI owns push |
| Live DB file | **None needed.** The live file was WAL before B1 (F1). Old code reads/writes a WAL file fine (T11a). | — |
| Optional full revert to rollback journal | With every Dirracuda process closed: `sqlite3 ~/.dirracuda/data/dirracuda.db "PRAGMA journal_mode=DELETE;"` from any directory. Effect: folds `-wal` into the main file and removes `-wal`/`-shm`. Risk: fails with `database is locked` if a process is still open — close it and retry. Note: the next CLI run sets WAL again (`tools/db_manager.py` did so before this effort). | **HI runs it** |

### Risks (B1)

| Risk | Mitigation |
|---|---|
| A caller leaves a transaction open on a persistent connection and blocks the CLI writer. | Outermost-exit rollback (T9). |
| Many short-lived `DatabaseReader` instances (≈19 construction sites, e.g. `server_list_window/details.py`) each keep a UI-thread connection until GC. | CPython frees on refcount drop; idle WAL connections hold no lock. T14 checks release. |
| WAL refused (network FS). | Helper warns and continues — same as today's behavior. |
| UI thread waits on a GUI write while the CLI writes. | Same 30 s ceiling as today; WAL removes reader/writer blocking, so only GUI writes can wait. |

---

## 5. CPM-B2 — Apply the helper to the remaining RW primary-DB openers

### Scope

Call `configure_primary_connection(conn)` right after each connect below.
Enumerated with `grep -rn "sqlite3.connect(" --include=*.py` on 2026-10-05;
DA must re-run the grep and reconcile before editing.

| File | Line(s) | Opener |
|---|---|---|
| `shared/database_ftp_persistence.py` | 77, 107, 252, 278, 295, 322 | CLI FTP persistence (today: default 5 s timeout) |
| `shared/database_http_persistence.py` | 79, 113, 258, 284, 314, 382 | CLI HTTP persistence (today: default 5 s timeout) |
| `gui/utils/data_import_engine.py` | 508, 565 | Data import (RW) |
| `gui/utils/db_tools_engine_core_methods.py` | 372 | DB tools RW |
| `gui/utils/db_tools_engine_maintenance_methods.py` | 44 (VACUUM INTO), 244 (VACUUM), 503 | Maintenance RW |
| `gui/utils/db_tools_engine_merge_methods.py` | 73 | Merge target RW |
| `experimental/webui/db_actions.py` | 31 (`_connect_rw`) | Web UI RW |
| `experimental/webui/results_probe_actions.py` | 66 | Web UI RW |
| `experimental/webui/db.py` | 829 (VACUUM INTO export) | Web UI RW |

Excluded, with reason: every `mode=ro` opener (cannot set WAL); subset-export
output (own file, intentionally DELETE); sidecar stores; Analyst; `scripts/`;
`tools/` one-off scripts; `app_config_dialog.py:1073` (read-only probe, file
dirty with HI edits).

Behavior change to call out: FTP/HTTP CLI persistence moves from a 5 s to a
30 s busy wait. This is intended (more GUI write concurrency after Card A).

### Acceptance criteria

- Every row above calls the helper. A guardrail test
  (`shared/tests/test_db_wal_concurrency.py::test_rw_openers_use_helper`)
  parses the listed files and fails if a non-`mode=ro` `sqlite3.connect(` in
  them is not followed by `configure_primary_connection` within the same
  function.
- **T8d** (added to the matrix): subprocess writes via
  `shared/database_http_persistence.py` (the real CLI HTTP path) while the
  parent runs `DatabaseReader` readers + writers. Same assertions as T8a.
- **T12**: a `mode=ro` connection reads while another connection holds
  `BEGIN IMMEDIATE`; the read returns within 1 s.
- Existing DB-tools tests pass: `gui/tests/test_db_tools_engine_maintenance.py`,
  `gui/tests/test_db_tools_subset_export.py`, Web UI tests in
  `experimental/webui/tests/`.

### Rollback (B2)

`git revert <B2 commit>`. No file action.

---

## 6. CPM-A — Shared probe-worker policy

### Scope

1. **New module `shared/probe_concurrency.py`** (in `shared/` because
   `experimental/` services and Web UI models must import it without Tk):
   - `PROBE_WORKERS_DEFAULT = 12`, `PROBE_WORKERS_MAX = 16`,
     `PROBE_WORKERS_MIN = 1`.
   - `clamp_probe_workers(value) -> int`: `None`, non-numeric, bool, `inf`/`nan` →
     default; otherwise coerce with `int` and clamp to [1, 16].
   - `resolve_probe_workers(settings_manager) -> int`: reads
     `probe.batch_max_workers` (default 12) and clamps. Any exception →
     default.
2. **Replace every hard-coded probe default/cap** (all 11 sites):

| # | File | CPM-A implementation |
|---|---|---|
| 1 | `gui/components/dashboard_batch_ops.py` | Shared settings resolver |
| 2 | `gui/components/server_list_window/actions/batch.py` | Shared clamp for probe only; other jobs retain default 1/cap 8 |
| 3 | `gui/components/server_list_window/actions/batch_operations.py` | Shared resolver/clamp and cap-derived label |
| 4 | `gui/components/scan_preflight.py` | Shared resolver/clamp and cap-derived label |
| 5 | `gui/components/scan_provider_options.py` | Shared resolver for workers |
| 6 | `gui/components/reddit_browser_window.py` | Shared settings resolver |
| 7 | `gui/components/se_dork_browser_window.py` | Shared settings resolver |
| 8 | `experimental/se_dork/service.py` | Shared clamp |
| 9 | `experimental/redseek/service.py` | Shared resolver clamp plus runner default/clamp |
| 10 | `experimental/webui/experimental_models.py` | Both validators use shared min/max constants |
| 11 | `gui/utils/default_gui_settings.py` | `probe.batch_max_workers` uses `PROBE_WORKERS_DEFAULT` |

   Additional sites found during CPM-A: `experimental/webui/templates/reddit.html`
   and `searxng.html`, plus `static/reddit.js` and `searxng.js`. Selectors now
   render 1–16 with default 12 using shared constants supplied by `app.py`;
   JavaScript fallbacks read that rendered default. Saved browser selections win.

   Note on #2: `batch.py:51 _start_batch_job` serves several `job_type`s
   (`probe`, `extract`, and others; it dispatches at lines 103 and 118). Apply the
   probe policy only when `job_type == "probe"`. Every other job type keeps
   today's default and cap. Use an explicit branch per job type, not a bare
   `else` (Development SOP). DA confirms the job-type values from the file.
3. **Saved values are honored.** A saved `probe.batch_max_workers` still wins.
   No migration of saved values (PA decision P3).

### Non-goals

`extract.batch_max_workers`, file-browser download workers, CLI
`discovery`/`access` `max_concurrent_hosts`, and the per-host share loop.

### File-size rule

Four target files exceed the 1200-line convention (`dashboard_batch_ops.py`
1629, `batch_operations.py` 1320, `reddit_browser_window.py` 1275,
`se_dork/service.py` 1257). Edits there must be **net-zero or negative** lines.
Replacing a literal clamp with a helper call meets that. No extraction needed.

### Acceptance criteria

- `grep -rnE "min\(8|, 8\)\)" ` over the 11 files shows no probe-worker clamp.
- Unit tests for `clamp_probe_workers` / `resolve_probe_workers`: unset → 12;
  `0` → 1; `99` → 16; `"abc"` → 12; `None` → 12; `16` → 16.
- `resolve_probe_limits(None)["workers"] == 12` (`scan_provider_options`).
- Web UI model accepts 16, rejects 17 and 0.
- Redseek and SE-dork resolvers: `None` → 12; `20` → 16.
- Dashboard batch probe with a fake settings manager (no saved value) builds a
  `ThreadPoolExecutor` with 12 workers (monkeypatch the executor).
- Existing suites pass, including `gui/tests/test_unified_scan_dialog_layout.py`
  and `gui/tests/test_scan_provider_options_searxng_scales.py` (slider range
  changes).
- Docs closeout: every doc that states "max 8" or "default 3" for probe
  workers is updated (DA greps `docs/`, `README.md`, `AGENTS.md`).

### Rollback

`git revert <A commit>`. Saved user settings are untouched.

---

## 7. CPM-C — Lower pacing defaults; zero means no wait

### Scope

1. **New defaults** (PA decision P4):

| Key | Hard-coded (`shared/config.py`) | `conf/config.json.example` | `default_gui_settings.py` (`scan_dialog`) | New |
|---|---|---|---|---|
| `connection.rate_limit_delay` | 3 | 1 | 1 | **0** |
| `connection.share_access_delay` | 7 | 2 | 1 | **0** |
| `file_collection.download_delay_seconds` | 2 | 2 | — | **0.5** |

   0.5 for downloads matches the existing fallback in
   `gui/components/batch_extract_dialog.py:106` and
   `server_list_window/details.py:1143`. It paces repeat requests to the
   **same** host, which is the one place pacing still has a purpose.
2. **`commands/discover/auth.py`.** When `get_rate_limit_delay() <= 0`, return
   immediately from both the smart and basic throttle waits. Today the smart
   path forces a hard 0.5 s floor (`max(0.5, effective_delay)`, line 61) under a
   global lock, which caps SMB auth at about 2 per second across all workers.
   Keep the floor when the delay is positive.
3. **`commands/access/operation.py:248`.** Skip the sleep when delay ≤ 0.
4. **Zero is valid input everywhere these are parsed.** DA checks
   `gui/components/scan_dialog.py` (`_parse_positive_int` at 723-736 and
   `_coerce_int` at 650-651), `shared/config.py` getters, and any config
   validation. Zero must be accepted and saved.

### Non-goals

Editing the user's live config. `~/.dirracuda/conf/config.json` holds `1 / 2 /
2` and overrides the new defaults. HI changes it if wanted (see Next in §10).
No change to timeouts, port-check timeouts, or Shodan pacing.

### Acceptance criteria

- With delay 0: smart and basic auth throttles do not call `time.sleep`
  (monkeypatched); share loop does not sleep.
- With delay 1: existing pacing behavior is unchanged (regression test).
- Scan dialog accepts and persists 0 for both fields.
- `SMBSeekConfig` with no config file returns 0, 0, 0.5.
- Docs closeout: config reference rows for the three keys.

### Rollback

`git revert <C commit>`. No data impact.

---

## 8. Decisions PA made (bounded judgment) — accepted by HI 2026-10-05

| # | Decision | Reason | HI can flip by |
|---|---|---|---|
| P1 | Split B into B1/B2; keep A separate. | Isolates the Hard-Stop change for an independent RA; B1 alone unblocks A. | Saying "merge". |
| P2 | `DatabaseReader` does **not** turn on `foreign_keys`. | D2's goal is lock relief. Enforcing FKs on GUI writes is a separate behavior change (deletes and upserts could start failing or cascading). `db_manager` keeps FK=ON as today. | Asking for a follow-up card. |
| P3 | Saved `probe.batch_max_workers` values are honored; no migration to 12. | A saved value is a user override (D1). PA cannot tell a deliberate 3 from a saved default. | Asking for a one-time migration card. |
| P4 | Delays 0 / 0 / 0.5. | HI rationale: load lands only on our host; host B does not affect host A. Downloads keep a small same-host pause. | Naming other numbers at approval. |
| P5 | Busy timeout 30 s for all RW openers. | Matches today's GUI and CLI; only FTP/HTTP persistence changes (5 s → 30 s). | Naming another value. |
| P6 | Helper warns, never raises, when WAL is refused. | Today's code works on filesystems that refuse WAL; raising would break them. | — |

---

## 9. Process notes

- Hard Stops: B1 and B2 are DB configuration. HI accepted the risk
  (FINDINGS_AA §3 D2). No schema change. No data migration. MI does not edit
  user config files or the live DB.
- RA must be a different holder than DA on B1 and B2.
- RA creates `LESSONS_LEARNED.md` in this folder at the first acceptance.
- Each card's commit message states what changed, why, and the test commands
  run with results.
- Validation commands per card:

```bash
./venv/bin/python -m pytest shared/tests/test_db_wal_concurrency.py -v
./venv/bin/python -m pytest shared/
./venv/bin/python -m pytest gui/
./venv/bin/python -m pytest experimental/webui/tests/
```

- **HI manual check after B1 + A:** start a Dashboard batch probe of ~20 hosts
  while a CLI scan runs. Expected: dashboard stays responsive;
  `~/.dirracuda/data/dirracuda.db-wal` exists during the run; no "database is
  locked" messages.

---

## 10. Status

| Card | Commit | Result |
|---|---|---|
| B1 | e5cfd1c | Accepted. shared+webui 4034 pass; gui 2502 pass. |
| B2 | bc1e560 | Accepted. 7 extra openers found by DA grep added at RA direction (DatabaseReader `_ensure_http_columns`, Redseek store ×3, SE-dork store ×2); `analyze_database_schema` exempt as a read-only validation probe. |
| A | 8a9806a | Accepted. Also covered Web UI selectors/JS and a second Redseek clamp. |
| C | 665e37a | Accepted. shared+webui 4109 pass; gui 2520 pass. |

Status: all four cards accepted; ready for final HI test (§9 manual check).
Next: HI runs the manual check; HI pushes.
Risk: higher default concurrency is not yet benchmarked on live hosts.
Open: the live `~/.dirracuda/conf/config.json` still holds delays 1 / 2 / 2,
which override the new defaults. HI edits it if wanted.
