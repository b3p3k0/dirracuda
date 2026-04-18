# C3: SearXNG Dork Module — Service + Sidecar Persistence

## Context

C2 shipped: preflight client, PreflightResult model, SeDorkTab with URL persistence and Test button. The Run button is a no-op stub. C3 wires it: build the orchestrator (`service.py`), sidecar SQLite store (`store.py`), extend models with run/result types, and surface a run summary in the tab. Classification stays out of scope (C4).

---

## Files to Modify / Create

| File | Action | Est. lines |
|---|---|---|
| `experimental/se_dork/models.py` | Edit — add RunOptions, RunResult, run status constants | ~80 |
| `experimental/se_dork/store.py` | New — sidecar SQLite store | ~165 |
| `experimental/se_dork/service.py` | New — run orchestrator | ~130 |
| `gui/components/experimental_features/se_dork_tab.py` | Edit — Run wiring + max_results row + hardening | ~210 |
| `shared/tests/test_se_dork_store.py` | New | ~155 |
| `shared/tests/test_se_dork_service.py` | New | ~150 |
| `gui/tests/test_se_dork_tab.py` | Edit — add Run flow tests | ~285 total |

All are well under the 1700-line stop-and-plan threshold.

---

## Step 1 — `experimental/se_dork/models.py` (edit)

Add after existing `PreflightResult`:

```python
# Run status constants
RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE    = "done"
RUN_STATUS_ERROR   = "error"

@dataclass
class RunOptions:
    instance_url: str
    query: str
    max_results: int = 50

@dataclass
class RunResult:
    run_id: Optional[int]      # None on pre-DB error
    fetched_count: int
    deduped_count: int          # rows actually inserted (after URL dedupe)
    status: str                 # RUN_STATUS_*
    error: Optional[str]        # None on success
```

Keep existing `PreflightResult` and reason-code constants untouched.

---

## Step 2 — `experimental/se_dork/store.py` (new)

Pattern: mirrors `experimental/redseek/store.py` — caller-supplied connections for CRUD, `init_db` / `open_connection` own their own connections.

### Schema

**`dork_runs`**
```sql
CREATE TABLE IF NOT EXISTS dork_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    instance_url   TEXT    NOT NULL,
    query          TEXT    NOT NULL,
    max_results    INTEGER NOT NULL,
    fetched_count  INTEGER NOT NULL DEFAULT 0,
    deduped_count  INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL,
    error_message  TEXT
)
```

**`dork_results`**
```sql
CREATE TABLE IF NOT EXISTS dork_results (
    result_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL,
    url                 TEXT    NOT NULL,
    url_normalized      TEXT    NOT NULL,
    title               TEXT,
    snippet             TEXT,
    source_engine       TEXT,
    source_engines_json TEXT,
    verdict             TEXT,
    reason_code         TEXT,
    http_status         INTEGER,
    checked_at          TEXT,
    FOREIGN KEY (run_id) REFERENCES dork_runs(run_id),
    UNIQUE (run_id, url_normalized)
)
```

`UNIQUE (run_id, url_normalized)` enforces dedupe per run at the DB level; INSERT OR IGNORE skips dups.

### Public API

```python
def get_db_path(override=None) -> Path
def init_db(path=None) -> None          # idempotent; creates parent dirs
def open_connection(path=None) -> sqlite3.Connection   # WAL, FK ON, schema-checked
def normalize_url(url: str) -> str      # see locked policy below
def insert_run(conn, options: RunOptions, started_at: str) -> int
    # always writes status=RUN_STATUS_RUNNING; no status parameter
def update_run(conn, run_id: int, finished_at: str, fetched_count: int,
               deduped_count: int, status: str,
               error_message: Optional[str] = None) -> None
def insert_result(conn, run_id: int, row: dict) -> bool  # True=inserted, False=dup skipped
```

**`normalize_url` logic** (stdlib only, `urllib.parse`) — locked policy:
- Parse with `urlparse`
- Lowercase scheme and netloc
- Strip trailing slash from path
- **Drop query string** (query params not meaningful for open-dir deduplication)
- Drop fragment
- Re-assemble with `urlunparse`

Raw URL is always stored in the `url` column unchanged; only `url_normalized` uses this stripped form. Tests must match this exact policy.

**`insert_result` dict keys consumed**: `url`, `title`, `content` (→snippet), `engine` (→source_engine), `engines` (→JSON-serialized list).

---

## Step 3 — `experimental/se_dork/service.py` (new)

### Transaction ownership

Two separate commits per successful run (prevents error-path row loss):

