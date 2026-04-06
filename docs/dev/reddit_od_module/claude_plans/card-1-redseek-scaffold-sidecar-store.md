# Card 1 Plan: redseek Scaffold + Sidecar Store
## Revision 3 — second critique round fixes applied

## Context

First implementation card for the Reddit OD module (`redseek`). Establishes the isolated package skeleton and its sidecar SQLite database. No networking, no GUI, no service layer — those come in later cards. The goal is a stable foundation that later cards build on without touching main DB machinery.

Constraint source: `docs/dev/reddit_od_module/` decision set (SPEC, ARCHITECTURE, LOCKED_DECISIONS).

### Revision history
**R1 → R2 fixes:**
- `upsert_post`: `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE` (prevents FK cascade delete of targets)
- `wipe_all`: calls `init_db(path)` first (first-run replace_cache safety); removed `PRAGMA foreign_keys=OFF`
- Gate A3: equality assertion → subset check (tolerates `sqlite_sequence`)
- Gates A8/A9: full-suite run → targeted checks

**R2 → R3 fixes:**
- `_check_schema`: adds constraint validation (UNIQUE, composite PK, FK) via PRAGMA index_list/index_info/foreign_key_list
- `upsert_post`: explicit immutable-vs-mutable field policy locked and tested
- Gate A8: "no tests ran" is no longer an acceptable PASS; `shared/tests/test_redseek_store.py` added to file touch list
- Gate A9: replaced broad keyword filter with fixed 6-file regression set and explicit baseline-comparison instruction

---

## 1. Constraint Summary

- **Separate sidecar DB** at `~/.dirracuda/reddit_od.db` — zero writes to main `dirracuda.db` tables.
- **No CLI path for MVP** — module is GUI-only; no CLI wiring in Card 1.
- **`Replace cache` = full wipe** — deletes rows from all three tables in one atomic transaction; preserves schema for immediate reuse.
- **Schema source of truth is the architecture doc** — `reddit_ingest_state` uses composite PK `(subreddit, sort_mode)`, not single `subreddit` PK from SPEC. Architecture wins.
- **Additive-only, idempotent schema ops** — `CREATE TABLE IF NOT EXISTS`; `PRAGMA table_info()` guard before any `ALTER TABLE`.
- **No shared DB imports in redseek** — do not import `shared.database`, `shared.db_migrations`, or `shared.config`. Keeps isolation clean; avoids Shodan/SMB import side effects.
- **Timestamps**: `datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")` matching existing app convention.
- **Guard schema + constraints before data ops** — `_check_schema()` validates column names, UNIQUE constraints, composite PK, and FK at `open_connection()`.
- **`redseek/` lives at repo root** — peer to `shared/`, `commands/`, `gui/`.
- **Card 1 does not register itself with the GUI** — no dashboard changes, no import in existing entry points.

---

## 2. File Touch List

### Created (new files only)

| File | Purpose |
|---|---|
| `redseek/__init__.py` | Package marker; `__version__ = "0.1.0"` |
| `redseek/models.py` | `@dataclass` definitions for Post, Target, IngestState |
| `redseek/store.py` | Sidecar DB init, schema guard, CRUD, wipe |
| `shared/tests/test_redseek_store.py` | Unit tests for store behavior (required for Gate A8) |

### Not modified in Card 1

| File | Why untouched |
|---|---|
| `shared/config.py` | No config accessor needed yet; path hardcoded in store.py |
| `shared/db_migrations.py` | Sidecar has its own init logic; never called by main migration runner |
| `tools/db_schema.sql` | Main DB schema only; sidecar schema lives in `store.py` |
| `gui/` (all) | No GUI wiring in Card 1 |
| Any other existing `.py` | Zero changes |

---

## 3. Step-by-Step Implementation Plan

### Step 1 — `redseek/__init__.py`

```python
__version__ = "0.1.0"
```

Minimal. No imports. Package marker only.

---

### Step 2 — `redseek/models.py`

Three `@dataclass` classes. Stdlib only (`dataclasses`, `typing`). No inheritance.

**`RedditPost`**
```
post_id: str
post_title: str
post_author: Optional[str]
post_created_utc: float
is_nsfw: int           # 0 or 1
had_targets: int       # 0 or 1
source_sort: str       # "new" or "top"
last_seen_at: str      # UTC datetime string
```

