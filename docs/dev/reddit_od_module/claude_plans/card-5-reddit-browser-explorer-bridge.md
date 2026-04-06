# Card 5: Reddit Browser + Explorer Bridge — Implementation Plan

## Context
Cards 1–4 are complete. The redseek package (`models`, `store`, `client`, `parser`, `service`) is fully
implemented and tested. The dashboard has a working `Reddit Grab` button. Card 5 adds the analyst-facing
read side: a browser window for reviewing ingested targets, plus the explorer bridge to open them.

---

## 1. Constraint Summary

| Constraint | Notes |
|---|---|
| Sidecar DB only | All reads/writes touch `~/.dirracuda/reddit_od.db` via `redseek.store` only |
| No main DB touch | Zero reads/writes to `dirracuda.db` tables |
| No scan pipeline | No `scan_manager`, no subprocess launch, no backend CLI path |
| No CLI path | GUI only — no argparse, no new entry point |
| No auto-probe/extract | Every network action is user-triggered only |
| Surgical/reversible | No refactors of surrounding code; new files preferred over edits |
| No commit | Do not commit unless HI says `commit` |

**Protocol inference order (explorer_bridge):**
1. `target_normalized` starts with `http://`, `https://`, or `ftp://` → open directly
2. `protocol` field is set and not `"unknown"`, AND `host` is non-empty → construct `{protocol}://{host}`
3. `host` has explicit port `:80` → infer `http`
4. `host` has explicit port `:443` → infer `https`
5. `host` has explicit port `:21` → infer `ftp`
6. Still unresolved → show protocol-pick prompt; do not guess or probe

---

## 2. File Touch List

### New files
| File | Purpose |
|---|---|
| `redseek/explorer_bridge.py` | `_infer_url`, `_ask_protocol` prompt, `open_target` entry point |
| `gui/components/reddit_browser_window.py` | `RedditBrowserWindow` + `show_reddit_browser_window` factory |
| `shared/tests/test_redseek_explorer_bridge.py` | Unit tests for bridge inference logic |
| `gui/tests/test_reddit_browser_window.py` | Headless tests for browser window |

### Modified files (surgical edits only)
| File | Change |
|---|---|
| `gui/components/dashboard.py` | Add `reddit_browser_button`; import + wire `show_reddit_browser_window` |

---

## 3. Step-by-Step Implementation Plan

### Step 1 — `redseek/explorer_bridge.py`

**Public surface:**
```python
def _infer_url(target: RedditTarget) -> str | None        # pure, unit-testable
def _ask_protocol(parent: tk.Widget, target_str: str) -> str | None
def open_target(target: RedditTarget, parent: tk.Widget) -> None
```

**`_infer_url` logic:**
1. If `target.target_normalized` starts with `http://`, `https://`, or `ftp://` → return as-is.
2. Else if `target.protocol` is non-None, not `"unknown"`, **and `target.host` is non-empty** →
   return `f"{target.protocol}://{target.host}"`.
   (Guard required: missing host would produce `"http://"` which is malformed.)
3. Else parse port from `target.host`:
   - Only attempt if host is non-empty and contains exactly one `:` that isn't part of a bracket
     IPv6 address (simple guard: skip if `host` starts with `[`).
   - `port = int(host.rsplit(":", 1)[-1])` wrapped in try/except ValueError.
   - Port 80 → `"http://{host}"`, port 443 → `"https://{host}"`, port 21 → `"ftp://{host}"`.
4. Return `None` if nothing resolved.

**`_ask_protocol` logic:**
- `simpledialog.askstring("Select Protocol", f"Cannot infer protocol for:\n{target_str}\n\nEnter: http / https / ftp", parent=parent)`
- Return `None` on cancel or empty input.
- Strip whitespace and trailing `://`, lowercase, validate in `{"http", "https", "ftp"}`.
- Show `messagebox.showerror` and return `None` if invalid.

**`open_target` logic:**
1. Call `_infer_url(target)` → `url`.
2. If `None`: call `_ask_protocol(parent, target.target_normalized)` → `proto`.
   - If `None` (user cancelled) → return silently.
   - Construct `url = f"{proto}://{target.host or target.target_normalized}"`.
3. Call `webbrowser.open(url)`.

---

### Step 2 — `gui/components/reddit_browser_window.py`

**DoD requirement:** rows must sort and filter correctly (03-TASK_CARDS.md line 130).

#### 2a. Row identity — stable iid mapping

