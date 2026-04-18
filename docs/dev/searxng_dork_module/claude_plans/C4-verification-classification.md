# C4 — Verification + Classification + Mandatory Promotion

## Context

C1–C3 delivered the SearXNG dork tab UI, preflight, search service, and sidecar store.
The sidecar schema already has `verdict`, `reason_code`, `http_status`, `checked_at` on
`dork_results` and `verified_count` on `dork_runs` — pre-designed for C4.

C4 adds three layers:
1. **Classifier** — reuses `commands/http/verifier.py` to triage each URL.
2. **Service + store integration** — wires classification into `run_dork_search` and persists results.
3. **Results browser + mandatory promotion** — minimal browser window with "Add to dirracuda DB" callback, following the exact Reddit browser / dashboard_experimental pattern.

Promotion is a C4 acceptance requirement: the code path must exist, be tested, and fire when a live Server List window is open. The C4 spec explicitly defines the fallback: "If callback unavailable, show explicit 'Not available' message." The "Not available" path when no Server List window is live is therefore **acceptable per spec** — promotion is mandatory in the sense that it is wired and tested, not that it succeeds unconditionally.

The promotion chain runs:
> dashboard_experimental (closure) → open_se_dork_results_db → se_dork browser window → server_window.open_add_record_dialog

---

## File inventory

| File | Action | Current lines |
|------|--------|---------------|
| `experimental/se_dork/classifier.py` | CREATE | — |
| `experimental/se_dork/models.py` | EDIT | 57 |
| `experimental/se_dork/store.py` | EDIT | 265 |
| `experimental/se_dork/service.py` | EDIT | 177 |
| `gui/components/se_dork_browser_window.py` | CREATE | — |
| `gui/components/experimental_features/se_dork_tab.py` | EDIT | 267 |
| `gui/components/dashboard_experimental.py` | EDIT | 98 |
| `shared/tests/test_se_dork_classifier.py` | CREATE | — |
| `shared/tests/test_se_dork_service.py` | EXTEND | 226 |
| `shared/tests/test_se_dork_store.py` | EXTEND | 244 |
| `gui/tests/test_se_dork_browser_window.py` | CREATE | — |
| `gui/tests/test_experimental_features_dialog.py` | EXTEND | ~257 |

`gui/dashboard/widget.py` is 1801 lines (poor, above 1700 stop-and-plan threshold) — **not touched**. The callback is wired via closure in `dashboard_experimental.py` instead (see Step 7).

---

## Step-by-step implementation

### 1. `experimental/se_dork/classifier.py` (new, ~100 lines)

Define verdict constants and one public function:

```python
VERDICT_OPEN_INDEX = "OPEN_INDEX"
VERDICT_MAYBE      = "MAYBE"
VERDICT_NOISE      = "NOISE"
VERDICT_ERROR      = "ERROR"

@dataclass
class ClassifyResult:
    verdict: str
    reason_code: Optional[str]   # None for OPEN_INDEX only
    http_status: Optional[int]   # None on network-level failure

def classify_url(url: str, timeout: float = 10.0) -> ClassifyResult:
```

**Logic:**
1. Wrap `urllib.parse.urlparse(url)` in try/except — any exception (e.g. `url=None`) → `ERROR / parse_error`.
2. Extract `scheme`, `hostname`, `port`, `path`. Port defaults: `{"http": 80, "https": 443}`.
   - Missing hostname → `ERROR / no_host`
   - Non-http/https scheme → `NOISE / unsupported_scheme`
3. Call `try_http_request(ip=hostname, port=port, scheme=scheme, path=path, request_host=hostname, timeout=timeout)`.
4. Map outcome:
   - `reason != ""` (network failure) → `ERROR / <reason>` e.g. `timeout`, `dns_fail`, `connect_fail`
   - `validate_index_page(body, status_code)` → `OPEN_INDEX / None`
   - `status_code == 200` → `MAYBE / no_index_tag`
   - `status_code >= 400` → `NOISE / http_<status_code>`
   - other (redirect codes, etc.) → `MAYBE / http_<status_code>`