**`RedditTarget`**
```
id: Optional[int]           # None before insert (AUTOINCREMENT)
post_id: str
target_raw: str
target_normalized: str
host: Optional[str]
protocol: Optional[str]     # "http", "https", "ftp", "unknown"
notes: Optional[str]
parse_confidence: Optional[str]  # "high", "medium", "low"
created_at: str
dedupe_key: str             # sha1(post_id + "|" + target_normalized)
```

**`RedditIngestState`**
```
subreddit: str
sort_mode: str              # "new" or "top"
last_post_created_utc: Optional[float]
last_post_id: Optional[str]
last_scrape_time: Optional[str]
```

---

### Step 3 — `redseek/store.py`

#### 3a. Path resolution

```python
_SIDECAR_DEFAULT = Path.home() / ".dirracuda" / "reddit_od.db"

def get_db_path(override: Optional[Path] = None) -> Path:
    return override if override is not None else _SIDECAR_DEFAULT
```

`override` enables test injection without monkeypatching globals.

#### 3b. Schema DDL (module-level constants)

Three `CREATE TABLE IF NOT EXISTS` strings. All column lists explicit.

`reddit_targets` has `FOREIGN KEY (post_id) REFERENCES reddit_posts(post_id)`.

`reddit_targets.dedupe_key TEXT NOT NULL UNIQUE`.

`reddit_ingest_state` primary key: `PRIMARY KEY (subreddit, sort_mode)` — composite.

`reddit_targets.id` uses `INTEGER PRIMARY KEY AUTOINCREMENT`.

#### 3c. `init_db(path=None)`

```
1. Resolve path via get_db_path(path)
2. path.parent.mkdir(parents=True, exist_ok=True)
3. with sqlite3.connect(str(path)) as conn:
   a. PRAGMA journal_mode=WAL
   b. PRAGMA foreign_keys=ON
   c. CREATE TABLE IF NOT EXISTS reddit_posts (...)
   d. CREATE TABLE IF NOT EXISTS reddit_targets (...)
   e. CREATE TABLE IF NOT EXISTS reddit_ingest_state (...)
   f. conn.commit()
```

Idempotent. Safe to call repeatedly.

#### 3d. `_check_schema(conn)`  ← REVISED: adds constraint validation

Called inside `open_connection()` before any data op. Three layers of checks:

**Layer 1 — Column presence** (unchanged):
```python
REQUIRED_COLUMNS = {
    "reddit_posts": {
        "post_id", "post_title", "post_author", "post_created_utc",
        "is_nsfw", "had_targets", "source_sort", "last_seen_at"
    },
    "reddit_targets": {
        "id", "post_id", "target_raw", "target_normalized",
        "host", "protocol", "notes", "parse_confidence",
        "created_at", "dedupe_key"
    },
    "reddit_ingest_state": {
        "subreddit", "sort_mode", "last_post_created_utc",
        "last_post_id", "last_scrape_time"
    },
}
for table, required_cols in REQUIRED_COLUMNS.items():
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    missing = required_cols - present
    if missing:
        raise RuntimeError(f"sidecar schema: {table} missing columns {missing}")
```

**Layer 2 — UNIQUE constraint on `dedupe_key`**:
```python
# Find a unique index on reddit_targets that covers exactly ['dedupe_key']
found_unique = False
for row in conn.execute("PRAGMA index_list(reddit_targets)"):
    idx_name, is_unique = row[1], row[2]
    if is_unique:
        idx_cols = [r[2] for r in conn.execute(f"PRAGMA index_info({idx_name})")]
        if idx_cols == ["dedupe_key"]:
            found_unique = True
            break
if not found_unique:
    raise RuntimeError("sidecar schema: reddit_targets missing UNIQUE constraint on dedupe_key")
```

**Layer 3 — Composite PK on `reddit_ingest_state`** (order-sensitive):
```python
# PRAGMA table_info pk column: 0 = not in PK, 1+ = 1-based position in composite PK
# Sort by pk position to get declared order; assert exact tuple, not just set,
# so that a reversed PK (sort_mode, subreddit) is caught as a schema error.
pk_cols = tuple(
    row[1]  # column name
    for row in sorted(
        (r for r in conn.execute("PRAGMA table_info(reddit_ingest_state)") if r[5] > 0),
        key=lambda r: r[5]  # sort by pk position ascending
    )
)
if pk_cols != ("subreddit", "sort_mode"):
    raise RuntimeError(
        f"sidecar schema: reddit_ingest_state PK must be (subreddit, sort_mode), got {pk_cols}"
    )
```