**Problem with `tree.index(sel[0])`:** once sort/filter reorders visible rows, visual index diverges
from `self._rows` list index, producing wrong record lookups.

**Solution:** use `iid = str(target_id)` (the DB primary key) as the Treeview item identifier, and
maintain `self._row_by_iid: dict[str, dict]` as the authoritative lookup. Selection always resolves
via `self._row_by_iid[sel[0]]` regardless of sort/filter state.

#### 2b. `COLUMN_KEY_MAP` — tree column → row dict key

Tree column IDs are short display names; row dicts use SQL column names. These must be mapped
explicitly. Define as a class-level constant used for both row rendering and sort key resolution:

```python
COLUMN_KEY_MAP = {
    "target": "target_normalized",
    "proto":  "protocol",
    "conf":   "parse_confidence",
    "author": "post_author",
    "nsfw":   "is_nsfw",
    "notes":  "notes",
    "date":   "created_at",
}
COLUMNS = list(COLUMN_KEY_MAP.keys())   # ordering used for Treeview columns and insert values
```

Row insertion uses `[row.get(COLUMN_KEY_MAP[c], "") or "" for c in COLUMNS]` as the `values` tuple,
ensuring column order is always consistent with the heading order.

#### 2c. Class state

```python
self.parent: tk.Widget
self.db_path: Optional[Path]
self.theme: SMBSeekTheme
self._row_by_iid: dict[str, dict]   # iid (str(id)) → row dict; source of truth
self._all_rows: list[dict]          # full unfiltered load; used for filter/resort
self._sort_col: Optional[str]       # tree column key (e.g. "target"), not dict key
self._sort_reverse: bool
```

#### 2d. `_build_window`
- Title: `"Reddit Post DB"`, geometry `"1000x500"`
- **Filter bar** above the tree: `tk.Label("Filter:")` + `tk.Entry` bound to `self._filter_var`
  (a `tk.StringVar`). Trace on write calls `_apply_filter_and_sort()`.
- `ttk.Treeview` with vertical `ttk.Scrollbar`, `show="headings"`, `selectmode="browse"`
- Columns: `COLUMNS` (from `COLUMN_KEY_MAP.keys()`)
  - Widths: target=280, proto=60, conf=55, author=90, nsfw=45, notes=160, date=140
  - Each `heading(col, text=COL_HEADERS[col], command=lambda c=col: self._on_sort(c))` for column-click sort
- `tk.StringVar` status label below tree
- Button row: `Open in Explorer`, `Open Reddit Post`, `Refresh`, `Clear DB`
  - Apply `theme.apply_to_widget(btn, "button_secondary")` to each

#### 2e. `_load_rows`
1. Clear `self._row_by_iid`, `self._all_rows`, and tree.
2. Catch explicit operational errors from DB setup; ensure connection is always closed:
   ```
   try:
       store.init_db(self.db_path)
   except (sqlite3.Error, OSError) as e:
       self.status_var.set(f"DB error: {e}")
       return

   try:
       conn = store.open_connection(self.db_path)
   except (sqlite3.Error, OSError, RuntimeError, FileNotFoundError) as e:
       self.status_var.set(f"DB error: {e}")
       return
   ```
   Use `with conn:` or a `finally: conn.close()` block around the query phase so the connection
   is released even if the query raises. Do not use bare `except Exception` — it would suppress
   real programming errors (AttributeError, NameError) and make bugs harder to find.
3. Execute query and close connection (wrapped in try/finally or `with` block; on `sqlite3.Error`
   set status and return):
   ```sql
   SELECT t.id, t.post_id, t.target_normalized, t.host, t.protocol,
          t.parse_confidence, t.notes, t.target_raw, t.dedupe_key, t.created_at,
          p.post_author, p.is_nsfw
   FROM reddit_targets t
   LEFT JOIN reddit_posts p ON t.post_id = p.post_id
   ORDER BY t.id DESC
   ```
4. Populate `self._all_rows` and `self._row_by_iid` (key = `str(row["id"])`).
5. Call `_apply_filter_and_sort()` — status text is set there, not here (see §2f).

#### 2f. `_apply_filter_and_sort`
`_apply_filter_and_sort` is the **sole owner of status text** in the browser window. `_load_rows`
does not set status text after a successful load — it delegates entirely to this method.

- Get filter text from `self._filter_var.get().strip().lower()`.
- If non-empty, filter `self._all_rows` to rows where `row["target_normalized"].lower()` contains
  it. **Filter scope is `target_normalized` only** in this card — not author, notes, or other
  fields. This is the correct MVP scope and must be documented in a comment in the implementation
  so future expansion is explicit rather than accidental.