Reuses:
- `commands/http/verifier.py::try_http_request` (line 46)
- `commands/http/verifier.py::validate_index_page` (line 153)

**parse_error note:** `urlparse()` does not raise for malformed strings, and `urlparse(None)` returns a `ParseResultBytes` (no exception). To make `parse_error` deterministic and testable, add an explicit `isinstance` guard **before** calling `urlparse`:

```python
if not isinstance(url, str):
    return ClassifyResult(verdict=VERDICT_ERROR, reason_code="parse_error", http_status=None)
```

The subsequent `try/except` around `urlparse` is retained as a deep-defense catch-all but is not the primary mechanism. Test uses `classify_url(123)` (integer input) to reliably trigger this path.

---

### 2. `experimental/se_dork/models.py` (edit, +1 line)

Add `verified_count: int = 0` to `RunResult` dataclass. Default 0 is backward-compatible with all existing tests and callers.

---

### 3. `experimental/se_dork/store.py` (edit, +~55 lines)

Add four new functions (caller owns connection and commits):

```python
def get_pending_results(conn, run_id: int) -> List[dict]:
    """Return unclassified rows for a run (verdict IS NULL). Columns: result_id, url."""

def update_result_verdict(conn, result_id, verdict, reason_code, http_status, checked_at):
    """Write verdict/reason_code/http_status/checked_at on a single result row."""

def update_run_verified_count(conn, run_id: int, verified_count: int):
    """Update verified_count on the run row."""

def get_all_results(conn) -> List[dict]:
    """Return all result rows for browser display (all runs), newest run_id first.
    Dict keys match DB column names exactly:
    result_id, run_id, url, title, verdict, reason_code, http_status, checked_at."""
```

Do NOT change the existing `update_run` signature (callers in service.py and tests depend on it).

**Fix: `open_connection()` must set `row_factory` for dict-returning read APIs**

`get_pending_results()` and `get_all_results()` return `List[dict]` and the browser window accesses rows by key. Without `conn.row_factory = sqlite3.Row`, sqlite3 returns tuples, breaking key access at runtime. Add to `open_connection()` after `conn.execute("PRAGMA foreign_keys=ON")`:

```python
conn.row_factory = sqlite3.Row
```

`sqlite3.Row` supports both positional and key access, so existing write-path callers (`insert_run`, `update_run`, `insert_result`) that use only `cur.lastrowid` / `cur.rowcount` are unaffected. The new read functions convert to plain dicts via `[dict(row) for row in cursor]` before returning.

**Strengthen `_check_schema` (integrity guards, not just column presence):**

Extend the existing `_check_schema` function to also verify:

1. `UNIQUE (run_id, url_normalized)` exists on `dork_results` — **exact** two-column match (subset `<=` is too permissive; an index with extra columns weakens dedupe):
```python
indexes = {row[1]: (row[2], row[3]) for row in conn.execute("PRAGMA index_list('dork_results')")}
unique_ok = any(
    is_unique and origin in ('u', 'c') and  # 'u' = UNIQUE constraint, 'c' = CREATE UNIQUE INDEX
    {r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")} == {'run_id', 'url_normalized'}
    for name, (is_unique, origin) in indexes.items()
)
if not unique_ok:
    raise RuntimeError("se_dork sidecar schema: dork_results missing UNIQUE(run_id, url_normalized)")
```

2. FK `dork_results.run_id → dork_runs(run_id)` exists — verify both child column (`from`) and parent column (`to`):
```python
# PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
fk_ok = any(
    row[2] == 'dork_runs' and row[3] == 'run_id' and row[4] == 'run_id'
    for row in conn.execute("PRAGMA foreign_key_list('dork_results')")
)
if not fk_ok:
    raise RuntimeError("se_dork sidecar schema: dork_results missing FK run_id → dork_runs(run_id)")
```

This ensures that `insert_result`'s dedupe (which relies on the UNIQUE constraint) and classification writes are not silently broken by schema drift. `_check_schema` is called from `open_connection`, so all callers are covered.