```
preflight check     — network only, no DB (abort early; avoids DB creation on dead instance)
init_db()           — owns connection, commits internally
open_connection()   — opens write connection
  insert_run(conn, options, started_at)   # status hardcoded internally
  conn.commit()     ← COMMIT 1: durable run row exists before any network I/O
  _fetch_results()  — network only
  for each result: insert_result()  — no commit yet
  update_run(..., status="done", counts)
  conn.commit()     ← COMMIT 2: results + final status
  conn.close()
```

Order: preflight → init_db → open_connection. Preflight fires first so `~/.dirracuda/se_dork.db` is not created when the instance is dead.

Network errors before `open_connection` → return error `RunResult` with `run_id=None`.
DB errors after first commit → rollback result inserts; update_run to "error" with its own commit (run row is durable from COMMIT 1).

### `_fetch_results(base_url, query, max_results, timeout=15) -> List[dict]`

- Uses `urllib.request` + `urllib.parse.urlencode`
- Loop: fetch page N, extend `accumulated`, stop when `len(accumulated) >= max_results` or SearXNG returns empty results list
- Hard page cap: 10 pages (not derived from max_results or assumed page size)
- SearXNG pagination parameter name: `pageno` (locked; e.g. `pageno=1`, `pageno=2`, …)
- Caller slices accumulated list to `options.max_results` after return
- Returns raw result dicts from SearXNG JSON (`results` list)
- Raises `urllib.error.URLError` / `ValueError` on failure (caller handles)

### `run_dork_search(options: RunOptions, db_path=None) -> RunResult`

```
1. run_preflight(options.instance_url) → if not ok: return error RunResult (run_id=None)
   [no DB I/O yet — avoids creating the sidecar file on a dead instance]
2. try:
     init_db(db_path)
     conn = open_connection(db_path)
   except Exception as exc:
     return RunResult(run_id=None, fetched_count=0, deduped_count=0,
                      status=RUN_STATUS_ERROR, error=f"DB setup failed: {exc}")
3. max_results = max(1, min(500, options.max_results))   # clamped local; options never mutated
   started_at = utcnow isoformat
   run_id = insert_run(conn, options, started_at)   # status=RUN_STATUS_RUNNING hardcoded inside
   conn.commit()    ← COMMIT 1: durable run row before network I/O
4. try:
     raw_rows = _fetch_results(options.instance_url, options.query, max_results)
     capped = raw_rows[:max_results]   # enforce cap after pagination; uses clamped local
     fetched = len(capped)
     deduped = 0
     for row in capped:
         if insert_result(conn, run_id, row):
             deduped += 1
     update_run(conn, run_id, utcnow, fetched, deduped, RUN_STATUS_DONE)
     conn.commit()    ← COMMIT 2: results + final status
     return RunResult(run_id, fetched, deduped, RUN_STATUS_DONE, None)
   except Exception as exc:
     conn.rollback()  # undo partial result inserts
     try:
         update_run(conn, run_id, utcnow, 0, 0, RUN_STATUS_ERROR, str(exc))
         conn.commit()  # run row from COMMIT 1 is already durable; this sets error status
     except Exception:
         pass  # best-effort; swallow so we always return a structured RunResult
     return RunResult(run_id, 0, 0, RUN_STATUS_ERROR, str(exc))
   finally:
     conn.close()
```

**Clamp rule**: `max_results = max(1, min(500, options.max_results))` is the first statement in the post-DB-setup body (step 3 above). All downstream calls — `_fetch_results(…, max_results)` and `raw_rows[:max_results]` — use this local variable. `options` is never mutated.

---

## Step 4 — `se_dork_tab.py` (edit)

### New UI additions

Add between query row and buttons:

```
Max results:  [entry, width=8, default=50]
```

New settings keys:
- `_SETTINGS_KEY_QUERY = "se_dork.query"`
- `_SETTINGS_KEY_MAX_RESULTS = "se_dork.max_results"`

Load persisted query and max_results in `_build()`. Add `_save_settings()` that persists all three (URL + query + max_results). Call `_save_settings()` from both `_invoke_test()` and `_invoke_run()`.

### Run button wiring

Change `run_btn = tk.Button(...)` to `self._run_btn = tk.Button(..., command=self._invoke_run)`.