- If `self._sort_col` is set, resolve sort dict key via `COLUMN_KEY_MAP[self._sort_col]`, then sort:
  `key=lambda r: str(r.get(COLUMN_KEY_MAP[self._sort_col]) or "").lower()`, reverse=`self._sort_reverse`.
- Clear tree children; re-insert filtered+sorted rows using `iid = str(row["id"])` and
  `values=[row.get(COLUMN_KEY_MAP[c], "") or "" for c in COLUMNS]`.
- Set status: `"{n} of {total} targets"` when a filter is active, else `"{n} targets loaded"`.
  (This covers both the post-load case and every subsequent filter/sort change.)

#### 2g. `_on_sort(col)`
- Toggle: if `self._sort_col == col`, flip `self._sort_reverse`; else set new col, `_sort_reverse=False`.
- Update heading text to add `▲`/`▼` indicator; remove indicator from previous heading.
- Call `_apply_filter_and_sort()`.

#### 2h. `_selected_row`
```python
def _selected_row(self) -> Optional[dict]:
    sel = self.tree.selection()
    if not sel:
        return None
    return self._row_by_iid.get(sel[0])
```
Safe under any sort/filter state.

#### 2i. Action handlers

**`_on_open_explorer`:**
- `row = _selected_row()` → if None, `messagebox.showinfo("No selection", "Select a row first.")`.
- Reconstruct `RedditTarget` from row dict (all fields present in `_row_by_iid` values).
- Call `explorer_bridge.open_target(target, self.window)`.

**`_on_open_reddit_post`:**
- `row = _selected_row()` → if None, show info.
- Construct `https://www.reddit.com/r/opendirectories/comments/{row["post_id"]}/`.
- `webbrowser.open(url)`.

**`_reset_headings()`:** Small helper that iterates all columns and sets each heading text back to its
plain `COL_HEADERS[col]` value (no `▲`/`▼`). Called by `_on_refresh` before `_load_rows`, and
also by `_on_sort` when clearing the previous column's indicator.

**`_on_refresh`:** Set `_sort_col = None`, `_sort_reverse = False`; call `_reset_headings()`; call
`_load_rows()` first (clears and repopulates `_all_rows` with fresh data, calls
`_apply_filter_and_sort` once internally); then call `_filter_var.set("")` last (trace fires a second
`_apply_filter_and_sort` call on fresh data — redundant but correct).

Ordering rationale: if `_filter_var.set("")` runs before `_load_rows`, the StringVar trace fires
`_apply_filter_and_sort` on the stale pre-reload `_all_rows`, producing a transient incorrect
render. Calling `_filter_var.set("")` after `_load_rows` ensures both `_apply_filter_and_sort`
calls operate on current data.

**`_on_clear_db`:**
- `messagebox.askyesno` confirmation.
- If confirmed: `store.wipe_all(self.db_path)` in try/except, then `_load_rows()`.

**Factory:**
```python
def show_reddit_browser_window(parent: tk.Widget, db_path: Optional[Path] = None) -> None:
    RedditBrowserWindow(parent, db_path)
```

---

### Step 3 — `gui/components/dashboard.py` (surgical edits)

**3a. Import** (after existing `reddit_grab_dialog` import, ~line 36):
```python
from gui.components.reddit_browser_window import show_reddit_browser_window
```

**3b. `__init__` state** (after `self.reddit_grab_button = None` at line ~180):
```python
self.reddit_browser_button = None
```

**3c. Button creation** in `_build_header_section` (after `reddit_grab_button.pack(...)` at line ~374,
before `servers_button` creation at line ~377):
```python
self.reddit_browser_button = tk.Button(
    left_actions,
    text="Reddit Post DB",
    command=self._handle_reddit_browser_button_click,
)
self.theme.apply_to_widget(self.reddit_browser_button, "button_secondary")
self.reddit_browser_button.pack(side=tk.LEFT, padx=(0, 5))
```

**3d. Theme refresh** in `_refresh_theme_cached_colors` (~line 431–442):
Add `getattr(self, "reddit_browser_button", None)` to the existing button list that receives
`theme.apply_to_widget(button, "button_secondary")`.

**3e. Handler method** (add after `_on_reddit_grab_done`, ~line 2912):
```python
def _handle_reddit_browser_button_click(self) -> None:
    """Open the Reddit Post DB browser window."""
    show_reddit_browser_window(parent=self.parent)
```