---

### 4. `experimental/se_dork/service.py` (edit, +~50 lines)

**Fix: COMMIT 1 exception risk + max_results clamping drift — single canonical snippet**

Both are applied together (clamping must happen before insert, so they compose naturally):

```python
max_results = max(1, min(500, options.max_results))
clamped_opts = RunOptions(
    instance_url=options.instance_url,
    query=options.query,
    max_results=max_results,
)
started_at = _utcnow()
try:
    run_id = insert_run(conn, clamped_opts, started_at)
    conn.commit()  # COMMIT 1
except Exception as exc:
    conn.close()
    return RunResult(run_id=None, fetched_count=0, deduped_count=0,
                     status=RUN_STATUS_ERROR, error=f"Run insert failed: {exc}")
```

`clamped_opts` is used for both `insert_run` (so `dork_runs.max_results` reflects the clamped value) and the subsequent fetch cap. Remove the now-superseded `**Fix: max_results clamping drift**` paragraph below.

**Add private helper:**
```python
def _classify_run_results(
    run_id: int,
    db_path: Optional[Path],
    timeout: float = 10.0,
) -> int:
    """
    Open a fresh connection, classify all pending results for run_id,
    write verdicts, update verified_count, commit, close.
    Returns number of rows classified. Never raises — returns 0 on any error.
    """
    from experimental.se_dork.classifier import classify_url
    try:
        conn = open_connection(db_path)
        try:
            rows = get_pending_results(conn, run_id)
            checked_at = _utcnow()
            verified = 0
            for row in rows:
                result = classify_url(row["url"], timeout=timeout)
                update_result_verdict(conn, row["result_id"], result.verdict,
                                      result.reason_code, result.http_status, checked_at)
                verified += 1
            update_run_verified_count(conn, run_id, verified)
            conn.commit()
            return verified
        finally:
            conn.close()
    except Exception:
        return 0
```

The outer `try/except Exception: return 0` covers `open_connection` failures, per-row errors, and commit failures — making "never raises" actually true. The caller's `try/except` around `_classify_run_results` is kept for defense-in-depth but is now redundant.

**Extend `run_dork_search`:**
After COMMIT 2 (inside the `try` block), before `return RunResult(...)`:
```python
# Phase 3: classify (best-effort — failure does not fail the run)
verified_count = 0
try:
    verified_count = _classify_run_results(run_id, db_path)
except Exception:
    pass

return RunResult(
    run_id=run_id, fetched_count=fetched, deduped_count=deduped,
    verified_count=verified_count, status=RUN_STATUS_DONE, error=None,
)
```

Transaction invariants: COMMIT 1 and COMMIT 2 are unchanged. Classification is COMMIT 3 inside `_classify_run_results`.

---

### 5. `gui/components/se_dork_browser_window.py` (new, ~360 lines)

Mirrors `gui/components/reddit_browser_window.py` (616 lines) but simpler.

**Class:** `SeDorkBrowserWindow(parent, db_path=None, add_record_callback=None)`

**Treeview columns (column IDs match `get_all_results()` dict keys):**
`url`, `verdict`, `reason_code`, `http_status`, `checked_at`
Display headers: `URL`, `Verdict`, `Reason`, `Status`, `Checked`
Widths: url=500, verdict=110, reason_code=140, http_status=70, checked_at=150

**Context menu (right-click):**
- `Copy URL`
- `Open in system browser`
- separator
- `Add to dirracuda DB`

**`_build_prefill(row: dict) -> Optional[dict]`:**
```python
parsed = urlparse(row["url"])
scheme = parsed.scheme.lower()
if scheme not in ("http", "https"):
    return None
hostname = parsed.hostname or ""
if not hostname:           # reject empty host — would push invalid payload into Add Record
    return None
port = parsed.port or (443 if scheme == "https" else 80)
return {
    "host_type": "H",
    "host": hostname,
    "port": port,
    "scheme": scheme,
    "_probe_host_hint": hostname,
    "_probe_path_hint": parsed.path or "/",
    "_promotion_source": "se_dork_browser",
}
```