**Layer 4 — FK on `reddit_targets.post_id → reddit_posts(post_id)`**:
```python
fk_list = [
    (row[2], row[3], row[4])  # (ref_table, from_col, to_col)
    for row in conn.execute("PRAGMA foreign_key_list(reddit_targets)")
]
if ("reddit_posts", "post_id", "post_id") not in fk_list:
    raise RuntimeError(
        "sidecar schema: reddit_targets missing FK post_id -> reddit_posts(post_id)"
    )
```

#### 3e. `open_connection(path=None) -> sqlite3.Connection`

```
1. Resolve path
2. If path does not exist as a file:
   raise FileNotFoundError("reddit sidecar DB not found — call init_db() first")
3. conn = sqlite3.connect(str(path))
4. conn.row_factory = sqlite3.Row
5. conn.execute("PRAGMA foreign_keys=ON")
6. _check_schema(conn)
7. return conn
```

Caller owns close/context-manager lifecycle.

#### 3f. `upsert_post(conn, post: RedditPost) -> None`  ← EXPLICIT POLICY LOCKED

**Upsert field policy:**

| Field | On conflict | Rationale |
|---|---|---|
| `post_id` | key — never updated | PK |
| `post_title` | **immutable** (first-seen) | Reddit titles can be edited; we preserve original ingest state |
| `post_author` | **immutable** (first-seen) | Accounts can be renamed/deleted; preserve first-seen attribution |
| `post_created_utc` | **immutable** (first-seen) | Never changes on Reddit |
| `source_sort` | **immutable** (first-seen) | Records which feed first found this post; `new` stays `new` if later seen via `top` |
| `is_nsfw` | **mutable** (update to latest) | Community NSFW tags change; analyst needs current state |
| `had_targets` | **mutable** (update to latest) | May improve as parser evolves across runs |
| `last_seen_at` | **mutable** (update to latest) | Always reflect most recent observation |

```sql
INSERT INTO reddit_posts
    (post_id, post_title, post_author, post_created_utc,
     is_nsfw, had_targets, source_sort, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(post_id) DO UPDATE SET
    is_nsfw      = excluded.is_nsfw,
    had_targets  = excluded.had_targets,
    last_seen_at = excluded.last_seen_at
```

#### 3g. `upsert_targets(conn, targets: list[RedditTarget]) -> None`

```sql
INSERT OR IGNORE INTO reddit_targets
    (post_id, target_raw, target_normalized, host, protocol,
     notes, parse_confidence, created_at, dedupe_key)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

`id` omitted — AUTOINCREMENT assigns it. Batched `executemany`. Silently skips on `dedupe_key` UNIQUE conflict.

#### 3h. `get_ingest_state(conn, subreddit: str, sort_mode: str) -> Optional[RedditIngestState]`

```sql
SELECT subreddit, sort_mode, last_post_created_utc, last_post_id, last_scrape_time
FROM reddit_ingest_state
WHERE subreddit = ? AND sort_mode = ?
```

Returns `None` if no row found.

#### 3i. `save_ingest_state(conn, state: RedditIngestState) -> None`

```sql
INSERT INTO reddit_ingest_state
    (subreddit, sort_mode, last_post_created_utc, last_post_id, last_scrape_time)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(subreddit, sort_mode) DO UPDATE SET
    last_post_created_utc = excluded.last_post_created_utc,
    last_post_id          = excluded.last_post_id,
    last_scrape_time      = excluded.last_scrape_time
```

#### 3j. `wipe_all(path=None) -> None`

Full wipe — deletes all rows from all three tables. Preserves schema for immediate reuse.

```
1. Resolve path
2. init_db(path)          ← ensures tables exist even on first-run replace_cache
3. with sqlite3.connect(str(path)) as conn:
   a. PRAGMA foreign_keys=ON
   b. DELETE FROM reddit_targets       ← child table first (FK-safe order)
   c. DELETE FROM reddit_posts
   d. DELETE FROM reddit_ingest_state
   e. conn.commit()