```python
def _invoke_run(self) -> None:
    url = self._url_var.get().strip()
    query = self._query_var.get().strip()
    if not url or not query:
        self._status_label.configure(text="Enter instance URL and query first.")
        return
    try:
        max_results = max(1, min(500, int(self._max_results_var.get().strip() or "50")))
    except (ValueError, TypeError):
        max_results = 50
    self._save_settings()
    self._test_btn.configure(state="disabled")
    self._run_btn.configure(state="disabled")
    self._status_label.configure(text="Running dork search…")
    options = RunOptions(instance_url=url, query=query, max_results=max_results)

    def _run() -> None:
        try:
            from experimental.se_dork.service import run_dork_search
            result = run_dork_search(options)
        except Exception as exc:
            from experimental.se_dork.models import RunResult, RUN_STATUS_ERROR
            result = RunResult(run_id=None, fetched_count=0,
                               deduped_count=0, status=RUN_STATUS_ERROR,
                               error=str(exc))
        self.frame.after(0, lambda: self._on_run_done(result))

    threading.Thread(target=_run, daemon=True).start()

def _on_run_done(self, result) -> None:
    self._test_btn.configure(state="normal")
    self._run_btn.configure(state="normal")
    if result.status == "done":
        self._status_label.configure(
            text=f"Done — fetched {result.fetched_count}, stored {result.deduped_count} unique."
        )
    else:
        self._status_label.configure(text=f"Run failed: {result.error}")
```

### Note on `_invoke_test`

Existing hardening at line 146 of `se_dork_tab.py` already covers the thread exception path — no changes needed there.

---

## Step 5 — Tests

### `shared/tests/test_se_dork_store.py`

- `test_init_db_creates_tables` — both tables exist after init
- `test_init_db_idempotent` — calling twice is safe
- `test_normalize_url_lowercases_and_strips` — parametrized cases
- `test_insert_run_returns_integer` — run_id is int
- `test_insert_result_returns_true_on_insert` — first insert returns True
- `test_insert_result_dedupes_by_normalized_url` — second INSERT OR IGNORE returns False
- `test_dedupe_allows_same_url_in_different_run` — different run_id bypasses UNIQUE
- `test_update_run_sets_status_and_counts` — verify via SELECT

All use `tmp_path` fixture and `get_db_path(override=tmp_path/...)`.

### `shared/tests/test_se_dork_service.py`

- `test_run_dork_search_success` — mock preflight OK + urlopen JSON, verify RunResult counts match rows
- `test_run_dork_search_preflight_fail` — mock preflight not-ok, RunResult.error set, run_id=None
- `test_run_dork_search_network_error` — mock urlopen raises URLError after preflight, returns error result
- `test_run_dork_search_dedupe` — SearXNG returns two rows with same URL, deduped_count=1
- `test_run_dork_search_respects_max_results` — 5-result response with max_results=3 stores 3
- `test_run_dork_search_db_setup_failure` — mock `init_db` to raise; verify RunResult has `run_id=None`, `status=RUN_STATUS_ERROR`, and no exception escapes
- `test_run_dork_search_persists_to_db` — sqlite3.connect real DB in tmp_path, verify row counts

All mock `urllib.request.urlopen` and `experimental.se_dork.client.run_preflight`.

### `gui/tests/test_se_dork_tab.py` (additions)

Add after existing tests:
- `test_invoke_run_calls_service` — monkeypatch thread synchronous, verify `run_dork_search` called with correct RunOptions
- `test_invoke_run_updates_status_on_success` — RunResult(status="done") → status label shows counts
- `test_invoke_run_updates_status_on_failure` — RunResult(status="error") → status label shows error
- `test_invoke_run_disables_both_buttons_then_reenables` — both `_test_btn` and `_run_btn` state sequence
- `test_invoke_run_exception_in_thread_reenables_buttons` — service raises, buttons re-enabled
- `test_invoke_run_empty_url_no_thread` — same guard as test button

---

## Reused Code

- `experimental/se_dork/client.py::run_preflight` — preflight gate in service
- `experimental/redseek/store.py` — store structure/pattern (sidecar path, DDL, `_check_schema`, caller-owned connections)
- `experimental/redseek/service.py` — transaction ownership pattern, `_error_result`-style helper
- Tab threading pattern from existing `_invoke_test` — `threading.Thread` + `frame.after(0, ...)`

---

## Verification

```bash
# 0. BEFORE — baseline counts for files that already exist (run before any edits)
wc -l \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/tests/test_se_dork_tab.py

# 1. AFTER — all touched files; rubric rating reported per file
wc -l \
  experimental/se_dork/service.py \
  experimental/se_dork/store.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py \
  shared/tests/test_se_dork_store.py \
  shared/tests/test_se_dork_service.py \
  gui/tests/test_se_dork_tab.py

# 2. Compile check — all new/edited Python files
./venv/bin/python -m py_compile \
  experimental/se_dork/service.py \
  experimental/se_dork/store.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py

# 3. Tests
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_store.py \
  shared/tests/test_se_dork_service.py \
  gui/tests/test_se_dork_tab.py -q
```

**HI test** (after tests pass): Open Experimental → SearXNG Dorking tab → enter instance URL + query → click Run → confirm status shows fetched/stored counts → check `~/.dirracuda/se_dork.db` for populated `dork_runs` and `dork_results` rows. Confirm Test button still works independently.