**`_resolve_prefill_host_ipv4(prefill) -> tuple[str, bool]`:**
Copied verbatim from `reddit_browser_window.py::_resolve_prefill_host_ipv4` (lines 539–569). Resolves hostname to IPv4; returns `(resolved_host, was_resolved)`.

**`_on_add_to_db()`:**
1. If `self._add_record_callback is None` → `safe_messagebox.showinfo("Not available", "Open this window from the Servers window to use 'Add to dirracuda DB'.", parent=self.window)`; return.
2. Get selected row; call `_build_prefill(row)` → None → showinfo about unsupported scheme; return.
3. Call `_resolve_prefill_host_ipv4(prefill)`.
4. If resolution failed and host is not a valid IPv4 → `safe_messagebox.showwarning(...)`.
5. `prefill["host"] = resolved_host`
6. `self._add_record_callback(prefill)`

Loads rows from `store.get_all_results(conn)` on `__init__`. Uses `gui.utils.safe_messagebox` for all dialogs.

**Factory:**
```python
def show_se_dork_browser_window(parent, db_path=None, add_record_callback=None):
    SeDorkBrowserWindow(parent, db_path=db_path, add_record_callback=add_record_callback)
```

---

### 6. `gui/components/experimental_features/se_dork_tab.py` (edit, +~10 lines)

Change `command=lambda: None` on "Open Results DB" button to `command=self._open_results_browser`.

Add method:
```python
def _open_results_browser(self) -> None:
    cb = self._context.get("open_se_dork_results_db")
    if cb is not None:
        cb()
    else:
        # Wiring absent — open browser without callback so "Not available"
        # message is shown if the user attempts promotion. Visible failure
        # is preferable to a silent no-op for a mandatory promotion path.
        from gui.components.se_dork_browser_window import show_se_dork_browser_window
        show_se_dork_browser_window(self.frame, add_record_callback=None)
```

The `open_se_dork_results_db` function resolves the server window and passes `add_record_callback` when available. If it is absent from context (e.g. tab opened in an unhosted test context), the fallback opens the browser with `add_record_callback=None`, which surfaces "Not available" to the user rather than silently doing nothing.

---

### 7. `gui/components/dashboard_experimental.py` (edit, +~22 lines)

Add a **module-level import** (same as `show_reddit_browser_window` at line 14 — consistent patching seam, testable as `gui.components.dashboard_experimental.show_se_dork_browser_window`):

```python
from gui.components.se_dork_browser_window import show_se_dork_browser_window
```

Add module-level function (mirrors `open_reddit_post_db`):

```python
def open_se_dork_results_db(widget) -> None:
    """Open the SE Dork results browser, wiring add_record_callback when possible.

    Resolution order: same as open_reddit_post_db — single-pass _resolve_server_window().
    """
    server_window = _resolve_server_window(widget)
    if server_window is not None:
        show_se_dork_browser_window(
            parent=server_window.window,
            add_record_callback=server_window.open_add_record_dialog,
        )
    else:
        show_se_dork_browser_window(
            parent=widget.parent,
            add_record_callback=None,
        )
```

In `handle_experimental_button_click`, add the callback as a **closure** (no widget.py edit required — `widget.py` is 1801 lines and must not be touched):

```python
context = {
    "reddit_grab_callback": widget._handle_reddit_grab_button_click,
    "open_reddit_post_db": widget._open_reddit_post_db,
    "open_se_dork_results_db": lambda: open_se_dork_results_db(widget),  # closure
    "parent": widget.parent,
}
```

The se_dork tab calls `self._context.get("open_se_dork_results_db")()` — the lambda forwards to `open_se_dork_results_db(widget)`. No method needs to be added to `widget.py`.

---

## Tests

### `shared/tests/test_se_dork_store.py` (extend, +~40 lines)