**No scan-state gating** for `reddit_browser_button` — it is read-only (same pattern as `servers_button`,
which is not managed in `_update_scan_button_state`).

---

## 4. Tests

### `shared/tests/test_redseek_explorer_bridge.py` (no display needed)

| Test | Scenario | Expected |
|---|---|---|
| `test_full_url_http` | `target_normalized="http://example.com/files/"` | returns as-is |
| `test_full_url_https` | `target_normalized="https://example.com"` | returns as-is |
| `test_full_url_ftp` | `target_normalized="ftp://files.example.com"` | returns as-is |
| `test_protocol_field_http` | `protocol="http"`, `host="10.0.0.1:8080"` | `"http://10.0.0.1:8080"` |
| `test_protocol_field_ftp` | `protocol="ftp"`, `host="ftp.example.com"` | `"ftp://ftp.example.com"` |
| `test_protocol_field_skipped_when_host_empty` | `protocol="http"`, `host=None` | falls through to port/None |
| `test_protocol_unknown_ignored` | `protocol="unknown"`, `host="example.com"`, no scheme | returns `None` |
| `test_port_80_infers_http` | `host="example.com:80"`, `protocol=None` | `"http://example.com:80"` |
| `test_port_443_infers_https` | `host="example.com:443"`, `protocol=None` | `"https://example.com:443"` |
| `test_port_21_infers_ftp` | `host="files.example.com:21"`, `protocol=None` | `"ftp://files.example.com:21"` |
| `test_bare_host_returns_none` | `host="example.com"`, `protocol=None` | `None` |
| `test_open_target_known_url` | full URL target | `webbrowser.open` called with that URL |
| `test_open_target_prompts_on_unknown` | bare host, `protocol=None` | `_ask_protocol` called once |
| `test_open_target_user_cancel` | `_ask_protocol` stubbed to return `None` | `webbrowser.open` NOT called |
| `test_ask_protocol_invalid_input_returns_none` | `askstring` stubbed to return `"sftp"` | `messagebox.showerror` called; `_ask_protocol` returns `None` |
| `test_ask_protocol_valid_input_returned` | `askstring` stubbed to return `" https:// "` | `_ask_protocol` returns `"https"` (stripped, no error) |

Note: invalid-input validation lives in `_ask_protocol`, not in `open_target`. `open_target` only handles
the `None` return path (which `test_open_target_user_cancel` covers). Stubbing `_ask_protocol` directly
in `open_target` tests would bypass that validation layer — wrong boundary.

Use `monkeypatch` for `webbrowser.open` and `simpledialog.askstring`; stub `_ask_protocol` at module level for `open_target` tests.

### `gui/tests/test_reddit_browser_window.py` (headless via `__new__` pattern)

| Test | Scenario | Expected |
|---|---|---|
| `test_load_rows_empty_db` | empty DB | status "0 targets loaded", tree empty |
| `test_load_rows_populates_row_by_iid` | 2 mock rows | `_row_by_iid` has 2 entries keyed by `str(id)` |
| `test_load_rows_init_db_error` | `store.init_db` raises `sqlite3.Error` | status shows error, no crash |
| `test_load_rows_open_connection_error` | `store.open_connection` raises `OSError` | status shows error, no crash |
| `test_selected_row_none_when_empty` | no selection | returns `None` |
| `test_selected_row_returns_correct_dict` | iid in `_row_by_iid` | returns matching dict |
| `test_apply_filter_reduces_visible_rows` | filter text matches 1 of 2 rows | tree shows 1 row |
| `test_apply_filter_empty_shows_all` | empty filter | tree shows all rows |
| `test_on_sort_sets_sort_col` | click "target" heading | `_sort_col == "target"`, `_sort_reverse == False` |
| `test_on_sort_toggles_reverse` | click same heading twice | `_sort_reverse == True` |
| `test_sort_by_target_reorders_rows` | 2 rows with `target_normalized="b..."` and `"a..."`, sort ascending | tree children order matches ascending target order (verify via `tree.get_children()` iids) |
| `test_on_open_reddit_post_no_selection` | no selection | `messagebox.showinfo`, no `webbrowser.open` |
| `test_on_open_reddit_post_opens_url` | row selected | `webbrowser.open` called with correct reddit URL |
| `test_on_refresh_resets_sort_filter` | call `_on_refresh` after sort | `_sort_col=None`, `_filter_var=""`, `_load_rows` called |
| `test_on_refresh_clears_heading_indicators` | sort applied then refresh | all heading texts match plain `COL_HEADERS` values (no `▲`/`▼`) |
| `test_on_clear_db_confirmed` | askyesno True | `store.wipe_all` called, `_load_rows` called |
| `test_on_clear_db_cancelled` | askyesno False | `store.wipe_all` NOT called |
| `test_on_open_explorer_no_selection` | no selection | info dialog, `open_target` NOT called |
| `test_on_open_explorer_calls_bridge` | row selected | `open_target` called with matching `RedditTarget` |