```

### Step 4 — `shared/tests/test_redseek_store.py`

Required test cases (using `tmp_path` fixture; no `conftest.py`):

| Test | What it asserts |
|---|---|
| `test_init_db_idempotent` | Two calls to `init_db` succeed; all 3 tables present |
| `test_wipe_all_fresh_db` | `wipe_all` on never-initialized path succeeds; schema present afterward |
| `test_wipe_all_clears_rows_preserves_schema` | Populate all tables, wipe, verify 0 rows in each, tables still present |
| `test_upsert_post_no_cascade_delete` | Insert post + target; re-upsert post; target row still present |
| `test_upsert_post_immutable_fields` | Insert post with title/author/source_sort; re-upsert with changed values; originals preserved |
| `test_upsert_post_mutable_fields` | Insert post with `is_nsfw=0`; re-upsert with `is_nsfw=1`; verify updated to 1 |
| `test_check_schema_raises_on_missing_column` | Manually create table with missing column; call `_check_schema`; assert `RuntimeError` |
| `test_check_schema_raises_on_missing_unique` | Create table without UNIQUE on dedupe_key; assert `RuntimeError` |
| `test_check_schema_raises_on_missing_pk` | Create state table with single-col PK; assert `RuntimeError` |
| `test_check_schema_raises_on_reversed_pk` | Create state table with `PRIMARY KEY (sort_mode, subreddit)` (reversed); assert `RuntimeError` |
| `test_check_schema_raises_on_missing_fk` | Create targets table without FK; assert `RuntimeError` |

---

## 4. Risks, Blockers, Bad Assumptions, Shortcuts to Avoid

### Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | `~/.dirracuda/` doesn't exist on first run | Low | `mkdir(parents=True, exist_ok=True)` in `init_db` |
| R2 | Importing `redseek.store` triggers `shared` imports | Medium | Keep `store.py` stdlib-only; verified by Gate A2 |
| R3 | `wipe_all` called before any init on fresh install | Low | `init_db(path)` at top of `wipe_all` |
| R4 | Schema mismatch SPEC vs architecture doc | Resolved | Architecture doc wins: composite PK, `source_sort` on posts |
| R5 | `init_db` called multiple times | Low | `IF NOT EXISTS` idempotent |
| R6 | `open_connection` called before `init_db` | Medium | Explicit `FileNotFoundError` with actionable message |
| R7 | `upsert_post` removes child targets via cascade | Resolved | `ON CONFLICT DO UPDATE` never deletes |
| R8 | Silent schema drift not caught | Resolved | `_check_schema` validates columns + UNIQUE + composite PK + FK |
| R9 | `source_sort` overwritten when post seen in both feeds | Resolved | `source_sort` is immutable-first-seen; not in DO UPDATE SET |
| R10 | `is_nsfw` becomes stale from first ingest | Resolved | `is_nsfw` is mutable; updated on every re-ingest |

### Bad Assumptions to Avoid

- `shared/db_migrations.py::run_migrations()` must never be called for the sidecar.
- No `conftest.py` exists — all test fixtures must be per-file using `tmp_path`.
- `PRAGMA index_list` returns indexes in arbitrary order — do not rely on position; match by column content.
- `PRAGMA table_info` pk column: 0 = not in PK, 1+ = 1-based position in composite PK. Do not treat as boolean.

### Shortcuts to Avoid

- No `tempfile.mktemp` — use `tmp_path` fixture.
- No bare `INSERT ... VALUES (...)` without column list.
- No `INSERT OR REPLACE` for any table that has child FK rows.
- Do not import `redseek` from any existing module.
- Do not call `_check_schema` inside `wipe_all` — wipe is valid even on a schema that hasn't been fully initialized yet (that's why `init_db` is called first, not `open_connection`).

---

## 5. Validation Commands and Expected Outcomes

**Pre-requisite**: Before any coding starts, record the baseline regression count:
```bash
./venv/bin/python -m pytest -q \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_scan_manager_config_path.py \
  gui/tests/test_backend_interface_commands.py \
  --tb=no 2>&1 | grep -oE '[0-9]+ (passed|failed|error)'