Add schema-drift negative tests for the new `_check_schema` integrity guards. Each test manually creates a malformed schema (bypassing `init_db`) and asserts `open_connection` raises `RuntimeError`:

- `test_check_schema_raises_on_missing_unique_constraint(tmp_path)` — create `dork_results` **without** `UNIQUE (run_id, url_normalized)`; call `open_connection(db_path)` → expect `RuntimeError` with "UNIQUE" in message.
- `test_check_schema_raises_on_missing_fk(tmp_path)` — create `dork_results` **without** `FOREIGN KEY (run_id)`; call `open_connection(db_path)` → expect `RuntimeError` with "FK" or "foreign" in message (case-insensitive).

Construction pattern: `sqlite3.connect(db_path)`, execute DDL for `dork_runs` normally, then execute `CREATE TABLE dork_results (result_id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, url TEXT NOT NULL, url_normalized TEXT NOT NULL, ...)` omitting the constraint, commit, close. Then call `open_connection(db_path)` and assert `RuntimeError`.

---

### `shared/tests/test_se_dork_classifier.py` (new, ~150 lines)

Mocks: `commands.http.verifier.try_http_request` via `monkeypatch`.

Scenarios:
- `test_classify_open_index` — 200 + valid index HTML → `OPEN_INDEX`, reason_code=None
- `test_classify_maybe_200_no_index` — 200 + plain HTML → `MAYBE / no_index_tag`
- `test_classify_noise_404` — 404 → `NOISE / http_404`
- `test_classify_noise_unsupported_scheme` — `ftp://host` → `NOISE / unsupported_scheme` (no HTTP call)
- `test_classify_error_timeout` — reason=`timeout` → `ERROR / timeout`, http_status=None
- `test_classify_error_dns_fail` — reason=`dns_fail` → `ERROR / dns_fail`
- `test_classify_error_no_host` — `http:///path` (empty netloc) → `ERROR / no_host` (no HTTP call)
- `test_classify_error_parse_error` — `classify_url(123)` → `ERROR / parse_error` (non-str input triggers isinstance guard before urlparse; deterministic)
- `test_classify_port_default_http` — `http://host/` → verifier called with `port=80`
- `test_classify_port_default_https` — `https://host/` → verifier called with `port=443`
- `test_classify_port_explicit` — `http://host:8080/` → verifier called with `port=8080`
- `test_classify_redirect_code` — 301, empty reason → `MAYBE / http_301`
- `test_classify_url_calls_verifier_with_parsed_fields` — verify `ip=hostname`, `scheme`, `port`, `path`, `request_host` args passed correctly

### `shared/tests/test_se_dork_service.py` (extend, +~55 lines)

New tests appended:

- `test_run_dork_search_populates_verdicts(tmp_path)` — mock `_classify_run_results` to write fixture verdicts; assert at least one row has `verdict IS NOT NULL` in DB.
- `test_run_dork_search_updates_verified_count(tmp_path)` — assert `dork_runs.verified_count > 0` after successful run.
- `test_run_dork_search_classification_failure_does_not_fail_run(tmp_path)` — mock `_classify_run_results` to raise; assert `result.status == RUN_STATUS_DONE`, `result.verified_count == 0`.
- `test_run_dork_search_commit1_exception_returns_structured_error(tmp_path)` — mock `insert_run` to raise; assert `result.status == RUN_STATUS_ERROR`, `result.run_id is None` (validates the COMMIT 1 fix).
- `test_run_dork_search_persists_clamped_max_results(tmp_path)` — call with `max_results=9999`; assert `dork_runs.max_results == 500` in DB (validates clamping drift fix).

### `gui/tests/test_se_dork_browser_window.py` (new, ~180 lines)

Uses `SeDorkBrowserWindow.__new__()` pattern (no display required).