Use `xvfb-run` only if a test instantiates `tk.Toplevel`; prefer `__new__` bypass throughout.

### Dashboard regression (existing, unchanged)
`gui/tests/test_dashboard_reddit_wiring.py` — **16 tests**, all must pass.

---

## 5. Validation Commands

```bash
# Syntax check new modules
./venv/bin/python -m py_compile redseek/explorer_bridge.py
./venv/bin/python -m py_compile gui/components/reddit_browser_window.py

# Explorer bridge unit tests (no display needed — pure logic)
./venv/bin/python -m pytest shared/tests/test_redseek_explorer_bridge.py -v

# Browser window tests (headless via __new__; xvfb-run -a as safety net)
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_reddit_browser_window.py -v

# Dashboard reddit wiring regression (16 tests; may import Tk internals)
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_dashboard_reddit_wiring.py -v

# Existing browser window regressions (Card 5 DoD: "existing browser windows unchanged")
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py \
    gui/tests/test_http_browser_window.py \
    gui/tests/test_smb_browser_window.py -v

# All reddit/redseek tests together
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"

# Broader dashboard regression
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py \
    gui/tests/test_dashboard_api_key_gate.py -v

# Full redseek shared regression (no display needed)
./venv/bin/python -m pytest shared/tests/test_redseek_store.py \
    shared/tests/test_redseek_client.py \
    shared/tests/test_redseek_parser.py \
    shared/tests/test_redseek_service.py -v
```

---

## 6. PASS/FAIL Gates

| Gate | Check | Required Result |
|---|---|---|
| G1 | `py_compile` on both new files | Exit 0 |
| G2 | `test_redseek_explorer_bridge.py` | All 16 tests PASS |
| G3 | `test_reddit_browser_window.py` | All 19 tests PASS |
| G4 | `test_dashboard_reddit_wiring.py` | All 16 existing tests PASS |
| G5 | FTP/HTTP/SMB browser window suites | No regressions |
| G6 | `-k "reddit or redseek"` | All pass, no new failures |
| G7 | Broader dashboard regression | No failures |
| G8 (manual) | "Reddit Post DB" button visible | Present, clickable |
| G9 (manual) | Open in Explorer — full URL row | Browser opens immediately, no prompt |
| G10 (manual) | Open in Explorer — bare host row | Protocol prompt appears |
| G11 (manual) | Open Reddit Post | Correct `reddit.com/…/comments/{id}/` URL opens |
| G12 (manual) | Column-click sort | Rows reorder; ▲/▼ indicator toggles |
| G13 (manual) | Filter entry | Typing filters visible rows; clear restores all |
| G14 (manual) | Refresh | Sort and filter reset; rows reload |
| G15 (manual) | Clear DB | Confirmation dialog; rows gone after confirm |
| G16 (manual) | Isolation: launch SMB/FTP/HTTP scan dialogs | No errors, no behavior change |

---

## 7. Risks and Shortcuts to Avoid

**Resolved design decisions:**
- Row identity uses `iid = str(target.id)` + `_row_by_iid` dict — safe under sort/filter.
- Sort: column-click heading command; `_apply_filter_and_sort` handles both concerns together.
- Filter: `tk.Entry` + `StringVar` trace; filters `_all_rows` in memory, no extra DB round-trip.
- Host guard in `_infer_url` branch 2: skip to next inference step if `host` is None or empty.

**Shortcuts to avoid:**
- Do NOT use `tree.index(sel[0])` for row lookup — always use `_row_by_iid[sel[0]]`.
- Do NOT add post-level rows (posts with `had_targets=0`) — window is targets view only.
- Do NOT gate `reddit_browser_button` on scan state — read-only, follows `servers_button` pattern.
- Do NOT call `store.open_connection` without calling `store.init_db` first.
- Do NOT clear `_row_by_iid` on filter — it must remain the full set so sort/filter can see all rows.