```
Record only the pass/fail counts — not elapsed time, which is nondeterministic. Example baseline: `14 passed`. Gate A9 compares against this string only. If headless, prefix with `xvfb-run -a`.

---

### Gate A1 — Syntax check

```bash
./venv/bin/python -m py_compile \
  redseek/__init__.py redseek/models.py redseek/store.py \
  shared/tests/test_redseek_store.py
```

**PASS**: No output, exit 0.  
**FAIL**: Syntax error with line number.

---

### Gate A2 — Isolated import (no shared lib side effects)

```bash
./venv/bin/python -c "import redseek.store; print('import OK')"
```

**PASS**: Prints `import OK`.  
**FAIL**: Any ImportError or unexpected output.

---

### Gate A3 — `init_db` idempotency + correct tables

```bash
./venv/bin/python -c "
import tempfile, pathlib, sqlite3
from redseek.store import init_db

REQUIRED = {'reddit_posts', 'reddit_targets', 'reddit_ingest_state'}

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'test_reddit.db'
    init_db(path)
    init_db(path)
    conn = sqlite3.connect(str(path))
    tables = {r[0] for r in conn.execute(
        \"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\"
    ).fetchall()}
    conn.close()
    assert REQUIRED <= tables, f'missing: {REQUIRED - tables}'
    print('PASS: init idempotent, all 3 tables present')
"
```

**PASS**: Prints `PASS: init idempotent, all 3 tables present`.  
**FAIL**: Assertion or exception.

---

### Gate A4 — `wipe_all` on fresh DB (first-run replace_cache)

```bash
./venv/bin/python -c "
import tempfile, pathlib, sqlite3
from redseek.store import wipe_all

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'test_reddit.db'
    wipe_all(path)   # no prior init_db call
    conn = sqlite3.connect(str(path))
    tables = {r[0] for r in conn.execute(
        \"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\"
    ).fetchall()}
    conn.close()
    assert {'reddit_posts', 'reddit_targets', 'reddit_ingest_state'} <= tables
    print('PASS: wipe_all safe on fresh DB, schema present')
"
```

**PASS**: Prints `PASS: wipe_all safe on fresh DB, schema present`.  
**FAIL**: Exception (especially `no such table`).

---

### Gate A5 — `upsert_post` does not cascade-delete child targets

```bash
./venv/bin/python -c "
import tempfile, pathlib, datetime
from redseek.store import init_db, open_connection, upsert_post, upsert_targets
from redseek.models import RedditPost, RedditTarget

now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'test_reddit.db'
    init_db(path)
    with open_connection(path) as conn:
        post = RedditPost('p1', 'Test Post', 'user', 1700000000.0, 0, 1, 'new', now)
        upsert_post(conn, post)
        target = RedditTarget(None, 'p1', 'http://x.com', 'http://x.com',
                              'x.com', 'http', None, 'high', now, 'key1')
        upsert_targets(conn, [target])
        upsert_post(conn, RedditPost('p1', 'Test Post', 'user', 1700000000.0, 0, 1, 'new', now))
        count = conn.execute(
            'SELECT COUNT(*) FROM reddit_targets WHERE post_id=?', ('p1',)
        ).fetchone()[0]
        conn.commit()
    assert count == 1, f'expected 1 target, got {count}'
    print('PASS: upsert_post does not delete child targets')
"
```

**PASS**: Prints `PASS: upsert_post does not delete child targets`.  
**FAIL**: `count == 0` — FK cascade delete still occurring.

---

### Gate A6 — `upsert_post` field policy (immutable + mutable)

```bash
./venv/bin/python -c "
import tempfile, pathlib, datetime
from redseek.store import init_db, open_connection, upsert_post
from redseek.models import RedditPost

now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'test_reddit.db'
    init_db(path)
    with open_connection(path) as conn:
        conn.execute('BEGIN')
        upsert_post(conn, RedditPost('p1', 'Original Title', 'alice', 1700000000.0, 0, 0, 'new', now))
        upsert_post(conn, RedditPost('p1', 'Changed Title', 'bob', 1700000000.0, 1, 1, 'top', now))
        row = conn.execute('SELECT * FROM reddit_posts WHERE post_id=?', ('p1',)).fetchone()
        conn.execute('ROLLBACK')

    # Immutable fields must not change
    assert row['post_title'] == 'Original Title', f'title changed: {row[\"post_title\"]}'
    assert row['post_author'] == 'alice', f'author changed: {row[\"post_author\"]}'
    assert row['source_sort'] == 'new', f'source_sort changed: {row[\"source_sort\"]}'
    # Mutable fields must update
    assert row['is_nsfw'] == 1, f'is_nsfw not updated: {row[\"is_nsfw\"]}'
    assert row['had_targets'] == 1, f'had_targets not updated: {row[\"had_targets\"]}'
    print('PASS: immutable fields preserved, mutable fields updated')