- `test_build_prefill_http_url` — HTTP URL with explicit port → `host_type="H"`, `scheme="http"`, correct `host`, `port`, hints, `_promotion_source="se_dork_browser"`
- `test_build_prefill_https_default_port` — HTTPS URL, no port → `port=443`, `scheme="https"`
- `test_build_prefill_unsupported_scheme` — `ftp://` URL → returns None
- `test_build_prefill_explicit_port` — `http://host:8080/path` → `port=8080`, `_probe_path_hint="/path"`
- `test_on_add_to_db_calls_callback_with_prefill` — mock selected row + `_resolve_prefill_host_ipv4`; assert callback invoked with expected prefill
- `test_on_add_to_db_no_callback_shows_not_available` — `add_record_callback=None` → `safe_messagebox.showinfo` called with "Not available"
- `test_on_add_to_db_unsupported_scheme_shows_message` — row with ftp URL → `showinfo` about unsupported
- `test_build_prefill_missing_hostname` — URL `http:///path` (empty netloc) → returns None (not an empty-host prefill dict)

### `gui/tests/test_experimental_features_dialog.py` (extend, +~60 lines)

Add `open_se_dork_results_db` coverage mirroring existing reddit tests:

- `test_open_se_dork_results_db_with_live_server_window` — live server window → `show_se_dork_browser_window` called with `add_record_callback=server_window.open_add_record_dialog`
- `test_open_se_dork_results_db_fallback_when_no_server_window` — getter=None → `add_record_callback=None`
- `test_open_se_dork_results_db_treats_dead_window_as_none` — `winfo_exists()=False` → fallback
- `test_open_se_dork_results_db_fallback_when_getter_raises` — getter raises → fallback (no exception propagates)
- `test_se_dork_tab_open_results_invokes_context_callback` — context has `open_se_dork_results_db` → callback called when `_open_results_browser()` runs
- `test_se_dork_tab_fallback_opens_browser_when_no_results_callback` — no key in context → `show_se_dork_browser_window` called with `add_record_callback=None` (not a silent no-op)

---

## Validation commands

```bash
# 1. Line counts BEFORE edits (baseline — run first, record for rubric)
wc -l experimental/se_dork/models.py \
       experimental/se_dork/store.py \
       experimental/se_dork/service.py \
       gui/components/experimental_features/se_dork_tab.py \
       gui/components/dashboard_experimental.py \
       shared/tests/test_se_dork_service.py \
       shared/tests/test_se_dork_store.py \
       gui/tests/test_experimental_features_dialog.py

# 2. Line counts AFTER edits (all touched + all new files; report rubric rating per file)
wc -l experimental/se_dork/classifier.py \
       experimental/se_dork/models.py \
       experimental/se_dork/store.py \
       experimental/se_dork/service.py \
       gui/components/se_dork_browser_window.py \
       gui/components/experimental_features/se_dork_tab.py \
       gui/components/dashboard_experimental.py \
       shared/tests/test_se_dork_classifier.py \
       shared/tests/test_se_dork_service.py \
       shared/tests/test_se_dork_store.py \
       gui/tests/test_se_dork_browser_window.py \
       gui/tests/test_experimental_features_dialog.py

# Rubric: <=1200 excellent | 1201-1500 good | 1501-1800 acceptable | 1801-2000 poor | >2000 unacceptable

# 3. Compile check (after edits)
./venv/bin/python -m py_compile \
  experimental/se_dork/classifier.py \
  experimental/se_dork/models.py \
  experimental/se_dork/store.py \
  experimental/se_dork/service.py \
  gui/components/se_dork_browser_window.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/components/dashboard_experimental.py

# 3. Tests
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_classifier.py \
  shared/tests/test_se_dork_service.py \
  shared/tests/test_se_dork_store.py \
  gui/tests/test_se_dork_browser_window.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  -q
```

`test_dashboard_reddit_wiring.py` is included as a regression guard for `dashboard_experimental.py` — editing that file for se_dork wiring could silently break existing reddit callback routing.

---

## Response format (post-implementation)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? yes — open results browser after a run, confirm "Add to dirracuda DB" context menu fires callback with `host_type="H"`, `_promotion_source="se_dork_browser"`.
- Line count rubric before/after per touched file
- No commit