"
```

**PASS**: Prints `PASS: immutable fields preserved, mutable fields updated`.  
**FAIL**: Any assertion — policy not implemented correctly.

---

### Gate A7 — Schema column + constraint verification

```bash
./venv/bin/python -c "
import tempfile, pathlib, sqlite3
from redseek.store import init_db

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'test_reddit.db'
    init_db(path)
    conn = sqlite3.connect(str(path))
    conn.execute('PRAGMA foreign_keys=ON')
    for table in ('reddit_posts', 'reddit_targets', 'reddit_ingest_state'):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
        print(f'{table} columns: {cols}')
    # Verify dedupe_key UNIQUE
    idx = [(r[1], r[2]) for r in conn.execute('PRAGMA index_list(reddit_targets)')]
    print(f'reddit_targets indexes: {idx}')
    # Verify composite PK
    pk = [(r[1], r[5]) for r in conn.execute('PRAGMA table_info(reddit_ingest_state)') if r[5] > 0]
    print(f'reddit_ingest_state PK cols: {pk}')
    # Verify FK
    fk = [(r[2], r[3], r[4]) for r in conn.execute('PRAGMA foreign_key_list(reddit_targets)')]
    print(f'reddit_targets FKs: {fk}')
    conn.close()
"
```

**PASS**: Manually verify columns match architecture doc; dedupe_key index is UNIQUE; PK includes both subreddit+sort_mode; FK shows reddit_posts/post_id/post_id.  
**FAIL**: Missing entry or empty output.

---

### Gate A8 — Targeted redseek unit tests (must collect ≥1 test)

```bash
./venv/bin/python -m pytest -v shared/tests/test_redseek_store.py
```

**PASS**: All tests in `test_redseek_store.py` pass; collection count ≥ 1.  
**FAIL**: Any test failure, or `collected 0 items` (which means the test file is missing or empty — a hard block).

---

### Gate A9 — Fixed regression set (compare to recorded baseline)

If running in a headless/CI environment without a display, prefix both the baseline recording and this command with `xvfb-run -a` — these tests exercise Tkinter GUI code and will error on `_tkinter.TclError: no display` otherwise.

```bash
./venv/bin/python -m pytest -q \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_scan_manager_config_path.py \
  gui/tests/test_backend_interface_commands.py \
  --tb=no 2>&1 | grep -oE '[0-9]+ (passed|failed|error)'
```

**PASS**: Output matches the baseline count string recorded before Card 1 coding started (e.g., `14 passed`). Elapsed time is excluded — it is nondeterministic.  
**FAIL**: Count string differs from baseline, or collection error.

---

### Gate B1 (Manual) — Existing app startup unaffected

```bash
./dirracuda &
```

**PASS**: Dashboard loads normally, no traceback.  
**FAIL**: Any error on startup.

---

## 6. Implementation-Time Guardrails

These are not blockers but must be honored during coding:

**Transaction ownership**: `upsert_post`, `upsert_targets`, `save_ingest_state`, `get_ingest_state` must never auto-commit. They execute SQL on a caller-supplied connection; the caller owns `BEGIN`/`commit`/`rollback`. Only `init_db` and `wipe_all` manage their own transactions (they own the connection lifecycle). Tests depend on this to inspect state mid-transaction before rolling back.

**Gate A9 comparison robustness**: `grep -oE '[0-9]+ (passed|failed|error)'` may emit multiple tokens (e.g., `14 passed` then `1 warning`) if pytest emits extras. Compare the `passed`/`failed`/`error` tokens explicitly, not raw line equality, to avoid string-order noise from warnings.

**xvfb-run consistency**: Use `xvfb-run -a` for both the baseline recording command and Gate A9 in headless runs. Mixing (one with, one without) produces a false mismatch even when no regressions exist.

## 7. Open Questions

None. All design decisions resolved by locked decisions + architecture doc.
